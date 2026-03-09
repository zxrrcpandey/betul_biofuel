import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields as _create_custom_fields


def create_custom_fields():
	"""Create custom fields on Purchase Receipt for BBF GRN integration."""
	custom_fields = {
		"Purchase Receipt": [
			{
				"fieldname": "bbf_token",
				"fieldtype": "Link",
				"label": "BBF Token",
				"options": "BBF Token",
				"insert_after": "naming_series",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
				"description": "Linked BBF Token (auto-set when GRN is created from BBF Gate Entry system)"
			},
			{
				"fieldname": "bbf_gate_entry",
				"fieldtype": "Link",
				"label": "BBF Gate Entry",
				"options": "BBF Gate Entry",
				"insert_after": "bbf_token",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
				"description": "Linked BBF Gate Entry (auto-set when GRN is created from BBF Gate Entry system)"
			},
		]
	}

	_create_custom_fields(custom_fields)
	_setup_purchase_receipt_permissions()


def _setup_purchase_receipt_permissions():
	"""Ensure Accounts User role has create permission on Purchase Receipt."""
	# Check if Accounts User already has create permission
	existing = frappe.db.exists("DocPerm", {
		"parent": "Purchase Receipt",
		"role": "Accounts User",
		"permlevel": 0,
		"create": 1
	})
	if not existing:
		# Add create permission for Accounts User on Purchase Receipt
		pr_doc = frappe.get_doc("DocType", "Purchase Receipt")
		has_accounts_user = False
		for perm in pr_doc.permissions:
			if perm.role == "Accounts User" and perm.permlevel == 0:
				perm.create = 1
				has_accounts_user = True
				break
		if not has_accounts_user:
			pr_doc.append("permissions", {
				"role": "Accounts User",
				"permlevel": 0,
				"read": 1,
				"write": 1,
				"create": 1,
				"submit": 1
			})
		pr_doc.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Purchase Receipt")
