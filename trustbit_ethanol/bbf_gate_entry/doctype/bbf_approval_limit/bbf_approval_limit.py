import frappe
from frappe.model.document import Document


class BBFApprovalLimit(Document):
	def validate(self):
		self._validate_unique_level()
		self._validate_limit_hierarchy()

	def _validate_unique_level(self):
		"""Ensure no two active roles share the same approval level."""
		duplicate = frappe.db.exists("BBF Approval Limit", {
			"approval_level": self.approval_level,
			"is_active": 1,
			"name": ["!=", self.name]
		})
		if duplicate:
			frappe.throw(
				f"Approval Level {self.approval_level} is already assigned to {duplicate}. "
				"Each active role must have a unique level."
			)

	def _validate_limit_hierarchy(self):
		"""Warn if limits are not ascending by level (lower levels should have lower limits)."""
		if not self.is_active:
			return

		lower_levels = frappe.get_all("BBF Approval Limit",
			filters={"is_active": 1, "approval_level": ["<", self.approval_level], "name": ["!=", self.name]},
			fields=["role", "approval_level", "approval_limit"]
		)
		for ll in lower_levels:
			# Skip comparison if either is unlimited (0)
			if ll.approval_limit == 0:
				frappe.throw(
					f"Level {ll.approval_level} ({ll.role}) has unlimited approval but is below "
					f"Level {self.approval_level}. Only the highest level should have unlimited approval."
				)
			if self.approval_limit and self.approval_limit < ll.approval_limit:
				frappe.msgprint(
					f"Warning: Level {self.approval_level} limit ({self.approval_limit}) is less than "
					f"Level {ll.approval_level} ({ll.role}) limit ({ll.approval_limit}). "
					"Higher levels should have higher or equal limits.",
					indicator="orange"
				)
