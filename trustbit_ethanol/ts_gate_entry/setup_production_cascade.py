"""Production Cascade Delete — after_migrate seeder (self-contained).

Seeds the kill switch + rate-limit + revert-window + executor-user config as Custom
Fields on TS Settings (NO JSON default on the kill switch — Lesson 227 — so a configured
value is never clobbered on migrate), AND seeds the production-cascade-delete page roles
via ORM (Lesson 290 — page roles don't sync via migrate/reload-doc).

Kept in this separate module (NOT setup.py, which is locked under MR_FULL/PO_FULL) — same
pattern as ts_stock_recon_perms.py / ts_production_seed.py / setup_omc_tracker.py.

Idempotent + self-healing on every migrate. Ships the tool INERT: ts_prod_cascade_enabled
has NO JSON default, so on a fresh field it is NULL/0 (disabled). The kill switch must be
flipped to 1 MANUALLY post-deploy.

Lesson references: 227 (no JSON default on Singles), 290 (page roles via ORM),
292 (UPSERT not INSERT-only).
"""

import frappe

SETTINGS_DOCTYPE = "TS Settings"
PAGE_NAME = "production-cascade-delete"
PAGE_ROLES = ["Super Admin", "CEO", "MD", "System Manager", "Administrator",
              "Auditor", "IT Head", "Manufacturing Manager"]

WORKSPACE_NAME = "TS Production"            # seeded by setup_dashboard_workspaces (v2.19.0)
SHORTCUT_LABEL = "Delete Production (Cascade)"

# Config fields on TS Settings — seeded as Custom Fields so this slice is self-contained
# and does NOT touch the shared ts_settings.json. The kill switch deliberately has NO
# default (Lesson 227) so it ships disabled and a configured value is never clobbered.
_PROD_CASCADE_SETTINGS_FIELDS = [
	{
		"fieldname": "ts_prod_cascade_section",
		"label": "Production Cascade Delete",
		"fieldtype": "Section Break",
		"insert_after": "ts_production_wip_recon_tolerance_pct",
		"collapsible": 1,
	},
	{
		"fieldname": "ts_prod_cascade_enabled",
		"label": "Enable Production Cascade Delete",
		"fieldtype": "Check",
		"insert_after": "ts_prod_cascade_section",
		"permlevel": 1,
		"description": ("Kill switch for the Production Cascade Delete tool. When OFF, every "
		                "endpoint returns 403. NO JSON default (Lesson 227) — ships disabled; "
		                "flip to 1 MANUALLY only when ready. This is a DESTRUCTIVE tool."),
	},
	{
		"fieldname": "ts_prod_cascade_rate_limit_count",
		"label": "Production Cascade Rate Limit (count)",
		"fieldtype": "Int",
		"insert_after": "ts_prod_cascade_enabled",
		"permlevel": 1,
		"description": "Max production cascades a user may initiate within the window (default 5 if unset).",
	},
	{
		"fieldname": "ts_prod_cascade_rate_limit_seconds",
		"label": "Production Cascade Rate Limit (window seconds)",
		"fieldtype": "Int",
		"insert_after": "ts_prod_cascade_rate_limit_count",
		"permlevel": 1,
		"description": "Rolling rate-limit window in seconds (default 172800 = 48h if unset).",
	},
	{
		"fieldname": "ts_prod_cascade_revert_window_minutes",
		"label": "Production Cascade Revert Window (minutes)",
		"fieldtype": "Int",
		"insert_after": "ts_prod_cascade_rate_limit_seconds",
		"permlevel": 1,
		"description": ("Forensic escape-hatch window (5-60 min; default 5). Revert restores "
		                "DOCUMENTS ONLY — NOT Stock Ledger / GL / bins."),
	},
	{
		"fieldname": "ts_prod_cascade_executor_user",
		"label": "Production Cascade Executor User (Super Admin)",
		"fieldtype": "Link",
		"options": "User",
		"insert_after": "ts_prod_cascade_revert_window_minutes",
		"permlevel": 1,
		"description": ("Optional: the Super-Admin-roled user the engine runs AS. If blank, the "
		                "first enabled Super Admin user is used (deterministic by creation)."),
	},
]


def _seed_settings_fields():
	"""Create the Production Cascade config fields as Custom Fields, only if not already
	present (has_field covers both Doc + Custom). Idempotent. NO JSON default on the kill
	switch (Lesson 227)."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	to_add = [f for f in _PROD_CASCADE_SETTINGS_FIELDS if not meta.has_field(f["fieldname"])]
	if not to_add:
		return False
	create_custom_fields({SETTINGS_DOCTYPE: to_add}, ignore_validate=True)
	return True


def _seed_page_roles():
	"""Seed the production-cascade-delete page roles via ORM (Lesson 290). Skip-if-absent
	(the Page is created by migrate from the page JSON; on the same migrate it may already
	exist). Filters to roles that exist on this server (cross-server role gap)."""
	if not frappe.db.exists("Page", PAGE_NAME):
		return False
	try:
		page = frappe.get_doc("Page", PAGE_NAME)
	except Exception:
		return False
	existing = {r.role for r in (page.roles or [])}
	changed = False
	for role in PAGE_ROLES:
		if role in existing:
			continue
		if not frappe.db.exists("Role", role):
			continue  # cross-server role gap
		page.append("roles", {"role": role})
		changed = True
	if changed:
		page.flags.ignore_permissions = True
		page.save()  # ORM save — migrate/reload-doc would NOT sync page roles (Lesson 290)
	return changed


def _seed_workspace_shortcut():
	"""Add the Production Cascade Delete page shortcut to the TS Production workspace if
	missing (the workspace itself is seeded by setup_dashboard_workspaces in v2.19.0).
	Adds BOTH the Workspace Shortcut child row AND the content `shortcut` block — the
	renderer matches a content block's shortcut_name to the child row's label. Idempotent;
	skip-if-absent (workspace or page not yet present on this slice/server)."""
	import json

	if not frappe.db.exists("Workspace", WORKSPACE_NAME) or not frappe.db.exists("Page", PAGE_NAME):
		return False
	ws = frappe.get_doc("Workspace", WORKSPACE_NAME)
	if any(s.label == SHORTCUT_LABEL or s.link_to == PAGE_NAME for s in ws.shortcuts):
		return False  # already present
	ws.append("shortcuts", {"type": "Page", "link_to": PAGE_NAME, "label": SHORTCUT_LABEL, "color": "Red"})
	try:
		content = json.loads(ws.content or "[]")
	except Exception:
		content = []
	content.append({"id": "sc_production_cascade", "type": "shortcut",
	                "data": {"shortcut_name": SHORTCUT_LABEL, "col": 4}})
	ws.content = json.dumps(content)
	ws.flags.ignore_permissions = True
	ws.save()  # ORM save — workspace shortcuts don't sync via migrate (Lesson 173)
	return True


def seed_production_cascade():
	"""after_migrate entry point (registered in hooks.py). Idempotent."""
	fields_changed = _seed_settings_fields()
	_seed_page_roles()
	_seed_workspace_shortcut()
	if fields_changed:
		frappe.clear_cache(doctype=SETTINGS_DOCTYPE)
