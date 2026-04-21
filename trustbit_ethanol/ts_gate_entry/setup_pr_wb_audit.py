"""v2.8.9 — Purchase Receipt Weighbridge-audit Custom Field seed.

Isolated from setup.py to avoid touching locked seed functions. Registered in
hooks.py `after_migrate`. Idempotent via ignore_if_duplicate + in-place update
when a field with a different spec already exists (won't overwrite
user-customized properties like `in_list_view`).

Creates:
  * Purchase Receipt.ts_wb_net_kg (Float, read-only) — Weighbridge Net (KG).
	Auto-populated from TS Weighbridge Log.net_weight at PR creation time by
	`stores_receiving_api.create_grn_for_weighed_token`. Used for audit
	reconciliation between weighed KG and received PO-UOM qty when the PO UOM
	is non-KG (Litre, Quintal, MT etc.).
"""

import frappe


def seed_pr_wb_audit_fields():
	"""Seed Purchase Receipt.ts_wb_net_kg (idempotent)."""
	fields = [
		{
			"doctype": "Custom Field",
			"dt": "Purchase Receipt",
			"fieldname": "ts_wb_net_kg",
			"label": "Weighbridge Net (KG)",
			"fieldtype": "Float",
			"read_only": 1,
			"insert_after": "lr_date",
			"print_hide": 0,
			"in_standard_filter": 0,
			"description": (
				"Auto-populated from the Weighbridge Log's net_weight when this "
				"PR was created via the Stores Receiving Dashboard. For audit "
				"reconciliation between weighed KG and received PO-UOM qty."
			),
		},
	]
	for f in fields:
		try:
			frappe.get_doc(f).insert(ignore_if_duplicate=True, ignore_permissions=True)
			frappe.logger().info(
				f"[seed_pr_wb_audit] Created/verified Custom Field {f['dt']}.{f['fieldname']}"
			)
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"seed_pr_wb_audit failure on {f['dt']}.{f['fieldname']}: {e}",
				title="seed_pr_wb_audit_fields",
			)

	frappe.clear_cache(doctype="Purchase Receipt")
