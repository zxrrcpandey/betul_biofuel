import re

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, get_last_day


# ─────────────────────────────────────────────────────────────────────────
#  ESY + quarter window helpers (canonical — imported by the read API too,
#  so the quarter math lives in exactly one place; Lesson 221 reuse-before-build)
# ─────────────────────────────────────────────────────────────────────────

# ESY = "alcoholic year" Nov 1 -> Oct 31. Fixed quarters:
#   Q1 Nov-Jan, Q2 Feb-Apr, Q3 May-Jul, Q4 Aug-Oct.
# Each quarter is defined by (start_month, start_day) -> end derived via
# get_last_day (handles Feb leap years). Offsets are months FROM esy_start.
_QUARTER_DEFS = (
	("Q1", "Q1 (Nov-Jan)", 0),
	("Q2", "Q2 (Feb-Apr)", 3),
	("Q3", "Q3 (May-Jul)", 6),
	("Q4", "Q4 (Aug-Oct)", 9),
)

_ESY_CODE_RE = re.compile(r"^\s*(?:ESY\s*)?(\d{4})\s*[-/]\s*(\d{2,4})\s*$")


def _add_months(d, n):
	"""Add n whole months to a date, clamped to the last valid day."""
	d = getdate(d)
	month0 = d.month - 1 + n
	year = d.year + month0 // 12
	month = month0 % 12 + 1
	# get_last_day clamps Feb/30-day months correctly (leap-year aware).
	last = get_last_day(getdate(f"{year}-{month:02d}-01")).day
	day = min(d.day, last)
	return getdate(f"{year}-{month:02d}-{day:02d}")


def parse_esy_start_year(esy_code):
	"""Extract the ESY start (calendar) year from a code like '2025-26',
	'ESY 2025-26', or '2025-2026'. Returns an int year or None."""
	if not esy_code:
		return None
	m = _ESY_CODE_RE.match(str(esy_code))
	if not m:
		return None
	return int(m.group(1))


def derive_esy_dates(esy_code):
	"""Return (esy_start, esy_end) = (01-Nov of start year, 31-Oct of next year)
	or (None, None) if the code can't be parsed."""
	start_year = parse_esy_start_year(esy_code)
	if not start_year:
		return None, None
	esy_start = getdate(f"{start_year}-11-01")
	esy_end = getdate(f"{start_year + 1}-10-31")
	return esy_start, esy_end


def esy_quarters(esy_start):
	"""Return the four (code, label, start, end) tuples for an ESY, derived
	from esy_start (01-Nov). End dates use get_last_day so Feb/Apr + leap
	years resolve correctly."""
	esy_start = getdate(esy_start)
	out = []
	for code, label, offset in _QUARTER_DEFS:
		q_start = _add_months(esy_start, offset)
		# last day of the third month of the quarter
		q_end = get_last_day(_add_months(esy_start, offset + 2))
		out.append((code, label, q_start, q_end))
	return out


def quarter_for_date(d, esy_start):
	"""Return the quarter code ('Q1'..'Q4') whose window contains date d within
	the ESY anchored at esy_start, else None (outside the ESY window)."""
	if not d or not esy_start:
		return None
	d = getdate(d)
	for code, _label, q_start, q_end in esy_quarters(esy_start):
		if q_start <= d <= q_end:
			return code
	return None


# ─────────────────────────────────────────────────────────────────────────
#  Controller
# ─────────────────────────────────────────────────────────────────────────

class TSOMCSupplyTarget(Document):
	def validate(self):
		self._derive_esy_window()
		self._compute_annual_allocation()
		self._validate_quarter_allocations()
		self._validate_unique_omc_esy()
		self._stamp_depot_quarters()

	def _derive_esy_window(self):
		"""Auto-fill esy_start / esy_end from esy_code if blank; warn if the
		window is not the canonical Nov 1 -> Oct 31."""
		derived_start, derived_end = derive_esy_dates(self.esy_code)
		if not self.esy_start and derived_start:
			self.esy_start = derived_start
		if not self.esy_end and derived_end:
			self.esy_end = derived_end

		if not self.esy_start or not self.esy_end:
			frappe.throw(
				"Could not derive the ESY window. Enter a valid ESY code "
				"(e.g. 2025-26) or set ESY Start / ESY End explicitly."
			)

		if getdate(self.esy_start) >= getdate(self.esy_end):
			frappe.throw("ESY Start must be before ESY End.")

		# Soft warning only — IT Head may run a non-standard window deliberately.
		if derived_start and derived_end:
			if getdate(self.esy_start) != derived_start or getdate(self.esy_end) != derived_end:
				frappe.msgprint(
					f"Note: ESY window ({self.esy_start} to {self.esy_end}) differs from the "
					f"standard Nov 1 to Oct 31 ({derived_start} to {derived_end}).",
					indicator="orange",
					alert=True,
				)

	def _compute_annual_allocation(self):
		"""Annual = Q1+Q2+Q3+Q4, ALWAYS recomputed server-side. The field is
		read-only; never trust a client-supplied total (Lesson 294)."""
		self.annual_allocation_kl = (
			flt(self.q1_allocation_kl)
			+ flt(self.q2_allocation_kl)
			+ flt(self.q3_allocation_kl)
			+ flt(self.q4_allocation_kl)
		)

	def _validate_quarter_allocations(self):
		"""Each quarter >= 0; at least one quarter > 0; warn on a 0 quarter."""
		quarters = {
			"Q1": flt(self.q1_allocation_kl),
			"Q2": flt(self.q2_allocation_kl),
			"Q3": flt(self.q3_allocation_kl),
			"Q4": flt(self.q4_allocation_kl),
		}
		for q, val in quarters.items():
			if val < 0:
				frappe.throw(f"{q} allocation cannot be negative.")

		if all(v == 0 for v in quarters.values()):
			frappe.throw(
				"At least one quarter must have a non-zero allocation. "
				"An all-zero target is not allowed."
			)

		zero_qs = [q for q, v in quarters.items() if v == 0]
		if zero_qs:
			frappe.msgprint(
				f"Note: {', '.join(zero_qs)} allocation is 0 (no OMC commitment for "
				f"that quarter). Allowed, but flagged.",
				indicator="orange",
				alert=True,
			)

	def _validate_unique_omc_esy(self):
		"""Enforce UNIQUE (omc, esy_code) — one target per OMC per ESY."""
		if not self.omc or not self.esy_code:
			return
		dupe = frappe.db.sql(
			"""
			SELECT name FROM `tabTS OMC Supply Target`
			WHERE omc = %s AND esy_code = %s AND name != %s
			LIMIT 1
			""",
			(self.omc, self.esy_code, self.name or ""),
		)
		if dupe:
			frappe.throw(
				f"A Supply Target for OMC '{self.omc}' and ESY '{self.esy_code}' "
				f"already exists ({dupe[0][0]}). Only one target per OMC per ESY is allowed."
			)

	def _stamp_depot_quarters(self):
		"""Derive + stamp each depot-plan row's read-only `quarter` from
		instructed_on vs the ESY quarter windows. Warn on a wrong-OMC depot
		and on duplicate (depot, quarter) rows."""
		if not self.depot_plan:
			return

		esy_start = getdate(self.esy_start)
		seen = set()
		for row in self.depot_plan:
			if not row.instructed_on:
				continue
			q = quarter_for_date(row.instructed_on, esy_start)
			if not q:
				frappe.throw(
					f"Depot row '{row.depot_customer}': Instructed On "
					f"({row.instructed_on}) falls outside the ESY window "
					f"({self.esy_start} to {self.esy_end})."
				)
			row.quarter = q

			# Warn (do not block) if the depot's own ts_omc tag differs from this target.
			if row.depot_customer:
				cust_omc = frappe.db.get_value("Customer", row.depot_customer, "ts_omc")
				if cust_omc and cust_omc != self.omc:
					frappe.msgprint(
						f"Note: depot '{row.depot_customer}' is tagged to OMC '{cust_omc}', "
						f"not '{self.omc}'. Its dispatches will roll up to '{cust_omc}'.",
						indicator="orange",
						alert=True,
					)

			key = (row.depot_customer, q)
			if key in seen:
				frappe.msgprint(
					f"Note: duplicate plan rows for depot '{row.depot_customer}' in {q}.",
					indicator="orange",
					alert=True,
				)
			seen.add(key)
