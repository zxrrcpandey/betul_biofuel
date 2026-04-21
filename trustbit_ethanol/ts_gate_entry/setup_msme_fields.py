"""MSME / Udyam fields on Supplier — v2.8.5 Custom Field seed.

Business need (CTO Rahul, 21 Apr 2026): capture Supplier's MSME / Udyam
registration details on the Supplier master for MSME Act 2006 compliance:
- MSME-1 quarterly report (payments to MSMEs > 45 days)
- Payment terms gating (Micro/Small suppliers must be paid within 45 days)
- TDS / Form 3CD disclosures

## Fields added (4 Custom Fields on Supplier)

1. msme_section — Section Break, collapsible, "MSME Registration"
2. msme_number — Data, "MSME / UDYAM Number"
3. msme_category — Select, "MSME Category" — blank | Micro | Small | Medium
4. msme_registration_date — Date, "MSME Registration Date"

## Placement

Inserted after `tax_withholding_category` (idx 29, native ERPNext field on
the Tax tab). Places MSME Registration inside the Tax tab, right below the
Tax Withholding Category field, keeping all tax/compliance classification
together.

## Why isolated from setup.py

setup.py contains MR_FULL / PO_FULL locked seed functions. Per Lessons 169,
183, 187, 188 pattern we keep each v2.8.x Custom Field seed in its own
module so one seed failure does not cascade to all others.

## Idempotency

Uses `ignore_if_duplicate=True` + existence check. Safe to re-run on every
bench migrate. If someone manually edits a MSME field's label via the
Customize Form UI, this seed will NOT overwrite — it only creates missing
rows. (Contrast with setup_pi_lr_fields.py which DOES override because
GST India installs those fields with wrong labels.)
"""

import frappe


def seed_msme_fields():
	"""Seed 4 MSME Custom Fields on Supplier. Idempotent."""
	fields = [
		{
			"doctype": "Custom Field",
			"dt": "Supplier",
			"fieldname": "msme_section",
			"label": "MSME Registration",
			"fieldtype": "Section Break",
			"insert_after": "tax_withholding_category",
			"collapsible": 1,
			"description": "MSME (Udyam) registration details for MSME Act 2006 compliance. Micro/Small suppliers must be paid within 45 days.",
		},
		{
			"doctype": "Custom Field",
			"dt": "Supplier",
			"fieldname": "msme_number",
			"label": "MSME / UDYAM Number",
			"fieldtype": "Data",
			"insert_after": "msme_section",
			"in_standard_filter": 1,
			"description": "Udyam Registration Number (format UDYAM-XX-XX-XXXXXXX) or older Udyog Aadhaar Memorandum number. Leave blank for non-MSME suppliers.",
		},
		{
			"doctype": "Custom Field",
			"dt": "Supplier",
			"fieldname": "msme_category",
			"label": "MSME Category",
			"fieldtype": "Select",
			"options": "\nMicro\nSmall\nMedium",
			"insert_after": "msme_number",
			"in_standard_filter": 1,
			"description": "MSME Category as per Udyam registration. Drives 45-day payment rule under MSME Act (Micro and Small only).",
		},
		{
			"doctype": "Custom Field",
			"dt": "Supplier",
			"fieldname": "msme_registration_date",
			"label": "MSME Registration Date",
			"fieldtype": "Date",
			"insert_after": "msme_category",
			"description": "Date of UDYAM registration (or older Udyog Aadhaar issue date).",
		},
	]
	for f in fields:
		cf_name = f"Supplier-{f['fieldname']}"
		if frappe.db.exists("Custom Field", cf_name):
			continue
		try:
			frappe.get_doc(f).insert(
				ignore_if_duplicate=True, ignore_permissions=True
			)
			frappe.logger().info(f"[seed_msme] Created {cf_name}")
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"seed_msme insert failure on {cf_name}: {e}",
				title="seed_msme_fields",
			)

	frappe.clear_cache(doctype="Supplier")
