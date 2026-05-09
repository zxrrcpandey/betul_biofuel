# v2.8.11.4 + v2.9.14.1 — Service Request → Purchase Order conversion helper.
#
# Problem: ERPNext's standard `make_purchase_order` mapper rejects
# `material_request_type = "Service Request"` (only allows "Purchase").
# BBF business reality: services ARE purchased from external vendors and
# require a PO.
#
# v2.9.14.1 fix: previously the helper flipped MR type → "Purchase"
# permanently, so the MR's original "Service Request" intent was lost
# in the live data (only the audit comment retained it). Now the flip
# is wrapped in try/finally and the original type is ALWAYS restored
# after the mapper returns (or raises).
#
# Flow:
#   1. UI confirmation dialog (in mr_approval.js)
#   2. JS calls this helper via @frappe.whitelist(methods=["POST"])
#   3. Helper temporarily flips material_request_type → "Purchase"
#   4. Helper logs an immutable Comment with original type + user + time
#   5. Helper calls standard make_purchase_order mapper
#   6. Helper restores material_request_type back to original (try/finally)
#   7. Returns PO doc to JS, which routes user to the new PO form
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

	# Temporarily flip the type so ERPNext's mapper accepts the MR (db.set_value
	# bypasses validate hooks; safe for docstatus=1). Restored in finally below.
	frappe.db.set_value(
		"Material Request", mr_name, "material_request_type", "Purchase",
		update_modified=True,
	)

	# Immutable audit comment
	audit_user = frappe.session.user
	audit_time = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M")
	audit_html = (
		f"<b>[v2.9.14.1 SR→PO]</b> material_request_type temporarily flipped "
		f"<b>'{frappe.utils.escape_html(original_type)}'</b> → "
		f"<b>'Purchase'</b> for PO mapping by {frappe.utils.escape_html(audit_user)} "
		f"on {audit_time}; restored to <b>'{frappe.utils.escape_html(original_type)}'</b> "
		f"after PO created. Original Service Request intent preserved on the MR."
	)
	frappe.get_doc({
		"doctype": "Comment",
		"comment_type": "Info",
		"reference_doctype": "Material Request",
		"reference_name": mr_name,
		"content": audit_html,
	}).insert(ignore_permissions=True)

	frappe.db.commit()

	# Invoke the standard mapper, then ALWAYS restore the original type
	# (even if the mapper raises) so the MR's Service Request intent persists.
	from erpnext.stock.doctype.material_request.material_request import make_purchase_order
	try:
		return make_purchase_order(mr_name)
	finally:
		frappe.db.set_value(
			"Material Request", mr_name, "material_request_type", original_type,
			update_modified=True,
		)
		frappe.db.commit()
