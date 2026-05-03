# Copyright (c) 2026 Trustbit Technologies. All rights reserved.
"""v2.9.9 — Stores Manager Independent Flow (Material Transfer + Material Issue).

When `material_request_type` is "Material Transfer" OR "Material Issue", MRs
follow a separate approval flow:

    User creates draft → Submit for Stores Approval → Pending Stores Manager
       → Stores Manager Approve → Approved (+ DRAFT Stock Entry created)
       → Stores Manager submits Stock Entry manually → inventory ledger updates

       OR

       → Stores Manager Reject → Rejected (terminal — user must Amend MR)

Material Transfer = stock movement BETWEEN warehouses (FROM + TO required).
Material Issue = stock issued OUT for consumption (only FROM/source warehouse).
Both use the same approval flow + Stores Manager role + Print Format.

This module is COMPLETELY isolated from ts_po_approval.py's Purchase chain.
The only contact point is `mr_before_save` in ts_po_approval.py which type-
branches and calls `handle_before_save` here, then returns early.

Kill switch: TS Settings.ts_material_transfer_flow_enabled. When 0, the
type-branch in mr_before_save does NOT fire and these MRs route through
the legacy Purchase chain (rollback path — exact pre-v2.9.9 behavior).

Lessons applied:
- Lesson 162: control-plane fields are tampered-guarded via _MR_GATE_FIELDS
  (handled in ts_po_approval.py BEFORE this module is called)
- Lesson 175: all whitelisted endpoints declare methods=["POST"] (CSRF)
- Lesson 233/235: not relevant here (no PR creation, no Item Tax Template)
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime


# ─────────────────────────────────────────────────────────────────────────
#  STATE MACHINE
# ─────────────────────────────────────────────────────────────────────────

ALLOWED_TRANSITIONS = {
	"Not Submitted": {"Pending Stores Manager"},
	"Pending Stores Manager": {"Approved", "Rejected"},
	"Approved": {"Pending Stores Manager"},  # only via SE on_cancel
	"Rejected": set(),  # terminal — user must Amend
}

REQUIRED_ITEM_FIELDS = ("item_code", "qty", "from_warehouse",
                        "ts_delivery_location")  # serial_no checked conditionally

# Types that follow the Stores Manager independent flow
STORES_FLOW_TYPES = ("Material Transfer", "Material Issue")


def _is_stores_flow_type(mr_type):
	"""True if this MR type follows the Stores Manager flow (Transfer + Issue)."""
	return mr_type in STORES_FLOW_TYPES


def _is_flow_enabled():
	"""Kill-switch gate. When False, type-branch in mr_before_save skips
	this module entirely and Transfer/Issue MRs use the legacy Purchase chain.
	"""
	val = frappe.db.get_single_value("TS Settings", "ts_material_transfer_flow_enabled")
	return cint(val) == 1


# ─────────────────────────────────────────────────────────────────────────
#  HOOKS — called from ts_po_approval.mr_before_save type-branch
# ─────────────────────────────────────────────────────────────────────────

def handle_before_save(doc):
	"""Called from ts_po_approval.mr_before_save when material_request_type
	is in STORES_FLOW_TYPES (Material Transfer / Material Issue). Validates
	Stores-flow invariants WITHOUT running any Purchase chain logic.

	Caller has already run `_block_gate_field_tampering` (Lesson 162) which
	stays applied for these types too.
	"""
	if not _is_stores_flow_type(doc.material_request_type):
		return  # defense-in-depth (caller already guards)

	# v2.9.9.8 — On Frappe amend (new doc with amended_from set), reset
	# ts_mr_status from terminal "Rejected" / "Approved" to "Not Submitted"
	# so the user can resubmit through the Stores Workflow. Without this
	# reset, the form sticks on the previous terminal status and shows no
	# action buttons (since Rejected is terminal in _compute_transfer_actions).
	if doc.is_new() and doc.get("amended_from"):
		if doc.ts_mr_status in ("Rejected", "Approved", "Pending Stores Manager"):
			doc.ts_mr_status = "Not Submitted"

	# Default initial status for new drafts
	if doc.is_new() and not doc.ts_mr_status:
		doc.ts_mr_status = "Not Submitted"

	# Items-during-approval lock — protect against item changes when MR is
	# ALREADY in Pending/Approved state. Compare against the DB-stored old
	# status (NOT the in-memory new status) so that the legitimate transition
	# save from Not Submitted → Pending Stores Manager doesn't trip on itself.
	if not doc.is_new() and doc.has_value_changed("items"):
		old_status = frappe.db.get_value("Material Request", doc.name, "ts_mr_status")
		if old_status in ("Pending Stores Manager", "Approved"):
			frappe.throw(
				_("Cannot modify items while MR is in status '{0}'. "
				  "Cancel the linked Stock Entry first OR ask the Stores "
				  "Manager to reject this request.").format(old_status)
			)


# ─────────────────────────────────────────────────────────────────────────
#  VALIDATION
# ─────────────────────────────────────────────────────────────────────────

def _validate_transfer_for_submit(doc):
	"""Hard validation when transitioning to Pending Stores Manager.

	Branches by type:
	- Material Transfer: requires from_warehouse + warehouse (TO), and they must differ
	- Material Issue: requires only the source warehouse (`warehouse` field per ERPNext)

	Raises frappe.ValidationError on first failure.
	"""
	if not doc.items or len(doc.items) == 0:
		frappe.throw(_("Material Request must have at least one item."))

	if not doc.cost_center:
		frappe.throw(_("Cost Center is required (used for MR naming)."))

	is_transfer = doc.material_request_type == "Material Transfer"
	is_issue = doc.material_request_type == "Material Issue"

	for idx, row in enumerate(doc.items, start=1):
		# Required field check (common)
		if not row.item_code:
			frappe.throw(_("Row {0}: Item Code is required.").format(idx))
		if flt(row.qty) <= 0:
			frappe.throw(_("Row {0}: Qty must be greater than zero.").format(idx))
		if not row.get("ts_delivery_location"):
			frappe.throw(_("Row {0}: Use Location is required (Define Use Location field).").format(idx))

		# Warehouse rules differ by type
		if is_transfer:
			# Transfer needs BOTH FROM and TO
			if not row.get("from_warehouse"):
				frappe.throw(_("Row {0}: From Warehouse is required for Material Transfer.").format(idx))
			if not row.get("warehouse"):
				frappe.throw(_("Row {0}: Target Warehouse is required for Material Transfer.").format(idx))
			if row.from_warehouse == row.warehouse:
				frappe.throw(
					_("Row {0}: From Warehouse and Target Warehouse must differ for Material Transfer.").format(idx)
				)
		elif is_issue:
			# Issue needs only the source warehouse (ERPNext stores it in `warehouse` field on MR Item)
			if not row.get("warehouse"):
				frappe.throw(_("Row {0}: Source Warehouse is required for Material Issue.").format(idx))

		# Serial No required ONLY when the item itself tracks serials
		has_serial = frappe.db.get_value("Item", row.item_code, "has_serial_no")
		if cint(has_serial) and not (row.get("serial_no") or "").strip():
			frappe.throw(
				_("Row {0}: Item {1} requires Serial No (item is serial-tracked).").format(
					idx, row.item_code
				)
			)


# ─────────────────────────────────────────────────────────────────────────
#  WHITELISTED ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def submit_for_stores_approval(mr_name):
	"""Creator (or Stores Manager) submits a Transfer MR for approval.

	State: Not Submitted → Pending Stores Manager.
	Triggers email to all active Stores Manager users.
	"""
	if not _is_flow_enabled():
		frappe.throw(_("Material Transfer flow is currently disabled (kill switch)."))

	doc = frappe.get_doc("Material Request", mr_name)
	if not _is_stores_flow_type(doc.material_request_type):
		frappe.throw(_("This endpoint is only available for Material Transfer / Material Issue MRs."))

	current = doc.ts_mr_status or "Not Submitted"
	if current != "Not Submitted":
		frappe.throw(
			_("Cannot submit: MR is in status '{0}'. Only drafts can be submitted.").format(current)
		)

	# Permission: creator OR a Stores Manager (Stores Manager can submit on behalf)
	user = frappe.session.user
	is_creator = (user == doc.owner)
	is_stores_manager = "Stores Manager" in frappe.get_roles(user)
	is_admin = user == "Administrator" or "System Manager" in frappe.get_roles(user)
	if not (is_creator or is_stores_manager or is_admin):
		frappe.throw(
			_("Only the creator or a Stores Manager can submit this Material Transfer."),
			exc=frappe.PermissionError,
		)

	# Hard validate
	_validate_transfer_for_submit(doc)

	# Set status + submit (docstatus 0 → 1)
	doc.ts_mr_status = "Pending Stores Manager"
	doc.flags.ignore_permissions = True

	# Do NOT save+submit if already submitted (idempotency)
	if doc.docstatus == 0:
		doc.save()
		doc.submit()
	else:
		# Already submitted — just update status field
		doc.db_set("ts_mr_status", "Pending Stores Manager", update_modified=False)

	doc.add_comment(
		"Comment",
		_("Submitted for Stores Manager approval by {0}.").format(user),
	)

	# v2.9.9.10 — defensive: notification failure must NEVER block submission
	try: _notify_stores_managers(doc.name)
	except Exception: pass
	try: _notify_stores_managers_system(doc.name)
	except Exception: pass
	frappe.db.commit()

	return {
		"status": "ok",
		"ts_mr_status": "Pending Stores Manager",
		"message": _("Submitted to Stores Manager. They have been notified."),
	}


@frappe.whitelist(methods=["POST"])
def approve_transfer(mr_name):
	"""Stores Manager approves a Transfer/Issue MR. Auto-creates a DRAFT Stock Entry.

	Accepts TWO entry states:
	  - Pending Stores Manager (normal flow: created by user → submitted → approved)
	  - Not Submitted (direct flow: Stores Manager edited an amended draft and
	    approves directly without an intermediate submit step). v2.9.9.11.
	In both paths the MR ends up at status=Approved + docstatus=1, and a draft
	Stock Entry is auto-created. Stores Manager reviews + submits the SE manually.
	"""
	if not _is_flow_enabled():
		frappe.throw(_("Material Transfer flow is currently disabled (kill switch)."))

	doc = frappe.get_doc("Material Request", mr_name)
	if not _is_stores_flow_type(doc.material_request_type):
		frappe.throw(_("This endpoint is only available for Material Transfer / Material Issue MRs."))

	if doc.ts_mr_status not in ("Pending Stores Manager", "Not Submitted"):
		frappe.throw(
			_("Cannot approve: MR is in status '{0}'. Only 'Pending Stores Manager' or 'Not Submitted' can be approved.").format(
				doc.ts_mr_status
			)
		)

	# Role gate
	user = frappe.session.user
	is_stores_manager = "Stores Manager" in frappe.get_roles(user)
	is_admin = user == "Administrator" or "System Manager" in frappe.get_roles(user)
	if not (is_stores_manager or is_admin):
		frappe.throw(
			_("Only Stores Manager can approve Material Transfers."),
			exc=frappe.PermissionError,
		)

	# v2.9.9.11/12 — Direct approval path is restricted to AMENDED drafts only.
	# Fresh drafts must go through Submit → Pending → Approve to enforce
	# separation of duties (creator cannot self-approve their own fresh doc).
	if doc.ts_mr_status == "Not Submitted":
		if not doc.get("amended_from") and not is_admin:
			frappe.throw(
				_("Direct approval is only available for amended drafts. "
				  "For fresh drafts, please use 'Submit for Stores Approval' first."),
				exc=frappe.ValidationError,
			)
		_validate_transfer_for_submit(doc)
		if doc.docstatus == 0:
			doc.flags.ignore_permissions = True
			doc.save()
			doc.submit()
			doc.reload()

	# Create the Stock Entry (DRAFT)
	# Purpose follows MR type: "Material Transfer" → SE purpose=Material Transfer (FROM+TO)
	#                         "Material Issue"   → SE purpose=Material Issue (FROM only)
	se_name = None
	se_error = None
	try:
		from erpnext.stock.doctype.material_request.material_request import make_stock_entry
		se = make_stock_entry(mr_name)
		# ERPNext's mapper sets purpose from MR type; be explicit anyway
		se_purpose = doc.material_request_type  # "Material Transfer" or "Material Issue"
		se.purpose = se_purpose
		se.stock_entry_type = se_purpose
		# Copy serial_no + warehouses from MR Item rows
		for s_idx, se_row in enumerate(se.items):
			mr_row = doc.items[s_idx] if s_idx < len(doc.items) else None
			if mr_row and mr_row.item_code == se_row.item_code:
				if mr_row.get("serial_no"):
					se_row.serial_no = mr_row.serial_no
				# Material Transfer: both source + target
				if doc.material_request_type == "Material Transfer":
					if mr_row.get("from_warehouse"):
						se_row.s_warehouse = mr_row.from_warehouse
					if mr_row.get("warehouse"):
						se_row.t_warehouse = mr_row.warehouse
				else:
					# Material Issue: source only (from `warehouse` field on MR Item)
					if mr_row.get("warehouse"):
						se_row.s_warehouse = mr_row.warehouse
		se.flags.ignore_permissions = True
		se.insert()  # DRAFT — docstatus=0
		se_name = se.name
	except Exception as e:
		se_error = str(e)
		try:
			frappe.log_error(
				title="v2.9.9 approve_transfer SE failed",
				message=f"MR {mr_name}: {e}",
			)
		except Exception:
			pass

	# Update MR status regardless of SE outcome (per spec — surface SE error to user)
	doc.db_set("ts_mr_status", "Approved", update_modified=False)
	if se_name:
		doc.add_comment(
			"Comment",
			_("Approved by {0}. Draft Stock Entry {1} created.").format(user, se_name),
		)
	else:
		doc.add_comment(
			"Comment",
			_("Approved by {0}. Stock Entry creation deferred (error: {1}). "
			  "Please create Stock Entry manually.").format(user, se_error or "unknown"),
		)

	# v2.9.9.10 — defensive: notification failure must NEVER block approval
	try: _notify_creator(doc.name, "approved", stock_entry=se_name)
	except Exception: pass
	try: _notify_creator_system(doc.name, "approved", stock_entry=se_name)
	except Exception: pass
	frappe.db.commit()

	return {
		"status": "ok",
		"ts_mr_status": "Approved",
		"stock_entry": se_name,
		"message": (
			_("Approved. Draft Stock Entry {0} created — please review + submit it.").format(se_name)
			if se_name
			else _("Approved, but Stock Entry creation failed: {0}. Create manually.").format(se_error)
		),
	}


@frappe.whitelist(methods=["POST"])
def reject_transfer(mr_name, reason):
	"""Stores Manager rejects a Transfer MR. Terminal state.

	State: Pending Stores Manager → Rejected. User must Amend the MR
	(Frappe-native amend creates a new doc with -1 suffix).
	"""
	if not _is_flow_enabled():
		frappe.throw(_("Material Transfer flow is currently disabled (kill switch)."))

	reason = (reason or "").strip()
	if len(reason) < 10:
		frappe.throw(_("Rejection reason must be at least 10 characters."))

	doc = frappe.get_doc("Material Request", mr_name)
	if not _is_stores_flow_type(doc.material_request_type):
		frappe.throw(_("This endpoint is only available for Material Transfer / Material Issue MRs."))

	if doc.ts_mr_status != "Pending Stores Manager":
		frappe.throw(
			_("Cannot reject: MR is in status '{0}'. Only 'Pending Stores Manager' can be rejected.").format(
				doc.ts_mr_status
			)
		)

	user = frappe.session.user
	is_stores_manager = "Stores Manager" in frappe.get_roles(user)
	is_admin = user == "Administrator" or "System Manager" in frappe.get_roles(user)
	if not (is_stores_manager or is_admin):
		frappe.throw(
			_("Only Stores Manager can reject Material Transfers."),
			exc=frappe.PermissionError,
		)

	doc.db_set("ts_mr_status", "Rejected", update_modified=False)
	doc.add_comment(
		"Comment",
		_("Rejected by {0}. Reason: {1}").format(user, frappe.utils.escape_html(reason)),
	)

	# v2.9.9.10 — defensive: notification failure must NEVER block rejection
	try: _notify_creator(doc.name, "rejected", reason=reason)
	except Exception: pass
	try: _notify_creator_system(doc.name, "rejected", reason=reason)
	except Exception: pass
	frappe.db.commit()

	return {
		"status": "ok",
		"ts_mr_status": "Rejected",
		"message": _("Rejected. Creator notified. To re-attempt, Amend the MR."),
	}


# ─────────────────────────────────────────────────────────────────────────
#  STOCK ENTRY ON-CANCEL HOOK — registered in hooks.py
# ─────────────────────────────────────────────────────────────────────────

def cleanup_draft_se_on_mr_cancel(doc, method=None):
	"""v2.9.9.9 — Material Request on_cancel hook for Stores flow MRs.

	When a Stores-flow MR (Transfer / Issue) is cancelled while having a
	DRAFT auto-created Stock Entry, that SE becomes an orphan — it can
	never be submitted because ERPNext blocks linking to cancelled docs.
	This hook deletes the orphan draft SE so the form list stays clean.

	Only acts on:
	- Stores flow types (Material Transfer / Material Issue)
	- DRAFT (docstatus=0) Stock Entries
	Submitted SEs are NOT touched (they have stock impact already).
	"""
	if not _is_stores_flow_type(doc.material_request_type):
		return
	# Find draft SEs created from this MR
	draft_se_names = frappe.db.sql_list("""
		SELECT DISTINCT se.name
		FROM `tabStock Entry` se
		INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		WHERE sed.material_request = %s
		  AND se.docstatus = 0
	""", (doc.name,))
	for se_name in draft_se_names:
		try:
			se_doc = frappe.get_doc("Stock Entry", se_name)
			se_doc.flags.ignore_permissions = True
			se_doc.delete()
			try:
				frappe.log_error(
					title="v2.9.9.9 cleanup",
					message=f"Deleted orphan draft SE {se_name} after MR {doc.name} cancel",
				)
			except Exception:
				pass
		except Exception as e:
			try:
				frappe.log_error(
					title="v2.9.9.9 cleanup failed",
					message=f"SE {se_name}: {e}",
				)
			except Exception:
				pass


def revert_on_stock_entry_cancel(doc, method=None):
	"""Stock Entry on_cancel hook. If the SE was created from an Approved
	Material Transfer/Issue MR, revert the MR back to Pending Stores Manager
	so it can be re-approved (or rejected).

	Idempotent: if MR is already Pending Stores Manager (e.g., race), no-op.
	"""
	# Accept both Material Transfer and Material Issue Stock Entry purposes
	if doc.purpose not in ("Material Transfer", "Material Issue"):
		return

	# Walk items, collect linked MRs
	mr_names = set()
	for row in (doc.items or []):
		mr = row.get("material_request")
		if mr:
			mr_names.add(mr)

	for mr_name in mr_names:
		mr = frappe.db.get_value(
			"Material Request",
			mr_name,
			["material_request_type", "ts_mr_status", "docstatus"],
			as_dict=True,
		)
		if not mr:
			continue
		if not _is_stores_flow_type(mr.material_request_type):
			continue
		if mr.ts_mr_status != "Approved":
			continue
		# Flip to Pending Stores Manager
		frappe.db.set_value(
			"Material Request",
			mr_name,
			"ts_mr_status",
			"Pending Stores Manager",
			update_modified=False,
		)
		mr_doc = frappe.get_doc("Material Request", mr_name)
		mr_doc.add_comment(
			"Comment",
			_("Stock Entry {0} cancelled — MR reverted to Pending Stores Manager.").format(doc.name),
		)


# ─────────────────────────────────────────────────────────────────────────
#  SYSTEM NOTIFICATION HELPERS — Frappe bell-icon (Notification Log)
# ─────────────────────────────────────────────────────────────────────────

def _create_system_notification(for_user, subject, mr_name, content=None):
	"""Create a Frappe in-app (bell icon) Notification Log entry for a user.

	This is independent from email — works on any environment regardless of
	SMTP config. Shows up in the user's bell-icon dropdown immediately.
	"""
	if not for_user or for_user in ("Administrator", "Guest"):
		return
	try:
		frappe.get_doc({
			"doctype": "Notification Log",
			"subject": subject,
			"for_user": for_user,
			"type": "Alert",
			"document_type": "Material Request",
			"document_name": mr_name,
			"from_user": frappe.session.user,
			"email_content": content or subject,
		}).insert(ignore_permissions=True)
	except Exception as e:
		try:
			frappe.log_error(
				title="v2.9.9 notification failed",
				message=f"User {for_user}: {e}",
			)
		except Exception:
			pass


def _notify_stores_managers_system(mr_name):
	"""Send in-app notification to all active Stores Manager users."""
	recipients = frappe.db.sql_list("""
		SELECT DISTINCT u.name
		FROM `tabUser` u
		INNER JOIN `tabHas Role` hr ON hr.parent = u.name
		WHERE hr.role = 'Stores Manager'
		  AND u.enabled = 1
		  AND u.name NOT IN ('Administrator', 'Guest')
	""")
	if not recipients:
		return
	doc = frappe.db.get_value("Material Request", mr_name,
		["material_request_type", "owner", "cost_center"], as_dict=True)
	if not doc:
		return
	type_label = doc.material_request_type or "Material Request"
	subject = f"{type_label} {mr_name} — pending your approval"
	content = (
		f"{type_label} <b>{mr_name}</b> from <b>{doc.owner}</b> "
		f"(CC: {doc.cost_center or '—'}) is pending Stores Manager approval."
	)
	for user in recipients:
		_create_system_notification(user, subject, mr_name, content)


def _notify_creator_system(mr_name, action, stock_entry=None, reason=None):
	"""Send in-app notification to MR creator on approve/reject."""
	doc = frappe.db.get_value("Material Request", mr_name,
		["material_request_type", "owner"], as_dict=True)
	if not doc or not doc.owner:
		return
	type_label = doc.material_request_type or "Material Request"
	if action == "approved":
		subject = f"{type_label} {mr_name} APPROVED"
		content = (
			f"Your {type_label} <b>{mr_name}</b> has been approved by Stores Manager."
		)
		if stock_entry:
			content += f" Draft Stock Entry <b>{stock_entry}</b> created — please review."
	elif action == "rejected":
		subject = f"{type_label} {mr_name} REJECTED"
		content = (
			f"Your {type_label} <b>{mr_name}</b> was rejected by Stores Manager. "
			f"Reason: {frappe.utils.escape_html(reason or '(no reason)')}"
		)
	else:
		return
	_create_system_notification(doc.owner, subject, mr_name, content)


# ─────────────────────────────────────────────────────────────────────────
#  EMAIL HELPERS
# ─────────────────────────────────────────────────────────────────────────

def _notify_stores_managers(mr_name):
	"""Email all active Stores Manager users that an MR is pending approval."""
	recipients = frappe.db.sql_list("""
		SELECT DISTINCT u.name
		FROM `tabUser` u
		INNER JOIN `tabHas Role` hr ON hr.parent = u.name
		WHERE hr.role = 'Stores Manager'
		  AND u.enabled = 1
		  AND u.name NOT IN ('Administrator', 'Guest')
	""")
	if not recipients:
		return

	doc = frappe.get_doc("Material Request", mr_name)
	site = frappe.utils.get_url()
	link = f"{site}/app/material-request/{mr_name}"
	subject = _("[BBPL] Material Transfer pending your approval — {0}").format(mr_name)
	body = _build_submit_email_html(doc, link)

	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=body,
			reference_doctype="Material Request",
			reference_name=mr_name,
			now=False,
		)
	except Exception as e:
		try:
			frappe.log_error(
				title="v2.9.9 email failed (stores managers)",
				message=f"MR {mr_name}: {e}",
			)
		except Exception:
			pass


def _notify_creator(mr_name, action, stock_entry=None, reason=None):
	"""Email the MR creator with approval or rejection outcome."""
	doc = frappe.get_doc("Material Request", mr_name)
	creator = doc.owner
	if not creator or creator in ("Administrator", "Guest"):
		return

	site = frappe.utils.get_url()
	link = f"{site}/app/material-request/{mr_name}"

	if action == "approved":
		subject = _("[BBPL] Your Material Transfer {0} approved").format(mr_name)
		if stock_entry:
			se_link = f"{site}/app/stock-entry/{stock_entry}"
			subject = _("[BBPL] Your Material Transfer {0} approved — Stock Entry {1} created").format(
				mr_name, stock_entry
			)
		body = _build_approve_email_html(doc, link, stock_entry, site)
	elif action == "rejected":
		subject = _("[BBPL] Your Material Transfer {0} rejected").format(mr_name)
		body = _build_reject_email_html(doc, link, reason)
	else:
		return

	try:
		frappe.sendmail(
			recipients=[creator],
			subject=subject,
			message=body,
			reference_doctype="Material Request",
			reference_name=mr_name,
			now=False,
		)
	except Exception as e:
		try:
			frappe.log_error(
				title="v2.9.9 email failed (creator)",
				message=f"MR {mr_name}: {e}",
			)
		except Exception:
			pass


def _build_submit_email_html(doc, link):
	esc = frappe.utils.escape_html
	cc = esc(doc.cost_center or "—")
	owner = esc(doc.owner or "—")
	required_by = esc(str(doc.schedule_date) if doc.schedule_date else "—")
	count = len(doc.items or [])
	return f"""
	<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
		<h3 style="color:#333;">Material Transfer pending your approval</h3>
		<table style="border-collapse:collapse;width:100%;">
			<tr><td style="padding:6px;border:1px solid #ddd;"><b>Request No</b></td><td style="padding:6px;border:1px solid #ddd;">{esc(doc.name)}</td></tr>
			<tr><td style="padding:6px;border:1px solid #ddd;"><b>Requested By</b></td><td style="padding:6px;border:1px solid #ddd;">{owner}</td></tr>
			<tr><td style="padding:6px;border:1px solid #ddd;"><b>Cost Center</b></td><td style="padding:6px;border:1px solid #ddd;">{cc}</td></tr>
			<tr><td style="padding:6px;border:1px solid #ddd;"><b>Required By</b></td><td style="padding:6px;border:1px solid #ddd;">{required_by}</td></tr>
			<tr><td style="padding:6px;border:1px solid #ddd;"><b>Items</b></td><td style="padding:6px;border:1px solid #ddd;">{count}</td></tr>
		</table>
		<p style="margin-top:16px;">
			<a href="{esc(link)}" style="background:#2563eb;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;">Open Material Request</a>
		</p>
		<p style="color:#666;font-size:12px;">Trustbit Technologies — BBPL ERP</p>
	</div>
	"""


def _build_approve_email_html(doc, link, stock_entry, site):
	esc = frappe.utils.escape_html
	se_block = ""
	if stock_entry:
		se_link = f"{site}/app/stock-entry/{stock_entry}"
		se_block = f"""
		<p>A draft Stock Entry has been created. Please review and submit it to actually move stock:</p>
		<p><a href="{esc(se_link)}" style="background:#16a34a;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;">Open Stock Entry {esc(stock_entry)}</a></p>
		"""
	return f"""
	<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
		<h3 style="color:#16a34a;">Your Material Transfer was approved ✓</h3>
		<p>MR <b>{esc(doc.name)}</b> has been approved by the Stores Manager.</p>
		{se_block}
		<p style="margin-top:16px;">
			<a href="{esc(link)}" style="background:#2563eb;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;">Open Material Request</a>
		</p>
		<p style="color:#666;font-size:12px;">Trustbit Technologies — BBPL ERP</p>
	</div>
	"""


def _build_reject_email_html(doc, link, reason):
	esc = frappe.utils.escape_html
	return f"""
	<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
		<h3 style="color:#dc2626;">Your Material Transfer was rejected</h3>
		<p>MR <b>{esc(doc.name)}</b> was rejected by the Stores Manager.</p>
		<p><b>Reason:</b></p>
		<blockquote style="background:#f9fafb;border-left:4px solid #dc2626;padding:10px;margin:10px 0;">
			{esc(reason or "(no reason provided)")}
		</blockquote>
		<p>To re-attempt, please <b>Amend</b> the MR (creates a new MR with corrections).</p>
		<p style="margin-top:16px;">
			<a href="{esc(link)}" style="background:#2563eb;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;">Open Material Request</a>
		</p>
		<p style="color:#666;font-size:12px;">Trustbit Technologies — BBPL ERP</p>
	</div>
	"""


# ─────────────────────────────────────────────────────────────────────────
#  CONTEXT — called from ts_po_approval._get_mr_approval_context type-branch
# ─────────────────────────────────────────────────────────────────────────

def get_transfer_context(doc):
	"""Return the action+state context for Material Transfer MRs.

	Called from ts_po_approval._get_mr_approval_context when type=Transfer.
	Mirrors the Purchase context shape but with `flow="transfer"` discriminator.
	"""
	user = frappe.session.user
	roles = frappe.get_roles(user)
	is_creator = (user == doc.owner)
	is_stores_manager = "Stores Manager" in roles
	is_admin = (user == "Administrator") or ("System Manager" in roles)
	is_amend = bool(doc.get("amended_from"))  # v2.9.9.12 — distinguish fresh vs amended
	status = doc.ts_mr_status or "Not Submitted"

	actions = _compute_transfer_actions(status, is_creator, is_stores_manager, is_admin, doc.docstatus, is_amend)

	stock_entry = frappe.db.get_value(
		"Stock Entry",
		{
			"purpose": "Material Transfer",
			"docstatus": ["<", 2],
			"name": ["in",
				frappe.db.sql_list("""
					SELECT DISTINCT parent FROM `tabStock Entry Detail`
					WHERE material_request = %s
				""", (doc.name,))
			] if doc.name else [],
		},
		"name",
	) if doc.name else None

	return {
		"flow": "transfer",
		"approval_enabled": True,
		"ts_mr_status": status,
		"is_creator": is_creator,
		"is_stores_manager": is_stores_manager,
		"is_admin": is_admin,
		"actions": actions,
		"stock_entry": stock_entry,
	}


def _compute_transfer_actions(status, is_creator, is_stores_manager, is_admin, docstatus, is_amend=False):
	"""Compute the list of action buttons available to the current user based on state + role.

	v2.9.9.12 — Direct Approve (skipping Submit) is restricted to amended drafts
	only. Fresh drafts from any creator (even Stores Manager creators) must go
	through the Submit → Pending → Approve path. This enforces separation-of-duties
	on initial creation while preserving the amend-after-rejection convenience flow.
	"""
	actions = []

	if status == "Not Submitted":
		# Direct Approve allowed only on AMENDED drafts (where original was
		# rejected + cancelled and now being corrected by a Stores Manager).
		# Fresh drafts must go through Submit-for-Stores-Approval, even if
		# the creator has Stores Manager role (separation of duties).
		can_direct_approve = is_amend and (is_stores_manager or is_admin)

		if can_direct_approve:
			actions.append({
				"key": "approve_transfer",
				"label": _("Approve & Create Stock Entry"),
				"primary": True,
				"color": "green",
				"icon": "✅",
				"confirm_msg": _(
					"Approve this amended request directly? It will be submitted "
					"and a draft Stock Entry will be created."
				),
			})
		elif is_creator:
			actions.append({
				"key": "submit_for_stores_approval",
				"label": _("Submit for Stores Approval"),
				"primary": True,
				"icon": "📤",
				"confirm_msg": _("Submit this request to the Stores Manager for approval?"),
			})

	elif status == "Pending Stores Manager" and (is_stores_manager or is_admin):
		actions.append({
			"key": "approve_transfer",
			"label": _("Approve & Create Stock Entry"),
			"primary": True,
			"color": "green",
			"icon": "✅",
			"confirm_msg": _(
				"Approving will auto-create a draft Stock Entry. The Stock Entry "
				"will need separate review and submission to actually move "
				"inventory. Continue?"
			),
		})
		actions.append({
			"key": "reject_transfer",
			"label": _("Reject"),
			"color": "red",
			"icon": "❌",
			"prompt_reason": True,  # JS opens frappe.prompt for reason
		})

	# Rejected is terminal — no actions

	return actions
