# TS Open PO Report — BBPL Report.xlsx spec (27 Jul 2026)
#
# ONE ROW PER PURCHASE ORDER ITEM of a submitted PO that is still open.
# Spec columns (BBPL Report.xlsx -> "Open PO Report"):
#   S.No. | item name | item code | Item Remark | PO ID | MR ID | Supplier |
#   Cost center | PO Amount | Ordered Qty | Received Qty | PO Balance Qty |
#   Delivery Date | PO Date | PO Creater
#
# Row grain changed from PO -> PO Item in this revision. "Open" keeps exactly
# the PO-level definition it always had: docstatus=1, status not in
# Closed/Completed/Delivered/Cancelled/Stopped, and per_received < 100 unless
# "Include Fully Received" is ticked — so the SET OF POs shown is unchanged;
# each one now contributes its item lines. "Hide Fully Received Lines" drops
# lines already fully received on an otherwise-open PO.
#
# PO Amount is the PO header's Rounded Total (per spec) and therefore REPEATS
# on every item row of that PO. The column carries disable_total so an
# accidental "Add Total Row" cannot sum it into a meaningless figure; the same
# guard applies to the mixed-UOM qty columns.
#
# Confidentiality: maize/grain-confidential POs are hidden from non-allow-listed
# users via confidential_sql_clause (Lesson 297 — raw SQL bypasses the
# permission_query_conditions hook).

import frappe
from frappe import _
from frappe.utils import flt, getdate, strip_html

ROW_LIMIT = 5000
CLOSED_STATES = ("Closed", "Completed", "Delivered", "Cancelled", "Stopped")


def execute(filters=None):
	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("Not permitted to read Purchase Order"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	data, truncated = get_data(filters)
	return get_columns(), data, None, None, _summary(data, truncated)


def _validate_filters(filters):
	frm, to = filters.get("from_date"), filters.get("to_date")
	if frm and to and getdate(frm) > getdate(to):
		frappe.throw(_("PO Date From cannot be after PO Date To"))


def get_columns():
	currency = "Company:company:default_currency"
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 65, "disable_total": 1},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 220},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "item_remark", "label": _("Item Remark"), "fieldtype": "Data", "width": 200},
		{"fieldname": "po_no", "label": _("PO ID"), "fieldtype": "Link", "options": "Purchase Order", "width": 175},
		{"fieldname": "mr_no", "label": _("MR ID"), "fieldtype": "Link", "options": "Material Request", "width": 180},
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Link", "options": "Supplier", "width": 200},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 185},
		# Header-level value repeated per item row -> never total it.
		{"fieldname": "po_amount", "label": _("PO Amount"), "fieldtype": "Currency", "options": currency, "width": 135, "disable_total": 1},
		# Item lines mix UOMs (Kg / MT / Nos / Ltr) -> a column sum is meaningless.
		{"fieldname": "ordered_qty", "label": _("Ordered Qty"), "fieldtype": "Float", "width": 110, "precision": 2, "disable_total": 1},
		{"fieldname": "received_qty", "label": _("Received Qty"), "fieldtype": "Float", "width": 115, "precision": 2, "disable_total": 1},
		{"fieldname": "balance_qty", "label": _("PO Balance Qty"), "fieldtype": "Float", "width": 125, "precision": 2, "disable_total": 1},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Data", "width": 75},
		{"fieldname": "delivery_date", "label": _("Delivery Date"), "fieldtype": "Date", "width": 115},
		{"fieldname": "po_date", "label": _("PO Date"), "fieldtype": "Date", "width": 105},
		{"fieldname": "po_creator", "label": _("PO Creator"), "fieldtype": "Data", "width": 175},
	]


def get_data(filters):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause

	params = {"limit": ROW_LIMIT + 1, "closed_states": CLOSED_STATES}
	clauses = ["po.docstatus = 1", "po.status NOT IN %(closed_states)s"]

	# Confidentiality leak-plug: hide maize-confidential POs from non-allowed
	# users. confidential_sql_clause returns a fragment starting with " AND (";
	# strip that leading AND so it joins cleanly with the clause list below.
	conf = confidential_sql_clause("po")
	if conf:
		clauses.append(conf.strip()[4:].strip())

	# Inherit any Purchase Order user-permission restrictions. build_match_conditions
	# qualifies columns with the full table name; this query aliases it as `po`.
	match_cond = frappe.build_match_conditions("Purchase Order")
	if match_cond:
		clauses.append("(%s)" % match_cond.replace("`tabPurchase Order`", "po"))

	if not filters.get("include_fully_received"):
		clauses.append("po.per_received < 100")
	if filters.get("hide_fully_received_lines"):
		clauses.append("IFNULL(poi.received_qty, 0) < poi.qty")
	if filters.get("cost_center"):
		# match either the PO header CC or this line's CC
		clauses.append("(po.cost_center = %(cost_center)s OR poi.cost_center = %(cost_center)s)")
		params["cost_center"] = filters["cost_center"]
	if filters.get("supplier"):
		clauses.append("po.supplier = %(supplier)s")
		params["supplier"] = filters["supplier"]
	if filters.get("item_code"):
		clauses.append("poi.item_code = %(item_code)s")
		params["item_code"] = filters["item_code"]
	if filters.get("from_date"):
		clauses.append("po.transaction_date >= %(from_date)s")
		params["from_date"] = getdate(filters["from_date"])
	if filters.get("to_date"):
		clauses.append("po.transaction_date <= %(to_date)s")
		params["to_date"] = getdate(filters["to_date"])

	where = " AND ".join(clauses)

	rows = frappe.db.sql(
		f"""
		SELECT
			poi.item_name                                        AS item_name,
			poi.item_code                                        AS item_code,
			poi.ts_item_remark                                   AS item_remark,
			po.name                                              AS po_no,
			poi.material_request                                 AS mr_no,
			po.supplier                                          AS supplier,
			COALESCE(NULLIF(poi.cost_center, ''), NULLIF(po.cost_center, '')) AS cost_center,
			COALESCE(NULLIF(po.rounded_total, 0), po.grand_total) AS po_amount,
			poi.qty                                              AS ordered_qty,
			IFNULL(poi.received_qty, 0)                          AS received_qty,
			poi.uom                                              AS uom,
			COALESCE(poi.schedule_date, po.schedule_date)        AS delivery_date,
			po.transaction_date                                  AS po_date,
			po.owner                                             AS po_owner
		FROM `tabPurchase Order Item` poi
		JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE {where}
		ORDER BY po.transaction_date DESC, po.name, poi.idx
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)

	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]

	names = _fullname_map([r.get("po_owner") for r in rows])
	for i, r in enumerate(rows, start=1):
		r["sr"] = i
		r["balance_qty"] = flt(r.get("ordered_qty")) - flt(r.get("received_qty"))
		r["po_creator"] = names.get(r.pop("po_owner", None) or "", "")
		# Item Remark is free text typed on the PO line and the datatable renders
		# cell content as HTML — strip tags so a pasted "<img onerror=...>" cannot
		# execute in a report a CEO/MD opens (same treatment the MR report gives
		# its Description column).
		if r.get("item_remark"):
			r["item_remark"] = " ".join(strip_html(r["item_remark"]).split())

	return rows, truncated


def _fullname_map(user_ids):
	"""user_id -> full name, in one query.

	Deliberately raw SQL: operators frequently lack `User` read permission
	(Lesson 168), and a creator/approver display name is not confidential —
	it is already surfaced on the PO form itself.
	"""
	ids = sorted({u for u in user_ids if u})
	if not ids:
		return {}
	rows = frappe.db.sql(
		"SELECT name, full_name FROM `tabUser` WHERE name IN %(ids)s",
		{"ids": tuple(ids)},
		as_dict=True,
	)
	return {r["name"]: (r["full_name"] or r["name"]) for r in rows}


def _summary(data, truncated):
	distinct_pos = len({r.get("po_no") for r in data if r.get("po_no")})
	distinct_items = len({r.get("item_code") for r in data if r.get("item_code")})
	# PO Amount repeats per line — count it once per PO, not per row.
	seen, open_value = set(), 0.0
	for r in data:
		po = r.get("po_no")
		if po and po not in seen:
			seen.add(po)
			open_value += flt(r.get("po_amount"))
	pending_lines = sum(1 for r in data if flt(r.get("balance_qty")) > 0)

	out = [
		{"label": _("Open POs"), "value": distinct_pos, "datatype": "Int"},
		{"label": _("Item Lines"), "value": len(data), "datatype": "Int"},
		{"label": _("Distinct Items"), "value": distinct_items, "datatype": "Int"},
		{"label": _("Lines Pending"), "value": pending_lines, "datatype": "Int",
		 "indicator": "Orange" if pending_lines else "Green"},
		{"label": _("Open PO Value"), "value": open_value, "datatype": "Currency"},
	]
	if truncated:
		out.append({
			"label": _("Notice"),
			"value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
			"datatype": "Data",
			"indicator": "Red",
		})
	return out
