import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	summary = get_summary(data)
	return columns, data, None, chart, summary


def get_columns():
	return [
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 200},
		{"fieldname": "annual_budget", "label": _("Annual Budget"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "committed", "label": _("Committed (POs)"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "actual_spent", "label": _("Actual Spent"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "available", "label": _("Available"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "utilization_pct", "label": _("Utilization %"), "fieldtype": "Percent", "width": 120},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
		{"fieldname": "monthly_distribution", "label": _("Monthly Dist."), "fieldtype": "Data", "width": 130},
	]


def get_data(filters):
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

	# Get committed (unbilled portion of POs in FY date range)
	committed_data = {}
	rows = frappe.db.sql("""
		SELECT cost_center, COALESCE(SUM(grand_total * (1 - IFNULL(per_billed, 0) / 100)), 0) as total
		FROM `tabPurchase Order`
		WHERE transaction_date BETWEEN %s AND %s
			AND company = %s AND docstatus = 1
			AND status NOT IN ('Closed', 'Cancelled')
		GROUP BY cost_center
	""", (fy_start, fy_end, company), as_dict=True)
	for r in rows:
		committed_data[r.cost_center] = flt(r.total)

	# Get actual (GL entries in FY date range)
	actual_data = {}
	rows = frappe.db.sql("""
		SELECT cost_center, COALESCE(SUM(debit) - SUM(credit), 0) as total
		FROM `tabGL Entry`
		WHERE posting_date BETWEEN %s AND %s
			AND company = %s AND is_cancelled = 0
			AND voucher_type IN ('Purchase Invoice', 'Journal Entry')
		GROUP BY cost_center
	""", (fy_start, fy_end, company), as_dict=True)
	for r in rows:
		actual_data[r.cost_center] = flt(r.total)

	data = []
	for b in budgets:
		cc = b.cost_center
		annual = flt(b.annual_budget)
		committed = committed_data.get(cc, 0)
		actual = actual_data.get(cc, 0)
		available = annual - committed - actual
		util = round((committed + actual) / annual * 100, 1) if annual else 0

		status = "Exceeded" if available < 0 else ("Warning" if util >= 80 else "OK")

		data.append({
			"cost_center": cc,
			"annual_budget": annual,
			"committed": committed,
			"actual_spent": actual,
			"available": available,
			"utilization_pct": util,
			"status": status,
			"monthly_distribution": b.monthly_distribution or "None",
		})

	data.sort(key=lambda x: x["utilization_pct"], reverse=True)
	return data


def get_chart(data):
	if not data:
		return None

	labels = [d["cost_center"].split(" - ")[0] for d in data[:10]]

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Budget"), "values": [d["annual_budget"] for d in data[:10]]},
				{"name": _("Committed"), "values": [d["committed"] for d in data[:10]]},
				{"name": _("Spent"), "values": [d["actual_spent"] for d in data[:10]]},
			]
		},
		"type": "bar",
		"colors": ["#3b82f6", "#f59e0b", "#10b981"],
		"barOptions": {"stacked": 0},
	}


def get_summary(data):
	if not data:
		return []

	total_budget = sum(d["annual_budget"] for d in data)
	total_committed = sum(d["committed"] for d in data)
	total_actual = sum(d["actual_spent"] for d in data)
	total_available = total_budget - total_committed - total_actual
	overall_util = round((total_committed + total_actual) / total_budget * 100, 1) if total_budget else 0

	exceeded_count = sum(1 for d in data if d["status"] == "Exceeded")

	return [
		{"value": total_budget, "label": _("Total Budget"), "datatype": "Currency", "indicator": "blue"},
		{"value": total_committed, "label": _("Total Committed"), "datatype": "Currency", "indicator": "orange"},
		{"value": total_actual, "label": _("Total Spent"), "datatype": "Currency", "indicator": "green"},
		{"value": total_available, "label": _("Total Available"), "datatype": "Currency",
		 "indicator": "red" if total_available < 0 else "green"},
		{"value": overall_util, "label": _("Overall Utilization"), "datatype": "Percent",
		 "indicator": "red" if overall_util > 100 else ("orange" if overall_util >= 80 else "green")},
		{"value": exceeded_count, "label": _("CCs Exceeded"), "datatype": "Int",
		 "indicator": "red" if exceeded_count > 0 else "green"},
	]
