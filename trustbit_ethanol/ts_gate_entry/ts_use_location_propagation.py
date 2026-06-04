"""v2.16.3 — propagate ts_delivery_location ("Define Use Location") + ts_item_remark
down MR->PO->PR->PI. ERPNext's `fetch_from` is client-only (doesn't fire on the
"Get Items" mapper / programmatic insert; `fetch_if_empty` doesn't help server-side —
both verified), so PO->PR drops the values. These before_validate handlers backfill
the two fields from the linked upstream line WHEN EMPTY (batched, never overwrites).
Idempotent + read-only (no qty/rate/accounting). Paths that already set the fields
(Stores Dashboard, Token GRN) no-op here.
"""

import frappe

_FIELDS = ("ts_delivery_location", "ts_item_remark")


def _is_empty(value):
	return not (value or "").strip()


def _backfill(items, link_field, source_doctype):
	"""Fill empty _FIELDS on each row from the upstream row at row.<link_field>."""
	if not items:
		return
	needed = {}
	for row in items:
		src_name = row.get(link_field)
		if not src_name or all(not _is_empty(row.get(f)) for f in _FIELDS):
			continue
		needed.setdefault(src_name, []).append(row)
	if not needed:
		return
	src_map = {
		r["name"]: r for r in frappe.get_all(
			source_doctype, filters={"name": ["in", list(needed.keys())]},
			fields=["name", *_FIELDS])
	}
	for src_name, rows in needed.items():
		src = src_map.get(src_name)
		if not src:
			continue
		for row in rows:
			for f in _FIELDS:
				if _is_empty(row.get(f)) and not _is_empty(src.get(f)):
					row.set(f, src.get(f))


def purchase_receipt_backfill(doc, method=None):
	"""PR item <- PO Item (via purchase_order_item) — fills the PO->PR gap."""
	_backfill(getattr(doc, "items", None), "purchase_order_item", "Purchase Order Item")


def purchase_invoice_backfill(doc, method=None):
	"""PI item <- PO Item (po_detail); fallback PR Item (pr_detail) for PI-from-PR."""
	items = getattr(doc, "items", None)
	_backfill(items, "po_detail", "Purchase Order Item")
	_backfill(items, "pr_detail", "Purchase Receipt Item")
