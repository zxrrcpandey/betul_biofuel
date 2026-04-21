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

Inserted after `language` (idx 23, native ERPNext field — last field in
the Supplier Details tab before `dashboard_tab` break). Places MSME
Registration as the bottom section of the Details tab so it's visible
on the main Supplier view without switching tabs.

(v2.8.5 originally placed it in the Tax tab after tax_withholding_category;
v2.8.5.1 moved it to Details tab per CTO Rahul's feedback for better
front-and-center visibility on Supplier onboarding.)

## Why isolated from setup.py

setup.py contains MR_FULL / PO_FULL locked seed functions. Per Lessons 169,
183, 187, 188 pattern we keep each v2.8.x Custom Field seed in its own
module so one seed failure does not cascade to all others.

## Idempotency

If a Custom Field already exists, the seed UPDATES `insert_after` + `label`
properties (v2.8.5.1 relocation). For fresh installs, the seed INSERTS
with correct placement immediately. Safe to re-run on every bench migrate.
"""

import frappe


def seed_msme_fields():
	"""Seed 4 MSME Custom Fields on Supplier. Idempotent — updates
	insert_after on existing rows to ensure placement stays at the bottom
	of the Supplier Details tab across BBF re-seeds."""
	fields = [
		{
			"doctype": "Custom Field",
			"dt": "Supplier",
			"fieldname": "msme_section",
			"label": "MSME Registration",
			"fieldtype": "Section Break",
			"insert_after": "language",
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
			# v2.8.5.1: update placement + label on existing rows so the
			# re-seed correctly relocates MSME section from Tax tab (v2.8.5)
			# to Details tab (v2.8.5.1) per CTO feedback.
			try:
				frappe.db.set_value(
					"Custom Field", cf_name,
					{
						"insert_after": f["insert_after"],
						"label": f["label"],
					},
				)
				frappe.logger().info(
					f"[seed_msme] Updated {cf_name} insert_after={f['insert_after']}"
				)
			except Exception as e:
				frappe.log_error(
					message=f"seed_msme update failure on {cf_name}: {e}",
					title="seed_msme_fields",
				)
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
