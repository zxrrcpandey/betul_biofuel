# Copyright (c) 2026, Trustbit Software and contributors
# TS WhatsApp Provider — the WhatsApp gateway connection + credentials.
#
# Split out of TS Settings (v2.18.5) so the gateway/credentials save on their
# OWN clean path, never blocked by TS Settings' unrelated validations
# (cascade-delete / post-dated / etc.). The actual "may we send" gate lives on
# TS WhatsApp Settings (kill switch + sandbox); this doctype is connection only.

import frappe
from frappe.model.document import Document


class TSWhatsAppProvider(Document):
	def validate(self):
		# Light hygiene only — keep the form frictionless (the enable-time
		# credential check is enforced on TS WhatsApp Settings).
		if self.get("ts_whatsapp_from"):
			self.ts_whatsapp_from = "".join(ch for ch in str(self.ts_whatsapp_from) if ch.isdigit())
		if self.get("ts_whatsapp_base_url"):
			self.ts_whatsapp_base_url = self.ts_whatsapp_base_url.strip()
