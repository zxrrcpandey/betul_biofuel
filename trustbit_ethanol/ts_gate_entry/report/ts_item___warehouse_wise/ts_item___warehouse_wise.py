"""TS Item - Warehouse Wise — stock of each item in each warehouse.

Two layouts (the server picks):
  - PIVOT (active warehouses <= ts_pivot_warehouse_threshold, default 10):
    one row per item; warehouses as columns; TOTAL Qty + TOTAL Value at right.
  - TREE  (more warehouses): Item > Warehouse rows.

Qty and value come from the LATEST Stock Ledger Entry of each
(item, warehouse) on or before the as-on date, in the same order ERPNext's
Stock Balance uses (posting_date, posting_time, creation). Never
SUM(actual_qty): a Stock Reconciliation row has actual_qty = 0 and carries
the new balance in qty_after_transaction (Lesson 286).

Stock is not held by a cost centre, so there is no cost-centre split
(removed 27 Oct 2026: it showed one item in several +/- rows).

Filters: company, as_on_date, warehouse (multi), item_group (multi),
item (multi), force_layout (Auto/Pivot/Tree), show_zero (check).
Permission gate: `frappe.has_permission("Stock Ledger Entry", "read")`;
SLE user restrictions apply through build_match_conditions.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

ROW_LIMIT = 5000
DEFAULT_PIVOT_THRESHOLD = 10


def execute(filters=None):
	if not frappe.has_permission("Stock Ledger Entry", "read"):
		frappe.throw(_("Not permitted to read Stock Ledger"), frappe.PermissionError)

	filters = _normalise_filters(filters or {})
	rows, truncated = _query_balances(filters)

	# Active-warehouse count drives the auto-switch (only warehouses present
	# in the filtered result, not all warehouses in the system).
	active_wh = sorted({r["warehouse"] for r in rows})
	threshold = (frappe.db.get_single_value(
		"TS Settings", "ts_pivot_warehouse_threshold"
	) or DEFAULT_PIVOT_THRESHOLD)

	force = (filters.get("force_layout") or "Auto").strip()
	banner = None
	if force == "Force Tree":
		mode = "tree"
	elif force == "Force Pivot":
		mode = "pivot"
	elif len(active_wh) > threshold:
		mode = "tree"
		banner = _(
			"Showing tree mode — {0} active warehouses exceed pivot threshold ({1}). "
			"Pick Force Pivot in filters to override, or raise the threshold in "
			"TS Settings > ts_pivot_warehouse_threshold."
		).format(len(active_wh), threshold)
	else:
		mode = "pivot"

	items = {}  # (item_code, item_name, uom) -> [row per warehouse]
	for r in rows:
		items.setdefault((r["item_code"], r["item_name"], r["uom"] or ""), []).append(r)
	# Heaviest item first.
	ordered = sorted(items.items(), key=lambda kv: -sum(flt(r["stock_value"]) for r in kv[1]))

	if mode == "pivot":
		columns, data = _render_pivot(ordered, active_wh)
	else:
		columns, data = _render_tree(ordered)

	summary = _summary(rows, active_wh, mode, filters, truncated)
	# Banner goes as `message` (third return value) — rendered above the table.
	return columns, data, banner, None, summary


# ----------------------------------------------------------------- filters

def _normalise_filters(filters):
	if not filters.get("company"):
		filters["company"] = frappe.defaults.get_user_default("Company")
	if not filters.get("company"):
		frappe.throw(_("Company is required"))
	if not filters.get("as_on_date"):
		filters["as_on_date"] = frappe.utils.today()
	# Multi-select fields may arrive as JSON-strings, comma-strings, or lists.
	for key in ("warehouse", "item_group", "item"):
		val = filters.get(key)
		if not val:
			filters[key] = []
		elif isinstance(val, str):
			val = val.strip()
			if val.startswith("["):
				try:
					filters[key] = [v for v in json.loads(val) if v]
				except Exception:
					filters[key] = [v.strip() for v in val.strip("[]").split(",") if v.strip()]
			elif "," in val:
				filters[key] = [v.strip() for v in val.split(",") if v.strip()]
			else:
				filters[key] = [val] if val else []
	return filters


# ----------------------------------------------------------------- main query

def _query_balances(filters):
	"""Latest SLE per (item, warehouse) <= as_on_date. Returns (rows, truncated)."""
	conds, params = _build_conditions(filters)
	# Match conditions name the table in full, so they go in the inner query
	# where the table has no alias.
	match_cond = frappe.build_match_conditions("Stock Ledger Entry")
	match_sql = f" AND ({match_cond})" if match_cond else ""

	rows = frappe.db.sql(f"""
		SELECT
			sle.warehouse,
			sle.item_code,
			COALESCE(item.item_name, sle.item_code) AS item_name,
			item.stock_uom                          AS uom,
			sle.qty_after_transaction               AS qty,
			sle.valuation_rate,
			sle.stock_value,
			sle.posting_date                        AS last_posting_date
		FROM (
			SELECT `tabStock Ledger Entry`.item_code, `tabStock Ledger Entry`.warehouse,
				`tabStock Ledger Entry`.posting_date, `tabStock Ledger Entry`.qty_after_transaction,
				`tabStock Ledger Entry`.valuation_rate, `tabStock Ledger Entry`.stock_value,
				ROW_NUMBER() OVER (
					PARTITION BY `tabStock Ledger Entry`.item_code, `tabStock Ledger Entry`.warehouse
					ORDER BY `tabStock Ledger Entry`.posting_date DESC,
						`tabStock Ledger Entry`.posting_time DESC,
						`tabStock Ledger Entry`.creation DESC
				) AS _rn
			FROM `tabStock Ledger Entry`
			WHERE `tabStock Ledger Entry`.company = %(company)s
			  AND `tabStock Ledger Entry`.posting_date <= %(as_on_date)s
			  AND `tabStock Ledger Entry`.is_cancelled = 0
			  AND `tabStock Ledger Entry`.docstatus < 2
			  {match_sql}
		) sle
		LEFT JOIN `tabItem` item ON item.name = sle.item_code
		WHERE sle._rn = 1
		  AND (sle.qty_after_transaction != 0 OR %(include_zero)s = 1)
		  {conds}
		ORDER BY sle.item_code, sle.warehouse
		LIMIT {ROW_LIMIT + 1}
	""", params, as_dict=True)

	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]
	for r in rows:
		r["qty"] = flt(r["qty"])
		r["valuation_rate"] = flt(r["valuation_rate"])
		r["stock_value"] = flt(r["stock_value"])
	return rows, truncated


def _build_conditions(filters):
	conds = []
	params = {
		"company": filters["company"],
		"as_on_date": filters["as_on_date"],
		"include_zero": 1 if filters.get("show_zero") else 0,
	}
	if filters.get("warehouse"):
		conds.append("sle.warehouse IN %(warehouse_list)s")
		params["warehouse_list"] = tuple(filters["warehouse"])
	if filters.get("item"):
		conds.append("sle.item_code IN %(item_list)s")
		params["item_list"] = tuple(filters["item"])
	if filters.get("item_group"):
		conds.append("item.item_group IN %(item_group_list)s")
		params["item_group_list"] = tuple(filters["item_group"])
	return (" AND " + " AND ".join(conds)) if conds else "", params


# ----------------------------------------------------------------- PIVOT mode

def _render_pivot(ordered, active_wh):
	"""One row per item; warehouses as columns; TOTAL Qty + Value on right."""
	currency_opt = "Company:company:default_currency"
	wh_slug_map = {w: f"wh_{_slug(w)}" for w in active_wh}

	columns = [
		{"fieldname": "name", "label": _("Item"), "fieldtype": "Data", "width": 320},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 70},
	]
	for w in active_wh:
		# disable_total: items in one warehouse mix UOMs, so a column total is meaningless.
		columns.append({
			"fieldname": wh_slug_map[w], "label": w, "fieldtype": "Float",
			"width": 130, "precision": 2, "disable_total": 1,
		})
	columns += [
		{"fieldname": "total_qty", "label": _("TOTAL Qty"), "fieldtype": "Float", "width": 130, "precision": 2, "disable_total": 1},
		{"fieldname": "total_value", "label": _("TOTAL Value"), "fieldtype": "Currency", "options": currency_opt, "width": 150},
		{"fieldname": "pct_of_grand", "label": _("% of Grand"), "fieldtype": "Percent", "width": 90, "disable_total": 1},
	]

	grand = sum(flt(r["stock_value"]) for _ik, grp in ordered for r in grp) or 1
	data = []
	for (item_code, item_name, uom), grp in ordered:
		value = sum(flt(r["stock_value"]) for r in grp)
		row = {
			"name": item_name or item_code, "item_code": item_code, "uom": uom,
			"total_qty": sum(flt(r["qty"]) for r in grp),
			"total_value": value,
			"pct_of_grand": value / grand * 100,
		}
		for r in grp:
			row[wh_slug_map[r["warehouse"]]] = flt(r["qty"])
		data.append(row)
	return columns, data


# ----------------------------------------------------------------- TREE mode

def _render_tree(ordered):
	"""Item > Warehouse rows; the item row adds up its warehouses."""
	currency_opt = "Company:company:default_currency"
	columns = [
		{"fieldname": "name", "label": _("Item / Warehouse"), "fieldtype": "Data", "width": 380},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 70},
		# disable_total — mixed UOM sum is meaningless.
		{"fieldname": "qty", "label": _("Stock Qty"), "fieldtype": "Float", "width": 120, "precision": 2, "disable_total": 1},
		{"fieldname": "valuation_rate", "label": _("Valuation Rate"), "fieldtype": "Currency", "options": currency_opt, "width": 140, "disable_total": 1},
		{"fieldname": "stock_value", "label": _("Stock Value"), "fieldtype": "Currency", "options": currency_opt, "width": 150},
		{"fieldname": "pct_of_parent", "label": _("% of Item"), "fieldtype": "Percent", "width": 90, "disable_total": 1},
	]

	data = []
	for (item_code, item_name, uom), grp in ordered:
		i_qty = sum(flt(r["qty"]) for r in grp)
		i_value = sum(flt(r["stock_value"]) for r in grp)
		label = item_name or item_code
		data.append({
			"name": label, "item_code": item_code, "uom": uom,
			"qty": i_qty, "valuation_rate": (i_value / i_qty) if i_qty else 0,
			"stock_value": i_value, "pct_of_parent": 100.0 if i_value else 0,
			"indent": 0, "is_group": 1,
		})
		# Always show the warehouse rows — even for a single-warehouse item,
		# WHICH warehouse holds the stock is the point of the report.
		for r in sorted(grp, key=lambda x: -flt(x["stock_value"])):
			data.append({
				"name": f"↳ {r['warehouse']}", "item_code": "", "uom": uom,
				"qty": flt(r["qty"]), "valuation_rate": flt(r["valuation_rate"]),
				"stock_value": flt(r["stock_value"]),
				"pct_of_parent": (flt(r["stock_value"]) / i_value * 100) if i_value else 0,
				"indent": 1, "is_group": 0, "parent": label,
			})
	return columns, data


# ----------------------------------------------------------------- summary

def _summary(rows, active_wh, mode, filters, truncated):
	out = [
		{"label": _("Total Items"), "value": len({r["item_code"] for r in rows}), "datatype": "Int"},
		# No `currency` key: the card then uses the system currency (INR).
		{"label": _("Total Stock Value"), "value": sum(flt(r["stock_value"]) for r in rows), "datatype": "Currency"},
		{"label": _("Active Warehouses"), "value": len(active_wh), "datatype": "Int"},
		{"label": _("Layout"), "value": mode.upper(), "datatype": "Data",
		 "indicator": "Blue" if mode == "pivot" else "Yellow"},
	]
	if any(filters.get(k) for k in ("warehouse", "item_group", "item")) or filters.get("show_zero"):
		out.append({"label": _("Active Filter"), "value": _("Scoped"),
		            "datatype": "Data", "indicator": "Purple"})
	if truncated:
		out.append({"label": _("Notice"),
		            "value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Red"})
	return out


# ----------------------------------------------------------------- helpers

def _slug(s):
	"""Make a SQL/JS-safe column key from a warehouse name."""
	out = []
	for ch in (s or "").lower():
		if ch.isalnum():
			out.append(ch)
		elif out and out[-1] != "_":
			out.append("_")
	return "".join(out).strip("_") or "wh"
