# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration — seeders + one-time migration (v2.18.5).
#
# v2.18.5 moved the WhatsApp config OFF the TS Settings tab into two dedicated
# Single doctypes (so their save path is isolated from TS Settings' unrelated
# validations — cascade-delete etc.):
#   * TS WhatsApp Provider  — gateway + credentials
#   * TS WhatsApp Settings  — kill switch, sandbox, retry/escalation, template map
#
# The fields themselves are now STANDARD doctype fields (defined in the doctype
# JSONs), not Custom Fields on TS Settings. This module only:
#   1. seeds fail-closed defaults (Lesson 227 — JSON defaults omitted, set here),
#   2. seeds the 10 event->template rows,
#   3. ONE-TIME migrates any legacy config off the old TS Settings tab and
#      deletes those Custom Fields (idempotent; a no-op on a server that never
#      had the old tab — e.g. production, where the feature shipped inert).
#
# Registered in hooks.py after_migrate (same function name as before). Idempotent.

import frappe
from frappe.utils.password import set_encrypted_password

PROVIDER_DT = "TS WhatsApp Provider"
SETTINGS_DT = "TS WhatsApp Settings"

# Legacy value seeded by Phase 1a (when Airtel was the only provider). base_url
# is now an optional override; clear this so the provider default applies.
LEGACY_AIRTEL_BASE = "https://iqwhatsapp.airtel.in:443/gateway/airtel-xchange/basic/whatsapp-manager/v1"

# The HYBRID template set (user decision 20 Jun) — PO/MR + Item Creator +
# Cascade Delete approval events + 4 shared reminders. New rows seed DISABLED.
TEMPLATE_EVENT_KEYS = (
	"po_needs_approval",
	"po_approved",
	"po_rejected",
	"mr_needs_approval",
	"mr_approved",
	"mr_rejected",
	"item_needs_approval",
	"item_approved",
	"item_rejected",
	"cascade_needs_approval",
	"cascade_executed",
	"cascade_rejected",
	"reminder_l1",
	"reminder_l2",
	"reminder_l3",
	"reminder_generic",
	"grain_po_pending_link",
	# v2.45.0 — daily digest to the grain linkers (2 variables: count, oldest-days)
	"grain_po_link_daily_digest",
)

# Field -> doctype mapping used by the one-time legacy migration.
_PROVIDER_VALUE_FIELDS = (
	"ts_whatsapp_provider",
	"ts_whatsapp_base_url",
	"ts_whatsapp_vendor_uid",
	"ts_whatsapp_from_phone_number_id",
	"ts_whatsapp_app_id",
	"ts_whatsapp_from",
	"ts_whatsapp_waba_id",
	"ts_whatsapp_customer_id",
	"ts_whatsapp_subaccount_id",
)
_SETTINGS_VALUE_FIELDS = (
	"ts_whatsapp_enabled",
	"ts_whatsapp_sandbox_mode",
	"ts_whatsapp_test_numbers",
	"ts_whatsapp_filter_blacklist",
	"ts_whatsapp_source_ip_allowlist",
	"ts_whatsapp_max_retries",
	"ts_whatsapp_l1_hours",
	"ts_whatsapp_l2_hours",
	"ts_whatsapp_l3_hours",
	"ts_whatsapp_qc_overdue_hours",
)


def seed_whatsapp_settings_fields():
	"""after_migrate hook: migrate legacy config (one-time), seed fail-closed
	defaults into the dedicated doctypes, and seed the template-map rows."""
	if not (frappe.db.exists("DocType", PROVIDER_DT) and frappe.db.exists("DocType", SETTINGS_DT)):
		return
	_migrate_legacy_ts_settings()  # one-time; no-op once the old tab CF is gone
	_seed_provider_defaults()
	_seed_settings_defaults()
	_seed_template_map_rows()


def _singles_has_value(doctype, field):
	"""True if tabSingles already holds a stored value for this Single field."""
	rows = frappe.db.sql(
		"""SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s LIMIT 1""",
		(doctype, field),
	)
	return bool(rows)


def _seed_provider_defaults():
	"""Default provider = WhatsHub360 (only when never set); clear the legacy
	Airtel base_url so the provider's own default base applies."""
	changed = False
	if not _singles_has_value(PROVIDER_DT, "ts_whatsapp_provider"):
		frappe.db.set_single_value(PROVIDER_DT, "ts_whatsapp_provider", "WhatsHub360")
		changed = True
	if frappe.db.get_single_value(PROVIDER_DT, "ts_whatsapp_base_url") == LEGACY_AIRTEL_BASE:
		frappe.db.set_single_value(PROVIDER_DT, "ts_whatsapp_base_url", "")
		changed = True
	if changed:
		frappe.db.commit()


def _seed_settings_defaults():
	"""Fail-closed switches + numeric defaults (Lesson 227 — set here, not via a
	JSON `default` that would overwrite the stored value on every migrate)."""
	changed = False
	fail_closed = {
		"ts_whatsapp_enabled": 0,        # master kill-switch ships OFF
		"ts_whatsapp_sandbox_mode": 1,   # sandbox ON until explicitly disabled
		"ts_whatsapp_filter_blacklist": 1,
	}
	for field, value in fail_closed.items():
		if not _singles_has_value(SETTINGS_DT, field):
			frappe.db.set_single_value(SETTINGS_DT, field, value)
			changed = True
	plain = {
		"ts_whatsapp_max_retries": 3,
		"ts_whatsapp_l1_hours": 4,
		"ts_whatsapp_l2_hours": 12,
		"ts_whatsapp_l3_hours": 24,
		"ts_whatsapp_qc_overdue_hours": 72,
	}
	for field, value in plain.items():
		cur = frappe.db.get_single_value(SETTINGS_DT, field)
		if cur in (None, "", 0, "0"):
			frappe.db.set_single_value(SETTINGS_DT, field, value)
			changed = True
	if changed:
		frappe.db.commit()


def _seed_template_map_rows():
	"""Ensure one (disabled, blank-ref) row per event_key exists on Settings."""
	doc = frappe.get_doc(SETTINGS_DT)  # Single
	existing = {(r.event_key or "").strip() for r in (doc.get("ts_whatsapp_templates") or [])}
	changed = False
	for key in TEMPLATE_EVENT_KEYS:
		if key not in existing:
			doc.append(
				"ts_whatsapp_templates",
				{"event_key": key, "language": "en", "category": "Utility", "enabled": 0},
			)
			changed = True
	if changed:
		doc.save(ignore_permissions=True)
		frappe.db.commit()


def _migrate_legacy_ts_settings():
	"""One-time: copy WhatsApp config off the old TS Settings tab into the
	dedicated doctypes (values + encrypted secrets), drop the legacy template
	rows, and delete the legacy Custom Fields + orphaned Singles values.
	No-op once the legacy tab Custom Field is gone (idempotent)."""
	if not frappe.db.exists("Custom Field", "TS Settings-tab_whatsapp"):
		return

	# Plain values — copy only when the target is still empty (re-run safe).
	for field in _PROVIDER_VALUE_FIELDS:
		old = frappe.db.get_single_value("TS Settings", field)
		if old not in (None, "") and frappe.db.get_single_value(PROVIDER_DT, field) in (None, ""):
			frappe.db.set_single_value(PROVIDER_DT, field, old)
	for field in _SETTINGS_VALUE_FIELDS:
		old = frappe.db.get_single_value("TS Settings", field)
		if old not in (None, "") and not _singles_has_value(SETTINGS_DT, field):
			frappe.db.set_single_value(SETTINGS_DT, field, old)

	# Encrypted secrets (Password fields).
	_migrate_password("TS Settings", "ts_whatsapp_auth_token", PROVIDER_DT)
	_migrate_password("TS Settings", "ts_whatsapp_callback_secret", SETTINGS_DT)

	# Legacy template rows were parented to TS Settings; drop them — fresh blank
	# rows are seeded onto TS WhatsApp Settings by _seed_template_map_rows().
	frappe.db.delete("TS WhatsApp Template Map", {"parenttype": "TS Settings"})

	# Delete the legacy Custom Fields (the tab + every ts_whatsapp_* field).
	for cf in frappe.get_all("Custom Field", filters={"dt": "TS Settings"}, fields=["name", "fieldname"]):
		fn = cf.get("fieldname") or ""
		if fn == "tab_whatsapp" or fn.startswith("ts_whatsapp"):
			frappe.delete_doc("Custom Field", cf["name"], ignore_permissions=True, force=True)

	# Clean the now-orphaned Singles values on TS Settings.
	frappe.db.sql("DELETE FROM `tabSingles` WHERE doctype='TS Settings' AND field LIKE 'ts_whatsapp%%'")
	frappe.clear_cache(doctype="TS Settings")
	frappe.db.commit()


def _migrate_password(src_dt, fieldname, dst_dt):
	"""Move an encrypted Password value from one Single to another, only if the
	destination doesn't already have one."""
	try:
		val = frappe.get_cached_doc(src_dt).get_password(fieldname, raise_exception=False)
	except Exception:
		val = None
	if not val:
		return
	try:
		existing = frappe.get_cached_doc(dst_dt).get_password(fieldname, raise_exception=False)
	except Exception:
		existing = None
	if not existing:
		set_encrypted_password(dst_dt, dst_dt, val, fieldname)
