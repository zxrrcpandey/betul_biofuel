"""QC Yearly Dashboard API — v2.9.0 Day 6.

Read-only whitelisted endpoints for /app/qc-yearly-dashboard. All GET (no
methods=["POST"] needed since no mutations — Lesson 175). 1-hour cache via
frappe.cache().

Permission gate: System Manager OR Accounts Manager OR Grain Manager OR
IT Head OR CEO OR MD. Enforced server-side; the role list mirrors the page
JSON.
"""

import frappe
from frappe.utils import flt
from datetime import date


CACHE_TTL_SECONDS = 60 * 60  # 1 hour
ALLOWED_ROLES = {
	"System Manager",
	"IT Head",
	"Accounts Manager",
	"Grain Manager",
	"CEO",
	"MD",
}

MONTH_NAMES = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]
# Indian fiscal year layout — month index in tuple corresponds to (year_offset, month_number).
# Apr is FY-month-1 in same calendar year; Jan-Mar are in calendar year+1.
FY_MONTHS = [
	(0, 4), (0, 5), (0, 6), (0, 7), (0, 8), (0, 9),
	(0, 10), (0, 11), (0, 12), (1, 1), (1, 2), (1, 3),
]


def _check_perm() -> None:
	"""Raise PermissionError unless caller has at least one allowed role."""
	user_roles = set(frappe.get_roles(frappe.session.user))
	if not (user_roles & ALLOWED_ROLES):
		frappe.throw(
			"You don't have access to QC Yearly Dashboard.",
			frappe.PermissionError,
		)


def _fy_bounds(year: int) -> tuple[str, str]:
	"""Return (fy_start_str, fy_end_str) for the given Indian FY start year."""
	year = int(year)
	return (f"{year}-04-01", f"{year + 1}-03-31")


def _cache_key(prefix: str, year: int, extra: str = "") -> str:
	return f"qc_dash:v1:{prefix}:{year}:{extra}"


def _get_or_set(key: str, no_cache: bool, builder):
	"""Cache helper: 1-hour TTL via frappe.cache."""
	if not no_cache:
		hit = frappe.cache().get_value(key)
		if hit is not None:
			return hit
	val = builder()
	try:
		frappe.cache().set_value(key, val, expires_in_sec=CACHE_TTL_SECONDS)
	except Exception:
		# Cache failure is non-fatal — return computed value anyway.
		pass
	return val


@frappe.whitelist()
def get_yearly_kpis(year=None, no_cache=0):
	"""4 hero KPIs: total_inspections, total_deduction_kg, avg_deduction_pct, variance_pct."""
	_check_perm()
	year = int(year or date.today().year)
	no_cache = int(no_cache or 0)
	key = _cache_key("kpis", year)

	def build():
		fy_from, fy_to = _fy_bounds(year)
		# Total inspections (submitted)
		total_qi = frappe.db.sql(
			"""
			SELECT COUNT(*) AS c, COALESCE(SUM(total_deduction_kg),0) AS total_kg,
			       COALESCE(AVG(total_deduction_pct),0) AS avg_pct
			FROM `tabTS Quality Inspection`
			WHERE docstatus = 1
			  AND inspection_date BETWEEN %s AND %s
			""",
			(fy_from, fy_to),
			as_dict=True,
		)
		row = total_qi[0] if total_qi else {}

		# Variance: average over DS records of (actual_deduction_pct - system_deduction_pct).
		ds = frappe.db.sql(
			"""
			SELECT COALESCE(AVG(actual_deduction_pct),0) AS actual_avg,
			       COALESCE(AVG(system_deduction_pct),0) AS system_avg,
			       COUNT(*) AS c
			FROM `tabTS Deduction Sheet`
			WHERE docstatus = 1
			  AND DATE(creation) BETWEEN %s AND %s
			""",
			(fy_from, fy_to),
			as_dict=True,
		)
		ds_row = ds[0] if ds else {}
		variance = None
		if ds_row.get("c"):
			variance = flt(ds_row.get("actual_avg") or 0) - flt(ds_row.get("system_avg") or 0)

		return {
			"year": year,
			"total_inspections": int(row.get("c") or 0),
			"total_deduction_kg": flt(row.get("total_kg") or 0, 3),
			"avg_deduction_pct": flt(row.get("avg_pct") or 0, 3),
			"variance_pct": flt(variance, 3) if variance is not None else None,
		}

	return _get_or_set(key, no_cache, build)


@frappe.whitelist()
def get_monthly_breakdown(year=None, no_cache=0):
	"""12 rows — one per FY month (Apr → Mar)."""
	_check_perm()
	year = int(year or date.today().year)
	no_cache = int(no_cache or 0)
	key = _cache_key("monthly", year)

	def build():
		# QI data per month
		qi_rows = frappe.db.sql(
			"""
			SELECT YEAR(inspection_date) AS y, MONTH(inspection_date) AS m,
			       COUNT(*) AS inspections,
			       COALESCE(AVG(total_deduction_pct),0) AS system_avg_pct,
			       COALESCE(SUM(total_deduction_kg),0) AS total_kg
			FROM `tabTS Quality Inspection`
			WHERE docstatus = 1
			  AND inspection_date BETWEEN %s AND %s
			GROUP BY YEAR(inspection_date), MONTH(inspection_date)
			""",
			_fy_bounds(year),
			as_dict=True,
		)
		qi_idx = {(r.y, r.m): r for r in qi_rows}

		# DS data per month (Tilok = actual_deduction_pct)
		ds_rows = frappe.db.sql(
			"""
			SELECT YEAR(creation) AS y, MONTH(creation) AS m,
			       COALESCE(AVG(actual_deduction_pct),0) AS tilok_avg_pct,
			       COALESCE(AVG(system_deduction_pct),0) AS system_avg_pct
			FROM `tabTS Deduction Sheet`
			WHERE docstatus = 1
			  AND DATE(creation) BETWEEN %s AND %s
			GROUP BY YEAR(creation), MONTH(creation)
			""",
			_fy_bounds(year),
			as_dict=True,
		)
		ds_idx = {(r.y, r.m): r for r in ds_rows}

		out = []
		for i, (year_off, month_num) in enumerate(FY_MONTHS):
			cal_year = year + year_off
			qi = qi_idx.get((cal_year, month_num))
			ds = ds_idx.get((cal_year, month_num))
			tilok = flt(ds.tilok_avg_pct, 3) if ds else None
			sysavg = flt(qi.system_avg_pct, 3) if qi else None
			variance = None
			if tilok is not None and sysavg is not None:
				variance = flt(tilok - sysavg, 3)
			out.append({
				"month_label": f"{MONTH_NAMES[i]} {str(cal_year)[-2:]}",
				"inspections": int(qi.inspections) if qi else 0,
				"system_avg_pct": sysavg,
				"tilok_avg_pct": tilok,
				"variance_pct": variance,
				"total_kg": flt(qi.total_kg, 3) if qi else None,
			})
		return out

	return _get_or_set(key, no_cache, build)


@frappe.whitelist()
def get_top_suppliers(year=None, limit=5, no_cache=0):
	"""Top N suppliers by total deduction Kg in selected FY."""
	_check_perm()
	year = int(year or date.today().year)
	limit = max(1, min(int(limit or 5), 50))
	no_cache = int(no_cache or 0)
	key = _cache_key("top_suppliers", year, str(limit))

	def build():
		from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause
		fy_from, fy_to = _fy_bounds(year)
		# Confidentiality leak-plug: hide maize-confidential POs (and thus their
		# suppliers) from non-allowed roles (e.g. Grain Manager).
		conf_po = confidential_sql_clause("po")
		# JOIN QI -> Purchase Order -> supplier; if PO missing, fallback to GE.
		rows = frappe.db.sql(
			f"""
			SELECT
			    po.supplier AS supplier,
			    s.supplier_name AS supplier_name,
			    COUNT(qi.name) AS inspections,
			    COALESCE(AVG(qi.total_deduction_pct),0) AS avg_deduction_pct,
			    COALESCE(SUM(qi.total_deduction_kg),0) AS total_kg
			FROM `tabTS Quality Inspection` qi
			LEFT JOIN `tabPurchase Order` po ON po.name = qi.purchase_order
			LEFT JOIN `tabSupplier` s ON s.name = po.supplier
			WHERE qi.docstatus = 1
			  AND qi.inspection_date BETWEEN %s AND %s
			  AND po.supplier IS NOT NULL
			  {conf_po}
			GROUP BY po.supplier, s.supplier_name
			ORDER BY total_kg DESC
			LIMIT %s
			""",
			(fy_from, fy_to, limit),
			as_dict=True,
		)
		return [
			{
				"supplier": r.supplier,
				"supplier_name": r.supplier_name or r.supplier,
				"inspections": int(r.inspections or 0),
				"avg_deduction_pct": flt(r.avg_deduction_pct, 3),
				"total_kg": flt(r.total_kg, 3),
			}
			for r in rows
		]

	return _get_or_set(key, no_cache, build)


@frappe.whitelist()
def get_insights(year=None, no_cache=0):
	"""Plain-text insight bullets derived from the year's data."""
	_check_perm()
	year = int(year or date.today().year)
	no_cache = int(no_cache or 0)
	key = _cache_key("insights", year)

	def build():
		fy_from, fy_to = _fy_bounds(year)
		insights = []

		# Insight 1: variance
		ds = frappe.db.sql(
			"""
			SELECT COALESCE(AVG(actual_deduction_pct),0) AS actual_avg,
			       COALESCE(AVG(system_deduction_pct),0) AS system_avg,
			       COUNT(*) AS c
			FROM `tabTS Deduction Sheet`
			WHERE docstatus = 1
			  AND DATE(creation) BETWEEN %s AND %s
			""",
			(fy_from, fy_to),
			as_dict=True,
		)
		if ds and ds[0].c:
			actual = flt(ds[0].actual_avg)
			sysavg = flt(ds[0].system_avg)
			delta = actual - sysavg
			if abs(delta) >= 0.1:
				direction = "lower" if delta < 0 else "higher"
				insights.append(
					f"Tilok-approved deductions averaged {abs(delta):.2f}% {direction} than system "
					f"calculation across {ds[0].c} Deduction Sheets this year."
				)

		# Insight 2: top 3 deduction parameters
		params = frappe.db.sql(
			"""
			SELECT pr.parameter_label AS lbl,
			       COUNT(*) AS hits,
			       COALESCE(AVG(pr.deduction_pct),0) AS avg_pct
			FROM `tabTS QI Parameter Result` pr
			INNER JOIN `tabTS Quality Inspection` qi ON qi.name = pr.parent
			WHERE qi.docstatus = 1
			  AND qi.inspection_date BETWEEN %s AND %s
			  AND pr.deduction_pct > 0
			GROUP BY pr.parameter_label
			ORDER BY avg_pct DESC
			LIMIT 3
			""",
			(fy_from, fy_to),
			as_dict=True,
		)
		if params:
			lbls = ", ".join(p.lbl or "?" for p in params)
			insights.append(f"Top 3 deduction parameters this year: {lbls}.")

		# Insight 3: monthly trend
		recent = frappe.db.sql(
			"""
			SELECT COUNT(*) AS c
			FROM `tabTS Quality Inspection`
			WHERE docstatus = 1
			  AND inspection_date BETWEEN %s AND %s
			""",
			_fy_bounds(year),
			as_dict=True,
		)
		if recent and recent[0].c == 0:
			insights.append("No submitted inspections this fiscal year yet.")

		return insights

	return _get_or_set(key, no_cache, build)
