"""One payment list sent for checking. The controller is thin on purpose: the rules live in
`ts_ai_check`, so the Submit path and the background job read the same code."""

import frappe
from frappe import _
from frappe.model.document import Document

from trustbit_ethanol.ts_gate_entry import ts_ai_check as ai


class TSAICheckSubmission(Document):
	def before_insert(self):
		self.flags.name_set = False  # a request may not pick the name

	def onload(self):
		ai.on_form_load(self)

	def validate(self):
		# runs on Save and on Submit (docstatus is already 1 there), never after submit
		old = self.get_doc_before_save()
		if old and self.owner != old.owner:
			# "if owner" rights are judged on the owner the REQUEST sends: core tests Write before it reads
			# the stored row. A request that changed the owner would take over another person's draft.
			self.raise_no_permission_to("write")
		ai.reset_control_fields(self)
		ai.check_file_field(self)

	def before_submit(self):
		ai.before_submit(self)

	def on_submit(self):
		ai.start(self)

	def before_update_after_submit(self):
		frappe.throw(_("A submitted list cannot be changed. Submit a new one."))

	def before_cancel(self):
		frappe.throw(_("A submitted list cannot be cancelled. Submit a new one."))

	def after_rename(self, *args):
		frappe.throw(_("Submissions cannot be renamed."), frappe.PermissionError)
