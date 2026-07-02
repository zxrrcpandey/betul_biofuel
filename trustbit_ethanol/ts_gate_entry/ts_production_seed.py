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
VARIANCE_STATUS_OPTIONS = "Draft\nPending Stores Release\nReleased\nCompleted\nRejected\nCancelled"

# Stores Manager — the single release-gate role (read/report + write to flip status).
_STORES_MANAGER_PERMS = {
	"read": 1, "write": 1, "create": 0, "delete": 0,
	"report": 1, "export": 1, "print": 1, "email": 1, "share": 1,
}

PAGE_NAME = "production-logging"
PAGE_ROLES = ["Manufacturing Manager", "Manufacturing User", "Stores Manager", "System Manager"]


def _apply_variance_status_options():
	"""Re-assert the ts_variance_status Select options via Property Setter (Lesson 239)."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	current = frappe.db.get_value(
		"Property Setter",
		{"doc_type": DOCTYPE, "field_name": "ts_variance_status", "property": "options"},
		"value",
	)
	if current == VARIANCE_STATUS_OPTIONS:
		return False
	make_property_setter(
		DOCTYPE, "ts_variance_status", "options", VARIANCE_STATUS_OPTIONS, "Text",
		validate_fields_for_doctype=False,
	)
	return True


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
	# Phase C (Multi-BOM plan) — kill switch for department consumption entries.
	# NO JSON default (Lesson 227): ships OFF; flip manually when the team is ready.
	{
		"fieldname": "ts_production_dept_entry_enabled",
		"label": "Enable Department Consumption Entries",
		"fieldtype": "Check",
		"insert_after": "ts_production_wip_recon_tolerance_pct",
		"description": ("Kill switch for TS Production Department Entry (reporting-only "
						"department consumption logs + the Log Consumption card on the "
						"Production Logging page). Ships OFF; no JSON default (Lesson 227)."),
	},
]


def _seed_production_settings_fields():
	"""Create the 3 Production-Logging TS Settings config fields as Custom Fields, ONLY
	if not already present as a Doc/Custom field (has_field covers both) — so it never
	duplicates a future committed DocField. Idempotent."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	to_add = [f for f in _PRODUCTION_SETTINGS_FIELDS if not meta.has_field(f["fieldname"])]
	if not to_add:
		return False
	create_custom_fields({SETTINGS_DOCTYPE: to_add}, ignore_validate=True)
	return True


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
	ps_changed = _apply_variance_status_options()
	perm_changed = _upsert_stores_manager_docperm()
	_seed_page_roles()

	if perm_changed:
		from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
		validate_permissions_for_doctype(DOCTYPE)
	if ps_changed or perm_changed:
		frappe.clear_cache(doctype=DOCTYPE)
