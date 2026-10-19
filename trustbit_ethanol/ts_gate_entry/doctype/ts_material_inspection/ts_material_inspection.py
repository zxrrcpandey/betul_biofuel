import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, escape_html, format_datetime, get_datetime, getdate, now_datetime, flt, get_fullname
from frappe.utils.html_utils import sanitize_html

from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
	get_cc_config,
	get_cc_notify_only_users,
)
from trustbit_ethanol.ts_gate_entry.ts_notification_routing import audience

# v2.59.0 — the only fields an inspector's form may send to the buttons below.
DECISION_FIELDS = ("item_status", "approved_qty", "held_qty", "rejected_qty", "remark")

# v2.67.0 — items in this Item Group, or any group below it (grain, coal), need no
# requester verification. Unverified items: Stores may GRN after this many working days.
EXEMPT_ITEM_GROUP = "Raw Material"
GRN_WAIT_WORKING_DAYS = 2


def _require_post():
	"""methods=["POST"] is not checked on every route: GET /api/resource/...?run_method=
	runs a whitelisted method after a read check, without CSRF. Re-assert it here.
	No request (scheduler, console, tests) stays allowed."""
	req = getattr(frappe.local, "request", None)
	if req is not None and req.method != "POST":
		raise frappe.PermissionError


def _clean_text(value):
	"""db_set() / db_update() skip the HTML cleaning a normal save applies, so clean
	here whenever a browser could read markup in the text (a tag, comment or CDATA,
	closed or not) — also when it parses as JSON, which sanitize_html() would let
	through. Other text (e.g. "moisture > 14%") is returned unchanged."""
	value = cstr(value)
	return sanitize_html(value, always_sanitize=bool(re.search(r"<[!?/a-zA-Z]", value)))


class TSMaterialInspection(Document):
	def onload(self):
		self.set_onload("can_inspect", self._can_inspect())

	def validate(self):
		# v2.59.0 — requesters and heads hold write through a share (the grid
		# must be editable), so a plain save is closed to them: decisions go
		# only through the buttons, which start again from the database.
		if not self.is_new() and frappe.session.user != "Administrator" \
				and "System Manager" not in frappe.get_roles():
			frappe.throw(_("Record the inspection with the Approve All, Reject All or Submit Item-wise button."))
		self._validate_item_quantities()

	def _reload_for_decision(self, keep_decisions=False):
		"""v2.59.0 — run_doc_method builds this document from the browser's copy:
		reload it, keeping at most the per-item decisions on the stored rows."""
		decisions = {d.name: {f: d.get(f) for f in DECISION_FIELDS}
			for d in self.items if d.name} if keep_decisions else {}
		# L362: two people deciding at once — the status gate reads the row through the lock.
		status = frappe.db.get_value(self.doctype, self.name, "status", for_update=True)
		self.reload()
		self.status = status
		for item in self.items:
			item.update(decisions.get(item.name, {}))
			if item.remark:
				item.remark = _clean_text(item.remark)

	def _validate_item_quantities(self):
		for item in self.items:
			# Compare at the field's precision: 10.1 + 0.2 is 10.299999999999999 in floats.
			p = item.precision("received_qty")
			if min(flt(item.approved_qty, p), flt(item.held_qty, p), flt(item.rejected_qty, p)) < 0:
				frappe.throw(f"Item {item.item_code}: quantities cannot be negative")
			total = flt(flt(item.approved_qty) + flt(item.held_qty) + flt(item.rejected_qty), p)
			if total > 0 and total != flt(item.received_qty, p):
				frappe.throw(
					f"Item {item.item_code}: Approved ({item.approved_qty}) + Held ({item.held_qty}) "
					f"+ Rejected ({item.rejected_qty}) = {total}, but received qty is {item.received_qty}"
				)

	@frappe.whitelist(methods=["POST"])
	def approve_all(self):
		"""Approve all items in full."""
		_require_post()
		self._reload_for_decision()
		if self.status not in ("Pending Inspection", "On Hold"):
			frappe.throw(f"Cannot approve from status '{self.status}'")

		self._check_inspection_permission()

		for item in self.items:
			item.approved_qty = flt(item.received_qty)
			item.held_qty = 0
			item.rejected_qty = 0
			item.item_status = "Approved"
			item.db_update()

		self.db_set({
			"status": "Approved",
			"overall_action": "Approve All",
			"inspection_by": frappe.session.user,
			"inspection_time": now_datetime()
		})

		self._notify_stores("Approved", "All items have been approved for GRN.")
		frappe.msgprint("All items approved", indicator="green")

	@frappe.whitelist(methods=["POST"])
	def reject_all(self, reason=""):
		"""Reject all items."""
		_require_post()
		reason = _clean_text(reason)
		self._reload_for_decision()
		if self.status not in ("Pending Inspection", "On Hold"):
			frappe.throw(f"Cannot reject from status '{self.status}'")

		self._check_inspection_permission()

		if not reason:
			frappe.throw("Rejection reason is required")

		for item in self.items:
			item.approved_qty = 0
			item.held_qty = 0
			item.rejected_qty = flt(item.received_qty)
			item.item_status = "Rejected"
			item.db_update()

		self.db_set({
			"status": "Rejected",
			"overall_action": "Reject All",
			"rejection_reason": reason,
			"inspection_by": frappe.session.user,
			"inspection_time": now_datetime()
		})

		self._notify_stores("Rejected", f"All items rejected. Reason: {escape_html(reason)}")
		frappe.msgprint("All items rejected", indicator="red")

	@frappe.whitelist(methods=["POST"])
	def submit_itemwise(self):
		"""Submit item-wise inspection decisions."""
		_require_post()
		self._reload_for_decision(keep_decisions=True)
		if self.status not in ("Pending Inspection", "On Hold"):
			frappe.throw(f"Cannot submit inspection from status '{self.status}'")

		self._check_inspection_permission()

		# Validate all items have been decided
		for item in self.items:
			if item.item_status not in ("Approved", "Held", "Rejected"):
				frappe.throw(f"Item {item.item_code} is still Pending. Please set a status for all items.")
			self._validate_item_row(item)
		self._validate_item_quantities()
		for item in self.items:
			item.db_update()

		# Determine overall status
		statuses = set(item.item_status for item in self.items)
		if statuses == {"Approved"}:
			overall = "Approved"
		elif statuses == {"Rejected"}:
			overall = "Rejected"
		elif "Held" in statuses:
			overall = "On Hold"
		elif "Rejected" in statuses and "Approved" in statuses:
			overall = "Partially Approved"
		else:
			overall = "Partially Approved"

		update = {
			"status": overall,
			"overall_action": "Item-wise",
			"inspection_by": frappe.session.user,
			"inspection_time": now_datetime()
		}

		self.db_set(update)

		self._notify_stores(overall, f"Item-wise inspection completed. Status: {overall}")
		frappe.msgprint(f"Inspection submitted — {overall}", indicator="green" if overall == "Approved" else "orange")

	def _validate_item_row(self, item):
		"""Validate quantities match the item_status."""
		if item.item_status == "Approved":
			if not flt(item.approved_qty):
				item.approved_qty = flt(item.received_qty)
			item.held_qty = 0
			item.rejected_qty = flt(item.received_qty) - flt(item.approved_qty)
		elif item.item_status == "Rejected":
			item.rejected_qty = flt(item.received_qty)
			item.approved_qty = 0
			item.held_qty = 0
		elif item.item_status == "Held":
			if not flt(item.held_qty):
				item.held_qty = flt(item.received_qty)

	def _can_inspect(self):
		"""Requester, their heads (stored + shared), IT Head or System Manager."""
		user = frappe.session.user
		if {"IT Head", "System Manager"} & set(frappe.get_roles(user)):
			return True
		return user in (self.requester, *_inspection_heads(self.name, self.requester, self.department_head))

	def _check_inspection_permission(self):
		"""Only requester, HOD, IT Head, or System Manager can inspect."""
		if not self._can_inspect():
			frappe.throw("You do not have permission to inspect these items")

	def _notify_stores(self, status, message):
		"""Tell the groups ticked for "inspection result" (default: Stores + G2)."""
		cc_config = _mr_cc_config(self.material_request)
		recipients = audience(
			"inspection_result", raised_by=self.requester,
			dept_heads=_inspection_heads(self.name, self.requester, self.department_head),
			cc_notify=get_cc_notify_only_users(cc_config) if cc_config else [])
		if recipients:
			_send_inspection_notification(
				self, recipients,
				f"Material Inspection {status} — {self.name}",
				message
			)
			if status in ("Approved", "Partially Approved", "Auto-Proceeded"):
				_whatsapp(self, "inspection_grn_allowed", recipients)


def create_material_inspection(gate_entry_name):
	"""Auto-create a Material Inspection when a Stock-IN Gate Entry is submitted.
	v2.67.0: items under EXEMPT_ITEM_GROUP are left out, whatever the gate's
	material_flow; with none left there is no inspection."""
	ge = frappe.get_doc("TS Gate Entry", gate_entry_name)

	settings = frappe.get_single("TS Settings")
	if not settings.enable_material_inspection:
		return

	# Check if inspection already exists
	if frappe.db.exists("TS Material Inspection", {"gate_entry": ge.name}):
		return

	ge_items = frappe.get_all(
		"TS Gate Entry Item",
		filters={"parent": ge.name},
		fields=["item_code", "item_name", "ordered_qty", "uom"]
	)
	exempt = _exempt_item_codes([i.item_code for i in ge_items])
	ge_items = [i for i in ge_items if i.item_code not in exempt]
	if not ge_items:
		return

	# Find the Material Request from PO
	mr_name = None
	requester = None
	if ge.purchase_order:
		mr_name = frappe.db.get_value(
			"Purchase Order Item",
			{"parent": ge.purchase_order},
			"material_request",
		)
		if mr_name:
			requester = frappe.db.get_value("Material Request", mr_name, "owner")

	# Fallback requester to PO owner
	if not requester and ge.purchase_order:
		requester = frappe.db.get_value("Purchase Order", ge.purchase_order, "owner")

	# v2.59.0 — heads: Department Head approvers on the MR cost centre's config
	heads = _department_heads(mr_name, requester)
	department_head = heads[0] if heads else None

	# Get supplier name
	supplier_name = ""
	if ge.purchase_order:
		supplier_name = frappe.db.get_value("Purchase Order", ge.purchase_order, "supplier_name") or ""

	# Create inspection
	insp = frappe.new_doc("TS Material Inspection")
	insp.token_number = ge.token_number
	insp.gate_entry = ge.name
	insp.purchase_order = ge.purchase_order
	insp.material_request = mr_name
	insp.supplier_name = supplier_name
	insp.requester = requester
	insp.requester_name = get_fullname(requester) if requester else ""
	insp.department_head = department_head
	insp.department_head_name = get_fullname(department_head) if department_head else ""
	insp.created_time = now_datetime()
	insp.status = "Pending Inspection"
	insp.notification_stage = 1
	insp.sla_1st_sent = now_datetime()

	# Add items from gate entry
	for ge_item in ge_items:
		insp.append("items", {
			"item_code": ge_item.item_code,
			"item_name": ge_item.item_name,
			"received_qty": flt(ge_item.ordered_qty),
			"uom": ge_item.uom,
			"item_status": "Pending"
		})

	insp.insert(ignore_permissions=True)
	_share_with_inspectors(insp.name, [requester, *heads])
	frappe.db.commit()

	# Send 1st notification
	recipients = audience("inspection_request", raised_by=requester, dept_heads=heads)

	if recipients:
		deadline = grn_allowed_after(insp.created_time, ge.purchase_order)
		_send_inspection_notification(
			insp, recipients,
			f"Material Inspection Required — {insp.name}",
			f"Items received at the gate against PO {ge.purchase_order or ''} (Token {ge.token_number or ''}). "
			f"Please verify them in ERP before the GRN is created. If they are not verified by "
			f"{format_datetime(deadline)}, Stores may create the GRN without your verification."
		)
		_whatsapp(insp, "inspection_needed", recipients)

	return insp.name


def check_inspection_sla():
	"""Scheduler: check pending inspections and send SLA notifications."""
	settings = frappe.get_single("TS Settings")
	if not settings.enable_material_inspection:
		return

	sla_hours = [
		0,  # 1st already sent on creation
		settings.inspection_2nd_notify_hours or 8,
		settings.inspection_3rd_notify_hours or 24,
		settings.inspection_4th_notify_hours or 48,
	]

	pending = frappe.get_all(
		"TS Material Inspection",
		filters={"status": "Pending Inspection"},
		fields=["name", "created_time", "notification_stage", "requester",
				"department_head", "token_number", "purchase_order", "gate_entry"]
	)

	now = now_datetime()

	# v2.67.0 — an inspection whose Gate Entry was cancelled no longer gates a GRN: no reminders.
	cancelled = set(frappe.get_all("TS Gate Entry", filters={"docstatus": 2,
		"name": ["in", [p.gate_entry for p in pending if p.gate_entry] or [""]]}, pluck="name"))

	for insp in pending:
		if not insp.created_time or insp.gate_entry in cancelled:
			continue

		from frappe.utils import time_diff_in_hours
		hours_elapsed = time_diff_in_hours(now, insp.created_time)
		current_stage = insp.notification_stage or 1

		# Check if next stage notification is due. v2.67.0: the final stage
		# (Stores may GRN) waits for the 2-working-day deadline, not 48 hours.
		if current_stage == 3:
			due = now >= grn_allowed_after(insp.created_time, insp.purchase_order)
		else:
			due = current_stage < 3 and hours_elapsed >= sla_hours[current_stage]
		if due:
			next_stage = current_stage + 1
			# L362: a decision saved at this moment wins — flip only a row still Pending.
			if next_stage == 4 and frappe.db.get_value(
					"TS Material Inspection", insp.name, "status", for_update=True) != "Pending Inspection":
				continue
			# v2.59.0 — the groups ticked in TS Notification Audience (the final
			# notice also goes to IT Head by default).
			recipients = audience(
				"inspection_final" if next_stage == 4 else "inspection_request",
				raised_by=insp.requester,
				dept_heads=_inspection_heads(insp.name, insp.requester, insp.department_head))

			stage_labels = {
				2: "Reminder",
				3: "Warning",
				4: "Final Notice"
			}

			subject = f"[{stage_labels.get(next_stage, 'Alert')}] Material Inspection Pending — {insp.name}"

			if next_stage == 4:
				message = (
					f"FINAL NOTICE: Material items for Token {insp.token_number} "
					f"(PO: {insp.purchase_order or 'N/A'}) were not verified within "
					f"{GRN_WAIT_WORKING_DAYS} working days. Stores may now create the GRN."
				)
			else:
				message = (
					f"Material items for Token {insp.token_number} "
					f"(PO: {insp.purchase_order or 'N/A'}) are still pending inspection "
					f"after {int(hours_elapsed)} hours. Please inspect and approve/reject in ERP."
				)

			sla_field = f"sla_{next_stage}{'st' if next_stage == 1 else 'nd' if next_stage == 2 else 'rd' if next_stage == 3 else 'th'}_sent"

			frappe.db.set_value("TS Material Inspection", insp.name, {
				"notification_stage": next_stage,
				sla_field: now
			}, update_modified=False)

			if next_stage == 4:
				frappe.db.set_value("TS Material Inspection", insp.name,
					"status", "Auto-Proceeded", update_modified=False)

			if recipients:
				_send_inspection_notification_raw(
					recipients, subject, message, insp.name
				)
				# Bell channel for SLA stages 2-4 — email alone is dead (no SMTP
				# on either server), so without this the reminders reach nobody.
				from trustbit_ethanol.ts_gate_entry.ts_notification_coverage import _coverage_bell
				_coverage_bell(recipients, subject,
					"TS Material Inspection", insp.name, body=message)
				if next_stage < 4:
					_whatsapp(insp.name, "inspection_reminder", recipients)

			if next_stage == 4:
				# v2.67.0 — tell Stores (+ WhatsApp) that the GRN may go ahead.
				try:
					frappe.get_doc("TS Material Inspection", insp.name)._notify_stores("Auto-Proceeded", message)
				except Exception:
					frappe.clear_messages()
					frappe.log_error(title="Material inspection: Stores notice failed",
						message=frappe.get_traceback()[:2000])

	frappe.db.commit()


def get_inspection_status_for_token(token_name):
	"""Get inspection status for a token. Returns dict or None.
	v2.67.0: the newest inspection whose Gate Entry is not cancelled."""
	rows = frappe.db.sql("""
		SELECT i.name, i.status, i.created_time, i.purchase_order, i.requester_name
		FROM `tabTS Material Inspection` i
		LEFT JOIN `tabTS Gate Entry` ge ON ge.name = i.gate_entry
		WHERE i.token_number = %s AND IFNULL(ge.docstatus, 0) != 2
		ORDER BY i.creation DESC
		LIMIT 1
	""", (token_name,), as_dict=True)
	return rows[0] if rows else None


def grn_allowed_after(created_time, purchase_order=None):
	"""v2.67.0 — when Stores may create the GRN of an unverified inspection: the same
	clock time on the GRN_WAIT_WORKING_DAYS-th working day after created_time. Sunday
	is always off, and so is every date in the PO company's default Holiday List.
	TS Settings inspection_4th_notify_hours stays as a floor."""
	created = get_datetime(created_time)
	company = frappe.db.get_value("Purchase Order", purchase_order, "company") if purchase_order else None
	holiday_list = frappe.db.get_value("Company", company, "default_holiday_list") if company else None
	holidays = {getdate(d) for d in frappe.get_all("Holiday",
		filters={"parent": holiday_list, "parenttype": "Holiday List"}, pluck="holiday_date")} if holiday_list else set()

	due, counted = created, 0
	for _i in range(60):
		due += timedelta(days=1)
		if due.weekday() != 6 and due.date() not in holidays:
			counted += 1
			if counted == GRN_WAIT_WORKING_DAYS:
				break
	floor_hours = cint(frappe.db.get_single_value("TS Settings", "inspection_4th_notify_hours")) or 48
	return max(due, created + timedelta(hours=floor_hours))


def _exempt_item_codes(item_codes):
	"""v2.67.0 — the given items whose Item Group is EXEMPT_ITEM_GROUP or below it.
	No such group on the site means nothing is exempt."""
	if not item_codes:
		return set()
	return set(frappe.db.sql_list("""
		SELECT it.name FROM `tabItem` it
		JOIN `tabItem Group` g ON g.name = it.item_group
		JOIN `tabItem Group` root ON root.name = %s
		WHERE it.name IN %s AND g.lft >= root.lft AND g.rgt <= root.rgt
	""", (EXEMPT_ITEM_GROUP, tuple(item_codes))))


def _whatsapp(insp, action, recipients):
	"""v2.67.0 — WhatsApp copy of an inspection notice. Fail-soft, and inert until
	the event's TS WhatsApp Template Map row is enabled."""
	try:
		from trustbit_ethanol.ts_gate_entry.ts_whatsapp_notify import whatsapp_notify_for_approval
		if isinstance(insp, str):
			insp = frappe.get_doc("TS Material Inspection", insp)
		whatsapp_notify_for_approval(insp, action, recipients)
	except Exception:
		frappe.clear_messages()
		frappe.log_error(title="Material inspection: WhatsApp notice failed",
			message=frappe.get_traceback()[:2000])


def _mr_cc_config(mr_name):
	"""Active approval config of the MR's cost centre (header, else first item).
	Checked for existence first: get_cc_config's get_doc would otherwise leave a
	"not found" message on the inspector's or gate operator's screen."""
	if not mr_name:
		return None
	cost_center = frappe.db.get_value("Material Request", mr_name, "cost_center") or frappe.db.get_value(
		"Material Request Item", {"parent": mr_name}, "cost_center", order_by="idx asc")
	if not cost_center or not frappe.db.exists("TS CC Approval Config", cost_center):
		return None
	return get_cc_config(cost_center)


def _department_heads(mr_name, requester):
	"""v2.59.0 — enabled Department Head approvers (Reviewer / Final Approver) on
	the MR cost centre's approval config; else the Employee lookup."""
	cc_config = _mr_cc_config(mr_name)
	users = [r.user for r in (cc_config.users if cc_config else []) if r.user and r.role == "Department Head"
		and r.action_type in ("Reviewer", "Final Approver")]
	enabled = set(frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name")) if users else set()
	heads = [u for u in dict.fromkeys(users) if u in enabled and u != requester]
	if not heads:
		head = _find_department_head(requester)
		heads = [head] if head and head != requester else []
	return heads


def _inspection_heads(name, requester, department_head):
	"""The stored head plus every head the inspection is shared with for writing."""
	shared = frappe.get_all("DocShare", filters={"share_doctype": "TS Material Inspection",
		"share_name": name, "write": 1}, pluck="user", order_by="creation asc") if name else []
	return [u for u in dict.fromkeys([department_head, *shared]) if u and u != requester]


def _share_with_inspectors(name, users):
	"""v2.59.0 — the requester and heads can open and decide the inspection
	whatever their roles (on prod only CEO/MD/IT Head/System Manager hold the
	doctype). notify=0: the inspection notice already tells them."""
	from frappe.share import add_docshare

	for user in dict.fromkeys(u for u in users if u and u not in ("Administrator", "Guest")):
		queued = len(frappe.local.message_log)
		try:
			add_docshare("TS Material Inspection", name, user, read=1, write=1,
				flags={"ignore_share_permission": True}, notify=0)
		except Exception:
			frappe.log_error(title="Material inspection share failed", message=frappe.get_traceback()[:2000])
			# Drop only this share's messages: the Gate Entry submit's own stay.
			del frappe.local.message_log[queued:]


def _find_department_head(user):
	"""Find the Department Head for a user's department."""
	if not user:
		return None

	# Get user's department from Employee
	department = frappe.db.get_value("Employee", {"user_id": user}, "department")
	if not department:
		return None

	# Find employee with Department Head role in the same department
	dh_employees = frappe.get_all(
		"Employee",
		filters={"department": department, "status": "Active"},
		fields=["user_id"]
	)

	dh_role_users = set(_get_role_users("Department Head"))
	for emp in dh_employees:
		if emp.user_id and emp.user_id in dh_role_users:
			return emp.user_id

	return None


def _get_role_users(role):
	"""Get list of active users with a given role."""
	return frappe.db.sql_list("""
		SELECT DISTINCT u.name
		FROM `tabUser` u
		JOIN `tabHas Role` hr ON hr.parent = u.name
		WHERE hr.role = %s AND u.enabled = 1
		AND u.name NOT IN ('Administrator', 'Guest')
	""", role)


def _send_inspection_notification(insp_doc, recipients, subject, message):
	"""Send notification for a material inspection."""
	_send_inspection_notification_raw(recipients, subject, message, insp_doc.name)

	# Also create system notification (ToDo)
	for user in recipients:
		if frappe.db.exists("User", user):
			frappe.get_doc({
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"document_type": "TS Material Inspection",
				"document_name": insp_doc.name,
				"subject": subject,
				"email_content": message,
			}).insert(ignore_permissions=True)


def _send_inspection_notification_raw(recipients, subject, message, insp_name):
	"""Send email notification."""
	link = frappe.utils.get_url_to_form("TS Material Inspection", insp_name)
	html_message = f"""
	<p>{message}</p>
	<p><a href="{link}" style="background:#1a237e;color:#fff;padding:8px 16px;text-decoration:none;border-radius:4px;">
		Open Inspection
	</a></p>
	"""
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=html_message
		)
	except Exception:
		frappe.log_error(f"Failed to send inspection notification for {insp_name}")
