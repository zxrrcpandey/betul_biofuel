# Copyright (c) 2026, Trustbit Software and contributors
# TS WhatsApp Settings — operational config for the WhatsApp integration.
#
# Split out of TS Settings (v2.18.5): the master kill switch, sandbox safety,
# retry/escalation thresholds and the event->template map. Connection details
# (provider + credentials) live on TS WhatsApp Provider. Saving this doctype is
# isolated from TS Settings' unrelated validations (cascade-delete etc.).
#
# The enable-time gate that USED to live on the per-field mandatory_depends_on
# (which can no longer reference the provider creds — they're on another
# doctype) is enforced here in validate(): you cannot flip the master switch ON
# until the active provider has its required credentials and (in sandbox) an
# allowlist. When OFF, saving is completely frictionless.

import frappe
from frappe import _
from frappe.model.document import Document

# Per-provider required credential FIELDS on TS WhatsApp Provider (besides the
# API token, which both providers need). Mirrors ts_whatsapp_providers.PROVIDERS.
_REQUIRED_PROVIDER_FIELDS = {
	"WhatsHub360": [("ts_whatsapp_vendor_uid", "Vendor UID")],
	"Airtel IQ": [("ts_whatsapp_app_id", "App Id"), ("ts_whatsapp_from", "Sender Number")],
}


class TSWhatsAppSettings(Document):
	def validate(self):
		# OFF (the default): nothing to enforce — keep saving frictionless.
		if not self.get("ts_whatsapp_enabled"):
			return

		provider = (
			frappe.db.get_single_value("TS WhatsApp Provider", "ts_whatsapp_provider")
			or "WhatsHub360"
		)
		missing = []

		token = None
		try:
			token = frappe.get_cached_doc("TS WhatsApp Provider").get_password(
				"ts_whatsapp_auth_token", raise_exception=False
			)
		except Exception:
			token = None
		if not token:
			missing.append("API Token")

		for field, label in _REQUIRED_PROVIDER_FIELDS.get(provider, []):
			if not (frappe.db.get_single_value("TS WhatsApp Provider", field) or "").strip():
				missing.append(label)

		if missing:
			frappe.throw(
				_(
					"Cannot enable WhatsApp: the {0} provider is missing required "
					"credentials in TS WhatsApp Provider — {1}. Fill those in first, "
					"then enable."
				).format(provider, ", ".join(missing))
			)

		# Sandbox safety: never go live in sandbox with an empty allowlist.
		if self.get("ts_whatsapp_sandbox_mode") and not (
			self.get("ts_whatsapp_test_numbers") or ""
		).strip():
			frappe.throw(
				_(
					"Cannot enable WhatsApp in Sandbox Mode with an empty Test Numbers "
					"allowlist. Add at least one test number (91XXXXXXXXXX), or turn "
					"Sandbox Mode off."
				)
			)
