# v2.9.8.14 — Reorder PO list view columns: ID in 2nd position.
#
# Desired order (per user request):
#   Supplier Name | ID | Approval Status | Grand Total
#
# Touches the same tabList View Settings.fields JSON modified by v2.9.8.13.
# Preserves any custom labels admins may have set + keeps any extra columns
# (added later) at the END after the desired core order.
#
# Idempotent via Singles default `ts_v29814_po_list_reordered`.

import json

import frappe

FLAG = "ts_v29814_po_list_reordered"
DESIRED_ORDER = ["supplier_name", "name", "ts_approval_status", "grand_total"]


def execute():
	if frappe.db.get_default(FLAG):
		return

	name = "Purchase Order"
	if not frappe.db.exists("List View Settings", name):
		frappe.db.set_default(FLAG, "1")
		return

	raw = frappe.db.get_value("List View Settings", name, "fields")
	if not raw:
		frappe.db.set_default(FLAG, "1")
		return

	try:
		fields = json.loads(raw)
	except Exception as e:
		frappe.log_error(
			message=f"v2.9.8.14: bad List View Settings fields JSON for {name}: {e}",
			title="patches.v2_9_8.reorder_po_list_columns",
		)
		return

	# Map fieldname → entry (preserves admin-customised labels).
	by_name = {(f or {}).get("fieldname"): f for f in fields if f and f.get("fieldname")}

	# Build new ordered list: desired order first, then any extras admins added.
	new_fields, seen = [], set()
	for fname in DESIRED_ORDER:
		if fname in by_name:
			new_fields.append(by_name[fname])
			seen.add(fname)
	for f in fields:
		fname = (f or {}).get("fieldname")
		if fname and fname not in seen:
			new_fields.append(f)
			seen.add(fname)

	if json.dumps(new_fields) != json.dumps(fields):
		frappe.db.set_value(
			"List View Settings", name,
			{"fields": json.dumps(new_fields)},
		)
		frappe.clear_cache(doctype="Purchase Order")

	frappe.db.set_default(FLAG, "1")
	frappe.db.commit()
