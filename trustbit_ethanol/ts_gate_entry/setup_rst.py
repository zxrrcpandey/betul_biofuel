"""RST Number Custom Field seeding — v2.8.2.

Isolated from setup.py to avoid touching MR_FULL / PO_FULL locked seed functions.
Registered in hooks.py after_migrate list. Idempotent via ignore_if_duplicate.
"""

import frappe


def seed_rst_custom_fields():
	"""Seed TS Token.custom_rst_number + Purchase Receipt.custom_rst_number (idempotent)."""
	fields = [
		{
			"doctype": "Custom Field",
			"dt": "TS Token",
			"fieldname": "custom_rst_number",
			"label": "RST Number",
			"fieldtype": "Data",
			"read_only": 1,
			"insert_after": "wb_gross_time",
			"in_list_view": 1,
			"print_hide": 1,
			"description": "Weighbridge RST slip number (mirrored from TS Weighbridge Log).",
		},
		{
			"doctype": "Custom Field",
			"dt": "Purchase Receipt",
			"fieldname": "custom_rst_number",
			"label": "RST Number",
			"fieldtype": "Data",
			"read_only": 1,
			"insert_after": "bill_date",
			"in_standard_filter": 1,
			"print_hide": 0,
			"description": "Weighbridge RST slip number, propagated from TS Token at GRN creation.",
		},
	]
	for f in fields:
		try:
			frappe.get_doc(f).insert(ignore_if_duplicate=True, ignore_permissions=True)
			frappe.logger().info(f"[seed_rst] Created/verified Custom Field {f['dt']}.{f['fieldname']}")
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(message=f"seed_rst failure on {f['dt']}.{f['fieldname']}: {e}", title="seed_rst_custom_fields")

	frappe.clear_cache(doctype="TS Token")
	frappe.clear_cache(doctype="Purchase Receipt")
