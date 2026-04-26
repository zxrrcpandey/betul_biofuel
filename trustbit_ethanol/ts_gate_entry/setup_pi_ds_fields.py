"""
v2.9.0 Day 4 — PI Custom Fields for Deduction Sheet propagation.

When TS Deduction Sheet is submitted, its actual_deduction_pct/kg + reason are
copied into linked Purchase Invoice via these 4 read-only Custom Fields.

Fields (Purchase Invoice):
  1. ts_ds_actual_deduction_section — Section Break (collapsible) "v2.9 DS Reference"
  2. ts_ds_reference                — Link → TS Deduction Sheet (read_only=1)
  3. ts_ds_actual_deduction_pct     — Percent (read_only=1)
  4. ts_ds_actual_deduction_kg      — Float (read_only=1)
  5. ts_ds_actual_deduction_reason  — Small Text (read_only=1)

Placement: insert_after = ts_qc_override_at (the last v2.8.1 QC override field).

Why isolated from setup.py:
  Per Lessons 169, 183, 187, 188, 190, 191 every v2.8.x / v2.9.x Custom Field
  seed lives in its own module so a single failure doesn't cascade.

Idempotency:
  ignore_if_duplicate=True + frappe.db.set_value update path on existing rows.

permlevel rationale:
  read_only=1 + no_copy=1. We DO NOT use permlevel=1 here — these fields
  are populated server-side by ts_deduction_sheet._propagate_to_pi() under
  frappe.flags.in_ds_internal=True. They're advisory display fields; PI
  payment logic doesn't read them. Tamper guard would be over-engineering.
"""

import frappe


def seed_pi_ds_fields():
	"""Seed PI Custom Fields for DS propagation (idempotent)."""
	fields = [
		{
			"doctype": "Custom Field",
			"dt": "Purchase Invoice",
			"fieldname": "ts_ds_actual_deduction_section",
			"label": "v2.9 Deduction Sheet Reference",
			"fieldtype": "Section Break",
			"insert_after": "ts_qc_override_at",
			"collapsible": 1,
			"description": (
				"v2.9.0: When a TS Deduction Sheet is submitted that references "
				"this PI's PR chain, the actual deduction values are copied here "
				"for audit. Read-only — do not edit manually."
			),
		},
		{
			"doctype": "Custom Field",
			"dt": "Purchase Invoice",
			"fieldname": "ts_ds_reference",
			"label": "DS Reference",
			"fieldtype": "Link",
			"options": "TS Deduction Sheet",
			"insert_after": "ts_ds_actual_deduction_section",
			"read_only": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
			"description": (
				"Submitted TS Deduction Sheet referencing this PI. Auto-populated "
				"on DS submit. Cleared on DS cancel."
			),
		},
		{
			"doctype": "Custom Field",
			"dt": "Purchase Invoice",
			"fieldname": "ts_ds_actual_deduction_pct",
			"label": "DS Actual Deduction %",
			"fieldtype": "Percent",
			"insert_after": "ts_ds_reference",
			"read_only": 1,
			"no_copy": 1,
			"precision": "3",
			"description": (
				"Actual deduction % from the linked TS Deduction Sheet "
				"(actual_deduction_pct). Read-only — auto-fetched on DS submit."
			),
		},
		{
			"doctype": "Custom Field",
			"dt": "Purchase Invoice",
			"fieldname": "ts_ds_actual_deduction_kg",
			"label": "DS Actual Deduction (Kg)",
			"fieldtype": "Float",
			"insert_after": "ts_ds_actual_deduction_pct",
			"read_only": 1,
			"no_copy": 1,
			"precision": "3",
			"description": (
				"Actual deduction in Kg from the linked TS Deduction Sheet "
				"(actual_deduction_kg). Read-only."
			),
		},
		{
			"doctype": "Custom Field",
			"dt": "Purchase Invoice",
			"fieldname": "ts_ds_actual_deduction_reason",
			"label": "DS Reason for Difference",
			"fieldtype": "Small Text",
			"insert_after": "ts_ds_actual_deduction_kg",
			"read_only": 1,
			"no_copy": 1,
			"description": (
				"Reason captured by Tilok (Grain Manager) explaining the "
				"difference between system and actual deduction %. Read-only."
			),
		},
	]

	for f in fields:
		cf_name = f"Purchase Invoice-{f['fieldname']}"
		if frappe.db.exists("Custom Field", cf_name):
			# Idempotent UPDATE — keep insert_after, label, description in sync
			update_props = {
				"insert_after": f["insert_after"],
				"label": f["label"],
				"description": f.get("description", ""),
			}
			if f["fieldtype"] == "Section Break":
				update_props["collapsible"] = f.get("collapsible", 0)
			if f["fieldtype"] == "Link":
				update_props["options"] = f.get("options", "")
			try:
				frappe.db.set_value("Custom Field", cf_name, update_props)
				frappe.logger().info(f"[setup_pi_ds_fields] Updated {cf_name}")
			except Exception as e:
				frappe.log_error(
					message=f"setup_pi_ds_fields update failure on {cf_name}: {e}",
					title="setup_pi_ds_fields",
				)
			continue

		try:
			frappe.get_doc(f).insert(ignore_if_duplicate=True, ignore_permissions=True)
			frappe.logger().info(f"[setup_pi_ds_fields] Created {cf_name}")
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"setup_pi_ds_fields insert failure on {cf_name}: {e}",
				title="setup_pi_ds_fields",
			)

	frappe.clear_cache(doctype="Purchase Invoice")
