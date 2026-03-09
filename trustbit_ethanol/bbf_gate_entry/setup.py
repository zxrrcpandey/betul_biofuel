import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


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

	create_custom_fields(custom_fields, update=True)
