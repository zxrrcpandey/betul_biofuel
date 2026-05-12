import frappe
from frappe import _
from frappe.utils import flt, getdate

ROW_LIMIT = 5000


def execute(filters=None):
	if not frappe.has_permission("Stock Ledger Entry", "read"):
		frappe.throw(_("Not permitted to read Stock Ledger"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	columns = get_columns()
	data, truncated = get_data(filters)
	report_summary = _get_summary(data, truncated)
	return columns, data, None, None, report_summary


def _validate_filters(filters):
	if not filters.get("company"):
		filters["company"] = frappe.defaults.get_user_default("Company")
	if not filters.get("company"):
		frappe.throw(_("Company is required"))
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are required"))
	if getdate(filters["from_date"]) > getdate(filters["to_date"]):
		frappe.throw(_("From Date cannot be after To Date"))


def get_columns():
	return [
		{"fieldname": "posting_datetime", "label": _("Posted At"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "voucher_type", "label": _("Voucher Type"), "fieldtype": "Link", "options": "DocType", "width": 130},
		{"fieldname": "voucher_no", "label": _("Voucher No"), "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 150},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 140},
		{"fieldname": "warehouse", "label": _("Warehouse"), "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 140},
		{"fieldname": "in_qty", "label": _("In Qty"), "fieldtype": "Float", "width": 90, "precision": 3},
		{"fieldname": "out_qty", "label": _("Out Qty"), "fieldtype": "Float", "width": 90, "precision": 3},
		{"fieldname": "qty_after_transaction", "label": _("Balance Qty"), "fieldtype": "Float", "width": 100, "precision": 3},
		{"fieldname": "valuation_rate", "label": _("Valuation Rate (FIFO)"), "fieldtype": "Currency", "options": "Company:company:default_currency", "width": 140},
		{"fieldname": "total_amount", "label": _("Total Amount"), "fieldtype": "Currency", "options": "Company:company:default_currency", "width": 130},
		{"fieldname": "stock_value", "label": _("Stock Value (Balance)"), "fieldtype": "Currency", "options": "Company:company:default_currency", "width": 140},
		{"fieldname": "valuation_method_display", "label": _("Valuation Method"), "fieldtype": "Data", "width": 110},
	]


def get_data(filters):
	conditions, params = _build_conditions(filters)
	match_cond = frappe.build_match_conditions("Stock Ledger Entry")
	if match_cond:
		conditions += f" AND ({match_cond}) "

	sql = f"""
		SELECT
			sle.name AS sle_name,
			sle.posting_date,
			sle.posting_time,
			TIMESTAMP(sle.posting_date, sle.posting_time) AS posting_datetime,
			sle.voucher_type,
			sle.voucher_no,
			sle.item_code,
			item.valuation_method,
			sle.warehouse,
			sle.actual_qty,
			sle.qty_after_transaction,
			sle.valuation_rate,
			sle.stock_value,
			sle.is_cancelled,
			gle.cost_center AS cost_center
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabItem` item ON item.name = sle.item_code
		LEFT JOIN (
			SELECT
				gle_inner.voucher_type,
				gle_inner.voucher_no,
				COALESCE(
					MIN(CASE WHEN acc.account_type IN ('Expense Account', 'Cost of Goods Sold', 'Direct Expense', 'Indirect Expense')
					    THEN gle_inner.cost_center END),
					MIN(gle_inner.cost_center)
				) AS cost_center
			FROM `tabGL Entry` gle_inner
			LEFT JOIN `tabAccount` acc ON acc.name = gle_inner.account
			WHERE gle_inner.is_cancelled = 0
			  AND gle_inner.cost_center IS NOT NULL
			  AND gle_inner.company = %(company)s
			GROUP BY gle_inner.voucher_type, gle_inner.voucher_no
		) gle ON gle.voucher_type = sle.voucher_type AND gle.voucher_no = sle.voucher_no
		WHERE sle.company = %(company)s
		  AND sle.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  AND sle.docstatus < 2
		  {conditions}
		ORDER BY sle.posting_date ASC, sle.posting_time ASC, sle.creation ASC
		LIMIT {ROW_LIMIT + 1}
	"""

	rows = frappe.db.sql(sql, params, as_dict=True)
	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]

	for row in rows:
		qty = flt(row.actual_qty)
		rate = flt(row.valuation_rate)
		row["in_qty"] = qty if qty > 0 else 0
		row["out_qty"] = abs(qty) if qty < 0 else 0
		row["total_amount"] = abs(qty) * rate
		row["valuation_method_display"] = row.valuation_method or "—"

	return rows, truncated


def _build_conditions(filters):
	conds = []
	params = {
		"company": filters.get("company"),
		"from_date": filters.get("from_date"),
		"to_date": filters.get("to_date"),
	}

	if filters.get("item_code"):
		conds.append("sle.item_code = %(item_code)s")
		params["item_code"] = filters["item_code"]
	if filters.get("item_group"):
		conds.append("item.item_group = %(item_group)s")
		params["item_group"] = filters["item_group"]
	if filters.get("warehouse"):
		conds.append("sle.warehouse = %(warehouse)s")
		params["warehouse"] = filters["warehouse"]
	if filters.get("cost_center"):
		conds.append("gle.cost_center = %(cost_center)s")
		params["cost_center"] = filters["cost_center"]
	if not filters.get("include_cancelled"):
		conds.append("sle.is_cancelled = 0")

	return (" AND " + " AND ".join(conds)) if conds else "", params


def _get_summary(data, truncated):
	total_amount = sum(flt(r.get("total_amount")) for r in data)
	total_in = sum(flt(r.get("in_qty")) for r in data)
	total_out = sum(flt(r.get("out_qty")) for r in data)
	summary = [
		{"label": _("Rows"), "value": len(data), "datatype": "Int"},
		{"label": _("Total In Qty"), "value": total_in, "datatype": "Float"},
		{"label": _("Total Out Qty"), "value": total_out, "datatype": "Float"},
		{"label": _("Total Amount"), "value": total_amount, "datatype": "Currency"},
	]
	if truncated:
		summary.append({
			"label": _("Notice"),
			"value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
			"datatype": "Data",
			"indicator": "Orange",
		})
	return summary
