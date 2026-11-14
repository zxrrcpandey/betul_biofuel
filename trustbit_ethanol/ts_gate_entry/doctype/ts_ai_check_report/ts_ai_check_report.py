"""What the checker read from one submitted payment list. Only the background job may make a report,
and nobody may change one: it is the record of what was read, kept apart from the submitter's form."""

import frappe
from frappe import _
from frappe.model.document import Document

FLAG = "ai_check_server_write"  # set by the job (`ts_ai_check._save_report`); a request cannot set a flag


class TSAICheckReport(Document):
	def before_insert(self):
		if not self.flags.get(FLAG):
			frappe.throw(_("Reports are made by the checker only."), frappe.PermissionError)

	def autoname(self):
		self.name = self.submission  # one report per submission; a second run cannot make another

	def validate(self):
		if not self.flags.get(FLAG):
			frappe.throw(_("A report cannot be changed."), frappe.PermissionError)

	def after_rename(self, *args):
		frappe.throw(_("Reports cannot be renamed."), frappe.PermissionError)
