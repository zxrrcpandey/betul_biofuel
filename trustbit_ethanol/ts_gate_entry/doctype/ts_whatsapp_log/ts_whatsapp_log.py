# Copyright (c) 2026, Trustbit Software and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# Control-plane fields written only by the adapter (on insert) and the delivery
# webhook (via db.set_value, which bypasses controller hooks). A human editing
# them in the form / via the API is a tamper vector (Lesson 162), so block it.
PROTECTED_FIELDS = (
	"status",
	"skip_reason",
	"provider_message_id",
	"vendor_ack_id",
	"error_code",
	"error_message",
	"retry_count",
	"status_updated_at",
)

VALID_STATUS = {"Queued", "Sent", "Delivered", "Read", "Failed", "Skipped"}


class TSWhatsAppLog(Document):
	def validate(self):
		# Numbers are stored normalized (digits only, 91XXXXXXXXXX). Strip defensively.
		if self.recipient_number:
			digits = "".join(ch for ch in str(self.recipient_number) if ch.isdigit())
			self.recipient_number = digits or None
		if self.status and self.status not in VALID_STATUS:
			frappe.throw(_("Invalid WhatsApp log status: {0}").format(self.status))

	def before_save(self):
		self._block_control_plane_tampering()

	def _block_control_plane_tampering(self):
		# New rows are written by the adapter with ignore_permissions — allow.
		if self.is_new():
			return
		# Server-side mutations set this flag in a try/finally (Lesson 176).
		if getattr(frappe.flags, "in_whatsapp_server", False):
			return
		before = self.get_doc_before_save()
		if not before:
			return
		for field in PROTECTED_FIELDS:
			if (self.get(field) or "") != (before.get(field) or ""):
				frappe.throw(
					_("Field '{0}' is system-managed and cannot be edited manually.").format(field)
				)
