import frappe
from frappe.model.document import Document


class BBFTransportMaster(Document):
	def before_insert(self):
		if not self.transporter_code:
			count = frappe.db.count("BBF Transport Master") + 1
			self.transporter_code = f"TRN-{count:04d}"
