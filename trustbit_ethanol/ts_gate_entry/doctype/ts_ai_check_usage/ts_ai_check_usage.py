"""What the AI used for one checked list: the model, the tokens and the cost. It is its own record so
that only the MD role can read it: a value on the submission itself could be fetched by anyone who can
open that submission. Only the background job writes it. Values from before this doctype existed are
moved in by the patch `patches/v2_83/move_ai_check_usage`."""

import frappe
from frappe import _
from frappe.model.document import Document

from trustbit_ethanol.ts_gate_entry import ts_ai_check as ai


class TSAICheckUsage(Document):
	def before_insert(self):
		if not self.flags.get(ai.SERVER_FLAG):
			frappe.throw(_("Only the check itself writes this record."), frappe.PermissionError)

	def validate(self):
		if not self.is_new():
			frappe.throw(_("This record cannot be changed."), frappe.PermissionError)

	def after_rename(self, *args):
		frappe.throw(_("This record cannot be renamed."), frappe.PermissionError)

