"""Material Request custom naming: PR-{CC_CODE}-{YY}-{#####}

Prefix based on MR Purpose (material_request_type):
  Purchase        → PR
  Service Request → SR
  Fixed Asset     → FA
  Material Transfer → BBPL-TRAN
  Material Issue    → BBPL-ISSU

CC Code from Cost Center's cc_code custom field.
Year is 2-digit. Serial is 5-digit, per prefix+cc_code+year.
"""

import frappe
from frappe.utils import getdate


# Purpose → Prefix mapping
PURPOSE_PREFIX = {
	"Purchase": "PR",
	"Service Request": "SR",
	"Fixed Asset": "FA",
	"Material Transfer": "BBPL-TRAN",
	"Material Issue": "BBPL-ISSU",
}

# Maximum retries to find a non-duplicate name
MAX_NAMING_RETRIES = 20


def _generate_mr_name(doc):
	"""Generate MR name: {PREFIX}-{CC_CODE}-{YY}-{#####}"""

	# Get prefix from purpose
	purpose = doc.material_request_type or "Purchase"
	prefix = PURPOSE_PREFIX.get(purpose, "PR")

	# Get CC code from Cost Center
	if not doc.cost_center:
		frappe.throw("Cost Center is required for Material Request naming.")

	cc_code = frappe.db.get_value("Cost Center", doc.cost_center, "cc_code")
	if not cc_code:
		frappe.throw(
			f"Cost Center <b>{doc.cost_center}</b> has no CC Code set. "
			f"Go to <a href='/app/cost-center/{doc.cost_center}'>{doc.cost_center}</a> "
			"and set the <b>CC Code</b> field."
		)

	# Year (2-digit)
	year = getdate(doc.transaction_date or None).strftime("%y")

	# Build series key for counter
	series_key = f"{prefix}-{cc_code}-{year}-"

	# Get next number, with duplicate protection
	name = _get_next_unique_name(series_key, prefix, cc_code, year)

	return name


def mr_autoname(doc, method=None):
	"""Called from doc_events autoname hook.
	Sets doc.name directly — Frappe will use this instead of naming_series."""
	doc.name = _generate_mr_name(doc)


def mr_before_insert(doc, method=None):
	"""Fallback: if autoname didn't fire (naming_series took priority),
	override the name here before insert."""
	if doc.name and doc.name.startswith(("PR-", "SR-", "FA-", "BBPL-TRAN-", "BBPL-ISSU-")):
		return  # autoname already set the name correctly

	# autoname didn't fire — generate name now
	doc.name = _generate_mr_name(doc)


def _get_next_unique_name(series_key, prefix, cc_code, year, digits=5):
	"""Get next unique MR name, auto-fixing counter if it's out of sync.

	1. First syncs the counter with the actual max in DB (prevents duplicates)
	2. Then gets next serial via Frappe's getseries()
	3. Verifies the name doesn't exist — retries up to MAX_NAMING_RETRIES times
	"""
	# Step 1: Sync counter with actual max in DB
	_sync_counter_with_db(series_key, prefix, cc_code, year, digits)

	# Step 2: Get next serial and verify uniqueness
	from frappe.model.naming import getseries

	for attempt in range(MAX_NAMING_RETRIES):
		serial = getseries(series_key, digits)
		name = f"{prefix}-{cc_code}-{year}-{serial}"

		if not frappe.db.exists("Material Request", name):
			return name

		# Name already exists — counter was behind, getseries already incremented
		# so next iteration will try the next number
		frappe.logger().warning(
			f"MR naming: {name} already exists (attempt {attempt + 1}), trying next serial"
		)

	# All retries exhausted
	frappe.throw(
		f"Cannot generate Material Request number. "
		f"The naming counter for <b>{series_key}</b> is out of sync. "
		f"Tried {MAX_NAMING_RETRIES} serial numbers but all already exist. "
		f"Please contact IT Head to fix the counter in tabSeries.",
		title="Naming Error"
	)


def _sync_counter_with_db(series_key, prefix, cc_code, year, digits=5):
	"""Ensure tabSeries counter is at least as high as the max existing MR number.

	This prevents duplicate names when the counter falls behind due to
	direct DB inserts, imports, or transaction rollbacks.
	"""
	# Find max existing serial for this prefix
	pattern = f"{prefix}-{cc_code}-{year}-%"
	max_name = frappe.db.sql(
		"SELECT name FROM `tabMaterial Request` WHERE name LIKE %s ORDER BY name DESC LIMIT 1",
		pattern
	)

	if not max_name:
		return  # No existing MRs — counter is fine (or will be created by getseries)

	# Extract the serial number from the last part
	last_name = max_name[0][0]
	try:
		last_serial = int(last_name.rsplit("-", 1)[1])
	except (ValueError, IndexError):
		return  # Can't parse — skip sync

	# Check current counter
	current = frappe.db.sql(
		"SELECT current FROM tabSeries WHERE name = %s", series_key
	)

	if current:
		current_val = current[0][0] or 0
		if current_val < last_serial:
			frappe.db.sql(
				"UPDATE tabSeries SET current = %s WHERE name = %s",
				(last_serial, series_key)
			)
			frappe.logger().warning(
				f"MR naming: Fixed counter {series_key} from {current_val} to {last_serial}"
			)
	else:
		# Counter doesn't exist — create it at the max value
		frappe.db.sql(
			"INSERT INTO tabSeries (name, current) VALUES (%s, %s)",
			(series_key, last_serial)
		)
		frappe.logger().warning(
			f"MR naming: Created missing counter {series_key} = {last_serial}"
		)
