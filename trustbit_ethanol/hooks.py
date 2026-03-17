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
			"IT Head",
			"Department Head",
			"General Manager",
			"CEO",
			"MD",
			"Purchase Manager",
			"Grain Purchase Manager",
			"AVP"
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
			"Brand-brand_code",
			"Purchase Order-bbf_approval_section",
			"Purchase Order-bbf_approval_status",
			"Purchase Order-bbf_current_level",
			"Purchase Order-bbf_required_level",
			"Purchase Order-bbf_approval_col1",
			"Purchase Order-bbf_approved_by",
			"Purchase Order-bbf_approved_date",
			"Purchase Order-bbf_revision_count",
			"Purchase Order-bbf_last_action",
			"Purchase Order-bbf_submitted_by",
			"Purchase Order-bbf_revision_section",
			"Purchase Order-bbf_revision_reason",
			"Purchase Order-bbf_revised_by",
			"Purchase Order-bbf_revision_col1",
			"Purchase Order-bbf_resubmit_mode",
			"Purchase Order-bbf_approval_log_section",
			"Purchase Order-bbf_approval_log",
			"Purchase Order-bbf_amount_at_submission",
			"Purchase Order-bbf_last_sla_alert",
			"Purchase Order-bbf_purchase_category",
			"Purchase Order-bbf_approval_rule",
			"Purchase Order-bbf_current_step",
			"Purchase Order-bbf_total_steps",
			"Purchase Order-bbf_self_skip_impossible",
			"Purchase Order-bbf_can_send_to_md",
			"Purchase Order-bbf_budget_overridden",
			"Purchase Order-bbf_budget_override_log_section",
			"Purchase Order-bbf_budget_override_log",
			"Material Request-bbf_mr_section",
			"Material Request-bbf_mr_status",
			"Material Request-bbf_mr_col1",
			"Material Request-bbf_mr_approved_by",
			"Material Request-bbf_mr_approved_date",
			"Material Request-bbf_mr_revision_section",
			"Material Request-bbf_mr_revision_reason",
			"Material Request-bbf_mr_log_section",
			"Material Request-bbf_mr_log",
			"Material Request-bbf_mr_route",
			"Material Request-bbf_mr_approval_route",
			"Material Request-bbf_mr_current_step",
			"Material Request-bbf_mr_total_steps",
			"Material Request-bbf_mr_self_skip_impossible",
			"Material Request-bbf_mr_submitted_by"
		]]]
	}
]

# Inject JS into standard DocTypes for approval buttons
doctype_js = {
	"Purchase Order": "public/js/po_approval.js",
	"Material Request": "public/js/mr_approval.js"
}

# Doc Events — PO lifecycle hooks for approval state management
doc_events = {
	"Purchase Order": {
		"on_cancel": "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.po_on_cancel",
		"before_insert": "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.po_on_amend",
		"before_save": "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.po_before_save",
	},
	"Material Request": {
		"before_save": "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.mr_before_save",
	}
}

# Setup custom fields on Purchase Receipt, Purchase Order, Material Request, Company, Item Group, Brand
after_migrate = [
	"trustbit_ethanol.bbf_gate_entry.setup.create_custom_fields"
]

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"trustbit_ethanol.bbf_gate_entry.api.check_sla_breaches"
		],
		"*/30 * * * *": [
			"trustbit_ethanol.bbf_gate_entry.bbf_po_approval.check_approval_sla"
		]
	}
}

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True
