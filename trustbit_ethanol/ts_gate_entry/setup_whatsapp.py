# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration — multi-adapter setup / seeders.
#
# Adds the WhatsApp config fields to TS Settings as Custom Fields (lock-safe:
# does not touch the app-owned ts_settings.json). The config is PROVIDER-AWARE:
# a Provider selector (WhatsHub360 | Airtel IQ) shows/requires only the fields
# that provider needs. Fail-closed defaults (Lesson 227) + event->template map.
#
# Registered in hooks.py after_migrate. Idempotent.

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields as _create_custom_fields

# Legacy value seeded by Phase 1a (when Airtel was the only provider). base_url
# is now an optional override; clear this so the provider default applies.
LEGACY_AIRTEL_BASE = "https://iqwhatsapp.airtel.in:443/gateway/airtel-xchange/basic/whatsapp-manager/v1"

# The HYBRID template set (user decision 20 Jun) — 6 custom PO/MR + 4 shared.
TEMPLATE_EVENT_KEYS = (
	"po_needs_approval",
	"po_approved",
	"po_rejected",
	"mr_needs_approval",
	"mr_approved",
	"mr_rejected",
	"reminder_l1",
	"reminder_l2",
	"reminder_l3",
	"reminder_generic",
)

_WH360 = "eval:doc.ts_whatsapp_provider=='WhatsHub360'"
_AIRTEL = "eval:doc.ts_whatsapp_provider=='Airtel IQ'"

# Custom Fields on TS Settings. permlevel 1 = IT Head / System Manager; the
# token (secret) is permlevel 2 = System Manager only. The two safety switches
# (enabled, sandbox_mode), filter_blacklist and provider carry NO JSON `default`
# — their initial value is set by the fail-closed seeder (Lesson 227).
WHATSAPP_TS_SETTINGS_FIELDS = {
	"TS Settings": [
		{
			"fieldname": "tab_whatsapp",
			"fieldtype": "Tab Break",
			"label": "WhatsApp",
			"insert_after": "ts_production_byproduct_warehouse",
		},
		{
			"fieldname": "ts_whatsapp_section_conn",
			"fieldtype": "Section Break",
			"label": "WhatsApp Connection",
			"insert_after": "tab_whatsapp",
		},
		{
			"fieldname": "ts_whatsapp_enabled",
			"fieldtype": "Check",
			"label": "Enable WhatsApp Notifications",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_section_conn",
			"description": "MASTER ON/OFF switch. When OFF (default) no WhatsApp is ever sent and the system behaves exactly as before. Turn ON only after the provider credentials and at least one approved template are filled in. Writable by IT Head / System Manager only.",
		},
		{
			"fieldname": "ts_whatsapp_provider",
			"fieldtype": "Select",
			"label": "Provider",
			"options": "WhatsHub360\nAirtel IQ",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_enabled",
			"description": "Choose your WhatsApp gateway. The credential fields below change to match the provider you pick. Default: WhatsHub360.",
		},
		{
			"fieldname": "ts_whatsapp_base_url",
			"fieldtype": "Data",
			"label": "API Base URL (optional override)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_provider",
			"description": "Leave BLANK to use the selected provider's default API URL. Only set this if your provider gave you a custom host. WhatsHub360 default: https://app.whatshub360.com/api",
		},
		{
			"fieldname": "ts_whatsapp_auth_token",
			"fieldtype": "Password",
			"label": "API Token",
			"permlevel": 2,
			"insert_after": "ts_whatsapp_base_url",
			"mandatory_depends_on": "eval:doc.ts_whatsapp_enabled",
			"description": "REQUIRED to send — mandatory once WhatsApp is enabled. WhatsHub360: your API Access Token (sent as 'Authorization: Bearer <token>'). Airtel IQ: the Basic-auth token = base64('username:password'). Stored encrypted; System Manager only.",
		},
		{
			"fieldname": "ts_whatsapp_vendor_uid",
			"fieldtype": "Data",
			"label": "Vendor UID (WhatsHub360)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_auth_token",
			"depends_on": _WH360,
			"mandatory_depends_on": "eval:doc.ts_whatsapp_enabled && doc.ts_whatsapp_provider=='WhatsHub360'",
			"description": "REQUIRED for WhatsHub360. Your Vendor UID — it forms part of the send URL (/api/<vendor_uid>/contact/send-message). Example: 5a712c48-05cd-411f-8444-ab9096cae95e",
		},
		{
			"fieldname": "ts_whatsapp_from_phone_number_id",
			"fieldtype": "Data",
			"label": "From Phone Number Id (WhatsHub360, optional)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_vendor_uid",
			"depends_on": _WH360,
			"description": "Optional (WhatsHub360). The phone-number id to send FROM; if blank, your account's default sender is used.",
		},
		{
			"fieldname": "ts_whatsapp_app_id",
			"fieldtype": "Data",
			"label": "App Id (Airtel IQ)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_from_phone_number_id",
			"depends_on": _AIRTEL,
			"mandatory_depends_on": "eval:doc.ts_whatsapp_enabled && doc.ts_whatsapp_provider=='Airtel IQ'",
			"description": "REQUIRED for Airtel IQ. The 'app-id' header value Airtel issues for your account. Example: IRONMAN",
		},
		{
			"fieldname": "ts_whatsapp_col_conn",
			"fieldtype": "Column Break",
			"insert_after": "ts_whatsapp_app_id",
		},
		{
			"fieldname": "ts_whatsapp_customer_id",
			"fieldtype": "Data",
			"label": "Customer Id (Airtel IQ)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_col_conn",
			"depends_on": _AIRTEL,
			"description": "Airtel IQ only. Used by the in-app Template Designer (Phase 3); not needed to send. Example: WA_NEW_CONV_2",
		},
		{
			"fieldname": "ts_whatsapp_waba_id",
			"fieldtype": "Data",
			"label": "WABA Id (Airtel IQ)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_customer_id",
			"depends_on": _AIRTEL,
			"description": "Airtel IQ only. WhatsApp Business Account id. Used by the Template Designer (Phase 3); not needed to send.",
		},
		{
			"fieldname": "ts_whatsapp_subaccount_id",
			"fieldtype": "Data",
			"label": "Sub-Account Id (Airtel IQ)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_waba_id",
			"depends_on": _AIRTEL,
			"description": "Airtel IQ only. Used by the Template Designer (Phase 3); not needed to send.",
		},
		{
			"fieldname": "ts_whatsapp_from",
			"fieldtype": "Data",
			"label": "Sender Number (Airtel IQ)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_subaccount_id",
			"depends_on": _AIRTEL,
			"mandatory_depends_on": "eval:doc.ts_whatsapp_enabled && doc.ts_whatsapp_provider=='Airtel IQ'",
			"description": "REQUIRED for Airtel IQ. Registered sender number — country code + 10 digits, NO '+'. Example: 919812345678",
		},
		{
			"fieldname": "ts_whatsapp_section_safety",
			"fieldtype": "Section Break",
			"label": "Safety & Sandbox",
			"insert_after": "ts_whatsapp_from",
		},
		{
			"fieldname": "ts_whatsapp_sandbox_mode",
			"fieldtype": "Check",
			"label": "Sandbox Mode (only message test numbers)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_section_safety",
			"description": "When ON (default) ONLY the numbers in 'Test Numbers' receive messages; everyone else is skipped and logged. KEEP ON while testing so real staff/suppliers are never messaged by accident. Turn OFF only on production once you are confident.",
		},
		{
			"fieldname": "ts_whatsapp_test_numbers",
			"fieldtype": "Small Text",
			"label": "Test Numbers (sandbox allowlist)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_sandbox_mode",
			"mandatory_depends_on": "eval:doc.ts_whatsapp_enabled && doc.ts_whatsapp_sandbox_mode",
			"description": "While Sandbox Mode is ON, only these numbers can receive messages. Mandatory when enabled AND sandbox is on, so you can't go live in sandbox with an empty allowlist. Comma / newline separated, each 91XXXXXXXXXX. Example: 919812345678, 919900112233",
		},
		{
			"fieldname": "ts_whatsapp_filter_blacklist",
			"fieldtype": "Check",
			"label": "Respect Blacklist",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_test_numbers",
			"description": "When ON (default) the provider skips numbers on its blacklist / opt-out list. Leave ON to honour opt-outs. (Used by Airtel's filterBlacklistNumbers.)",
		},
		{
			"fieldname": "ts_whatsapp_col_safety",
			"fieldtype": "Column Break",
			"insert_after": "ts_whatsapp_filter_blacklist",
		},
		{
			"fieldname": "ts_whatsapp_callback_secret",
			"fieldtype": "Password",
			"label": "Delivery / Inbound Webhook Secret",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_col_safety",
			"description": "A secret YOU choose and give to your provider; it must be sent back (X-Callback-Token header) on webhooks, so forged callbacks are rejected. Use a long random string.",
		},
		{
			"fieldname": "ts_whatsapp_source_ip_allowlist",
			"fieldtype": "Small Text",
			"label": "Webhook Source IP Allowlist",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_callback_secret",
			"description": "Optional: only these provider server IPs may call the webhook. Comma / newline separated. Leave blank to rely on the secret alone.",
		},
		{
			"fieldname": "ts_whatsapp_max_retries",
			"fieldtype": "Int",
			"label": "Max Retries (transient errors)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_source_ip_allowlist",
			"default": "3",
			"description": "How many times to retry a send that fails with a TEMPORARY error (e.g. rate-limit / 5xx). 0 = never retry. Default 3.",
		},
		{
			"fieldname": "ts_whatsapp_section_esc",
			"fieldtype": "Section Break",
			"label": "Reminder Escalation Thresholds (hours)",
			"insert_after": "ts_whatsapp_max_retries",
		},
		{
			"fieldname": "ts_whatsapp_l1_hours",
			"fieldtype": "Int",
			"label": "Reminder L1 (hours)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_section_esc",
			"default": "4",
			"description": "FIRST reminder: hours a document may sit pending before the approver gets a WhatsApp nudge. Default 4.",
		},
		{
			"fieldname": "ts_whatsapp_l2_hours",
			"fieldtype": "Int",
			"label": "Reminder L2 (hours)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_l1_hours",
			"default": "12",
			"description": "SECOND reminder: hours pending before a stronger nudge. Default 12.",
		},
		{
			"fieldname": "ts_whatsapp_l3_hours",
			"fieldtype": "Int",
			"label": "Reminder L3 - escalate (hours)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_l2_hours",
			"default": "24",
			"description": "ESCALATION: hours pending before the reminder climbs to leadership (CEO / MD). Default 24.",
		},
		{
			"fieldname": "ts_whatsapp_col_esc",
			"fieldtype": "Column Break",
			"insert_after": "ts_whatsapp_l3_hours",
		},
		{
			"fieldname": "ts_whatsapp_qc_overdue_hours",
			"fieldtype": "Int",
			"label": "QC Stuck Threshold (hours)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_col_esc",
			"default": "72",
			"description": "Hours a QC / inspection may stay pending before a WhatsApp nudge fires. Default 72.",
		},
		{
			"fieldname": "ts_whatsapp_section_tpl",
			"fieldtype": "Section Break",
			"label": "Template Map",
			"insert_after": "ts_whatsapp_qc_overdue_hours",
		},
		{
			"fieldname": "ts_whatsapp_templates",
			"fieldtype": "Table",
			"label": "WhatsApp Templates",
			"options": "TS WhatsApp Template Map",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_section_tpl",
			"description": "Maps each business event to its approved template. Paste the provider template reference (WhatsHub360: template NAME; Airtel: templateId) and tick Enabled once it's approved. Rows with no ref / not enabled are skipped. The 10 rows are pre-created.",
		},
	]
}


def seed_whatsapp_settings_fields():
	"""after_migrate: create the WhatsApp Custom Fields on TS Settings, seed
	fail-closed defaults, and seed the template-map rows. Idempotent."""
	if not frappe.db.exists("DocType", "TS Settings"):
		return
	_create_custom_fields(WHATSAPP_TS_SETTINGS_FIELDS, ignore_validate=True)
	_seed_whatsapp_defaults()
	_seed_template_map_rows()


def _singles_has_value(field):
	"""True if tabSingles already holds a stored value for this TS Settings field."""
	rows = frappe.db.sql(
		"""SELECT value FROM `tabSingles`
		   WHERE doctype='TS Settings' AND field=%s LIMIT 1""",
		field,
	)
	return bool(rows)


def _seed_whatsapp_defaults():
	"""Seed initial TS Settings values via set_single_value (avoids the Single
	default-application ambiguity and the TS Settings controller)."""
	if not frappe.db.exists("TS Settings", "TS Settings"):
		return
	changed = False

	# Switches / provider — set ONLY when never set before (Lesson 227).
	fail_closed = {
		"ts_whatsapp_enabled": 0,            # master kill-switch ships OFF
		"ts_whatsapp_sandbox_mode": 1,       # sandbox ON until explicitly disabled
		"ts_whatsapp_filter_blacklist": 1,
		"ts_whatsapp_provider": "WhatsHub360",  # default provider
	}
	for field, value in fail_closed.items():
		if not _singles_has_value(field):
			frappe.db.set_single_value("TS Settings", field, value)
			changed = True

	# base_url is now an OPTIONAL override; clear the legacy Phase-1a Airtel value
	# so the selected provider's default base applies. A custom override won't
	# match LEGACY_AIRTEL_BASE and is preserved.
	if frappe.db.get_single_value("TS Settings", "ts_whatsapp_base_url") == LEGACY_AIRTEL_BASE:
		frappe.db.set_single_value("TS Settings", "ts_whatsapp_base_url", "")
		changed = True

	# Numeric defaults — fill when the stored value is empty/zero.
	plain = {
		"ts_whatsapp_max_retries": 3,
		"ts_whatsapp_l1_hours": 4,
		"ts_whatsapp_l2_hours": 12,
		"ts_whatsapp_l3_hours": 24,
		"ts_whatsapp_qc_overdue_hours": 72,
	}
	for field, value in plain.items():
		cur = frappe.db.get_single_value("TS Settings", field)
		if cur in (None, "", 0, "0"):
			frappe.db.set_single_value("TS Settings", field, value)
			changed = True

	if changed:
		frappe.db.commit()


def _seed_template_map_rows():
	"""Ensure one (disabled, blank-ref) row per event_key exists."""
	if not frappe.db.exists("TS Settings", "TS Settings"):
		return
	doc = frappe.get_doc("TS Settings")
	if not hasattr(doc, "ts_whatsapp_templates"):
		return
	existing = {(r.event_key or "").strip() for r in (doc.ts_whatsapp_templates or [])}
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
