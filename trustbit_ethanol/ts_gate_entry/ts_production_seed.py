"""Production Logging — after_migrate seeder (TS Production Entry release flow).

NOT registered in hooks.py here — the orchestrator wires
`after_migrate_production_logging` into hooks.py after_migrate under the
STOCK_RECON_PERMS unlock (hooks.py is locked / deferred for this slice).

Kept in this separate module (NOT setup.py, which is locked under MR_FULL/PO_FULL)
— same pattern as ts_stock_recon_perms.py / ts_se_header_dimensions.py.

Idempotent + self-healing on every migrate:
  1. Apply the ts_variance_status Select enum via Property Setter (Lesson 239 —
     migrate does NOT re-apply seed_data option strings; the JSON DocField alone is
     not enough on an existing site).
  2. UPSERT the Stores Manager Custom DocPerm on TS Production Entry so the release
     endpoints' write gate has backing perms (Lesson 292 — the generic INSERT-only
     seed_custom_docperm() would silently no-op an existing matrix).
  3. Seed the production-logging page roles, if/when the page exists (skip-if-absent
     — the page is a separate slice; Lesson 290 — page roles don't sync via migrate).
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

DOCTYPE = "TS Production Entry"
PERMLEVEL = 0

# The full enum the release flow uses (must match ts_production_entry.json options).
# Phase D adds the two Multiple-flow states; v2.21 adds 'Awaiting Department
# Production' (dept-gate re-sequencing). Lesson 239: re-applied via Property Setter.
VARIANCE_STATUS_OPTIONS = ("Draft\nPending Stores Release\nReleased"
                           "\nAwaiting Department Production\nPending Material Request"
                           "\nAwaiting Distribution\nCompleted\nRejected\nCancelled")

# v2.21 dept-gate: the department entry's own enum (must match
# ts_production_department_entry.json options). Lesson 239 applies here too.
DEPT_DOCTYPE = "TS Production Department Entry"
DEPT_STATUS_OPTIONS = "Draft\nPending\nLogged\nSkipped\nCancelled"

# Stores Manager — the single release-gate role (read/report + write to flip status).
_STORES_MANAGER_PERMS = {
	"read": 1, "write": 1, "create": 0, "delete": 0,
	"report": 1, "export": 1, "print": 1, "email": 1, "share": 1,
}

PAGE_NAME = "production-logging"
# v2.21: CEO + MD added — the R3 status-board viewers (L290 ORM append).
PAGE_ROLES = ["Manufacturing Manager", "Manufacturing User", "Stores Manager",
              "System Manager", "CEO", "MD"]

# v2.21: CEO/MD read-only access to department entries (the board's row data).
# UPSERT via add_permission + update_permission_property (L292/L299 — preserves
# every existing row; a raw INSERT would drop Standard DocPerm, L169).
_DEPT_READONLY_ROLES = ("CEO", "MD")
_DEPT_READONLY_PERMS = {
	"read": 1, "report": 1, "export": 1, "print": 1, "email": 1, "share": 1,
	"write": 0, "create": 0, "delete": 0,
}


def _apply_status_options(doctype, fieldname, options):
	"""Compare-then-apply a Select enum via Property Setter (Lesson 239 —
	migrate does NOT re-apply option strings). Idempotent."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	current = frappe.db.get_value(
		"Property Setter",
		{"doc_type": doctype, "field_name": fieldname, "property": "options"},
		"value",
	)
	if current == options:
		return False
	make_property_setter(
		doctype, fieldname, "options", options, "Text",
		validate_fields_for_doctype=False,
	)
	return True


def _apply_variance_status_options():
	return _apply_status_options(DOCTYPE, "ts_variance_status", VARIANCE_STATUS_OPTIONS)


def _apply_dept_status_options():
	"""v2.21: the dept-entry enum (+Pending/+Skipped). Skip-if-absent for fresh
	installs where the dept doctype migrates after this seeder's first run."""
	if not frappe.db.exists("DocType", DEPT_DOCTYPE):
		return False
	return _apply_status_options(DEPT_DOCTYPE, "status", DEPT_STATUS_OPTIONS)


def _row_filter(role):
	return {"parent": DOCTYPE, "role": role, "permlevel": PERMLEVEL, "if_owner": 0}


def _upsert_stores_manager_docperm():
	"""UPSERT the Stores Manager Custom DocPerm on TS Production Entry (Lesson 292)."""
	if not frappe.db.exists("Role", "Stores Manager"):
		return False  # cross-server role gap — skip
	changed = False
	if not frappe.db.exists("Custom DocPerm", _row_filter("Stores Manager")):
		# add_permission() runs setup_custom_perms() first (Lesson 169 guard).
		add_permission(DOCTYPE, "Stores Manager", PERMLEVEL)
		changed = True
	for ptype, value in _STORES_MANAGER_PERMS.items():
		current = frappe.db.get_value("Custom DocPerm", _row_filter("Stores Manager"), ptype)
		if int(current or 0) != value:
			update_permission_property(DOCTYPE, "Stores Manager", PERMLEVEL, ptype, value, validate=False)
			changed = True
	return changed


def _upsert_dept_readonly_docperms():
	"""v2.21: CEO/MD read-only Custom DocPerm on the dept entry (board row data).
	UPSERT preserving existing rows (L292/L299). Skip-if-absent per role/doctype."""
	if not frappe.db.exists("DocType", DEPT_DOCTYPE):
		return False
	changed = False
	for role in _DEPT_READONLY_ROLES:
		if not frappe.db.exists("Role", role):
			continue  # cross-server role gap
		row_filter = {"parent": DEPT_DOCTYPE, "role": role, "permlevel": 0, "if_owner": 0}
		if not frappe.db.exists("Custom DocPerm", row_filter):
			# add_permission() runs setup_custom_perms() first (Lesson 169 guard).
			add_permission(DEPT_DOCTYPE, role, 0)
			changed = True
		for ptype, value in _DEPT_READONLY_PERMS.items():
			current = frappe.db.get_value("Custom DocPerm", row_filter, ptype)
			if int(current or 0) != value:
				update_permission_property(DEPT_DOCTYPE, role, 0, ptype, value, validate=False)
				changed = True
	return changed


def _seed_page_roles():
	"""Seed the production-logging page roles if the page exists (Lesson 290).
	The page is a separate slice — skip silently if it isn't installed yet."""
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


SETTINGS_DOCTYPE = "TS Settings"
# The 3 Production-Logging config fields (the v2.15.0 wip/fg/byproduct warehouse +
# enable + tolerance fields already ship as DocFields in ts_settings.json). Seeded as
# Custom Fields here so this slice is self-contained and does NOT touch the shared
# ts_settings.json — same approach setup_omc_tracker.py uses for the OMC fields.
_PRODUCTION_SETTINGS_FIELDS = [
	{
		"fieldname": "ts_production_release_source_warehouse",
		"label": "Default Release Source Warehouse (Stores)",
		"fieldtype": "Link", "options": "Warehouse",
		"insert_after": "ts_production_byproduct_warehouse",
		"description": ("Production Logging: raw material is RELEASED FROM this Stores warehouse "
						"(Stores -> WIP Material-Transfer-for-Manufacture). Fills the blank source on "
						"BOM rows; also the destination when surplus WIP is auto-returned. Defaults to "
						"the WIP/Source warehouse if left blank."),
	},
	{
		"fieldname": "ts_production_auto_return_surplus",
		"label": "Auto-Return Over-Released Surplus WIP -> Stores",
		"fieldtype": "Check", "default": "1",
		"insert_after": "ts_production_release_source_warehouse",
		"description": ("Production Logging: when ON (default), raw material released over what the "
						"BOM consumed is auto-returned WIP -> Stores on completion so no leftover "
						"accumulates in WIP. Turn OFF to leave surplus in WIP (auditable via the note)."),
	},
	{
		"fieldname": "ts_production_wip_recon_tolerance_pct",
		"label": "WIP Reconciliation Tolerance (%)",
		"fieldtype": "Percent",
		"insert_after": "ts_production_auto_return_surplus",
		"description": ("Production Logging: if the released-vs-consumed WIP variance exceeds this %, "
						"the reconciliation note is flagged (informational only, NOT a gate). No JSON "
						"default so a configured value is never clobbered on migrate (Lesson 227)."),
	},
	# v2.21 layout — own section for the dept/Multiple-flow block. Without it these
	# fields chained after the cascade fields and rendered INSIDE the collapsible
	# "Production Cascade Delete" section (user finding, 8 Jul). Anchored after the
	# LAST cascade field so the block starts a fresh section below it.
	{
		"fieldname": "ts_dept_production_section",
		"label": "Department Production — Multiple (Connector) Flow",
		"fieldtype": "Section Break",
		"insert_after": "ts_prod_cascade_executor_user",
		"collapsible": 0,
	},
	# Phase C (Multi-BOM plan) — kill switch for department consumption entries.
	# NO JSON default (Lesson 227): ships OFF; flip manually when the team is ready.
	{
		"fieldname": "ts_production_dept_entry_enabled",
		"label": "Enable Department Consumption Entries",
		"fieldtype": "Check",
		"insert_after": "ts_dept_production_section",
		"description": ("Kill switch for TS Production Department Entry (reporting-only "
						"department consumption logs + the Log Consumption card on the "
						"Production Logging page). Ships OFF; no JSON default (Lesson 227)."),
	},
	# Phase D (Multi-BOM plan) — kill switch for the Multiple (BOM Connector) flow.
	{
		"fieldname": "ts_production_multi_flow_enabled",
		"label": "Enable Multiple (BOM Connector) Production Flow",
		"fieldtype": "Check",
		"insert_after": "ts_production_dept_entry_enabled",
		"description": ("Kill switch for the Multiple flow (auto Material Request release + "
						"multi-warehouse distribution). Ships OFF; no JSON default (Lesson 227). "
						"Independent of the Single flow and dept entries."),
	},
	# Phase D — optional cost centre for the auto-created Material Requests (the MR
	# naming series requires a cost centre). Falls back to the company default.
	{
		"fieldname": "ts_production_mr_cost_center",
		"label": "Production MR Cost Center",
		"fieldtype": "Link", "options": "Cost Center",
		"insert_after": "ts_production_multi_flow_enabled",
		"description": ("Cost centre stamped on the Multiple-flow auto Material Requests "
						"(drives MR naming). Blank = the company's default cost centre."),
	},
	# v2.21 layout — reminders in a right-hand column beside the kill switches.
	{
		"fieldname": "ts_dept_production_col",
		"fieldtype": "Column Break",
		"insert_after": "ts_production_mr_cost_center",
	},
	# v2.21 P2 — dept Add-Production reminder thresholds + U7 cap. NO JSON
	# defaults (Lesson 227); code fallbacks are 4/12/24h and 0 = unlimited.
	{
		"fieldname": "ts_production_reminder_l1_hours",
		"label": "Dept Reminder L1 (hours)",
		"fieldtype": "Float",
		"insert_after": "ts_dept_production_col",
		"description": "Hours after department notification before the FIRST reminder. Blank/0 = 4h fallback.",
	},
	{
		"fieldname": "ts_production_reminder_l2_hours",
		"label": "Dept Reminder L2 (hours)",
		"fieldtype": "Float",
		"insert_after": "ts_production_reminder_l1_hours",
		"description": "Hours after notification before the SECOND reminder. Blank/0 = 12h fallback.",
	},
	{
		"fieldname": "ts_production_reminder_l3_hours",
		"label": "Dept Reminder L3 (hours)",
		"fieldtype": "Float",
		"insert_after": "ts_production_reminder_l2_hours",
		"description": "Hours before the THIRD reminder; also the repeat cadence after the PM escalation. Blank/0 = 24h fallback.",
	},
	{
		"fieldname": "ts_production_reminder_max",
		"label": "Dept Reminder Max (0 = keep reminding)",
		"fieldtype": "Int",
		"insert_after": "ts_production_reminder_l3_hours",
		"description": ("Total department reminders before going silent. 0/blank = UNLIMITED — reminders repeat "
						"at the L3 cadence until the department acts (user decision U7, 4 Jul 2026)."),
	},
	# v2.21 layout — the access list gets its own full-width section.
	{
		"fieldname": "ts_production_access_section",
		"label": "Production Access Control",
		"fieldtype": "Section Break",
		"insert_after": "ts_production_reminder_max",
		"collapsible": 0,
	},
	# v2.21 UAT ④ — configurable production access (user decision 8 Jul: "Need Setting
	# in TS Production ... Super Admin/CEO/MD or Admin can configure user wise").
	# EMPTY table = current role-based behavior (nothing breaks until configured).
	{
		"fieldname": "ts_production_authorized_users",
		"label": "Production Authorized Users",
		"fieldtype": "Table",
		"options": "TS Production Authorized User",
		"insert_after": "ts_production_access_section",
		"description": ("Who may create Multiple-flow production runs and post the distribution. "
						"EMPTY = current behavior (any Production/Manufacturing role). Once users are "
						"listed, ONLY they (plus Super Admin / CEO / MD / System Manager / Administrator) "
						"can run production."),
	},
]

# Phase D — tag field on Material Request identifying the Multiple-flow auto-MRs
# (user decision 2 Jul: tag + filterable so they don't clutter the procurement list).
_MR_TAG_FIELD = {
	"fieldname": "ts_production_run",
	"label": "Production Run (auto)",
	"fieldtype": "Link",
	"options": "TS Production Entry",
	"insert_after": "material_request_type",
	"read_only": 1,
	"no_copy": 1,
	"in_standard_filter": 1,
	"description": "Set when this Material Request was auto-created by a Multiple-flow production run.",
}


def _seed_mr_tag_field():
	"""Create Material Request.ts_production_run, only if absent. Idempotent."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	if not frappe.db.exists("DocType", "TS Production Entry"):
		return False
	meta = frappe.get_meta("Material Request")
	if meta.has_field(_MR_TAG_FIELD["fieldname"]):
		return False
	create_custom_fields({"Material Request": [_MR_TAG_FIELD]}, ignore_validate=True)
	frappe.clear_cache(doctype="Material Request")
	return True


def _seed_production_settings_fields():
	"""Create the 3 Production-Logging TS Settings config fields as Custom Fields, ONLY
	if not already present as a Doc/Custom field (has_field covers both) — so it never
	duplicates a future committed DocField. Idempotent."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	to_add = [f for f in _PRODUCTION_SETTINGS_FIELDS if not meta.has_field(f["fieldname"])]
	# a Table field needs its child DocType migrated first (fresh-install order)
	to_add = [f for f in to_add
	          if f["fieldtype"] != "Table" or frappe.db.exists("DocType", f["options"])]
	changed = False
	if to_add:
		create_custom_fields({SETTINGS_DOCTYPE: to_add}, ignore_validate=True)
		changed = True
	# Layout reconcile (v2.21, 8 Jul): the spec is the source of truth for WHERE each
	# field sits — re-anchor any existing Custom Field whose insert_after drifted
	# (fixes the dept/multi block rendering inside "Production Cascade Delete").
	for spec in _PRODUCTION_SETTINGS_FIELDS:
		cf = frappe.db.get_value(
			"Custom Field", {"dt": SETTINGS_DOCTYPE, "fieldname": spec["fieldname"]},
			["name", "insert_after"], as_dict=True)
		if cf and spec.get("insert_after") and cf.insert_after != spec["insert_after"]:
			frappe.db.set_value("Custom Field", cf.name,
			                    "insert_after", spec["insert_after"])
			changed = True
	if changed:
		frappe.clear_cache(doctype=SETTINGS_DOCTYPE)
	return changed


# Phase B (Multi-BOM plan) — one Custom Field on BOM tagging its production category.
# Optional (BOM is ERPNext-native and heavily used — never reqd). Lesson 227: no default.
_BOM_CATEGORY_FIELD = {
	"fieldname": "ts_production_category",
	"label": "Production Category",
	"fieldtype": "Link",
	"options": "TS Production BOM Category",
	"insert_after": "with_operations",
	"description": ("Optional: the TS Production BOM Category this BOM belongs to "
					"(RS Main / WTP / Electricity ...). Used to auto-suggest categories "
					"when building a TS BOM Connector and to route notifications."),
}


def _seed_bom_category_field():
	"""Create the BOM.ts_production_category Custom Field, only if absent. Idempotent;
	skip-if-absent when the category DocType isn't migrated yet (fresh install order)."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	if not frappe.db.exists("DocType", "TS Production BOM Category"):
		return False
	meta = frappe.get_meta("BOM")
	if meta.has_field(_BOM_CATEGORY_FIELD["fieldname"]):
		return False
	create_custom_fields({"BOM": [_BOM_CATEGORY_FIELD]}, ignore_validate=True)
	frappe.clear_cache(doctype="BOM")
	return True


def after_migrate_production_logging():
	"""after_migrate entry point (register in hooks.py under unlock). Idempotent."""
	_seed_production_settings_fields()
	_seed_bom_category_field()
	_seed_mr_tag_field()
	ps_changed = _apply_variance_status_options()
	dept_ps_changed = _apply_dept_status_options()
	perm_changed = _upsert_stores_manager_docperm()
	dept_perm_changed = _upsert_dept_readonly_docperms()
	_seed_page_roles()

	from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
	if perm_changed:
		validate_permissions_for_doctype(DOCTYPE)
	if dept_perm_changed:
		validate_permissions_for_doctype(DEPT_DOCTYPE)
	if ps_changed or perm_changed:
		frappe.clear_cache(doctype=DOCTYPE)
	if dept_ps_changed or dept_perm_changed:
		frappe.clear_cache(doctype=DEPT_DOCTYPE)
