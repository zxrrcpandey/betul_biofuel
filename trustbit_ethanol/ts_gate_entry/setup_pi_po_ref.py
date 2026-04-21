"""Purchase Invoice — auto-computed Purchase Order reference (v2.8.6).

Business need (CTO Rahul, 21 Apr 2026): Accounts team wants to see the
linked PO(s) at a glance on the PI header without drilling into the items
child table. Standard ERPNext only stores PO links per-item.

## What ships

1. Custom Field `ts_po_reference` on Purchase Invoice (Data, read-only,
   insert_after=lr_date, in_standard_filter=1, print_hide=1 screen-only).
2. `populate_ts_po_reference()` doc_events handler fired on `validate` —
   computes distinct `purchase_order` values from `items` child table and
   writes them as a comma-separated string to `ts_po_reference`.
   - Single PO → `"PUR-ORD-2026-00001"`
   - Multiple POs → `"PUR-ORD-2026-00001, PUR-ORD-2026-00002"`
   - Zero POs → empty string

## Placement

Custom Field placed after `lr_date` (v2.8.4 field) so all purchasing
reference numbers (bill_no / bill_date / lr_no / lr_date / ts_po_reference)
sit together on the PI form.

## Why validate (not before_save)

`before_save` is already in use by `_block_pi_qc_override_tampering` (v2.8.1).
Using `validate` (which fires on every save AND on submit) keeps the PO ref
in sync with any items-table edit. Read-only field so accounts users cannot
manually override; the computed value is always fresh.

## Auto-carry behavior

Items.purchase_order is populated automatically by ERPNext's
make_purchase_invoice() mapper when PI is created from a PO or PR with a
linked PO. So on standard "Make → Purchase Invoice" flows, ts_po_reference
will be populated on first save with zero manual input.
"""

import frappe


def seed_pi_po_ref_field():
	"""Seed ts_po_reference Custom Field on Purchase Invoice. Idempotent —
	on existing rows updates placement + label; on fresh installs inserts."""
	field = {
		"doctype": "Custom Field",
		"dt": "Purchase Invoice",
		"fieldname": "ts_po_reference",
		"label": "Purchase Order Reference",
		"fieldtype": "Data",
		"insert_after": "lr_date",
		"read_only": 1,
		"in_standard_filter": 1,
		"print_hide": 1,
		"no_copy": 0,
		"description": "Auto-computed list of Purchase Orders linked to this PI items. Refreshed on every save. Read-only.",
	}
	cf_name = "Purchase Invoice-ts_po_reference"
	if frappe.db.exists("Custom Field", cf_name):
		try:
			frappe.db.set_value(
				"Custom Field", cf_name,
				{
					"insert_after": field["insert_after"],
					"label": field["label"],
					"read_only": field["read_only"],
					"in_standard_filter": field["in_standard_filter"],
					"print_hide": field["print_hide"],
					"description": field["description"],
				},
			)
			frappe.logger().info(f"[seed_pi_po_ref] Updated {cf_name}")
		except Exception as e:
			frappe.log_error(
				message=f"seed_pi_po_ref update failure: {e}",
				title="seed_pi_po_ref_field",
			)
	else:
		try:
			frappe.get_doc(field).insert(
				ignore_if_duplicate=True, ignore_permissions=True
			)
			frappe.logger().info(f"[seed_pi_po_ref] Created {cf_name}")
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"seed_pi_po_ref insert failure: {e}",
				title="seed_pi_po_ref_field",
			)

	frappe.clear_cache(doctype="Purchase Invoice")


def populate_ts_po_reference(doc, method=None):
	"""doc_events handler fired on Purchase Invoice `validate`.

	Computes distinct purchase_order values from doc.items and writes them
	comma-separated to doc.ts_po_reference. Preserves order-of-appearance
	so first PO listed is the first PI row's PO.

	Safe on:
	- Empty items table → ts_po_reference = ""
	- Items without purchase_order (manual invoices) → that item skipped
	- Cancelled/Return PIs → still fires, just reflects linked POs
	"""
	if not getattr(doc, "items", None):
		doc.ts_po_reference = ""
		return

	seen = set()
	ordered = []
	for item in doc.items:
		po = getattr(item, "purchase_order", None)
		if po and po not in seen:
			seen.add(po)
			ordered.append(po)

	doc.ts_po_reference = ", ".join(ordered) if ordered else ""
