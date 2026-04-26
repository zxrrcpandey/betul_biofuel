"""
TS QI Parameter Result — child table of TS Quality Inspection (v2.9.0 Day 4).

Each row represents one parameter being measured during a quality inspection,
populated from the linked TS QC Template's parameters table when the inspector
selects a template.

Inspector enters:
  - actual_value: the measured value
  - deduction_pct: per-parameter deduction percentage

Read-only fields (copied from QC Template row):
  - parameter_code, parameter_label, parameter_type, uom, min_value, max_value,
    is_critical

Sums into TS Quality Inspection.total_deduction_pct via parent's validate hook.

This is a pure child DocType (`istable=1`) — all behaviour lives on the parent
TSQualityInspection controller; no per-row logic required.
"""

import frappe
from frappe.model.document import Document


class TSQIParameterResult(Document):
	# Child table — no row-level logic. Validation + aggregation happens on parent.
	pass
