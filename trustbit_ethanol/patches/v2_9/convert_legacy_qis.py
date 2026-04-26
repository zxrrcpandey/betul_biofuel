"""v2.9.0 Day 5A — Legacy QI Conversion patch.

Find existing TS Quality Inspection records with NO `qc_template` set and
attach a generic LEGACY template so v2.9 calculation paths still work.

- Grain QIs    -> LEGACY_GRAIN template (4 generic params: Moisture, FM, Damaged, Other)
- Coal/Other QIs -> LEGACY_COAL  template (3 generic params: GCV, Moisture, Ash)

Auto-creates the templates (TS QC Template + child TS QC Inspection Row)
on first run if missing. Sets `quality_report_no` = "LEGACY-QI-{name}" when
empty so the unique BBPL-QR-YY-##### space is not collided with.

Idempotent + safe-rollback:
  Skipped entirely when TS Settings.ts_qc_v29_legacy_converted == 1.
  Per-doc skip when qc_template already set.

Lessons applied:
  171/172 — TS Settings is a Single, fields can return 0/None — fail-closed default.
  181 — sql_ddl() not used (no DDL here, only ORM ops).
  196 — get_list with ignore_permissions=False not needed (admin context patch).
"""

import frappe


LEGACY_GRAIN_TEMPLATE = "LEGACY_GRAIN"
LEGACY_COAL_TEMPLATE = "LEGACY_COAL"

LEGACY_GRAIN_PARAMS = [
	{"parameter_code": "MOISTURE", "parameter_label": "Moisture", "parameter_type": "Numeric", "uom": "%", "max_value": 14.0, "is_mandatory": 1},
	{"parameter_code": "FM", "parameter_label": "Foreign Matter", "parameter_type": "Numeric", "uom": "%", "max_value": 2.0, "is_mandatory": 1},
	{"parameter_code": "DAMAGED", "parameter_label": "Damaged Grain", "parameter_type": "Numeric", "uom": "%", "max_value": 5.0, "is_mandatory": 0},
	{"parameter_code": "OTHER", "parameter_label": "Other Defects", "parameter_type": "Numeric", "uom": "%", "max_value": 5.0, "is_mandatory": 0},
]

LEGACY_COAL_PARAMS = [
	{"parameter_code": "GCV", "parameter_label": "GCV", "parameter_type": "Numeric", "uom": "kcal/kg", "is_mandatory": 1},
	{"parameter_code": "MOIST", "parameter_label": "Moisture", "parameter_type": "Numeric", "uom": "%", "max_value": 12.0, "is_mandatory": 0},
	{"parameter_code": "ASH", "parameter_label": "Ash", "parameter_type": "Numeric", "uom": "%", "max_value": 25.0, "is_mandatory": 0},
]


def _ensure_template(name: str, category: str, params: list) -> None:
	"""Idempotently create a legacy template if missing."""
	if frappe.db.exists("TS QC Template", name):
		return

	doc = frappe.new_doc("TS QC Template")
	doc.template_name = name
	doc.category = category
	doc.is_active = 1
	doc.version = "1.0-legacy"
	doc.description = f"Auto-created by v2.9.0 migration patch — wraps pre-v2.9 {category} QIs."
	doc.supports_bags = 0
	for p in params:
		doc.append("parameters", p)
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)
	frappe.db.commit()


def execute():
	# Safe-rollback gate — skip if already run.
	already = frappe.db.get_single_value("TS Settings", "ts_qc_v29_legacy_converted") or 0
	if int(already) == 1:
		print("[v2.9.0] convert_legacy_qis: already executed (ts_qc_v29_legacy_converted=1) — skipping.")
		return

	# 1. Ensure legacy templates exist.
	_ensure_template(LEGACY_GRAIN_TEMPLATE, "Grain", LEGACY_GRAIN_PARAMS)
	_ensure_template(LEGACY_COAL_TEMPLATE, "Coal", LEGACY_COAL_PARAMS)

	# 2. Find candidate QIs — qc_template empty.
	# Use raw SQL: fast + does not load full doc.
	rows = frappe.db.sql(
		"""
		SELECT name, item_category, quality_report_no
		FROM `tabTS Quality Inspection`
		WHERE COALESCE(qc_template, '') = ''
		""",
		as_dict=True,
	)

	if not rows:
		print("[v2.9.0] convert_legacy_qis: no candidates — marking complete.")
		_mark_done()
		return

	updated = 0
	skipped = 0
	for r in rows:
		category = (r.item_category or "").strip()
		if category == "Grain":
			template = LEGACY_GRAIN_TEMPLATE
		elif category == "Coal":
			template = LEGACY_COAL_TEMPLATE
		else:
			# "Other" or empty -> default to coal (closest to non-grain semantics).
			template = LEGACY_COAL_TEMPLATE

		# quality_report_no fallback: only assign when empty (preserve any user value).
		new_qr = r.quality_report_no
		if not new_qr or not str(new_qr).strip():
			new_qr = f"LEGACY-QI-{r.name}"

		try:
			# Direct DB update — no docevents fire (controlled migration context).
			frappe.db.set_value(
				"TS Quality Inspection",
				r.name,
				{
					"qc_template": template,
					"quality_report_no": new_qr,
				},
				update_modified=False,
			)
			updated += 1
		except Exception as e:
			frappe.log_error(
				message=f"Legacy QI conversion failed for {r.name}: {e}",
				title="v2.9.0 convert_legacy_qis",
			)
			skipped += 1

	frappe.db.commit()
	print(
		f"[v2.9.0] convert_legacy_qis: updated={updated} skipped={skipped} total_candidates={len(rows)}"
	)
	_mark_done()


def _mark_done():
	frappe.db.set_value(
		"TS Settings",
		"TS Settings",
		"ts_qc_v29_legacy_converted",
		1,
		update_modified=False,
	)
	frappe.db.commit()
