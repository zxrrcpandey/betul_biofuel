# v2.8.11.4 + v2.9.14.1 + v2.30.1 — Service Request → Purchase Order helpers.
#
# Problem: ERPNext's standard `make_purchase_order` mapper rejects
# `material_request_type = "Service Request"` (only allows "Purchase").
# BBF business reality: services ARE purchased from external vendors and
# require a PO — numbered as a Work Order (BBPL-WO-*).
#
# v2.9.14.1: the type flip is wrapped in try/finally so the MR's original
# "Service Request" intent is ALWAYS restored after the mapper returns
# (or raises).
#
# v2.30.1: TWO entry points now share the same flip/audit/restore core:
#   1. convert_sr_to_po(mr_name)
#      — MR form ▸ Create ▸ Purchase Order (mr_approval.js confirm dialog)
#   2. map_mr_to_po(source_name, target_doc, args)
#      — PO form ▸ Get Items From ▸ Material Request, invoked per selected MR
#        by frappe.model.mapper.map_docs (po_approval.js button override)
# Both stamp the resulting PO as a Work Order — naming_series = BBPL-WO-OP
# mask + ts_po_type = "Work Order" (the visible PO Type dropdown) — ONLY
# while the target PO is still unsaved: naming_series is set_only_once, so
# an already-saved draft keeps its series (stamping it would raise
# CannotChangeConstantError at the next save).
#
# Security:
#   - methods=["POST"] per Lesson 175 (both endpoints mutate via the temp flip)
#   - Caller must have write permission on the MR (mr.check_permission)
#   - docstatus must be 1 (submitted) — same as ERPNext's mapper

import frappe
from frappe import _

# The Work Order numbering mask. Must exist in the live PO naming_series
# Property Setter options (present on demo + prod since 2 Apr 2026).
WO_SERIES = "BBPL-WO-OP-.YYYY.-.#####"


def _stamp_work_order(doc):
	"""Mark an UNSAVED mapped PO as a Work Order (series + visible PO Type).

	A PO that already exists in the DB keeps whatever series it was created
	under (set_only_once). A deliberate BBPL-WO-* pick already on the target
	(e.g. BBPL-WO-OT) is left untouched — only the family matters.
	"""
	if doc.name and frappe.db.exists("Purchase Order", doc.name):
		return doc
	if not (doc.naming_series or "").startswith("BBPL-WO"):
		doc.naming_series = WO_SERIES
	doc.ts_po_type = "Work Order"
	return doc


def _map_service_request(mr, target_doc=None, args=None):
	"""Flip a Service Request MR to Purchase, run the core mapper, restore.

	The single permission + docstatus gate shared by BOTH endpoints. The flip
	is db.set_value (bypasses validate hooks; safe for docstatus=1) and is
	ALWAYS restored in finally, even if the mapper raises.
	"""
	# @frappe.whitelist(methods=["POST"]) is enforced on the TOP-LEVEL cmd only
	# (handler.py::is_valid_http_method) — reached through core's map_docs
	# trampoline (a bare whitelist) this function would be callable by GET,
	# which skips CSRF entirely while our explicit commits below still persist.
	# Re-assert POST here, where every caller passes. request is None for
	# background jobs / console / tests, which stay allowed.
	_req = getattr(frappe.local, "request", None)
	if _req is not None and _req.method != "POST":
		raise frappe.PermissionError

	mr.check_permission("write")

	if mr.docstatus != 1:
		frappe.throw(
			_("Material Request {0} must be Submitted before creating a Purchase Order.")
			.format(mr.name),
			title=_("Cannot Create Purchase Order"),
		)

	original_type = mr.material_request_type

	frappe.db.set_value(
		"Material Request", mr.name, "material_request_type", "Purchase",
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
		"reference_name": mr.name,
		"content": audit_html,
	}).insert(ignore_permissions=True)

	frappe.db.commit()

	# Invoke the standard mapper, then ALWAYS restore the original type
	# (even if the mapper raises) so the MR's Service Request intent persists.
	from erpnext.stock.doctype.material_request.material_request import make_purchase_order
	try:
		return make_purchase_order(mr.name, target_doc, args)
	finally:
		frappe.db.set_value(
			"Material Request", mr.name, "material_request_type", original_type,
			update_modified=True,
		)
		frappe.db.commit()


@frappe.whitelist(methods=["POST"])
def convert_sr_to_po(mr_name):
	"""Convert a Service Request MR to Purchase type and return a draft PO."""
	if not mr_name:
		frappe.throw(_("Material Request name is required"))

	mr = frappe.get_doc("Material Request", mr_name)

	# Permission gate (kept here too so the Purchase passthrough keeps the
	# original write requirement; _map_service_request re-checks — harmless)
	mr.check_permission("write")

	if mr.docstatus != 1:
		frappe.throw(
			_("Material Request {0} must be Submitted before creating a Purchase Order.")
			.format(mr_name),
			title=_("Cannot Create Purchase Order"),
		)

	# If already Purchase, just defer to standard mapper
	if mr.material_request_type == "Purchase":
		from erpnext.stock.doctype.material_request.material_request import make_purchase_order
		return make_purchase_order(mr_name)

	# We only handle Service Request → Purchase via this helper
	if mr.material_request_type != "Service Request":
		frappe.throw(
			_("This action only converts Service Request type to Purchase. "
			  "Current type is {0} — please use the standard mapper.")
			.format(mr.material_request_type),
			title=_("Unsupported MR Type"),
		)

	return _stamp_work_order(_map_service_request(mr))


@frappe.whitelist(methods=["POST"])
def map_mr_to_po(source_name, target_doc=None, args=None):
	"""Mapper for PO form ▸ Get Items From ▸ Material Request (via map_docs).

	Purchase MRs pass straight through to the core mapper (which enforces its
	own read permission and type/docstatus validation). Service Request MRs go
	through the shared flip/audit/restore path and stamp the unsaved target as
	a Work Order. Any other MR type falls through to core, which rejects it —
	fail-closed.

	The read gate runs BEFORE the type branch so an unauthorized caller gets
	the identical PermissionError for every MR — no type oracle via differing
	error paths.
	"""
	mr = frappe.get_doc("Material Request", source_name)
	mr.check_permission("read")

	if mr.material_request_type != "Service Request":
		from erpnext.stock.doctype.material_request.material_request import make_purchase_order
		return make_purchase_order(source_name, target_doc, args)

	return _stamp_work_order(_map_service_request(mr, target_doc, args))
