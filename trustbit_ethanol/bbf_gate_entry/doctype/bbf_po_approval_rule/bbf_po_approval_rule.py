import frappe
from frappe.model.document import Document
from frappe.utils import flt


class BBFPOApprovalRule(Document):
	def validate(self):
		self._validate_steps()
		self._validate_amount_range()
		self._validate_final_approve()

	def _validate_steps(self):
		"""Ensure steps are ordered and have unique step_order."""
		if not self.approval_steps:
			frappe.throw("At least one approval step is required.")
		orders = []
		for step in self.approval_steps:
			if step.step_order in orders:
				frappe.throw(f"Duplicate step order: {step.step_order}. Each step must have a unique order.")
			orders.append(step.step_order)

	def _validate_amount_range(self):
		"""Validate min_amount < max_amount if both are set."""
		if flt(self.min_amount) and flt(self.max_amount):
			if flt(self.min_amount) > flt(self.max_amount):
				frappe.throw("Min Amount cannot be greater than Max Amount.")

	def _validate_final_approve(self):
		"""Ensure the last step is Final Approve."""
		if not self.approval_steps:
			return
		steps = sorted(self.approval_steps, key=lambda s: s.step_order)
		last_step = steps[-1]
		if last_step.action_type != "Final Approve":
			frappe.throw(
				f"The last step (Step {last_step.step_order}: {last_step.role_label or last_step.role}) "
				"must have action type 'Final Approve'."
			)
