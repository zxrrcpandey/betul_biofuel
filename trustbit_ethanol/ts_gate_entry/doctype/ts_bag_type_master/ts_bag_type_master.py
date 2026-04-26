import frappe
from frappe.model.document import Document


class TSBagTypeMaster(Document):
	def validate(self):
		self._validate_weights()

	def _validate_weights(self):
		"""Standard weight must be > 0 if provided; tolerance cannot be negative."""
		if self.standard_weight_kg is not None and self.standard_weight_kg < 0:
			frappe.throw("Standard Weight (Kg) cannot be negative.")
		if self.tolerance_kg is not None and self.tolerance_kg < 0:
			frappe.throw("Tolerance (Kg) cannot be negative.")
