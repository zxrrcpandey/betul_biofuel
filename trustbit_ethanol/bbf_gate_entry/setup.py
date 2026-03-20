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
				"fieldtype": "Data",
				"label": "Approval Status",
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
				"fieldtype": "Data",
				"label": "Resubmit Mode",
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
			# ── v2.0 Category & Step Tracking ──
			{
				"fieldname": "bbf_purchase_category",
				"fieldtype": "Data",
				"label": "Purchase Category",
				"insert_after": "bbf_required_level",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
				"description": "Auto-detected from PO items: Store, Chemical, Grain, Coal"
			},
			{
				"fieldname": "bbf_approval_rule",
				"fieldtype": "Link",
				"label": "Approval Rule",
				"options": "BBF PO Approval Rule",
				"insert_after": "bbf_purchase_category",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "bbf_current_step",
				"fieldtype": "Int",
				"label": "Current Step",
				"insert_after": "bbf_approval_rule",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "bbf_total_steps",
				"fieldtype": "Int",
				"label": "Total Steps",
				"insert_after": "bbf_current_step",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "bbf_self_skip_impossible",
				"fieldtype": "Check",
				"label": "Self Skip Impossible",
				"insert_after": "bbf_total_steps",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Set when submitter has the only approval role (single-step rule) — allows self-approval"
			},
			{
				"fieldname": "bbf_can_send_to_md",
				"fieldtype": "Check",
				"label": "Can Send to MD",
				"insert_after": "bbf_self_skip_impossible",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Set by server when CEO can manually trigger MD approval step"
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
			# ── Budget Override Fields ──
			{
				"fieldname": "bbf_budget_overridden",
				"fieldtype": "Check",
				"label": "Budget Overridden",
				"insert_after": "bbf_last_sla_alert",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Set when CEO overrides budget block on this PO"
			},
			{
				"fieldname": "bbf_budget_override_log_section",
				"fieldtype": "Section Break",
				"label": "Budget Override History",
				"insert_after": "bbf_budget_overridden",
				"collapsible": 1,
				"depends_on": "eval:doc.bbf_budget_overridden"
			},
			{
				"fieldname": "bbf_budget_override_log",
				"fieldtype": "Table",
				"label": "Budget Override Log",
				"options": "BBF Budget Override Log",
				"insert_after": "bbf_budget_override_log_section",
				"read_only": 1,
				"cannot_add_rows": 1,
				"cannot_delete_rows": 1,
				"no_copy": 1
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
				"reqd": 0,
				"ignore_user_permissions": 1,
				"description": "Cost Center for MR approval routing. Shows all Cost Centers across companies."
			},
			{
				"fieldname": "bbf_mr_section",
				"fieldtype": "Section Break",
				"label": "BBF Approval",
				"insert_after": "terms",
				"collapsible": 0
			},
			{
				"fieldname": "bbf_mr_status",
				"fieldtype": "Data",
				"label": "MR Approval Status",
				"insert_after": "bbf_mr_section",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
				"bold": 1,
				"allow_on_submit": 1
			},
			# ── v2.0 Route & Step Tracking ──
			{
				"fieldname": "bbf_mr_route",
				"fieldtype": "Data",
				"label": "MR Approval Route",
				"insert_after": "bbf_mr_status",
				"read_only": 1,
				"no_copy": 1,
				"description": "Resolved route: Operational or CAPEX (based on Cost Center)"
			},
			{
				"fieldname": "bbf_mr_approval_route",
				"fieldtype": "Link",
				"label": "Approval Route Ref",
				"options": "BBF MR Approval Route",
				"insert_after": "bbf_mr_route",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "bbf_mr_current_step",
				"fieldtype": "Int",
				"label": "MR Current Step",
				"insert_after": "bbf_mr_approval_route",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "bbf_mr_total_steps",
				"fieldtype": "Int",
				"label": "MR Total Steps",
				"insert_after": "bbf_mr_current_step",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1
			},
			{
				"fieldname": "bbf_mr_self_skip_impossible",
				"fieldtype": "Check",
				"label": "MR Self Skip Impossible",
				"insert_after": "bbf_mr_total_steps",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Set when MR submitter has the only approval role (single-step route) — allows self-approval"
			},
			{
				"fieldname": "bbf_mr_submitted_by",
				"fieldtype": "Link",
				"label": "MR Submitted By",
				"options": "User",
				"insert_after": "bbf_mr_self_skip_impossible",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "User who submitted this MR for approval (for self-approval prevention)"
			},
			{
				"fieldname": "bbf_mr_col1",
				"fieldtype": "Column Break",
				"insert_after": "bbf_mr_submitted_by"
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
	_create_approval_roles()
	_setup_purchase_order_permissions()
	_setup_material_request_permissions()
	_setup_admin_reception_permissions()
	_setup_workspace_roles()
	_fix_workspace_content()
	# Legacy v1.0 — BBF Approval Limit is no longer used by v2.0 rule-based system
	# _seed_default_approval_limits()


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
		"BBF Token": {"read": 1, "write": 1, "create": 1},
		"BBF Visitor": {"read": 1, "write": 1, "create": 1},
		"BBF Gate Pass Destination": {"read": 1, "write": 0, "create": 0},
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
			clean_filter = '[["BBF Token","entry_type","=","Gate Pass"]]'
			frappe.db.set_value("Workspace Shortcut", s.name, "stats_filter", clean_filter)

		if s.label == "All Gate Passes":
			frappe.db.set_value("Workspace Shortcut", s.name, "label", "Todays Visitors")

		# Fix "New Gate Pass" — ensure it's DocType/New (not broken URL)
		if s.label == "New Gate Pass" and s.type == "URL":
			frappe.db.set_value("Workspace Shortcut", s.name, {
				"type": "DocType",
				"link_to": "BBF Token",
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
	if frappe.db.count("BBF Gate Pass Destination") > 0:
		return

	defaults = [
		{
			"destination_name": "Admin Office",
			"has_g2_checkpoint": 0,
			"enabled": 1,
			"description": "Common area admin office — no G2 checkpoint required"
		},
		{
			"destination_name": "BBF Plant",
			"has_g2_checkpoint": 1,
			"enabled": 1,
			"description": "Betul Bio Fuel plant area — G2 checkpoint required"
		},
	]

	for d in defaults:
		doc = frappe.new_doc("BBF Gate Pass Destination")
		doc.update(d)
		doc.insert(ignore_permissions=True)

	frappe.db.commit()


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
