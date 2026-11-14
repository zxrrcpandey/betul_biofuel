"""One line of a payment list as the checker read it. Rows are written only together with their report."""

import frappe
from frappe import _
from frappe.model.document import Document

FLAG = "ai_check_server_write"  # the same flag the report asks for


def refuse_alone(row):
	"""A child row saved or deleted on its own (REST /api/resource/<child>/<row>) skips the report's
	refusal. A parent save never runs a child's validate/on_trash, so refusing here blocks only that route."""
	if not row.flags.get(FLAG):
		frappe.throw(_("Change this row from its report."), frappe.PermissionError)


class TSAICheckLine(Document):
	def validate(self):
		refuse_alone(self)

	def on_trash(self):
		refuse_alone(self)
