"""Material Request custom naming: PR-{CC_CODE}-{YY}-{#####}

Prefix based on MR Purpose (material_request_type):
  Purchase        → PR
  Service Request → SR  (custom purpose added in v2.5)
  Fixed Asset     → FA  (not standard in ERPNext, mapped from purpose field)
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


def mr_autoname(doc, method=None):
	"""Generate MR name: {PREFIX}-{CC_CODE}-{YY}-{#####}"""

	# Get prefix from purpose
	purpose = doc.material_request_type or "Purchase"
	prefix = PURPOSE_PREFIX.get(purpose)
	if not prefix:
		prefix = "PR"

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

	# Get next number using Frappe's built-in series counter (atomic, handles concurrency)
	# This uses the tabSeries table — same mechanism as naming_series
	serial = _get_next_serial(series_key, 5)

	doc.name = f"{prefix}-{cc_code}-{year}-{serial}"


def _get_next_serial(series_key, digits=5):
	"""Get next serial number for a series key using tabSeries (atomic)."""
	# Check if series exists
	current = frappe.db.sql(
		"SELECT current FROM tabSeries WHERE name = %s FOR UPDATE",
		series_key
	)

	if current:
		next_val = (current[0][0] or 0) + 1
		frappe.db.sql(
			"UPDATE tabSeries SET current = %s WHERE name = %s",
			(next_val, series_key)
		)
	else:
		next_val = 1
		frappe.db.sql(
			"INSERT INTO tabSeries (name, current) VALUES (%s, %s)",
			(series_key, next_val)
		)

	return str(next_val).zfill(digits)
