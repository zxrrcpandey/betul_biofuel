"""TS Deduction Master API — v2.9.0 Phase 3 Day 3.

Whitelisted endpoints used by po_approval.js (and future MR / PI screens) to
fetch a Deduction Master's rows so the form can pre-populate
`ts_deduction_overrides` (TS PO Deduction Override child table).

## Endpoints

- get_template_rows(template_name)
    POST endpoint per Lesson 175. Returns the active rows of a TS Deduction
    Master, formatted for direct copy into a TS PO Deduction Override grid.
    Permission: any user with TS Deduction Master `read` perm. Falls through
    cleanly with `frappe.PermissionError` if the caller lacks read on the
    master record (Frappe `frappe.get_doc` checks read perm by default —
    we re-validate explicitly via `frappe.has_permission` to fail fast with
    a friendlier 403 message).

- get_default_for_category(category)
    POST endpoint. Returns the name of the Deduction Master flagged
    is_default=1 + is_active=1 for the given category, or None. Used by
    po_before_save (Plan v4 Gap 1 — silent fallback).

## Why methods=["POST"]

Lesson 175 — mutation-adjacent reads still use POST to keep CSRF tokens in
play. These endpoints are read-only but ride alongside the form save flow,
so following the same convention keeps middleware behaviour uniform.

## Why we DON'T use allow_guest

Lesson 190 — `allow_guest` is only needed for endpoints that legitimately
fire on /login or non-/app routes. Our calls fire on the PO form (Desk-only),
so the default authenticated path is correct.
"""

import frappe
from frappe import _


@frappe.whitelist(methods=["POST"])
def get_template_rows(template_name: str):
	"""Return the active rows of a TS Deduction Master, ready for grid copy.

	Args:
		template_name: name of a TS Deduction Master record.

	Returns:
		dict {
			"template_name": <str>,
			"category": <str>,
			"is_active": <int>,
			"rows": [
				{
					"deduction_code": <str>,
					"deduction_label": <str>,
					"default_rate": <float>,
					"default_rate_type": <str>,
				},
				...
			],
		}

	Raises:
		frappe.PermissionError if caller lacks read on TS Deduction Master.
		frappe.DoesNotExistError if template_name is unknown.
		frappe.ValidationError if template is inactive (is_active=0).
	"""
	if not template_name:
		frappe.throw(_("Template name is required."))

	# Explicit permission check — `get_doc` does this implicitly but we want a
	# clean 403 message ahead of any data work.
	if not frappe.has_permission("TS Deduction Master", ptype="read",
								  doc=template_name):
		raise frappe.PermissionError(
			_("You do not have read permission on TS Deduction Master.")
		)

	try:
		master = frappe.get_doc("TS Deduction Master", template_name)
	except frappe.DoesNotExistError:
		frappe.throw(_("Deduction Master '{0}' does not exist.").format(template_name))

	if not master.is_active:
		frappe.throw(
			_("Deduction Master '{0}' is inactive. Pick an active template.").format(
				template_name
			)
		)

	rows = []
	for r in master.get("deductions") or []:
		# Only emit rows where the row itself is active (Day 1-2 row JSON has
		# an is_active Check default=1 — if a future user deactivates a row
		# inside an active master, we skip it on copy).
		if not r.is_active:
			continue
		rows.append({
			"deduction_code": (r.deduction_code or "").strip().upper(),
			"deduction_label": r.deduction_label,
			"default_rate": _round4(r.rate),
			"default_rate_type": r.rate_type,
		})

	return {
		"template_name": master.name,
		"category": master.category,
		"is_active": master.is_active,
		"rows": rows,
	}


@frappe.whitelist(methods=["POST"])
def get_default_for_category(category: str):
	"""Return the name of the default+active Deduction Master for a category.

	Used by po_before_save for Plan v4 Gap 1 silent fallback. Returns None if
	no default is configured for the category.

	Args:
		category: 'Grain', 'Coal', or 'Other'.

	Returns:
		dict {"name": <str | None>, "category": <str>}
	"""
	if not category:
		return {"name": None, "category": None}

	# Permission gate — read on TS Deduction Master required.
	if not frappe.has_permission("TS Deduction Master", ptype="read"):
		raise frappe.PermissionError(
			_("You do not have read permission on TS Deduction Master.")
		)

	row = frappe.db.get_value(
		"TS Deduction Master",
		{"category": category, "is_default": 1, "is_active": 1},
		"name",
	)
	return {"name": row, "category": category}


def _round4(val) -> float:
	"""Round to 4 decimals (Plan v4 Gap 2). Returns 0.0 if val is None / non-numeric."""
	try:
		return round(float(val or 0), 4)
	except (TypeError, ValueError):
		return 0.0
