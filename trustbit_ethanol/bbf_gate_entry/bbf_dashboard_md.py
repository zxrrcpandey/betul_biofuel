import frappe
from frappe.utils import getdate, add_days, add_months, get_first_day, today, now_datetime, time_diff_in_seconds, flt

MD_ROLES = ("MD", "IT Head", "System Manager")


def _check_role():
	user_roles = frappe.get_roles(frappe.session.user)
	if not any(r in user_roles for r in MD_ROLES):
		frappe.throw("Not authorized to view this dashboard", frappe.PermissionError)


def _resolve_dates(date_range, start=None, end=None):
	t = getdate(today())
	if date_range == "Today":
		return t, t
	elif date_range == "This Week":
		return add_days(t, -t.weekday()), t
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


def _get_fiscal_year():
	fy = frappe.defaults.get_global_default("fiscal_year")
	if not fy:
		return None, None, None
	fy_doc = frappe.db.get_value("Fiscal Year", fy, ["year_start_date", "year_end_date"], as_dict=True)
	if not fy_doc:
		return fy, None, None
	return fy, getdate(fy_doc.year_start_date), getdate(fy_doc.year_end_date)


@frappe.whitelist()
def get_md_dashboard(date_range="This Month", start=None, end=None):
	"""Return all MD dashboard data — comprehensive business overview."""
	_check_role()

	start_date, end_date = _resolve_dates(date_range, start, end)
	today_date = getdate(today())
	fiscal_year, fy_start, fy_end = _get_fiscal_year()

	return {
		"company_overview": _get_company_overview(today_date, start_date, end_date, fiscal_year, fy_start, fy_end),
		"action_items": _get_action_items(),
		"gate_operations": _get_gate_operations(today_date),
		"procurement": _get_procurement(start_date, end_date),
		"budget": _get_budget(fiscal_year, fy_start, fy_end),
		"quality": _get_quality(start_date, end_date),
		"monthly_trend": _get_monthly_trend(),
		"top_suppliers": _get_top_suppliers(start_date, end_date),
		"non_branded_items": _get_non_branded_items(),
		"filters_applied": {
			"date_range": date_range,
			"start": str(start_date),
			"end": str(end_date),
		},
		"last_updated": str(now_datetime()),
	}


# ═══════════════════════════════════════════════════════════════════════
#  COMPANY OVERVIEW — Both companies side by side
# ═══════════════════════════════════════════════════════════════════════

def _get_company_overview(today_date, start_date, end_date, fiscal_year, fy_start, fy_end):
	companies = frappe.db.get_all("Company", filters={"is_group": 0}, pluck="name")

	overview = []
	for company in companies:
		co = frappe.db.escape(company)

		# PO value this period
		po_val = frappe.db.sql(f"""
			SELECT COALESCE(SUM(grand_total), 0)
			FROM `tabPurchase Order`
			WHERE docstatus = 1 AND company = {co}
			AND transaction_date BETWEEN %s AND %s
		""", (start_date, end_date))[0][0] or 0

		# PI value (purchase invoices = actual spend)
		pi_val = frappe.db.sql(f"""
			SELECT COALESCE(SUM(grand_total), 0)
			FROM `tabPurchase Invoice`
			WHERE docstatus = 1 AND company = {co}
			AND posting_date BETWEEN %s AND %s
		""", (start_date, end_date))[0][0] or 0

		# PR count (purchase receipts = GRNs)
		pr_count = frappe.db.sql(f"""
			SELECT COUNT(*)
			FROM `tabPurchase Receipt`
			WHERE docstatus = 1 AND company = {co}
			AND posting_date BETWEEN %s AND %s
		""", (start_date, end_date))[0][0] or 0

		# Budget utilization
		budget_pct = 0
		total_budget = 0
		total_used = 0
		if fiscal_year and fy_start and fy_end:
			budget_data = frappe.db.sql("""
				SELECT COALESCE(SUM(ba.budget_amount), 0) as total_budget
				FROM `tabBudget Account` ba
				JOIN `tabBudget` b ON ba.parent = b.name
				WHERE b.fiscal_year = %s AND b.company = %s AND b.docstatus = 1
			""", (fiscal_year, company))
			total_budget = flt(budget_data[0][0]) if budget_data else 0

			if total_budget > 0:
				committed = frappe.db.sql(f"""
					SELECT COALESCE(SUM(grand_total * (1 - IFNULL(per_billed, 0) / 100)), 0)
					FROM `tabPurchase Order`
					WHERE company = {co} AND transaction_date BETWEEN %s AND %s
					AND docstatus = 1 AND status NOT IN ('Closed', 'Cancelled')
				""", (fy_start, fy_end))[0][0] or 0

				actual = frappe.db.sql(f"""
					SELECT COALESCE(SUM(debit) - SUM(credit), 0)
					FROM `tabGL Entry`
					WHERE company = {co} AND posting_date BETWEEN %s AND %s
					AND is_cancelled = 0 AND voucher_type IN ('Purchase Invoice', 'Journal Entry')
				""", (fy_start, fy_end))[0][0] or 0

				total_used = flt(committed) + flt(actual)
				budget_pct = round((total_used / total_budget) * 100, 1)

		# Pending POs
		pending_pos = frappe.db.sql(f"""
			SELECT COUNT(*) FROM `tabPurchase Order`
			WHERE docstatus = 0 AND company = {co}
			AND bbf_approval_status LIKE 'Pending%%'
		""")[0][0] or 0

		overview.append({
			"company": company,
			"short_name": company.replace("Betul Bio Fuel", "BBF").replace("Betul Biofuel Private Limited", "BBPL").strip(),
			"po_value": flt(po_val),
			"pi_value": flt(pi_val),
			"pr_count": pr_count,
			"pending_pos": pending_pos,
			"budget_total": total_budget,
			"budget_used": total_used,
			"budget_pct": budget_pct,
		})

	# Global KPIs
	total_pending = frappe.db.sql("""
		SELECT COUNT(*) FROM `tabPurchase Order`
		WHERE docstatus = 0
		AND (bbf_approval_status LIKE 'Pending MD%%'
			OR bbf_approval_status LIKE 'Awaiting%%Send%%MD%%')
	""")[0][0] or 0

	vehicles_inside = frappe.db.sql("""
		SELECT COUNT(*) FROM `tabBBF Token`
		WHERE entry_type = 'Material' AND status NOT IN ('Exited', 'Token Generated')
	""")[0][0] or 0

	today_inward = frappe.db.sql("""
		SELECT COUNT(*) FROM `tabBBF Token`
		WHERE entry_date = %s AND entry_type = 'Material'
		AND (stock_direction IS NULL OR stock_direction = 'Stock IN' OR stock_direction = '')
	""", today_date)[0][0] or 0

	today_weight = frappe.db.sql("""
		SELECT COALESCE(SUM(wl.net_weight), 0) / 1000
		FROM `tabBBF Weighbridge Log` wl
		JOIN `tabBBF Token` t ON wl.token_number = t.name
		WHERE t.entry_date = %s AND wl.net_weight > 0
		AND (wl.stock_direction IS NULL OR wl.stock_direction = 'Stock IN' OR wl.stock_direction = '')
	""", today_date)[0][0] or 0

	avg_ta = frappe.db.sql("""
		SELECT AVG(TIMESTAMPDIFF(MINUTE, g1_entry_time, g1_exit_time))
		FROM `tabBBF Token`
		WHERE entry_date = %s AND status = 'Exited' AND entry_type = 'Material'
		AND g1_entry_time IS NOT NULL AND g1_exit_time IS NOT NULL
	""", today_date)[0][0] or 0

	# Stuck vehicles
	settings = frappe.get_single("BBF Settings")
	threshold = settings.sla_threshold_minutes or 30
	now = now_datetime()
	stuck = frappe.db.sql("""
		SELECT COALESCE(
			CASE status
				WHEN 'PO Linked' THEN g2_link_time
				WHEN 'Gross Weighed' THEN wb_gross_time
				WHEN 'Quality Done' THEN quality_time
				WHEN 'Graded' THEN grading_time
				WHEN 'Unloading' THEN unload_start_time
				WHEN 'Tare Weighed' THEN wb_tare_time
			END,
			g1_entry_time
		) as last_ts
		FROM `tabBBF Token`
		WHERE entry_type = 'Material'
		AND status NOT IN ('Exited', 'Token Generated', 'GRN Created', 'Dispatch Ready')
	""", as_dict=True)
	stuck_count = sum(1 for t in stuck if t.last_ts and time_diff_in_seconds(now, t.last_ts) / 60 > threshold)

	return {
		"companies": overview,
		"md_pending": total_pending,
		"vehicles_inside": vehicles_inside,
		"today_inward": today_inward,
		"today_weight_mt": round(flt(today_weight), 1),
		"avg_turnaround_min": round(flt(avg_ta), 0),
		"stuck_vehicles": stuck_count,
	}


# ═══════════════════════════════════════════════════════════════════════
#  ACTION ITEMS — POs pending MD approval
# ═══════════════════════════════════════════════════════════════════════

def _get_action_items():
	now = now_datetime()

	pos = frappe.db.sql("""
		SELECT name, supplier_name, grand_total, company,
			bbf_purchase_category as category,
			bbf_approval_status as status, modified
		FROM `tabPurchase Order`
		WHERE docstatus = 0
		AND (bbf_approval_status LIKE 'Pending MD%%'
			OR bbf_approval_status LIKE 'Awaiting%%Send%%MD%%')
		ORDER BY modified ASC
		LIMIT 20
	""", as_dict=True)

	for po in pos:
		seconds = time_diff_in_seconds(now, po.modified)
		po["waiting_hours"] = round(seconds / 3600, 1) if seconds > 0 else 0

	return {"pos": pos, "count": len(pos)}


# ═══════════════════════════════════════════════════════════════════════
#  GATE OPERATIONS — Live plant status
# ═══════════════════════════════════════════════════════════════════════

def _get_gate_operations(today_date):
	# Today's flow
	flow = frappe.db.sql("""
		SELECT
			COUNT(*) as total_entries,
			SUM(CASE WHEN status = 'Exited' THEN 1 ELSE 0 END) as exits,
			SUM(CASE WHEN entry_type = 'Material' AND (stock_direction IS NULL OR stock_direction = 'Stock IN' OR stock_direction = '') THEN 1 ELSE 0 END) as stock_in,
			SUM(CASE WHEN entry_type = 'Material' AND stock_direction = 'Stock OUT' THEN 1 ELSE 0 END) as stock_out,
			SUM(CASE WHEN entry_type = 'Gate Pass' THEN 1 ELSE 0 END) as visitors
		FROM `tabBBF Token` WHERE entry_date = %s
	""", today_date, as_dict=True)
	f = flow[0] if flow else {}

	# Stage breakdown
	stages = frappe.db.sql("""
		SELECT status as stage, COUNT(*) as count
		FROM `tabBBF Token`
		WHERE entry_type = 'Material' AND status NOT IN ('Exited', 'Token Generated')
		GROUP BY status
		ORDER BY FIELD(status,
			'PO Linked', 'Gross Weighed', 'Quality Done', 'Graded',
			'Unloading', 'Tare Weighed', 'GRN Created',
			'SI Linked', 'Tare Recorded', 'Loading Done', 'Gross Recorded', 'Dispatch Ready')
	""", as_dict=True)

	# Weight processed today
	weight = frappe.db.sql("""
		SELECT
			ROUND(COALESCE(SUM(CASE WHEN wl.stock_direction IS NULL OR wl.stock_direction = 'Stock IN' OR wl.stock_direction = '' THEN wl.net_weight ELSE 0 END), 0) / 1000, 2) as in_mt,
			ROUND(COALESCE(SUM(CASE WHEN wl.stock_direction = 'Stock OUT' THEN wl.net_weight ELSE 0 END), 0) / 1000, 2) as out_mt
		FROM `tabBBF Weighbridge Log` wl
		JOIN `tabBBF Token` t ON wl.token_number = t.name
		WHERE t.entry_date = %s AND wl.net_weight > 0
	""", today_date, as_dict=True)
	w = weight[0] if weight else {}

	return {
		"entries": f.get("total_entries") or 0,
		"exits": f.get("exits") or 0,
		"stock_in": f.get("stock_in") or 0,
		"stock_out": f.get("stock_out") or 0,
		"visitors": f.get("visitors") or 0,
		"stages": stages,
		"weight_in_mt": flt(w.get("in_mt")),
		"weight_out_mt": flt(w.get("out_mt")),
	}


# ═══════════════════════════════════════════════════════════════════════
#  PROCUREMENT — PO pipeline across both companies
# ═══════════════════════════════════════════════════════════════════════

def _get_procurement(start_date, end_date):
	# By category + status
	pipeline = frappe.db.sql("""
		SELECT
			IFNULL(bbf_purchase_category, 'Uncategorized') as category,
			company,
			CASE
				WHEN bbf_approval_status IN ('', 'Draft') OR bbf_approval_status IS NULL THEN 'Draft'
				WHEN bbf_approval_status LIKE 'Pending%%' THEN 'Pending'
				WHEN bbf_approval_status LIKE 'Awaiting%%' THEN 'Awaiting'
				WHEN bbf_approval_status = 'Approved' THEN 'Approved'
				WHEN bbf_approval_status = 'Rejected' THEN 'Rejected'
				ELSE bbf_approval_status
			END as status_group,
			COUNT(*) as cnt,
			SUM(grand_total) as value
		FROM `tabPurchase Order`
		WHERE docstatus < 2
		AND transaction_date BETWEEN %s AND %s
		GROUP BY category, company, status_group
	""", (start_date, end_date), as_dict=True)

	categories = {}
	for r in pipeline:
		cat = r.category
		if cat not in categories:
			categories[cat] = {"category": cat, "Draft": 0, "Pending": 0, "Awaiting": 0, "Approved": 0, "Rejected": 0, "total_value": 0}
		categories[cat][r.status_group] = categories[cat].get(r.status_group, 0) + r.cnt
		if r.status_group == "Approved":
			categories[cat]["total_value"] += flt(r.value)

	# Summary totals
	total_pos = frappe.db.sql("""
		SELECT COUNT(*), COALESCE(SUM(grand_total), 0)
		FROM `tabPurchase Order`
		WHERE docstatus = 1 AND transaction_date BETWEEN %s AND %s
	""", (start_date, end_date))

	return {
		"pipeline": list(categories.values()),
		"total_approved_count": total_pos[0][0] or 0,
		"total_approved_value": flt(total_pos[0][1]),
	}


# ═══════════════════════════════════════════════════════════════════════
#  BUDGET — Both companies
# ═══════════════════════════════════════════════════════════════════════

def _get_budget(fiscal_year, fy_start, fy_end):
	if not fiscal_year or not fy_start or not fy_end:
		return {"companies": [], "totals": {}}

	companies = frappe.db.get_all("Company", filters={"is_group": 0}, pluck="name")
	result = []
	grand_budget = 0
	grand_used = 0

	for company in companies:
		co = frappe.db.escape(company)
		budget_amt = frappe.db.sql("""
			SELECT COALESCE(SUM(ba.budget_amount), 0)
			FROM `tabBudget Account` ba
			JOIN `tabBudget` b ON ba.parent = b.name
			WHERE b.fiscal_year = %s AND b.company = %s AND b.docstatus = 1
		""", (fiscal_year, company))[0][0] or 0

		if not budget_amt:
			continue

		committed = frappe.db.sql(f"""
			SELECT COALESCE(SUM(grand_total * (1 - IFNULL(per_billed, 0) / 100)), 0)
			FROM `tabPurchase Order`
			WHERE company = {co} AND transaction_date BETWEEN %s AND %s
			AND docstatus = 1 AND status NOT IN ('Closed', 'Cancelled')
		""", (fy_start, fy_end))[0][0] or 0

		actual = frappe.db.sql(f"""
			SELECT COALESCE(SUM(debit) - SUM(credit), 0)
			FROM `tabGL Entry`
			WHERE company = {co} AND posting_date BETWEEN %s AND %s
			AND is_cancelled = 0 AND voucher_type IN ('Purchase Invoice', 'Journal Entry')
		""", (fy_start, fy_end))[0][0] or 0

		used = flt(committed) + flt(actual)
		pct = round((used / flt(budget_amt)) * 100, 1) if budget_amt > 0 else 0

		short_name = company.replace("Betul Bio Fuel", "BBF").replace("Betul Biofuel Private Limited", "BBPL").strip()
		result.append({
			"company": company,
			"short_name": short_name,
			"budget": flt(budget_amt),
			"committed": flt(committed),
			"actual": flt(actual),
			"used": used,
			"pct": pct,
		})

		grand_budget += flt(budget_amt)
		grand_used += used

	return {
		"companies": result,
		"totals": {
			"budget": grand_budget,
			"used": grand_used,
			"pct": round((grand_used / grand_budget) * 100, 1) if grand_budget > 0 else 0,
		},
	}


# ═══════════════════════════════════════════════════════════════════════
#  QUALITY OVERVIEW
# ═══════════════════════════════════════════════════════════════════════

def _get_quality(start_date, end_date):
	qi = frappe.db.sql("""
		SELECT
			COUNT(*) as total,
			SUM(CASE WHEN decision = 'Accepted' THEN 1 ELSE 0 END) as accepted,
			SUM(CASE WHEN decision = 'Rejected' THEN 1 ELSE 0 END) as rejected
		FROM `tabBBF Quality Inspection`
		WHERE creation BETWEEN %s AND %s
	""", (start_date, end_date), as_dict=True)
	q = qi[0] if qi else {}

	by_cat = frappe.db.sql("""
		SELECT item_category,
			ROUND(AVG(actual_gcv), 1) as avg_gcv,
			ROUND(AVG(actual_moisture_percent), 2) as avg_moisture,
			COUNT(*) as cnt
		FROM `tabBBF Quality Inspection`
		WHERE creation BETWEEN %s AND %s
		AND item_category IN ('Coal', 'Grain')
		GROUP BY item_category
	""", (start_date, end_date), as_dict=True)

	# Material inspection — use try/except in case table structure differs
	mi_data = {}
	try:
		mi = frappe.db.sql("""
			SELECT status, COUNT(*) as cnt
			FROM `tabBBF Material Inspection`
			WHERE creation BETWEEN %s AND %s
			GROUP BY status
		""", (start_date, end_date), as_dict=True)
		for r in mi:
			mi_data[r.status] = r.cnt
	except Exception:
		pass

	return {
		"total": q.get("total") or 0,
		"accepted": q.get("accepted") or 0,
		"rejected": q.get("rejected") or 0,
		"reject_rate": round(((q.get("rejected") or 0) / (q.get("total") or 1)) * 100, 1),
		"by_category": by_cat,
		"material_inspection": mi_data,
	}


# ═══════════════════════════════════════════════════════════════════════
#  MONTHLY TRENDS (last 6 months)
# ═══════════════════════════════════════════════════════════════════════

def _get_monthly_trend():
	# PO values by month
	po_trend = frappe.db.sql("""
		SELECT DATE_FORMAT(transaction_date, '%%Y-%%m') as month,
			SUM(grand_total) as value, COUNT(*) as cnt
		FROM `tabPurchase Order`
		WHERE docstatus = 1
		AND transaction_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
		GROUP BY month ORDER BY month
	""", as_dict=True)

	# Vehicle entries by month
	vehicle_trend = frappe.db.sql("""
		SELECT DATE_FORMAT(entry_date, '%%Y-%%m') as month,
			COUNT(*) as cnt
		FROM `tabBBF Token`
		WHERE entry_type = 'Material'
		AND entry_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
		GROUP BY month ORDER BY month
	""", as_dict=True)

	# Weight by month (MT)
	weight_trend = frappe.db.sql("""
		SELECT DATE_FORMAT(t.entry_date, '%%Y-%%m') as month,
			ROUND(SUM(wl.net_weight) / 1000, 1) as mt
		FROM `tabBBF Weighbridge Log` wl
		JOIN `tabBBF Token` t ON wl.token_number = t.name
		WHERE wl.net_weight > 0
		AND t.entry_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
		GROUP BY month ORDER BY month
	""", as_dict=True)

	return {
		"po": po_trend,
		"vehicles": vehicle_trend,
		"weight": weight_trend,
	}


# ═══════════════════════════════════════════════════════════════════════
#  TOP SUPPLIERS
# ═══════════════════════════════════════════════════════════════════════

def _get_top_suppliers(start_date, end_date):
	return frappe.db.sql("""
		SELECT supplier_name, company,
			SUM(grand_total) as total_value,
			COUNT(*) as po_count,
			ROUND(SUM(grand_total) / COUNT(*), 0) as avg_po_value
		FROM `tabPurchase Order`
		WHERE docstatus = 1 AND transaction_date BETWEEN %s AND %s
		GROUP BY supplier_name, company
		ORDER BY total_value DESC LIMIT 10
	""", (start_date, end_date), as_dict=True)


# ═══════════════════════════════════════════════════════════════════════
#  NON-BRANDED ITEMS — items missing Brand field
# ═══════════════════════════════════════════════════════════════════════

def _get_non_branded_items():
	items = frappe.db.sql("""
		SELECT name, item_name, item_group, creation
		FROM `tabItem`
		WHERE disabled = 0
		AND (brand IS NULL OR brand = '')
		ORDER BY creation DESC
		LIMIT 50
	""", as_dict=True)

	return {
		"count": len(items),
		"items": items,
	}
