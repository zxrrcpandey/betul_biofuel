# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration (Airtel IQ) — Phase 1a setup / seeders.
#
# Adds the WhatsApp config fields to TS Settings as Custom Fields (lock-safe:
# does not touch the app-owned ts_settings.json), seeds fail-closed defaults
# (Lesson 227 — never let a JSON default clobber an operator's setting on
# migrate), and seeds the event->template map rows (blank template_ids until
# Meta approves them).
#
# Registered in hooks.py after_migrate. Idempotent.

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields as _create_custom_fields

# Default API base (Airtel IQ send path; adapter appends /template/send).
DEFAULT_BASE_URL = "https://iqwhatsapp.airtel.in:443/gateway/airtel-xchange/basic/whatsapp-manager/v1"

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

# Custom Fields added to the TS Settings Single. permlevel 1 = IT Head /
# System Manager; secrets (auth token) permlevel 2 = System Manager only.
# NOTE: the two safety switches (enabled, sandbox_mode) and filter_blacklist
# carry NO `default` here — their initial value is set by the fail-closed
# seeder so a later migrate can never overwrite an operator's choice (L227).
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
			"label": "WhatsApp Connection (Airtel IQ)",
			"insert_after": "tab_whatsapp",
		},
		{
			"fieldname": "ts_whatsapp_enabled",
			"fieldtype": "Check",
			"label": "Enable WhatsApp Notifications",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_section_conn",
			"description": "MASTER ON/OFF switch. When OFF (default) no WhatsApp is ever sent and the system behaves exactly as before. Turn ON only after the Token, App Id, Sender Number and at least one approved template are filled in. Writable by IT Head / System Manager only. Example: keep OFF until setup is complete.",
		},
		{
			"fieldname": "ts_whatsapp_base_url",
			"fieldtype": "Data",
			"label": "API Base URL",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_enabled",
			"description": "Airtel IQ send-API base path; the system automatically adds '/template/send'. Leave the default unless Airtel gives you a different host. Example: https://iqwhatsapp.airtel.in:443/gateway/airtel-xchange/basic/whatsapp-manager/v1",
		},
		{
			"fieldname": "ts_whatsapp_app_id",
			"fieldtype": "Data",
			"label": "App Id (app-id header)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_base_url",
			"description": "REQUIRED to send. The 'app-id' header value Airtel issues for your account (their documentation shows the sample value 'IRONMAN' — Airtel gives you your real one). Example: IRONMAN",
		},
		{
			"fieldname": "ts_whatsapp_auth_token",
			"fieldtype": "Password",
			"label": "Authorization Token (Basic)",
			"permlevel": 2,
			"insert_after": "ts_whatsapp_app_id",
			"description": "REQUIRED to send. The Basic-auth token from Airtel = base64 of 'username:password', sent as the 'Authorization: Basic <token>' header on every call. Stored encrypted; System Manager only. Example: YWxhZGRpbjpvcGVuc2VzYW1l",
		},
		{
			"fieldname": "ts_whatsapp_col_conn",
			"fieldtype": "Column Break",
			"insert_after": "ts_whatsapp_auth_token",
		},
		{
			"fieldname": "ts_whatsapp_customer_id",
			"fieldtype": "Data",
			"label": "Customer Id",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_col_conn",
			"description": "NOT needed to send. Used only by the in-app Template Designer (Phase 3) for Create/Fetch/Edit Template calls. Your Airtel customer/account id. Example: WA_NEW_CONV_2",
		},
		{
			"fieldname": "ts_whatsapp_waba_id",
			"fieldtype": "Data",
			"label": "WABA Id",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_customer_id",
			"description": "NOT needed to send. Used only by the Template Designer (Phase 3). Your WhatsApp Business Account (WABA) id from Airtel/Meta. Example: 102290129912345",
		},
		{
			"fieldname": "ts_whatsapp_subaccount_id",
			"fieldtype": "Data",
			"label": "Sub-Account Id",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_waba_id",
			"description": "NOT needed to send. Used only by the Template Designer (Phase 3). Your Airtel sub-account id. Example: SUBACC_001",
		},
		{
			"fieldname": "ts_whatsapp_from",
			"fieldtype": "Data",
			"label": "Sender Number (91XXXXXXXXXX)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_subaccount_id",
			"description": "REQUIRED to send. Your registered Airtel WhatsApp sender number — country code + 10 digits, NO '+', no spaces. This is the number messages come FROM. Example: 919812345678",
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
			"description": "When ON (default) ONLY the numbers in 'Test Numbers' receive messages; everyone else is skipped and logged. KEEP ON while testing so real staff/suppliers are never messaged by accident. Turn OFF only on production once you are confident. Example: ON on demo, OFF at go-live.",
		},
		{
			"fieldname": "ts_whatsapp_test_numbers",
			"fieldtype": "Small Text",
			"label": "Test Numbers (sandbox allowlist)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_sandbox_mode",
			"description": "While Sandbox Mode is ON, only these numbers can receive messages. Separate with commas or new lines, each as 91XXXXXXXXXX. Example: 919812345678, 919900112233",
		},
		{
			"fieldname": "ts_whatsapp_filter_blacklist",
			"fieldtype": "Check",
			"label": "Respect Blacklist (filterBlacklistNumbers)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_test_numbers",
			"description": "When ON (default) Airtel skips any number on its blacklist / opt-out list (sends 'filterBlacklistNumbers=true'). Leave ON to honour opt-outs.",
		},
		{
			"fieldname": "ts_whatsapp_col_safety",
			"fieldtype": "Column Break",
			"insert_after": "ts_whatsapp_filter_blacklist",
		},
		{
			"fieldname": "ts_whatsapp_callback_secret",
			"fieldtype": "Password",
			"label": "Delivery Webhook Secret",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_col_safety",
			"description": "A secret YOU choose and give to Airtel. Airtel must send it back in the 'X-Callback-Token' header on every delivery-status webhook, so forged callbacks are rejected. Use a long random string. Example: 7f3c9b1e2a4d6f8091a2b3c4d5e6f70a",
		},
		{
			"fieldname": "ts_whatsapp_source_ip_allowlist",
			"fieldtype": "Small Text",
			"label": "Webhook Source IP Allowlist",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_callback_secret",
			"description": "Optional extra security: only these Airtel server IPs may call the delivery webhook. Comma / newline separated. Leave blank if you rely on the secret alone. Example: 13.234.10.20, 65.0.30.40",
		},
		{
			"fieldname": "ts_whatsapp_max_retries",
			"fieldtype": "Int",
			"label": "Max Retries (retryable codes)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_source_ip_allowlist",
			"default": "3",
			"description": "How many times to retry a send that fails with a TEMPORARY error (e.g. rate-limit). 0 = never retry. Default 3. Example: 3",
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
			"description": "FIRST reminder: how many hours a document may sit pending before the approver gets a WhatsApp nudge. (Used by the Phase 1b escalation scheduler.) Default 4. Example: 4",
		},
		{
			"fieldname": "ts_whatsapp_l2_hours",
			"fieldtype": "Int",
			"label": "Reminder L2 (hours)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_l1_hours",
			"default": "12",
			"description": "SECOND reminder: hours pending before a stronger nudge is sent. Default 12. Example: 12",
		},
		{
			"fieldname": "ts_whatsapp_l3_hours",
			"fieldtype": "Int",
			"label": "Reminder L3 - escalate (hours)",
			"permlevel": 1,
			"insert_after": "ts_whatsapp_l2_hours",
			"default": "24",
			"description": "ESCALATION: hours pending before the reminder climbs the chain to leadership (CEO / MD). Default 24. Example: 24",
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
			"description": "How many hours a QC / inspection may stay pending before a WhatsApp nudge fires. Default 72. Example: 72",
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
			"description": "Maps each business event to its Airtel/Meta-approved template. For each row, paste the Airtel Template Id and tick Enabled once Meta approves it. Rows with no id or not enabled are skipped (logged as 'no_template'). The 10 rows are pre-created for you.",
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
	"""True if tabSingles already holds a stored value for this TS Settings field.
	tabSingles has no `modified` column, so raw SQL (mirrors seed_ts_settings)."""
	rows = frappe.db.sql(
		"""SELECT value FROM `tabSingles`
		   WHERE doctype='TS Settings' AND field=%s LIMIT 1""",
		field,
	)
	return bool(rows)


def _seed_whatsapp_defaults():
	"""Seed initial TS Settings values. Writes directly to tabSingles via
	set_single_value (avoids the Single-load default-application ambiguity and
	the TS Settings controller)."""
	if not frappe.db.exists("TS Settings", "TS Settings"):
		return
	changed = False

	# Safety switches — set ONLY when never set before, so a later migrate can
	# never overwrite an operator's intentional setting (Lesson 227).
	fail_closed = {
		"ts_whatsapp_enabled": 0,       # master kill-switch ships OFF
		"ts_whatsapp_sandbox_mode": 1,  # sandbox ON until explicitly disabled
		"ts_whatsapp_filter_blacklist": 1,
	}
	for field, value in fail_closed.items():
		if not _singles_has_value(field):
			frappe.db.set_single_value("TS Settings", field, value)
			changed = True

	# Non-safety defaults — fill when the stored value is empty/zero. These are
	# all non-empty defaults, so once set they are never re-applied.
	plain = {
		"ts_whatsapp_base_url": DEFAULT_BASE_URL,
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
	"""Ensure one (disabled, blank-templateId) row per event_key exists."""
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
