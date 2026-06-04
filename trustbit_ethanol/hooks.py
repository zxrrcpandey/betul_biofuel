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
	"/assets/trustbit_ethanol/js/po_payment_amount_gst.js",
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
	"/assets/trustbit_ethanol/js/ts_connections.js",
	"/assets/trustbit_ethanol/js/ts_version_badge.js",
	"/assets/trustbit_ethanol/js/vehicle_origin_autocomplete.js",
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

# v2.9.14.4 — Patch ERPNext PurchaseInvoice to allow update_stock=1 on Returns
# (validate_purchase_receipt_if_update_stock honors is_return; status_updater already supports the combo)
override_doctype_class = {
	"Purchase Invoice": "trustbit_ethanol.ts_gate_entry.ts_pi_override.TSPurchaseInvoice"
}

# Inject JS into standard DocTypes for approval buttons
doctype_js = {
	"Purchase Order": "public/js/po_approval.js",
	"Material Request": "public/js/mr_approval.js",
	"Purchase Receipt": "public/js/pr_pi_columns.js",
	"Purchase Invoice": "public/js/pr_pi_columns.js",
	# v2.16.5 — header Cost Center / Project cascade to item rows
	"Stock Entry": "public/js/stock_entry_header_dims.js",
}


# Force password change on login (set by IT Head via User Management page)
on_session_creation = "trustbit_ethanol.ts_gate_entry.ts_user_management.check_force_password_change"

# Doc Events — PO lifecycle hooks for approval state management
doc_events = {
	"Purchase Order": {
		# v2.10.0 — list-form (Lesson 247): cancel cascade now also drops any Pending budget-override doc.
		"on_cancel": [
			"trustbit_ethanol.ts_gate_entry.ts_po_approval.po_on_cancel",
			"trustbit_ethanol.ts_gate_entry.ts_budget_override.cancel_linked_override_on_source_cancel",
		],
		"before_insert": "trustbit_ethanol.ts_gate_entry.ts_po_approval.po_on_amend",
		"before_save": "trustbit_ethanol.ts_gate_entry.ts_po_approval.po_before_save",
		"on_update": "trustbit_ethanol.ts_gate_entry.ts_po_approval.po_on_update",
	},
	"Material Request": {
		"autoname": "trustbit_ethanol.ts_gate_entry.ts_mr_naming.mr_autoname",
		"before_insert": [
			"trustbit_ethanol.ts_gate_entry.ts_mr_naming.mr_before_insert",
			# v2.9.12 Sprint 1 — reset ts_mr_status + tracking fields on amend (parallel to po_on_amend)
			"trustbit_ethanol.ts_gate_entry.ts_po_approval.mr_on_amend",
		],
		# v2.10.0.4 — clear hidden from_warehouse on non-Transfer MR types so
		# ERPNext's validate_from_warehouse() doesn't block save when row's
		# invisible from_warehouse collides with warehouse (amend carry-over).
		"before_validate": "trustbit_ethanol.ts_gate_entry.ts_mr_warehouse_guard.mr_clear_invisible_from_warehouse",
		"before_save": "trustbit_ethanol.ts_gate_entry.ts_po_approval.mr_before_save",
		# v2.9.17.9 — server-side guard blocking direct submit bypass (Stock User loophole)
		"before_submit": "trustbit_ethanol.ts_gate_entry.ts_po_approval.mr_before_submit_block_direct",
		"on_update": "trustbit_ethanol.ts_gate_entry.ts_po_approval.mr_on_update",
		# v2.9.9.9 — clean up orphan draft Stock Entries when a Stores-flow MR is cancelled
		# v2.10.0 — list-form (Lesson 247): also drops any Pending budget-override doc.
		"on_cancel": [
			"trustbit_ethanol.ts_gate_entry.ts_mr_transfer.cleanup_draft_se_on_mr_cancel",
			"trustbit_ethanol.ts_gate_entry.ts_budget_override.cancel_linked_override_on_source_cancel",
		],
		# v2.9.14.5 — refresh items.actual_qty from live Bin on form open (in-memory only, safe on docstatus=1)
		"onload": "trustbit_ethanol.ts_gate_entry.ts_mr_available_refresh.refresh_actual_qty_onload",
	},
	"Purchase Receipt": {
		"before_validate": [
			"trustbit_ethanol.ts_gate_entry.ts_use_location_propagation.purchase_receipt_backfill",
			"trustbit_ethanol.ts_gate_entry.ts_vehicle_origin_propagation.pr_backfill_vehicle",
		],
		"validate": "trustbit_ethanol.ts_gate_entry.ts_pr_bill_check.validate_unique_supplier_bill",
		"before_submit": "trustbit_ethanol.ts_gate_entry.ts_pr_bill_check.require_supplier_bill_on_submit",
		"before_save": "trustbit_ethanol.ts_gate_entry.stores_receiving_api.pr_before_save_audit_guard",
		"on_submit": "trustbit_ethanol.ts_gate_entry.stores_receiving_api.pr_on_submit_update_token",
		"on_cancel": "trustbit_ethanol.ts_gate_entry.stores_receiving_api.pr_on_cancel_clear_token",
		"after_delete": "trustbit_ethanol.ts_gate_entry.stores_receiving_api.pr_after_delete_clear_token",
	},
	"Purchase Invoice": {
		"before_validate": "trustbit_ethanol.ts_gate_entry.ts_use_location_propagation.purchase_invoice_backfill",
		"validate": [
			"trustbit_ethanol.ts_gate_entry.ts_pi_qc_gate.validate_pi_qc_approved",
			"trustbit_ethanol.ts_gate_entry.ts_pi_qc_gate.validate_pi_ds_required",
			"trustbit_ethanol.ts_gate_entry.setup_pi_po_ref.populate_ts_po_reference",
			"trustbit_ethanol.ts_gate_entry.ts_vehicle_origin_propagation.propagate_to_pi",
		],
		"before_save": "trustbit_ethanol.ts_gate_entry.ts_pi_qc_gate._block_pi_qc_override_tampering",
		"before_submit": "trustbit_ethanol.ts_gate_entry.ts_pi_supplier_invoice_validator.validate_supplier_invoice_match",
	},
	"TS Gate Entry": {
		"on_update": "trustbit_ethanol.ts_gate_entry.ts_vehicle_origin_propagation.propagate_to_token",
	},
	"TS Quality Inspection": {
		"on_submit": "trustbit_ethanol.ts_gate_entry.ts_qc_auto_reject.on_qi_submitted",
	},
	"Stock Entry": {
		# v2.16.5 — header Cost Center / Project cascade (fill-blank) to item rows
		"before_validate": "trustbit_ethanol.ts_gate_entry.ts_se_header_dimensions.cascade_se_header_dimensions",
		"before_save": "trustbit_ethanol.ts_gate_entry.ts_stock_issue_warning.warn_rejected_stock_in_warehouse",
		# v2.16.5 — Material Issue requires Define Use Location / Item Remark / Cost Center on every row (at submit)
		"before_submit": "trustbit_ethanol.ts_gate_entry.ts_stock_issue_warning.require_issue_fields_on_submit",
		# v2.9.9 — revert linked Material Transfer MR back to Pending Stores Manager when SE is cancelled
		# v2.15.0 — also flip a TS Production Entry to Cancelled when its Manufacture SE is cancelled
		#           (both handlers early-return on non-matching purposes — mutually exclusive in effect)
		"on_cancel": [
			"trustbit_ethanol.ts_gate_entry.ts_mr_transfer.revert_on_stock_entry_cancel",
			"trustbit_ethanol.ts_gate_entry.ts_production_api.production_on_stock_entry_cancel",
		],
	},
	"User": {
		"on_update": "trustbit_ethanol.ts_gate_entry.ts_user_management.on_user_update",
	},
}

# Setup custom fields on Purchase Receipt, Purchase Order, Material Request, Company, Item Group, Brand
after_migrate = [
	"trustbit_ethanol.ts_gate_entry.setup.create_custom_fields",
	"trustbit_ethanol.ts_gate_entry.ts_se_header_dimensions.seed_se_header_dimension_fields",
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
	"trustbit_ethanol.ts_gate_entry.setup_apb_perms.seed_apb_auditor_pr_read",
	"trustbit_ethanol.ts_gate_entry.setup_pi_lr_fields.seed_pi_lr_fields",
	"trustbit_ethanol.ts_gate_entry.setup_msme_fields.seed_msme_fields",
	"trustbit_ethanol.ts_gate_entry.setup_pi_po_ref.seed_pi_po_ref_field",
	"trustbit_ethanol.ts_gate_entry.setup_po_stats_tab.seed_po_stats_tab",
	"trustbit_ethanol.ts_gate_entry.setup_cc_standard_filter.seed_cc_standard_filter",
	"trustbit_ethanol.ts_gate_entry.setup_pr_wb_audit.seed_pr_wb_audit_fields",
	"trustbit_ethanol.ts_gate_entry.setup_my_approvals.seed_my_approvals",
	"trustbit_ethanol.ts_gate_entry.setup_bank_ifsc.seed_bank_ifsc",
	# v2.9.0 Phase 3 Day 3 — PO Deduction Terms Custom Fields + Grain Manager role
	"trustbit_ethanol.ts_gate_entry.setup_po_deduction_terms.seed_po_deduction_terms",
	# v2.9.0 Phase 4 Day 4 — PI Custom Fields for DS propagation
	"trustbit_ethanol.ts_gate_entry.setup_pi_ds_fields.seed_pi_ds_fields",
	# v2.9.0 Day 6 — QC Yearly Dashboard workspace reload (Lesson 173)
	"trustbit_ethanol.ts_gate_entry.setup_qc_dashboard.reload_quality_lab_workspace",
	# v2.9.11 — shared Connections panel Custom Fields (PO/MR/PR/PI)
	"trustbit_ethanol.ts_gate_entry.setup_connections_panel.seed_connections_panel",
	# v2.9.11.2 — BBPL Letter Head default (creates on demo, no-op on prod)
	"trustbit_ethanol.ts_gate_entry.setup_default_letter_head.seed_default_letter_head",
	# v2.9.12 Sprint 2 — Health Check kill-switch field on TS Settings
	"trustbit_ethanol.ts_gate_entry.setup_health_check.seed_health_check",
	# v2.11.0 — Token Cascade Delete System (Super Admin role + post-install setup)
	"trustbit_ethanol.ts_gate_entry.setup_super_admin_role.seed_super_admin_role",
	"trustbit_ethanol.ts_gate_entry.setup_cascade_delete.seed_cascade_delete",
	# v2.16.4 — read-only Vehicle Number Custom Field on Purchase Receipt (after vehicle_origin)
	"trustbit_ethanol.ts_gate_entry.ts_vehicle_origin_propagation.seed_pr_vehicle_number_field",
]

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"trustbit_ethanol.ts_gate_entry.api.check_sla_breaches",
			"trustbit_ethanol.ts_gate_entry.ts_post_dated.expire_post_dated_requests",
			# v2.11.0 — cascade delete revert window expiry (observability)
			"trustbit_ethanol.ts_gate_entry.cascade_delete_api.scheduled_expire_revert_windows",
		],
		"0 3 * * *": [
			# v2.11.0 — nightly cascade orphan-trail + hash chain audit
			"trustbit_ethanol.ts_gate_entry.cascade_delete_api.scheduled_nightly_integrity_scan",
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
		# v2.9.12 Sprint 2 — Health Check nightly digest (DISABLED until Sprint 3)
		# Will be uncommented and registered in Sprint 3 once email digest fn is implemented.
		# "0 6 * * *": [
		# 	"trustbit_ethanol.ts_gate_entry.health_check_validators.health_check_nightly_digest",
		# ],
	}
}

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True
