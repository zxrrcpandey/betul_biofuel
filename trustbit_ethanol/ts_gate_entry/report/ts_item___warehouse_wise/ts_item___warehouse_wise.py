"""TS Item - Warehouse Wise — items × warehouse × cost center.

Dual-layout Script Report:
  - PIVOT mode (active warehouses <= ts_pivot_warehouse_threshold, default 10):
    rows = CC > Item; warehouses as columns; TOTAL Qty + TOTAL Value at right.
  - TREE mode  (active warehouses >  ts_pivot_warehouse_threshold):
    rows = CC > Item > Warehouse; per-warehouse rate/value visible inline.

Reuses the production-proven SLE<->GLE join from `ts_stock_ledger_fifo.py`
(lines 71-89): expense-account-preferred CC per voucher, LEFT JOIN so vouchers
with no CC fall to '__UNALLOCATED__'. Reconciliation invariant: sum(qty across
all CCs) per (item, warehouse) == ERPNext native Stock Balance qty (proven by
dry-run validation 26-May-2026 with 938/938 matching pairs + 4 net-zero
offsets that are benign per-CC visibility, not drift).

Filters (8): company, as_on_date, warehouse (multi), cost_center (multi),
item_group (multi), item (multi), force_layout (Auto/Pivot/Tree), show_zero
(check), show_offsetting (check — reveals net-zero (item,WH) pairs).

Permission gate: `frappe.has_permission("Stock Ledger Entry", "read")`.
Match conditions: inherits SLE-level user restrictions.
Lesson refs: 13 (dark mode), 173 (workspace reload), 232 (table layout),
175 (parameterised SQL), 168 (TS Setting permlevel=1).
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

ROW_LIMIT = 5000
UNALLOC = "__UNALLOCATED__"
UNALLOC_LABEL = "Unallocated"
DEFAULT_PIVOT_THRESHOLD = 10


def execute(filters=None):
	if not frappe.has_permission("Stock Ledger Entry", "read"):
		frappe.throw(_("Not permitted to read Stock Ledger"), frappe.PermissionError)

	filters = _normalise_filters(filters or {})
	company = filters["company"]
	as_on = filters["as_on_date"]

	rows, truncated = _query_balances(filters)
	# Hide (item, warehouse) pairs that net to zero across CCs unless the user
	# explicitly asks to see them (dry-run found 4 such pairs on demo).
	if not filters.get("show_offsetting"):
		rows = _drop_net_zero_pairs(rows)

	# Active-warehouse-count drives the auto-switch decision (only counts WHs
	# actually present in the filtered result, not all WHs in the system).
	active_wh = sorted({r["warehouse"] for r in rows})
	threshold = (frappe.db.get_single_value(
		"TS Settings", "ts_pivot_warehouse_threshold"
	) or DEFAULT_PIVOT_THRESHOLD)

	force = (filters.get("force_layout") or "Auto").strip()
	if force == "Force Tree":
		mode = "tree"
		banner = None
	elif force == "Force Pivot":
		mode = "pivot"
		banner = None
	elif len(active_wh) > threshold:
		mode = "tree"
		banner = _(
			"Showing tree mode — {0} active warehouses exceed pivot threshold ({1}). "
			"Pick Force Pivot in filters to override, or raise the threshold in "
			"TS Settings > ts_pivot_warehouse_threshold."
		).format(len(active_wh), threshold)
	else:
		mode = "pivot"
		banner = None

	# Build a value-ordered CC list (heaviest CC first; Unallocated last) so
	# the report leads with the most informative rows.
	cc_value = {}
	for r in rows:
		cc_value[r["cost_center"]] = cc_value.get(r["cost_center"], 0) + abs(flt(r["stock_value"]))

	if mode == "pivot":
		columns, data = _render_pivot(rows, active_wh, cc_value, filters)
	else:
		columns, data = _render_tree(rows, cc_value, filters)

	summary = _summary(rows, active_wh, mode, threshold, filters, truncated)
	# Pass banner as `message` (third return value) — Frappe renders it above the table.
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
	for key in ("warehouse", "cost_center", "item_group", "item"):
		val = filters.get(key)
		if not val:
			filters[key] = []
		elif isinstance(val, str):
			val = val.strip()
			if val.startswith("["):
				import json
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
	"""Run the SLE<->GLE pivot query. Returns (rows, truncated)."""
	conds, params = _build_conditions(filters)
	match_cond = frappe.build_match_conditions("Stock Ledger Entry")
	if match_cond:
		conds += f" AND ({match_cond}) "

	sql = f"""
		SELECT
			COALESCE(gle.cost_center, '{UNALLOC}') AS cost_center,
			sle.warehouse,
			sle.item_code,
			COALESCE(item.item_name, sle.item_code)  AS item_name,
			item.stock_uom                            AS uom,
			SUM(sle.actual_qty)                       AS qty,
			SUM(sle.actual_qty * sle.valuation_rate)  AS approx_value,
			MAX(sle.posting_date)                     AS last_posting_date
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabItem` item ON item.name = sle.item_code
		LEFT JOIN (
			SELECT gle_inner.voucher_type, gle_inner.voucher_no,
				COALESCE(
					MIN(CASE WHEN acc.account_type IN
						('Expense Account', 'Cost of Goods Sold',
						 'Direct Expense', 'Indirect Expense')
						THEN gle_inner.cost_center END),
					MIN(gle_inner.cost_center)
				) AS cost_center
			FROM `tabGL Entry` gle_inner
			LEFT JOIN `tabAccount` acc ON acc.name = gle_inner.account
			WHERE gle_inner.is_cancelled = 0
			  AND gle_inner.cost_center IS NOT NULL
			  AND gle_inner.company = %(company)s
			GROUP BY gle_inner.voucher_type, gle_inner.voucher_no
		) gle ON gle.voucher_type = sle.voucher_type
			 AND gle.voucher_no   = sle.voucher_no
		WHERE sle.company = %(company)s
		  AND sle.posting_date <= %(as_on_date)s
		  AND sle.is_cancelled = 0
		  AND sle.docstatus < 2
		  {conds}
		GROUP BY 1, 2, 3
		HAVING qty != 0 OR %(include_zero)s = 1
		ORDER BY cost_center, item_code, warehouse
		LIMIT {ROW_LIMIT + 1}
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]
	# Coerce numeric fields once.
	for r in rows:
		r["qty"] = flt(r["qty"])
		r["approx_value"] = flt(r["approx_value"])
	# Pull authoritative valuation_rate per (item, warehouse) from the LAST
	# non-zero SLE row <= as_on_date — matches ERPNext Stock Balance.
	_attach_valuation_rates(rows, filters)
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
		params["warehouse_list"] = tuple(filters["warehouse"]) or ("__none__",)
	if filters.get("item"):
		conds.append("sle.item_code IN %(item_list)s")
		params["item_list"] = tuple(filters["item"]) or ("__none__",)
	if filters.get("item_group"):
		conds.append("item.item_group IN %(item_group_list)s")
		params["item_group_list"] = tuple(filters["item_group"]) or ("__none__",)
	if filters.get("cost_center"):
		# Filter applied AFTER the GLE join, on the resolved CC value (incl. Unallocated).
		# Accept "__UNALLOCATED__" or the labelled value, allow either.
		cc_list = list(filters["cost_center"])
		# Normalise UI value -> internal sentinel
		cc_list = [UNALLOC if c == UNALLOC_LABEL else c for c in cc_list]
		conds.append("COALESCE(gle.cost_center, %(unalloc_sent)s) IN %(cc_list)s")
		params["cc_list"] = tuple(cc_list) or ("__none__",)
		params["unalloc_sent"] = UNALLOC
	return (" AND " + " AND ".join(conds)) if conds else "", params


def _attach_valuation_rates(rows, filters):
	"""For each unique (item, warehouse), look up the LAST SLE valuation_rate
	on or before as_on_date with qty_after_transaction > 0. Falls back to the
	row's averaged approx_value/qty if no positive-balance SLE is found.
	"""
	if not rows:
		return
	pairs = sorted({(r["item_code"], r["warehouse"]) for r in rows})
	rate_map = {}
	for item_code, wh in pairs:
		v = frappe.db.sql(
			"""
			SELECT valuation_rate FROM `tabStock Ledger Entry`
			WHERE item_code = %s AND warehouse = %s
			  AND posting_date <= %s AND is_cancelled = 0 AND docstatus < 2
			ORDER BY posting_date DESC, posting_time DESC, creation DESC
			LIMIT 1
			""",
			(item_code, wh, filters["as_on_date"]),
		)
		rate_map[(item_code, wh)] = flt(v[0][0]) if v else 0
	for r in rows:
		auth = rate_map.get((r["item_code"], r["warehouse"]), 0)
		r["valuation_rate"] = auth
		# Authoritative stock_value = qty * authoritative rate
		r["stock_value"] = flt(r["qty"]) * auth


def _drop_net_zero_pairs(rows):
	"""Remove (item, warehouse) groups whose qty sums to zero across all CCs.
	Hides per-CC offsetting movements that don't reflect current stock.
	"""
	by_iw = {}
	for r in rows:
		by_iw.setdefault((r["item_code"], r["warehouse"]), []).append(r)
	keep = []
	for grp in by_iw.values():
		if abs(sum(flt(r["qty"]) for r in grp)) > 0.0001:
			keep.extend(grp)
	return keep


# ----------------------------------------------------------------- PIVOT mode

def _render_pivot(rows, active_wh, cc_value, filters):
	"""Pivot: CC > Item rows; warehouses as columns; TOTAL Qty + Value on right."""
	currency_opt = "Company:company:default_currency"
	wh_slug_map = {w: f"wh_{_slug(w)}" for w in active_wh}

	columns = [
		{"fieldname": "name", "label": _("Cost Center / Item"), "fieldtype": "Data", "width": 320},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 70},
	]
	for w in active_wh:
		# disable_total: items in one warehouse mix UOMs (Kg, Quintal, MT, Nos,
		# Mtr, …) so a column total is physically meaningless.
		columns.append({
			"fieldname": wh_slug_map[w], "label": w, "fieldtype": "Float",
			"width": 130, "precision": 2, "disable_total": 1,
		})
	columns += [
		# Same reason: total_qty would sum across UOMs.
		{"fieldname": "total_qty", "label": _("TOTAL Qty"), "fieldtype": "Float", "width": 130, "precision": 2, "disable_total": 1},
		{"fieldname": "total_value", "label": _("TOTAL Value"), "fieldtype": "Currency", "options": currency_opt, "width": 150},
		{"fieldname": "pct_of_grand", "label": _("% of Grand"), "fieldtype": "Percent", "width": 90, "disable_total": 1},
	]

	# Build CC -> Item -> per-WH qty
	tree = {}  # cc -> { (item_code, item_name, uom) -> { wh: {"qty": x, "value": y} } }
	for r in rows:
		cc = r["cost_center"]
		ik = (r["item_code"], r["item_name"], r["uom"] or "")
		bucket = tree.setdefault(cc, {}).setdefault(ik, {})
		cell = bucket.setdefault(r["warehouse"], {"qty": 0, "value": 0})
		cell["qty"] += flt(r["qty"])
		cell["value"] += flt(r["stock_value"])

	grand_total_value = sum(
		sum(c["value"] for c in cells.values())
		for items in tree.values()
		for cells in items.values()
	) or 1

	data = []
	for cc in _sorted_ccs(tree.keys(), cc_value):
		cc_items = tree[cc]
		cc_total_qty = sum(sum(c["qty"] for c in cells.values()) for cells in cc_items.values())
		cc_total_value = sum(sum(c["value"] for c in cells.values()) for cells in cc_items.values())
		cc_row = {
			"name": _UNALLOC_DISP if cc == UNALLOC else cc,
			"item_code": "", "uom": "",
			"total_qty": cc_total_qty,
			"total_value": cc_total_value,
			"pct_of_grand": (cc_total_value / grand_total_value * 100) if grand_total_value else 0,
			"indent": 0, "is_group": 1, "_is_unalloc": (cc == UNALLOC),
		}
		for w in active_wh:
			cc_row[wh_slug_map[w]] = sum(
				flt(cells.get(w, {}).get("qty", 0)) for cells in cc_items.values()
			)
		data.append(cc_row)
		# Item rows (sorted by descending total value within the CC)
		def item_total_value(kv):
			return sum(c["value"] for c in kv[1].values())
		for (item_code, item_name, uom), cells in sorted(cc_items.items(), key=item_total_value, reverse=True):
			item_qty = sum(c["qty"] for c in cells.values())
			item_value = sum(c["value"] for c in cells.values())
			item_row = {
				"name": item_name or item_code,
				"item_code": item_code, "uom": uom,
				"total_qty": item_qty,
				"total_value": item_value,
				"pct_of_grand": (item_value / cc_total_value * 100) if cc_total_value else 0,
				"indent": 1, "is_group": 0, "parent": cc_row["name"],
			}
			for w in active_wh:
				item_row[wh_slug_map[w]] = flt(cells.get(w, {}).get("qty", 0))
			data.append(item_row)

	# NOTE: Frappe's `add_total_row=1` (in the JSON) auto-renders a distinct
	# "Total" strip at the bottom that sums numeric columns of LEAF rows
	# (skipping rows with `is_group=1`). No manual Grand Total append needed.
	return columns, data


# ----------------------------------------------------------------- TREE mode

def _render_tree(rows, cc_value, filters):
	"""Tree: CC > Item > Warehouse rows; item row rolls up across its warehouses."""
	currency_opt = "Company:company:default_currency"
	columns = [
		{"fieldname": "name", "label": _("Cost Center / Item / Warehouse"), "fieldtype": "Data", "width": 380},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 70},
		# disable_total — mixed UOM sum is physically meaningless.
		{"fieldname": "qty", "label": _("Stock Qty"), "fieldtype": "Float", "width": 120, "precision": 2, "disable_total": 1},
		{"fieldname": "valuation_rate", "label": _("Valuation Rate"), "fieldtype": "Currency", "options": currency_opt, "width": 140, "disable_total": 1},
		{"fieldname": "stock_value", "label": _("Stock Value"), "fieldtype": "Currency", "options": currency_opt, "width": 150},
		{"fieldname": "pct_of_parent", "label": _("% of CC"), "fieldtype": "Percent", "width": 90, "disable_total": 1},
	]

	tree = {}  # cc -> item_key -> [row,...]
	for r in rows:
		ik = (r["item_code"], r["item_name"], r["uom"] or "")
		tree.setdefault(r["cost_center"], {}).setdefault(ik, []).append(r)

	data = []
	for cc in _sorted_ccs(tree.keys(), cc_value):
		cc_items = tree[cc]
		cc_qty = sum(flt(r["qty"]) for grp in cc_items.values() for r in grp)
		cc_total_value = sum(flt(r["stock_value"]) for grp in cc_items.values() for r in grp)
		cc_label = _UNALLOC_DISP if cc == UNALLOC else cc
		data.append({
			"name": cc_label, "item_code": "", "uom": "",
			"qty": cc_qty, "valuation_rate": None, "stock_value": cc_total_value,
			"pct_of_parent": 100.0 if cc_total_value else 0,
			"indent": 0, "is_group": 1, "_is_unalloc": (cc == UNALLOC),
		})
		# Item rows sorted by desc value
		def item_value(kv):
			return sum(flt(r["stock_value"]) for r in kv[1])
		for (item_code, item_name, uom), grp in sorted(cc_items.items(), key=item_value, reverse=True):
			i_qty = sum(flt(r["qty"]) for r in grp)
			i_value = sum(flt(r["stock_value"]) for r in grp)
			# Weighted-avg valuation rate
			i_rate = (i_value / i_qty) if i_qty else 0
			data.append({
				"name": item_name or item_code,
				"item_code": item_code, "uom": uom,
				"qty": i_qty, "valuation_rate": i_rate, "stock_value": i_value,
				"pct_of_parent": (i_value / cc_total_value * 100) if cc_total_value else 0,
				"indent": 1, "is_group": (len(grp) > 1), "parent": cc_label,
			})
			# Warehouse leaves only if item is in >1 warehouse OR if only 1 WH show it for consistency
			if len(grp) > 1 or filters.get("show_single_wh_breakdown"):
				for wh_row in sorted(grp, key=lambda x: flt(x["stock_value"]), reverse=True):
					data.append({
						"name": f"↳ {wh_row['warehouse']}",
						"item_code": "", "uom": uom,
						"qty": flt(wh_row["qty"]),
						"valuation_rate": flt(wh_row["valuation_rate"]),
						"stock_value": flt(wh_row["stock_value"]),
						"pct_of_parent": (flt(wh_row["stock_value"]) / i_value * 100) if i_value else 0,
						"indent": 2, "is_group": 0, "parent": item_name or item_code,
					})

	# NOTE: Frappe's `add_total_row=1` auto-renders the Total strip; no manual.
	return columns, data


# ----------------------------------------------------------------- summary

def _summary(rows, active_wh, mode, threshold, filters, truncated):
	item_codes = {r["item_code"] for r in rows}
	ccs = {r["cost_center"] for r in rows}
	total_value = sum(flt(r["stock_value"]) for r in rows)
	unalloc_value = sum(flt(r["stock_value"]) for r in rows if r["cost_center"] == UNALLOC)
	unalloc_pct = (unalloc_value / total_value * 100) if total_value else 0

	out = [
		{"label": _("Total Items"), "value": len(item_codes), "datatype": "Int"},
		# Frappe's summary card formats Currency using the system default
		# currency. The `currency` key would override; we omit it so it
		# correctly uses INR (don't pass the company NAME as a currency code).
		{"label": _("Total Stock Value"), "value": total_value, "datatype": "Currency"},
		{"label": _("Cost Centers"), "value": len(ccs), "datatype": "Int"},
		{"label": _("Active Warehouses"), "value": len(active_wh), "datatype": "Int"},
		{"label": _("Unallocated %"), "value": round(unalloc_pct, 1), "datatype": "Percent",
		 "indicator": "Orange" if unalloc_pct > 20 else "Green"},
		{"label": _("Layout"), "value": mode.upper(),
		 "datatype": "Data",
		 "indicator": "Blue" if mode == "pivot" else "Yellow"},
	]
	any_filter = any(filters.get(k) for k in ("warehouse", "cost_center", "item_group", "item")) \
		or filters.get("show_zero") or filters.get("show_offsetting")
	if any_filter:
		out.append({"label": _("Active Filter"), "value": _("Scoped"),
		            "datatype": "Data", "indicator": "Purple"})
	if truncated:
		out.append({"label": _("Notice"),
		            "value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Red"})
	return out


# ----------------------------------------------------------------- helpers

def _sorted_ccs(ccs, cc_value=None):
	"""Order CCs by abs(total value) descending so the heaviest CC surfaces
	first; push Unallocated to the end regardless of value. Falls back to
	alphabetical order when no value map is provided.
	"""
	cc_value = cc_value or {}
	real = [c for c in ccs if c != UNALLOC]
	real.sort(key=lambda c: (-abs(cc_value.get(c, 0)), c))
	tail = [UNALLOC] if UNALLOC in ccs else []
	return real + tail


_UNALLOC_DISP = f"⚠ {UNALLOC_LABEL}"


def _slug(s):
	"""Make a SQL/JS-safe column key from a warehouse name."""
	out = []
	for ch in (s or "").lower():
		if ch.isalnum():
			out.append(ch)
		elif out and out[-1] != "_":
			out.append("_")
	return "".join(out).strip("_") or "wh"
