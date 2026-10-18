import hmac

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import getdate, add_days, get_first_day, today, now_datetime, time_diff_in_seconds, flt

MD_ROLES = ("MD", "IT Head", "System Manager")


def _check_role():
	user_roles = frappe.get_roles(frappe.session.user)
	if not any(r in user_roles for r in MD_ROLES):
		frappe.throw("Not authorized to view this dashboard", frappe.PermissionError)


def _get_md_pin():
	"""Read the MD Dashboard PIN (Password field — must decrypt)."""
	from frappe.utils.password import get_decrypted_password
	try:
		return get_decrypted_password("TS Settings", "TS Settings", "md_dashboard_pin")
	except Exception:
		return None


@frappe.whitelist()
def check_md_pin_required():
	"""Check if MD Dashboard PIN lock is enabled."""
	_check_role()
	pin = _get_md_pin()
	return {"pin_required": bool(pin)}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=300)
def verify_md_pin(pin):
	"""Verify PIN for MD Dashboard access. Rate-limited: the PIN is short."""
	_check_role()
	stored_pin = _get_md_pin()
	if not stored_pin:
		return {"valid": True}
	if isinstance(pin, str) and hmac.compare_digest(pin.encode(), stored_pin.encode()):
		return {"valid": True}
	frappe.log_error(
		title="MD Dashboard PIN Failed",
		message=f"Failed PIN attempt by {frappe.session.user} from {frappe.local.request_ip}"
	)
	return {"valid": False}


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


# ═══════════════════════════════════════════════════════════════════════
#  v2.65.0 — MD DASHBOARD PAGE: get_md_overview
#  /app/md-dashboard reads this. get_md_dashboard further down stays for the
#  MD Wall Display (md_wall_display.js). The queue is always the MD's, whoever
#  views it: admin accounts also hold CEO, whose queue is on the CEO page.
#  Every PO / PR / PI read here is raw SQL, so each one carries the
#  confidential_sql_clause itself (L297) — the list-view PQC never runs.
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def get_md_overview(date_range="This Month", company=None):
	"""The MD dashboard: the MD's approval queue, what the MD signed off,
	budget, grain, companies, purchases and the gate — each section fails
	on its own."""
	_check_role()
	from trustbit_ethanol import __version__
	from trustbit_ethanol.ts_gate_entry import ts_dashboard_ceo as ov
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential
	from trustbit_ethanol.ts_gate_entry.ts_exec_api import _log_once

	if date_range not in ov._OV_RANGES:
		frappe.throw(frappe._("Invalid period."), frappe.ValidationError)
	company = company or frappe.defaults.get_global_default("company")
	# isinstance: a JSON body can send a dict, which db.exists reads as filters.
	# {"name": ...}: a bare string equal to the doctype name ("Company") matches.
	if not isinstance(company, str) or not frappe.db.exists("Company", {"name": company}):
		frappe.throw(frappe._("Invalid company."), frappe.ValidationError)

	now = now_datetime()
	start, end, prev_start, prev_end = ov._ov_window(date_range, company)
	budgets = {}

	def budget_for(co):  # the selected company's budget is also one company card
		if co not in budgets:
			try:  # one company's budget failure must not blank every company card
				budgets[co] = ov._ov_budget(co)
			except Exception:
				_log_once("md_ov:budget", "MD dashboard section failed", frappe.get_traceback())
				frappe.clear_messages()
				budgets[co] = {"state": "error"}
		return budgets[co]

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
		("waiting", lambda: _md_waiting(now)),
		("approved", lambda: _md_approved(start, end)),
		("budget", lambda: budget_for(company)),
		("grain", lambda: _md_grain(start, end, prev_start, prev_end)),
		("companies", lambda: _md_companies(start, end, budget_for)),
		("purchases", lambda: ov._ov_purchases(company, start, end, prev_start, prev_end)),
		("plant", lambda: ov._ov_plant(now)),
		("brandless", lambda: dict(_get_non_branded_items(limit=5), state="ok")),
	)
	for key, build in sections:
		try:
			out[key] = build()
		except Exception:
			_log_once("md_ov:" + key, "MD dashboard section failed", frappe.get_traceback())
			frappe.clear_messages()
			out[key] = {"state": "error"}
	return out


def _md_waiting(now):
	"""POs and MRs at the MD's step, plus post-dated entry requests (CEO or MD
	approves those) — up to 20 rows, oldest first."""
	from trustbit_ethanol.ts_gate_entry.ts_dashboard_ceo import _ov_queue, _ov_waiting

	w = _ov_waiting(now, roles={"MD"}, limit=20)
	pd = _ov_queue(
		"TS Post Dated Entry Request", "Post-dated entry",
		"FROM `tabTS Post Dated Entry Request` x WHERE x.status = 'Pending Approval' AND x.docstatus < 2",
		"NULL", "CONCAT_WS(' · ', x.request_type, COALESCE("
		"(SELECT u.full_name FROM `tabUser` u WHERE u.name = x.requested_by), x.requested_by))", {}, now,
		{"status": "Pending Approval"}, 20,
	)
	w["oldest"] = sorted(w["oldest"] + pd.pop("rows"), key=lambda r: -r["hours"])[:20]
	w["post_dated"] = pd
	w["total"] += pd["count"]
	return w


def _md_approved(start, end):
	"""POs the MD signed off in the period, read from the approval log — so the
	₹6 L / ₹30 L route thresholds live only in the approval rules."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause as conf
	from trustbit_ethanol.ts_gate_entry.ts_dashboard_ceo import _ov_statuses

	md_st = _ov_statuses("Purchase Order", {"MD"})[1]
	r = frappe.db.sql(f"""
		SELECT COUNT(*), SUM(po.base_grand_total) FROM `tabPurchase Order` po
		WHERE po.docstatus = 1 {conf('po')} AND po.name IN (
			SELECT al.parent FROM `tabTS Approval Log` al
			WHERE al.parenttype = 'Purchase Order' AND al.action IN ('Approved', 'Final Approved')
				AND al.from_state IN %(st)s AND al.action_date >= %(a)s AND al.action_date < %(b)s)
	""", {"st": md_st, "a": start, "b": add_days(end, 1)})[0]  # action_date is a datetime
	return {"state": "ok", "count": r[0] or 0, "value": flt(r[1])}


def _md_grain(start, end, prev_start, prev_end):
	"""Grain received: weighbridge net weight by the token's entry date. Grain =
	a submitted Raw-Material gate entry of a grain material type (the grain-defer
	test), so loads still waiting for a PO link are counted too. Before June 2026
	some grain loads were saved with no material type (229 on prod); those count
	when their PO's category is Grain."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause as conf
	from trustbit_ethanol.ts_gate_entry.ts_grain_defer import GRAIN_MATERIAL_TYPES

	def received(a, b):
		r = frappe.db.sql(f"""
			SELECT COUNT(*), SUM(x.nw) FROM (
				SELECT wl.token_number, MAX(wl.net_weight) AS nw
				FROM `tabTS Weighbridge Log` wl
				JOIN `tabTS Token` t ON t.name = wl.token_number
				WHERE wl.net_weight > 0 AND IFNULL(wl.stock_direction, '') IN ('', 'Stock IN')
					AND t.entry_date BETWEEN %(a)s AND %(b)s
					AND EXISTS (SELECT 1 FROM `tabTS Gate Entry` ge
						LEFT JOIN `tabPurchase Order` po ON po.name = ge.purchase_order
						WHERE ge.token_number = t.name AND ge.docstatus = 1
							AND ge.material_flow = 'Raw Material'
							AND (ge.ts_material_type IN %(grain)s
								OR (IFNULL(ge.ts_material_type, '') = '' AND po.ts_purchase_category = 'Grain' {conf('po')})))
				GROUP BY wl.token_number) x
		""", {"a": a, "b": b, "grain": tuple(sorted(GRAIN_MATERIAL_TYPES))})[0]
		return r[0] or 0, round(flt(r[1]) / 1000, 1)

	loads, mt = received(start, end)
	return {"state": "ok", "loads": loads, "mt": mt, "prev_mt": received(prev_start, prev_end)[1]}


def _md_companies(start, end, budget_for):
	rows = []
	for c in _md_company_list():
		b = budget_for(c.name)
		s = b.get("summary") or {}
		rows.append(dict(
			_md_company_totals(c.name, start, end),
			company=c.name, abbr=c.abbr or c.name, budget_state=b.get("state"),
			fiscal_year=b.get("fiscal_year"), budget_total=flt(s.get("total_budget")),
			budget_used=flt(s.get("total_committed")) + flt(s.get("total_actual")),
			budget_pct=flt(s.get("overall_utilization")),
		))
	return {"state": "ok", "rows": rows}


def _md_company_list():
	"""Companies that buy: a submitted PO or budget. Not is_group = 0 — on prod
	the main company (Betul Biofuel Pvt. Ltd.) is a group company."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause as conf

	return frappe.db.sql(f"""
		SELECT c.name, c.abbr FROM `tabCompany` c
		WHERE EXISTS (SELECT 1 FROM `tabPurchase Order` po WHERE po.company = c.name AND po.docstatus = 1 {conf('po')})
			OR EXISTS (SELECT 1 FROM `tabBudget` b WHERE b.company = c.name AND b.docstatus = 1)
		ORDER BY c.name
	""", as_dict=True)


def _md_company_totals(company, a, b):
	"""PO value and count, invoice value and GRN count for one company in a period."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause as conf

	p = {"co": company, "a": a, "b": b}
	po = frappe.db.sql(f"""
		SELECT COUNT(*), SUM(po.base_grand_total) FROM `tabPurchase Order` po
		WHERE po.docstatus = 1 AND po.company = %(co)s AND po.transaction_date BETWEEN %(a)s AND %(b)s {conf('po')}
	""", p)[0]
	pi = frappe.db.sql(f"""
		SELECT SUM(pi.base_grand_total) FROM `tabPurchase Invoice` pi
		WHERE pi.docstatus = 1 AND pi.company = %(co)s AND pi.posting_date BETWEEN %(a)s AND %(b)s
		{conf('pi', doctype='Purchase Invoice')}
	""", p)[0][0]
	pr = frappe.db.sql(f"""
		SELECT COUNT(*) FROM `tabPurchase Receipt` pr
		WHERE pr.docstatus = 1 AND pr.company = %(co)s AND pr.posting_date BETWEEN %(a)s AND %(b)s
		{conf('pr', doctype='Purchase Receipt')}
	""", p)[0][0]
	return {"po_count": po[0] or 0, "po_value": flt(po[1]), "pi_value": flt(pi), "pr_count": pr or 0}


# ═══════════════════════════════════════════════════════════════════════
#  MD WALL DISPLAY — get_md_dashboard (md_wall_display.js reads every key)
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_md_dashboard(date_range="This Month", start=None, end=None):
	"""MD Wall Display data. The MD Dashboard page reads get_md_overview."""
	_check_role()

	start_date, end_date = _resolve_dates(date_range, start, end)
	today_date = getdate(today())

	return {
		"company_overview": _get_company_overview(today_date, start_date, end_date),
		"gate_operations": _get_gate_operations(today_date),
		"quality": _get_quality(start_date, end_date),
		"non_branded_items": _get_non_branded_items(),
		"filters_applied": {
			"date_range": date_range,
			"start": str(start_date),
			"end": str(end_date),
		},
		"last_updated": str(now_datetime()),
	}


# ═══════════════════════════════════════════════════════════════════════
#  COMPANY OVERVIEW — the buying companies side by side
# ═══════════════════════════════════════════════════════════════════════

def _get_company_overview(today_date, start_date, end_date):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause as conf
	from trustbit_ethanol.ts_gate_entry.ts_dashboard_ceo import _ov_budget, _ov_statuses

	overview = []
	for c in _md_company_list():
		t = _md_company_totals(c.name, start_date, end_date)
		# Same figures as the TS Budget Dashboard report (expense accounts only).
		try:  # a budget failure must not blank the whole Wall on every refresh
			s = _ov_budget(c.name).get("summary") or {}
		except Exception:
			from trustbit_ethanol.ts_gate_entry.ts_exec_api import _log_once
			_log_once("md_wall:budget", "MD Wall Display budget failed", frappe.get_traceback())
			frappe.clear_messages()
			s = {}

		pending_pos = frappe.db.sql(f"""
			SELECT COUNT(*) FROM `tabPurchase Order` po
			WHERE po.docstatus = 0 AND po.company = %s
			AND po.ts_approval_status LIKE 'Pending%%' {conf('po')}
		""", c.name)[0][0] or 0

		overview.append({
			"company": c.name,
			"short_name": c.abbr or c.name,
			"po_value": t["po_value"],
			"pi_value": t["pi_value"],
			"pr_count": t["pr_count"],
			"pending_pos": pending_pos,
			"budget_total": flt(s.get("total_budget")),
			"budget_used": flt(s.get("total_committed")) + flt(s.get("total_actual")),
			"budget_pct": flt(s.get("overall_utilization")),
		})

	# The MD's own step only ("Awaiting Send to MD" is not actionable by the MD).
	md_st = _ov_statuses("Purchase Order", {"MD"})[1]
	total_pending = frappe.db.sql(f"""
		SELECT COUNT(*) FROM `tabPurchase Order` po
		WHERE po.docstatus = 0 AND po.ts_approval_status IN %(st)s {conf('po')}
	""", {"st": md_st})[0][0] or 0

	vehicles_inside = frappe.db.sql("""
		SELECT COUNT(*) FROM `tabTS Token`
		WHERE entry_type = 'Material' AND status NOT IN ('Exited', 'Campus Exited', 'Token Generated')
	""")[0][0] or 0

	today_inward = frappe.db.sql("""
		SELECT COUNT(*) FROM `tabTS Token`
		WHERE entry_date = %s AND entry_type = 'Material'
		AND (stock_direction IS NULL OR stock_direction = 'Stock IN' OR stock_direction = '')
	""", today_date)[0][0] or 0

	today_weight = frappe.db.sql("""
		SELECT COALESCE(SUM(wl.net_weight), 0) / 1000
		FROM `tabTS Weighbridge Log` wl
		JOIN `tabTS Token` t ON wl.token_number = t.name
		WHERE t.entry_date = %s AND wl.net_weight > 0
		AND (wl.stock_direction IS NULL OR wl.stock_direction = 'Stock IN' OR wl.stock_direction = '')
	""", today_date)[0][0] or 0

	avg_ta = frappe.db.sql("""
		SELECT AVG(TIMESTAMPDIFF(MINUTE, g1_entry_time, g1_exit_time))
		FROM `tabTS Token`
		WHERE entry_date = %s AND status IN ('Exited', 'Campus Exited') AND entry_type = 'Material'
		AND g1_entry_time IS NOT NULL AND g1_exit_time IS NOT NULL
	""", today_date)[0][0] or 0

	# Stuck vehicles
	settings = frappe.get_single("TS Settings")
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
		FROM `tabTS Token`
		WHERE entry_type = 'Material'
		AND status NOT IN ('Exited', 'Campus Exited', 'Plant Exited', 'Token Generated', 'GRN Created', 'Dispatch Ready')
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
#  GATE OPERATIONS — Live plant status
# ═══════════════════════════════════════════════════════════════════════

def _get_gate_operations(today_date):
	# Today's flow
	flow = frappe.db.sql("""
		SELECT
			COUNT(*) as total_entries,
			SUM(CASE WHEN status IN ('Exited', 'Campus Exited') THEN 1 ELSE 0 END) as exits,
			SUM(CASE WHEN entry_type = 'Material' AND (stock_direction IS NULL OR stock_direction = 'Stock IN' OR stock_direction = '') THEN 1 ELSE 0 END) as stock_in,
			SUM(CASE WHEN entry_type = 'Material' AND stock_direction = 'Stock OUT' THEN 1 ELSE 0 END) as stock_out,
			SUM(CASE WHEN entry_type = 'Gate Pass' THEN 1 ELSE 0 END) as visitors
		FROM `tabTS Token` WHERE entry_date = %s
	""", today_date, as_dict=True)
	f = flow[0] if flow else {}

	# Stage breakdown
	stages = frappe.db.sql("""
		SELECT status as stage, COUNT(*) as count
		FROM `tabTS Token`
		WHERE entry_type = 'Material' AND status NOT IN ('Exited', 'Campus Exited', 'Token Generated')
		GROUP BY status
		ORDER BY FIELD(status,
			'G1 Entered', 'G2 Entered',
			'PO Linked', 'Gross Weighed', 'Quality Done', 'Graded',
			'Unloading', 'Tare Weighed', 'GRN Created', 'Plant Exited',
			'SI Linked', 'Tare Recorded', 'Loading Done', 'Gross Recorded', 'Dispatch Ready')
	""", as_dict=True)

	# Weight processed today
	weight = frappe.db.sql("""
		SELECT
			ROUND(COALESCE(SUM(CASE WHEN wl.stock_direction IS NULL OR wl.stock_direction = 'Stock IN' OR wl.stock_direction = '' THEN wl.net_weight ELSE 0 END), 0) / 1000, 2) as in_mt,
			ROUND(COALESCE(SUM(CASE WHEN wl.stock_direction = 'Stock OUT' THEN wl.net_weight ELSE 0 END), 0) / 1000, 2) as out_mt
		FROM `tabTS Weighbridge Log` wl
		JOIN `tabTS Token` t ON wl.token_number = t.name
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
#  QUALITY OVERVIEW
# ═══════════════════════════════════════════════════════════════════════

def _get_quality(start_date, end_date):
	# `creation` is a datetime: compare against the day AFTER end_date, or a
	# BETWEEN on bare dates drops everything created on the end day itself
	# (the wall asks for "Today", so it always showed 0).
	end_date = add_days(end_date, 1)
	qi = frappe.db.sql("""
		SELECT
			COUNT(*) as total,
			SUM(CASE WHEN decision = 'Accepted' THEN 1 ELSE 0 END) as accepted,
			SUM(CASE WHEN decision = 'Rejected' THEN 1 ELSE 0 END) as rejected
		FROM `tabTS Quality Inspection`
		WHERE creation >= %s AND creation < %s
	""", (start_date, end_date), as_dict=True)
	q = qi[0] if qi else {}

	by_cat = frappe.db.sql("""
		SELECT item_category,
			ROUND(AVG(actual_gcv), 1) as avg_gcv,
			ROUND(AVG(actual_moisture_percent), 2) as avg_moisture,
			COUNT(*) as cnt
		FROM `tabTS Quality Inspection`
		WHERE creation >= %s AND creation < %s
		AND item_category IN ('Coal', 'Grain')
		GROUP BY item_category
	""", (start_date, end_date), as_dict=True)

	# Material inspection — use try/except in case table structure differs
	mi_data = {}
	try:
		mi = frappe.db.sql("""
			SELECT status, COUNT(*) as cnt
			FROM `tabTS Material Inspection`
			WHERE creation >= %s AND creation < %s
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
#  NON-BRANDED ITEMS — items missing Brand field
# ═══════════════════════════════════════════════════════════════════════

def _get_non_branded_items(limit=50):
	"""Enabled items with no Brand, most-purchased first. count is the true
	total, not the length of the capped list."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause as conf

	count = frappe.db.sql("""
		SELECT COUNT(*) FROM `tabItem` WHERE disabled = 0 AND IFNULL(brand, '') = ''
	""")[0][0] or 0
	items = frappe.db.sql(f"""
		SELECT i.name, i.item_name, i.item_group,
			COALESCE(po_stats.po_count, 0) as po_count,
			COALESCE(po_stats.total_qty, 0) as total_qty
		FROM `tabItem` i
		LEFT JOIN (
			SELECT poi.item_code, COUNT(DISTINCT poi.parent) as po_count,
				SUM(poi.qty) as total_qty
			FROM `tabPurchase Order Item` poi
			JOIN `tabPurchase Order` po ON poi.parent = po.name
			WHERE po.docstatus = 1 {conf('po')}
			GROUP BY poi.item_code
		) po_stats ON po_stats.item_code = i.name
		WHERE i.disabled = 0
		AND (i.brand IS NULL OR i.brand = '')
		ORDER BY po_stats.po_count DESC, i.item_name ASC
		LIMIT {int(limit)}
	""", as_dict=True)

	return {
		"count": count,
		"items": items,
	}
