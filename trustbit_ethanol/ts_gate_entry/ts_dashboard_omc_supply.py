"""OMC Ethanol Supply Tracker — read-only dashboard API (planner v6, §3).

Single whitelisted read method mirroring the house dashboard pattern
(ts_dashboard_ceo.py): role gate FIRST, one nested dict, %s-parameterised
SQL, no caching, str(now_datetime()) last_updated. Writes NOTHING — plain
@frappe.whitelist() (GET-able read; Lesson 175 = POST only for mutations).

Numerator: supplied_kl = SUM(submitted Delivery Note Item.qty) for ethanol
items, attributed to an OMC via the Customer tag `ts_omc`, within the
selected period (a quarter or the full ESY), Litres / 1000 -> KL. The
period arg is resolved against a {Q1..Q4,FY} whitelist server-side and is
NEVER interpolated into SQL — only the resolved dates are bound.
"""

import frappe
from frappe.utils import escape_html, flt, getdate, now_datetime, today, date_diff

from trustbit_ethanol.ts_gate_entry.doctype.ts_omc_supply_target.ts_omc_supply_target import (
	derive_esy_dates,
	esy_quarters,
	quarter_for_date,
)

VERSION = "omc_supply_tracker.v1"

OMC_VIEW_ROLES = ("CEO", "MD", "PM", "Grain PM", "IT Head", "System Manager")
OMC_OPS_ROLES = ("PM", "Grain PM", "IT Head", "System Manager")

VALID_PERIODS = ("Q1", "Q2", "Q3", "Q4", "FY")

# Default ethanol item group + seeded item (overridable via TS Settings, §2e/§3d).
DEFAULT_ETHANOL_ITEM_GROUP = "Products"
PACE_BAND_PP = 6.0  # amber gauge pace-band width in percentage points (Q-A default)


def _check_role():
	if not any(r in frappe.get_roles(frappe.session.user) for r in OMC_VIEW_ROLES):
		frappe.throw("Not authorized to view this dashboard", frappe.PermissionError)


def _has_ops():
	return any(r in frappe.get_roles(frappe.session.user) for r in OMC_OPS_ROLES)


# ─────────────────────────────────────────────────────────────────────────
#  Config resolution
# ─────────────────────────────────────────────────────────────────────────

def _resolve_ethanol_items():
	"""Resolve the ethanol item-code set (data-driven, Lesson 221):
	explicit ts_ethanol_items list if set, else all enabled items in
	ts_ethanol_item_group (default 'Products'). Returns (item_codes, source)."""
	settings = frappe.get_single("TS Settings")

	raw = (getattr(settings, "ts_ethanol_items", None) or "").strip()
	if raw:
		codes = [c.strip() for c in raw.replace(",", "\n").splitlines() if c.strip()]
		codes = sorted(set(codes))
		if codes:
			return codes, "explicit"

	group = (getattr(settings, "ts_ethanol_item_group", None) or DEFAULT_ETHANOL_ITEM_GROUP).strip()
	if not group:
		return [], "unconfigured"

	rows = frappe.db.sql(
		"""
		SELECT name FROM `tabItem`
		WHERE item_group = %s AND IFNULL(disabled, 0) = 0
		""",
		(group,),
	)
	codes = sorted({r[0] for r in rows})
	return codes, f"group:{group}"


def _plant_klpd():
	"""Plant rated KLPD; 0/empty means 'not set' (fail-closed — Lesson 171/227)."""
	val = frappe.db.get_single_value("TS Settings", "ts_plant_klpd")
	return flt(val)


# ─────────────────────────────────────────────────────────────────────────
#  ESY + period resolution
# ─────────────────────────────────────────────────────────────────────────

def _active_targets(esy_code):
	"""Active TS OMC Supply Target docs for the resolved ESY, ordered by the
	OMC's display_order then name."""
	return frappe.db.sql(
		"""
		SELECT t.name, t.omc, t.esy_code, t.esy_start, t.esy_end,
		       t.q1_allocation_kl, t.q2_allocation_kl, t.q3_allocation_kl,
		       t.q4_allocation_kl, t.annual_allocation_kl,
		       o.omc_code, o.logo_color, o.logo_key, o.display_order
		FROM `tabTS OMC Supply Target` t
		LEFT JOIN `tabTS OMC` o ON o.name = t.omc
		WHERE t.is_active = 1 AND t.esy_code = %s
		ORDER BY IFNULL(o.display_order, 999), t.omc
		""",
		(esy_code,),
		as_dict=True,
	)


def _esy_options():
	"""Distinct ESY codes that have active targets (for the selector)."""
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT esy_code FROM `tabTS OMC Supply Target`
		WHERE is_active = 1 AND IFNULL(esy_code, '') != ''
		ORDER BY esy_code DESC
		"""
	)
	return [r[0] for r in rows]


def _resolve_esy(esy):
	"""Resolve the ESY code to use. If given, use it; else pick the ESY whose
	window contains today, else the most recent ESY with active targets."""
	options = _esy_options()
	if esy and esy in options:
		return esy, options
	if esy:
		# An explicitly requested ESY with no targets — honour it (empty state).
		return esy, options

	t = getdate(today())
	for code in options:
		start, end = derive_esy_dates(code)
		if start and end and start <= t <= end:
			return code, options
	return (options[0] if options else None), options


def _resolve_period(period, esy_start, esy_end):
	"""Resolve a period (Q1..Q4/FY) to (period_code, period_start, period_end).
	A bogus value defaults to the current quarter (today within the ESY) or FY.
	The arg is validated against VALID_PERIODS and NEVER goes into SQL."""
	quarters = esy_quarters(esy_start)
	qmap = {code: (start, end) for code, _label, start, end in quarters}

	p = (period or "").strip().upper() if period else ""
	if p not in VALID_PERIODS:
		p = ""  # invalid -> fall through to current-quarter default

	if p in qmap:
		start, end = qmap[p]
		return p, start, end
	if p == "FY":
		return "FY", getdate(esy_start), getdate(esy_end)

	# default = current quarter by today within the ESY, else FY
	t = getdate(today())
	cur = quarter_for_date(t, esy_start)
	if cur and cur in qmap:
		start, end = qmap[cur]
		return cur, start, end
	return "FY", getdate(esy_start), getdate(esy_end)


def _to_kl(qty, uom):
	"""Convert a DN line qty to KL. Litre -> /1000; KL -> as-is. Any other
	UOM is unit-uncertain: skipped + flagged (returns (0, True))."""
	u = (uom or "").strip().lower()
	if u in ("litre", "litres", "liter", "liters", "l"):
		return flt(qty) / 1000.0, False
	if u in ("kl", "kilolitre", "kilolitres", "kiloliter", "kiloliters"):
		return flt(qty), False
	return 0.0, True  # unit-uncertain — skip + flag


# ─────────────────────────────────────────────────────────────────────────
#  Aggregation (per OMC, per quarter, per depot) — over the full ESY window
#  in ONE query each, then bucketed in Python (no per-quarter round-trips).
# ─────────────────────────────────────────────────────────────────────────

def _supplied_rows(omc, esy_start, esy_end, ethanol_items):
	"""All submitted ethanol DN lines for one OMC over the full ESY window,
	with posting_date + customer + uom + qty. Bucketed in Python."""
	return frappe.db.sql(
		"""
		SELECT dn.posting_date AS posting_date, dn.customer AS customer,
		       dni.uom AS uom, dni.qty AS qty
		FROM `tabDelivery Note Item` dni
		JOIN `tabDelivery Note` dn ON dn.name = dni.parent
		JOIN `tabCustomer` c ON c.name = dn.customer
		WHERE dn.docstatus = 1
		  AND c.ts_omc = %(omc)s
		  AND dn.posting_date BETWEEN %(start)s AND %(end)s
		  AND dni.item_code IN %(items)s
		""",
		{"omc": omc, "start": esy_start, "end": esy_end, "items": tuple(ethanol_items)},
		as_dict=True,
	)


def _bucket_supplied(rows, esy_start, period_start, period_end):
	"""From the ESY-wide rows, compute:
	  - period_supplied_kl (within period window)
	  - per_quarter supplied {Q1..Q4: kl}  (always all four)
	  - per_depot period supplied {customer: kl}
	  - unit_uncertain flag
	"""
	quarters = {code: 0.0 for code, _l, _s, _e in esy_quarters(esy_start)}
	period_total = 0.0
	per_depot = {}
	unit_uncertain = False

	ps, pe = getdate(period_start), getdate(period_end)
	for r in rows:
		kl, uncertain = _to_kl(r.qty, r.uom)
		if uncertain:
			unit_uncertain = True
			continue
		pdate = getdate(r.posting_date)

		q = quarter_for_date(pdate, esy_start)
		if q in quarters:
			quarters[q] += kl

		if ps <= pdate <= pe:
			period_total += kl
			per_depot[r.customer] = per_depot.get(r.customer, 0.0) + kl

	return period_total, quarters, per_depot, unit_uncertain


def _planned_by_depot(target_name, period_code):
	"""Planned KL per depot for the selected period: plan rows whose quarter
	matches the selected quarter (ALL rows for FY)."""
	if period_code == "FY":
		rows = frappe.db.sql(
			"""
			SELECT depot_customer, COALESCE(SUM(planned_kl), 0) AS planned
			FROM `tabTS OMC Depot Plan`
			WHERE parent = %s
			GROUP BY depot_customer
			""",
			(target_name,),
			as_dict=True,
		)
	else:
		rows = frappe.db.sql(
			"""
			SELECT depot_customer, COALESCE(SUM(planned_kl), 0) AS planned
			FROM `tabTS OMC Depot Plan`
			WHERE parent = %s AND quarter = %s
			GROUP BY depot_customer
			""",
			(target_name, period_code),
			as_dict=True,
		)
	return {r.depot_customer: flt(r.planned) for r in rows}


# ─────────────────────────────────────────────────────────────────────────
#  Gauge math (within the selected period) — pure arithmetic, guarded
# ─────────────────────────────────────────────────────────────────────────

def _gauge(allocation, supplied, period_start, period_end):
	"""Compute pacing for one card within a period window. Returns a dict of
	gauge fields. All divisions guarded."""
	A = flt(allocation)
	S = flt(supplied)
	t = getdate(today())
	ps, pe = getdate(period_start), getdate(period_end)

	total_days = date_diff(pe, ps) + 1  # inclusive
	total_days = max(total_days, 1)

	if t < ps:
		day_index = 0  # period not started
	elif t > pe:
		day_index = total_days  # period complete
	else:
		day_index = date_diff(t, ps) + 1  # incl today
	day_index = max(0, min(day_index, total_days))

	days_left = max(total_days - day_index + 1, 0) if day_index else total_days

	pct = (S / A * 100.0) if A > 0 else 0.0
	prorata_pct = (day_index / total_days * 100.0) if total_days else 0.0
	remaining_kl = max(A - S, 0.0)

	if day_index > 0:
		projected_kl = S + (S / max(day_index, 1)) * days_left
	else:
		projected_kl = 0.0  # not started — no run-rate
	projected_pct = (projected_kl / A * 100.0) if A > 0 else 0.0

	catchup_kl_day = (remaining_kl / max(days_left, 1)) if days_left else remaining_kl
	cum_variance_kl = S - (prorata_pct / 100.0 * A)

	# status zones keyed to period pace
	red_end = max(prorata_pct - PACE_BAND_PP, 0.0)
	amber_end = prorata_pct
	if pct >= amber_end:
		status, status_txt = "green", "On Track"
	elif pct >= red_end:
		status, status_txt = "amber", "Slightly Behind"
	else:
		status, status_txt = "red", "Behind"

	return {
		"allocation_kl": round(A, 3),
		"supplied_kl": round(S, 3),
		"remaining_kl": round(remaining_kl, 3),
		"pct": round(pct, 1),
		"prorata_pct": round(prorata_pct, 1),
		"projected_pct": round(projected_pct, 1),
		"catchup_kl_day": round(catchup_kl_day, 3),
		"cum_variance_kl": round(cum_variance_kl, 3),
		"status": status,
		"status_txt": status_txt,
		"redEnd": round(red_end, 1),
		"amberEnd": round(amber_end, 1),
		"day_index": day_index,
		"total_days": total_days,
		"days_left": days_left,
	}


def _placeholder_daily():
	"""Daily Target & Catch-Up fields — data wiring PENDING user finalization
	(§3f). Safe placeholders so the card renders."""
	return {
		"daily_target_kl": None,
		"actual_today_kl": None,
		"variance_today_kl": None,
	}


# ─────────────────────────────────────────────────────────────────────────
#  Main endpoint
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_omc_supply_tracker(esy=None, period=None):
	"""Return the full OMC Supply Tracker payload (read-only).

	period in {Q1,Q2,Q3,Q4,FY}; default = current quarter within the ESY.
	"""
	_check_role()

	esy_code, esy_options = _resolve_esy(esy)
	plant_klpd = _plant_klpd()
	last_updated = str(now_datetime())

	# ── targets_set=false safe empty state (selectors still render) ──────────
	targets = _active_targets(esy_code) if esy_code else []
	if not esy_code or not targets:
		# Build esy.quarters for the selector even when empty, if we can derive.
		esy_block = _empty_esy_block(esy_code, period)
		return {
			"version": VERSION,
			"esy": esy_block,
			"esy_options": esy_options,
			"period_selected": esy_block["period_selected"],
			"targets_set": False,
			"plant_klpd": plant_klpd,
			"omcs": [],
			"plant_today": _placeholder_daily(),
			"verdict": {"level": "none", "label": "Targets not set"},
			"risk": None,
			"ops": None,
			"trend": {"available": False},
			"last_updated": last_updated,
		}

	esy_start, esy_end = derive_esy_dates(esy_code)
	# If the code couldn't be parsed, fall back to the first target's window.
	if not esy_start or not esy_end:
		esy_start = getdate(targets[0].esy_start)
		esy_end = getdate(targets[0].esy_end)

	period_code, period_start, period_end = _resolve_period(period, esy_start, esy_end)
	ethanol_items, item_source = _resolve_ethanol_items()

	# Quarter metadata block (selector + chart frame)
	quarters_meta = esy_quarters(esy_start)
	current_q = quarter_for_date(getdate(today()), esy_start)
	q_alloc_total = {"Q1": 0.0, "Q2": 0.0, "Q3": 0.0, "Q4": 0.0}
	for tgt in targets:
		q_alloc_total["Q1"] += flt(tgt.q1_allocation_kl)
		q_alloc_total["Q2"] += flt(tgt.q2_allocation_kl)
		q_alloc_total["Q3"] += flt(tgt.q3_allocation_kl)
		q_alloc_total["Q4"] += flt(tgt.q4_allocation_kl)

	esy_quarters_out = [
		{
			"code": code,
			"label": label,
			"start": str(q_start),
			"end": str(q_end),
			"allocation": round(q_alloc_total.get(code, 0.0), 3),
			"is_current": (code == current_q),
		}
		for code, label, q_start, q_end in quarters_meta
	]

	g = _gauge_window_meta(period_start, period_end)

	# ── per-OMC build ────────────────────────────────────────────────────────
	omcs = []
	any_unit_uncertain = False
	if not ethanol_items:
		# nothing to aggregate — produce zeroed gauges + a warning flag
		ethanol_items = ["__none__"]
		item_source = "unconfigured"

	# Plant-Total accumulators
	pt_period_supplied = 0.0
	pt_quarters = {"Q1": 0.0, "Q2": 0.0, "Q3": 0.0, "Q4": 0.0}
	pt_period_alloc = 0.0
	pt_q_alloc = dict(q_alloc_total)

	for tgt in targets:
		rows = _supplied_rows(tgt.omc, esy_start, esy_end, ethanol_items)
		period_supplied, q_supplied, per_depot, unit_uncertain = _bucket_supplied(
			rows, esy_start, period_start, period_end
		)
		any_unit_uncertain = any_unit_uncertain or unit_uncertain

		period_alloc = _period_allocation(tgt, period_code)
		gauge = _gauge(period_alloc, period_supplied, period_start, period_end)

		# per_quarter[] — always all four (chart)
		per_quarter = []
		for code in ("Q1", "Q2", "Q3", "Q4"):
			q_alloc = flt(getattr(tgt, f"{code.lower()}_allocation_kl"))
			q_sup = q_supplied.get(code, 0.0)
			per_quarter.append({
				"q": code,
				"allocation_kl": round(q_alloc, 3),
				"supplied_kl": round(q_sup, 3),
				"pct": round((q_sup / q_alloc * 100.0), 1) if q_alloc > 0 else None,
				"is_current": (code == current_q),
			})

		# depot drill-down (period-scoped) + Unplanned bucket
		planned = _planned_by_depot(tgt.name, period_code)
		depots, unplanned = _build_depots(per_depot, planned)

		omc_card = {
			"code": tgt.omc_code or (tgt.omc[:3].upper() if tgt.omc else ""),
			"key": (tgt.logo_key or (tgt.omc or "").lower().replace(" ", "_")),
			"name": escape_html(tgt.omc or ""),
			"full": escape_html(tgt.omc or ""),
			"logo_cls": escape_html(tgt.logo_key or ""),
			"per_quarter": per_quarter,
			"depots": depots,
			"depots_unplanned": unplanned,
			"infeasible": None,  # PENDING (needs KLPD-derived capacity wiring)
		}
		omc_card.update(gauge)
		omc_card.update(_placeholder_daily())
		omcs.append(omc_card)

		# accumulate Plant-Total
		pt_period_supplied += period_supplied
		for code in pt_quarters:
			pt_quarters[code] += q_supplied.get(code, 0.0)
		pt_period_alloc += period_alloc

	# ── Plant-Total card (permanent, automatic, period-scoped) ───────────────
	pt_gauge = _gauge(pt_period_alloc, pt_period_supplied, period_start, period_end)
	pt_per_quarter = [
		{
			"q": code,
			"allocation_kl": round(pt_q_alloc.get(code, 0.0), 3),
			"supplied_kl": round(pt_quarters.get(code, 0.0), 3),
			"pct": round((pt_quarters[code] / pt_q_alloc[code] * 100.0), 1)
			if pt_q_alloc.get(code, 0.0) > 0 else None,
			"is_current": (code == current_q),
		}
		for code in ("Q1", "Q2", "Q3", "Q4")
	]
	plant_total = {
		"code": "ALL",
		"key": "total",
		"name": "Plant Total",
		"full": "Plant Total",
		"logo_cls": "total",
		"per_quarter": pt_per_quarter,
		"depots": [],
		"depots_unplanned": {"supplied_kl": 0.0, "count": 0},
		"infeasible": None,
	}
	plant_total.update(pt_gauge)
	plant_total.update(_placeholder_daily())
	omcs.append(plant_total)

	verdict = _verdict(omcs)

	return {
		"version": VERSION,
		"esy": {
			"code": esy_code,
			"label": f"ESY {esy_code}",
			"start": str(esy_start),
			"end": str(esy_end),
			"day_index": g["day_index"],
			"total_days": g["total_days"],
			"days_left": g["days_left"],
			"prorata_pct": g["prorata_pct"],
			"quarters": esy_quarters_out,
			"period_selected": period_code,
		},
		"esy_options": esy_options,
		"period_selected": period_code,
		"targets_set": True,
		"plant_klpd": plant_klpd,
		"omcs": omcs,
		"plant_today": _placeholder_daily(),
		"verdict": verdict,
		"risk": None,  # PENDING user finalization (§3f)
		"ops": _ops_block(item_source, ethanol_items, any_unit_uncertain) if _has_ops() else None,
		"trend": {"available": False},  # PENDING
		"unit_uncertain": any_unit_uncertain,
		"ethanol_configured": item_source != "unconfigured",
		"klpd_set": plant_klpd > 0,
		"last_updated": last_updated,
	}


# ─────────────────────────────────────────────────────────────────────────
#  Helpers used by the main endpoint
# ─────────────────────────────────────────────────────────────────────────

def _period_allocation(tgt, period_code):
	if period_code == "FY":
		return flt(tgt.annual_allocation_kl)
	return flt(getattr(tgt, f"{period_code.lower()}_allocation_kl"))


def _gauge_window_meta(period_start, period_end):
	"""Reuse _gauge's window arithmetic (allocation/supplied irrelevant here)
	to derive day_index/total_days/days_left/prorata for the ESY hero block."""
	return _gauge(0, 0, period_start, period_end)


def _build_depots(per_depot_supplied, planned):
	"""Build the depots[] drill-down + the Unplanned bucket, period-scoped.
	Reconciliation: OMC supplied == Σ planned-depot supplied + unplanned."""
	depots = []
	unplanned_kl = 0.0
	unplanned_count = 0

	planned_set = set(planned.keys())
	# planned depots first (even if 0 supplied)
	for cust, planned_kl in planned.items():
		supplied = flt(per_depot_supplied.get(cust, 0.0))
		depots.append({
			"customer": cust,
			"depot_label": escape_html(cust or ""),
			"planned_kl": round(flt(planned_kl), 3),
			"supplied_kl": round(supplied, 3),
			"pct": round((supplied / flt(planned_kl) * 100.0), 1) if flt(planned_kl) > 0 else None,
		})

	# supplied to depots NOT in the period plan -> Unplanned bucket
	for cust, supplied in per_depot_supplied.items():
		if cust in planned_set:
			continue
		unplanned_kl += flt(supplied)
		unplanned_count += 1

	depots.sort(key=lambda d: d["supplied_kl"], reverse=True)
	return depots, {"supplied_kl": round(unplanned_kl, 3), "count": unplanned_count}


def _verdict(omcs):
	"""AT RISK if any OMC projected_pct < 100 - tol; ACTION if any amber;
	ON TRACK otherwise. Plant-Total excluded from the at-risk scan
	(it is a display sum, never netted)."""
	tol = 5.0
	at_risk = False
	action = False
	for o in omcs:
		if o.get("key") == "total":
			continue
		if flt(o.get("allocation_kl")) > 0 and flt(o.get("projected_pct")) < (100.0 - tol):
			at_risk = True
		if o.get("status") == "amber":
			action = True
	if at_risk:
		return {"level": "at_risk", "label": "At Risk"}
	if action:
		return {"level": "action", "label": "Action Needed"}
	return {"level": "on_track", "label": "On Track"}


def _ops_block(item_source, ethanol_items, unit_uncertain):
	"""Ops-only diagnostics (present for ops roles; null for CEO/MD). Most
	operational metrics (yield, stock-cover, run-rate) are PENDING user
	finalization (§3f); this surfaces the resolved config for transparency."""
	items = [c for c in ethanol_items if c != "__none__"]
	return {
		"ethanol_item_source": escape_html(item_source),
		"ethanol_items": [escape_html(c) for c in items],
		"ethanol_item_count": len(items),
		"unit_uncertain": unit_uncertain,
		"capacity_util_pct": None,
		"yield_l_per_t": None,
		"stock_cover_days": None,
		"cum_shortfall_kl": None,
		"run_rate_rows": [],
	}


def _empty_esy_block(esy_code, period):
	"""Build the esy block for the targets_set=false path so the period +
	quarter selectors + chart frame still render."""
	esy_start, esy_end = derive_esy_dates(esy_code) if esy_code else (None, None)
	if not esy_start:
		# nothing to derive — return a minimal shell
		return {
			"code": esy_code,
			"label": f"ESY {esy_code}" if esy_code else "",
			"start": None,
			"end": None,
			"day_index": 0,
			"total_days": 0,
			"days_left": 0,
			"prorata_pct": 0.0,
			"quarters": [],
			"period_selected": (period if period in VALID_PERIODS else "FY"),
		}

	period_code, period_start, period_end = _resolve_period(period, esy_start, esy_end)
	current_q = quarter_for_date(getdate(today()), esy_start)
	quarters = [
		{
			"code": code,
			"label": label,
			"start": str(q_start),
			"end": str(q_end),
			"allocation": 0.0,
			"is_current": (code == current_q),
		}
		for code, label, q_start, q_end in esy_quarters(esy_start)
	]
	g = _gauge_window_meta(period_start, period_end)
	return {
		"code": esy_code,
		"label": f"ESY {esy_code}",
		"start": str(esy_start),
		"end": str(esy_end),
		"day_index": g["day_index"],
		"total_days": g["total_days"],
		"days_left": g["days_left"],
		"prorata_pct": g["prorata_pct"],
		"quarters": quarters,
		"period_selected": period_code,
	}
