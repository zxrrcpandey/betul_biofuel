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

# Fixtures for roles
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
	}
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
