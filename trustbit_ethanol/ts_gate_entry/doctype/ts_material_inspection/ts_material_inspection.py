import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, flt, get_fullname


class TSMaterialInspection(Document):
	def validate(self):
		self._validate_item_quantities()

	def _validate_item_quantities(self):
		for item in self.items:
			total = flt(item.approved_qty) + flt(item.held_qty) + flt(item.rejected_qty)
			if total > 0 and total != flt(item.received_qty):
				frappe.throw(
					f"Item {item.item_code}: Approved ({item.approved_qty}) + Held ({item.held_qty}) "
					f"+ Rejected ({item.rejected_qty}) = {total}, but received qty is {item.received_qty}"
				)

	@frappe.whitelist()
	def approve_all(self):
		"""Approve all items in full."""
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

	@frappe.whitelist()
	def reject_all(self, reason=""):
		"""Reject all items."""
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

		self._notify_stores("Rejected", f"All items rejected. Reason: {reason}")
		frappe.msgprint("All items rejected", indicator="red")

	@frappe.whitelist()
	def submit_itemwise(self):
		"""Submit item-wise inspection decisions."""
		if self.status not in ("Pending Inspection", "On Hold"):
			frappe.throw(f"Cannot submit inspection from status '{self.status}'")

		self._check_inspection_permission()

		# Validate all items have been decided
		for item in self.items:
			if item.item_status == "Pending":
				frappe.throw(f"Item {item.item_code} is still Pending. Please set a status for all items.")
			self._validate_item_row(item)
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

	def _check_inspection_permission(self):
		"""Only requester, HOD, IT Head, or System Manager can inspect."""
		user = frappe.session.user
		allowed_users = {self.requester, self.department_head}
		allowed_roles = {"IT Head", "System Manager"}
		if user not in allowed_users and not allowed_roles.intersection(set(frappe.get_roles())):
			frappe.throw("You do not have permission to inspect these items")

	def _notify_stores(self, status, message):
		"""Notify stores/G2 about the inspection result."""
		stores_users = _get_role_users("Stores User")
		g2_users = _get_role_users("G2 Gate Operator")
		recipients = list(set(stores_users + g2_users))
		if recipients:
			_send_inspection_notification(
				self, recipients,
				f"Material Inspection {status} — {self.name}",
				message
			)


def create_material_inspection(gate_entry_name):
	"""Auto-create a Material Inspection when a Non-RM Gate Entry is submitted."""
	ge = frappe.get_doc("TS Gate Entry", gate_entry_name)

	# Only for Non-RM items (not Raw Material, not Gate Pass)
	if ge.material_flow == "Raw Material":
		return

	settings = frappe.get_single("TS Settings")
	if not settings.enable_material_inspection:
		return

	# Check if inspection already exists
	if frappe.db.exists("TS Material Inspection", {"gate_entry": ge.name}):
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

	# Find HOD — user with Department Head role in the requester's department
	department_head = _find_department_head(requester)

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
	ge_items = frappe.get_all(
		"TS Gate Entry Item",
		filters={"parent": ge.name},
		fields=["item_code", "item_name", "ordered_qty", "uom"]
	)
	for ge_item in ge_items:
		insp.append("items", {
			"item_code": ge_item.item_code,
			"item_name": ge_item.item_name,
			"received_qty": flt(ge_item.ordered_qty),
			"uom": ge_item.uom,
			"item_status": "Pending"
		})

	insp.insert(ignore_permissions=True)
	frappe.db.commit()

	# Send 1st notification
	recipients = []
	if requester:
		recipients.append(requester)
	if department_head:
		recipients.append(department_head)

	if recipients:
		_send_inspection_notification(
			insp, list(set(recipients)),
			f"Material Inspection Required — {insp.name}",
			f"Non-Raw Material items received at G2 against PO {ge.purchase_order or ''}. "
			f"Please inspect and approve/reject in ERP before GRN is created."
		)

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
				"department_head", "token_number", "purchase_order"]
	)

	now = now_datetime()

	for insp in pending:
		if not insp.created_time:
			continue

		from frappe.utils import time_diff_in_hours
		hours_elapsed = time_diff_in_hours(now, insp.created_time)
		current_stage = insp.notification_stage or 1

		# Check if next stage notification is due
		if current_stage < 4 and hours_elapsed >= sla_hours[current_stage]:
			next_stage = current_stage + 1
			recipients = []
			if insp.requester:
				recipients.append(insp.requester)
			if insp.department_head:
				recipients.append(insp.department_head)

			# 4th notification also goes to IT Head
			if next_stage == 4:
				it_head_users = _get_role_users("IT Head")
				recipients.extend(it_head_users)

			stage_labels = {
				2: "Reminder",
				3: "Warning",
				4: "Final Notice"
			}

			subject = f"[{stage_labels.get(next_stage, 'Alert')}] Material Inspection Pending — {insp.name}"

			if next_stage == 4:
				message = (
					f"FINAL NOTICE: Material items for Token {insp.token_number} "
					f"(PO: {insp.purchase_order or 'N/A'}) have NOT been inspected after "
					f"{int(hours_elapsed)} hours. Storekeeper will proceed with GRN. "
					f"Please inspect immediately if items need to be rejected."
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
					list(set(recipients)), subject, message, insp.name
				)
				# Bell channel for SLA stages 2-4 — email alone is dead (no SMTP
				# on either server), so without this the reminders reach nobody.
				from trustbit_ethanol.ts_gate_entry.ts_notification_coverage import _coverage_bell
				_coverage_bell(list(set(recipients)), subject,
					"TS Material Inspection", insp.name, body=message)

	frappe.db.commit()


def get_inspection_status_for_token(token_name):
	"""Get inspection status for a token. Returns dict or None."""
	insp = frappe.db.get_value(
		"TS Material Inspection",
		{"token_number": token_name},
		["name", "status"],
		as_dict=True
	)
	return insp


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
