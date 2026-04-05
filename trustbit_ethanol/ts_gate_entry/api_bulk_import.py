import frappe
import json
import io
import csv


@frappe.whitelist()
def download_template():
	"""Return CSV template content for bulk item import."""
	headers = [
		"item_name", "company", "item_group", "stock_uom", "gst_hsn_code",
		"has_variant", "variant_source", "variant_codes",
		"valuation_rate", "standard_rate", "opening_stock", "opening_warehouse",
		"posting_date", "item_tax_template", "description", "maintain_stock"
	]

	example_rows = [
		[
			"Broken Rice Grade A", "Betul Biofuel Pvt. Ltd.", "Raw Material", "Kg", "100630",
			"No", "", "",
			"100", "50", "", "",
			"", "", "", "Yes"
		],
		[
			"Rice Bran Oil", "Betul Biofuel Pvt. Ltd.", "Raw Material", "Kg", "151590",
			"Yes", "Custom Variant", "BRK,GRA",
			"80", "120", "100", "Stores - BBPL",
			"2026-04-01", "", "Multi-variant oil product", "Yes"
		],
		[
			"Maize Grain", "Betul Biofuel Pvt. Ltd.", "Raw Material", "Kg", "100590",
			"No", "", "",
			"", "", "", "",
			"", "", "", "Yes"
		],
	]

	output = io.StringIO()
	writer = csv.writer(output)
	writer.writerow(headers)
	for row in example_rows:
		writer.writerow(row)

	return {
		"csv_content": output.getvalue(),
		"filename": "ts_bulk_item_template.csv"
	}


@frappe.whitelist()
def parse_uploaded_file(file_content, file_type="csv"):
	"""Parse uploaded CSV content into row dicts."""
	if isinstance(file_content, str):
		pass  # already string
	else:
		file_content = file_content.decode("utf-8")

	rows = []
	reader = csv.DictReader(io.StringIO(file_content))

	for i, row in enumerate(reader):
		if i >= 500:
			frappe.throw("Maximum 500 rows allowed. Please split your file into smaller batches.")

		# Normalize keys
		cleaned = {}
		for key, val in row.items():
			if key:
				cleaned[key.strip().lower().replace(" ", "_")] = (val or "").strip()
		cleaned["_row_num"] = i + 2  # 1-based, skip header
		rows.append(cleaned)

	return rows


@frappe.whitelist()
def bulk_validate_and_preview(rows):
	"""Dry-run: validate all rows and compute preview item codes without creating anything."""
	if isinstance(rows, str):
		rows = json.loads(rows)

	settings = frappe.get_single("TS Item Code Settings")
	digits = max(int(settings.serial_digits or 3), 2)
	sep = settings.default_separator or "-"

	# Build a local counter map for preview (don't modify actual counters)
	preview_counters = {}
	for row in settings.counters:
		key = (row.company_code, row.category_code)
		preview_counters[key] = row.last_serial or 0

	results = []

	for row in rows:
		row_num = row.get("_row_num", "?")
		errors = []
		warnings = []

		# --- Required field checks ---
		company = row.get("company", "")
		item_group = row.get("item_group", "")
		stock_uom = row.get("stock_uom", "") or "Kg"
		gst_hsn_code = row.get("gst_hsn_code", "")
		has_variant = (row.get("has_variant", "") or "").lower() in ("yes", "1", "true")
		variant_source = row.get("variant_source", "") or ""
		variant_codes_str = row.get("variant_codes", "") or ""
		maintain_stock = (row.get("maintain_stock", "") or "yes").lower() in ("yes", "1", "true")

		item_name = row.get("item_name", "").strip()

		if not company:
			errors.append("Company is required")
		if not item_group:
			errors.append("Item Group is required")
		if not gst_hsn_code:
			errors.append("GST HSN Code is required")

		# --- Duplicate check ---
		if item_name and item_group:
			existing = frappe.db.get_value("Item",
				{"item_name": item_name, "item_group": item_group}, "name")
			if existing:
				errors.append(f"Duplicate: Item '{item_name}' already exists in '{item_group}' as {existing}")

		# --- Validate masters exist ---
		company_code = ""
		category_code = ""

		if company:
			if not frappe.db.exists("Company", company):
				errors.append(f"Company '{company}' not found")
			else:
				company_code = frappe.db.get_value("Company", company, "company_num_code") or frappe.db.get_value("Company", company, "company_code") or ""
				if not company_code:
					errors.append(f"Company '{company}' has no company code set")

		if item_group:
			if not frappe.db.exists("Item Group", item_group):
				errors.append(f"Item Group '{item_group}' not found")
			else:
				category_code = frappe.db.get_value("Item Group", item_group, "category_num_code") or frappe.db.get_value("Item Group", item_group, "category_code") or ""
				if not category_code:
					errors.append(f"Item Group '{item_group}' has no category code set")

		if stock_uom and not frappe.db.exists("UOM", stock_uom):
			errors.append(f"UOM '{stock_uom}' not found")

		if gst_hsn_code and not frappe.db.exists("GST HSN Code", gst_hsn_code):
			# HSN might be entered as code, not name
			hsn_exists = frappe.db.exists("GST HSN Code", {"hsn_code": gst_hsn_code})
			if not hsn_exists:
				warnings.append(f"GST HSN Code '{gst_hsn_code}' not found (will attempt to use as-is)")

		# --- Variant validation ---
		variant_codes = []
		if has_variant:
			if not variant_source:
				errors.append("variant_source required when has_variant=Yes (Brand or Custom Variant)")
			if not variant_codes_str:
				errors.append("variant_codes required when has_variant=Yes (comma-separated)")
			else:
				variant_codes = [c.strip() for c in variant_codes_str.split(",") if c.strip()]
				if not variant_codes:
					errors.append("No valid variant codes found")

				# Check variant codes exist
				for vc in variant_codes:
					if variant_source == "Brand":
						brand = frappe.db.get_value("Brand", {"brand_code": vc}, "name")
						if not brand:
							errors.append(f"Brand with brand_code '{vc}' not found")
					elif variant_source == "Custom Variant":
						if not frappe.db.exists("TS Variant", vc):
							errors.append(f"TS Variant '{vc}' not found")

		# --- Compute preview code ---
		preview_code = ""
		preview_variants = []
		if company_code and category_code and not errors:
			# Counter keys use char codes (same as _assign_serial in TS Item Creator)
			counter_company = frappe.db.get_value("Company", company, "company_code") or company_code if company else company_code
			counter_category = frappe.db.get_value("Item Group", item_group, "category_code") or category_code if item_group else category_code
			key = (counter_company, counter_category)
			current = preview_counters.get(key, 0)
			next_serial = current + 1
			preview_counters[key] = next_serial
			serial_str = str(next_serial).zfill(digits)
			preview_code = sep.join([company_code, category_code, serial_str])

			if has_variant and variant_codes:
				for vc in variant_codes:
					preview_variants.append(sep.join([preview_code, vc]))

		results.append({
			"row_num": row_num,
			"valid": len(errors) == 0,
			"errors": errors,
			"warnings": warnings,
			"preview_code": preview_code,
			"preview_variants": preview_variants,
			"item_name": row.get("item_name", ""),
			"company": company,
			"item_group": item_group,
			"stock_uom": stock_uom,
			"gst_hsn_code": gst_hsn_code,
			"has_variant": has_variant,
			"variant_codes": variant_codes,
		})

	valid_count = sum(1 for r in results if r["valid"])
	error_count = sum(1 for r in results if not r["valid"])

	return {
		"results": results,
		"total": len(results),
		"valid_count": valid_count,
		"error_count": error_count,
	}


@frappe.whitelist()
def bulk_create_items(rows):
	"""Create items in bulk. Each row creates a TS Item Creator doc + calls create_item().
	Processes in sequence to ensure serial numbers are sequential."""
	if isinstance(rows, str):
		rows = json.loads(rows)

	results = []

	for row in rows:
		row_num = row.get("_row_num", "?")
		try:
			result = _create_single_item(row)
			frappe.db.commit()
			results.append({
				"row_num": row_num,
				"success": True,
				"item_code": result.get("item_code", ""),
				"template_code": result.get("template_code", ""),
				"variant_items": result.get("variant_items", []),
				"message": result.get("message", ""),
			})
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Bulk Item Import Error (Row {row_num})",
				message=frappe.get_traceback()
			)
			frappe.clear_messages()
			results.append({
				"row_num": row_num,
				"success": False,
				"error": str(e),
				"item_name": row.get("item_name", ""),
			})

	success_count = sum(1 for r in results if r["success"])
	fail_count = sum(1 for r in results if not r["success"])

	return {
		"results": results,
		"total": len(results),
		"success_count": success_count,
		"fail_count": fail_count,
	}


def _create_single_item(row):
	"""Create a single item from a row dict via TS Item Creator."""
	has_variant = (row.get("has_variant", "") or "").lower() in ("yes", "1", "true")
	variant_source = row.get("variant_source", "") or ""
	variant_codes_str = row.get("variant_codes", "") or ""
	maintain_stock = (row.get("maintain_stock", "") or "yes").lower() in ("yes", "1", "true")

	# Block duplicates — check item_name + item_group combination
	item_name = row.get("item_name", "").strip()
	item_group = row.get("item_group", "").strip()
	if item_name and item_group:
		existing = frappe.db.get_value("Item",
			{"item_name": item_name, "item_group": item_group}, "name")
		if existing:
			frappe.throw(f"Duplicate: Item '{item_name}' already exists in '{item_group}' as {existing}")

	# Validate HSN code — auto-create if missing, pad to 6 digits minimum
	hsn_code = row.get("gst_hsn_code", "").strip()
	if hsn_code:
		# Pad to 6 digits if shorter (India Compliance requires 6 or 8 digits)
		if hsn_code.isdigit() and len(hsn_code) < 6:
			hsn_code = hsn_code.ljust(6, "0")

		if not frappe.db.exists("GST HSN Code", hsn_code):
			# Try lookup by hsn_code field
			existing = frappe.db.get_value("GST HSN Code", {"hsn_code": hsn_code}, "name")
			if existing:
				hsn_code = existing
			else:
				# Auto-create the HSN code
				try:
					frappe.get_doc({
						"doctype": "GST HSN Code",
						"hsn_code": hsn_code,
						"description": hsn_code,
					}).insert(ignore_permissions=True)
				except Exception:
					# If HSN creation fails (invalid format), skip it
					hsn_code = ""

	# Build TS Item Creator doc
	doc_data = {
		"doctype": "TS Item Creator",
		"company": row.get("company", ""),
		"company_code_type": "Numerical",
		"item_group": row.get("item_group", ""),
		"category_code_type": "Numerical",
		"item_name": row.get("item_name", ""),
		"stock_uom": row.get("stock_uom", "") or "Kg",
		"gst_hsn_code": hsn_code,
		"item_tax_template": row.get("item_tax_template", ""),
		"description": row.get("description", ""),
		"maintain_stock": 1 if maintain_stock else 0,
		"has_variant": 1 if has_variant else 0,
		"variant_source": variant_source if has_variant else "",
	}

	# Parse numeric fields
	for field in ("valuation_rate", "standard_rate", "opening_stock"):
		val = row.get(field, "")
		if val:
			try:
				doc_data[field] = float(val)
			except (ValueError, TypeError):
				pass

	if row.get("opening_warehouse"):
		doc_data["opening_warehouse"] = row["opening_warehouse"]

	# Post-dated import: parse and convert posting_date to YYYY-MM-DD
	if row.get("posting_date"):
		doc_data["posting_date"] = _parse_date(row["posting_date"])

	# Handle variants
	if has_variant and variant_codes_str:
		variant_codes = [c.strip() for c in variant_codes_str.split(",") if c.strip()]
		variants_child = []

		for vc in variant_codes:
			vrow = {}
			if variant_source == "Brand":
				brand_name = frappe.db.get_value("Brand", {"brand_code": vc}, "name")
				vrow["brand"] = brand_name or ""
				vrow["brand_code"] = vc
			elif variant_source == "Custom Variant":
				vrow["variant"] = vc
				vrow["variant_code"] = vc

			# Use parent-level rates as defaults for each variant
			if doc_data.get("valuation_rate"):
				vrow["valuation_rate"] = doc_data["valuation_rate"]
			if doc_data.get("standard_rate"):
				vrow["standard_rate"] = doc_data["standard_rate"]
			if doc_data.get("opening_stock"):
				vrow["opening_stock"] = doc_data["opening_stock"]

			variants_child.append(vrow)

		doc_data["variants"] = variants_child

	# Insert TS Item Creator doc (triggers before_save → serial assignment + code building)
	creator = frappe.get_doc(doc_data)
	creator.insert(ignore_permissions=True)

	# Call create_item to actually create the ERPNext Item(s)
	result = creator.create_item()

	return result


@frappe.whitelist()
def download_reference_guide():
	"""Return all valid values for bulk import — companies, item groups, brands, variants, UOMs."""
	data = {}

	# Companies with codes
	data["companies"] = frappe.get_all("Company",
		fields=["name", "company_code", "company_num_code"],
		order_by="name", limit_page_length=0)

	# Item Groups with codes (only leaf groups that have codes)
	data["item_groups"] = frappe.get_all("Item Group",
		fields=["name", "category_code", "category_num_code"],
		order_by="name", limit_page_length=0)

	# Brands with codes
	data["brands"] = frappe.get_all("Brand",
		fields=["name", "brand_code"],
		order_by="name", limit_page_length=0)

	# TS Variants with codes
	data["variants"] = frappe.get_all("TS Variant",
		fields=["name", "variant_code"],
		filters={"enabled": 1},
		order_by="name", limit_page_length=0)

	# UOMs
	data["uoms"] = frappe.get_all("UOM",
		fields=["name"],
		order_by="name", limit_page_length=0)

	# GST HSN Codes (top 500)
	data["hsn_codes"] = frappe.get_all("GST HSN Code",
		fields=["name", "description"],
		order_by="name", limit_page_length=500)

	return data


def _parse_date(date_str):
	"""Parse date from various formats (DD-MM-YYYY, DD/MM/YYYY, YYYY-MM-DD) to YYYY-MM-DD."""
	if not date_str:
		return ""

	date_str = date_str.strip()

	# Already in correct format
	if len(date_str) == 10 and date_str[4] == "-" and date_str[7] == "-":
		return date_str

	from frappe.utils import getdate
	from datetime import datetime

	for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%m-%d-%Y", "%m/%d/%Y", "%Y/%m/%d"):
		try:
			return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
		except ValueError:
			continue

	# Fallback: let frappe try
	try:
		return str(getdate(date_str))
	except Exception:
		frappe.throw(f"Cannot parse date '{date_str}'. Use format YYYY-MM-DD or DD-MM-YYYY.")
