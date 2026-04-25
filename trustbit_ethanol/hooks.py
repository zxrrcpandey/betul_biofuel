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
app_include_css = "/assets/trustbit_ethanol/css/ts_theme.css"
web_include_css = "/assets/trustbit_ethanol/css/ts_login.css"
app_include_js = [
	"/assets/trustbit_ethanol/js/ts_executive_override.js",
	"/assets/trustbit_ethanol/js/ts_approval_ux.js",
	"/assets/trustbit_ethanol/js/ts_my_approvals.js",
	"/assets/trustbit_ethanol/js/ts_cc_filter.js",
	"/assets/trustbit_ethanol/js/po_list.js",
	"/assets/trustbit_ethanol/js/mr_list.js",
	"/assets/trustbit_ethanol/js/ts_post_dated_entry_request_list.js",
	"/assets/trustbit_ethanol/js/ts_budget_proposal_list.js",
	"/assets/trustbit_ethanol/js/ts_post_dated.js",
	"/assets/trustbit_ethanol/js/item_list.js",
	"/assets/trustbit_ethanol/js/ts_version_badge.js",
]

# Module-workspace mapping for correct breadcrumbs
module_app_map = {
	"TS Gate Entry": "trustbit_ethanol",
	"TS Return Item Tracker": "trustbit_ethanol",
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
			"AVP",
			"Admin Reception",
			"Return Item Controller",
			"Return Item Custodian"
		]]]
	},
	{
		"doctype": "Print Format",
		"filters": [["name", "in", [
			"TS Token Print",
			"TS Token Slip",
			"TS Gate Pass",
			"TS Gate Entry Detailed",
			"TS Gate Entry Slip"
		]]]
	},
	{
		"doctype": "Custom Field",
		"filters": [["name", "in", [
			"Purchase Receipt-ts_token",
			"Purchase Receipt-ts_gate_entry",
			"Company-company_code",
			"Company-company_num_code",
			"Item Group-category_code",
			"Item Group-category_num_code",
			"Brand-brand_code",
			"Purchase Order-ts_approval_section",
			"Purchase Order-ts_approval_status",
			"Purchase Order-ts_current_level",
			"Purchase Order-ts_required_level",
			"Purchase Order-ts_approval_col1",
			"Purchase Order-ts_approved_by",
			"Purchase Order-ts_approved_date",
			"Purchase Order-ts_revision_count",
			"Purchase Order-ts_last_action",
			"Purchase Order-ts_submitted_by",
			"Purchase Order-ts_revision_section",
			"Purchase Order-ts_revision_reason",
			"Purchase Order-ts_revised_by",
			"Purchase Order-ts_revision_col1",
			"Purchase Order-ts_resubmit_mode",
			"Purchase Order-ts_approval_log_section",
			"Purchase Order-ts_approval_log",
			"Purchase Order-ts_amount_at_submission",
			"Purchase Order-ts_last_sla_alert",
			"Purchase Order-ts_purchase_category",
			"Purchase Order-ts_approval_rule",
			"Purchase Order-ts_current_step",
			"Purchase Order-ts_total_steps",
			"Purchase Order-ts_self_skip_impossible",
			"Purchase Order-ts_can_send_to_md",
			"Purchase Order-ts_budget_overridden",
			"Purchase Order-ts_budget_override_log_section",
			"Purchase Order-ts_budget_override_log",
			"Material Request-cost_center",
			"Material Request-ts_mr_section",
			"Material Request-ts_mr_status",
			"Material Request-ts_mr_col1",
			"Material Request-ts_mr_approved_by",
			"Material Request-ts_mr_approved_date",
			"Material Request-ts_mr_revision_section",
			"Material Request-ts_mr_revision_reason",
			"Material Request-ts_mr_log_section",
			"Material Request-ts_mr_log",
			"Material Request-ts_mr_route",
			"Material Request-ts_mr_approval_route",
			"Material Request-ts_mr_current_step",
			"Material Request-ts_mr_total_steps",
			"Material Request-ts_mr_self_skip_impossible",
			"Material Request-ts_mr_submitted_by",
			"Material Request-ts_mr_hold_section",
			"Material Request-ts_mr_hold_reason",
			"Material Request-ts_mr_held_by",
			"Material Request-ts_mr_held_at_step"
		]]]
	}
]

# Override standard DocType dashboards to show TS connections
override_doctype_dashboards = {
	"Purchase Order": "trustbit_ethanol.ts_gate_entry.dashboard_overrides.get_data_for_purchase_order"
}

# Inject JS into standard DocTypes for approval buttons
doctype_js = {
	"Purchase Order": "public/js/po_approval.js",
	"Material Request": "public/js/mr_approval.js",
	"Purchase Receipt": "public/js/pr_pi_columns.js",
	"Purchase Invoice": "public/js/pr_pi_columns.js",
}


# Force password change on login (set by IT Head via User Management page)
on_session_creation = "trustbit_ethanol.ts_gate_entry.ts_user_management.check_force_password_change"

# Doc Events — PO lifecycle hooks for approval state management
doc_events = {
	"Purchase Order": {
		"on_cancel": "trustbit_ethanol.ts_gate_entry.ts_po_approval.po_on_cancel",
		"before_insert": "trustbit_ethanol.ts_gate_entry.ts_po_approval.po_on_amend",
		"before_save": "trustbit_ethanol.ts_gate_entry.ts_po_approval.po_before_save",
		"on_update": "trustbit_ethanol.ts_gate_entry.ts_po_approval.po_on_update",
	},
	"Material Request": {
		"autoname": "trustbit_ethanol.ts_gate_entry.ts_mr_naming.mr_autoname",
		"before_insert": "trustbit_ethanol.ts_gate_entry.ts_mr_naming.mr_before_insert",
		"before_save": "trustbit_ethanol.ts_gate_entry.ts_po_approval.mr_before_save",
		"on_update": "trustbit_ethanol.ts_gate_entry.ts_po_approval.mr_on_update",
	},
	"Purchase Receipt": {
		"validate": "trustbit_ethanol.ts_gate_entry.ts_pr_bill_check.validate_unique_supplier_bill",
		"before_save": "trustbit_ethanol.ts_gate_entry.stores_receiving_api.pr_before_save_audit_guard",
		"on_submit": "trustbit_ethanol.ts_gate_entry.stores_receiving_api.pr_on_submit_update_token",
		"on_cancel": "trustbit_ethanol.ts_gate_entry.stores_receiving_api.pr_on_cancel_clear_token",
		"after_delete": "trustbit_ethanol.ts_gate_entry.stores_receiving_api.pr_after_delete_clear_token",
	},
	"Purchase Invoice": {
		"validate": [
			"trustbit_ethanol.ts_gate_entry.ts_pi_qc_gate.validate_pi_qc_approved",
			"trustbit_ethanol.ts_gate_entry.setup_pi_po_ref.populate_ts_po_reference",
		],
		"before_save": "trustbit_ethanol.ts_gate_entry.ts_pi_qc_gate._block_pi_qc_override_tampering",
	},
	"TS Quality Inspection": {
		"on_submit": "trustbit_ethanol.ts_gate_entry.ts_qc_auto_reject.on_qi_submitted",
	},
	"Stock Entry": {
		"before_save": "trustbit_ethanol.ts_gate_entry.ts_stock_issue_warning.warn_rejected_stock_in_warehouse",
	},
	"User": {
		"on_update": "trustbit_ethanol.ts_gate_entry.ts_user_management.on_user_update",
	},
}

# Setup custom fields on Purchase Receipt, Purchase Order, Material Request, Company, Item Group, Brand
after_migrate = [
	"trustbit_ethanol.ts_gate_entry.setup.create_custom_fields",
	"trustbit_ethanol.ts_gate_entry.setup.seed_gate_pass_destinations",
	"trustbit_ethanol.ts_gate_entry.setup.seed_visiting_companies",
	"trustbit_ethanol.ts_gate_entry.setup.migrate_store1_route",
	"trustbit_ethanol.ts_gate_entry.setup.seed_cc_approval_configs",
	"trustbit_ethanol.ts_gate_entry.setup.seed_number_cards",
	"trustbit_ethanol.ts_gate_entry.setup.seed_purchase_categories",
	"trustbit_ethanol.ts_gate_entry.setup.seed_po_approval_rules",
	"trustbit_ethanol.ts_gate_entry.setup.seed_mr_approval_routes",
	"trustbit_ethanol.ts_gate_entry.setup.seed_ts_settings",
	"trustbit_ethanol.ts_gate_entry.setup.seed_locations",
	"trustbit_ethanol.ts_gate_entry.setup.seed_cc_codes",
	"trustbit_ethanol.ts_gate_entry.setup.seed_list_view_settings",
	"trustbit_ethanol.ts_gate_entry.setup.seed_custom_docperm",
	"trustbit_ethanol.ts_gate_entry.setup.seed_property_setters",
	"trustbit_ethanol.ts_gate_entry.setup.seed_fiscal_years",
	"trustbit_ethanol.ts_gate_entry.setup.seed_monthly_distributions",
	"trustbit_ethanol.ts_gate_entry.setup.seed_brands",
	"trustbit_ethanol.ts_gate_entry.setup.seed_global_defaults",
	"trustbit_ethanol.ts_gate_entry.setup.patch_wkhtmltopdf_whitelist",
	"trustbit_ethanol.ts_gate_entry.setup.seed_navbar_website_settings",
	"trustbit_ethanol.ts_gate_entry.setup.seed_over_receipt_allowance",
	"trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config.cleanup_unrestricted_user_permissions",
	"trustbit_ethanol.ts_gate_entry.setup_rst.seed_rst_custom_fields",
	"trustbit_ethanol.ts_gate_entry.setup_two_pass_gates.seed_two_pass_gate_fields",
	"trustbit_ethanol.ts_gate_entry.setup_two_pass_gates.patch_number_card_exited_filters",
	"trustbit_ethanol.ts_gate_entry.setup_admin_cancel_perms.seed_admin_cancel_perms",
	"trustbit_ethanol.ts_gate_entry.setup_pi_lr_fields.seed_pi_lr_fields",
	"trustbit_ethanol.ts_gate_entry.setup_msme_fields.seed_msme_fields",
	"trustbit_ethanol.ts_gate_entry.setup_pi_po_ref.seed_pi_po_ref_field",
	"trustbit_ethanol.ts_gate_entry.setup_po_stats_tab.seed_po_stats_tab",
	"trustbit_ethanol.ts_gate_entry.setup_cc_standard_filter.seed_cc_standard_filter",
	"trustbit_ethanol.ts_gate_entry.setup_pr_wb_audit.seed_pr_wb_audit_fields",
	"trustbit_ethanol.ts_gate_entry.setup_my_approvals.seed_my_approvals",
	"trustbit_ethanol.ts_gate_entry.setup_bank_ifsc.seed_bank_ifsc",
]

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"trustbit_ethanol.ts_gate_entry.api.check_sla_breaches",
			"trustbit_ethanol.ts_gate_entry.ts_post_dated.expire_post_dated_requests",
		],
		"*/30 * * * *": [
			"trustbit_ethanol.ts_gate_entry.ts_po_approval.check_approval_sla",
			"trustbit_ethanol.ts_gate_entry.doctype.ts_material_inspection.ts_material_inspection.check_inspection_sla",
			"trustbit_ethanol.ts_return_item_tracker.ts_return_item_api.check_overdue_assignments",
			"trustbit_ethanol.ts_gate_entry.ts_qc_sla_scheduler.scan_overdue_qc_30min",
		],
		"0 9 * * 1": [
			"trustbit_ethanol.ts_gate_entry.ts_qc_sla_scheduler.weekly_qc_email",
		],
	}
}

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True
