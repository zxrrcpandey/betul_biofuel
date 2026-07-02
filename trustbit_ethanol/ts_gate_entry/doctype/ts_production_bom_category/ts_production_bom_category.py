# Copyright (c) 2026, Trustbit Software
# TS Production BOM Category — config master for the Multi-BOM production flow.
# Links a BOM category to a Department + holds the notification recipients.
# Pure configuration: no stock, no transactions. See project_production_multi_flow.md.

import frappe
from frappe import _
from frappe.model.document import Document


class TSProductionBOMCategory(Document):
	def validate(self):
		self._validate_recipients()

	def _validate_recipients(self):
		"""Each recipient row must resolve to at least one concrete target:
		a User, a Role, or a literal email (In-App/Email) / mobile (WhatsApp)."""
		for row in (self.notification_recipients or []):
			if not (row.active or 0):
				continue
			if row.user or row.role:
				continue
			if row.channel == "WhatsApp" and row.mobile:
				continue
			if row.channel in ("Email", "In-App") and row.email:
				continue
			frappe.throw(_(
				"Recipient row #{0}: set a User or Role — or a literal Email "
				"(for Email) / Mobile (for WhatsApp)."
			).format(row.idx))
