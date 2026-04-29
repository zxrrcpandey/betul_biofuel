# v2.9.8.13 — Remove native "Status" column from Purchase Order list view.
#
# User asked to hide the native PO Status column on /app/purchase-order list,
# keeping only the custom "Approval Status" column (ts_approval_status).
#
# The column was configured in tabList View Settings.fields JSON for
# Purchase Order (per-doctype, global, not per-user). This patch parses the
# JSON, removes the entry whose fieldname == 'status_field', re-saves the
# row, clears the doctype cache.
#
# Idempotent via Singles default `ts_v29813_po_status_col_removed`.

import json

import frappe

FLAG = "ts_v29813_po_status_col_removed"


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
			message=f"v2.9.8.13: bad List View Settings fields JSON for {name}: {e}",
			title="patches.v2_9_8.remove_po_status_column",
		)
		return

	new_fields = [f for f in fields if (f or {}).get("fieldname") != "status_field"]

	if len(new_fields) != len(fields):
		frappe.db.set_value(
			"List View Settings", name,
			{
				"fields": json.dumps(new_fields),
				"total_fields": str(len(new_fields)),
			},
		)
		frappe.clear_cache(doctype="Purchase Order")

	frappe.db.set_default(FLAG, "1")
	frappe.db.commit()
