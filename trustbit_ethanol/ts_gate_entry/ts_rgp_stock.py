# Copyright (c) 2026, Trustbit Software and contributors
# RGP stock leg (v2.48.0, decision D4 "hybrid") — the OPTIONAL ledger mirror
# of the pass register.
#
# Enabled ONLY while TS Settings.ts_rgp_out_warehouse is set (a Link field —
# warehouses are configuration, never hardcoded; blank = register-only, the
# pre-existing behaviour, so prod is unchanged until the field is set).
#
# Movements:
# - G1 final exit OUT  → Material Transfer: line.warehouse → out-warehouse
#   for every STOCK line (is_stock_item; service items and no-stock assets
#   stay register-only). Created + submitted BEFORE the endorsement stamps
#   (L288: side effect first, status last) — if the ledger move fails, the
#   gate endorsement fails loudly rather than letting stock truth drift.
# - Each credited return lot → reverse transfer out-warehouse → line.warehouse
#   for the lot's stock items, before the status flip in record_rgp_return.
# - Close-short: the written-off balance REMAINS in the out-warehouse as
#   visible stranded stock — accounting writes it off deliberately (the
#   close-short trail row says so); this module never destroys stock.
#
# ignore_permissions on the Stock Entry is deliberate (L224 pattern): the
# acting user (a gate guard / stores user) has been authorized by the calling
# endpoint's role gate + has_permission fence; guards hold no Stock Entry
# DocPerms by design.

import frappe
from frappe import _
from frappe.utils import cstr, flt


def rgp_out_warehouse(strict=False):
	"""Config read. strict=True (the transfer paths) lets a read failure
	PROPAGATE — security #3: a silent None there would fail-open the ledger
	mirror while the endorsement reports success. strict=False stays fail-soft
	for advisory reads (the close-short stranded-stock note)."""
	if strict:
		val = frappe.db.sql(
			"""SELECT value FROM `tabSingles`
			   WHERE doctype = 'TS Settings' AND field = 'ts_rgp_out_warehouse'
			   LIMIT 1""")
		return cstr(val[0][0]) if val and val[0][0] else None
	try:
		return rgp_out_warehouse(strict=True)
	except Exception:
		return None


def _stock_rows(doc, qty_by_row=None):
	"""RGP lines eligible for a ledger move. qty_by_row limits to a return
	lot ({item row name: qty}); None means the full outstanding qty_out."""
	rows = []
	for line in (doc.items or []):
		if not line.warehouse:
			continue
		if not frappe.get_cached_value("Item", line.item_code, "is_stock_item"):
			continue
		qty = flt(qty_by_row.get(line.name)) if qty_by_row is not None \
			else flt(line.qty_out)
		if qty <= 0:
			continue
		rows.append((line, qty))
	return rows


def _make_transfer(doc, rows, direction):
	"""direction 'out' = line.warehouse → out-wh; 'in' = the reverse."""
	out_wh = rgp_out_warehouse(strict=True)
	if not out_wh or not rows:
		return None
	se = frappe.new_doc("Stock Entry")
	se.purpose = se.stock_entry_type = "Material Transfer"
	se.company = doc.company
	se.remarks = _("RGP {0} — {1} transfer ({2})").format(
		doc.name, _("repair-out") if direction == "out" else _("repair-return"),
		doc.supplier_name or doc.supplier)
	for line, qty in rows:
		row = {
			"item_code": line.item_code,
			"qty": qty,
			"uom": line.uom,
			"s_warehouse": line.warehouse if direction == "out" else out_wh,
			"t_warehouse": out_wh if direction == "out" else line.warehouse,
		}
		# Security #4: v15 builds a serial bundle from serial_no and validates
		# count == qty — attach serials only when the FULL outbound qty moves
		# (partial serialized lots would hard-throw with an opaque message).
		if (cstr(line.serial_no_out).strip()
				and flt(qty) == flt(line.qty_out)):
			row["serial_no"] = line.serial_no_out
		se.append("items", row)
	se.flags.ignore_permissions = True
	se.insert(ignore_permissions=True)
	se.submit()
	return se.name


def make_out_transfer(doc):
	"""Called by ts_rgp_gate G1-exit BEFORE the endorsement stamps. Throws on
	ledger failure (insufficient stock / missing valuation) so the gate action
	fails atomically — never a stamped exit with an unmoved ledger."""
	rows = _stock_rows(doc)
	try:
		return _make_transfer(doc, rows, "out")
	except Exception as e:
		frappe.log_error(title="RGP out-transfer failed",
			message=frappe.get_traceback())
		frappe.throw(
			_("Stock transfer to the repair warehouse failed — the exit was "
			  "NOT endorsed. Check stock balance/valuation of the pass items "
			  "or clear TS Settings › RGP Out Warehouse to run register-only. "
			  "({0})").format(cstr(e)[:120]))


def make_return_transfer(doc, qty_by_row):
	"""Called by record_rgp_return for the credited lot, before the status
	flip. Throws on failure so the lot is not credited without its ledger
	move (same atomicity doctrine)."""
	rows = _stock_rows(doc, qty_by_row=qty_by_row)
	try:
		return _make_transfer(doc, rows, "in")
	except Exception as e:
		frappe.log_error(title="RGP return-transfer failed",
			message=frappe.get_traceback())
		frappe.throw(
			_("Reverse stock transfer from the repair warehouse failed — the "
			  "return was NOT recorded. Check the repair-warehouse balance or "
			  "clear TS Settings › RGP Out Warehouse to run register-only. "
			  "({0})").format(cstr(e)[:120]))
