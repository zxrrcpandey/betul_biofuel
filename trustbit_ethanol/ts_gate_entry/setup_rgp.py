# Copyright (c) 2026, Trustbit Software and contributors
# RGP (Returnable Gate Pass) — after_migrate seeder (v2.46.0, Phase A1).
#
# Phase A1 ships ONLY the purpose-aware MR routing leg (decision D1 option 2,
# user-approved plan 28 Aug 2026):
#   1. TS Settings kill switch `ts_rgp_enabled` (Check, default OFF) — the ONLY
#      thing that arms the purpose-scoped Pass 1 in ts_po_approval._find_mr_route
#      (decision O-2: routing and the RGP feature switch on together). Readers
#      are fail-CLOSED (cint(None) == 0 → legacy routing).
#   2. The "RGP Service" route: DH(Review) → AVP(Review) → CEO(Final Approve),
#      applies_to_purpose = "Service Request", NO cost_centers rows (= company-
#      wide wildcard; enumeration is impossible — _validate_cost_centers forbids
#      a CC on two active routes and every CC is already claimed).
#
# Seeder posture (L340 class): create-if-absent for the route + compare-then-
# apply self-heal for its applies_to_purpose field only. Steps are seeded once
# and then belong to the admins — this seeder never rewrites existing steps, so
# a deliberate server-side tuning survives every migrate. The Custom Field uses
# create_custom_fields(update=True) and is therefore self-healing (L239 n/a —
# no Property Setter involved). Roles were verified present on BOTH servers on
# 28 Aug 2026; the guard below keeps a fresh install from silently seeding a
# route with a missing role (a role name that doesn't exist is a silent no-op
# everywhere else — L281 class).

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

RGP_ROUTE_NAME = "RGP Service"
RGP_ROUTE_PURPOSE = "Service Request"

_RGP_ROUTE_STEPS = (
	{"step_order": 1, "role": "Department Head", "role_label": "Department Head",
	 "action_type": "Review", "can_revise": 1, "can_reject": 1},
	{"step_order": 2, "role": "AVP", "role_label": "AVP",
	 "action_type": "Review", "can_revise": 1, "can_reject": 1},
	{"step_order": 3, "role": "CEO", "role_label": "CEO",
	 "action_type": "Final Approve", "can_revise": 1, "can_reject": 1},
)


def setup_rgp():
	"""Idempotent — safe on every migrate (hooks.py after_migrate)."""
	_create_rgp_settings_fields()
	_seed_rgp_route()
	_apply_rgp_report_roles()
	_add_stores_workspace_shortcut()
	_grant_transport_picker_to_stores()


# v2.47.0 user-walkthrough finding (28 Aug 2026): the RGP `transporter` Link
# targets TS Transport Master, which Stores roles cannot pick from — the same
# gap Sales had before v2.39.0. Mirror of setup_dn_transport's
# _grant_transport_master_picker_access, with all its documented gotchas:
# select-only (search_fields to the picker, no form/bank/PAN click-through);
# add_permission dedup keys on (parent, role, permlevel) not ptype; a fresh
# Custom DocPerm row inherits field DEFAULTS (read/export can land 1), so the
# update loop zeroes every grantable right except select on every migrate.
_RGP_TM_PICKER_ROLES = ("Stores User", "Stores Manager")


def _grant_transport_picker_to_stores():
	try:
		from frappe.permissions import add_permission, update_permission_property

		zeroed = ("read", "write", "create", "delete", "report", "export",
			"print", "email", "share")
		for role in _RGP_TM_PICKER_ROLES:
			if not frappe.db.exists("Role", role):
				continue
			add_permission("TS Transport Master", role, ptype="select")
			for ptype in zeroed:
				update_permission_property("TS Transport Master", role, 0, ptype, 0)
		frappe.clear_cache(doctype="TS Transport Master")
	except Exception:
		frappe.log_error(title="RGP Setup: transport picker grant failed",
			message=frappe.get_traceback())
		frappe.clear_messages()


# Report roles are NOT synced by migrate (L281) — apply via ORM, filtered to
# roles that exist on the target server (L290: a missing role name is a silent
# no-op in fixtures but a hard link error in a child row).
RGP_REPORT_NAME = "TS Open RGP Register"
RGP_REPORT_ROLES = (
	"System Manager", "IT Head", "Stores Manager", "Stores User",
	"CEO", "MD", "Purchase Manager", "Purchase User",
	"Accounts Manager", "Department Head", "AVP",
)


def _apply_rgp_report_roles():
	try:
		if not frappe.db.exists("Report", RGP_REPORT_NAME):
			return
		report = frappe.get_doc("Report", RGP_REPORT_NAME)
		existing = {r.role for r in (report.roles or [])}
		changed = False
		for role in RGP_REPORT_ROLES:
			if role in existing or not frappe.db.exists("Role", role):
				continue
			report.append("roles", {"role": role})
			changed = True
		if changed:
			report.flags.ignore_permissions = True
			report.save()
	except Exception:
		frappe.log_error(title="RGP Setup: report roles failed",
			message=frappe.get_traceback())
		frappe.clear_messages()


def _add_stores_workspace_shortcut():
	"""Idempotent shortcut on the Stores workspace (L173: migrate does not
	sync workspace shortcuts). ignore_links guards against dangling rows on a
	target workspace (the prod BBPL Ethanol precedent)."""
	try:
		ws_name = frappe.db.get_value("Workspace", {"name": "Stores"}, "name") \
			or frappe.db.get_value("Workspace", {"title": "Stores"}, "name")
		if not ws_name:
			return
		ws = frappe.get_doc("Workspace", ws_name)
		if any((s.label or "") == "Returnable Gate Pass" for s in (ws.shortcuts or [])):
			return
		ws.append("shortcuts", {
			"type": "DocType",
			"label": "Returnable Gate Pass",
			"link_to": "TS Returnable Gate Pass",
			"color": "Orange",
		})
		ws.flags.ignore_permissions = True
		ws.flags.ignore_links = True
		ws.save()
	except Exception:
		frappe.log_error(title="RGP Setup: workspace shortcut failed",
			message=frappe.get_traceback())
		frappe.clear_messages()


def _create_rgp_settings_fields():
	try:
		create_custom_fields({
			"TS Settings": [
				{
					"fieldname": "ts_rgp_section",
					"fieldtype": "Section Break",
					"label": "Returnable Gate Pass (RGP)",
					"collapsible": 1,
				},
				{
					"fieldname": "ts_rgp_enabled",
					"fieldtype": "Check",
					"label": "Enable RGP (Returnable Gate Pass)",
					"default": "0",
					"insert_after": "ts_rgp_section",
					"description": (
						"Master switch for the RGP feature. While OFF (default), MR routing "
						"is exactly the pre-v2.46 behaviour and every purpose-scoped route "
						"is ignored. Turn ON only after the RGP rollout is announced."
					),
				},
			]
		}, update=True)
	except Exception:
		frappe.log_error(title="RGP Setup: settings fields failed",
			message=frappe.get_traceback())
		frappe.clear_messages()


def _seed_rgp_route():
	try:
		# Scoped rollback target (security LOW-5): a bare rollback in the shared
		# after_migrate transaction could discard PRECEDING hooks' uncommitted
		# writes when no DDL committed in between. The savepoint confines the
		# except-path rollback to this seeder's own work.
		frappe.db.savepoint("rgp_seed")
		missing_roles = [s["role"] for s in _RGP_ROUTE_STEPS
			if not frappe.db.exists("Role", s["role"])]
		if missing_roles:
			frappe.log_error(title="RGP Setup: route NOT seeded — roles missing",
				message=f"Missing roles on this site: {missing_roles}")
			return

		if frappe.db.exists("TS MR Approval Route", RGP_ROUTE_NAME):
			# Self-heal ONLY the purpose binding (compare-then-apply, L340) —
			# steps/active-state stay admin-owned after first seed. db.set_value
			# bypasses validate, so re-check exclusivity first (security L-3):
			# never create a second active route for the purpose via self-heal.
			stored = frappe.db.get_value(
				"TS MR Approval Route", RGP_ROUTE_NAME, "applies_to_purpose")
			clash = frappe.db.get_value("TS MR Approval Route", {
				"applies_to_purpose": RGP_ROUTE_PURPOSE, "is_active": 1,
				"name": ("!=", RGP_ROUTE_NAME)}, "name")
			if stored != RGP_ROUTE_PURPOSE and not clash:
				frappe.db.set_value("TS MR Approval Route", RGP_ROUTE_NAME,
					"applies_to_purpose", RGP_ROUTE_PURPOSE, update_modified=False)
			elif clash:
				frappe.log_error(title="RGP Setup: purpose self-heal skipped",
					message=f"Active route '{clash}' already holds purpose "
						f"'{RGP_ROUTE_PURPOSE}' — resolve manually.")
			return

		route = frappe.new_doc("TS MR Approval Route")
		route.route_name = RGP_ROUTE_NAME
		route.is_active = 1
		route.applies_to_purpose = RGP_ROUTE_PURPOSE
		route.description = (
			"RGP flow (v2.46): Service Request indents company-wide — "
			"Department Head review, AVP review, CEO final approval. "
			"No Cost Centers rows = wildcard; armed only while TS Settings › "
			"Enable RGP is ON."
		)
		for step in _RGP_ROUTE_STEPS:
			route.append("approval_steps", step)
		route.insert(ignore_permissions=True)
	except Exception:
		# Roll back a possible partial insert so the next migrate re-seeds
		# cleanly instead of treating a half-written route as "already seeded"
		frappe.db.rollback(save_point="rgp_seed")
		frappe.log_error(title="RGP Setup: route seeding failed",
			message=frappe.get_traceback())
		frappe.clear_messages()
