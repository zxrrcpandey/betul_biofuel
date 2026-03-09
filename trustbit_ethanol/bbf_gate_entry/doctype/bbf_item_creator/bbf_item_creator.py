import frappe
from frappe.model.document import Document


class BBFItemCreator(Document):
	def before_save(self):
		self._fetch_codes()
		if not self.serial_number:
			self._assign_serial()
		self._build_item_code()

	def _fetch_codes(self):
		"""Fetch company and category codes based on selected code type."""
		if self.company:
			if self.company_code_type == "Numerical":
				self.company_code = frappe.db.get_value(
					"Company", self.company, "company_num_code"
				) or ""
			else:
				self.company_code = frappe.db.get_value(
					"Company", self.company, "company_code"
				) or ""

		if self.item_group:
			if self.category_code_type == "Numerical":
				self.category_code = frappe.db.get_value(
					"Item Group", self.item_group, "category_num_code"
				) or ""
			else:
				self.category_code = frappe.db.get_value(
					"Item Group", self.item_group, "category_code"
				) or ""

		# Fetch variant codes
		if self.has_variant:
			if self.variant_source == "Brand" and self.brand:
				self.brand_code = frappe.db.get_value(
					"Brand", self.brand, "brand_code"
				) or ""
			elif self.variant_source == "Custom Variant" and self.variant:
				self.variant_code = frappe.db.get_value(
					"BBF Variant", self.variant, "variant_code"
				) or ""

	def _assign_serial(self):
		"""Get next serial number for this company + category combination."""
		if not self.company_code or not self.category_code:
			return

		settings = frappe.get_single("BBF Item Code Settings")
		digits = max(int(settings.serial_digits or 3), 2)

		# Use character codes as the counter key (consistent regardless of display type)
		company_key = frappe.db.get_value("Company", self.company, "company_code") or self.company_code
		category_key = frappe.db.get_value("Item Group", self.item_group, "category_code") or self.category_code

		# Find or create counter row
		counter_row = None
		for row in settings.counters:
			if row.company_code == company_key and row.category_code == category_key:
				counter_row = row
				break

		if counter_row:
			counter_row.last_serial = (counter_row.last_serial or 0) + 1
			next_serial = counter_row.last_serial
		else:
			settings.append("counters", {
				"company_code": company_key,
				"category_code": category_key,
				"last_serial": 1
			})
			next_serial = 1

		settings.save(ignore_permissions=True)
		self.serial_number = str(next_serial).zfill(digits)

	def _build_item_code(self):
		"""Build the generated item code from all parts."""
		if not self.company_code or not self.category_code or not self.serial_number:
			return

		settings = frappe.get_single("BBF Item Code Settings")
		sep = settings.default_separator or "-"

		parts = [self.company_code, self.category_code, self.serial_number]

		if self.has_variant:
			v_code = ""
			if self.variant_source == "Brand" and self.brand_code:
				v_code = self.brand_code
			elif self.variant_source == "Custom Variant" and self.variant_code:
				v_code = self.variant_code

			if v_code:
				parts.append(v_code)

		self.generated_item_code = sep.join(parts)

	def validate(self):
		if not self.company_code:
			frappe.throw(
				"Company Code not found. Please set <b>company_code</b> "
				"(or <b>company_num_code</b>) on the Company master."
			)
		if not self.category_code:
			frappe.throw(
				"Category Code not found. Please set <b>category_code</b> "
				"(or <b>category_num_code</b>) on the Item Group master."
			)

		if self.has_variant:
			if self.variant_source == "Brand" and not self.brand:
				frappe.throw("Please select a Brand or uncheck 'Has Variant'")
			if self.variant_source == "Brand" and self.brand and not self.brand_code:
				frappe.throw(
					f"Brand Code not found for <b>{self.brand}</b>. "
					"Please set <b>brand_code</b> on the Brand master."
				)
			if self.variant_source == "Custom Variant" and not self.variant:
				frappe.throw("Please select a Custom Variant or uncheck 'Has Variant'")

	@frappe.whitelist()
	def create_item(self):
		"""Create an Item in ERPNext from the generated item code."""
		# Idempotency guard
		if self.item_created:
			frappe.throw(
				f"Item <b>{self.item_created}</b> already created from this record."
			)

		if not self.generated_item_code:
			frappe.throw("Generated Item Code is empty. Please save the form first.")

		# Check if item code already exists
		if frappe.db.exists("Item", self.generated_item_code):
			frappe.throw(
				f"Item with code <b>{self.generated_item_code}</b> already exists in ERPNext."
			)

		if self.has_variant:
			result = self._create_variant_item()
		else:
			result = self._create_standalone_item()

		# Update this record
		self.item_created = result["item_code"]
		self.template_item = result.get("template_code", "")
		self.status = "Created"
		self.save(ignore_permissions=True)

		frappe.msgprint(
			result["message"],
			title="Item Created",
			indicator="green"
		)

		return result

	def _create_standalone_item(self):
		"""Create a standalone item (no variant)."""
		item = frappe.get_doc({
			"doctype": "Item",
			"item_code": self.generated_item_code,
			"item_name": self.item_name or self.generated_item_code,
			"item_group": self.item_group,
			"stock_uom": self.stock_uom,
			"description": self.description or self.item_name or self.generated_item_code,
		})
		item.insert(ignore_permissions=True)

		return {
			"item_code": item.name,
			"message": f"Item <b>{item.name}</b> created successfully."
		}

	def _create_variant_item(self):
		"""Create a template item (if needed) + variant item using ERPNext's variant system."""
		settings = frappe.get_single("BBF Item Code Settings")
		sep = settings.default_separator or "-"

		# Template code = company-category-serial (without variant part)
		template_code = sep.join([self.company_code, self.category_code, self.serial_number])

		# Get variant code
		if self.variant_source == "Brand" and self.brand_code:
			variant_code_value = self.brand_code
		elif self.variant_source == "Custom Variant" and self.variant_code:
			variant_code_value = self.variant_code
		else:
			frappe.throw("Variant code is missing. Please select a Brand or Custom Variant.")
			return

		# Ensure the Item Attribute "BBF Variant Code" exists
		attr_name = _ensure_variant_attribute(variant_code_value)

		# Step 1: Create or reuse template item
		if not frappe.db.exists("Item", template_code):
			template = frappe.get_doc({
				"doctype": "Item",
				"item_code": template_code,
				"item_name": self.item_name or template_code,
				"item_group": self.item_group,
				"stock_uom": self.stock_uom,
				"description": self.description or self.item_name or template_code,
				"has_variants": 1,
				"variant_based_on": "Item Attribute",
				"attributes": [{"attribute": attr_name}],
			})
			template.insert(ignore_permissions=True)
			template_msg = f"Template <b>{template_code}</b> created. "
		else:
			# Template exists - ensure it has has_variants=1 and our attribute
			template = frappe.get_doc("Item", template_code)
			if not template.has_variants:
				frappe.throw(
					f"Item <b>{template_code}</b> already exists but is not a template item. "
					"Cannot create variant under it."
				)

			# Add our attribute if not already present
			attr_exists = any(
				row.attribute == attr_name for row in template.attributes
			)
			if not attr_exists:
				template.append("attributes", {"attribute": attr_name})
				template.save(ignore_permissions=True)

			template_msg = f"Using existing template <b>{template_code}</b>. "

		# Step 2: Create variant item
		variant_item = frappe.get_doc({
			"doctype": "Item",
			"item_code": self.generated_item_code,
			"item_name": (self.item_name or template_code) + sep + variant_code_value,
			"item_group": self.item_group,
			"stock_uom": self.stock_uom,
			"description": self.description or self.item_name or self.generated_item_code,
			"variant_of": template_code,
			"variant_based_on": "Item Attribute",
			"attributes": [{
				"attribute": attr_name,
				"attribute_value": variant_code_value,
			}],
		})

		# Set brand if variant source is Brand
		if self.variant_source == "Brand" and self.brand:
			variant_item.brand = self.brand

		variant_item.insert(ignore_permissions=True)

		return {
			"item_code": variant_item.name,
			"template_code": template_code,
			"message": template_msg + f"Variant <b>{self.generated_item_code}</b> created successfully."
		}


def _ensure_variant_attribute(variant_code_value):
	"""Ensure the 'BBF Variant Code' Item Attribute exists with the given value."""
	attr_name = "BBF Variant Code"

	if not frappe.db.exists("Item Attribute", attr_name):
		attr = frappe.get_doc({
			"doctype": "Item Attribute",
			"attribute_name": attr_name,
			"item_attribute_values": [{
				"attribute_value": variant_code_value,
				"abbr": variant_code_value,
			}],
		})
		attr.insert(ignore_permissions=True)
	else:
		attr = frappe.get_doc("Item Attribute", attr_name)
		# Add the value if not already present
		value_exists = any(
			row.attribute_value == variant_code_value
			for row in attr.item_attribute_values
		)
		if not value_exists:
			attr.append("item_attribute_values", {
				"attribute_value": variant_code_value,
				"abbr": variant_code_value,
			})
			attr.save(ignore_permissions=True)

	return attr_name


@frappe.whitelist()
def get_next_serial_preview(company, item_group):
	"""Get the next serial number for preview (without incrementing)."""
	settings = frappe.get_single("BBF Item Code Settings")
	digits = max(int(settings.serial_digits or 3), 2)

	company_key = frappe.db.get_value("Company", company, "company_code") or ""
	category_key = frappe.db.get_value("Item Group", item_group, "category_code") or ""

	if not company_key or not category_key:
		return "001"

	for row in settings.counters:
		if row.company_code == company_key and row.category_code == category_key:
			return str((row.last_serial or 0) + 1).zfill(digits)

	return str(1).zfill(digits)
