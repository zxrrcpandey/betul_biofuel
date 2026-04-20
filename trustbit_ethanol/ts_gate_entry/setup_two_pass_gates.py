"""Two-Pass Gate Flow (v2.8.3) — Custom Field seeding for TS Token only.

Isolated from setup.py (which is partially locked under MR_FULL/PO_FULL).
Fields are audit/tracking — set exclusively via the 3 new POST APIs
(g2_mat_log_entry, g2_mat_log_exit, g1_final_exit on TS Token).

Also extends TS Token.status Select options via Property Setter to include
the new values introduced by the two-pass flow:
  - G1 Entered
  - G2 Entered
  - Plant Exited
  - Campus Exited

The legacy "Exited" value is preserved in the Select list during the
transition cycle so (a) existing tokens don't fail validation and (b)
rollback is possible without data loss. The patch
`trustbit_ethanol.patches.v2_8.rename_exited_status` migrates rows.
"""

import json

import frappe


def seed_two_pass_gate_fields():
	"""Seed 4 Custom Fields on TS Token for two-pass (G2 Entry/Exit) material flow
	and extend Token.status Select options. Idempotent."""
	fields = [
		{
			"doctype": "Custom Field",
			"dt": "TS Token",
			"fieldname": "g2_mat_entry_time",
			"label": "G2 Entry Time (Material)",
			"fieldtype": "Datetime",
			"read_only": 1,
			"insert_after": "g1_entry_time",
			"in_list_view": 0,
			"print_hide": 1,
			"description": "v2.8.3: timestamp when vehicle physically enters via G2 (second entry gate). Set by G2 Gate Operator via g2_mat_log_entry API.",
		},
		{
			"doctype": "Custom Field",
			"dt": "TS Token",
			"fieldname": "g2_mat_entry_by",
			"label": "G2 Entry Recorded By",
			"fieldtype": "Link",
			"options": "User",
			"read_only": 1,
			"insert_after": "g2_mat_entry_time",
			"print_hide": 1,
			"description": "v2.8.3: G2 operator who recorded the entry.",
		},
		{
			"doctype": "Custom Field",
			"dt": "TS Token",
			"fieldname": "g2_mat_exit_time",
			"label": "G2 Exit Time (Material)",
			"fieldtype": "Datetime",
			"read_only": 1,
			"insert_after": "g2_mat_entry_by",
			"in_list_view": 0,
			"print_hide": 1,
			"description": "v2.8.3: timestamp when vehicle physically exits via G2 (first exit checkpoint, after Tare). Transitions status to 'Plant Exited'.",
		},
		{
			"doctype": "Custom Field",
			"dt": "TS Token",
			"fieldname": "g2_mat_exit_by",
			"label": "G2 Exit Recorded By",
			"fieldtype": "Link",
			"options": "User",
			"read_only": 1,
			"insert_after": "g2_mat_exit_time",
			"print_hide": 1,
			"description": "v2.8.3: G2 operator who recorded the exit.",
		},
	]
	for f in fields:
		try:
			frappe.get_doc(f).insert(ignore_if_duplicate=True, ignore_permissions=True)
			frappe.logger().info(f"[seed_two_pass] {f['fieldname']} ok")
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(message=f"seed_two_pass failure {f['fieldname']}: {e}", title="seed_two_pass_gate_fields")

	# v2.8.3: extend TS Token.status options with G1 Entered, G2 Entered,
	# Plant Exited, Campus Exited. "Exited" stays in the list for one cycle
	# so legacy rows and rollback remain valid.
	STATUS_PS_NAME = "TS Token-status-options"
	new_options = "\n".join([
		"Token Generated", "PO Linked", "G1 Entered", "G2 Entered",
		"Gross Weighed", "Quality Done", "Graded", "Unloading",
		"Tare Weighed", "GRN Created", "Plant Exited", "Campus Exited", "Exited",
		"SI Linked", "Tare Recorded", "Loading Done", "Gross Recorded", "Dispatch Ready",
	])
	try:
		existing = frappe.db.get_value("Property Setter", STATUS_PS_NAME, "value")
		if not existing or existing != new_options:
			ps = frappe.get_doc({
				"doctype": "Property Setter",
				"doctype_or_field": "DocField",
				"doc_type": "TS Token",
				"field_name": "status",
				"property": "options",
				"value": new_options,
				"property_type": "Text",
			})
			ps.flags.ignore_permissions = True
			ps.insert(ignore_if_duplicate=True)
		frappe.clear_cache(doctype="TS Token")
	except Exception as e:
		frappe.log_error(message=f"seed_two_pass status Property Setter: {e}", title="seed_two_pass_gate_fields")

	frappe.clear_cache(doctype="TS Token")


def patch_number_card_exited_filters():
	"""v2.8.3: rewrite Number Card filters_json entries that reference the
	legacy 'Exited' status so they include 'Campus Exited' too.

	Must be registered AFTER `setup.seed_number_cards` in hooks.py
	`after_migrate` because that seeder overwrites filters_json with the
	legacy literal 'Exited' on every migrate. Without this corrective pass,
	dashboards under-count exited tokens after v2.8.3 ships.

	Idempotent: only rewrites rows still missing 'Campus Exited'.
	"""
	try:
		cards = frappe.db.sql(
			"SELECT name, filters_json FROM `tabNumber Card` WHERE filters_json LIKE %s",
			("%\"Exited\"%",),
			as_dict=True,
		)
	except Exception as e:
		frappe.log_error(message=f"patch_number_card_exited_filters select: {e}", title="seed_two_pass_gate_fields")
		return

	updated = 0
	for c in cards:
		try:
			filters = json.loads(c.filters_json or "[]")
			changed = False
			for i, flt in enumerate(filters):
				if not (isinstance(flt, list) and len(flt) >= 4):
					continue
				if flt[1] != "status":
					continue
				# equality filter against "Exited" → include both values
				if flt[2] == "=" and flt[3] == "Exited":
					filters[i] = [flt[0], flt[1], "in", ["Exited", "Campus Exited"]]
					changed = True
				# not-in filter containing "Exited" but not yet "Campus Exited"
				elif (
					flt[2] == "not in"
					and isinstance(flt[3], list)
					and "Exited" in flt[3]
					and "Campus Exited" not in flt[3]
				):
					filters[i] = [flt[0], flt[1], "not in", list(flt[3]) + ["Campus Exited"]]
					changed = True
			if changed:
				frappe.db.set_value("Number Card", c.name, "filters_json", json.dumps(filters))
				updated += 1
		except Exception as e:
			frappe.log_error(message=f"Number Card {c.name} filter migration: {e}", title="seed_two_pass_gate_fields")

	frappe.db.commit()
	frappe.logger().info(f"patch_number_card_exited_filters: updated {updated} Number Cards")
