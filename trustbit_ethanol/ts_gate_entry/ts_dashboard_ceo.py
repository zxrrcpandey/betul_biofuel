import frappe
from frappe.utils import getdate, add_days, get_first_day, today, now_datetime, time_diff_in_seconds, flt

CEO_ROLES = ("CEO", "MD", "IT Head", "System Manager")


def _check_role():
	user_roles = frappe.get_roles(frappe.session.user)
	if not any(r in user_roles for r in CEO_ROLES):
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
def get_ceo_dashboard(date_range="This Month", company=None, start=None, end=None):
	"""Return all CEO/MD dashboard data in a single call."""
	_check_role()

	start_date, end_date = _resolve_dates(date_range, start, end)
	if not company:
		company = frappe.defaults.get_global_default("company")

	today_date = getdate(today())
	fiscal_year, fy_start, fy_end = _get_fiscal_year()

	return {
		"kpis": _get_kpis(today_date, start_date, end_date, company, fiscal_year, fy_start, fy_end),
		"action_items": _get_action_items(),
		"daily_ops": _get_daily_ops(today_date),
		"procurement": _get_procurement(start_date, end_date, company),
		"budget_overview": _get_budget_overview(fiscal_year, company, fy_start, fy_end),
		"quality": _get_quality(start_date, end_date),
		"filters_applied": {
			"date_range": date_range,
			"start": str(start_date),
			"end": str(end_date),
			"company": company,
		},
		"last_updated": str(now_datetime()),
	}


# ═══════════════════════════════════════════════════════════════════════
#  KPI CARDS
# ═══════════════════════════════════════════════════════════════════════

def _get_kpis(today_date, start_date, end_date, company, fiscal_year, fy_start, fy_end):
	# Pending approvals for CEO/MD
	pending_pos = frappe.db.sql("""
		SELECT COUNT(*) as cnt FROM `tabPurchase Order`
		WHERE docstatus = 0
		AND (ts_approval_status LIKE 'Pending CEO%%'
			OR ts_approval_status LIKE 'Pending MD%%'
			OR ts_approval_status LIKE 'Awaiting%%Send%%MD%%')
	""")[0][0] or 0

	pending_mrs = frappe.db.sql("""
		SELECT COUNT(*) as cnt FROM `tabMaterial Request`
		WHERE docstatus = 0
		AND (ts_mr_status LIKE 'Pending CEO%%'
			OR ts_mr_status LIKE 'Pending MD%%'
			OR ts_mr_status LIKE 'On Hold%%')
	""")[0][0] or 0

	# Today's inward
	inward = frappe.db.sql("""
		SELECT COUNT(*) as cnt
		FROM `tabTS Token`
		WHERE entry_date = %s AND entry_type = 'Material'
		AND (stock_direction IS NULL OR stock_direction = 'Stock IN' OR stock_direction = '')
	""", today_date)[0][0] or 0

	# Today's weight (net KG from completed weighbridge logs)
	weight_mt = frappe.db.sql("""
		SELECT COALESCE(SUM(wl.net_weight), 0) / 1000 as mt
		FROM `tabTS Weighbridge Log` wl
		JOIN `tabTS Token` t ON wl.token_number = t.name
		WHERE t.entry_date = %s AND wl.net_weight > 0
		AND (wl.stock_direction IS NULL OR wl.stock_direction = 'Stock IN' OR wl.stock_direction = '')
	""", today_date)[0][0] or 0

	# Vehicles inside
	vehicles_inside = frappe.db.sql("""
		SELECT COUNT(*) FROM `tabTS Token`
		WHERE entry_type = 'Material' AND status NOT IN ('Exited', 'Token Generated')
	""")[0][0] or 0

	# Avg turnaround today (minutes)
	avg_ta = frappe.db.sql("""
		SELECT AVG(TIMESTAMPDIFF(MINUTE, g1_entry_time, g1_exit_time)) as avg_min
		FROM `tabTS Token`
		WHERE entry_date = %s AND status = 'Exited' AND entry_type = 'Material'
		AND g1_entry_time IS NOT NULL AND g1_exit_time IS NOT NULL
	""", today_date)[0][0] or 0

	# PO value this period (approved)
	company_filter = f"AND company = {frappe.db.escape(company)}" if company else ""
	po_value = frappe.db.sql(f"""
		SELECT COALESCE(SUM(grand_total), 0) as total
		FROM `tabPurchase Order`
		WHERE docstatus = 1 AND transaction_date BETWEEN %s AND %s
		{company_filter}
	""", (start_date, end_date))[0][0] or 0

	# Budget utilization (overall)
	budget_pct = 0
	if fiscal_year and fy_start and fy_end and company:
		budget_data = frappe.db.sql("""
			SELECT COALESCE(SUM(ba.budget_amount), 0) as total_budget
			FROM `tabBudget Account` ba
			JOIN `tabBudget` b ON ba.parent = b.name
			WHERE b.fiscal_year = %s AND b.company = %s AND b.docstatus = 1
		""", (fiscal_year, company))
		total_budget = flt(budget_data[0][0]) if budget_data else 0

		if total_budget > 0:
			committed = frappe.db.sql("""
				SELECT COALESCE(SUM(grand_total * (1 - IFNULL(per_billed, 0) / 100)), 0)
				FROM `tabPurchase Order`
				WHERE company = %s AND transaction_date BETWEEN %s AND %s
				AND docstatus = 1 AND status NOT IN ('Closed', 'Cancelled')
			""", (company, fy_start, fy_end))[0][0] or 0

			actual = frappe.db.sql("""
				SELECT COALESCE(SUM(debit) - SUM(credit), 0)
				FROM `tabGL Entry`
				WHERE company = %s AND posting_date BETWEEN %s AND %s
				AND is_cancelled = 0 AND voucher_type IN ('Purchase Invoice', 'Journal Entry')
			""", (company, fy_start, fy_end))[0][0] or 0

			budget_pct = round(((flt(committed) + flt(actual)) / total_budget) * 100, 1)

	# Stuck vehicles (CTL breaches)
	settings = frappe.get_single("TS Settings")
	threshold = settings.sla_threshold_minutes or 30
	now = now_datetime()

	stuck = frappe.db.sql("""
		SELECT name, status,
			COALESCE(
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
		FROM `tabTS Token`
		WHERE entry_type = 'Material'
		AND status NOT IN ('Exited', 'Token Generated', 'GRN Created', 'Dispatch Ready')
	""", as_dict=True)
	stuck_count = sum(1 for t in stuck if t.last_ts and time_diff_in_seconds(now, t.last_ts) / 60 > threshold)

	# Pending GRNs
	pending_grn = frappe.db.sql("""
		SELECT COUNT(*) FROM `tabTS Token`
		WHERE status = 'Tare Weighed' AND entry_type = 'Material'
	""")[0][0] or 0

	return {
		"pending_approvals": pending_pos + pending_mrs,
		"pending_pos": pending_pos,
		"pending_mrs": pending_mrs,
		"today_inward": inward,
		"today_weight_mt": round(flt(weight_mt), 1),
		"vehicles_inside": vehicles_inside,
		"avg_turnaround_min": round(flt(avg_ta), 0),
		"po_value": flt(po_value),
		"budget_pct": budget_pct,
		"stuck_vehicles": stuck_count,
		"pending_grn": pending_grn,
	}


# ═══════════════════════════════════════════════════════════════════════
#  ACTION ITEMS — POs + MRs pending CEO/MD
# ═══════════════════════════════════════════════════════════════════════

def _get_action_items():
	now = now_datetime()

	pos = frappe.db.sql("""
		SELECT name, supplier_name, grand_total, ts_purchase_category as category,
			ts_approval_status as status, ts_current_step as step,
			ts_total_steps as total_steps, modified
		FROM `tabPurchase Order`
		WHERE docstatus = 0
		AND (ts_approval_status LIKE 'Pending CEO%%'
			OR ts_approval_status LIKE 'Pending MD%%'
			OR ts_approval_status LIKE 'Awaiting%%Send%%MD%%')
		ORDER BY modified ASC
		LIMIT 20
	""", as_dict=True)

	for po in pos:
		seconds = time_diff_in_seconds(now, po.modified)
		po["waiting_hours"] = round(seconds / 3600, 1) if seconds > 0 else 0

	mrs = frappe.db.sql("""
		SELECT name, cost_center, ts_mr_status as status,
			ts_mr_current_step as step, ts_mr_total_steps as total_steps, modified
		FROM `tabMaterial Request`
		WHERE docstatus = 0
		AND (ts_mr_status LIKE 'Pending CEO%%'
			OR ts_mr_status LIKE 'Pending MD%%'
			OR ts_mr_status LIKE 'On Hold%%')
		ORDER BY modified ASC
		LIMIT 20
	""", as_dict=True)

	for mr in mrs:
		seconds = time_diff_in_seconds(now, mr.modified)
		mr["waiting_hours"] = round(seconds / 3600, 1) if seconds > 0 else 0

	return {"pos": pos, "mrs": mrs}


# ═══════════════════════════════════════════════════════════════════════
#  DAILY OPERATIONS
# ═══════════════════════════════════════════════════════════════════════

def _get_daily_ops(today_date):
	# Today's entries/exits
	today_row = frappe.db.sql("""
		SELECT
			COUNT(*) as entries,
			SUM(CASE WHEN status = 'Exited' THEN 1 ELSE 0 END) as exits,
			SUM(CASE WHEN entry_type = 'Material' AND (stock_direction IS NULL OR stock_direction = 'Stock IN' OR stock_direction = '') THEN 1 ELSE 0 END) as stock_in,
			SUM(CASE WHEN entry_type = 'Material' AND stock_direction = 'Stock OUT' THEN 1 ELSE 0 END) as stock_out,
			SUM(CASE WHEN entry_type = 'Gate Pass' THEN 1 ELSE 0 END) as gate_pass
		FROM `tabTS Token` WHERE entry_date = %s
	""", today_date, as_dict=True)
	t = today_row[0] if today_row else {}

	# This week
	week_start = add_days(today_date, -today_date.weekday())
	week_row = frappe.db.sql("""
		SELECT COUNT(*) as entries,
			SUM(CASE WHEN status = 'Exited' THEN 1 ELSE 0 END) as exits
		FROM `tabTS Token` WHERE entry_date BETWEEN %s AND %s
	""", (week_start, today_date), as_dict=True)
	w = week_row[0] if week_row else {}

	# Weight by material type today
	weight_by_type = frappe.db.sql("""
		SELECT ge.material_flow,
			ROUND(SUM(wl.gross_weight) / 1000, 2) as gross_mt,
			ROUND(SUM(wl.net_weight) / 1000, 2) as net_mt,
			COUNT(*) as cnt
		FROM `tabTS Weighbridge Log` wl
		JOIN `tabTS Gate Entry` ge ON wl.gate_entry = ge.name
		JOIN `tabTS Token` t ON wl.token_number = t.name
		WHERE t.entry_date = %s AND wl.net_weight > 0
		AND (wl.stock_direction IS NULL OR wl.stock_direction = 'Stock IN' OR wl.stock_direction = '')
		GROUP BY ge.material_flow
	""", today_date, as_dict=True)

	# Stage distribution (simplified counts)
	stages = frappe.db.sql("""
		SELECT status, COUNT(*) as cnt
		FROM `tabTS Token`
		WHERE entry_type = 'Material' AND status NOT IN ('Exited', 'Token Generated')
		GROUP BY status
		ORDER BY FIELD(status,
			'PO Linked', 'Gross Weighed', 'Quality Done', 'Graded',
			'Unloading', 'Tare Weighed', 'GRN Created',
			'SI Linked', 'Tare Recorded', 'Loading Done', 'Gross Recorded', 'Dispatch Ready')
	""", as_dict=True)

	return {
		"today": {
			"entries": t.get("entries") or 0,
			"exits": t.get("exits") or 0,
			"stock_in": t.get("stock_in") or 0,
			"stock_out": t.get("stock_out") or 0,
			"gate_pass": t.get("gate_pass") or 0,
		},
		"week": {
			"entries": w.get("entries") or 0,
			"exits": w.get("exits") or 0,
		},
		"weight_by_type": weight_by_type,
		"stages": [{"stage": r.status, "count": r.cnt} for r in stages],
	}


# ═══════════════════════════════════════════════════════════════════════
#  PROCUREMENT HEALTH
# ═══════════════════════════════════════════════════════════════════════

def _get_procurement(start_date, end_date, company):
	company_filter = f"AND po.company = {frappe.db.escape(company)}" if company else ""

	# PO pipeline by category
	pipeline = frappe.db.sql(f"""
		SELECT
			IFNULL(po.ts_purchase_category, 'Uncategorized') as category,
			CASE
				WHEN po.ts_approval_status IN ('', 'Draft') OR po.ts_approval_status IS NULL THEN 'Draft'
				WHEN po.ts_approval_status LIKE 'Pending%%' THEN 'Pending'
				WHEN po.ts_approval_status LIKE 'Awaiting%%' THEN 'Awaiting'
				WHEN po.ts_approval_status = 'Approved' THEN 'Approved'
				WHEN po.ts_approval_status = 'Rejected' THEN 'Rejected'
				ELSE po.ts_approval_status
			END as status_group,
			COUNT(*) as cnt,
			SUM(po.grand_total) as value
		FROM `tabPurchase Order` po
		WHERE po.docstatus < 2
		AND po.transaction_date BETWEEN %s AND %s
		{company_filter}
		GROUP BY category, status_group
	""", (start_date, end_date), as_dict=True)

	categories = {}
	for r in pipeline:
		cat = r.category
		if cat not in categories:
			categories[cat] = {"category": cat, "Draft": 0, "Pending": 0, "Awaiting": 0, "Approved": 0, "Rejected": 0}
		categories[cat][r.status_group] = r.cnt

	# Monthly trend (last 6 months)
	monthly = frappe.db.sql(f"""
		SELECT DATE_FORMAT(po.transaction_date, '%Y-%m') as month,
			SUM(po.grand_total) as value, COUNT(*) as cnt
		FROM `tabPurchase Order` po
		WHERE po.docstatus = 1 AND po.transaction_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
		{company_filter}
		GROUP BY month ORDER BY month
	""", as_dict=True)

	# Top 5 suppliers
	suppliers = frappe.db.sql(f"""
		SELECT po.supplier_name, SUM(po.grand_total) as total_value, COUNT(*) as po_count
		FROM `tabPurchase Order` po
		WHERE po.docstatus = 1 AND po.transaction_date BETWEEN %s AND %s
		{company_filter}
		GROUP BY po.supplier_name
		ORDER BY total_value DESC LIMIT 5
	""", (start_date, end_date), as_dict=True)

	return {
		"po_pipeline": list(categories.values()),
		"monthly_trend": monthly,
		"top_suppliers": suppliers,
	}


# ═══════════════════════════════════════════════════════════════════════
#  BUDGET OVERVIEW
# ═══════════════════════════════════════════════════════════════════════

def _get_budget_overview(fiscal_year, company, fy_start, fy_end):
	if not fiscal_year or not fy_start or not fy_end or not company:
		return {"cc_data": [], "totals": {}, "overrides": []}

	# All budgets for this FY + company
	budgets = frappe.db.sql("""
		SELECT b.cost_center, SUM(ba.budget_amount) as budget
		FROM `tabBudget Account` ba
		JOIN `tabBudget` b ON ba.parent = b.name
		WHERE b.fiscal_year = %s AND b.company = %s AND b.docstatus = 1
		GROUP BY b.cost_center
	""", (fiscal_year, company), as_dict=True)

	if not budgets:
		return {"cc_data": [], "totals": {}, "overrides": []}

	# Committed per CC
	committed_map = {}
	committed_rows = frappe.db.sql("""
		SELECT cost_center, COALESCE(SUM(grand_total * (1 - IFNULL(per_billed, 0) / 100)), 0) as committed
		FROM `tabPurchase Order`
		WHERE company = %s AND transaction_date BETWEEN %s AND %s
		AND docstatus = 1 AND status NOT IN ('Closed', 'Cancelled')
		GROUP BY cost_center
	""", (company, fy_start, fy_end), as_dict=True)
	for r in committed_rows:
		committed_map[r.cost_center] = flt(r.committed)

	# Actual per CC
	actual_map = {}
	actual_rows = frappe.db.sql("""
		SELECT cost_center, COALESCE(SUM(debit) - SUM(credit), 0) as actual
		FROM `tabGL Entry`
		WHERE company = %s AND posting_date BETWEEN %s AND %s
		AND is_cancelled = 0 AND voucher_type IN ('Purchase Invoice', 'Journal Entry')
		GROUP BY cost_center
	""", (company, fy_start, fy_end), as_dict=True)
	for r in actual_rows:
		actual_map[r.cost_center] = flt(r.actual)

	# Build CC data
	cc_data = []
	total_budget = 0
	total_committed = 0
	total_actual = 0

	for b in budgets:
		cc = b.cost_center
		budget_amt = flt(b.budget)
		committed_amt = committed_map.get(cc, 0)
		actual_amt = actual_map.get(cc, 0)
		used = committed_amt + actual_amt
		pct = round((used / budget_amt) * 100, 1) if budget_amt > 0 else 0

		cc_data.append({
			"cost_center": cc,
			"budget": budget_amt,
			"committed": committed_amt,
			"actual": actual_amt,
			"used": used,
			"pct": pct,
		})

		total_budget += budget_amt
		total_committed += committed_amt
		total_actual += actual_amt

	cc_data.sort(key=lambda x: x["pct"], reverse=True)

	# Recent overrides
	overrides = frappe.db.sql("""
		SELECT parent as po_name, override_by, override_date, reason,
			budget_available, po_amount, shortfall
		FROM `tabTS Budget Override Log`
		ORDER BY creation DESC LIMIT 5
	""", as_dict=True)

	return {
		"cc_data": cc_data[:15],
		"totals": {
			"budget": total_budget,
			"committed": total_committed,
			"actual": total_actual,
			"pct": round(((total_committed + total_actual) / total_budget) * 100, 1) if total_budget > 0 else 0,
		},
		"overrides": overrides,
	}


# ═══════════════════════════════════════════════════════════════════════
#  QUALITY & REJECTIONS
# ═══════════════════════════════════════════════════════════════════════

def _get_quality(start_date, end_date):
	# QI summary
	qi = frappe.db.sql("""
		SELECT
			COUNT(*) as total,
			SUM(CASE WHEN decision = 'Accepted' THEN 1 ELSE 0 END) as accepted,
			SUM(CASE WHEN decision = 'Rejected' THEN 1 ELSE 0 END) as rejected,
			SUM(CASE WHEN decision = 'Hold' THEN 1 ELSE 0 END) as on_hold
		FROM `tabTS Quality Inspection`
		WHERE creation BETWEEN %s AND %s
	""", (start_date, end_date), as_dict=True)
	qi_data = qi[0] if qi else {"total": 0, "accepted": 0, "rejected": 0, "on_hold": 0}

	# Avg GCV/Moisture by category
	by_category = frappe.db.sql("""
		SELECT item_category,
			ROUND(AVG(actual_gcv), 1) as avg_gcv,
			ROUND(AVG(actual_moisture_percent), 2) as avg_moisture,
			COUNT(*) as cnt
		FROM `tabTS Quality Inspection`
		WHERE creation BETWEEN %s AND %s
		AND item_category IN ('Coal', 'Grain')
		GROUP BY item_category
	""", (start_date, end_date), as_dict=True)

	# Material Inspection (Non-RM) status
	mi = frappe.db.sql("""
		SELECT status, COUNT(*) as cnt
		FROM `tabTS Material Inspection`
		WHERE creation BETWEEN %s AND %s
		GROUP BY status
	""", (start_date, end_date), as_dict=True)

	mi_data = {}
	for r in mi:
		mi_data[r.status] = r.cnt

	return {
		"qi": qi_data,
		"by_category": by_category,
		"material_inspection": mi_data,
	}
