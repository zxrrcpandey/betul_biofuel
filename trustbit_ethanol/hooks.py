app_name = "trustbit_ethanol"
app_title = "Trustbit Ethanol Custom App"
app_publisher = "Trustbit Software"
app_description = "Custom ERPNext app for Betul Bio Fuel Pvt Ltd - Ethanol Division"
app_email = "info@trustbit.com"
app_license = "mit"

# required_apps = []

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/trustbit_ethanol/css/trustbit_ethanol.css"
# app_include_js = "/assets/trustbit_ethanol/js/trustbit_ethanol.js"

# Module-workspace mapping for correct breadcrumbs
module_app_map = {
	"BBF Gate Entry": "trustbit_ethanol"
}

# Fixtures for roles and custom fields
fixtures = [
	{
		"doctype": "Role",
		"filters": [["name", "in", [
			"G1 Security",
			"G2 Gate Operator",
			"Weighbridge Operator",
			"Stores User",
			"Quality Inspector",
			"CTO"
		]]]
	},
	{
		"doctype": "Custom Field",
		"filters": [["name", "in", [
			"Purchase Receipt-bbf_token",
			"Purchase Receipt-bbf_gate_entry",
			"Company-company_code",
			"Company-company_num_code",
			"Item Group-category_code",
			"Item Group-category_num_code",
			"Brand-brand_code"
		]]]
	}
]

# Setup custom fields on Purchase Receipt, Company, Item Group, Brand
after_migrate = [
	"trustbit_ethanol.bbf_gate_entry.setup.create_custom_fields"
]

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"trustbit_ethanol.bbf_gate_entry.api.check_sla_breaches"
		]
	}
}

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True
