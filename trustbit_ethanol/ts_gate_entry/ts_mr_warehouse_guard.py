# v2.10.0.4 — Hidden from_warehouse guard for non-Transfer Material Requests.
#
# ERPNext's BuyingController.validate_from_warehouse() throws
# "Row #N: Accepted Warehouse and Supplier Warehouse cannot be same" whenever
# item.from_warehouse == item.warehouse on any MR row. For Material Request
# types OTHER than Material Transfer, both `from_warehouse` (row) and
# `set_from_warehouse` (header) are HIDDEN from the user by depends_on
# (`eval:parent.material_request_type == "Material Transfer"`), so the user
# cannot see or edit those values from the form. During amend the values
# carry over invisibly and can collide with `warehouse`, blocking save with
# no way for the user to fix it.
#
# This guard runs as `before_validate` (i.e. BEFORE ERPNext's validate())
# and clears the invisible fields when material_request_type != "Material
# Transfer". For Material Transfer MRs the fields are user-visible and we
# leave them alone.

import frappe


def mr_clear_invisible_from_warehouse(doc, method=None):
	if (doc.material_request_type or "") == "Material Transfer":
		return

	if doc.get("set_from_warehouse"):
		doc.set_from_warehouse = ""

	for item in (doc.get("items") or []):
		if item.get("from_warehouse"):
			item.from_warehouse = ""
