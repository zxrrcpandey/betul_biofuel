import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields as _create_custom_fields


def create_custom_fields():
	"""Create custom fields on Purchase Receipt, Purchase Order, Material Request, Company, Item Group, and Brand."""
	custom_fields = {
		"Purchase Receipt": [
			{
				"fieldname": "ts_token",
				"fieldtype": "Link",
				"label": "TS Token",
				"options": "TS Token",
				"insert_after": "naming_series",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 0,
				"in_standard_filter": 1,
				"bold": 1,
				"description": "Linked TS Token (auto-set when GRN is created from TS Gate Entry system)"
			},
			{
				"fieldname": "ts_gate_entry",
				"fieldtype": "Link",
				"label": "TS Gate Entry",
				"options": "TS Gate Entry",
				"insert_after": "ts_token",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
				"description": "Linked TS Gate Entry (auto-set when GRN is created from TS Gate Entry system)"
			},
			{
				"fieldname": "ts_non_weighing_grn",
				"fieldtype": "Check",
				"label": "TS Non-Weighing GRN",
				"insert_after": "ts_gate_entry",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1,
				"print_hide": 1,
				"default": "0",
				"description": "Audit marker — set to 1 when PR was created via Stores Receiving Dashboard for a Non-RM no-weighing token"
			},
			{
				"fieldname": "ts_direct_po_grn",
				"fieldtype": "Check",
				"label": "TS Direct PO GRN",
				"insert_after": "ts_non_weighing_grn",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1,
				"print_hide": 1,
				"default": "0",
				"description": "Audit marker — set to 1 when PR was created via Stores Receiving Dashboard for a Direct PO cost center"
			},
			{
				"fieldname": "ts_stores_dashboard_source",
				"fieldtype": "Data",
				"label": "TS Stores Dashboard Source",
				"insert_after": "ts_direct_po_grn",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1,
				"print_hide": 1,
				"description": "Audit marker — which Stores Receiving Dashboard section created this PR (Section B / Section C)"
			},
			# ── v2.8.0.2: Supplier Invoice details — in Details tab after Supplier ──
			{
				"fieldname": "bill_no",
				"fieldtype": "Data",
				"label": "Supplier Invoice No",
				"insert_after": "supplier",
				"no_copy": 0,
				"print_hide": 0,
				"in_list_view": 1,
				"in_standard_filter": 1,
				"description": "Supplier's invoice/bill number (auto-carries to Purchase Invoice on creation)"
			},
			{
				"fieldname": "bill_date",
				"fieldtype": "Date",
				"label": "Supplier Invoice Date",
				"insert_after": "bill_no",
				"no_copy": 0,
				"print_hide": 0,
				"description": "Supplier's invoice/bill date (auto-carries to Purchase Invoice on creation)"
			},
			# ── v2.8.1 Phase B: QC Gate status on PR (auto-populated from linked QI) ──
			{
				"fieldname": "ts_qc_status",
				"fieldtype": "Select",
				"label": "TS QC Status",
				"options": "Pending\nApproved\nRejected",
				"default": "Pending",
				"insert_after": "ts_stores_dashboard_source",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 0,
				"in_standard_filter": 1,
				"permlevel": 1,
				"description": "QC status derived from linked TS Quality Inspection. Auto-set by QI on_submit hook. Used by Purchase Invoice validate hook to gate payment. Writable at permlevel 1 (IT Head/SM/Admin)."
			},
		],

		# ── v2.8.1 Phase B: Purchase Invoice override fields ──
		"Purchase Invoice": [
			{
				"fieldname": "ts_qc_override_section",
				"fieldtype": "Section Break",
				"label": "QC Override (v2.8.1)",
				"insert_after": "total_taxes_and_charges",
				"collapsible": 1,
				"collapsible_depends_on": "eval:!doc.ts_qc_override",
				"description": "Use only when QC is Rejected/Pending and business has decided to accept with reason. Writable by CEO / System Manager / IT Head only."
			},
			{
				"fieldname": "ts_qc_override",
				"fieldtype": "Check",
				"label": "QC Override (bypass QC gate)",
				"insert_after": "ts_qc_override_section",
				"default": "0",
				"permlevel": 1,
				"no_copy": 1,
				"description": "Check to bypass the QC gate. Requires reason. CEO / SM / IT Head only (permlevel 1)."
			},
			{
				"fieldname": "ts_qc_override_reason",
				"fieldtype": "Small Text",
				"label": "QC Override Reason",
				"insert_after": "ts_qc_override",
				"mandatory_depends_on": "eval:doc.ts_qc_override",
				"permlevel": 1,
				"no_copy": 1,
				"description": "Business justification for bypassing the QC gate. Minimum 20 characters recommended. Audit-logged."
			},
			{
				"fieldname": "ts_qc_override_col",
				"fieldtype": "Column Break",
				"insert_after": "ts_qc_override_reason"
			},
			{
				"fieldname": "ts_qc_override_by",
				"fieldtype": "Data",
				"label": "Overridden By",
				"insert_after": "ts_qc_override_col",
				"read_only": 1,
				"permlevel": 1,
				"no_copy": 1,
				"description": "Auto-set from session user when override is saved."
			},
			{
				"fieldname": "ts_qc_override_at",
				"fieldtype": "Datetime",
				"label": "Overridden At",
				"insert_after": "ts_qc_override_by",
				"read_only": 1,
				"permlevel": 1,
				"no_copy": 1,
				"description": "Auto-set to current time when override is saved."
			},
		],

		# ── v2.8.1 Phase B: Deduction Sheet links to PI ──
		"TS Deduction Sheet": [
			{
				"fieldname": "ts_purchase_invoice",
				"fieldtype": "Link",
				"label": "Purchase Invoice",
				"options": "Purchase Invoice",
				"insert_after": "token_number",
				"description": "Link to the Purchase Invoice this deduction applies to (v2.8.1: becomes required when ts_qc_gate_enabled is ON)."
			},
			{
				"fieldname": "ts_purchase_receipt",
				"fieldtype": "Link",
				"label": "Purchase Receipt",
				"options": "Purchase Receipt",
				"insert_after": "ts_purchase_invoice",
				"description": "Informational link to the source Purchase Receipt."
			},
		],

		# ── PO Approval Fields ──────────────────────────────────────────
		"Purchase Order": [
			{
				"fieldname": "ts_approval_section",
				"fieldtype": "Section Break",
				"label": "TS Approval",
				"insert_after": "terms",
				"collapsible": 0
			},
			{
				"fieldname": "ts_approval_status",
				"fieldtype": "Data",
				"label": "Approval Status",
				"insert_after": "ts_approval_section",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
				"bold": 1,
				"allow_on_submit": 1,
				"default": "Not Submitted",
				"description": "Current approval status (managed by TS Approval System)"
			},
			{
				"fieldname": "ts_current_level",
				"fieldtype": "Int",
				"label": "Current Approval Level",
				"insert_after": "ts_approval_status",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1
			},
			{
				"fieldname": "ts_required_level",
				"fieldtype": "Int",
				"label": "Required Approval Level",
				"insert_after": "ts_current_level",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1,
				"description": "Highest level this PO needs to reach for final approval"
			},
			{
				"fieldname": "ts_approval_col1",
				"fieldtype": "Column Break",
				"insert_after": "ts_required_level"
			},
			{
				"fieldname": "ts_approved_by",
				"fieldtype": "Link",
				"label": "Final Approved By",
				"options": "User",
				"insert_after": "ts_approval_col1",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "ts_approved_date",
				"fieldtype": "Datetime",
				"label": "Approved Date",
				"insert_after": "ts_approved_by",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "ts_revision_count",
				"fieldtype": "Int",
				"label": "Revision Count",
				"insert_after": "ts_approved_date",
				"read_only": 1,
				"no_copy": 1,
				"default": "0"
			},
			{
				"fieldname": "ts_last_action",
				"fieldtype": "Data",
				"label": "Last Action",
				"insert_after": "ts_revision_count",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "ts_submitted_by",
				"fieldtype": "Link",
				"label": "Submitted For Approval By",
				"options": "User",
				"insert_after": "ts_last_action",
				"read_only": 1,
				"no_copy": 1
			},
			# ── Revision Info ──
			{
				"fieldname": "ts_revision_section",
				"fieldtype": "Section Break",
				"label": "Revision Info",
				"insert_after": "ts_submitted_by",
				"collapsible": 1,
				"depends_on": "eval:doc.ts_revision_count > 0"
			},
			{
				"fieldname": "ts_revision_reason",
				"fieldtype": "Small Text",
				"label": "Revision Reason",
				"insert_after": "ts_revision_section",
				"read_only": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_revised_by",
				"fieldtype": "Data",
				"label": "Revised By",
				"insert_after": "ts_revision_reason",
				"read_only": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_revision_col1",
				"fieldtype": "Column Break",
				"insert_after": "ts_revised_by"
			},
			{
				"fieldname": "ts_resubmit_mode",
				"fieldtype": "Data",
				"label": "Resubmit Mode",
				"insert_after": "ts_revision_col1",
				"read_only": 1,
				"no_copy": 1,
				"depends_on": "eval:doc.ts_approval_status=='Revised'"
			},
			# ── Approval Log ──
			{
				"fieldname": "ts_approval_log_section",
				"fieldtype": "Section Break",
				"label": "Approval History",
				"insert_after": "ts_resubmit_mode",
				"collapsible": 1
			},
			{
				"fieldname": "ts_approval_log",
				"fieldtype": "Table",
				"label": "Approval Log",
				"options": "TS Approval Log",
				"insert_after": "ts_approval_log_section",
				"read_only": 1,
				"cannot_add_rows": 1,
				"cannot_delete_rows": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			# ── v2.0 Category & Step Tracking ──
			{
				"fieldname": "ts_purchase_category",
				"fieldtype": "Data",
				"label": "Purchase Category",
				"insert_after": "ts_required_level",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
				"description": "Auto-detected from PO items: Store, Chemical, Grain, Coal"
			},
			{
				"fieldname": "ts_approval_rule",
				"fieldtype": "Link",
				"label": "Approval Rule",
				"options": "TS PO Approval Rule",
				"insert_after": "ts_purchase_category",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_current_step",
				"fieldtype": "Int",
				"label": "Current Step",
				"insert_after": "ts_approval_rule",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_total_steps",
				"fieldtype": "Int",
				"label": "Total Steps",
				"insert_after": "ts_current_step",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_self_skip_impossible",
				"fieldtype": "Check",
				"label": "Self Skip Impossible",
				"insert_after": "ts_total_steps",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Set when submitter has the only approval role (single-step rule) — allows self-approval"
			},
			{
				"fieldname": "ts_can_send_to_md",
				"fieldtype": "Check",
				"label": "Can Send to MD",
				"insert_after": "ts_self_skip_impossible",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Set by server when CEO can manually trigger MD approval step"
			},
			# ── Hidden tracking fields ──
			{
				"fieldname": "ts_amount_at_submission",
				"fieldtype": "Currency",
				"label": "Amount at Submission",
				"insert_after": "ts_approval_log",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Captures grand_total when PO enters approval to detect amount tampering"
			},
			{
				"fieldname": "ts_last_sla_alert",
				"fieldtype": "Datetime",
				"label": "Last CTL Alert",
				"insert_after": "ts_amount_at_submission",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			# ── Budget Override Fields ──
			{
				"fieldname": "ts_budget_overridden",
				"fieldtype": "Check",
				"label": "Budget Overridden",
				"insert_after": "ts_last_sla_alert",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Set when CEO overrides budget block on this PO"
			},
			{
				"fieldname": "ts_budget_override_log_section",
				"fieldtype": "Section Break",
				"label": "Budget Override History",
				"insert_after": "ts_budget_overridden",
				"collapsible": 1,
				"depends_on": "eval:doc.ts_budget_overridden"
			},
			{
				"fieldname": "ts_budget_override_log",
				"fieldtype": "Table",
				"label": "Budget Override Log",
				"options": "TS Budget Override Log",
				"insert_after": "ts_budget_override_log_section",
				"read_only": 1,
				"cannot_add_rows": 1,
				"cannot_delete_rows": 1,
				"no_copy": 1
			},
			# ── v2.28 Executive Hold (CEO/MD, any stage) ──
			{
				"fieldname": "ts_po_hold_section",
				"fieldtype": "Section Break",
				"label": "Hold Info",
				"insert_after": "ts_budget_override_log",
				"collapsible": 1,
				"depends_on": "eval:doc.ts_po_on_hold"
			},
			{
				"fieldname": "ts_po_on_hold",
				"fieldtype": "Check",
				"label": "On Hold (Executive)",
				"insert_after": "ts_po_hold_section",
				"read_only": 1,
				"no_copy": 1,
				"default": "0",
				# v2.28.6 — surfaces an "On Hold (Executive)" box in the PO list filter
				# row. This is the ONLY honest way to filter for held POs: the obvious
				# alternative — adding "On Hold" to the ts_approval_status Select — would
				# be a phantom option, because that field deliberately keeps the real
				# approval step so Resume can restore it, so such a filter would always
				# return 0 rows (the v2.28.1 MR trap, mirrored). Filtering the real flag
				# also works in Report view and via the API. The control does NOT inherit
				# `default: "0"` (base_list.js:838 builds a fresh object) and only applies
				# when ticked (:856 `if (value)`), so nobody's default list view changes.
				"in_standard_filter": 1,
				# allow_on_submit deliberately 0: before_save (where the tamper guard
				# lives) does not run on update-after-submit paths — core
				# UpdateAfterSubmitError is the docstatus-1 tamper layer. The
				# hold/resume endpoints write via db_set, which is unaffected.
				"description": "Executive hold flag — set/cleared ONLY via the Hold/Resume buttons (CEO/MD)."
			},
			{
				"fieldname": "ts_po_hold_reason",
				"fieldtype": "Small Text",
				"label": "Hold Reason",
				"insert_after": "ts_po_on_hold",
				"read_only": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_po_held_by",
				"fieldtype": "Data",
				"label": "Held By",
				"insert_after": "ts_po_hold_reason",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1
			},
		],

		# ── MR Approval Fields ──────────────────────────────────────────
		"Material Request": [
			{
				"fieldname": "cost_center",
				"fieldtype": "Link",
				"label": "Cost Center",
				"options": "Cost Center",
				"insert_after": "schedule_date",
				"reqd": 1,
				"ignore_user_permissions": 1,
				"description": "Cost Center for MR approval routing. Shows all Cost Centers across companies."
			},
			{
				"fieldname": "ts_mr_section",
				"fieldtype": "Section Break",
				"label": "TS Approval",
				"insert_after": "terms",
				"collapsible": 0
			},
			{
				"fieldname": "ts_mr_status",
				"fieldtype": "Data",
				"label": "MR Approval Status",
				"insert_after": "ts_mr_section",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
				"bold": 1,
				"allow_on_submit": 1,
				"default": "Not Submitted"
			},
			# ── v2.0 Route & Step Tracking ──
			{
				"fieldname": "ts_mr_route",
				"fieldtype": "Data",
				"label": "MR Approval Route",
				"insert_after": "ts_mr_status",
				"read_only": 1,
				"no_copy": 1,
				"description": "Resolved route: Operational or CAPEX (based on Cost Center)"
			},
			{
				"fieldname": "ts_mr_approval_route",
				"fieldtype": "Link",
				"label": "Approval Route Ref",
				"options": "TS MR Approval Route",
				"insert_after": "ts_mr_route",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_mr_current_step",
				"fieldtype": "Int",
				"label": "MR Current Step",
				"insert_after": "ts_mr_approval_route",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_mr_total_steps",
				"fieldtype": "Int",
				"label": "MR Total Steps",
				"insert_after": "ts_mr_current_step",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_mr_self_skip_impossible",
				"fieldtype": "Check",
				"label": "MR Self Skip Impossible",
				"insert_after": "ts_mr_total_steps",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Set when MR submitter has the only approval role (single-step route) — allows self-approval"
			},
			{
				"fieldname": "ts_mr_submitted_by",
				"fieldtype": "Link",
				"label": "MR Submitted By",
				"options": "User",
				"insert_after": "ts_mr_self_skip_impossible",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "User who submitted this MR for approval (for self-approval prevention)"
			},
			{
				"fieldname": "ts_mr_col1",
				"fieldtype": "Column Break",
				"insert_after": "ts_mr_submitted_by"
			},
			{
				"fieldname": "ts_mr_approved_by",
				"fieldtype": "Link",
				"label": "MR Approved By",
				"options": "User",
				"insert_after": "ts_mr_col1",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "ts_mr_approved_date",
				"fieldtype": "Datetime",
				"label": "MR Approved Date",
				"insert_after": "ts_mr_approved_by",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "ts_mr_revision_section",
				"fieldtype": "Section Break",
				"label": "MR Revision Info",
				"insert_after": "ts_mr_approved_date",
				"collapsible": 1,
				"depends_on": "eval:doc.ts_mr_status=='Revised'"
			},
			{
				"fieldname": "ts_mr_revision_reason",
				"fieldtype": "Small Text",
				"label": "MR Revision Reason",
				"insert_after": "ts_mr_revision_section",
				"read_only": 1,
				"no_copy": 1
			},
			# ── v2.5 Hold Fields (inserted AFTER log section, not revision) ──
			{
				"fieldname": "ts_mr_hold_section",
				"fieldtype": "Section Break",
				"label": "Hold Info",
				"insert_after": "ts_mr_log",
				"collapsible": 1,
				"depends_on": "eval:doc.ts_mr_status && doc.ts_mr_status.startsWith('On Hold')"
			},
			{
				"fieldname": "ts_mr_hold_reason",
				"fieldtype": "Small Text",
				"label": "Hold Reason",
				"insert_after": "ts_mr_hold_section",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "ts_mr_held_by",
				"fieldtype": "Data",
				"label": "Held By",
				"insert_after": "ts_mr_hold_reason",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1
			},
			{
				"fieldname": "ts_mr_held_at_step",
				"fieldtype": "Int",
				"label": "Held At Step",
				"insert_after": "ts_mr_held_by",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1,
				"description": "Step order when MR was put on hold (for Resume to restore)"
			},
			{
				"fieldname": "ts_mr_last_sla_alert",
				"fieldtype": "Datetime",
				"label": "Last CTL Alert",
				"insert_after": "ts_mr_held_at_step",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "ts_mr_log_section",
				"fieldtype": "Section Break",
				"label": "MR Approval History",
				"insert_after": "ts_mr_revision_reason",
				"collapsible": 1
			},
			{
				"fieldname": "ts_mr_log",
				"fieldtype": "Table",
				"label": "MR Approval Log",
				"options": "TS Approval Log",
				"insert_after": "ts_mr_log_section",
				"read_only": 1,
				"cannot_add_rows": 1,
				"cannot_delete_rows": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
		],

		"Company": [
			{
				"fieldname": "company_code",
				"fieldtype": "Data",
				"label": "Company Code (ABC)",
				"insert_after": "company_name",
				"description": "3-letter character code for item coding (e.g., BBF, TBT)"
			},
			{
				"fieldname": "company_num_code",
				"fieldtype": "Data",
				"label": "Company Code (123)",
				"insert_after": "company_code",
				"description": "2-digit numerical code for item coding (e.g., 01, 02)"
			},
		],
		"Item Group": [
			{
				"fieldname": "category_code",
				"fieldtype": "Data",
				"label": "Category Code (ABC)",
				"insert_after": "item_group_name",
				"description": "3-letter character code for item coding (e.g., GRN, COL)"
			},
			{
				"fieldname": "category_num_code",
				"fieldtype": "Data",
				"label": "Category Code (123)",
				"insert_after": "category_code",
				"description": "2-digit numerical code for item coding (e.g., 01, 02)"
			},
		],
		"Cost Center": [
			{
				"fieldname": "cc_code",
				"fieldtype": "Data",
				"label": "CC Code",
				"insert_after": "cost_center_name",
				"unique": 1,
				"description": "Short code for MR naming series (e.g., MEC, CIV, BLR)"
			},
		],
		"Brand": [
			{
				"fieldname": "brand_code",
				"fieldtype": "Data",
				"label": "Brand Code",
				"insert_after": "brand",
				"description": "3-letter code for item coding (e.g., CAR, ADM, MCL)"
			},
		],

		# ── v2.6.0+ Additional Fields ──────────────────────────────────
		"Material Request Item": [
			{
				"fieldname": "ts_delivery_location",
				"fieldtype": "Data",
				"label": "Define Use Location",
				"insert_after": "warehouse",
				"in_list_view": 1,
				"columns": 2,
			},
			{
				"fieldname": "ts_item_remark",
				"fieldtype": "Data",
				"label": "Item Remark",
				"insert_after": "ts_delivery_location",
				"in_list_view": 1,
				"columns": 2,
			},
		],
		"Purchase Order Item": [
			{
				"fieldname": "ts_delivery_location",
				"fieldtype": "Data",
				"label": "Define Use Location",
				"insert_after": "uom",
				"in_list_view": 1,
				"columns": 2,
				"fetch_from": "material_request_item.ts_delivery_location",
			},
			{
				"fieldname": "ts_item_remark",
				"fieldtype": "Data",
				"label": "Item Remark",
				"insert_after": "ts_delivery_location",
				"in_list_view": 1,
				"columns": 2,
				"fetch_from": "material_request_item.ts_item_remark",
			},
		],
		"Purchase Receipt Item": [
			{
				"fieldname": "ts_delivery_location",
				"fieldtype": "Data",
				"label": "Define Use Location",
				"insert_after": "uom",
				"in_list_view": 1,
				"columns": 2,
				"fetch_from": "purchase_order_item.ts_delivery_location",
			},
			{
				"fieldname": "ts_item_remark",
				"fieldtype": "Data",
				"label": "Item Remark",
				"insert_after": "ts_delivery_location",
				"in_list_view": 1,
				"columns": 2,
				"fetch_from": "purchase_order_item.ts_item_remark",
			},
		],
		"Purchase Invoice Item": [
			{
				"fieldname": "ts_delivery_location",
				"fieldtype": "Data",
				"label": "Define Use Location",
				"insert_after": "uom",
				"fetch_from": "po_detail.ts_delivery_location",
			},
			{
				"fieldname": "ts_item_remark",
				"fieldtype": "Data",
				"label": "Item Remark",
				"insert_after": "ts_delivery_location",
				"in_list_view": 1,
				"columns": 2,
				"fetch_from": "po_detail.ts_item_remark",
			},
		],
		# v2.14.0 — capture Use Location + Item Remark at issue time on the Stock
		# Entry items grid. Gated to Material Issue / Material Transfer via the same
		# parent.purpose depends_on pattern ERPNext uses (putaway_rule, subcontracted_item).
		# fetch_from + fetch_if_empty auto-fills from the linked MR line; editable.
		"Stock Entry Detail": [
			{
				"fieldname": "ts_delivery_location",
				"fieldtype": "Data",
				"label": "Define Use Location",
				"insert_after": "cost_center",
				"in_list_view": 1,
				"columns": 2,
				"translatable": 0,
				"fetch_from": "material_request_item.ts_delivery_location",
				"fetch_if_empty": 1,
				"depends_on": "eval:in_list(['Material Issue','Material Transfer'], parent.purpose)",
			},
			{
				"fieldname": "ts_item_remark",
				"fieldtype": "Data",
				"label": "Item Remark",
				"insert_after": "ts_delivery_location",
				"in_list_view": 1,
				"columns": 2,
				"translatable": 0,
				"fetch_from": "material_request_item.ts_item_remark",
				"fetch_if_empty": 1,
				"depends_on": "eval:in_list(['Material Issue','Material Transfer'], parent.purpose)",
			},
		],
	}

	# ── v2.6.0+ Priority, Remark, Project fields on MR and PO ──
	custom_fields["Material Request"].extend([
		{
			"fieldname": "ts_priority",
			"fieldtype": "Select",
			"label": "Priority",
			"options": "\nLow\nMedium\nHigh\nUrgent",
			"insert_after": "company",
		},
		{
			"fieldname": "ts_username",
			"fieldtype": "Data",
			"label": "Username",
			"insert_after": "ts_priority",
		},
		{
			"fieldname": "ts_department_name",
			"fieldtype": "Data",
			"label": "Department Name",
			"insert_after": "ts_username",
		},
		{
			"fieldname": "ts_project",
			"fieldtype": "Link",
			"label": "Project",
			"options": "Project",
			"insert_after": "cost_center",
		},
		{
			"fieldname": "ts_is_new_project",
			"fieldtype": "Check",
			"label": "Is this a New Project?",
			"insert_after": "ts_project",
		},
		{
			"fieldname": "ts_remark_section",
			"fieldtype": "Section Break",
			"label": "Remark",
			"insert_after": "items",
		},
		{
			"fieldname": "ts_remark",
			"fieldtype": "Small Text",
			"label": "Remark",
			"insert_after": "ts_remark_section",
		},
		# v2.9.9 — Stores flow remark (visible for Material Transfer + Material Issue)
		{
			"fieldname": "ts_transfer_remark",
			"fieldtype": "Small Text",
			"label": "Transfer / Issue Remark",
			"insert_after": "ts_remark",
			"depends_on": "eval:in_list([\"Material Transfer\",\"Material Issue\"], doc.material_request_type)",
		},
	])

	custom_fields["Purchase Order"].extend([
		{
			"fieldname": "ts_priority",
			"fieldtype": "Select",
			"label": "Priority",
			"options": "\nLow\nMedium\nHigh\nUrgent",
			"insert_after": "schedule_date",
		},
		{
			"fieldname": "ts_username",
			"fieldtype": "Data",
			"label": "Username",
			"insert_after": "ts_priority",
		},
		{
			"fieldname": "ts_department_name",
			"fieldtype": "Data",
			"label": "Department Name",
			"insert_after": "ts_username",
		},
		{
			"fieldname": "ts_project",
			"fieldtype": "Link",
			"label": "Project",
			"options": "Project",
			"insert_after": "cost_center",
		},
		{
			"fieldname": "ts_is_new_project",
			"fieldtype": "Check",
			"label": "Is this a New Project?",
			"insert_after": "ts_project",
		},
		{
			"fieldname": "ts_remark_section",
			"fieldtype": "Section Break",
			"label": "Remark",
			"insert_after": "items",
		},
		{
			"fieldname": "ts_remark",
			"fieldtype": "Small Text",
			"label": "Remark",
			"insert_after": "ts_remark_section",
		},
	])

	# ── v2.6.1 G1 Driver Details on TS Token (Custom Fields) ──
	custom_fields["TS Token"] = [
		{
			"fieldname": "ts_driver_name_g1",
			"fieldtype": "Data",
			"label": "Driver Name",
			"insert_after": "vehicle_number",
			"depends_on": "eval:doc.entry_type=='Material'",
		},
		{
			"fieldname": "ts_driver_license_g1",
			"fieldtype": "Data",
			"label": "Driver License Number",
			"insert_after": "ts_driver_name_g1",
			"depends_on": "eval:doc.entry_type=='Material'",
		},
		{
			"fieldname": "ts_driver_mobile_g1",
			"fieldtype": "Data",
			"label": "Driver Mobile",
			"insert_after": "ts_driver_license_g1",
			"depends_on": "eval:doc.entry_type=='Material'",
		},
		{
			"fieldname": "ts_g1_operator_name",
			"fieldtype": "Data",
			"label": "G1 Operator Name",
			"insert_after": "ts_driver_mobile_g1",
		},
	]

	# ── v2.6.1 G1 Driver Info + G2 Operator on TS Gate Entry (Custom Fields) ──
	custom_fields["TS Gate Entry"] = [
		{
			"fieldname": "ts_g1_driver_name",
			"fieldtype": "Data",
			"label": "G1 Driver Name",
			"insert_after": "vehicle_number_display",
		},
		{
			"fieldname": "ts_g1_driver_license",
			"fieldtype": "Data",
			"label": "G1 Driver License",
			"insert_after": "ts_g1_driver_name",
		},
		{
			"fieldname": "ts_g1_driver_mobile",
			"fieldtype": "Data",
			"label": "G1 Driver Mobile",
			"insert_after": "ts_g1_driver_license",
		},
		{
			"fieldname": "ts_g2_operator_name",
			"fieldtype": "Data",
			"label": "G2 Operator Name",
			"insert_after": "ts_g1_driver_mobile",
		},
		# v2.9.17.7 — Material Type granular classification (sits next to material_flow)
		{
			"fieldname": "ts_material_type",
			"fieldtype": "Select",
			"label": "Material Type",
			"options": "\nStore Material\nScrap\nBardana\nFly Ash\nDWGS\nMaize\nRice\nCoal\nDDGS\nEthanol\nLiquid Co2\nIron\nDORB\nFusel oil\nDry Ice",
			"insert_after": "material_flow",
		},
	]

	# User custom field for force password change
	custom_fields["User"] = [
		{
			"fieldname": "ts_force_password_change",
			"fieldtype": "Check",
			"label": "Force Password Change",
			"insert_after": "logout_all_sessions",
			"hidden": 1,
			"read_only": 1,
			"no_copy": 1,
			"description": "Set by IT Head via User Management page — forces password change on next login",
		},
	]

	_create_custom_fields(custom_fields)
	_seed_vehicle_origin_fields()
	_seed_pi_update_stock_return_visibility()
	_seed_dsg_receipt_context_fields()
	_seed_quality_lab_dsg_shortcuts()
	_seed_pi_receipt_context_fields()
	_seed_cost_center_approval_perms()
	# v2.10.0 — budget override approval flow Custom Fields on MR + PO and list view config
	_seed_budget_override_fields()
	# v2.10.0.8 — display-only "Including GST" toggle on PO Payment Terms PDF
	_seed_payment_amount_gst_field()
	_seed_gate_entry_docperm()
	_setup_purchase_receipt_permissions()
	_create_approval_roles()
	_setup_purchase_order_permissions()
	_setup_material_request_permissions()
	_setup_admin_reception_permissions()
	_setup_workspace_roles()
	_fix_workspace_content()
	# v2.9.8.37 — hide redundant NATIVE project field on PO (we use ts_project, downstream reads ts_project)
	_hide_native_project_on_po()
	# v2.9.9 — Material Transfer Independent Flow: kill switch first (creates fields needed by other seeds), then role + perms + auto-assignment
	_seed_material_transfer_kill_switch()
	_seed_stores_manager_role()
	_seed_stores_manager_perms()
	_seed_stores_manager_assignments()
	# Legacy v1.0 — TS Approval Limit is no longer used by v2.0 rule-based system
	# _seed_default_approval_limits()


def _hide_native_project_on_po():
	"""v2.9.8.37 — REVERSAL of v2.9.8.36. Hide NATIVE Frappe `project` field
	on Purchase Order (not our `ts_project`).

	Reason: BBPL's downstream code (reports, automation, deduction-sheet
	calculations) reads `ts_project`, not the native ERPNext `project` field.
	If users edit the native field, ts_project stays empty and the downstream
	stops working. To prevent the duplication confusion AND keep our
	functionality intact, we hide the native field instead.

	v2.9.8.36 had hidden the wrong one (ts_project) — this function reverses
	by ALSO deleting the v2.9.8.36 Property Setter (so ts_project becomes
	visible again) before adding the new one.

	IMPORTANT: Do NOT touch Material Request. MR has NO native project field
	at the header level — only ts_project. Already correct from v2.9.8.36.

	ERPNext mapper compatibility: po_before_save (ts_po_approval.py) mirrors
	ts_project → project on every save, so the native project column stays
	populated for PR/PI mappers + project-based reports, even though the
	field is hidden from the form.
	"""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter
	# Step 1: undo v2.9.8.36 — un-hide ts_project on PO (delete the wrong-direction PS)
	old_ps = frappe.db.exists("Property Setter", {
		"doc_type": "Purchase Order", "field_name": "ts_project", "property": "hidden"
	})
	if old_ps:
		frappe.delete_doc("Property Setter", old_ps, ignore_permissions=True)
	# Step 2: hide the native project field on PO
	make_property_setter("Purchase Order", "project", "hidden", 1, "Check",
		validate_fields_for_doctype=False)


def _setup_purchase_receipt_permissions():
	"""Ensure Accounts User role has create permission on Purchase Receipt.

	Uses direct DocPerm insert/update to avoid saving the DocType (requires developer mode).
	"""
	existing = frappe.db.exists("DocPerm", {
		"parent": "Purchase Receipt",
		"role": "Accounts User",
		"permlevel": 0,
	})
	if existing:
		# Update existing permission to add create
		frappe.db.set_value("DocPerm", existing, "create", 1, update_modified=False)
	else:
		frappe.get_doc({
			"doctype": "DocPerm",
			"parent": "Purchase Receipt",
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": "Accounts User",
			"permlevel": 0,
			"read": 1,
			"write": 1,
			"create": 1,
			"submit": 1,
		}).insert(ignore_permissions=True)

	frappe.clear_cache(doctype="Purchase Receipt")


def _create_approval_roles():
	"""Create approval roles if they don't exist."""
	for role_name in ["Department Head", "General Manager", "CEO", "MD", "Purchase Manager", "Grain Purchase Manager", "AVP", "IT Head", "Admin Reception"]:
		if not frappe.db.exists("Role", role_name):
			role = frappe.new_doc("Role")
			role.role_name = role_name
			role.desk_access = 1
			role.insert(ignore_permissions=True)
	frappe.db.commit()


def _setup_purchase_order_permissions():
	"""Ensure approval roles have read+write on Purchase Order (no submit — controller handles that).

	Uses direct DocPerm insert to avoid saving the DocType (which requires developer mode).
	"""
	approval_roles = ["Department Head", "General Manager", "CEO", "MD", "Purchase Manager", "Grain Purchase Manager", "AVP"]

	for role in approval_roles:
		existing = frappe.db.exists("DocPerm", {
			"parent": "Purchase Order",
			"role": role,
			"permlevel": 0
		})
		if not existing:
			frappe.get_doc({
				"doctype": "DocPerm",
				"parent": "Purchase Order",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": 0,
				"read": 1,
				"write": 1,
				"create": 0,
				"submit": 0,
				"cancel": 0,
				"amend": 0,
			}).insert(ignore_permissions=True)

	frappe.clear_cache(doctype="Purchase Order")


def _setup_material_request_permissions():
	"""Ensure approval roles have read+write on Material Request."""
	mr_roles = ["Department Head", "AVP", "CEO", "General Manager", "MD"]

	for role in mr_roles:
		existing = frappe.db.exists("DocPerm", {
			"parent": "Material Request",
			"role": role,
			"permlevel": 0
		})
		if not existing:
			frappe.get_doc({
				"doctype": "DocPerm",
				"parent": "Material Request",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": 0,
				"read": 1,
				"write": 1,
				"create": 0,
				"submit": 0,
				"cancel": 0,
				"amend": 0,
			}).insert(ignore_permissions=True)

	frappe.clear_cache(doctype="Material Request")


def _setup_role_permissions(role, doctypes_config):
	"""Add a role to DocType permissions, handling Custom DocPerm override.

	CRITICAL: If Custom DocPerm exists for a DocType (via Role Permissions Manager),
	Frappe IGNORES all standard tabDocPerm/JSON permissions. New roles must be added
	to Custom DocPerm table in that case.

	Args:
		role: Role name (e.g., "Admin Reception")
		doctypes_config: dict of {doctype: {"read": 1, "write": 1, "create": 1, ...}}
	"""
	from frappe.permissions import get_doctypes_with_custom_docperms
	custom_perm_doctypes = get_doctypes_with_custom_docperms()

	for doctype, perm_config in doctypes_config.items():
		if doctype in custom_perm_doctypes:
			# Custom DocPerm exists — must add there (standard DocPerm is ignored)
			existing = frappe.db.exists("Custom DocPerm", {
				"parent": doctype, "role": role, "permlevel": 0
			})
			if not existing:
				max_idx = frappe.db.sql(
					"SELECT MAX(idx) FROM `tabCustom DocPerm` WHERE parent=%s", doctype
				)[0][0] or 0
				doc = frappe.get_doc({
					"doctype": "Custom DocPerm",
					"parent": doctype,
					"parenttype": "DocType",
					"parentfield": "permissions",
					"role": role,
					"permlevel": 0,
					"idx": max_idx + 1,
					**perm_config,
				})
				doc.insert(ignore_permissions=True)
		else:
			# No Custom DocPerm — add via standard DocType save
			dt = frappe.get_doc("DocType", doctype)
			has_perm = any(p.role == role for p in dt.permissions)
			if not has_perm:
				dt.append("permissions", {"role": role, "permlevel": 0, **perm_config})
				dt.flags.ignore_permissions = True
				dt.flags.ignore_version = True
				dt.save()

		frappe.clear_cache(doctype=doctype)


def _setup_admin_reception_permissions():
	"""Ensure Admin Reception role has permissions on Gate Pass DocTypes."""
	_setup_role_permissions("Admin Reception", {
		"TS Token": {"read": 1, "write": 1, "create": 1},
		"TS Visitor": {"read": 1, "write": 1, "create": 1},
		"TS Gate Pass Destination": {"read": 1, "write": 0, "create": 0},
	})


def _setup_workspace_roles():
	"""Ensure all custom roles can see their workspaces.

	CRITICAL: Child workspace won't show in sidebar unless parent workspace
	also includes the role. Must add role to BOTH parent and child.
	"""
	# Map: role → workspaces that need it (parent + child)
	role_workspace_map = {
		"Admin Reception": ["Trustbit Ethanol", "Admin Reception"],
	}
	for role, workspaces in role_workspace_map.items():
		for ws_name in workspaces:
			if not frappe.db.exists("Workspace", ws_name):
				continue
			ws = frappe.get_doc("Workspace", ws_name)
			has_role = any(r.role == role for r in ws.roles)
			if not has_role:
				ws.append("roles", {"role": role})
				ws.flags.ignore_permissions = True
				ws.save()


def _fix_workspace_content():
	"""Ensure Admin Reception workspace shortcuts are clean — no date filters."""
	if not frappe.db.exists("Workspace", "Admin Reception"):
		return

	# Fix any shortcut that has a date filter (entry_date, Today, etc.)
	all_shortcuts = frappe.get_all("Workspace Shortcut",
		filters={"parent": "Admin Reception"},
		fields=["name", "label", "stats_filter", "link_to", "type"])
	for s in all_shortcuts:
		sf = s.stats_filter or ""
		if "entry_date" in sf or "Today" in sf or "today" in sf or "Timespan" in sf:
			clean_filter = '[["TS Token","entry_type","=","Gate Pass"]]'
			frappe.db.set_value("Workspace Shortcut", s.name, "stats_filter", clean_filter)

		if s.label == "All Gate Passes":
			frappe.db.set_value("Workspace Shortcut", s.name, "label", "Todays Visitors")

		# Fix "New Gate Pass" — ensure it's DocType/New (not broken URL)
		if s.label == "New Gate Pass" and s.type == "URL":
			frappe.db.set_value("Workspace Shortcut", s.name, {
				"type": "DocType",
				"link_to": "TS Token",
				"doc_view": "New",
			})

	# Fix content JSON
	content = frappe.db.get_value("Workspace", "Admin Reception", "content") or ""
	changed = False
	if "All Gate Passes" in content:
		content = content.replace("All Gate Passes", "Todays Visitors")
		changed = True
	if changed:
		frappe.db.set_value("Workspace", "Admin Reception", "content", content)


def seed_gate_pass_destinations():
	"""Create default Gate Pass destinations if none exist."""
	if frappe.db.count("TS Gate Pass Destination") > 0:
		return

	defaults = [
		{
			"destination_name": "Admin Office",
			"has_g2_checkpoint": 0,
			"enabled": 1,
			"description": "Common area admin office — no G2 checkpoint required"
		},
		{
			"destination_name": "TS Plant",
			"has_g2_checkpoint": 1,
			"enabled": 1,
			"description": "Betul Bio Fuel plant area — G2 checkpoint required"
		},
	]

	for d in defaults:
		doc = frappe.new_doc("TS Gate Pass Destination")
		doc.update(d)
		doc.insert(ignore_permissions=True)

	frappe.db.commit()


def seed_visiting_companies():
	"""Create default visiting companies if none exist."""
	if frappe.db.count("TS Visiting Company") > 0:
		return

	defaults = [
		{"company_name": "Betul Bio Fuel", "enabled": 1, "description": "Ethanol Division"},
		{"company_name": "Cattle Feed", "enabled": 1, "description": "Cattle Feed Division"},
		{"company_name": "Frozen Food", "enabled": 1, "description": "Frozen Food Division"},
	]

	for d in defaults:
		doc = frappe.new_doc("TS Visiting Company")
		doc.update(d)
		doc.insert(ignore_permissions=True)

	frappe.db.commit()


def migrate_store1_route():
	"""Deactivate 'Store 1' MR route and move its CCs to 'AVP MR App'.
	Idempotent: if Store 1 doesn't exist or is already inactive, skip.
	Cannot delete Store 1 because existing MRs reference it — deactivate instead.
	"""
	if not frappe.db.exists("TS MR Approval Route", "Store 1"):
		return

	store1 = frappe.get_doc("TS MR Approval Route", "Store 1")
	if not store1.is_active:
		return  # Already migrated

	store1_ccs = [c.cost_center for c in store1.cost_centers]

	# Deactivate Store 1 (can't delete — linked MRs reference it)
	# Remove CCs from Store 1 so they don't conflict with AVP MR App
	store1.cost_centers = []
	store1.is_active = 0
	store1.flags.ignore_permissions = True
	store1.save()
	frappe.db.commit()

	# Add CCs to AVP MR App
	if store1_ccs and frappe.db.exists("TS MR Approval Route", "AVP MR App"):
		avp_route = frappe.get_doc("TS MR Approval Route", "AVP MR App")
		existing_ccs = {c.cost_center for c in avp_route.cost_centers}

		for cc in store1_ccs:
			if cc not in existing_ccs:
				avp_route.append("cost_centers", {"cost_center": cc})

		avp_route.flags.ignore_permissions = True
		avp_route.save()
		frappe.db.commit()


def seed_cc_approval_configs():
	"""Seed TS CC Approval Config records from CCA.xlsx indent matrix.
	Idempotent: skips CCs that already have a config.
	"""
	if frappe.db.count("TS CC Approval Config") > 0:
		return  # Already seeded

	# Map of CC → {route, flow_type, users: [{action_type, user, role, step_order}]}
	# Based on CCA.xlsx Indent Matrix provided by IT Head
	configs = [
		# ── Standard CCs (MR → Approval → PO) ──
		{"cc": "Civil - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "rupesh.dhote@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "hr.navaahar@betulbiofuel.com", "role": "department user"},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		], "notes": "No HOD — skips to AVP"},
		{"cc": "Boiler ( Bed Material, Charcoal & Consumable ) - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "powerhouse@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "jayesh.bhardwaj@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "powerhouse@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		], "notes": "Akash Jain is both Creator and HOD"},
		{"cc": "HR, safety & and Furniture Material - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "hr.navaahar@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "hr.navaahar@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		], "notes": "Ashish Sharma is both Creator and HOD"},
		{"cc": "IT Hardware + Services - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "it_helpdesk@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "it_helpdesk@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		], "notes": "Kr Dhotte IT Head is own HOD"},
		{"cc": "Mechanical + Services - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "mechanical@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "Mechanical Boiler + Services - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "powerhouse@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "jayesh.bhardwaj@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "powerhouse@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "DG Fuel + Electricity bill - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "purchase@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "purchase@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "Process ( WTP/CPU, BIOLOGICAL) - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "dgm.operation@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		], "notes": "S.N. Shukla is both Creator and HOD"},
		{"cc": "Electrical+ Services - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "electricalinstrument@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "dilip.arya@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "electricalinstrument@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "CF- Production - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "takshak.sambare@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "production.navaahar@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "production.navaahar@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "CBG-Farming - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "hr.navaahar@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "cbg.agriculture@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "hr.navaahar@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "BOILER THERMAX - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "powerhouse@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "powerhouse@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "TRIVENI TURBINE - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "powerhouse@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "powerhouse@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "LIASING - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "purchasemanager@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "hr.navaahar@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "purchase@betulbiofuel.com", "role": "department user"},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		], "notes": "No HOD — skips to AVP"},
		{"cc": "Bhopal Office - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "purchase@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "purchase@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "MISCELLANEOUS - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "hr.navaahar@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "hr.navaahar@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		# Process sub-CCs — all HOD = S.N. Shukla
		{"cc": "PROCESS ISGEK - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "dgm.operation@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "PROCESS MILLING - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "dgm.operation@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "PROCESS STRUCTURE - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "dgm.operation@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "PROCESS ACC - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "dgm.operation@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "PROCESS WTP/CPU - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "dgm.operation@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "PROCESS DRYER - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "dgm.operation@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "PROCESS BOP - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "dgm.operation@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		# Boiler sub-CCs — all HOD = Akash Jain
		{"cc": "BOILER THERMEX - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "powerhouse@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "powerhouse@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "BOILER ESP - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "powerhouse@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "powerhouse@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "BOILER TRIVENI - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "powerhouse@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "powerhouse@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "BOILER BOP - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "powerhouse@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "powerhouse@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		{"cc": "BOILER FUEL/ASH - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "powerhouse@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "powerhouse@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		]},
		# CCs that go to CEO (not AVP) — need a CEO route
		{"cc": "CF- MARKETING - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "sales.navaahar@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "sales.navaahar@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		], "notes": "IT Head to update route to CEO route when created"},
		{"cc": "CF- Raw Material - BBPL", "route": "AVP MR App", "flow": "Standard", "users": [
			{"type": "Creator", "user": "takshak.sambare@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "production.navaahar@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "production.navaahar@betulbiofuel.com", "role": "Department Head", "step": 2},
			{"type": "Final Approver", "user": "generalmanager@betulbiofuel.com", "role": "AVP", "step": 3},
		], "notes": "IT Head to update route to CEO route when created"},
		# ── CAPEX CCs ──
		{"cc": "Capex - BBPL", "route": "CAPEX", "flow": "Standard", "users": [
			{"type": "Creator", "user": "purchasemanager@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "sheikh.mubsshir@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "purchase@betulbiofuel.com", "role": "department user"},
			{"type": "Final Approver", "user": "pradeep.modi@betulbiofuel.com", "role": "CEO", "step": 1},
		], "notes": "CAPEX route — CEO final approve"},
		{"cc": "CBG CAPEX - BBPL", "route": "CAPEX", "flow": "Standard", "users": [
			{"type": "Creator", "user": "purchasemanager@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "sheikh.mubsshir@betulbiofuel.com", "role": "department user"},
			{"type": "Creator", "user": "purchase@betulbiofuel.com", "role": "department user"},
			{"type": "Reviewer", "user": "dgm.operation@betulbiofuel.com", "role": "Department Head", "step": 1},
			{"type": "Final Approver", "user": "pradeep.modi@betulbiofuel.com", "role": "CEO", "step": 1},
		], "notes": "CAPEX with DGM as HOD"},
		# ── Direct PO CCs (no MR needed) ──
		{"cc": "Coal - BBPL", "route": "", "flow": "Direct PO", "users": [
			{"type": "Creator", "user": "purchasemanager@betulbiofuel.com", "role": "Purchase Manager"},
		], "notes": "Coal direct PO, MD Akram creates"},
		{"cc": "Machinery - BBPL", "route": "", "flow": "Direct PO", "users": [
			{"type": "Creator", "user": "purchase@betulbiofuel.com", "role": "Purchase Manager"},
		], "notes": "Direct PO"},
		{"cc": "DDGS - BBPL", "route": "", "flow": "Direct PO", "users": [
			{"type": "Creator", "user": "grain.manager@betulbiofuel.com", "role": "Grain Purchase Manager"},
		], "notes": "Direct PO, Grain Manager creates"},
		{"cc": "CIVIL MATERIAL - BBPL", "route": "", "flow": "Direct PO", "users": [
			{"type": "Creator", "user": "rupesh.dhote@betulbiofuel.com", "role": "department user"},
		], "notes": "Direct PO"},
		{"cc": "CIVIL BOP - BBPL", "route": "", "flow": "Direct PO", "users": [
			{"type": "Creator", "user": "rupesh.dhote@betulbiofuel.com", "role": "department user"},
		], "notes": "Direct PO"},
	]

	for cfg in configs:
		cc = cfg["cc"]
		# Skip if CC doesn't exist on this site
		if not frappe.db.exists("Cost Center", cc):
			continue
		# Skip if config already exists
		if frappe.db.exists("TS CC Approval Config", cc):
			continue

		doc = frappe.new_doc("TS CC Approval Config")
		doc.cost_center = cc
		doc.mr_approval_route = cfg.get("route") or ""
		doc.flow_type = cfg.get("flow", "Standard")
		doc.is_active = 1
		doc.notes = cfg.get("notes", "")

		for u in cfg.get("users", []):
			# Skip if user doesn't exist
			user_email = u.get("user", "")
			if user_email and not frappe.db.exists("User", user_email):
				continue
			full_name = frappe.db.get_value("User", user_email, "full_name") if user_email else ""
			doc.append("users", {
				"action_type": u["type"],
				"user": user_email,
				"user_full_name": full_name or "",
				"role": u.get("role", ""),
				"step_order": u.get("step", 0),
				"can_create_mr": 1 if u["type"] == "Creator" else 0,
				"can_approve": 1 if u["type"] in ("Reviewer", "Final Approver") else 0,
				"gets_notified": 1,
			})

		if doc.users:
			doc.flags.ignore_permissions = True
			doc.insert()

	frappe.db.commit()


def _seed_default_approval_limits():
	"""Create default TS Approval Limit records if none exist."""
	if frappe.db.count("TS Approval Limit") > 0:
		return

	defaults = [
		{
			"role": "Department Head",
			"role_label": "Department Head",
			"approval_level": 1,
			"approval_limit": 10000,
			"can_final_approve": 1,
			"can_revise": 1,
			"revise_to_levels": "",
			"is_active": 1,
			"notify_on_pending": 1,
			"notify_on_approval": 1,
			"description": "Level 1: Can final-approve POs up to Rs. 10,000"
		},
		{
			"role": "General Manager",
			"role_label": "General Manager (GM)",
			"approval_level": 2,
			"approval_limit": 100000,
			"can_final_approve": 1,
			"can_revise": 1,
			"revise_to_levels": "1",
			"is_active": 1,
			"notify_on_pending": 1,
			"notify_on_approval": 1,
			"description": "Level 2: Can final-approve POs up to Rs. 1,00,000"
		},
		{
			"role": "CEO",
			"role_label": "CEO",
			"approval_level": 3,
			"approval_limit": 600000,
			"can_final_approve": 1,
			"can_revise": 1,
			"revise_to_levels": "1,2",
			"is_active": 1,
			"notify_on_pending": 1,
			"notify_on_approval": 1,
			"description": "Level 3: Can final-approve POs up to Rs. 6,00,000"
		},
		{
			"role": "MD",
			"role_label": "Managing Director (MD)",
			"approval_level": 4,
			"approval_limit": 0,
			"can_final_approve": 1,
			"can_revise": 1,
			"revise_to_levels": "1,2,3",
			"is_active": 1,
			"notify_on_pending": 1,
			"notify_on_approval": 1,
			"description": "Level 4: Unlimited approval authority (highest level)"
		},
	]

	for d in defaults:
		doc = frappe.new_doc("TS Approval Limit")
		doc.update(d)
		doc.insert(ignore_permissions=True)

	frappe.db.commit()


def seed_number_cards():
	"""Create Number Cards with colorful backgrounds. Updates background_color on existing cards."""
	cards = [
		# Green — completed/positive
		{"name": "Approved Quality", "label": "Approved Quality", "document_type": "TS Quality Inspection", "filters_json": '[["TS Quality Inspection", "status", "=", "Approved"]]', "color": "#10b981", "bg": "#dcfce7", "module": "TS Gate Entry"},
		{"name": "Completed Today WB", "label": "Completed Today", "document_type": "TS Weighbridge Log", "filters_json": '[["TS Weighbridge Log", "status", "=", "Completed"], ["TS Weighbridge Log", "creation", "Timespan", "today"]]', "color": "#10b981", "bg": "#dcfce7"},
		{"name": "Exited Today", "label": "Exited Today", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "=", "Exited"], ["TS Token", "g1_exit_time", "Timespan", "today"]]', "color": "#10b981", "bg": "#dcfce7"},
		{"name": "Exited Today - Main", "label": "Exited Today", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "=", "Exited"], ["TS Token", "creation", "Timespan", "today"]]', "color": "#10b981", "bg": "#dcfce7"},
		{"name": "GRN Created Today", "label": "GRN Created Today", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "=", "GRN Created"]]', "color": "#10b981", "bg": "#dcfce7"},
		{"name": "Sent to Stores", "label": "Sent to Stores", "document_type": "TS Gate Entry", "filters_json": '[["TS Gate Entry", "status", "=", "Sent to Stores"], ["TS Gate Entry", "docstatus", "=", 1]]', "color": "#10b981", "bg": "#d1fae5"},
		{"name": "Total Today - Main", "label": "Total Today", "document_type": "TS Token", "filters_json": '[["TS Token", "creation", "Timespan", "today"]]', "color": "#10b981", "bg": "#d1fae5"},
		{"name": "Total Today - Mgmt", "label": "Total Today", "document_type": "TS Token", "filters_json": '[["TS Token", "creation", "Timespan", "today"]]', "color": "#10b981", "bg": "#d1fae5"},
		{"name": "Vehicles Today", "label": "Vehicles Today", "document_type": "TS Token", "filters_json": '[["TS Token", "creation", "Timespan", "today"]]', "color": "#10b981", "bg": "#d1fae5"},
		{"name": "Visitors Today", "label": "Visitors Today", "document_type": "TS Token", "filters_json": '[["TS Token", "entry_type", "=", "Gate Pass"], ["TS Token", "creation", "Timespan", "today"]]', "color": "#10b981", "bg": "#dcfce7"},
		# Red — pending/critical
		{"name": "Pending Quality-1", "label": "Pending Quality", "document_type": "TS Quality Inspection", "filters_json": '[["TS Quality Inspection", "status", "=", "Pending"]]', "color": "#ef4444", "bg": "#fee2e2", "module": "TS Gate Entry"},
		{"name": "Pending Exit", "label": "Pending Exit", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "in", ["GRN Created", "Tare Weighed"]]]', "color": "#ef4444", "bg": "#fee2e2"},
		{"name": "Pending GRN", "label": "Pending GRN", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "=", "Tare Weighed"]]', "color": "#ef4444", "bg": "#fee2e2"},
		{"name": "Stuck Vehicles", "label": "Stuck Vehicles", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "not in", ["Exited", "Token Generated"]]]', "color": "#ef4444", "bg": "#fee2e2"},
		{"name": "SLA Breaches", "label": "SLA Breaches", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "not in", ["Exited", "Token Generated"]]]', "color": "#ef4444", "bg": "#fee2e2"},
		{"name": "Awaiting Tare Weight", "label": "Awaiting Tare", "document_type": "TS Weighbridge Log", "filters_json": '[["TS Weighbridge Log", "status", "=", "Awaiting Tare Weight"]]', "color": "#ef4444", "bg": "#fee2e2"},
		{"name": "Items Without Code", "label": "Items Without Code", "document_type": "Item", "filters_json": '[["Item", "item_code", "is", "not set"]]', "color": "#ef4444", "bg": "#fee2e2"},
		# Pink
		{"name": "Pending Material Inspections", "label": "Pending Material Inspections", "document_type": "TS Material Inspection", "filters_json": '[["TS Material Inspection", "status", "=", "Pending"]]', "color": "#ec4899", "bg": "#fce7f3", "module": "TS Gate Entry"},
		# Blue — active/info
		{"name": "Vehicles Inside Plant", "label": "Vehicles Inside Plant", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "not in", ["Exited", "Token Generated"]]]', "color": "#3b82f6", "bg": "#dbeafe"},
		{"name": "Vehicles Inside - Main", "label": "Vehicles Inside", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "not in", ["Exited", "Token Generated"]]]', "color": "#3b82f6", "bg": "#dbeafe"},
		{"name": "Vehicles Inside - Mgmt", "label": "Vehicles Inside", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "not in", ["Exited", "Token Generated"]]]', "color": "#3b82f6", "bg": "#dbeafe"},
		{"name": "Sent to Weighbridge", "label": "Sent to Weighbridge", "document_type": "TS Gate Entry", "filters_json": '[["TS Gate Entry", "status", "=", "Sent to Weighbridge"], ["TS Gate Entry", "docstatus", "=", 1]]', "color": "#3b82f6", "bg": "#dbeafe"},
		{"name": "Sent to Weighbridge Today", "label": "Sent to Weighbridge (Today)", "document_type": "TS Gate Entry", "filters_json": '[["TS Gate Entry", "status", "=", "Sent to Weighbridge"], ["TS Gate Entry", "docstatus", "=", 1], ["TS Gate Entry", "creation", "Timespan", "today"]]', "color": "#3b82f6", "bg": "#dbeafe"},
		{"name": "Sent to Stores Today", "label": "Sent to Stores (Today)", "document_type": "TS Gate Entry", "filters_json": '[["TS Gate Entry", "status", "=", "Sent to Stores"], ["TS Gate Entry", "docstatus", "=", 1], ["TS Gate Entry", "creation", "Timespan", "today"]]', "color": "#10b981", "bg": "#d1fae5"},
		{"name": "Todays Quality Checks", "label": "Today's Quality Checks", "document_type": "TS Quality Inspection", "filters_json": '[["TS Quality Inspection", "creation", "Timespan", "today"]]', "color": "#3b82f6", "bg": "#dbeafe", "module": "TS Gate Entry"},
		{"name": "Todays Deductions", "label": "Today's Deductions", "document_type": "TS Deduction Sheet", "filters_json": '[["TS Deduction Sheet", "creation", "Timespan", "today"]]', "color": "#3b82f6", "bg": "#e0e7ff", "module": "TS Gate Entry"},
		{"name": "Tare Weighed Today", "label": "Tare Weighed Today", "document_type": "TS Weighbridge Log", "filters_json": '[["TS Weighbridge Log", "status", "in", ["Tare Recorded", "Completed"]], ["TS Weighbridge Log", "creation", "Timespan", "today"]]', "color": "#3b82f6", "bg": "#dbeafe"},
		{"name": "Visitors Inside Campus", "label": "Visitors Inside Campus", "document_type": "TS Token", "filters_json": '[["TS Token", "entry_type", "=", "Gate Pass"], ["TS Token", "gate_pass_status", "=", "Inside Campus"]]', "color": "#3b82f6", "bg": "#dbeafe"},
		{"name": "Total Active Items", "label": "Total Active Items", "document_type": "Item", "filters_json": '[]', "color": "#10b981", "bg": "#dcfce7"},
		{"name": "Total Item Groups", "label": "Total Item Groups", "document_type": "Item Group", "filters_json": '[]', "color": "#3b82f6", "bg": "#dbeafe"},
		# Amber — waiting
		{"name": "Pending Deductions", "label": "Pending Deductions", "document_type": "TS Deduction Sheet", "filters_json": '[["TS Deduction Sheet", "status", "=", "Calculated"]]', "color": "#f59e0b", "bg": "#fef3c7", "module": "TS Gate Entry"},
		{"name": "Awaiting PO Link", "label": "Awaiting PO Link", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "=", "Token Generated"]]', "color": "#f59e0b", "bg": "#fef3c7"},
		{"name": "Awaiting Gross Weight", "label": "Awaiting Gross", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "=", "PO Linked"]]', "color": "#f59e0b", "bg": "#fef3c7"},
		{"name": "Pending Quality", "label": "Pending Quality", "document_type": "TS Token", "filters_json": '[["TS Token", "status", "=", "Tare Weighed"]]', "color": "#f59e0b", "bg": "#fef3c7"},
		{"name": "Visitors Inside Plant", "label": "Visitors Inside Plant", "document_type": "TS Token", "filters_json": '[["TS Token", "entry_type", "=", "Gate Pass"], ["TS Token", "gate_pass_status", "=", "Inside Plant"]]', "color": "#f59e0b", "bg": "#fef3c7"},
		# Purple
		{"name": "Total Brands", "label": "Total Brands", "document_type": "Brand", "filters_json": '[]', "color": "#8b5cf6", "bg": "#ede9fe"},
		{"name": "Total Visitors Master", "label": "Total Visitors", "document_type": "TS Visitor", "filters_json": '[]', "color": "#8b5cf6", "bg": "#ede9fe"},
	]

	for nc in cards:
		if frappe.db.exists("Number Card", nc["name"]):
			# Update background_color on existing cards
			frappe.db.set_value("Number Card", nc["name"], {
				"color": nc.get("color"),
				"background_color": nc.get("bg"),
			})
		else:
			doc = frappe.get_doc({
				"doctype": "Number Card",
				"label": nc["label"],
				"document_type": nc["document_type"],
				"function": "Count",
				"filters_json": nc["filters_json"],
				"is_standard": 0,
				"type": "Document Type",
				"color": nc.get("color"),
				"background_color": nc.get("bg"),
				"module": nc.get("module"),
				"owner": "Administrator",
			})
			doc.insert(ignore_permissions=True, set_name=nc["name"])

	frappe.db.commit()


def seed_purchase_categories():
	"""Create Purchase Categories (Store, Grain, Coal) with item group mappings if none exist."""
	if frappe.db.count("TS Purchase Category") > 0:
		return

	categories = {
		"Coal": ["Raw Material - Coal", "Coal A", "Coal B"],
		"Grain": ["Raw Material - Grain", "Rice", "Maize", "Raw Material"],
		"Store": [
			# General store items
			"Mechanical", "PUSH ELBOW", "PUSH", "T MANIFOLD", "VOLT METER", "GLASS FUSE",
			"LAB MOTOR", "Patch Code", "Cable Manager", "Ash Silo", "Conveyor Belt Idlers",
			"Plant Cleaning", "Steam line", "AHP", "Boiler", "STRUCTURE", "Sub Assemblies",
			"Services", "IT", "Battery", "GENRAL", "PIPE FITTING & VALVE", "FASTENERS",
			"Electrical", "Tools", "SPARE", "Consumable", "Products", "All Item Groups",
			# Chemical items (same approval rules as Store — Lesson 145)
			"Chemical", "D-Nature Chemicals", "Process Chemical", "Processing Chemical",
			"Process Lab Chemical", "Production Chemical", "WTP/CPU Chemical",
			"Biological Chemical", "CHEMICAL EARTHING",
		],
	}

	for cat_name, item_groups in categories.items():
		doc = frappe.new_doc("TS Purchase Category")
		doc.category_name = cat_name
		for ig in item_groups:
			if frappe.db.exists("Item Group", ig):
				doc.append("item_groups", {"item_group": ig})
		doc.insert(ignore_permissions=True, set_name=cat_name)

	frappe.db.commit()


def seed_po_approval_rules():
	"""Create PO Approval Rules (category + amount routing) if none exist.
	Must run AFTER seed_purchase_categories (categories are Link targets)."""
	if frappe.db.count("TS PO Approval Rule") > 0:
		return

	rules = [
		{"name": "Store-Under 1L", "purchase_category": "Store", "min_amount": 0, "max_amount": 99999, "is_active": 1,
		 "steps": [{"step_order": 1, "role": "CEO", "action_type": "Final Approve"}]},
		{"name": "Store-1L to 6L", "purchase_category": "Store", "min_amount": 100000, "max_amount": 599999, "is_active": 1,
		 "steps": [{"step_order": 1, "role": "Purchase Manager", "action_type": "Review"},
		           {"step_order": 2, "role": "CEO", "action_type": "Final Approve"}]},
		{"name": "Store-Above 6L", "purchase_category": "Store", "min_amount": 600000, "max_amount": 0, "is_active": 1,
		 "steps": [{"step_order": 1, "role": "Purchase Manager", "action_type": "Review"},
		           {"step_order": 2, "role": "CEO", "action_type": "Review"},
		           {"step_order": 3, "role": "MD", "action_type": "Final Approve"}]},
		{"name": "Grain-Under 6L", "purchase_category": "Grain", "min_amount": 0, "max_amount": 599999, "is_active": 1,
		 "steps": [{"step_order": 1, "role": "Grain Purchase Manager", "action_type": "Review"},
		           {"step_order": 2, "role": "CEO", "action_type": "Final Approve"}]},
		{"name": "Grain-Above 6L", "purchase_category": "Grain", "min_amount": 600000, "max_amount": 0, "is_active": 1,
		 "steps": [{"step_order": 1, "role": "Grain Purchase Manager", "action_type": "Review"},
		           {"step_order": 2, "role": "CEO", "action_type": "Review"},
		           {"step_order": 3, "role": "MD", "action_type": "Final Approve"}]},
		{"name": "Coal-Under 30L", "purchase_category": "Coal", "min_amount": 0, "max_amount": 2999999, "is_active": 1,
		 "steps": [{"step_order": 1, "role": "CEO", "action_type": "Final Approve"}]},
		{"name": "Coal-Above 30L", "purchase_category": "Coal", "min_amount": 3000000, "max_amount": 0, "is_active": 1,
		 "steps": [{"step_order": 1, "role": "CEO", "action_type": "Review"},
		           {"step_order": 2, "role": "MD", "action_type": "Final Approve"}]},
	]

	for r in rules:
		doc = frappe.new_doc("TS PO Approval Rule")
		doc.rule_name = r["name"]
		doc.purchase_category = r["purchase_category"]
		doc.min_amount = r["min_amount"]
		doc.max_amount = r["max_amount"]
		doc.is_active = r["is_active"]
		for s in r["steps"]:
			doc.append("approval_steps", s)
		doc.insert(ignore_permissions=True, set_name=r["name"])

	frappe.db.commit()


def seed_mr_approval_routes():
	"""Create MR Approval Routes (AVP MR App, CAPEX, IT, Store 1) if none exist."""
	if frappe.db.count("TS MR Approval Route") > 0:
		return

	routes = [
		{
			"name": "AVP MR App", "route_name": "AVP MR App", "is_active": 1,
			"steps": [
				{"step_order": 1, "role": "Stock User", "action_type": "Review"},
				{"step_order": 2, "role": "Department Head", "action_type": "Review"},
				{"step_order": 3, "role": "AVP", "action_type": "Final Approve"},
			],
			"cost_centers": [
				"Boiler (Bed Material) - BBPL", "BOILER BOP - BBPL", "BOILER ESP - BBPL",
				"BOILER FUEL-ASH - BBPL", "BOILER THERMAX - BBPL", "BOILER TRIVENI - BBPL",
				"CBG-Farming - BBPL", "CF- MARKETING - BBPL", "CF- Production - BBPL",
				"CF- Raw Material - BBPL", "Civil - BBPL", "DG Fuel - BBPL",
				"Electrical+ Services - BBPL", "HR, safety & and Furniture Material - BBPL",
				"LIASING - BBPL", "Main - BBPL", "Mechanical + Services - BBPL",
				"Mechanical Boiler - BBPL", "MISCELLANEOUS - BBPL",
				"Process (WTP/CPU BIOLOGICAL) - BBPL", "PROCESS ACC - BBPL",
				"PROCESS BOP - BBPL", "PROCESS DRYER - BBPL", "PROCESS ISGEK - BBPL",
				"PROCESS MILLING - BBPL", "PROCESS STRUCTURE - BBPL",
				"PROCESS WTP-CPU - BBPL", "TRIVENI TURBINE - BBPL",
				"Bhopal Office - BBPL", "RTS Betul - BBPL",
			],
		},
		{
			"name": "CAPEX", "route_name": "CAPEX", "is_active": 1,
			"steps": [
				{"step_order": 1, "role": "CEO", "action_type": "Final Approve"},
			],
			"cost_centers": [
				"CF- Capex - BBPL", "CBG CAPEX - BBPL", "NEW VALLEY CAPEX - BBPL",
				"BAC Capex - BBPL", "Capex - BBPL", "Delhi Office - BBPL",
			],
		},
		{
			"name": "IT", "route_name": "IT", "is_active": 1,
			"steps": [
				{"step_order": 1, "role": "AVP", "action_type": "Final Approve"},
			],
			"cost_centers": ["IT Hardware + Services - BBPL"],
		},
		{
			"name": "Store 1", "route_name": "Store 1", "is_active": 0,
			"steps": [
				{"step_order": 1, "role": "Production Head", "action_type": "Review"},
				{"step_order": 2, "role": "AVP", "action_type": "Final Approve"},
			],
			"cost_centers": [],
		},
	]

	for r in routes:
		doc = frappe.new_doc("TS MR Approval Route")
		doc.route_name = r["route_name"]
		doc.is_active = r["is_active"]
		for s in r["steps"]:
			doc.append("approval_steps", s)
		for cc in r["cost_centers"]:
			if frappe.db.exists("Cost Center", cc):
				doc.append("cost_centers", {"cost_center": cc})
		doc.flags.ignore_validate = True
		doc.flags.ignore_links = True
		doc.insert(ignore_permissions=True, set_name=r["name"])

	frappe.db.commit()


def seed_ts_settings():
	"""Set TS Settings defaults if not already configured."""
	if not frappe.db.exists("TS Settings", "TS Settings"):
		return

	doc = frappe.get_doc("TS Settings")
	changed = False

	defaults = {
		"sla_threshold_minutes": 120,
		"token_suffix_digits": 5,
		"default_warehouse": "Stores - BBPL",
		"default_accepted_warehouse": "Stores - BBPL",
		"auto_submit_grn": 1,
		"enable_po_approval": 1,
		"enable_mr_approval": 1,
		"enable_budget_check": 1,
		"default_monthly_distribution": "FY 25-26",
		"g2_print_mode": "Slip Only",
		"weighbridge_url": "http://122.186.18.59:5001/read",
		"weighbridge_timeout": 5,
	}

	for field, value in defaults.items():
		if hasattr(doc, field) and not doc.get(field):
			doc.set(field, value)
			changed = True

	# Inspection gate for Stores Receiving Dashboard — default ON (fail-closed).
	# Separate from `defaults` loop because `not doc.get(field)` would treat
	# an intentional OFF (0) the same as missing. tabSingles has no `modified`
	# column so we use raw SQL.
	if hasattr(doc, "block_grn_on_inspection_pending"):
		rows = frappe.db.sql("""
			SELECT value FROM `tabSingles`
			WHERE doctype='TS Settings' AND field='block_grn_on_inspection_pending' LIMIT 1
		""")
		if not rows:
			doc.set("block_grn_on_inspection_pending", 1)
			changed = True

	# v2.8.0 flow simplification flag — default ON. Same fail-closed pattern.
	if hasattr(doc, "ts_flow_v28_enabled"):
		rows = frappe.db.sql("""
			SELECT value FROM `tabSingles`
			WHERE doctype='TS Settings' AND field='ts_flow_v28_enabled' LIMIT 1
		""")
		if not rows:
			doc.set("ts_flow_v28_enabled", 1)
			changed = True

	# v2.8.1 PI QC Gate flag — default OFF (opt-in).
	# Flip ON only after QI team is ready to gate invoices.
	if hasattr(doc, "ts_qc_gate_enabled"):
		rows = frappe.db.sql("""
			SELECT value FROM `tabSingles`
			WHERE doctype='TS Settings' AND field='ts_qc_gate_enabled' LIMIT 1
		""")
		if not rows:
			doc.set("ts_qc_gate_enabled", 0)
			changed = True

	if changed:
		doc.save(ignore_permissions=True)
		frappe.db.commit()


def seed_cc_codes():
	"""Seed cc_code on Cost Centers for MR naming series."""
	CC_CODES = {
		"BAC Capex - BBPL": "BAC-CAP",
		"Betul Biofuel Private Limited- BBPL - BBPL": "BBPL",
		"Bhopal Office - BBPL": "BHO",
		"Boiler ( Bed Material, Charcoal & Consumable ) - BBPL": "BLR-CON",
		"BOILER BOP - BBPL": "BLR-BOP",
		"BOILER ESP - BBPL": "BLR-ESP",
		"BOILER FUEL/ASH - BBPL": "BLR-FA",
		"BOILER THERMEX - BBPL": "BLR-THX",
		"BOILER TRIVENI - BBPL": "BLR-TRV",
		"BOLIER THERMAX - BBPL": "BLR-TMX",
		"Capex - BBPL": "CAPEX",
		"CBG B.G-Gen - BBPL": "CBG-GEN",
		"CBG B.G-Upg - BBPL": "CBG-UPG",
		"CBG CAPEX - BBPL": "CBG-CAP",
		"CBG CIVIL - BBPL": "CBG-CIV",
		"CBG E&C - BBPL": "CBG-EC",
		"CBG Infra - BBPL": "CBG-INF",
		"CBG-Farming - BBPL": "CBG-FRM",
		"CF- Capex - BBPL": "CF-CAP",
		"CF- MARKETING - BBPL": "CF-MKT",
		"CF- Production - BBPL": "CF-PRD",
		"CF- Raw Material - BBPL": "CF-RM",
		"Civil - BBPL": "CIV",
		"CIVIL BOP - BBPL": "CIV-BOP",
		"CIVIL MATERIAL - BBPL": "CIV-MAT",
		"CO2 - BBPL": "CO2",
		"Coal - BBPL": "COAL",
		"DDGS - BBPL": "DDGS",
		"Delhi Office - BBPL": "DEL",
		"DG Fuel + Electricity bill - BBPL": "DG",
		"Electrical+ Services - BBPL": "ELC",
		"Expantion 150KLPD Mechnical - BBPL": "EXP-MEC",
		"Expantion Civil Work - BBPL": "EXP-CIV",
		"HR, safety & and Furniture Material - BBPL": "HR",
		"IT Hardware + Services - BBPL": "IT",
		"Labour Quarter - BBPL": "LBR",
		"LIASING - BBPL": "LIA",
		"Machinery - BBPL": "MACH",
		"Main - BBPL": "MAIN",
		"Mechanical + Services - BBPL": "MEC",
		"Mechanical Boiler + Services - BBPL": "MEC-BLR",
		"MISCELLANEOUS - BBPL": "MISC",
		"NEW VALLEY CAPEX - BBPL": "NV-CAP",
		"Process ( WTP/CPU, BIOLOGICAL) - BBPL": "PRC-BIO",
		"PROCESS ACC - BBPL": "PRC-ACC",
		"PROCESS BOP - BBPL": "PRC-BOP",
		"PROCESS DRYER - BBPL": "PRC-DRY",
		"PROCESS ISGEK - BBPL": "PRC-ISG",
		"PROCESS MILLING - BBPL": "PRC-MIL",
		"PROCESS STRUCTURE - BBPL": "PRC-STR",
		"PROCESS WTP/CPU - BBPL": "PRC-WTP",
		"RDPS-VIJAY INTERNATIONAL - BBPL": "RDPS",
		"RM MAIZE/RICE/DORB - BBPL": "RM",
		"RTS Betul - BBPL": "RTS",
		"TRIVENI TURBINE - BBPL": "TRV-TRB",
		# v2.9.5 — CFBBPL company cost centers (sister concern Cattle Feed BBF Pvt Ltd).
		# Discovered missing during build phase — added so MR naming on CFBBPL
		# doesn't fall through to the "MISC" fallback.
		"Main - CFBBPL": "CFB-MAIN",
		"Cattle Feed Betul Biofuel Private Limited - CFBBPL": "CFBBPL",
		# v2.9.8.17 — CF- Production - CFBBPL was missed in the v2.9.5 batch.
		# Caught by regression test (MR creation against this CC failed with "no
		# CC Code" error). Pattern matches BBPL counterpart "CF-PRD" with CFB- prefix.
		"CF- Production - CFBBPL": "CFB-PRD",
	}

	for cc_name, code in CC_CODES.items():
		if frappe.db.exists("Cost Center", cc_name):
			current = frappe.db.get_value("Cost Center", cc_name, "cc_code")
			if not current:
				frappe.db.set_value("Cost Center", cc_name, "cc_code", code)


def seed_locations():
	"""Create 20 physical locations if none exist."""
	if not frappe.db.exists("DocType", "TS Location"):
		return
	if frappe.db.count("TS Location") > 0:
		return

	locations = [
		"Admin Office", "Bhopal Office", "Boiler Area", "CBG Agriculture", "CBG Plant",
		"CF Marketing", "CF Production", "CF Store", "Civil Works", "Coal Yard",
		"Electrical Section", "HR Office", "IT Department", "Labour Quarter", "Main Store",
		"Mechanical Boiler", "Mechanical Workshop", "Power House", "Process Plant", "Weighbridge",
	]

	for loc in locations:
		doc = frappe.new_doc("TS Location")
		doc.location_name = loc
		doc.insert(ignore_permissions=True)

	frappe.db.commit()


def seed_list_view_settings():
	"""Create List View Settings for Material Request and Purchase Order."""
	settings = {
		"Material Request": {
			"fields": '[{"fieldname":"subject","width":2},{"fieldname":"status","width":2},{"fieldname":"ts_mr_status","width":2},{"fieldname":"schedule_date","width":2},{"fieldname":"cost_center","width":2},{"fieldname":"name","width":2}]',
		},
		"Purchase Order": {
			"fields": '[{"fieldname":"supplier_name","width":2},{"fieldname":"status","width":2},{"fieldname":"ts_approval_status","width":2},{"fieldname":"transaction_date","width":2},{"fieldname":"grand_total","width":2},{"fieldname":"name","width":2}]',
		},
	}

	for dt, config in settings.items():
		if frappe.db.exists("List View Settings", dt):
			continue
		doc = frappe.get_doc({
			"doctype": "List View Settings",
			"name": dt,
			"fields": config["fields"],
		})
		doc.insert(ignore_permissions=True, set_name=dt)

	frappe.db.commit()


def seed_custom_docperm():
	"""Create Custom DocPerm entries for TS and standard DocTypes if not already set."""
	from trustbit_ethanol.ts_gate_entry.seed_data import CUSTOM_DOCPERM

	for entry in CUSTOM_DOCPERM:
		parent = entry["parent"]
		role = entry["role"]
		permlevel = entry.get("permlevel", 0)

		# Skip if already exists
		existing = frappe.db.exists("Custom DocPerm", {
			"parent": parent, "role": role, "permlevel": permlevel
		})
		if existing:
			continue

		# Skip if role doesn't exist
		if not frappe.db.exists("Role", role):
			continue

		doc = frappe.get_doc({
			"doctype": "Custom DocPerm",
			"parent": parent,
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": role,
			"permlevel": permlevel,
			"read": entry.get("read", 0),
			"write": entry.get("write", 0),
			"create": entry.get("create", 0),
			"delete": entry.get("delete", 0),
			"submit": entry.get("submit", 0),
			"cancel": entry.get("cancel", 0),
			"amend": entry.get("amend", 0),
			"report": entry.get("report", 0),
			"export": entry.get("export", 0),
			"share": entry.get("share", 0),
			"print": entry.get("print", 0),
			"email": entry.get("email", 0),
		})
		doc.db_insert()

	frappe.db.commit()


def seed_property_setters():
	"""Create Property Setters for TS and standard DocTypes if not already set."""
	from trustbit_ethanol.ts_gate_entry.seed_data import PROPERTY_SETTERS

	for entry in PROPERTY_SETTERS:
		doc_type = entry["doc_type"]
		field_name = entry.get("field_name")
		prop = entry["property"]

		# Check if already exists
		filters = {"doc_type": doc_type, "property": prop}
		if field_name:
			filters["field_name"] = field_name

		if frappe.db.exists("Property Setter", filters):
			continue

		doc = frappe.get_doc({
			"doctype": "Property Setter",
			"doc_type": doc_type,
			"field_name": field_name,
			"property": prop,
			"property_type": entry.get("property_type"),
			"value": entry.get("value"),
			"doctype_or_field": entry.get("doctype_or_field", "DocField"),
		})
		doc.insert(ignore_permissions=True)

	frappe.db.commit()


def seed_over_receipt_allowance():
	"""v2.8.1.4: ensure all Items have over_delivery_receipt_allowance = 10%.

	BBF policy — weighed commodities commonly over-receive by small margins
	(moisture variance, scale calibration). 10% tolerance prevents spurious
	submit-blocks on PR. Stock Settings global is also set to 10.

	Idempotent: re-runs harmlessly; only updates items <10%.
	"""
	# Global Stock Settings fallback
	try:
		current = frappe.db.get_single_value("Stock Settings", "over_delivery_receipt_allowance")
		if not current or float(current) < 10:
			frappe.db.set_single_value("Stock Settings", "over_delivery_receipt_allowance", 10)
	except Exception as e:
		frappe.log_error(message=f"seed_over_receipt_allowance (Stock Settings): {e}",
						 title="seed_over_receipt_allowance")

	# Bulk-set all items below 10%. Using direct SQL since we have thousands of items
	# and ORM save would trigger on_update hooks per item.
	try:
		frappe.db.sql("""
			UPDATE `tabItem`
			SET over_delivery_receipt_allowance = 10
			WHERE over_delivery_receipt_allowance IS NULL
			   OR over_delivery_receipt_allowance < 10
		""")
		frappe.db.commit()
		frappe.clear_cache(doctype="Item")
	except Exception as e:
		frappe.log_error(message=f"seed_over_receipt_allowance (Items): {e}",
						 title="seed_over_receipt_allowance")


def patch_wkhtmltopdf_whitelist():
	"""Patch Frappe core: add @frappe.whitelist() to is_wkhtmltopdf_valid.
	This function is called from JS but missing the decorator in Frappe v15.
	Without it, PDF print shows 'Invalid wkhtmltopdf version' warning."""
	import os
	pdf_path = os.path.join(frappe.get_app_path("frappe"), "utils", "pdf.py")
	try:
		content = open(pdf_path).read()
		if "@frappe.whitelist()" not in content.split("def is_wkhtmltopdf_valid")[0].split("def ")[-1]:
			# Only patch if not already patched
			content = content.replace(
				"@redis_cache(ttl=60 * 60)\ndef is_wkhtmltopdf_valid",
				"@frappe.whitelist()\n@redis_cache(ttl=60 * 60)\ndef is_wkhtmltopdf_valid"
			)
			open(pdf_path, "w").write(content)
	except Exception:
		pass


def seed_global_defaults():
	"""Set Global Defaults — default company, fiscal year, country, currency.

	Note: Frappe v15 Global Defaults DocType does NOT have `current_fiscal_year`.
	Default fiscal year is stored in `tabDefaultValue` via `frappe.defaults.set_global_default`.
	(Lesson 165 — fixed 15 Apr 2026, was AttributeError on every migrate.)
	"""
	gd = frappe.get_doc("Global Defaults")
	changed = False

	if not gd.default_company:
		company = frappe.db.get_value("Company", {"abbr": "BBPL"}, "name")
		if company:
			gd.default_company = company
			changed = True

	if not gd.country:
		gd.country = "India"
		changed = True

	if changed:
		gd.save(ignore_permissions=True)
		frappe.db.commit()

	# Default fiscal year — set via DefaultValue since the field was removed from Global Defaults
	current_fy = frappe.defaults.get_global_default("fiscal_year")
	if not current_fy:
		fy = frappe.get_all("Fiscal Year", filters={"disabled": 0}, fields=["name"],
			order_by="year_start_date desc", limit=1)
		if fy:
			frappe.defaults.set_global_default("fiscal_year", fy[0].name)
			frappe.db.commit()


def seed_navbar_website_settings():
	"""Set Navbar logo and Website Settings (app name, footer, branding)."""
	ns = frappe.get_doc("Navbar Settings")
	if not ns.app_logo:
		ns.app_logo = "/files/client_logo.png"
		ns.save(ignore_permissions=True)

	ws = frappe.get_doc("Website Settings")
	changed = False
	if not ws.app_name:
		ws.app_name = "Betul Biofuel Pvt. Ltd."
		changed = True
	if not ws.banner_image:
		ws.banner_image = "/files/client_logo.png"
		changed = True
	if not ws.footer_powered:
		ws.footer_powered = "Trustbit Technologies Pvt. Ltd."
		changed = True
	if changed:
		ws.save(ignore_permissions=True)

	frappe.db.commit()


def seed_fiscal_years():
	"""Create Fiscal Years 2025-2026 and 2026-2027 if not exist."""
	fiscal_years = [
		{"name": "2025-2026", "start": "2025-04-01", "end": "2026-03-31"},
		{"name": "2026-2027", "start": "2026-04-01", "end": "2027-03-31"},
	]

	for fy in fiscal_years:
		if frappe.db.exists("Fiscal Year", fy["name"]):
			continue
		doc = frappe.get_doc({
			"doctype": "Fiscal Year",
			"year": fy["name"],
			"year_start_date": fy["start"],
			"year_end_date": fy["end"],
			"disabled": 0,
		})
		doc.insert(ignore_permissions=True, set_name=fy["name"])

	frappe.db.commit()


def seed_monthly_distributions():
	"""Create Monthly Distributions for budget control if not exist."""
	months = ["January", "February", "March", "April", "May", "June",
	          "July", "August", "September", "October", "November", "December"]

	distributions = {
		"FY 25-26": {"fiscal_year": "2025-2026", "pcts": [8.33] * 12},
		"Grain Seasonal Distribution": {"fiscal_year": "2025-2026", "pcts": [5.0, 5.0, 8.0, 10.0, 12.0, 12.0, 12.0, 10.0, 8.0, 8.0, 5.0, 5.0]},
	}

	for name, config in distributions.items():
		if frappe.db.exists("Monthly Distribution", name):
			continue
		if not frappe.db.exists("Fiscal Year", config["fiscal_year"]):
			continue
		doc = frappe.new_doc("Monthly Distribution")
		doc.distribution_id = name
		doc.fiscal_year = config["fiscal_year"]
		for i, month in enumerate(months):
			doc.append("percentages", {"month": month, "percentage_allocation": config["pcts"][i]})
		doc.insert(ignore_permissions=True, set_name=name)

	frappe.db.commit()


def seed_brands():
	"""Create Brand records if not exist."""
	brands = ["Apple", "Cisco", "D-Link", "Eurotherm", "Exide",
	          "Jindal", "PROMECON", "SKF/FAG", "TP-Link", "Xoptix Limited (UK)"]

	for brand_name in brands:
		if frappe.db.exists("Brand", brand_name):
			continue
		frappe.get_doc({"doctype": "Brand", "brand": brand_name}).insert(ignore_permissions=True)

	frappe.db.commit()


# ═══════════════════════════════════════════════════════════════════════
#  v2.9.9 — Material Transfer Independent Flow (Stores Manager role + kill switch)
# ═══════════════════════════════════════════════════════════════════════

def _seed_stores_manager_role():
	"""v2.9.9 — Create the Stores Manager role for Material Transfer flow."""
	if frappe.db.exists("Role", "Stores Manager"):
		return
	frappe.get_doc({
		"doctype": "Role",
		"role_name": "Stores Manager",
		"desk_access": 1,
		"disabled": 0,
	}).insert(ignore_permissions=True)
	frappe.db.commit()


def _seed_stores_manager_perms():
	"""v2.9.9 — Permission rows for Stores Manager: MR + Stock Entry + Item + Warehouse."""
	perms_config = {
		"Material Request": {"read": 1, "write": 1, "create": 0, "submit": 0,
		                     "cancel": 0, "delete": 0, "report": 1, "print": 1, "email": 1},
		"Stock Entry": {"read": 1, "write": 1, "create": 1, "submit": 1,
		                "cancel": 1, "delete": 0, "report": 1, "print": 1, "email": 1},
		"Item": {"read": 1, "report": 1},
		"Warehouse": {"read": 1, "report": 1},
	}
	for doctype, perms in perms_config.items():
		existing = frappe.db.exists("DocPerm",
			{"parent": doctype, "role": "Stores Manager", "permlevel": 0})
		if existing:
			continue
		fields = {"doctype": "DocPerm", "parent": doctype, "parenttype": "DocType",
		          "parentfield": "permissions", "role": "Stores Manager", "permlevel": 0}
		fields.update(perms)
		try:
			frappe.get_doc(fields).insert(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(f"Stores Manager perms on {doctype} failed: {e}",
			                 "v2.9.9 setup")
	frappe.db.commit()


def _seed_stores_manager_assignments():
	"""v2.9.9 — One-shot: assign Stores Manager role to all current Stock Manager users.

	Idempotent via TS Settings flag `ts_v299_stores_managers_seeded`. Skips on re-runs.
	Admins (Administrator/Guest) excluded; Stock Manager users get Stores Manager
	assigned automatically so the existing IT Head + key personnel can approve transfers
	immediately on deploy.
	"""
	if frappe.db.get_single_value("TS Settings", "ts_v299_stores_managers_seeded"):
		return

	stock_managers = frappe.db.sql_list("""
		SELECT DISTINCT parent FROM `tabHas Role`
		WHERE role = 'Stock Manager' AND parent NOT IN ('Administrator', 'Guest')
	""")
	already_assigned = set(frappe.db.sql_list(
		"SELECT parent FROM `tabHas Role` WHERE role = 'Stores Manager'"))

	added = 0
	for user in stock_managers:
		if user in already_assigned:
			continue
		try:
			frappe.get_doc({
				"doctype": "Has Role",
				"parent": user,
				"parenttype": "User",
				"parentfield": "roles",
				"role": "Stores Manager",
			}).insert(ignore_permissions=True)
			added += 1
		except Exception as e:
			frappe.log_error(f"Stores Manager assignment to {user} failed: {e}",
			                 "v2.9.9 setup")

	# Mark seeded so we don't re-fire on every migrate (preserves admin's manual removals)
	frappe.db.set_single_value("TS Settings", "ts_v299_stores_managers_seeded", 1)
	frappe.db.commit()
	if added:
		frappe.log_error(f"v2.9.9: Stores Manager role assigned to {added} Stock Manager users",
		                 "v2.9.9 setup")


def _seed_material_transfer_kill_switch():
	"""v2.9.9 — Add kill-switch + idempotency Custom Fields to TS Settings.

	`ts_material_transfer_flow_enabled` (Check, default 1, permlevel=1) — the
	feature flag. When 0: Transfer-type MRs route through the legacy Purchase
	chain (rollback path). All v2.9.9 type-guards check this flag.

	`ts_v299_stores_managers_seeded` (Check, default 0, hidden) — used by
	`_seed_stores_manager_assignments` to fire only once.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
	create_custom_fields({
		"TS Settings": [
			{
				"fieldname": "ts_material_transfer_flow_enabled",
				"label": "Material Transfer Flow Enabled",
				"fieldtype": "Check",
				"default": "1",
				"permlevel": 1,
				"insert_after": "ts_avp_deputy_enabled",
				"description": "v2.9.9 kill switch — when 0, Material Transfer MRs use the legacy Purchase approval chain (rollback path).",
			},
			{
				"fieldname": "ts_v299_stores_managers_seeded",
				"label": "Stores Managers Seeded (v2.9.9)",
				"fieldtype": "Check",
				"default": "0",
				"hidden": 1,
				"read_only": 1,
				"insert_after": "ts_material_transfer_flow_enabled",
				"description": "Internal flag — v2.9.9 setup writes this to 1 after auto-assigning Stores Manager role. Prevents re-firing on subsequent migrates.",
			},
		]
	})
	frappe.db.commit()


def _seed_vehicle_origin_fields():
	"""v2.9.14 — Add `vehicle_origin` (Data) Custom Field on G1 / G2 / PR / PI.

	Direct-insert via frappe.get_doc with ignore_links=True so a pre-existing
	broken Document Link on TS Gate Entry (referencing the long-removed
	"TS G1 Entry" doctype) does not block the seed. PR's gets fetch_from on
	`ts_gate_entry.vehicle_origin` for declarative auto-fetch from G1.
	"""
	rows = [
		("TS Gate Entry", "vehicle_number", None),
		("TS Token", "vehicle_number", None),
		("Purchase Receipt", "ts_gate_entry", "ts_gate_entry.vehicle_origin"),
		("Purchase Invoice", "bill_date", None),
	]
	for dt, anchor, fetch_from in rows:
		if frappe.db.exists("Custom Field", {"dt": dt, "fieldname": "vehicle_origin"}):
			continue
		spec = {
			"doctype": "Custom Field",
			"dt": dt,
			"fieldname": "vehicle_origin",
			"fieldtype": "Data",
			"label": "Vehicle Origin",
			"insert_after": anchor,
			"length": 140,
			"description": "Origin of vehicle (city / state / source). Manually entered at G1; auto-fetched on G2/PR/PI; editable on each.",
		}
		if fetch_from:
			spec["fetch_from"] = fetch_from
			spec["fetch_if_empty"] = 1
		cf = frappe.get_doc(spec)
		cf.flags.ignore_links = True
		cf.insert(ignore_permissions=True, ignore_links=True)
	# Schema sync — direct insert with ignore_links bypasses Frappe's auto-updatedb
	for dt, _, _ in rows:
		frappe.db.updatedb(dt)
	frappe.db.commit()


def _seed_pi_update_stock_return_visibility():
	"""v2.9.14.3 — Override Purchase Invoice update_stock depends_on to show on Returns.

	Native ERPNext hides update_stock when any PI item has pr_detail (linked to a
	Purchase Receipt item) — this prevents double stock-update on PI from PR. But
	for Purchase Returns the operator legitimately needs update_stock available
	to handle the return stock-out. Add `doc.is_return ||` to the eval so returns
	always show the field, regardless of pr_detail.
	"""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter
	new_value = "eval:doc.is_return || doc.items.every((item) => !item.pr_detail)"
	make_property_setter(
		"Purchase Invoice", "update_stock", "depends_on", new_value, "Code",
		validate_fields_for_doctype=False,
	)
	frappe.db.commit()


def _seed_dsg_receipt_context_fields():
	"""v2.9.15.1 — Add Receipt Context section (5 RO fields) to TS Deduction Suggestion.

	Snapshot at insert: vehicle_number, rst_number, supplier_code, supplier_name.
	Live-fetched on form load: net_weight (from matching TS Weighbridge Log).
	"""
	specs = [
		("dsg_receipt_context_section", "Section Break", "Receipt Context", "naming_series", None, None),
		("vehicle_number", "Data", "Vehicle Number", "dsg_receipt_context_section", None, None),
		("rst_number", "Data", "RST Number", "vehicle_number", None, "From Weighbridge Log"),
		("net_weight", "Float", "Total Net Weight (kg)", "rst_number", None, "Live from matching Weighbridge Log"),
		("dsg_receipt_context_col_break", "Column Break", None, "net_weight", None, None),
		("supplier_code", "Link", "Supplier Code", "dsg_receipt_context_col_break", "Supplier", "From PO"),
		("supplier_name", "Data", "Supplier Name", "supplier_code", None, None),
		("purchase_order", "Link", "Purchase Order", "supplier_name", "Purchase Order", "From QI"),
	]
	for fieldname, fieldtype, label, anchor, options, description in specs:
		if frappe.db.exists("Custom Field", {"dt": "TS Deduction Suggestion", "fieldname": fieldname}):
			continue
		spec = {
			"doctype": "Custom Field", "dt": "TS Deduction Suggestion",
			"fieldname": fieldname, "fieldtype": fieldtype, "insert_after": anchor,
		}
		if label:
			spec["label"] = label
		if options:
			spec["options"] = options
		if description:
			spec["description"] = description
		if fieldtype not in ("Section Break", "Column Break"):
			spec["read_only"] = 1
			spec["no_copy"] = 1
		if fieldtype == "Float":
			spec["precision"] = "3"
		frappe.get_doc(spec).insert(ignore_permissions=True)
	# Bump parent DocType.modified so browsers with cached form metadata
	# (Frappe's getdoctype returns "use_cache" if client cached_timestamp
	# matches server) invalidate and re-fetch — otherwise newly added
	# Custom Fields stay invisible until user clears localStorage.
	frappe.db.set_value(
		"DocType", "TS Deduction Suggestion", "modified",
		frappe.utils.now(), update_modified=False,
	)
	frappe.db.commit()


def _seed_pi_receipt_context_fields():
	"""v2.9.16.4 — Add 'Token / Receipt Context' section to Purchase Invoice.

	5 read-only Link/Data fields snapshotted at PI insert from the first
	linked PR (via items[].purchase_receipt). Header-level display summary;
	does NOT change ERPNext's per-item billing/stock semantics.

	Also performs one-time backfill of existing PIs that have a linked PR
	but blank Receipt Context fields. Uses db.set_value(update_modified=False)
	to handle submitted/cancelled rows without bumping modified.
	"""
	specs = [
		("ts_pi_receipt_context_section", "Section Break", "Token / Receipt Context", "ts_po_reference", None, None),
		("ts_token", "Link", "Token Number / Gate Pass", "ts_pi_receipt_context_section", "TS Token", "From linked PR"),
		("ts_rst_number", "Data", "RST Number", "ts_token", None, "From linked PR"),
		("ts_quality_inspection", "Link", "Quality Inspection", "ts_rst_number", "TS Quality Inspection", "Looked up by token"),
		("ts_pi_receipt_context_col_break", "Column Break", None, "ts_quality_inspection", None, None),
		("ts_deduction_sheet", "Link", "Deduction Sheet", "ts_pi_receipt_context_col_break", "TS Deduction Sheet", "From linked PR"),
		("ts_purchase_receipt", "Link", "Purchase Receipt", "ts_deduction_sheet", "Purchase Receipt", "First linked PR"),
	]
	for fieldname, fieldtype, label, anchor, options, description in specs:
		if frappe.db.exists("Custom Field", {"dt": "Purchase Invoice", "fieldname": fieldname}):
			continue
		spec = {
			"doctype": "Custom Field", "dt": "Purchase Invoice",
			"fieldname": fieldname, "fieldtype": fieldtype, "insert_after": anchor,
		}
		if label:
			spec["label"] = label
		if options:
			spec["options"] = options
		if description:
			spec["description"] = description
		if fieldtype not in ("Section Break", "Column Break"):
			spec["read_only"] = 1
			spec["no_copy"] = 1
		frappe.get_doc(spec).insert(ignore_permissions=True)
	# Bump parent DocType.modified to invalidate browser form-meta cache
	# (Lesson 263 — without this, newly added Custom Fields stay invisible
	# to users until localStorage.clear()).
	frappe.db.set_value(
		"DocType", "Purchase Invoice", "modified",
		frappe.utils.now(), update_modified=False,
	)
	frappe.db.commit()
	# One-time backfill of existing PIs (idempotent — only touches rows whose
	# ts_token is currently NULL/empty and that have at least one item with
	# purchase_receipt set). Submitted/cancelled docs are safe because
	# update_modified=False avoids modified-bump (no version row created).
	candidates = frappe.db.sql("""
		SELECT DISTINCT pi.name AS pi_name, pri.purchase_receipt
		FROM `tabPurchase Invoice` pi
		JOIN `tabPurchase Invoice Item` pri ON pri.parent = pi.name
		WHERE (pi.ts_token IS NULL OR pi.ts_token = '')
		  AND pri.purchase_receipt IS NOT NULL
		  AND pri.purchase_receipt != ''
		ORDER BY pi.name, pri.idx
	""", as_dict=True)
	seen = set()
	backfilled = 0
	for row in candidates:
		if row.pi_name in seen:
			continue
		seen.add(row.pi_name)
		pr = frappe.db.get_value(
			"Purchase Receipt", row.purchase_receipt,
			["ts_token", "custom_rst_number"],
			as_dict=True,
		)
		if not pr:
			continue
		# DS link chain: PR.ts_token -> QI(token_number=...) -> DS(quality_inspection=...)
		qi = ds = None
		if pr.ts_token:
			qi = frappe.db.get_value(
				"TS Quality Inspection",
				{"token_number": pr.ts_token, "docstatus": ["!=", 2]},
				"name",
			)
			if qi:
				ds = frappe.db.get_value(
					"TS Deduction Sheet",
					{"quality_inspection": qi, "docstatus": ["!=", 2]},
					"name",
				)
		updates = {
			"ts_purchase_receipt": row.purchase_receipt,
			"ts_token": pr.ts_token or None,
			"ts_rst_number": pr.custom_rst_number or None,
			"ts_deduction_sheet": ds or None,
			"ts_quality_inspection": qi or None,
		}
		frappe.db.set_value("Purchase Invoice", row.pi_name, updates, update_modified=False)
		backfilled += 1
	if backfilled:
		frappe.db.commit()


def _seed_quality_lab_dsg_shortcuts():
	"""Append 'New Deduction Suggestion' + 'All Deduction Suggestions' shortcuts
	to the Quality Lab workspace, idempotently.

	reload_doc does NOT sync Workspace Shortcut child rows for an EXISTING
	workspace (Lesson 261). So after a fresh `bench migrate` adds the JSON to
	the codebase, the child rows still aren't in DB. This seeder appends them
	by Python append + save, mirroring the v2.9.16.1 'Stock Reports' workspace
	pattern but applied as an ADD to a workspace that already exists.

	Bumps `Workspace.modified` afterwards so browsers invalidate their cached
	workspace layout (echoes Lesson 263).
	"""
	if not frappe.db.exists("Workspace", "Quality Lab"):
		return
	ws = frappe.get_doc("Workspace", "Quality Lab")
	existing = {s.label for s in (ws.shortcuts or [])}
	added = False
	if "New Deduction Suggestion" not in existing:
		ws.append("shortcuts", {
			"label": "New Deduction Suggestion",
			"link_to": "TS Deduction Suggestion",
			"type": "DocType",
		})
		added = True
	if "All Deduction Suggestions" not in existing:
		ws.append("shortcuts", {
			"label": "All Deduction Suggestions",
			"link_to": "TS Deduction Suggestion",
			"type": "DocType",
			"doc_view": "List",
		})
		added = True
	# Also sync the `content` JSON column from the workspace JSON file so the
	# "Quick Access" layout renders the 2 new shortcut blocks even if migrate
	# already cached an older content (Lesson 261 — reload_doc doesn't always
	# refresh content). Idempotent — same JSON each run.
	import os, json as _json
	ws_json_path = os.path.join(
		frappe.get_app_path("trustbit_ethanol"),
		"ts_gate_entry", "workspace", "quality_lab", "quality_lab.json",
	)
	try:
		with open(ws_json_path) as _f:
			file_content = _json.load(_f).get("content")
		if file_content and file_content != ws.content:
			ws.content = file_content
			added = True
	except Exception:
		pass
	if added:
		ws.save(ignore_permissions=True)
		frappe.db.set_value(
			"Workspace", "Quality Lab", "modified",
			frappe.utils.now(), update_modified=False,
		)
		frappe.db.commit()


def _seed_cost_center_approval_perms():
	"""v2.9.15.2 — Grant Cost Center read perm to BBF approval-tier roles.

	PO + MR list views have Property Setter cost_center.in_standard_filter=1
	(seeded by setup_cc_standard_filter.py v2.8.8.3). Frappe renders that as
	a Link autocomplete, which requires read perm on Cost Center. Approval
	roles created by BBF (Grain Purchase Manager, Department Head, etc.) lack
	that perm, so the user sees a "No permission for Cost Center" popup that
	blocks the PO/MR list view.

	Reproduced on demo for grain.manager@betulbiofuel.com (roles: Grain
	Purchase Manager + Grain Manager + Quality Manager — none had CC read).

	Read-only at permlevel=0; CC scoping for approvals continues via
	TS CC Approval Config.get_user_allowed_cost_centers (independent layer).

	Idempotent — skips rows that already exist; skips roles that don't exist.
	Pattern mirrors seed_custom_docperm() (Lesson 169 safe — uses get_doc +
	db_insert, NOT raw INSERT which would drop Standard DocPerm).
	"""
	roles = [
		"Grain Purchase Manager", "Department Head", "General Manager",
		"AVP", "Grain Manager", "Quality Manager", "Admin Reception",
	]
	for role in roles:
		if not frappe.db.exists("Role", role):
			continue
		if frappe.db.exists("Custom DocPerm", {
			"parent": "Cost Center", "role": role, "permlevel": 0,
		}):
			continue
		try:
			frappe.get_doc({
				"doctype": "Custom DocPerm",
				"parent": "Cost Center",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": 0,
				"read": 1,
			}).db_insert()
		except Exception as e:
			frappe.log_error(
				title="seed_cost_center_approval_perms",
				message=f"Failed for role {role}: {e}",
			)
	frappe.db.commit()
	frappe.clear_cache(doctype="Cost Center")


def _seed_gate_entry_docperm():
	"""v2.9.16.7 — Idempotent Custom DocPerm seeder for TS Gate Entry.

	Lesson 169 master-data parity gap: prod had 10 Custom DocPerm rows on
	TS Gate Entry (including Weighbridge Operator + 9 others), demo had
	only 2 (IT Head + System Manager). Since Custom DocPerm exists on the
	doctype, Standard DocPerm is fully overridden — so any role not in
	Custom DocPerm gets ZERO permission. Pure-WB-Operator users on demo
	got "No permission for TS Gate Entry" popup on every Weighbridge Log
	new-form load (JS line 83 calls frappe.db.get_value on TS Gate Entry).

	Idempotent: skips rows that already exist; skips roles that don't exist.
	Forward-safe: any fresh install or migrate re-asserts the 10-row state.
	Pattern mirrors v2.9.15.2 _seed_cost_center_approval_perms.
	"""
	roles_read_only = [
		"Accounts Manager", "CEO", "G1 Security", "G2 Gate Operator",
		"IT Head", "MD", "Quality Inspector", "Stores User",
		"System Manager", "Weighbridge Operator",
	]
	for role in roles_read_only:
		if not frappe.db.exists("Role", role):
			continue
		if frappe.db.exists("Custom DocPerm", {
			"parent": "TS Gate Entry", "role": role, "permlevel": 0,
		}):
			continue
		try:
			frappe.get_doc({
				"doctype": "Custom DocPerm",
				"parent": "TS Gate Entry",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": 0,
				"read": 1, "write": 0, "create": 0, "delete": 0,
				"report": 1, "export": 1, "share": 0, "print": 1, "email": 0,
			}).db_insert()
		except Exception:
			pass
	frappe.db.commit()
	frappe.clear_cache(doctype="TS Gate Entry")


def _seed_budget_override_fields():
	"""v2.10.0 — Install Custom Fields on Material Request + Purchase Order for the
	budget-override approval flow, plus the list view config for the new doctype.

	Custom Fields installed (idempotent via create_custom_fields):
	- ts_budget_override_ref (Link → TS Budget Override Approval, read-only, permlevel=1)
	- ts_budget_breach_flag (Check, hidden, read-only, permlevel=1)

	Both anchor to the existing status field so they ride alongside the approval pill
	(insert_after = ts_mr_status / ts_approval_status). Permlevel=1 is the tamper wall
	(Lesson 162). The Lesson 263 modified-bump at the bottom invalidates any cached
	form-meta in user browsers so the new fields render without a hard refresh.
	"""
	custom_fields = {
		"Material Request": [
			{
				"fieldname": "ts_budget_override_ref",
				"fieldtype": "Link",
				"options": "TS Budget Override Approval",
				"label": "Budget Override Ref",
				"insert_after": "ts_mr_status",
				"read_only": 1,
				"permlevel": 1,
				"depends_on": "eval:doc.ts_budget_override_ref",
				"translatable": 0,
				"description": "Set automatically when this MR is held pending CEO budget approval.",
			},
			{
				"fieldname": "ts_budget_breach_flag",
				"fieldtype": "Check",
				"label": "Budget Breached",
				"insert_after": "ts_budget_override_ref",
				"read_only": 1,
				"permlevel": 1,
				"hidden": 1,
				"default": 0,
				"translatable": 0,
			},
		],
		"Purchase Order": [
			{
				"fieldname": "ts_budget_override_ref",
				"fieldtype": "Link",
				"options": "TS Budget Override Approval",
				"label": "Budget Override Ref",
				"insert_after": "ts_approval_status",
				"read_only": 1,
				"permlevel": 1,
				"depends_on": "eval:doc.ts_budget_override_ref",
				"translatable": 0,
				"description": "Set automatically when this PO is held pending CEO budget approval.",
			},
			{
				"fieldname": "ts_budget_breach_flag",
				"fieldtype": "Check",
				"label": "Budget Breached",
				"insert_after": "ts_budget_override_ref",
				"read_only": 1,
				"permlevel": 1,
				"hidden": 1,
				"default": 0,
				"translatable": 0,
			},
		],
	}
	_create_custom_fields(custom_fields, ignore_validate=True)

	# List view config for TS Budget Override Approval (Lesson 231 — exact-set, user spec).
	list_view_doctype = "TS Budget Override Approval"
	if frappe.db.exists("DocType", list_view_doctype):
		listview_columns = [
			{"key": "status", "width": "8"},
			{"key": "reference_doctype", "width": "8"},
			{"key": "reference_name", "width": "10"},
			{"key": "cost_center", "width": "10"},
			{"key": "breach_type", "width": "6"},
			{"key": "source_amount", "width": "10"},
			{"key": "submitted_by", "width": "10"},
		]
		import json as _json

		settings_name = list_view_doctype
		if frappe.db.exists("List View Settings", settings_name):
			frappe.db.set_value(
				"List View Settings",
				settings_name,
				{
					"fields": _json.dumps(listview_columns),
					"disable_count": 0,
					"disable_sidebar_stats": 0,
				},
				update_modified=False,
			)
		else:
			try:
				frappe.get_doc({
					"doctype": "List View Settings",
					"name": settings_name,
					"fields": _json.dumps(listview_columns),
				}).db_insert()
			except Exception:
				pass

	# Lesson 263 — bump tabDocType.modified so cached form meta invalidates for users.
	for dt in ("Material Request", "Purchase Order", "TS Budget Override Approval"):
		if frappe.db.exists("DocType", dt):
			try:
				frappe.db.set_value(
					"DocType", dt, "modified", frappe.utils.now(), update_modified=False
				)
			except Exception:
				pass
	frappe.db.commit()
	frappe.clear_cache(doctype="Material Request")
	frappe.clear_cache(doctype="Purchase Order")
	if frappe.db.exists("DocType", "TS Budget Override Approval"):
		frappe.clear_cache(doctype="TS Budget Override Approval")


def _seed_payment_amount_gst_field():
	"""v2.10.0.8 — display-only 'Including GST' toggle on Purchase Order.

	Controls how the BBPL Purchase Order print format renders each Payment
	Schedule row's amount. Default=1 keeps current behavior (flat inclusive
	amount). When unticked, the print format decomposes each row into
	'net + GST tax' parts for the supplier. DB stored values unchanged.
	"""
	if frappe.db.exists("Custom Field", {"dt": "Purchase Order", "fieldname": "ts_payment_amount_includes_gst"}):
		return
	frappe.get_doc({
		"doctype": "Custom Field",
		"dt": "Purchase Order",
		"fieldname": "ts_payment_amount_includes_gst",
		"label": "Including GST",
		"fieldtype": "Check",
		"default": "1",
		"insert_after": "payment_schedule",
		"description": "If ticked (default), BBPL Purchase Order PDF shows each Payment Schedule amount as the flat inclusive value. If unticked, the PDF decomposes each row into 'net amount + GST tax'. Display-only — does not change ERPNext's stored payment_amount math.",
		"no_copy": 0,
		"translatable": 0,
	}).insert(ignore_permissions=True)
	# Lesson 263 — bump tabDocType.modified so cached form metadata invalidates client-side
	frappe.db.set_value("DocType", "Purchase Order", "modified", frappe.utils.now(), update_modified=False)
	frappe.db.commit()


def seed_gst_breakdown_field():
	"""Display-only GST Breakdown + editable GST Due Date on Purchase Order (Terms tab).

	Two custom fields, shown ONLY when "Including GST" (ts_payment_amount_includes_gst)
	is UNticked (depends_on):
	  - ts_gst_due_date       : editable Date the user sets; printed on the BBPL PO PDF.
	  - ts_gst_breakdown_html : read-only CGST/SGST/IGST + Total GST table (with a
	    GST Due Date row), rendered by public/js/po_payment_amount_gst.js + the PDF.
	When unticked the Payment Schedule shows the NET (ex-GST) amount; ticked shows the
	native with-GST amount (JS + print-format only). Display-only; ERPNext's stored
	payment_amount / tax math is untouched. Fully idempotent (create-or-update).
	"""
	COND = "eval:!doc.ts_payment_amount_includes_gst"

	# 1) Editable GST Due Date (date picker), shown only when "Including GST" is unticked
	if not frappe.db.exists("Custom Field", {"dt": "Purchase Order", "fieldname": "ts_gst_due_date"}):
		frappe.get_doc({
			"doctype": "Custom Field",
			"dt": "Purchase Order",
			"fieldname": "ts_gst_due_date",
			"label": "GST Due Date",
			"fieldtype": "Date",
			"insert_after": "ts_payment_amount_includes_gst",
			"depends_on": COND,
			"description": "Editable GST due date. Shown with the GST Breakdown only when 'Including GST' is unticked; printed on the BBPL Purchase Order PDF.",
			"no_copy": 0,
			"print_hide": 1,
			"translatable": 0,
		}).insert(ignore_permissions=True)
	else:
		frappe.db.set_value("Custom Field", "Purchase Order-ts_gst_due_date",
			{"depends_on": COND, "insert_after": "ts_payment_amount_includes_gst"})

	# 2) Read-only GST Breakdown table, positioned after the date, same visibility condition
	if not frappe.db.exists("Custom Field", {"dt": "Purchase Order", "fieldname": "ts_gst_breakdown_html"}):
		frappe.get_doc({
			"doctype": "Custom Field",
			"dt": "Purchase Order",
			"fieldname": "ts_gst_breakdown_html",
			"label": "GST Breakdown",
			"fieldtype": "HTML",
			"insert_after": "ts_gst_due_date",
			"depends_on": COND,
			"description": "Read-only CGST/SGST/IGST + Total GST summary, auto-computed from this PO's taxes. Display-only — also shown on the BBPL Purchase Order PDF.",
			"no_copy": 1,
			"print_hide": 1,
			"translatable": 0,
		}).insert(ignore_permissions=True)
	else:
		frappe.db.set_value("Custom Field", "Purchase Order-ts_gst_breakdown_html",
			{"depends_on": COND, "insert_after": "ts_gst_due_date"})

	# 3) Refresh the "Including GST" toggle description (decomposition retired -> net/with-GST swap + breakdown)
	if frappe.db.exists("Custom Field", "Purchase Order-ts_payment_amount_includes_gst"):
		frappe.db.set_value(
			"Custom Field", "Purchase Order-ts_payment_amount_includes_gst", "description",
			"If unticked, the Payment Schedule shows the NET (ex-GST) amount and the GST Breakdown table "
			"(CGST/SGST/IGST + Total GST Amount) + editable GST Due Date appear on the PO Terms tab and the "
			"BBPL Purchase Order PDF. Ticked shows the with-GST amount. Display-only — does not change "
			"ERPNext's stored payment_amount / tax math.")

	# Lesson 263 — bump tabDocType.modified so cached form metadata invalidates client-side
	frappe.db.set_value("DocType", "Purchase Order", "modified", frappe.utils.now(), update_modified=False)
	frappe.db.commit()
