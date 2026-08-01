import frappe
from frappe import _
from frappe.utils import flt

# ACCESS NOTE: this Query Report's `roles` list (ts_budget_dashboard.json) is
# the ONLY gate on the aggregates below (PO, payment and GL sums per cost
# centre) — query_report.run never checks source-doctype DocPerms. Keep the
# audience a subset of the confidential-PO allow-list; the
# confidential_sql_clause() below fails closed if it ever isn't.

# Fiscal-year month order (April-March FY). Index 0 = first FY month.
FY_MONTHS = ["April", "May", "June", "July", "August", "September",
	"October", "November", "December", "January", "February", "March"]

VIEW_PIPELINE = "Full Pipeline"
VIEW_PO = "Budget vs PO"
VIEW_CONSUMED = "Budget vs Consumed"


def execute(filters=None):
	view = _get_view(filters)
	columns = get_columns(filters, view)
	data = get_data(filters, view)
	chart = get_chart(data, view)
	summary = get_summary(data, view)
	return columns, data, None, chart, summary


def _get_view(filters):
	# str() rescue: a hand-crafted API call can pass a non-string (int/list/…)
	# and .strip() would 500 — fall back to the default view instead.
	view = str(filters.get("view") or "").strip() if filters else ""
	return view if view in (VIEW_PIPELINE, VIEW_PO, VIEW_CONSUMED) else VIEW_PIPELINE


def get_columns(filters=None, view=VIEW_PIPELINE):
	month = str(filters.get("month") or "").strip() if filters else ""
	budget_label = _("Budget ({0})").format(_(month)) if month in FY_MONTHS else _("Annual Budget")
	# GST basis deliberately NOT repeated in column labels — the "PO Amounts"
	# filter states it, and the suffix made headers truncate at crore widths.

	cc_col = {"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 190}
	budget_col = {"fieldname": "annual_budget", "label": budget_label, "fieldtype": "Currency", "width": 155}
	ordered_col = {"fieldname": "ordered", "label": _("Ordered"), "fieldtype": "Currency", "width": 155}
	billed_col = {"fieldname": "billed", "label": _("Billed"), "fieldtype": "Currency", "width": 155}
	paid_col = {"fieldname": "paid", "label": _("Paid (info)"), "fieldtype": "Currency", "width": 145}
	consumed_col = {"fieldname": "consumed", "label": _("Consumed"), "fieldtype": "Currency", "width": 155}
	available_col = {"fieldname": "available", "label": _("Available"), "fieldtype": "Currency", "width": 155}
	util_col = {"fieldname": "utilization_pct", "label": _("Util %"), "fieldtype": "Percent", "width": 90}
	status_col = {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 95}

	if view == VIEW_PO:
		total_col = {"fieldname": "total_po_spend", "label": _("Total PO Spend"), "fieldtype": "Currency", "width": 160}
		return [cc_col, budget_col, ordered_col, billed_col, total_col, available_col, util_col, status_col]
	if view == VIEW_CONSUMED:
		return [cc_col, budget_col, consumed_col, available_col, util_col, status_col]
	# Full Pipeline: location of every rupee — deliberately NO available/status,
	# so the visibility view can never be mistaken for a budget judgement.
	return [cc_col, budget_col, ordered_col, billed_col, paid_col, consumed_col]


def get_data(filters, view=VIEW_PIPELINE):
	fiscal_year = filters.get("fiscal_year") if filters else None
	company = filters.get("company") if filters else None

	if not fiscal_year:
		fiscal_year = frappe.defaults.get_global_default("fiscal_year")
	if not company:
		company = frappe.defaults.get_global_default("company")

	# Get budgets
	budgets = frappe.db.sql("""
		SELECT
			b.cost_center,
			b.monthly_distribution,
			SUM(ba.budget_amount) as annual_budget
		FROM `tabBudget` b
		INNER JOIN `tabBudget Account` ba ON ba.parent = b.name
		WHERE b.fiscal_year = %s AND b.company = %s AND b.docstatus = 1
		GROUP BY b.cost_center, b.monthly_distribution
	""", (fiscal_year, company), as_dict=True)

	if not budgets:
		return []

	# Get FY date range for PO/GL queries (PO has no fiscal_year field)
	fy_doc = frappe.get_doc("Fiscal Year", fiscal_year)
	fy_start = fy_doc.year_start_date
	fy_end = fy_doc.year_end_date

	# Month filter: blank = full FY; a month narrows every query to that month
	# and shows the budget's monthly slice (annual/12 — equal distribution,
	# same convention as the budget-override gate).
	month = str(filters.get("month") or "").strip() if filters else ""
	month_selected = month in FY_MONTHS
	period_start, period_end = fy_start, fy_end
	if month_selected:
		import calendar
		from datetime import date

		month_no = ((FY_MONTHS.index(month) + fy_start.month - 1) % 12) + 1
		year = fy_start.year if month_no >= fy_start.month else fy_end.year
		period_start = date(year, month_no, 1)
		period_end = date(year, month_no, calendar.monthrange(year, month_no)[1])

	# ── Station 1+2: Ordered (unbilled portion) and Billed (billed portion) of
	# the period's POs, per header cost centre.
	# tax_basis: 'Without GST' (default) uses po.total; 'With GST' grand_total.
	# NOTE the CEO override gate (_committed_for_period) enforces on grand_total
	# — the With-GST figures are the enforcement-matching ones.
	with_gst = bool(filters) and (filters.get("tax_basis") or "Without GST") == "With GST"
	amount_col = "grand_total" if with_gst else "total"  # two literals only — no user input
	# L297 fail-closed hardening: '' for allow-listed users (all current report
	# roles), a hiding clause for anyone else if the roles list is ever widened.
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause

	conf_po = confidential_sql_clause("po", doctype="Purchase Order")
	ordered_data, billed_data = {}, {}
	rows = frappe.db.sql("""
		SELECT po.cost_center,
			COALESCE(SUM(po.{amount_col} * (1 - IFNULL(po.per_billed, 0) / 100)), 0) as ordered_amt,
			COALESCE(SUM(po.{amount_col} * IFNULL(po.per_billed, 0) / 100), 0) as billed_amt
		FROM `tabPurchase Order` po
		WHERE po.transaction_date BETWEEN %s AND %s
			AND po.company = %s AND po.docstatus = 1
			AND po.status NOT IN ('Closed', 'Cancelled')
			{conf_po}
		GROUP BY po.cost_center
	""".format(amount_col=amount_col, conf_po=conf_po), (period_start, period_end, company), as_dict=True)
	for r in rows:
		ordered_data[r.cost_center] = flt(r.ordered_amt)
		billed_data[r.cost_center] = flt(r.billed_amt)

	# ── Station 3: Paid (cash information ONLY — never part of budget math,
	# the spend was already counted at Billed). Payment allocations against
	# Purchase Invoices, attributed to the source PO's header cost centre.
	# Always gross (cash is cash — the GST toggle does not apply to payments).
	# Known approximation: a PI spanning POs of SEVERAL cost centres counts its
	# allocation once per CC; single-CC invoices are the norm on this site.
	paid_data = {}
	if view == VIEW_PIPELINE:
		rows = frappe.db.sql("""
			SELECT po_cc.cost_center, COALESCE(SUM(per.allocated_amount), 0) as total
			FROM `tabPayment Entry Reference` per
			JOIN `tabPayment Entry` pe ON pe.name = per.parent
			JOIN (
				SELECT DISTINCT pii.parent AS pi_name, po.cost_center
				FROM `tabPurchase Invoice Item` pii
				JOIN `tabPurchase Order` po ON po.name = pii.purchase_order
				WHERE po.company = %s
					{conf_po}
			) po_cc ON po_cc.pi_name = per.reference_name
			WHERE per.reference_doctype = 'Purchase Invoice'
				AND pe.docstatus = 1
				AND pe.posting_date BETWEEN %s AND %s
				AND pe.company = %s
			GROUP BY po_cc.cost_center
		""".format(conf_po=conf_po), (company, period_start, period_end, company), as_dict=True)
		for r in rows:
			paid_data[r.cost_center] = flt(r.total)

	# ── Station 4: Consumed (Actual) — expense-root GL rows only; Stock Entry
	# included because Material Issue is this site's primary consumption path
	# (CC force-stamped by ts_se_header_dimensions). Purchase Receipt excluded
	# (SRBNB re-booked by PI). MIRROR of the Budget Control Matrix query in
	# ts_budget.py — keep in sync.
	consumed_data = {}
	rows = frappe.db.sql("""
		SELECT gle.cost_center, COALESCE(SUM(gle.debit) - SUM(gle.credit), 0) as total
		FROM `tabGL Entry` gle
		INNER JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE gle.posting_date BETWEEN %s AND %s
			AND gle.company = %s AND gle.is_cancelled = 0
			AND gle.voucher_type IN ('Purchase Invoice', 'Journal Entry', 'Stock Entry')
			AND acc.root_type = 'Expense'
		GROUP BY gle.cost_center
	""", (period_start, period_end, company), as_dict=True)
	for r in rows:
		consumed_data[r.cost_center] = flt(r.total)

	data = []
	for b in budgets:
		cc = b.cost_center
		budget = flt(b.annual_budget) / 12.0 if month_selected else flt(b.annual_budget)
		ordered = ordered_data.get(cc, 0)
		billed = billed_data.get(cc, 0)
		paid = paid_data.get(cc, 0)
		consumed = consumed_data.get(cc, 0)

		row = {
			"cost_center": cc,
			"annual_budget": budget,
			"ordered": ordered,
			"billed": billed,
			"paid": paid,
			"consumed": consumed,
		}

		# Per-view judgement. Consumed is NEVER added on top of PO spend —
		# consumed material's money was already counted at Ordered/Billed
		# (no double counting; the two comparison views answer two different
		# questions instead).
		if view == VIEW_PO:
			total_po_spend = ordered + billed
			available = budget - total_po_spend
			util = round(total_po_spend / budget * 100, 1) if budget else 0
			row.update({"total_po_spend": total_po_spend, "available": available, "utilization_pct": util,
				"status": "Exceeded" if available < 0 else ("Warning" if util >= 80 else "OK")})
		elif view == VIEW_CONSUMED:
			available = budget - consumed
			util = round(consumed / budget * 100, 1) if budget else 0
			row.update({"available": available, "utilization_pct": util,
				"status": "Exceeded" if available < 0 else ("Warning" if util >= 80 else "OK")})

		data.append(row)

	if view == VIEW_PIPELINE:
		data.sort(key=lambda x: x["ordered"] + x["billed"] + x["consumed"], reverse=True)
	else:
		data.sort(key=lambda x: x["utilization_pct"], reverse=True)
	return data


def get_chart(data, view=VIEW_PIPELINE):
	if not data:
		return None

	# A Budget row can carry NO cost_center (seen live: BUDGET-2026-00026) —
	# guard the label or the whole chart 500s whenever that row reaches top-10.
	labels = [(d["cost_center"] or _("(No Cost Center)")).split(" - ")[0] for d in data[:10]]

	if view == VIEW_PO:
		datasets = [
			{"name": _("Budget"), "values": [d["annual_budget"] for d in data[:10]]},
			{"name": _("Total PO Spend"), "values": [d["total_po_spend"] for d in data[:10]]},
		]
		colors = ["#3b82f6", "#d97706"]
	elif view == VIEW_CONSUMED:
		datasets = [
			{"name": _("Budget"), "values": [d["annual_budget"] for d in data[:10]]},
			{"name": _("Consumed"), "values": [d["consumed"] for d in data[:10]]},
		]
		colors = ["#3b82f6", "#10b981"]
	else:
		datasets = [
			{"name": _("Budget"), "values": [d["annual_budget"] for d in data[:10]]},
			{"name": _("Ordered"), "values": [d["ordered"] for d in data[:10]]},
			{"name": _("Billed"), "values": [d["billed"] for d in data[:10]]},
			{"name": _("Consumed"), "values": [d["consumed"] for d in data[:10]]},
		]
		colors = ["#3b82f6", "#d97706", "#7c3aed", "#10b981"]

	return {
		"data": {"labels": labels, "datasets": datasets},
		"type": "bar",
		"colors": colors,
		"barOptions": {"stacked": 0},
	}


def get_summary(data, view=VIEW_PIPELINE):
	if not data:
		return []

	total_budget = sum(d["annual_budget"] for d in data)
	total_ordered = sum(d["ordered"] for d in data)
	total_billed = sum(d["billed"] for d in data)
	total_paid = sum(d["paid"] for d in data)
	total_consumed = sum(d["consumed"] for d in data)

	if view == VIEW_PO:
		total_spend = total_ordered + total_billed
		total_available = total_budget - total_spend
		util = round(total_spend / total_budget * 100, 1) if total_budget else 0
		exceeded = sum(1 for d in data if d.get("status") == "Exceeded")
		return [
			{"value": total_budget, "label": _("Total Budget"), "datatype": "Currency", "indicator": "blue"},
			{"value": total_ordered, "label": _("Ordered"), "datatype": "Currency", "indicator": "orange"},
			{"value": total_billed, "label": _("Billed"), "datatype": "Currency", "indicator": "purple"},
			{"value": total_spend, "label": _("Total PO Spend"), "datatype": "Currency", "indicator": "red"},
			{"value": total_available, "label": _("Available"), "datatype": "Currency",
			 "indicator": "red" if total_available < 0 else "green"},
			{"value": util, "label": _("Utilization"), "datatype": "Percent",
			 "indicator": "red" if util > 100 else ("orange" if util >= 80 else "green")},
			{"value": exceeded, "label": _("CCs Exceeded"), "datatype": "Int",
			 "indicator": "red" if exceeded > 0 else "green"},
		]
	if view == VIEW_CONSUMED:
		total_available = total_budget - total_consumed
		util = round(total_consumed / total_budget * 100, 1) if total_budget else 0
		exceeded = sum(1 for d in data if d.get("status") == "Exceeded")
		return [
			{"value": total_budget, "label": _("Total Budget"), "datatype": "Currency", "indicator": "blue"},
			{"value": total_consumed, "label": _("Consumed (Actual)"), "datatype": "Currency", "indicator": "green"},
			{"value": total_available, "label": _("Available"), "datatype": "Currency",
			 "indicator": "red" if total_available < 0 else "green"},
			{"value": util, "label": _("Utilization"), "datatype": "Percent",
			 "indicator": "red" if util > 100 else ("orange" if util >= 80 else "green")},
			{"value": exceeded, "label": _("CCs Exceeded"), "datatype": "Int",
			 "indicator": "red" if exceeded > 0 else "green"},
		]
	# Full Pipeline — location totals only, no judgement figures.
	return [
		{"value": total_budget, "label": _("Total Budget"), "datatype": "Currency", "indicator": "blue"},
		{"value": total_ordered, "label": _("Ordered"), "datatype": "Currency", "indicator": "orange"},
		{"value": total_billed, "label": _("Billed"), "datatype": "Currency", "indicator": "purple"},
		{"value": total_paid, "label": _("Paid (info)"), "datatype": "Currency", "indicator": "grey"},
		{"value": total_consumed, "label": _("Consumed"), "datatype": "Currency", "indicator": "green"},
	]
