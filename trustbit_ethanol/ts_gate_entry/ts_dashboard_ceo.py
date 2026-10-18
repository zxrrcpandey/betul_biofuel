import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import (
	getdate, add_days, add_months, add_to_date, get_first_day, today, now_datetime,
	time_diff_in_seconds, time_diff_in_hours, flt,
)

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

	budget_overview = _get_budget_overview(fiscal_year, company, fy_start, fy_end)
	kpis = _get_kpis(today_date, start_date, end_date, company)
	# The tile uses the same per-cost-centre totals as the budget panel (and the
	# TS Budget Dashboard report); a company-wide GL sum picks up cost centres
	# that have no budget at all.
	kpis["budget_pct"] = (budget_overview.get("totals") or {}).get("pct", 0)

	return {
		"kpis": kpis,
		"action_items": _get_action_items(),
		"daily_ops": _get_daily_ops(today_date),
		"procurement": _get_procurement(start_date, end_date, company),
		"budget_overview": budget_overview,
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

def _get_kpis(today_date, start_date, end_date, company):
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
		WHERE entry_type = 'Material' AND status NOT IN ('Exited', 'Campus Exited', 'Token Generated')
	""")[0][0] or 0

	# Avg turnaround today (minutes)
	avg_ta = frappe.db.sql("""
		SELECT AVG(TIMESTAMPDIFF(MINUTE, g1_entry_time, g1_exit_time)) as avg_min
		FROM `tabTS Token`
		WHERE entry_date = %s AND status IN ('Exited', 'Campus Exited') AND entry_type = 'Material'
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
		AND status NOT IN ('Exited', 'Campus Exited', 'Plant Exited', 'Token Generated', 'GRN Created', 'Dispatch Ready')
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
			SUM(CASE WHEN status IN ('Exited', 'Campus Exited') THEN 1 ELSE 0 END) as exits,
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
			SUM(CASE WHEN status IN ('Exited', 'Campus Exited') THEN 1 ELSE 0 END) as exits
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
		WHERE entry_type = 'Material' AND status NOT IN ('Exited', 'Campus Exited', 'Token Generated')
		GROUP BY status
		ORDER BY FIELD(status,
			'G1 Entered', 'G2 Entered',
			'PO Linked', 'Gross Weighed', 'Quality Done', 'Graded',
			'Unloading', 'Tare Weighed', 'GRN Created', 'Plant Exited',
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
		SELECT gle.cost_center, COALESCE(SUM(gle.debit) - SUM(gle.credit), 0) as actual
		FROM `tabGL Entry` gle
		INNER JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE gle.company = %s AND gle.posting_date BETWEEN %s AND %s
		AND gle.is_cancelled = 0 AND acc.root_type = 'Expense'
		AND gle.voucher_type IN ('Purchase Invoice', 'Journal Entry', 'Stock Entry')
		GROUP BY gle.cost_center
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
	# `creation` is a datetime: compare against the day AFTER end_date, or a
	# BETWEEN on bare dates drops everything created on the end day itself.
	end_date = add_days(end_date, 1)
	# QI summary
	qi = frappe.db.sql("""
		SELECT
			COUNT(*) as total,
			SUM(CASE WHEN decision = 'Accepted' THEN 1 ELSE 0 END) as accepted,
			SUM(CASE WHEN decision = 'Rejected' THEN 1 ELSE 0 END) as rejected,
			SUM(CASE WHEN decision = 'Hold' THEN 1 ELSE 0 END) as on_hold
		FROM `tabTS Quality Inspection`
		WHERE creation >= %s AND creation < %s
	""", (start_date, end_date), as_dict=True)
	qi_data = qi[0] if qi else {"total": 0, "accepted": 0, "rejected": 0, "on_hold": 0}

	# Avg GCV/Moisture by category
	by_category = frappe.db.sql("""
		SELECT item_category,
			ROUND(AVG(actual_gcv), 1) as avg_gcv,
			ROUND(AVG(actual_moisture_percent), 2) as avg_moisture,
			COUNT(*) as cnt
		FROM `tabTS Quality Inspection`
		WHERE creation >= %s AND creation < %s
		AND item_category IN ('Coal', 'Grain')
		GROUP BY item_category
	""", (start_date, end_date), as_dict=True)

	# Material Inspection (Non-RM) status
	mi = frappe.db.sql("""
		SELECT status, COUNT(*) as cnt
		FROM `tabTS Material Inspection`
		WHERE creation >= %s AND creation < %s
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


# ═══════════════════════════════════════════════════════════════════════
#  v2.64.0 — REDESIGNED PAGE: get_ceo_overview
#  The /app/ceo-dashboard page reads this. get_ceo_dashboard above stays
#  because the CEO Wall Display (ceo_wall_display.js) reads its payload.
#  Every PO / MR read here is raw SQL, so each one carries the
#  confidential_sql_clause itself (L297) — the list-view PQC never runs.
# ═══════════════════════════════════════════════════════════════════════

_OV_RANGES = ("Today", "This Week", "This Month", "This Quarter", "This Year")
_OV_CATEGORIES = ("Grain", "Coal", "Store", "Capex")


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def get_ceo_overview(date_range="This Month", company=None):
	"""The CEO dashboard: what waits for the viewer, purchases, budget,
	product stock and the gate — each section fails on its own."""
	_check_role()
	from trustbit_ethanol import __version__
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential
	from trustbit_ethanol.ts_gate_entry.ts_exec_api import _log_once

	if date_range not in _OV_RANGES:
		frappe.throw(frappe._("Invalid period."), frappe.ValidationError)
	company = company or frappe.defaults.get_global_default("company")
	# isinstance: a JSON body can send a dict, which db.exists reads as filters.
	if not isinstance(company, str) or not frappe.db.exists("Company", {"name": company}):
		frappe.throw(frappe._("Invalid company."), frappe.ValidationError)

	now = now_datetime()
	start, end, prev_start, prev_end = _ov_window(date_range, company)
	out = {
		"filters": {
			"date_range": date_range, "company": company,
			"start": str(start), "end": str(end),
			"prev_start": str(prev_start), "prev_end": str(prev_end),
		},
		"as_of": str(now),
		"version": __version__,
		"confidential_scope": "full" if user_sees_confidential("Purchase Order") else "partial",
	}
	sections = (
		("waiting", lambda: _ov_waiting(now)),
		("stuck", lambda: _ov_stuck(now)),
		("purchases", lambda: _ov_purchases(company, start, end, prev_start, prev_end)),
		("budget", lambda: _ov_budget(company)),
		("stock", lambda: _ov_stock(company)),
		("plant", lambda: _ov_plant(now)),
	)
	for key, build in sections:
		try:
			out[key] = build()
		except Exception:
			_log_once("ceo_ov:" + key, "CEO dashboard section failed", frappe.get_traceback())
			frappe.clear_messages()
			out[key] = {"state": "error"}
	return out


def _ov_window(date_range, company):
	"""(start, end, prev_start, prev_end): the period to date, and the same
	number of days into the previous period. 'This Year' is the fiscal year."""
	t = getdate(today())
	if date_range == "Today":
		return t, t, add_days(t, -1), add_days(t, -1)
	if date_range == "This Week":
		s = add_days(t, -t.weekday())
		return s, t, add_days(s, -7), add_days(t, -7)
	if date_range == "This Quarter":
		s = getdate(f"{t.year}-{((t.month - 1) // 3) * 3 + 1:02d}-01")
		ps = add_months(s, -3)
	elif date_range == "This Year":
		from trustbit_ethanol.ts_gate_entry.ts_budget import _get_fiscal_year_dates, _resolve_fiscal_year
		fy = _resolve_fiscal_year(company)
		s = getdate(_get_fiscal_year_dates(fy)[0]) if fy else getdate(f"{t.year}-01-01")
		ps = add_months(s, -12)
	else:
		s = get_first_day(t)
		ps = add_months(s, -1)
	return s, t, ps, min(add_days(ps, (t - s).days), add_days(s, -1))


def _ov_statuses(doctype, roles=None):
	"""Pending statuses the viewer approves, from ROLE_STATUS_MAP (the inboxes'
	vocabulary). IT Head / System Manager without CEO or MD see both queues.
	roles: a fixed queue instead (the MD page always shows the MD's)."""
	from trustbit_ethanol.ts_gate_entry.ts_my_approvals_api import ROLE_STATUS_MAP

	roles = set(roles or ()) or (set(frappe.get_roles()) & {"CEO", "MD"}) or {"CEO", "MD"}
	cfg = ROLE_STATUS_MAP[doctype]
	statuses = {s for r in roles for s in cfg["roles"].get(r, []) if s.startswith("Pending")}
	return cfg["field"], tuple(sorted(statuses))


def _ov_queue(doctype, kind, sql_from, value_sql, party_sql, params, now, filters, limit=5):
	agg = frappe.db.sql(f"SELECT COUNT(*), SUM({value_sql}), MIN(x.modified) {sql_from}", params)[0]
	rows = frappe.db.sql(f"""
		SELECT x.name, x.modified, {value_sql} AS value, {party_sql} AS party
		{sql_from} ORDER BY x.modified ASC LIMIT {int(limit)}
	""", params, as_dict=True)
	return {
		"doctype": doctype,
		"count": agg[0] or 0,
		"value": flt(agg[1]) if agg[1] is not None else None,
		"oldest_hours": round(time_diff_in_hours(now, agg[2]), 1) if agg[2] else None,
		"filters": filters,
		"rows": [{
			"doctype": doctype, "kind": kind, "name": r.name, "party": r.party or "",
			"value": flt(r.value) if r.value is not None else None,
			"hours": round(time_diff_in_hours(now, r.modified), 1),
		} for r in rows],
	}


def _ov_waiting(now, roles=None, limit=5):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause as conf

	po_field, po_st = _ov_statuses("Purchase Order", roles)
	mr_field, mr_st = _ov_statuses("Material Request", roles)
	out = {}
	if roles is None or "CEO" in roles:  # budget overrides are the CEO's queue only
		out["overrides"] = _ov_queue(
			"TS Budget Override Approval", "Budget override",
			f"""FROM `tabTS Budget Override Approval` x
			LEFT JOIN `tabPurchase Order` po ON x.reference_doctype = 'Purchase Order' AND po.name = x.reference_name
			LEFT JOIN `tabMaterial Request` mr ON x.reference_doctype = 'Material Request' AND mr.name = x.reference_name
			WHERE x.docstatus = 1 AND x.status = 'Pending CEO' {conf('po')} {conf('mr', doctype='Material Request')}""",
			"x.source_amount", "CONCAT_WS(' · ', x.reference_name, x.cost_center)", {}, now,
			{"docstatus": 1, "status": "Pending CEO"}, limit,
		)
	out["pos"] = _ov_queue(
		"Purchase Order", "Purchase order",
		f"FROM `tabPurchase Order` x WHERE x.docstatus = 0 AND x.ts_approval_status IN %(st)s {conf('x')}",
		"x.base_grand_total", "CONCAT_WS(' · ', TRIM(x.supplier_name), x.ts_purchase_category)", {"st": po_st}, now,
		{"docstatus": 0, po_field: ["in", list(po_st)]}, limit,
	)
	out["mrs"] = _ov_queue(
		"Material Request", "Material request",
		f"""FROM `tabMaterial Request` x WHERE x.docstatus = 0 AND x.ts_mr_status IN %(st)s
		{conf('x', doctype='Material Request')}""",
		"NULL", "x.cost_center", {"st": mr_st}, now,
		{"docstatus": 0, mr_field: ["in", list(mr_st)]}, limit,
	)
	queues = list(out.values())
	oldest = sorted((r for q in queues for r in q.pop("rows")), key=lambda r: -r["hours"])[:limit]
	return dict(out, state="ok", total=sum(q["count"] for q in queues), oldest=oldest)


def _ov_stuck(now):
	"""Documents in no one's queue: parked at Pending Budget Override with no
	live override, and stages no approver role maps to (reused from the exec
	app so both screens agree)."""
	from trustbit_ethanol.ts_gate_entry.ts_exec_api import _card_approvals, _orphaned_and_hidden

	noun = {"Purchase Order": ("purchase order", "purchase orders"),
			"Material Request": ("material request", "material requests")}
	field = {"Purchase Order": "ts_approval_status", "Material Request": "ts_mr_status"}
	def label(dt, n):
		return f"{n} {noun[dt][0]} waits" if n == 1 else f"{n} {noun[dt][1]} wait"

	orphaned, approvals = _orphaned_and_hidden(now), _card_approvals(now)
	items = []
	for dt in ("Purchase Order", "Material Request"):
		docs = [o for o in orphaned["orphaned"]["items"] if o["doctype"] == dt]
		if docs:
			items.append({
				"doctype": dt, "count": len(docs), "days": max(o["days"] for o in docs),
				"text": f"{label(dt, len(docs))} at Pending Budget Override, but no override is pending",
				"filters": {"name": ["in", [o["name"] for o in docs]]},
			})
	for u in approvals["unrouted"]["items"]:
		if u.get("companion"):
			continue  # Pending Budget Override with a live override is in the overrides tile
		items.append({
			"doctype": u["doctype"], "count": u["count"], "days": u["days"],
			"text": f"{label(u['doctype'], u['count'])} at "
					f"{u['stage'] or '(no status)'} — no approver is set for this step",
			"filters": {"docstatus": 0, field[u["doctype"]]: u["stage"] or ["is", "not set"]},
		})
	return {
		"state": "ok", "count": sum(i["count"] for i in items), "items": items,
		"partial": bool(orphaned["partial"] or approvals["partial"]),
	}


def _ov_purchases(company, start, end, prev_start, prev_end):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause as conf

	where = f"po.docstatus = 1 AND po.company = %(co)s {conf('po')}"
	cat = "IFNULL(NULLIF(po.ts_purchase_category, ''), 'Other')"

	def by_category(a, b):
		return frappe.db.sql(f"""
			SELECT {cat} AS category, COUNT(*) AS count, SUM(po.base_grand_total) AS value
			FROM `tabPurchase Order` po
			WHERE {where} AND po.transaction_date BETWEEN %(a)s AND %(b)s
			GROUP BY category
		""", {"co": company, "a": a, "b": b}, as_dict=True)

	current = by_category(start, end)
	previous = by_category(prev_start, prev_end)
	order = {c: i for i, c in enumerate(_OV_CATEGORIES)}
	current.sort(key=lambda r: (order.get(r.category, 99), r.category))

	m0 = add_months(get_first_day(getdate(today())), -5)
	months = [add_months(m0, i) for i in range(6)]
	trend = {m.strftime("%Y-%m"): {"label": m.strftime("%b"), "total": 0.0, "by": {}} for m in months}
	for r in frappe.db.sql(f"""
		SELECT DATE_FORMAT(po.transaction_date, '%%Y-%%m') AS month, {cat} AS category,
			SUM(po.base_grand_total) AS value
		FROM `tabPurchase Order` po
		WHERE {where} AND po.transaction_date >= %(m0)s
		GROUP BY month, category
	""", {"co": company, "m0": m0}, as_dict=True):
		if r.month in trend:
			trend[r.month]["by"][r.category] = flt(r.value)
			trend[r.month]["total"] += flt(r.value)

	suppliers = frappe.db.sql(f"""
		SELECT po.supplier, MAX(TRIM(po.supplier_name)) AS supplier_name, COUNT(*) AS count,
			SUM(po.base_grand_total) AS value,
			SUBSTRING_INDEX(GROUP_CONCAT({cat} ORDER BY po.base_grand_total DESC), ',', 1) AS category
		FROM `tabPurchase Order` po
		WHERE {where} AND po.transaction_date BETWEEN %(a)s AND %(b)s
		GROUP BY po.supplier ORDER BY value DESC LIMIT 5
	""", {"co": company, "a": start, "b": end}, as_dict=True)

	return {
		"state": "ok",
		"current": sum(flt(r.value) for r in current),
		"previous": sum(flt(r.value) for r in previous),
		"count": sum(r["count"] for r in current),
		"by_category": [{"category": r.category, "count": r["count"], "value": flt(r.value)} for r in current],
		"previous_by_category": {r.category: flt(r.value) for r in previous},
		"trend": list(trend.values()),
		"top_suppliers": [{
			"supplier": s.supplier, "supplier_name": s.supplier_name, "category": s.category,
			"count": s["count"], "value": flt(s.value),
		} for s in suppliers],
	}


def _ov_budget(company):
	"""Same figures as the TS Budget Dashboard report (expense accounts only).
	That function has no confidentiality filter, so a viewer who cannot see
	confidential POs gets nothing rather than leaked committed values."""
	from trustbit_ethanol.ts_gate_entry.ts_budget import _resolve_fiscal_year, get_budget_dashboard
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential

	if not user_sees_confidential("Purchase Order"):
		return {"state": "restricted"}
	fy = _resolve_fiscal_year(company)
	d = get_budget_dashboard(fy, company)
	rows = [{
		# A Budget can be against a Project, leaving cost_center empty.
		"cost_center": r["cost_center"],
		"label": (r["cost_center"] or frappe._("(no cost centre)")).rsplit(" - ", 1)[0],
		"budget": r["annual_budget"],
		"used": r["committed"] + r["actual_spent"],
		"pct": r["utilization_pct"],
	} for r in d.get("data") or []]
	return {
		"state": "ok", "fiscal_year": fy, "company": company, "summary": d.get("summary") or {},
		"rows": rows[:24], "total_rows": len(rows),
		"over_count": len([r for r in rows if r["pct"] > 100]),
	}


def _ov_stock(company):
	"""Product stock per item in its own UOM — never summed across items."""
	from trustbit_ethanol.ts_gate_entry.ts_dashboard_omc_supply import _resolve_ethanol_items

	codes, source = _resolve_ethanol_items()
	if not codes:
		return {"state": "ok", "products": {"state": "unconfigured", "source": source, "items": []}}
	items = frappe.db.sql("""
		SELECT i.name AS item_code, i.item_name, i.stock_uom AS uom, COALESCE(SUM(b.actual_qty), 0) AS qty
		FROM `tabItem` i
		LEFT JOIN `tabBin` b ON b.item_code = i.name
			AND b.warehouse IN (SELECT w.name FROM `tabWarehouse` w WHERE w.company = %(co)s)
		WHERE i.name IN %(codes)s
		GROUP BY i.name, i.item_name, i.stock_uom
		ORDER BY qty DESC LIMIT 12
	""", {"co": company, "codes": tuple(codes)}, as_dict=True)
	for i in items:
		i.qty = flt(i.qty, 2)
	return {"state": "ok", "products": {"state": "ok", "source": source, "items": items}}


def _ov_plant(now):
	"""Gate numbers from the exec app's gate card, so both screens agree."""
	from trustbit_ethanol.ts_gate_entry.ts_exec_api import _TOKEN_EXITED, _card_gate

	g = _card_gate(now)
	ip, gh, go = g.get("in_plant"), g.get("grain_held"), g.get("grn_overdue")
	return {
		"state": "ok" if (ip or gh or go) else "error",
		"arrived_24h": ip["arrived_today"] if ip else None,
		# No microseconds: the list filter's date check rejects them.
		"arrived_since": add_to_date(now, hours=-24).strftime("%Y-%m-%d %H:%M:%S"),
		"arrived_exclude": list(_TOKEN_EXITED),
		# *_all: the whole queue — count/over_6h stop at the exec list cap of 50.
		"grain_waiting": gh.get("count_all", gh["count"]) if gh else None,
		"grain_over_6h": gh.get("over_6h_all", gh["over_6h"]) if gh else None,
		"receipt_owed": go["count"] if go else None,
		"receipt_owed_grain": go["grain"] if go else None,
		"receipt_owed_non_rm": go["non_rm"] if go else None,
	}
