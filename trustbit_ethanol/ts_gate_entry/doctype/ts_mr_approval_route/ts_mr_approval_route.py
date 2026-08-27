import frappe
from frappe.model.document import Document


class TSMRApprovalRoute(Document):
	def validate(self):
		self._validate_steps()
		self._validate_cost_centers()
		self._validate_final_approve()
		self._validate_purpose_exclusive()

	def _validate_steps(self):
		"""Ensure steps are ordered and have unique step_order."""
		if not self.approval_steps:
			frappe.throw("At least one approval step is required.")
		orders = []
		for step in self.approval_steps:
			if step.step_order in orders:
				frappe.throw(f"Duplicate step order: {step.step_order}. Each step must have a unique order.")
			orders.append(step.step_order)

	def _validate_cost_centers(self):
		"""Ensure no Cost Center is mapped to multiple active routes."""
		if not self.cost_centers:
			return
		for row in self.cost_centers:
			existing = frappe.db.sql("""
				SELECT parent FROM `tabTS MR Route Cost Center`
				WHERE cost_center = %s AND parent != %s
			""", (row.cost_center, self.name))
			if existing:
				route = existing[0][0]
				is_active = frappe.db.get_value("TS MR Approval Route", route, "is_active")
				if is_active:
					frappe.throw(
						f"Cost Center '{row.cost_center}' is already mapped to "
						f"active route '{route}'. Each Cost Center can belong to only one active route."
					)

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

	def _validate_purpose_exclusive(self):
		"""v2.46 RGP A1 — at most ONE active route per applies_to_purpose value.
		Keeps the purpose-scoped lookup pass total and deterministic (the CC-based
		exclusivity in _validate_cost_centers cannot cover a wildcard route, which
		has no cost_centers rows). Relax deliberately if CC-explicit purpose routes
		are ever introduced — never by deleting this check."""
		if not self.applies_to_purpose:
			return
		# Role-matching keys CC rows on the step's role — a repeated role would
		# collapse two chain tiers into one CC gate (security INFO), so purpose
		# routes additionally require distinct roles per step.
		roles = [s.role for s in (self.approval_steps or [])]
		if len(roles) != len(set(roles)):
			frappe.throw(
				"A purpose-scoped route must not repeat a role across steps "
				"(approver matching is role-based for these routes)."
			)
		if not self.is_active:
			return
		clash = frappe.db.get_value(
			"TS MR Approval Route",
			{
				"applies_to_purpose": self.applies_to_purpose,
				"is_active": 1,
				"name": ("!=", self.name),
			},
			"name",
		)
		if clash:
			frappe.throw(
				f"Route '{clash}' is already active for purpose '{self.applies_to_purpose}'. "
				"Only one active route per purpose is allowed — deactivate it first."
			)
