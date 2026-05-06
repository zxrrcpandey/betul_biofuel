# v2.9.12 Sprint 2 — Health Check setup seeder.
#
# Idempotent. Creates / updates:
#   - Custom Field on TS Settings: ts_health_check_enabled (Check, default 1,
#     permlevel=1 → IT Head + System Manager only; serves as kill-switch for
#     scheduler + endpoints).
#
# The TS Health Check Acknowledgment DocType ships as a standard fixture
# (auto-installed by bench migrate from doctype/ts_health_check_acknowledgment/).
import frappe


def seed_health_check():
	"""Idempotent kill-switch field on TS Settings."""
	if frappe.flags.in_install or frappe.flags.in_test:
		return

	_ensure_kill_switch_field()


def _ensure_kill_switch_field():
	field = frappe.db.get_value(
		"Custom Field",
		{"dt": "TS Settings", "fieldname": "ts_health_check_enabled"},
		["name", "permlevel", "default"],
		as_dict=True,
	)

	target = {
		"dt": "TS Settings",
		"fieldname": "ts_health_check_enabled",
		"label": "Approval Flow Health Check Enabled",
		"fieldtype": "Check",
		"default": "1",
		"permlevel": 1,
		"description": (
			"v2.9.12 — Master kill-switch for the Approval Flow Health Check feature. "
			"When OFF (0): all validators return empty + scheduler digest skipped + "
			"endpoints return 'feature_disabled'. IT Head + System Manager can flip."
		),
		"insert_after": "ts_avp_deputy_enabled",
		"in_list_view": 0,
	}

	if not field:
		# Create the field
		doc = frappe.get_doc({"doctype": "Custom Field", **target})
		# after_migrate runs as Administrator; ignore_permissions for fresh-site seed.
		doc.flags.ignore_permissions = True
		doc.insert()
		# Frappe Singles default trap (Lesson 227): the Custom Field `default`
		# does NOT auto-populate tabSingles.value. Force initial value to 1
		# explicitly. IT Head can flip OFF later via UI (permlevel=1 gates).
		try:
			frappe.db.set_single_value("TS Settings", "ts_health_check_enabled", 1)
			frappe.db.commit()
		except Exception:
			pass
		return

	# Already exists — verify permlevel + default are still 1
	# (defensive: someone may have edited via UI)
	updates = {}
	if field.permlevel != 1:
		updates["permlevel"] = 1
	if field.default not in ("1", 1):
		updates["default"] = "1"
	if updates:
		for k, v in updates.items():
			frappe.db.set_value("Custom Field", field.name, k, v, update_modified=False)
