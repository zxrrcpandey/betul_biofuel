import frappe
from frappe.model.document import Document


class TSQCTemplateMap(Document):
	def validate(self):
		self._validate_template_active()
		self._validate_template_category_matches_item_group()

	def _validate_template_active(self):
		"""The mapped template must be active."""
		if not self.default_template:
			return
		if not frappe.db.get_value("TS QC Template", self.default_template, "is_active"):
			frappe.throw(
				f"QC Template '{self.default_template}' is inactive. "
				f"Activate it first or pick a different template."
			)

	def _validate_template_category_matches_item_group(self):
		"""
		Soft check: warn (not throw) if the template's category doesn't obviously match the
		item group. We don't enforce hard binding because item_group → category mapping is
		owned by TS Purchase Category and may evolve. Just emit a msgprint guidance.
		"""
		if not (self.default_template and self.item_group):
			return
		template_category = frappe.db.get_value(
			"TS QC Template", self.default_template, "category"
		)
		if not template_category:
			return
		# Look up purchase category for this item_group (best-effort)
		matched_category = frappe.db.sql(
			"""
			SELECT pc.name
			FROM `tabTS Purchase Category` pc
			JOIN `tabTS Purchase Category Item` pci ON pci.parent = pc.name
			WHERE pci.item_group = %s
			LIMIT 1
			""",
			(self.item_group,),
			as_dict=True,
		)
		if not matched_category:
			return
		# Best-effort name match (case-insensitive substring)
		if template_category.lower() not in matched_category[0]["name"].lower():
			frappe.msgprint(
				f"Note: Template category '{template_category}' may not match "
				f"Item Group '{self.item_group}' (mapped to Purchase Category "
				f"'{matched_category[0]['name']}'). Please verify.",
				alert=True,
				indicator="orange",
			)
