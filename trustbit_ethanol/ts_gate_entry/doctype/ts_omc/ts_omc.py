import frappe
from frappe.model.document import Document


class TSOMC(Document):
	def validate(self):
		"""Normalise the optional short code; keep the master tiny + clean."""
		if self.omc_code:
			self.omc_code = self.omc_code.strip().upper()
