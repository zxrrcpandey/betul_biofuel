import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, escape_html


class TSMRApprovalRoute(Document):
	def validate(self):
		self._validate_steps()
		self._validate_cost_centers()
		self._validate_final_approve()
		self._validate_purpose_exclusive()
		self._validate_strict_steps()

	def _validate_strict_steps(self):
		"""v2.52.0 Strict Step — configuration guard.

		A strict step admits ONLY its own role (and the TS CC Approval Config approvers
		configured at that step) while a Material Request is pending there; the
		later-step rescue is gone by design. So a strict Review step whose configured
		approvers can never act (user disabled, or not holding the step's role) would
		freeze every MR on that Cost Center for everyone. Refuse the save (fail-closed)
		and name the Cost Centers; warn — non-blocking — when Strict is ticked on the
		final step, where it changes nothing (no later step exists to exclude).
		"""
		strict = [s for s in (self.approval_steps or []) if cint(s.get("strict_step"))]
		if not strict:
			return
		steps = sorted(self.approval_steps, key=lambda s: cint(s.step_order))
		last = steps[-1]
		if cint(last.get("strict_step")):
			frappe.msgprint(
				_("Strict Step on the final step ({0}) has no effect: there is no later step to exclude.")
				.format(escape_html(last.role_label or last.role)),
				indicator="orange",
			)
		if not self.cost_centers:
			return  # wildcard / purpose route: no Cost Center list to check
		from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
			get_cc_approvers_for_step,
			get_cc_config,
		)
		configs = {rc.cost_center: get_cc_config(rc.cost_center) for rc in self.cost_centers}
		user_ok = {}  # (user, role) -> enabled AND holds the role; memoised per save

		def _can_act(user, role):
			key = (user, role)
			if key not in user_ok:
				user_ok[key] = bool(cint(frappe.db.get_value("User", user, "enabled"))) and role in frappe.get_roles(user)
			return user_ok[key]

		problems = []
		for step in strict:
			if step is last:
				continue
			for rc in self.cost_centers:
				cfg = configs.get(rc.cost_center)
				if not cfg:
					continue  # no config = role-only fallback: any holder of the step role can act
				approvers = get_cc_approvers_for_step(cfg, step.step_order, route=self)
				if not approvers:
					continue  # no rows at this step = role-only fallback
				if not any(_can_act(u, step.role) for u in approvers):
					# Name the Cost Center + step only: approver identities live in TS CC Approval
					# Config, which not every route writer may read (security scan, LOW).
					problems.append("{0} (step {1} {2})".format(rc.cost_center, step.step_order, step.role_label or step.role))
		if problems:
			frappe.throw(
				_("Strict Step cannot be enabled: on these Cost Centers no configured approver at that step "
				  "is an enabled user holding the step's role, so every Material Request pending there would "
				  "be frozen for everyone:<br>{0}<br>Fix the TS CC Approval Config rows (the user must hold "
				  "the role, or remove the rows to fall back to role-only) or untick Strict Step.")
				.format("<br>".join(escape_html(p) for p in problems)),
				title=_("Strict Step: invalid approvers"),
			)

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
