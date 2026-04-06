import frappe
from frappe.model.document import Document


class TSReturnItemTransaction(Document):
	def validate(self):
		"""Prevent editing completed transactions."""
		if not self.is_new() and self.status in ("Completed", "Cancelled"):
			old_status = frappe.db.get_value("TS Return Item Transaction", self.name, "status")
			if old_status in ("Completed", "Cancelled"):
				frappe.throw("Cannot modify a {} transaction".format(old_status))
