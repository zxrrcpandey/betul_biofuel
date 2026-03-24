import frappe
from frappe.utils import getdate, add_days, get_first_day, get_last_day, today, flt

PROCUREMENT_ROLES = ("Purchase Manager", "Grain Purchase Manager", "CEO", "MD", "AVP", "IT Head", "System Manager")


def _check_role():
	user_roles = frappe.get_roles(frappe.session.user)
	if not any(r in user_roles for r in PROCUREMENT_ROLES):
		frappe.throw("Not authorized to view this dashboard", frappe.PermissionError)


def _resolve_dates(date_range, start=None, end=None):
	"""Convert date range label to (start_date, end_date)."""
	t = getdate(today())
	if date_range == "Today":
		return t, t
	elif date_range == "This Week":
		weekday = t.weekday()
		return add_days(t, -weekday), t
	elif date_range == "This Month":
		return get_first_day(t), t
	elif date_range == "This Quarter":
		q_month = ((t.month - 1) // 3) * 3 + 1
		return getdate(f"{t.year}-{q_month:02d}-01"), t
	elif date_range == "This Year":
		return getdate(f"{t.year}-01-01"), t
	elif date_range == "Custom" and start and end:
		return getdate(start), getdate(end)
	return get_first_day(t), t


@frappe.whitelist()
def get_procurement_dashboard(date_range="This Month", company=None, category=None, start=None, end=None):
	"""Return all procurement dashboard data in a single call."""
	_check_role()

	start_date, end_date = _resolve_dates(date_range, start, end)
	if not company:
		company = frappe.defaults.get_global_default("company")

	filters = {"company": company} if company else {}
	cat_filter = f"AND po.bbf_purchase_category = {frappe.db.escape(category)}" if category else ""

	return {
		"po_pipeline": _get_po_pipeline(start_date, end_date, filters, cat_filter),
		"pending_steps": _get_pending_steps(filters, cat_filter),
		"avg_cycle_time": _get_avg_cycle_time(start_date, end_date, filters, cat_filter),
		"mr_pipeline": _get_mr_pipeline(start_date, end_date, filters),
		"top_suppliers": _get_top_suppliers(start_date, end_date, filters, cat_filter),
		"monthly_trend": _get_monthly_trend(filters, cat_filter),
		"summary": _get_summary(start_date, end_date, filters, cat_filter),
		"filters_applied": {
			"date_range": date_range,
			"start": str(start_date),
			"end": str(end_date),
			"company": company,
			"category": category,
		}
	}


def _get_po_pipeline(start_date, end_date, filters, cat_filter):
	"""PO counts grouped by category and approval status."""
	company_filter = f"AND po.company = {frappe.db.escape(filters['company'])}" if filters.get("company") else ""

	rows = frappe.db.sql(f"""
		SELECT
			IFNULL(po.bbf_purchase_category, 'Uncategorized') as category,
			CASE
				WHEN po.bbf_approval_status IN ('', 'Draft') OR po.bbf_approval_status IS NULL THEN 'Draft'
				WHEN po.bbf_approval_status LIKE 'Pending%%' THEN 'Pending'
				WHEN po.bbf_approval_status LIKE 'Awaiting%%' THEN 'Awaiting'
				WHEN po.bbf_approval_status = 'Approved' THEN 'Approved'
				WHEN po.bbf_approval_status = 'Rejected' THEN 'Rejected'
				WHEN po.bbf_approval_status = 'Revised' THEN 'Revised'
				ELSE po.bbf_approval_status
			END as status_group,
			COUNT(*) as cnt
		FROM `tabPurchase Order` po
		WHERE po.docstatus < 2
		AND po.transaction_date BETWEEN %s AND %s
		{company_filter}
		{cat_filter}
		GROUP BY category, status_group
		ORDER BY category, status_group
	""", (start_date, end_date), as_dict=True)

	# Pivot into category → status breakdown
	categories = {}
	for r in rows:
		cat = r.category
		if cat not in categories:
			categories[cat] = {"category": cat, "Draft": 0, "Pending": 0, "Awaiting": 0, "Approved": 0, "Rejected": 0, "Revised": 0}
		categories[cat][r.status_group] = r.cnt

	return list(categories.values())


def _get_pending_steps(filters, cat_filter):
	"""POs currently pending at each approval step."""
	company_filter = f"AND po.company = {frappe.db.escape(filters['company'])}" if filters.get("company") else ""

	rows = frappe.db.sql(f"""
		SELECT
			po.bbf_approval_status as status,
			po.bbf_current_step as current_step,
			po.bbf_total_steps as total_steps,
			COUNT(*) as cnt,
			SUM(po.grand_total) as total_value
		FROM `tabPurchase Order` po
		WHERE po.docstatus = 0
		AND po.bbf_approval_status LIKE 'Pending%%'
		{company_filter}
		{cat_filter}
		GROUP BY po.bbf_approval_status, po.bbf_current_step, po.bbf_total_steps
		ORDER BY cnt DESC
	""", as_dict=True)

	return rows


def _get_avg_cycle_time(start_date, end_date, filters, cat_filter):
	"""Average approval cycle time in hours for approved POs."""
	company_filter = f"AND po.company = {frappe.db.escape(filters['company'])}" if filters.get("company") else ""

	rows = frappe.db.sql(f"""
		SELECT
			IFNULL(po.bbf_purchase_category, 'Uncategorized') as category,
			COUNT(*) as cnt,
			AVG(TIMESTAMPDIFF(HOUR, po.creation, po.bbf_approved_date)) as avg_hours
		FROM `tabPurchase Order` po
		WHERE po.docstatus = 1
		AND po.bbf_approved_date IS NOT NULL
		AND po.bbf_approved_date BETWEEN %s AND %s
		{company_filter}
		{cat_filter}
		GROUP BY category
	""", (start_date, end_date), as_dict=True)

	overall = frappe.db.sql(f"""
		SELECT AVG(TIMESTAMPDIFF(HOUR, po.creation, po.bbf_approved_date)) as avg_hours
		FROM `tabPurchase Order` po
		WHERE po.docstatus = 1
		AND po.bbf_approved_date IS NOT NULL
		AND po.bbf_approved_date BETWEEN %s AND %s
		{company_filter}
		{cat_filter}
	""", (start_date, end_date), as_dict=True)

	return {
		"overall_hours": round(flt(overall[0].avg_hours if overall else 0), 1),
		"by_category": rows
	}


def _get_mr_pipeline(start_date, end_date, filters):
	"""Material Request counts by approval status."""
	company_filter = f"AND mr.company = {frappe.db.escape(filters['company'])}" if filters.get("company") else ""

	rows = frappe.db.sql(f"""
		SELECT
			CASE
				WHEN mr.bbf_mr_status IN ('', 'Draft') OR mr.bbf_mr_status IS NULL THEN 'Draft'
				WHEN mr.bbf_mr_status LIKE 'Pending%%' THEN 'Pending'
				WHEN mr.bbf_mr_status LIKE 'On Hold%%' THEN 'On Hold'
				WHEN mr.bbf_mr_status = 'Approved' THEN 'Approved'
				WHEN mr.bbf_mr_status = 'Rejected' THEN 'Rejected'
				WHEN mr.bbf_mr_status = 'Revised' THEN 'Revised'
				ELSE mr.bbf_mr_status
			END as status_group,
			COUNT(*) as cnt
		FROM `tabMaterial Request` mr
		WHERE mr.docstatus < 2
		AND mr.transaction_date BETWEEN %s AND %s
		{company_filter}
		GROUP BY status_group
	""", (start_date, end_date), as_dict=True)

	result = {"Draft": 0, "Pending": 0, "On Hold": 0, "Approved": 0, "Rejected": 0, "Revised": 0}
	for r in rows:
		result[r.status_group] = r.cnt
	result["total"] = sum(result.values())
	return result


def _get_top_suppliers(start_date, end_date, filters, cat_filter):
	"""Top 10 suppliers by PO value."""
	company_filter = f"AND po.company = {frappe.db.escape(filters['company'])}" if filters.get("company") else ""

	return frappe.db.sql(f"""
		SELECT
			po.supplier,
			po.supplier_name,
			SUM(po.grand_total) as total_value,
			COUNT(*) as po_count
		FROM `tabPurchase Order` po
		WHERE po.docstatus = 1
		AND po.transaction_date BETWEEN %s AND %s
		{company_filter}
		{cat_filter}
		GROUP BY po.supplier, po.supplier_name
		ORDER BY total_value DESC
		LIMIT 10
	""", (start_date, end_date), as_dict=True)


def _get_monthly_trend(filters, cat_filter):
	"""PO value by month for last 6 months."""
	company_filter = f"AND po.company = {frappe.db.escape(filters['company'])}" if filters.get("company") else ""

	return frappe.db.sql(f"""
		SELECT
			DATE_FORMAT(po.transaction_date, '%Y-%m') as month,
			SUM(po.grand_total) as value,
			COUNT(*) as cnt
		FROM `tabPurchase Order` po
		WHERE po.docstatus = 1
		AND po.transaction_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
		{company_filter}
		{cat_filter}
		GROUP BY month
		ORDER BY month
	""", as_dict=True)


def _get_summary(start_date, end_date, filters, cat_filter):
	"""Top-line summary KPIs."""
	company_filter = f"AND po.company = {frappe.db.escape(filters['company'])}" if filters.get("company") else ""

	totals = frappe.db.sql(f"""
		SELECT
			COUNT(*) as total_pos,
			SUM(CASE WHEN po.docstatus = 1 THEN po.grand_total ELSE 0 END) as approved_value,
			SUM(CASE WHEN po.docstatus = 0 AND po.bbf_approval_status LIKE 'Pending%%' THEN 1 ELSE 0 END) as pending_count,
			SUM(CASE WHEN po.docstatus = 0 AND po.bbf_approval_status = 'Rejected' THEN 1 ELSE 0 END) as rejected_count,
			SUM(CASE WHEN po.docstatus = 1 THEN 1 ELSE 0 END) as approved_count
		FROM `tabPurchase Order` po
		WHERE po.docstatus < 2
		AND po.transaction_date BETWEEN %s AND %s
		{company_filter}
		{cat_filter}
	""", (start_date, end_date), as_dict=True)

	t = totals[0] if totals else {}
	return {
		"total_pos": t.get("total_pos") or 0,
		"approved_value": flt(t.get("approved_value") or 0),
		"pending_count": t.get("pending_count") or 0,
		"rejected_count": t.get("rejected_count") or 0,
		"approved_count": t.get("approved_count") or 0,
	}
