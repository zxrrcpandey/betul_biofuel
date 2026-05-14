import re

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowtime


def derive_fy_short(fiscal_year):
	"""Convert 'YYYY-YYYY' fiscal year name to short form 'YY-YY'.

	Falls back to year_start_date/year_end_date on the Fiscal Year doc if the
	name does not match the standard pattern.
	"""
	if not fiscal_year:
		return ""
	name = str(fiscal_year).strip()
	match = re.match(r"^(\d{4})-(\d{4})$", name)
	if match:
		return match.group(1)[-2:] + "-" + match.group(2)[-2:]
	# Handle 'FY 2025-2026' or similar
	match = re.search(r"(\d{4})\D+(\d{4})", name)
	if match:
		return match.group(1)[-2:] + "-" + match.group(2)[-2:]
	# Last resort: read dates from Fiscal Year DocType
	try:
		fy = frappe.db.get_value(
			"Fiscal Year", fiscal_year, ["year_start_date", "year_end_date"], as_dict=True
		)
		if fy and fy.year_start_date and fy.year_end_date:
			return str(fy.year_start_date.year)[-2:] + "-" + str(fy.year_end_date.year)[-2:]
	except Exception:
		pass
	return name


class TSItemCreator(Document):
	def before_validate(self):
		self._fetch_codes()

	def before_save(self):
		if not self.serial_number:
			self._assign_serial()
		self._build_item_code()

	def _fetch_codes(self):
		"""Fetch company and category codes based on selected code type.
		Skips fetch if codes are already set (e.g. passed from the custom page).
		"""
		if self.company and not self.company_code:
			if self.company_code_type == "Numerical":
				self.company_code = frappe.db.get_value(
					"Company", self.company, "company_num_code"
				) or ""
			else:
				self.company_code = frappe.db.get_value(
					"Company", self.company, "company_code"
				) or ""

		# Fixed Asset path: use Fiscal Year short form as the category key
		if self.creation_type == "Fixed Asset":
			# Derive FY short form (e.g. "2025-2026" -> "25-26")
			if self.fiscal_year:
				self.fiscal_year_short = derive_fy_short(self.fiscal_year)
			else:
				self.fiscal_year_short = ""
			# Override item_group + category_code for asset path.
			# category_code stores the FY short form so downstream serial and
			# code-generation logic can stay generic.
			self.item_group = "Fixed Assets"
			self.category_code = self.fiscal_year_short or ""
			# asset_category_code is no longer used in the code, clear it
			self.asset_category_code = ""
			return

		if self.item_group and not self.category_code:
			if self.category_code_type == "Numerical":
				self.category_code = frappe.db.get_value(
					"Item Group", self.item_group, "category_num_code"
				) or ""
			else:
				self.category_code = frappe.db.get_value(
					"Item Group", self.item_group, "category_code"
				) or ""

		# Fetch variant codes for single-variant fields (backward compat)
		if self.has_variant and not self.variants:
			if self.variant_source == "Brand" and self.brand and not self.brand_code:
				self.brand_code = frappe.db.get_value(
					"Brand", self.brand, "brand_code"
				) or ""
			elif self.variant_source == "Custom Variant" and self.variant and not self.variant_code:
				self.variant_code = frappe.db.get_value(
					"TS Variant", self.variant, "variant_code"
				) or ""

	def _assign_serial(self):
		"""Get next serial number for this company + category combination.
		Uses DB-level locking (for_update) to prevent duplicates in bulk imports."""
		if not self.company_code or not self.category_code:
			return

		# Read settings fresh (clear cache to avoid stale data in bulk imports)
		frappe.clear_document_cache("TS Item Code Settings", "TS Item Code Settings")
		settings = frappe.get_doc("TS Item Code Settings")

		# Fixed Asset format always uses 5 serial digits (FA-BBPL-25-26-00001)
		is_asset = self.creation_type == "Fixed Asset"
		digits = 5 if is_asset else max(int(settings.serial_digits or 3), 2)
		sep = settings.default_separator or "-"

		# Use character codes as the counter key (consistent regardless of display type)
		company_key = frappe.db.get_value("Company", self.company, "company_code") or self.company_code

		# Counter key: Fixed Asset uses "FY_25-26"; Regular uses Item Group code
		if is_asset:
			category_key = "FY_" + (self.fiscal_year_short or self.category_code or "")
		else:
			category_key = frappe.db.get_value("Item Group", self.item_group, "category_code") or self.category_code

		# Get current counter directly from DB (bypass cache completely)
		current_serial = frappe.db.sql("""
			SELECT last_serial FROM `tabTS Code Counter`
			WHERE parent = 'TS Item Code Settings' AND company_code = %s AND category_code = %s
			FOR UPDATE
		""", (company_key, category_key))

		if current_serial:
			next_serial = (current_serial[0][0] or 0) + 1
		else:
			# Create counter row
			settings.append("counters", {
				"company_code": company_key,
				"category_code": category_key,
				"last_serial": 0
			})
			settings.save(ignore_permissions=True)
			next_serial = 1

		# Skip past any existing items (handles previously created items from failed batches)
		while True:
			serial_str = str(next_serial).zfill(digits)
			if is_asset:
				candidate_code = sep.join(["FA", company_key, self.fiscal_year_short or "", serial_str])
			else:
				candidate_code = sep.join([self.company_code, self.category_code, serial_str])
			if not frappe.db.exists("Item", candidate_code):
				break
			next_serial += 1

		# Update counter directly in DB (atomic, no cache issues)
		frappe.db.sql("""
			UPDATE `tabTS Code Counter` SET last_serial = %s
			WHERE parent = 'TS Item Code Settings' AND company_code = %s AND category_code = %s
		""", (next_serial, company_key, category_key))

		self.serial_number = str(next_serial).zfill(digits)

	def _build_item_code(self):
		"""Build the generated item code from all parts (base code without variant)."""
		if not self.company_code or not self.category_code or not self.serial_number:
			return

		settings = frappe.get_single("TS Item Code Settings")
		sep = settings.default_separator or "-"

		# Fixed Asset format: FA-{company_code}-{fiscal_year_short}-{serial}
		if self.creation_type == "Fixed Asset":
			company_key = frappe.db.get_value("Company", self.company, "company_code") or self.company_code
			parts = ["FA", company_key, self.fiscal_year_short or "", self.serial_number]
			self.generated_item_code = sep.join(parts)
			return

		# For multi-variant, generated_item_code is the TEMPLATE code (no variant suffix)
		# For standalone, it's the full code
		# For single-variant (backward compat), it includes variant
		parts = [self.company_code, self.category_code, self.serial_number]

		if self.has_variant and not self.variants:
			# Single-variant backward compat
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
				f"Company Code not found for <b>{self.company}</b>.<br>"
				f"Go to <a href='/app/company/{self.company}'>Company → {self.company}</a> "
				f"and set the <b>{'company_num_code' if self.company_code_type == 'Numerical' else 'company_code'}</b> field."
			)

		# Fixed Asset path: validate fiscal year + asset category
		if self.creation_type == "Fixed Asset":
			if not self.fiscal_year:
				frappe.throw("Please select a Fiscal Year for Fixed Asset items.")
			if not self.fiscal_year_short:
				frappe.throw(
					f"Could not derive short form from Fiscal Year <b>{self.fiscal_year}</b>.<br>"
					f"Expected format like <b>2025-2026</b>."
				)
			if not self.asset_category:
				frappe.throw(
					"Please select an Asset Category. This is required by ERPNext to "
					"auto-create the Asset record when the Purchase Order is received."
				)
			# Skip variant validation for assets — they don't have variants
			return

		if not self.category_code:
			frappe.throw(
				f"Category Code not found for <b>{self.item_group}</b>.<br>"
				f"Go to <a href='/app/item-group/{self.item_group}'>Item Group → {self.item_group}</a> "
				f"and set the <b>{'category_num_code' if self.category_code_type == 'Numerical' else 'category_code'}</b> field."
			)

		if self.has_variant and self.variants:
			# Multi-variant validation
			for i, row in enumerate(self.variants):
				v_code = row.brand_code or row.variant_code
				if not v_code:
					frappe.throw(
						f"Row {i+1}: Variant code is missing. "
						"Please select a Brand or Custom Variant with a valid code."
					)
		elif self.has_variant:
			# Single-variant backward compat
			if self.variant_source == "Brand" and not self.brand:
				frappe.throw("Please select a Brand or uncheck 'Has Variant'")
			if self.variant_source == "Brand" and self.brand and not self.brand_code:
				frappe.throw(
					f"Brand Code not found for <b>{self.brand}</b>.<br>"
					f"Go to <a href='/app/brand/{self.brand}'>Brand → {self.brand}</a> "
					"and set the <b>brand_code</b> field."
				)
			if self.variant_source == "Custom Variant" and not self.variant:
				frappe.throw("Please select a Custom Variant or uncheck 'Has Variant'")

	@frappe.whitelist()
	def create_item(self):
		"""Create Item(s) in ERPNext from the generated item code."""
		if self.item_created:
			frappe.throw(
				f"Item(s) already created from this record: <b>{self.item_created}</b>"
			)

		if not self.generated_item_code:
			frappe.throw("Generated Item Code is empty. Please save the form first.")

		if self.creation_type == "Fixed Asset":
			result = self._create_fixed_asset_item()
		elif self.has_variant and self.variants:
			result = self._create_multi_variant_items()
		elif self.has_variant:
			result = self._create_variant_item()
		else:
			result = self._create_standalone_item()

		# Update this record
		if result.get("template_code"):
			# Multi-variant: store template in item_created (Link field can only hold one value)
			self.item_created = result["template_code"]
			self.template_item = result["template_code"]
		else:
			self.item_created = result["item_code"]
			self.template_item = ""
		self.status = "Created"
		self.save(ignore_permissions=True)

		frappe.msgprint(
			result["message"],
			title="Item Created",
			indicator="green"
		)

		return result

	def _has_custom_posting_date(self):
		"""Check if a custom (backdated) posting date is set."""
		if self.posting_date and getdate(self.posting_date) != getdate():
			return True
		return False

	def _get_common_item_fields(self, item_code, item_name=None):
		"""Return dict of common Item fields used across standalone and variant creation."""
		data = {
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_name or self.item_name or item_code,
			"item_group": self.item_group,
			"stock_uom": self.stock_uom,
			"description": self.description or self.item_name or item_code,
			"is_stock_item": self.maintain_stock,
		}
		if self.gst_hsn_code:
			data["gst_hsn_code"] = self.gst_hsn_code
		if self.valuation_rate:
			data["valuation_rate"] = self.valuation_rate
		if self.standard_rate:
			data["standard_rate"] = self.standard_rate

		# If custom posting_date is set, skip ERPNext's set_opening_stock()
		# (it hardcodes today's date). We'll create Stock Entry manually after insert.
		if self._has_custom_posting_date():
			# Don't pass opening_stock — we handle it in _create_backdated_opening_stock()
			if self.maintain_stock and self.opening_warehouse:
				data["opening_warehouse"] = self.opening_warehouse
		else:
			if self.maintain_stock and self.opening_stock:
				data["opening_stock"] = self.opening_stock
			if self.maintain_stock and self.opening_warehouse:
				data["opening_warehouse"] = self.opening_warehouse

		if self.item_tax_template:
			data["taxes"] = [{"item_tax_template": self.item_tax_template}]
		return data

	def _create_backdated_opening_stock(self, item_code, qty, rate, warehouse=None, company=None):
		"""Create a Stock Entry (Material Receipt) with a custom posting_date for opening stock."""
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		if not qty or flt(qty) <= 0:
			return

		if not rate:
			frappe.throw(f"Valuation Rate is required for backdated opening stock of {item_code}")

		if not company:
			company = frappe.defaults.get_defaults().company

		if not warehouse:
			warehouse = frappe.db.get_value(
				"Warehouse", {"warehouse_name": "Stores", "company": company}
			) or frappe.db.get_single_value("Stock Settings", "default_warehouse")

		if not warehouse:
			frappe.throw(f"Opening Warehouse is required for backdated opening stock of {item_code}")

		posting_date = getdate(self.posting_date)

		stock_entry = make_stock_entry(
			item_code=item_code,
			target=warehouse,
			qty=flt(qty),
			rate=flt(rate),
			company=company,
			posting_date=posting_date,
			posting_time=nowtime(),
			do_not_save=True,
		)

		stock_entry.insert(ignore_permissions=True)
		stock_entry.submit()
		stock_entry.load_from_db()
		stock_entry.add_comment("Comment", f"Opening Stock (Post-dated: {posting_date})")

	def _create_fixed_asset_item(self):
		"""Create a Fixed Asset Item (is_fixed_asset=1, no stock)."""
		item_data = {
			"doctype": "Item",
			"item_code": self.generated_item_code,
			"item_name": self.item_name or self.generated_item_code,
			"item_group": "Fixed Assets",
			"stock_uom": self.stock_uom or "Nos",
			"description": self.description or self.item_name or self.generated_item_code,
			"is_stock_item": 0,
			"is_fixed_asset": 1,
			"asset_category": self.asset_category,
			"asset_naming_series": "ACC-ASS-.YYYY.-",
			"auto_create_assets": 1,
			"is_grouped_asset": 0,
		}
		if self.gst_hsn_code:
			item_data["gst_hsn_code"] = self.gst_hsn_code
		if self.item_tax_template:
			item_data["taxes"] = [{"item_tax_template": self.item_tax_template}]

		item = frappe.get_doc(item_data)
		item.insert(ignore_permissions=True)

		return {
			"item_code": item.name,
			"message": (
				f"Fixed Asset Item <b>{item.name}</b> created successfully.<br>"
				f"Asset Category: <b>{self.asset_category}</b><br>"
				f"You can now create a Purchase Order with this item."
			),
		}

	def _create_standalone_item(self):
		"""Create a standalone item (no variant)."""
		item_data = self._get_common_item_fields(self.generated_item_code)

		item = frappe.get_doc(item_data)
		item.insert(ignore_permissions=True)

		# Handle backdated opening stock
		msg_extra = ""
		if self._has_custom_posting_date() and self.maintain_stock and flt(self.opening_stock) > 0:
			self._create_backdated_opening_stock(
				item_code=item.name,
				qty=self.opening_stock,
				rate=self.valuation_rate or self.standard_rate,
				warehouse=self.opening_warehouse,
				company=self.company,
			)
			msg_extra = f"<br>Opening Stock: {self.opening_stock} posted on <b>{self.posting_date}</b>"

		return {
			"item_code": item.name,
			"message": f"Item <b>{item.name}</b> created successfully.{msg_extra}"
		}

	def _create_variant_item(self):
		"""Create a template item (if needed) + variant item using ERPNext's variant system."""
		settings = frappe.get_single("TS Item Code Settings")
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

		# Ensure the Item Attribute "TS Variant Code" exists
		attr_name = _ensure_variant_attribute(variant_code_value)

		# Step 1: Create or reuse template item
		if not frappe.db.exists("Item", template_code):
			template_data = self._get_common_item_fields(template_code)
			# Template items don't hold stock directly; remove opening stock
			template_data.pop("opening_stock", None)
			template_data.pop("opening_warehouse", None)
			template_data.update({
				"has_variants": 1,
				"variant_based_on": "Item Attribute",
				"attributes": [{"attribute": attr_name}],
			})

			template = frappe.get_doc(template_data)
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
		variant_name = (self.item_name or template_code) + sep + variant_code_value
		variant_data = self._get_common_item_fields(self.generated_item_code, variant_name)
		variant_data.update({
			"variant_of": template_code,
			"variant_based_on": "Item Attribute",
			"attributes": [{
				"attribute": attr_name,
				"attribute_value": variant_code_value,
			}],
		})

		variant_item = frappe.get_doc(variant_data)

		# Set brand if variant source is Brand
		if self.variant_source == "Brand" and self.brand:
			variant_item.brand = self.brand

		variant_item.insert(ignore_permissions=True)

		# Handle backdated opening stock for variant
		msg_extra = ""
		if self._has_custom_posting_date() and self.maintain_stock and flt(self.opening_stock) > 0:
			self._create_backdated_opening_stock(
				item_code=variant_item.name,
				qty=self.opening_stock,
				rate=self.valuation_rate or self.standard_rate,
				warehouse=self.opening_warehouse,
				company=self.company,
			)
			msg_extra = f"<br>Opening Stock: {self.opening_stock} posted on <b>{self.posting_date}</b>"

		return {
			"item_code": variant_item.name,
			"template_code": template_code,
			"message": template_msg + f"Variant <b>{self.generated_item_code}</b> created successfully.{msg_extra}"
		}

	def _create_multi_variant_items(self):
		"""Create a template item + multiple variant items from the variants child table."""
		settings = frappe.get_single("TS Item Code Settings")
		sep = settings.default_separator or "-"

		# Template code = company-category-serial
		template_code = sep.join([self.company_code, self.category_code, self.serial_number])

		# Check if template already exists
		if frappe.db.exists("Item", template_code):
			existing = frappe.get_doc("Item", template_code)
			if not existing.has_variants:
				frappe.throw(
					f"Item <b>{template_code}</b> already exists but is not a template item."
				)

		# Collect all variant codes first, ensure attributes exist
		variant_codes = []
		for row in self.variants:
			v_code = row.brand_code or row.variant_code
			if not v_code:
				frappe.throw(f"Row {row.idx}: Variant code is missing.")
			variant_codes.append(v_code)

		# Ensure all variant attribute values exist
		attr_name = None
		for v_code in variant_codes:
			attr_name = _ensure_variant_attribute(v_code)

		# Step 1: Create template item if it doesn't exist
		template_msg = ""
		if not frappe.db.exists("Item", template_code):
			template_data = {
				"doctype": "Item",
				"item_code": template_code,
				"item_name": self.item_name or template_code,
				"item_group": self.item_group,
				"stock_uom": self.stock_uom,
				"description": self.item_name or template_code,
				"is_stock_item": self.maintain_stock,
				"has_variants": 1,
				"variant_based_on": "Item Attribute",
				"attributes": [{"attribute": attr_name}],
			}
			if self.gst_hsn_code:
				template_data["gst_hsn_code"] = self.gst_hsn_code
			if self.item_tax_template:
				template_data["taxes"] = [{"item_tax_template": self.item_tax_template}]

			template = frappe.get_doc(template_data)
			template.insert(ignore_permissions=True)
			template_msg = f"Template <b>{template_code}</b> created.<br>"
		else:
			template = frappe.get_doc("Item", template_code)
			# Add attribute if missing
			if attr_name and not any(r.attribute == attr_name for r in template.attributes):
				template.append("attributes", {"attribute": attr_name})
				template.save(ignore_permissions=True)
			template_msg = f"Using existing template <b>{template_code}</b>.<br>"

		# Step 2: Create variant items
		created_items = []
		for row in self.variants:
			v_code = row.brand_code or row.variant_code
			variant_item_code = sep.join([template_code, v_code])

			# Skip if already exists
			if frappe.db.exists("Item", variant_item_code):
				template_msg += f"Variant <b>{variant_item_code}</b> already exists (skipped).<br>"
				created_items.append(variant_item_code)
				continue

			variant_name = (self.item_name or template_code) + sep + v_code

			variant_data = {
				"doctype": "Item",
				"item_code": variant_item_code,
				"item_name": variant_name,
				"item_group": self.item_group,
				"stock_uom": self.stock_uom,
				"description": row.description or variant_name,
				"is_stock_item": self.maintain_stock,
				"variant_of": template_code,
				"variant_based_on": "Item Attribute",
				"attributes": [{
					"attribute": attr_name,
					"attribute_value": v_code,
				}],
			}

			if self.gst_hsn_code:
				variant_data["gst_hsn_code"] = self.gst_hsn_code
			if row.valuation_rate:
				variant_data["valuation_rate"] = row.valuation_rate
			if row.standard_rate:
				variant_data["standard_rate"] = row.standard_rate

			# If backdated, skip ERPNext's set_opening_stock (uses today)
			if self._has_custom_posting_date():
				# Don't pass opening_stock — we handle it manually below
				pass
			else:
				if self.maintain_stock and row.opening_stock:
					variant_data["opening_stock"] = row.opening_stock

			if self.maintain_stock and self.opening_warehouse:
				variant_data["opening_warehouse"] = self.opening_warehouse
			if self.item_tax_template:
				variant_data["taxes"] = [{"item_tax_template": self.item_tax_template}]

			variant_item = frappe.get_doc(variant_data)

			# Set brand if present
			if row.brand:
				variant_item.brand = row.brand

			variant_item.insert(ignore_permissions=True)

			# Handle backdated opening stock per variant
			if self._has_custom_posting_date() and self.maintain_stock and flt(row.opening_stock) > 0:
				self._create_backdated_opening_stock(
					item_code=variant_item.name,
					qty=row.opening_stock,
					rate=row.valuation_rate or row.standard_rate or self.valuation_rate or self.standard_rate,
					warehouse=self.opening_warehouse,
					company=self.company,
				)
				template_msg += f"Variant <b>{variant_item_code}</b> created (stock posted on {self.posting_date}).<br>"
			else:
				template_msg += f"Variant <b>{variant_item_code}</b> created.<br>"

			created_items.append(variant_item.name)

		posting_note = ""
		if self._has_custom_posting_date():
			posting_note = f"<br>Opening stock posted on <b>{self.posting_date}</b>"

		return {
			"item_code": ", ".join(created_items),
			"template_code": template_code,
			"variant_items": created_items,
			"message": template_msg + f"<br><b>{len(created_items)}</b> variant(s) created successfully.{posting_note}"
		}


def _ensure_variant_attribute(variant_code_value):
	"""Ensure the 'TS Variant Code' Item Attribute exists with the given value."""
	attr_name = "TS Variant Code"

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
	# Read digits directly from DB (bypass cache — user may have just changed it)
	digits_val = frappe.db.get_single_value("TS Item Code Settings", "serial_digits")
	digits = max(int(digits_val or 3), 2)

	company_key = frappe.db.get_value("Company", company, "company_code") or ""
	category_key = frappe.db.get_value("Item Group", item_group, "category_code") or ""

	if not company_key or not category_key:
		return str(1).zfill(digits)

	# Read counter directly from DB (bypass cached singleton)
	result = frappe.db.sql("""
		SELECT last_serial FROM `tabTS Code Counter`
		WHERE parent = 'TS Item Code Settings' AND company_code = %s AND category_code = %s
	""", (company_key, category_key))

	if result:
		return str((result[0][0] or 0) + 1).zfill(digits)

	return str(1).zfill(digits)


@frappe.whitelist()
def get_next_asset_serial_preview(company, fiscal_year):
	"""Get the next serial number for Fixed Asset preview (without incrementing).

	Counter key is 'FY_{fy_short}' per company. Resets naturally on each new
	fiscal year. Returns 5-digit zero-padded serial.
	"""
	digits = 5
	company_key = frappe.db.get_value("Company", company, "company_code") or ""
	fy_short = derive_fy_short(fiscal_year)

	if not company_key or not fy_short:
		return str(1).zfill(digits)

	category_key = "FY_" + fy_short

	result = frappe.db.sql("""
		SELECT last_serial FROM `tabTS Code Counter`
		WHERE parent = 'TS Item Code Settings' AND company_code = %s AND category_code = %s
	""", (company_key, category_key))

	if result:
		return str((result[0][0] or 0) + 1).zfill(digits)

	return str(1).zfill(digits)


@frappe.whitelist()
def get_current_fiscal_year():
	"""Return the Fiscal Year name for today's date, plus its short form."""
	today = getdate()
	fy_list = frappe.get_all(
		"Fiscal Year",
		filters={
			"year_start_date": ["<=", today],
			"year_end_date": [">=", today],
		},
		fields=["name", "year_start_date", "year_end_date"],
		limit=1,
	)
	if not fy_list:
		return {"fiscal_year": "", "fiscal_year_short": ""}
	fy = fy_list[0]
	return {
		"fiscal_year": fy.name,
		"fiscal_year_short": derive_fy_short(fy.name),
	}


@frappe.whitelist()
def get_variant_code(name):
	"""Lesson 168 helper — return TS Variant.variant_code for the given name.

	Stores User / Accounts roles may lack read perm on TS Variant master.
	Wrapping the lookup in a whitelisted endpoint that fail-closes lets the
	flat-form page fetch the code without the JS triggering a permission modal.
	Returns {"variant_code": str}; empty string when name not found or no code set.
	"""
	if not name:
		return {"variant_code": ""}
	try:
		code = frappe.db.get_value("TS Variant", name, "variant_code") or ""
		return {"variant_code": code}
	except Exception:
		return {"variant_code": ""}


@frappe.whitelist()
def search_existing_items(query, limit=10):
	"""v2.9.17.7 — Duplicate-detection typeahead for Item Name field.

	Two-stage fuzzy match:
	  Stage 1 — token-LIKE: split query on whitespace; AND each token via
	            `item_name LIKE %token%`. Catches wrong-sequence input
	            (e.g. "rice broken" finds "Broken Rice").
	  Stage 2 — Levenshtein fallback: if Stage 1 returns < 5 rows, run
	            `difflib.get_close_matches` over the full item_name index
	            (cutoff 0.6) to catch typos (e.g. "brokn rce" finds
	            "Broken Rice"). In-memory; ~80ms on 3258 items.

	Returns top `limit` matches as a list of dicts, sorted by usage_count
	(PO Item rows) DESC so most-used items rank first.

	Fail-closed: any exception returns []. Empty/short query (<3 chars)
	short-circuits to [] to avoid a 3258-row scan per keystroke.
	"""
	try:
		import difflib

		q = (query or "").strip().lower()
		if len(q) < 3:
			return []

		try:
			lim = max(1, min(int(limit or 10), 25))
		except (TypeError, ValueError):
			lim = 10

		# Permission gate (silent fail-closed — page role JSON is the upstream gate)
		if not frappe.has_permission("Item", "read", throw=False):
			return []

		# Stage 1 — token-LIKE
		tokens = [t for t in q.split() if t]
		seen = set()
		tokens = [t for t in tokens if not (t in seen or seen.add(t))]

		stage1 = []
		if tokens:
			like_clauses = " AND ".join(["LOWER(item_name) LIKE %s"] * len(tokens))
			args = ["%" + t + "%" for t in tokens]
			rows = frappe.db.sql(
				"""
				SELECT name AS item_code, item_name, item_group, brand, stock_uom, disabled
				FROM `tabItem`
				WHERE disabled = 0 AND ({where})
				LIMIT %s
				""".format(where=like_clauses),
				args + [lim * 2],
				as_dict=True,
			)
			stage1 = list(rows)

		# Stage 2 — Levenshtein fallback (only if Stage 1 is weak)
		stage2 = []
		if len(stage1) < 5:
			all_names = frappe.db.sql(
				"SELECT name AS item_code, item_name FROM `tabItem` WHERE disabled = 0 AND item_name IS NOT NULL",
				as_dict=True,
			)
			name_to_code = {r.item_name.lower(): r.item_code for r in all_names if r.item_name}
			close = difflib.get_close_matches(q, list(name_to_code.keys()), n=lim, cutoff=0.6)
			fallback_codes = [name_to_code[n] for n in close]
			if fallback_codes:
				placeholders = ",".join(["%s"] * len(fallback_codes))
				stage2 = frappe.db.sql(
					"SELECT name AS item_code, item_name, item_group, brand, stock_uom, disabled "
					"FROM `tabItem` WHERE name IN ({p})".format(p=placeholders),
					fallback_codes,
					as_dict=True,
				)

		# Merge + dedupe by item_code
		merged = {}
		for r in stage1 + stage2:
			if r.item_code not in merged:
				merged[r.item_code] = r
		results = list(merged.values())

		if not results:
			return []

		# usage_count subquery — count Purchase Order Item rows per item_code
		codes = [r.item_code for r in results]
		placeholders = ",".join(["%s"] * len(codes))
		usage_rows = frappe.db.sql(
			"SELECT item_code, COUNT(*) AS cnt FROM `tabPurchase Order Item` "
			"WHERE item_code IN ({p}) GROUP BY item_code".format(p=placeholders),
			codes,
			as_dict=True,
		)
		usage_map = {r.item_code: int(r.cnt) for r in usage_rows}

		for r in results:
			r["usage_count"] = usage_map.get(r.item_code, 0)

		results.sort(key=lambda r: (-r["usage_count"], r["item_name"] or ""))
		return results[:lim]
	except Exception:
		return []
