"""v2.8.13.1 — IFSC Code Custom Field on Bank Account.

Business need (CTO Rahul, 23 Apr 2026): explicit IFSC Code field on
Bank Account doctype. ERPNext native `branch_code` field is generic;
Indian banking workflow benefits from a dedicated IFSC field clearly
labelled for users and reports.

Optional field (reqd=0). No format regex — flexibility first; can be
added later via a separate seed if mis-entries become an issue.

Idempotent: insert-or-update pattern matching v2.8.5 MSME / v2.8.4 LR
isolated seed convention.
"""

import frappe


def seed_bank_ifsc():
	"""Seed `ifsc_code` Custom Field on Bank Account. Idempotent."""
	cf_name = "Bank Account-ifsc_code"
	spec = {
		"doctype": "Custom Field",
		"dt": "Bank Account",
		"fieldname": "ifsc_code",
		"label": "IFSC Code",
		"fieldtype": "Data",
		"length": 11,
		"insert_after": "branch_code",
		"in_list_view": 1,
		"in_standard_filter": 1,
		"description": (
			"Indian Financial System Code (11-character bank-branch identifier). "
			"Optional — leave blank for non-Indian or pre-regulatory accounts. "
			"Format: 4 letters + 0 + 6 alphanumerics (e.g. SBIN0001234)."
		),
	}

	if frappe.db.exists("Custom Field", cf_name):
		try:
			frappe.db.set_value(
				"Custom Field", cf_name,
				{
					"label": spec["label"],
					"length": spec["length"],
					"insert_after": spec["insert_after"],
					"in_list_view": spec["in_list_view"],
					"in_standard_filter": spec["in_standard_filter"],
					"description": spec["description"],
				},
			)
		except Exception as e:
			frappe.log_error(
				message=f"seed_bank_ifsc update: {e}",
				title="seed_bank_ifsc",
			)
	else:
		try:
			frappe.get_doc(spec).insert(
				ignore_if_duplicate=True, ignore_permissions=True
			)
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"seed_bank_ifsc insert: {e}",
				title="seed_bank_ifsc",
			)

	frappe.clear_cache(doctype="Bank Account")
