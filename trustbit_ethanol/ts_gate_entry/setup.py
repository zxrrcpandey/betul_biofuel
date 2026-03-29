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
				"print_hide": 1,
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
				"allow_on_submit": 1
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
	# Legacy v1.0 — TS Approval Limit is no longer used by v2.0 rule-based system
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
			if u.get("user") and not frappe.db.exists("User", u["user"]):
				continue
			doc.append("users", {
				"action_type": u["type"],
				"user": u.get("user", ""),
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
