import frappe
from frappe.model.document import Document
from frappe.utils import getdate


class TSVehicleMaster(Document):
	def validate(self):
		if self.is_blacklisted and self.has_value_changed("is_blacklisted"):
			self.blacklisted_by = frappe.session.user
			self.blacklisted_on = getdate()
		elif not self.is_blacklisted:
			self.blacklisted_by = None
			self.blacklisted_on = None
			self.blacklist_reason = None
