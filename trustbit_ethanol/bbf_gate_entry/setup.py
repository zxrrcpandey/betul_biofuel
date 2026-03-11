import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields as _create_custom_fields


def create_custom_fields():
	"""Create custom fields on Purchase Receipt, Purchase Order, Material Request, Company, Item Group, and Brand."""
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
		],

		# ── PO Approval Fields ──────────────────────────────────────────
		"Purchase Order": [
			{
				"fieldname": "bbf_approval_section",
				"fieldtype": "Section Break",
				"label": "BBF Approval",
				"insert_after": "terms",
				"collapsible": 0
			},
			{
				"fieldname": "bbf_approval_status",
				"fieldtype": "Select",
				"label": "Approval Status",
				"options": "\nDraft\nPending Department Head\nPending GM\nPending CEO\nPending MD\nApproved\nRevised\nRejected",
				"insert_after": "bbf_approval_section",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
				"bold": 1,
				"allow_on_submit": 1,
				"description": "Current approval status (managed by BBF Approval System)"
			},
			{
				"fieldname": "bbf_current_level",
				"fieldtype": "Int",
				"label": "Current Approval Level",
				"insert_after": "bbf_approval_status",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1
			},
			{
				"fieldname": "bbf_required_level",
				"fieldtype": "Int",
				"label": "Required Approval Level",
				"insert_after": "bbf_current_level",
				"read_only": 1,
				"no_copy": 1,
				"hidden": 1,
				"description": "Highest level this PO needs to reach for final approval"
			},
			{
				"fieldname": "bbf_approval_col1",
				"fieldtype": "Column Break",
				"insert_after": "bbf_required_level"
			},
			{
				"fieldname": "bbf_approved_by",
				"fieldtype": "Link",
				"label": "Final Approved By",
				"options": "User",
				"insert_after": "bbf_approval_col1",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "bbf_approved_date",
				"fieldtype": "Datetime",
				"label": "Approved Date",
				"insert_after": "bbf_approved_by",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "bbf_revision_count",
				"fieldtype": "Int",
				"label": "Revision Count",
				"insert_after": "bbf_approved_date",
				"read_only": 1,
				"no_copy": 1,
				"default": "0"
			},
			{
				"fieldname": "bbf_last_action",
				"fieldtype": "Data",
				"label": "Last Action",
				"insert_after": "bbf_revision_count",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "bbf_submitted_by",
				"fieldtype": "Link",
				"label": "Submitted For Approval By",
				"options": "User",
				"insert_after": "bbf_last_action",
				"read_only": 1,
				"no_copy": 1
			},
			# ── Revision Info ──
			{
				"fieldname": "bbf_revision_section",
				"fieldtype": "Section Break",
				"label": "Revision Info",
				"insert_after": "bbf_submitted_by",
				"collapsible": 1,
				"depends_on": "eval:doc.bbf_revision_count > 0"
			},
			{
				"fieldname": "bbf_revision_reason",
				"fieldtype": "Small Text",
				"label": "Revision Reason",
				"insert_after": "bbf_revision_section",
				"read_only": 1,
				"no_copy": 1
			},
			{
				"fieldname": "bbf_revised_by",
				"fieldtype": "Data",
				"label": "Revised By",
				"insert_after": "bbf_revision_reason",
				"read_only": 1,
				"no_copy": 1
			},
			{
				"fieldname": "bbf_revision_col1",
				"fieldtype": "Column Break",
				"insert_after": "bbf_revised_by"
			},
			{
				"fieldname": "bbf_resubmit_mode",
				"fieldtype": "Select",
				"label": "Resubmit Mode",
				"options": "\nRestart from Department Head\nRe-enter at reviser level",
				"insert_after": "bbf_revision_col1",
				"read_only": 1,
				"no_copy": 1,
				"depends_on": "eval:doc.bbf_approval_status=='Revised'"
			},
			# ── Approval Log ──
			{
				"fieldname": "bbf_approval_log_section",
				"fieldtype": "Section Break",
				"label": "Approval History",
				"insert_after": "bbf_resubmit_mode",
				"collapsible": 1
			},
			{
				"fieldname": "bbf_approval_log",
				"fieldtype": "Table",
				"label": "Approval Log",
				"options": "BBF Approval Log",
				"insert_after": "bbf_approval_log_section",
				"read_only": 1,
				"cannot_add_rows": 1,
				"cannot_delete_rows": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			# ── Hidden tracking fields ──
			{
				"fieldname": "bbf_amount_at_submission",
				"fieldtype": "Currency",
				"label": "Amount at Submission",
				"insert_after": "bbf_approval_log",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Captures grand_total when PO enters approval to detect amount tampering"
			},
			{
				"fieldname": "bbf_last_sla_alert",
				"fieldtype": "Datetime",
				"label": "Last SLA Alert",
				"insert_after": "bbf_amount_at_submission",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
		],

		# ── MR Approval Fields ──────────────────────────────────────────
		"Material Request": [
			{
				"fieldname": "bbf_mr_section",
				"fieldtype": "Section Break",
				"label": "BBF Approval",
				"insert_after": "terms",
				"collapsible": 0
			},
			{
				"fieldname": "bbf_mr_status",
				"fieldtype": "Select",
				"label": "MR Approval Status",
				"options": "\nDraft\nPending Department Head\nApproved\nRevised\nRejected",
				"insert_after": "bbf_mr_section",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
				"bold": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "bbf_mr_col1",
				"fieldtype": "Column Break",
				"insert_after": "bbf_mr_status"
			},
			{
				"fieldname": "bbf_mr_approved_by",
				"fieldtype": "Link",
				"label": "MR Approved By",
				"options": "User",
				"insert_after": "bbf_mr_col1",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "bbf_mr_approved_date",
				"fieldtype": "Datetime",
				"label": "MR Approved Date",
				"insert_after": "bbf_mr_approved_by",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1
			},
			{
				"fieldname": "bbf_mr_revision_section",
				"fieldtype": "Section Break",
				"label": "MR Revision Info",
				"insert_after": "bbf_mr_approved_date",
				"collapsible": 1,
				"depends_on": "eval:doc.bbf_mr_status=='Revised'"
			},
			{
				"fieldname": "bbf_mr_revision_reason",
				"fieldtype": "Small Text",
				"label": "MR Revision Reason",
				"insert_after": "bbf_mr_revision_section",
				"read_only": 1,
				"no_copy": 1
			},
			{
				"fieldname": "bbf_mr_log_section",
				"fieldtype": "Section Break",
				"label": "MR Approval History",
				"insert_after": "bbf_mr_revision_reason",
				"collapsible": 1
			},
			{
				"fieldname": "bbf_mr_log",
				"fieldtype": "Table",
				"label": "MR Approval Log",
				"options": "BBF Approval Log",
				"insert_after": "bbf_mr_log_section",
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
		"Brand": [
			{
				"fieldname": "brand_code",
				"fieldtype": "Data",
				"label": "Brand Code",
				"insert_after": "brand",
				"description": "3-letter code for item coding (e.g., CAR, ADM, MCL)"
			},
		],
	}

	_create_custom_fields(custom_fields)
	_setup_purchase_receipt_permissions()
	_setup_purchase_order_permissions()
	_seed_default_approval_limits()


def _setup_purchase_receipt_permissions():
	"""Ensure Accounts User role has create permission on Purchase Receipt."""
	existing = frappe.db.exists("DocPerm", {
		"parent": "Purchase Receipt",
		"role": "Accounts User",
		"permlevel": 0,
		"create": 1
	})
	if not existing:
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


def _setup_purchase_order_permissions():
	"""Ensure approval roles have read+write on Purchase Order (no submit — controller handles that)."""
	approval_roles = ["Department Head", "General Manager", "CEO", "MD"]
	po_doc = frappe.get_doc("DocType", "Purchase Order")
	changed = False

	for role in approval_roles:
		has_role = any(
			p.role == role and p.permlevel == 0
			for p in po_doc.permissions
		)
		if not has_role:
			po_doc.append("permissions", {
				"role": role,
				"permlevel": 0,
				"read": 1,
				"write": 1,
				"create": 0,
				"submit": 0,
				"cancel": 0,
				"amend": 0,
			})
			changed = True

	if changed:
		po_doc.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Purchase Order")


def _seed_default_approval_limits():
	"""Create default BBF Approval Limit records if none exist."""
	if frappe.db.count("BBF Approval Limit") > 0:
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
		doc = frappe.new_doc("BBF Approval Limit")
		doc.update(d)
		doc.insert(ignore_permissions=True)

	frappe.db.commit()
