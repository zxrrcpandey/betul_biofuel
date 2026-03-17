import frappe
from frappe.model.document import Document


class BBFPurchaseCategory(Document):
	def validate(self):
		self._validate_item_groups()

	def _validate_item_groups(self):
		"""Ensure no Item Group is mapped to multiple categories."""
		if not self.item_groups:
			return
		for row in self.item_groups:
			existing = frappe.db.sql("""
				SELECT parent FROM `tabBBF Purchase Category Item`
				WHERE item_group = %s AND parent != %s
			""", (row.item_group, self.name))
			if existing:
				frappe.throw(
					f"Item Group '{row.item_group}' is already mapped to "
					f"category '{existing[0][0]}'. Each Item Group can belong to only one category."
				)
