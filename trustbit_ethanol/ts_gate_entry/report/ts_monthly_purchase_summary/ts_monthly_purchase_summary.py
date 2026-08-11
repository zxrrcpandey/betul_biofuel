# TS Monthly Purchase Summary — BBPL Report Updated 11 Aug 2026.xlsx, sheet
# "Monthly Purchase Summary" (Wave 3 #12; plan: PLAN_bbpl_wave3_reports.md)
#
# ONE ROW PER (fiscal-year month x cost centre), month-major (FY order, then CC).
# Included CCs = union of budgeted CCs (submitted Budgets, budget_against =
# 'Cost Center' ONLY — Project budgets excluded) and CCs carrying attributed
# spend. A CC with zero budget AND zero spend never appears.
#
#   Allowed  = SUM(Budget Account.budget_amount) per CC, split by the budget's
#              Monthly Distribution percentages, DUST-NORMALISED so the 12
#              months sum exactly to the annual figure (residual onto the FY's
#              final month); /12 fallback when no distribution.
#   Purchase = SUM(poi.base_net_amount) attributed to COALESCE(item CC, header
#              CC), grouped by month(po.transaction_date). Base currency,
#              pre-tax. Doubly-blank CC rows are excluded and surfaced on the
#              "Unattributed Spend" card, never silently dropped.
#   Balance  = Allowed - Purchase (month-local, signed)          [sheet col F]
#   Avail_m  = Allowed_m + carry_(m-1);  carry seeds at 0.
#   Extra expense     = max(0, Purchase_m - Avail_m)             [sheet col G]
#   Balance allocated = max(0, Avail_m - Purchase_m)             [sheet col H]
#   carry_m  = Avail_m - Purchase_m (signed — overrun reduces next month,
#              surplus adds; exactly the rule written on the client's sheet).
#   The month window filters DISPLAY only — the recurrence always runs from
#   FY month 1 so a sliced view cannot change the carry-forward.
#
# ROLES are the PO-confidentiality allow-list intersection ONLY (plan R1):
# System Manager, IT Head, Accounts Manager, Accounts User, CEO, MD. Purchase
# Manager / General Manager / AVP would see silently undercounted spend against
# the confidential POs; widening the list = MAIZE_PO_CONFIDENTIAL decision.
# The spend query still carries confidential_sql_clause + match conditions
# (house rule) — a no-op '' for every permitted viewer.

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate

from trustbit_ethanol.ts_gate_entry.report import report_utils as ru


ROW_LIMIT = 5000


def execute(filters=None):
	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("Not permitted to read Purchase Order"), frappe.PermissionError)

	filters = filters or {}
	months = _validate_filters(filters)
	rows, truncated, unattributed = get_data(filters, months)
	return get_columns(), rows, None, None, _summary(rows, truncated, unattributed)


def _fy_months(fiscal_year):
	fy = frappe.db.get_value("Fiscal Year", fiscal_year,
	                         ["year_start_date", "year_end_date"], as_dict=True)
	if not fy:
		frappe.throw(_("Invalid Fiscal Year"))
	months = []
	d = getdate(fy.year_start_date)
	for _i in range(12):
		nxt = getdate(add_months(d, 1))
		months.append({"label": d.strftime("%b %Y"), "key": d.strftime("%Y-%m"),
		               "start": d, "end": min(getdate(add_months(d, 1)) , getdate(fy.year_end_date)) if _i == 11 else nxt,
		               "month_name": d.strftime("%B")})
		d = nxt
	# month ranges: [start, next_start) — last month capped at year_end inclusive
	months[-1]["end"] = getdate(fy.year_end_date)
	return months, getdate(fy.year_start_date), getdate(fy.year_end_date)


def _validate_filters(filters):
	if not filters.get("fiscal_year"):
		frappe.throw(_("Fiscal Year is required"))
	if not frappe.db.exists("Fiscal Year", filters["fiscal_year"]):
		frappe.throw(_("Invalid Fiscal Year"))
	if not filters.get("company"):
		filters["company"] = frappe.defaults.get_global_default("company")
	if not filters.get("company") or not frappe.db.exists("Company", filters["company"]):
		frappe.throw(_("Invalid Company"))
	months, _s, _e = _fy_months(filters["fiscal_year"])
	labels = [m["label"] for m in months]
	for f in ("from_month", "to_month"):
		if filters.get(f) and filters[f] not in labels:
			frappe.throw(_("Invalid {0}").format(f))
	if filters.get("from_month") and filters.get("to_month"):
		if labels.index(filters["from_month"]) > labels.index(filters["to_month"]):
			frappe.throw(_("From Month is after To Month"))
	return months


def get_columns():
	cur = "Company:company:default_currency"
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 60, "disable_total": 1},
		{"fieldname": "month", "label": _("Month"), "fieldtype": "Data", "width": 100},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 220},
		{"fieldname": "allowed_amount", "label": _("Allowed Amount"), "fieldtype": "Currency", "options": cur, "width": 140},
		{"fieldname": "purchase_value", "label": _("Purchase Value"), "fieldtype": "Currency", "options": cur, "width": 140},
		{"fieldname": "balance", "label": _("Balance"), "fieldtype": "Currency", "options": cur, "width": 135},
		{"fieldname": "extra_expense", "label": _("Extra Expense"), "fieldtype": "Currency", "options": cur, "width": 130},
		{"fieldname": "balance_allocated", "label": _("Balance Allocated Amount"), "fieldtype": "Currency", "options": cur, "width": 175, "disable_total": 1},
		{"fieldname": "po_count", "label": _("# POs"), "fieldtype": "Int", "width": 70, "disable_total": 1},
	]


def _allowed_by_cc_month(filters, months):
	"""cc -> [12 allowed amounts], dust-normalised per budget doc."""
	budgets = frappe.db.sql(
		"""SELECT b.name, b.cost_center, b.monthly_distribution,
			(SELECT IFNULL(SUM(ba.budget_amount), 0) FROM `tabBudget Account` ba
			 WHERE ba.parent = b.name) AS annual
		FROM `tabBudget` b
		WHERE b.docstatus = 1 AND b.budget_against = 'Cost Center'
		  AND b.fiscal_year = %(fy)s AND b.company = %(company)s""",
		{"fy": filters["fiscal_year"], "company": filters["company"]},
		as_dict=True,
	)
	dist_cache = {}

	def dist_pcts(name):
		if name not in dist_cache:
			rows = frappe.db.sql(
				"""SELECT month, IFNULL(SUM(percentage_allocation), 0) FROM `tabMonthly Distribution Percentage`
				WHERE parent = %s GROUP BY month""", (name,))
			dist_cache[name] = {r[0]: flt(r[1]) for r in rows}
		return dist_cache[name]

	out = {}
	for b in budgets:
		annual = flt(b["annual"])
		if not annual and not b["cost_center"]:
			continue
		shares = []
		if b["monthly_distribution"]:
			pcts = dist_pcts(b["monthly_distribution"])
			total_pct = sum(pcts.values()) or 100.0
			for m in months[:-1]:
				shares.append(round(annual * pcts.get(m["month_name"], 0.0) / total_pct, 2))
		else:
			for _m in months[:-1]:
				shares.append(round(annual / 12.0, 2))
		shares.append(round(annual - sum(shares), 2))  # residual on the final FY month
		arr = out.setdefault(b["cost_center"], [0.0] * 12)
		for i, s in enumerate(shares):
			arr[i] = round(arr[i] + s, 2)
	return out


def _spend_by_cc_month(filters, months):
	"""(cc -> [12 purchase values], cc -> [12 po-name-sets], unattributed_total)."""
	conds = ["po.docstatus = 1", "po.company = %(company)s",
	         "po.transaction_date >= %(fy_start)s", "po.transaction_date <= %(fy_end)s"]
	conds += ru.conf_match_clauses("Purchase Order", "po")

	rows = frappe.db.sql(
		f"""SELECT COALESCE(NULLIF(poi.cost_center, ''), NULLIF(po.cost_center, '')) AS cc,
			DATE_FORMAT(po.transaction_date, '%%Y-%%m') AS mkey,
			po.name AS po, IFNULL(poi.base_net_amount, 0) AS amt
		FROM `tabPurchase Order Item` poi
		JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE {" AND ".join(conds)}""",
		{"company": filters["company"], "fy_start": months[0]["start"], "fy_end": months[-1]["end"]},
		as_dict=True,
	)
	keyidx = {m["key"]: i for i, m in enumerate(months)}
	spend, pos, unattributed = {}, {}, 0.0
	for r in rows:
		i = keyidx.get(r["mkey"])
		if i is None:
			continue
		if not r["cc"]:
			unattributed += flt(r["amt"])
			continue
		spend.setdefault(r["cc"], [0.0] * 12)[i] += flt(r["amt"])
		pos.setdefault(r["cc"], [set() for _ in range(12)])[i].add(r["po"])
	return spend, pos, unattributed


def get_data(filters, months):
	allowed = _allowed_by_cc_month(filters, months)
	spend, pos, unattributed = _spend_by_cc_month(filters, months)

	ccs = sorted(set(allowed) | set(spend))
	if filters.get("cost_center"):
		ccs = [c for c in ccs if c == filters["cost_center"]]

	labels = [m["label"] for m in months]
	lo = labels.index(filters["from_month"]) if filters.get("from_month") else 0
	hi = labels.index(filters["to_month"]) if filters.get("to_month") else 11

	rows = []
	truncated = False
	# compute full recurrence per CC, then slice the display window
	percc = {}
	for cc in ccs:
		a = allowed.get(cc, [0.0] * 12)
		p = spend.get(cc, [0.0] * 12)
		carry = 0.0
		series = []
		for i in range(12):
			avail = round(a[i] + carry, 2)
			extra = round(max(0.0, p[i] - avail), 2)
			balloc = round(max(0.0, avail - p[i]), 2)
			series.append({
				"allowed_amount": a[i], "purchase_value": round(p[i], 2),
				"balance": round(a[i] - p[i], 2),
				"extra_expense": extra, "balance_allocated": balloc,
				"po_count": len(pos.get(cc, [set()] * 12)[i]) if cc in pos else 0,
			})
			carry = round(avail - p[i], 2)
		percc[cc] = series

	for i in range(lo, hi + 1):
		for cc in ccs:
			s = percc[cc][i]
			rows.append(dict(s, month=labels[i], cost_center=cc))
			if len(rows) > ROW_LIMIT:
				truncated = True
				break
		if truncated:
			break
	if truncated:
		rows = rows[:ROW_LIMIT]
	for i, r in enumerate(rows, start=1):
		r["sr"] = i
	return rows, truncated, unattributed


def _summary(rows, truncated, unattributed):
	tot_allowed = sum(flt(r["allowed_amount"]) for r in rows)
	tot_purchase = sum(flt(r["purchase_value"]) for r in rows)
	overrun_cells = sum(1 for r in rows if flt(r["extra_expense"]) > 0)
	out = []
	if truncated:
		out.append({"label": _("Result truncated"),
		            "value": _("first {0} rows — cards reflect shown rows only").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Orange"})
	out += [
		{"label": _("Total Allowed"), "value": tot_allowed, "datatype": "Currency"},
		{"label": _("Total Purchase"), "value": tot_purchase, "datatype": "Currency",
		 "indicator": "Red" if tot_purchase > tot_allowed else "Green"},
		{"label": _("Cells Overrunning"), "value": overrun_cells, "datatype": "Int",
		 "indicator": "Red" if overrun_cells else "Green"},
	]
	if unattributed:
		out.append({"label": _("Unattributed Spend (no CC)"), "value": round(unattributed, 2),
		            "datatype": "Currency", "indicator": "Orange"})
	return out
