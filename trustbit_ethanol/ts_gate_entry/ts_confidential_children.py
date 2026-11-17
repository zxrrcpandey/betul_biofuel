"""Child rows of a confidential document follow their parent's rule in list queries.

Frappe looks up `permission_query_conditions` by the queried doctype only, so the rule on Purchase Order
does not reach `Purchase Order Item`: a user who may read purchase orders at all could list the item
lines of the hidden ones with a hand-made call (measured on demo, 17 Nov 2026: all 100 hidden orders).
Two parts:
- `rows`: list, count and chart queries. hooks.py registers it for every child table of the four
  doctypes; test_confidential_children.py fails when a parent gains a child table not registered there.
- `one_row`: opening ONE child row. Frappe's own check does not reach the parent document (see there).
  Applied from rename_guard's before_request / before_job hook, as chart_guard is.
- `wip_asset_items`: the web version of one ERPNext call that returned every receipt line (see there).
"""

import frappe
from frappe import permissions

from trustbit_ethanol.ts_gate_entry import ts_confidential_po as conf

# parent doctype -> its own list rule (the one hooks.py registers for the parent)
PARENTS = {
	"Purchase Order": conf.get_pqc_purchase_order,
	"Purchase Receipt": conf.get_pqc_purchase_receipt,
	"Purchase Invoice": conf.get_pqc_purchase_invoice,
	"Material Request": conf.get_pqc_material_request,
}


def rows(user=None, doctype=None):
	"""A row whose parent is one of the four doctypes is listed only if that parent is listed for `user`.
	Rows of any other parent (a shared table such as Payment Schedule) are left alone."""
	out = []
	for parent, rule in PARENTS.items():
		if not any(df.options == doctype for df in frappe.get_meta(parent).get_table_fields()):
			continue
		if listed := rule(user):
			out.append(
				f"(`tab{doctype}`.`parenttype` != {frappe.db.escape(parent)} or `tab{doctype}`.`parent` in "
				f"(select `name` from `tab{parent}` where {listed}))"
			)
	return " and ".join(out)


# Frappe's own function. Kept on Frappe's module, so a second import of this file still finds it.
_core_row = getattr(permissions, "_ts_core_child", None) or permissions.has_child_permission
permissions._ts_core_child = _core_row


def one_row(child_doctype, ptype="read", child_doc=None, user=None, raise_exception=True, parent_doctype=None, **more):
	"""Frappe's check of ONE child row, then the parent DOCUMENT's own check for the four doctypes.
	Frappe means to check the parent document itself (`getattr(child_doc, "parent_doc", child_doc.parent)`),
	but that attribute always exists, so the fallback to the parent's name never happens: a row loaded on its
	own is checked against the parent doctype's role only, and `frappe.client.get` with a filter on `parent`
	returned a line of a hidden order (measured on demo, 17 Nov 2026)."""
	allowed = _core_row(child_doctype, ptype, child_doc, user, raise_exception, parent_doctype, **more)
	if not allowed or not child_doc:
		return allowed
	if isinstance(child_doc, str):
		child_doc = frappe.db.get_value(child_doctype, child_doc, ("parent", "parenttype"), as_dict=True) or {}
	parent, parenttype = child_doc.get("parent"), child_doc.get("parenttype")
	if parenttype not in PARENTS or not parent or getattr(child_doc, "parent_doc", None):
		return allowed  # not ours, or loaded through its parent: Frappe has checked that parent document
	if not frappe.db.exists(parenttype, parent):
		return allowed
	return permissions.has_permission(parenttype, ptype, doc=parent, user=user, raise_exception=raise_exception)


@frappe.whitelist()
def wip_asset_items(params):
	"""The web version of ERPNext's `get_items_tagged_to_wip_composite_asset` (hooks.py
	override_whitelisted_methods). ERPNext's own reads Purchase Receipt Item with `frappe.get_all` and no
	check: with an empty filter any login got every submitted receipt line, rates included (measured on demo,
	17 Nov 2026: 272 of 274 lines to a gate-role user). Receipt readers only, and only the lines of receipts
	that are listed for the caller (role, User Permissions and the confidentiality rule together)."""
	from erpnext.assets.doctype.asset_capitalization import asset_capitalization as core

	frappe.has_permission("Purchase Receipt", "read", throw=True)
	parts = core.get_items_tagged_to_wip_composite_asset(params)
	names = list({row.get("purchase_receipt") for part in parts for row in part} - {None})
	listed = set(frappe.get_list("Purchase Receipt", filters={"name": ("in", names)}, pluck="name",
		limit_page_length=0)) if names else set()
	return tuple([row for row in part if row.get("purchase_receipt") in listed] for part in parts)


def apply():
	if permissions.has_child_permission is not one_row:
		permissions.has_child_permission = one_row
