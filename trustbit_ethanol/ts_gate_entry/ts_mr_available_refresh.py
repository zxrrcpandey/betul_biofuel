"""v2.9.14.5 — Material Request "Available Qty" live refresh.

ERPNext's `actual_qty` on Material Request Item is a snapshot captured at
MR creation and never refreshes after that. We relabeled it to "Available
Qty" (seed_data.py:194 Property Setter) which gives users the impression
of a live indicator — but underneath it stays frozen forever, even after
new stock arrives.

This onload hook refreshes each item's `actual_qty` from the live `tabBin`
when the MR is opened (in-memory only, no DB write — safe on docstatus=1).
Users see real-time stock; reports/exports still see the original snapshot
(separate concern, not changed here).
"""
import frappe


def refresh_actual_qty_onload(doc, method=None):
	for it in (doc.get("items") or []):
		if not it.item_code or not it.warehouse:
			continue
		bin_qty = frappe.db.get_value(
			"Bin",
			{"item_code": it.item_code, "warehouse": it.warehouse},
			"actual_qty",
		)
		it.actual_qty = bin_qty or 0
