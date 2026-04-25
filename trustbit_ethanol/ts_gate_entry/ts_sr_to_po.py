# v2.8.11.4 — Service Request → Purchase Order conversion helper.
#
# Problem: ERPNext's standard `make_purchase_order` mapper rejects
# `material_request_type = "Service Request"` (only allows "Purchase").
# BBF business reality: services ARE purchased from external vendors and
# require a PO. Without this helper users hit "Cannot map because
# following condition fails: material_request_type=Purchase" on every
# Service Request MR (5 cases on production today; v2.8.11.4 prevents
# recurrence by automating the type flip).
#
# Flow:
#   1. UI confirmation dialog (in mr_approval.js)
#   2. JS calls this helper via @frappe.whitelist(methods=["POST"])
#   3. Helper flips material_request_type → "Purchase"
#   4. Helper logs an immutable Comment with original type + user + time
#   5. Helper calls standard make_purchase_order mapper
#   6. Returns PO doc to JS, which routes user to the new PO form
#
# Security:
#   - methods=["POST"] per Lesson 175 (this is a state-mutating endpoint)
#   - Caller must have write permission on the MR (mr.check_permission)
#   - docstatus must be 1 (submitted) — same as ERPNext's mapper

import frappe
from frappe import _


@frappe.whitelist(methods=["POST"])
def convert_sr_to_po(mr_name):
	"""Convert a Service Request MR to Purchase type and return a draft PO."""
	if not mr_name:
		frappe.throw(_("Material Request name is required"))

	mr = frappe.get_doc("Material Request", mr_name)

	# Permission gate
	mr.check_permission("write")

	if mr.docstatus != 1:
		frappe.throw(
			_("Material Request {0} must be Submitted before creating a Purchase Order.")
			.format(mr_name),
			title=_("Cannot Create Purchase Order"),
		)

	original_type = mr.material_request_type

	# If already Purchase, just defer to standard mapper
	if original_type == "Purchase":
		from erpnext.stock.doctype.material_request.material_request import make_purchase_order
		return make_purchase_order(mr_name)

	# We only handle Service Request → Purchase via this helper
	if original_type != "Service Request":
		frappe.throw(
			_("This action only converts Service Request type to Purchase. "
			  "Current type is {0} — please use the standard mapper.")
			.format(original_type),
			title=_("Unsupported MR Type"),
		)

	# Flip the type (db.set_value bypasses validate hooks; safe for docstatus=1)
	frappe.db.set_value(
		"Material Request", mr_name, "material_request_type", "Purchase",
		update_modified=True,
	)

	# Immutable audit comment
	audit_user = frappe.session.user
	audit_time = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M")
	audit_html = (
		f"<b>[v2.8.11.4 SR→PO]</b> material_request_type changed from "
		f"<b>'{frappe.utils.escape_html(original_type)}'</b> → "
		f"<b>'Purchase'</b> by {frappe.utils.escape_html(audit_user)} "
		f"on {audit_time} to enable Purchase Order creation. "
		f"Original Service Request intent preserved in this audit entry."
	)
	frappe.get_doc({
		"doctype": "Comment",
		"comment_type": "Info",
		"reference_doctype": "Material Request",
		"reference_name": mr_name,
		"content": audit_html,
	}).insert(ignore_permissions=True)

	frappe.db.commit()

	# Now invoke the standard mapper — guaranteed to succeed since type is Purchase
	from erpnext.stock.doctype.material_request.material_request import make_purchase_order
	return make_purchase_order(mr_name)
