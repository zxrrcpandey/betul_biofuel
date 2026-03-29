import frappe
from frappe.utils import getdate, today, now_datetime, time_diff_in_seconds, flt

GATE_ROLES = ("G1 Security", "G2 Gate Operator", "IT Head", "System Manager", "CEO", "MD")


def _check_role():
	user_roles = frappe.get_roles(frappe.session.user)
	if not any(r in user_roles for r in GATE_ROLES):
		frappe.throw("Not authorized to view this dashboard", frappe.PermissionError)


@frappe.whitelist()
def get_gate_dashboard(date=None):
	"""Return all gate operations dashboard data in a single call."""
	_check_role()

	target_date = getdate(date) if date else getdate(today())

	return {
		"vehicles_inside": _get_vehicles_inside(),
		"today_summary": _get_today_summary(target_date),
		"avg_turnaround": _get_avg_turnaround(target_date),
		"stage_pending": _get_stage_pending(),
		"hourly_distribution": _get_hourly_distribution(target_date),
		"sla_breaches": _get_sla_breaches(),
		"gate_passes": _get_gate_passes(),
		"recent_activity": _get_recent_activity(target_date),
		"last_updated": str(now_datetime()),
	}


def _get_vehicles_inside():
	"""Vehicles currently inside campus by stage."""
	rows = frappe.db.sql("""
		SELECT status, COUNT(*) as cnt
		FROM `tabTS Token`
		WHERE entry_type = 'Material'
		AND status NOT IN ('Exited', 'Token Generated')
		GROUP BY status
		ORDER BY FIELD(status,
			'PO Linked', 'Gross Weighed', 'Quality Done', 'Graded',
			'Unloading', 'Tare Weighed', 'GRN Created',
			'SI Linked', 'Tare Recorded', 'Loading Done', 'Gross Recorded', 'Dispatch Ready'
		)
	""", as_dict=True)

	total = sum(r.cnt for r in rows)

	# Get token details for each stage
	by_stage = []
	for r in rows:
		tokens = frappe.db.sql("""
			SELECT name, token_number, vehicle_number, g1_entry_time, stock_direction
			FROM `tabTS Token`
			WHERE entry_type = 'Material' AND status = %s
			ORDER BY g1_entry_time
			LIMIT 10
		""", r.status, as_dict=True)
		by_stage.append({
			"stage": r.status,
			"count": r.cnt,
			"tokens": tokens,
		})

	return {"total": total, "by_stage": by_stage}


def _get_today_summary(target_date):
	"""Entry/exit counts for the day."""
	row = frappe.db.sql("""
		SELECT
			COUNT(*) as entries,
			SUM(CASE WHEN status = 'Exited' THEN 1 ELSE 0 END) as exits,
			SUM(CASE WHEN entry_type = 'Material' AND (stock_direction IS NULL OR stock_direction = 'Stock IN' OR stock_direction = '') THEN 1 ELSE 0 END) as stock_in,
			SUM(CASE WHEN entry_type = 'Material' AND stock_direction = 'Stock OUT' THEN 1 ELSE 0 END) as stock_out,
			SUM(CASE WHEN entry_type = 'Gate Pass' THEN 1 ELSE 0 END) as gate_pass
		FROM `tabTS Token`
		WHERE entry_date = %s
	""", target_date, as_dict=True)

	r = row[0] if row else {}
	return {
		"entries": r.get("entries") or 0,
		"exits": r.get("exits") or 0,
		"stock_in": r.get("stock_in") or 0,
		"stock_out": r.get("stock_out") or 0,
		"gate_pass": r.get("gate_pass") or 0,
	}


def _get_avg_turnaround(target_date):
	"""Average turnaround time for exited vehicles today."""
	row = frappe.db.sql("""
		SELECT AVG(TIMESTAMPDIFF(MINUTE, g1_entry_time, g1_exit_time)) as avg_min
		FROM `tabTS Token`
		WHERE entry_date = %s
		AND status = 'Exited'
		AND entry_type = 'Material'
		AND g1_entry_time IS NOT NULL
		AND g1_exit_time IS NOT NULL
	""", target_date, as_dict=True)

	return round(flt(row[0].avg_min if row else 0), 0)


def _get_stage_pending():
	"""How many vehicles are waiting at each processing stage with avg wait time."""
	now = now_datetime()

	rows = frappe.db.sql("""
		SELECT
			status,
			COUNT(*) as cnt,
			AVG(TIMESTAMPDIFF(MINUTE,
				COALESCE(
					CASE status
						WHEN 'PO Linked' THEN g2_link_time
						WHEN 'Gross Weighed' THEN wb_gross_time
						WHEN 'Quality Done' THEN quality_time
						WHEN 'Graded' THEN grading_time
						WHEN 'Unloading' THEN unload_start_time
						WHEN 'Tare Weighed' THEN wb_tare_time
						WHEN 'SI Linked' THEN g2_link_time
						WHEN 'Tare Recorded' THEN wb_tare_time
						WHEN 'Loading Done' THEN unload_end_time
						WHEN 'Gross Recorded' THEN wb_gross_time
					END,
					g1_entry_time
				),
				%s
			)) as avg_wait_min
		FROM `tabTS Token`
		WHERE entry_type = 'Material'
		AND status NOT IN ('Exited', 'Token Generated', 'GRN Created', 'Dispatch Ready')
		GROUP BY status
		ORDER BY cnt DESC
	""", now, as_dict=True)

	return [{"stage": r.status, "count": r.cnt, "avg_wait_min": round(flt(r.avg_wait_min), 0)} for r in rows]


def _get_hourly_distribution(target_date):
	"""Vehicle entries by hour for the day."""
	rows = frappe.db.sql("""
		SELECT HOUR(g1_entry_time) as hour, COUNT(*) as cnt
		FROM `tabTS Token`
		WHERE entry_date = %s
		AND g1_entry_time IS NOT NULL
		AND entry_type = 'Material'
		GROUP BY HOUR(g1_entry_time)
		ORDER BY hour
	""", target_date, as_dict=True)

	# Fill all 24 hours
	hours = {r.hour: r.cnt for r in rows}
	return [{"hour": h, "count": hours.get(h, 0)} for h in range(6, 22)]  # 6 AM to 10 PM


def _get_sla_breaches():
	"""Vehicles exceeding CTL (Committed Time Limit) threshold — stuck vehicles."""
	settings = frappe.get_single("TS Settings")
	threshold = settings.sla_threshold_minutes or 30
	now = now_datetime()

	tokens = frappe.db.sql("""
		SELECT name, token_number, status, vehicle_number, g1_entry_time,
			g2_link_time, wb_gross_time, quality_time, grading_time,
			unload_start_time, wb_tare_time
		FROM `tabTS Token`
		WHERE entry_type = 'Material'
		AND status NOT IN ('Exited', 'Token Generated')
	""", as_dict=True)

	breaches = []
	for t in tokens:
		last_ts = _get_last_timestamp(t)
		if last_ts:
			minutes = time_diff_in_seconds(now, last_ts) / 60
			if minutes > threshold:
				breaches.append({
					"token": t.token_number,
					"token_name": t.name,
					"status": t.status,
					"vehicle": t.vehicle_number or "",
					"minutes": round(minutes, 0),
				})

	breaches.sort(key=lambda b: b["minutes"], reverse=True)
	return {"threshold": threshold, "count": len(breaches), "items": breaches[:20]}


def _get_last_timestamp(token):
	"""Get the timestamp when token entered current status."""
	timestamp_map = {
		"PO Linked": token.g2_link_time,
		"Gross Weighed": token.wb_gross_time,
		"Quality Done": token.quality_time,
		"Graded": token.grading_time,
		"Unloading": token.unload_start_time,
		"Tare Weighed": token.wb_tare_time,
		"GRN Created": token.get("grn_time"),
		"SI Linked": token.g2_link_time,
		"Tare Recorded": token.get("wb_tare_time"),
		"Gross Recorded": token.wb_gross_time,
		"Dispatch Ready": token.wb_gross_time,
	}
	return timestamp_map.get(token.status)


def _get_gate_passes():
	"""Gate pass visitors currently inside."""
	rows = frappe.db.sql("""
		SELECT gate_pass_status, COUNT(*) as cnt
		FROM `tabTS Token`
		WHERE entry_type = 'Gate Pass'
		AND gate_pass_status IN ('Inside Campus', 'Inside Plant')
		GROUP BY gate_pass_status
	""", as_dict=True)

	result = {"inside_campus": 0, "inside_plant": 0}
	for r in rows:
		if r.gate_pass_status == "Inside Campus":
			result["inside_campus"] = r.cnt
		elif r.gate_pass_status == "Inside Plant":
			result["inside_plant"] = r.cnt
	result["total"] = result["inside_campus"] + result["inside_plant"]
	return result


def _get_recent_activity(target_date):
	"""Last 15 token events today."""
	return frappe.db.sql("""
		SELECT name, token_number, status, vehicle_number, entry_type,
			g1_entry_time, g1_exit_time, stock_direction
		FROM `tabTS Token`
		WHERE entry_date = %s
		ORDER BY modified DESC
		LIMIT 15
	""", target_date, as_dict=True)
