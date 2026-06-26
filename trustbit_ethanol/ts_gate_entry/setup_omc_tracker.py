"""OMC Ethanol Supply Tracker — idempotent after_migrate seeders (planner v6 §2f).

NOT registered in hooks.py here (locked file). The orchestrator wires
after_migrate_omc_tracker() into hooks.py after_migrate separately under a lock.

Seeds (all idempotent, all safe to re-run):
  - seed_omc_list()              : 3 TS OMC rows (IOCL/HPCL/BPCL, codes IO/HP/BP)
  - seed_omc_tracker_fields()    : Customer.ts_omc tag + 3 TS Settings fields
  - seed_omc_customer_tags()     : blank-only, idempotent name-pattern tagger
  - seed_omc_target_perms()      : UPSERT DocPerm matrix for the 3 doctypes (Lesson 292)
  - seed_omc_tracker_page_roles(): apply page roles[] via ORM (Lesson 290)

`after_migrate_omc_tracker()` calls them in order.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields as _create_custom_fields
from frappe.permissions import add_permission, update_permission_property


# ─────────────────────────────────────────────────────────────────────────
#  Static definitions
# ─────────────────────────────────────────────────────────────────────────

OMC_SEED = [
	{"omc_name": "IOCL", "omc_code": "IO", "logo_key": "iocl", "logo_color": "#f97316", "display_order": 1},
	{"omc_name": "HPCL", "omc_code": "HP", "logo_key": "hpcl", "logo_color": "#2563eb", "display_order": 2},
	{"omc_name": "BPCL", "omc_code": "BP", "logo_key": "bpcl", "logo_color": "#eab308", "display_order": 3},
]

# Name-pattern -> OMC. LIKE patterns (case-insensitive); blank-only tagging.
TAG_PATTERNS = [
	("IOCL", ["IOCL%", "INDIAN OIL%"]),
	("HPCL", ["HPCL%", "HINDUSTAN%"]),
	("BPCL", ["BPCL%", "BHARAT PETROLEUM%"]),
]

# Doctypes that share the OMC Supply Target permission matrix.
_OMC_DOCTYPES = ("TS OMC", "TS OMC Supply Target", "TS OMC Depot Plan")
_PERMLEVEL = 0
_FULL_DOER = {
	"read": 1, "write": 1, "create": 1, "delete": 1,
	"report": 1, "export": 1, "print": 1, "email": 1, "share": 1,
}
_READ_ONLY = {
	"read": 1, "write": 0, "create": 0, "delete": 0,
	"report": 1, "export": 1, "print": 1, "email": 1, "share": 1,
}
_PERM_MATRIX = {
	"PM": _FULL_DOER,
	"Grain PM": _FULL_DOER,
	"IT Head": _FULL_DOER,
	"System Manager": _FULL_DOER,
	"CEO": _READ_ONLY,
	"MD": _READ_ONLY,
}

# Page name -> roles[] (filtered to existing roles at apply time, Lesson 290).
_PAGE_ROLES = {
	"omc-supply-tracker": [
		"PM", "Grain PM", "CEO", "MD", "IT Head", "System Manager",
	],
	"omc-supply-tracker-wall": [
		"PM", "Grain PM", "CEO", "MD", "IT Head", "System Manager",
	],
}


# ─────────────────────────────────────────────────────────────────────────
#  1. OMC list master
# ─────────────────────────────────────────────────────────────────────────

def seed_omc_list():
	"""Seed the 3 TS OMC rows (skip-if-exists). Extensible (Lesson 221)."""
	if not frappe.db.exists("DocType", "TS OMC"):
		return
	for spec in OMC_SEED:
		name = spec["omc_name"]
		if frappe.db.exists("TS OMC", name):
			continue
		try:
			frappe.get_doc({
				"doctype": "TS OMC",
				"is_active": 1,
				**spec,
			}).insert(ignore_if_duplicate=True, ignore_permissions=True)
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(message=f"seed_omc_list {name}: {e}", title="seed_omc_tracker")


# ─────────────────────────────────────────────────────────────────────────
#  2. Customer tag + TS Settings fields
# ─────────────────────────────────────────────────────────────────────────

def seed_omc_tracker_fields():
	"""Create Customer.ts_omc tag + 3 TS Settings config fields. Idempotent."""
	custom_fields = {
		"Customer": [
			{
				"fieldname": "ts_omc",
				"fieldtype": "Link",
				"label": "OMC",
				"options": "TS OMC",
				"insert_after": "customer_group",
				"in_standard_filter": 1,
				"description": (
					"Tag this Customer (depot) to an Oil Marketing Company. "
					"Ethanol dispatches to tagged customers roll up to this OMC "
					"on the OMC Supply Tracker."
				),
			},
		],
		"TS Settings": [
			{
				"fieldname": "ts_plant_klpd",
				"fieldtype": "Float",
				"label": "Plant Rated KLPD",
				"precision": "2",
				"permlevel": 1,
				"insert_after": "ts_production_byproduct_warehouse",
				"description": (
					"Plant rated capacity in KL per day (KLPD). Used by the OMC "
					"Supply Tracker for capacity/feasibility. No default — 0/empty "
					"means 'not set' and capacity metrics are omitted."
				),
			},
			{
				"fieldname": "ts_ethanol_item_group",
				"fieldtype": "Link",
				"options": "Item Group",
				"label": "Ethanol Item Group",
				"permlevel": 1,
				"default": "Products",
				"insert_after": "ts_plant_klpd",
				"description": (
					"Item Group whose enabled items count as ethanol on the OMC "
					"Supply Tracker (default 'Products'). Overridden by the explicit "
					"item list below if set."
				),
			},
			{
				"fieldname": "ts_ethanol_items",
				"fieldtype": "Small Text",
				"label": "Ethanol Items (override list)",
				"permlevel": 1,
				"insert_after": "ts_ethanol_item_group",
				"description": (
					"Optional explicit ethanol item codes (one per line or comma-"
					"separated, e.g. 10-02-0001). If set, this overrides the item "
					"group resolution above."
				),
			},
		],
	}
	_create_custom_fields(custom_fields, ignore_validate=True)

	# Singles default trap (Lesson 227): create_custom_fields `default` does NOT
	# auto-populate tabSingles.value. Force the ethanol item group only (KLPD has
	# NO default by design — fail-closed). Skip if already set (don't clobber).
	rows = frappe.db.sql(
		"""SELECT value FROM `tabSingles`
		   WHERE doctype='TS Settings' AND field='ts_ethanol_item_group' LIMIT 1"""
	)
	if not rows:
		try:
			frappe.db.set_single_value("TS Settings", "ts_ethanol_item_group", "Products")
			frappe.db.commit()
		except Exception as e:
			frappe.log_error(message=f"seed_omc_tracker_fields default: {e}", title="seed_omc_tracker")


# ─────────────────────────────────────────────────────────────────────────
#  3. Name-pattern tagger (blank-only, idempotent, review-gated)
# ─────────────────────────────────────────────────────────────────────────

def seed_omc_customer_tags():
	"""Blank-only, idempotent name-pattern tagger. NEVER overwrites a non-blank
	ts_omc. Surfaces an 'N untagged' count. Returns a summary dict."""
	if not frappe.db.has_column("Customer", "ts_omc"):
		return {"tagged": 0, "skipped": 0, "untagged": 0, "note": "ts_omc field absent"}

	tagged = 0
	for omc, patterns in TAG_PATTERNS:
		if not frappe.db.exists("TS OMC", omc):
			continue
		for pat in patterns:
			# Parameterised LIKE; only blank ts_omc rows. customer_name + name both checked.
			rows = frappe.db.sql(
				"""
				SELECT name FROM `tabCustomer`
				WHERE IFNULL(ts_omc, '') = ''
				  AND (UPPER(customer_name) LIKE UPPER(%(pat)s)
				       OR UPPER(name) LIKE UPPER(%(pat)s))
				""",
				{"pat": pat},
			)
			for (cust,) in rows:
				# re-check blank (a prior pattern in this run may have tagged it)
				current = frappe.db.get_value("Customer", cust, "ts_omc")
				if current:
					continue
				frappe.db.set_value("Customer", cust, "ts_omc", omc, update_modified=False)
				tagged += 1

	frappe.db.commit()
	untagged = frappe.db.count("Customer", {"ts_omc": ["in", ["", None]]})
	summary = {"tagged": tagged, "untagged": untagged}
	frappe.logger().info(f"[seed_omc_customer_tags] tagged={tagged} untagged_remaining={untagged}")
	return summary


# ─────────────────────────────────────────────────────────────────────────
#  4. DocPerm UPSERT matrix (Lesson 292)
# ─────────────────────────────────────────────────────────────────────────

def _row_filter(doctype, role):
	return {"parent": doctype, "role": role, "permlevel": _PERMLEVEL, "if_owner": 0}


def seed_omc_target_perms():
	"""UPSERT the DocPerm matrix for the 3 OMC doctypes. Skips roles absent on
	this site (cross-server role gap). add_permission runs setup_custom_perms
	first (keeps Standard DocPerm, Lesson 169)."""
	from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype

	for doctype in _OMC_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		changed = False
		for role, perms in _PERM_MATRIX.items():
			if not frappe.db.exists("Role", role):
				continue
			if not frappe.db.exists("Custom DocPerm", _row_filter(doctype, role)):
				try:
					add_permission(doctype, role, _PERMLEVEL)
					changed = True
				except Exception as e:
					frappe.log_error(message=f"add_permission {doctype}/{role}: {e}", title="seed_omc_tracker")
					continue
			for ptype, value in perms.items():
				current = frappe.db.get_value("Custom DocPerm", _row_filter(doctype, role), ptype)
				if int(current or 0) != value:
					try:
						update_permission_property(doctype, role, _PERMLEVEL, ptype, value, validate=False)
						changed = True
					except Exception as e:
						frappe.log_error(message=f"update_perm {doctype}/{role}/{ptype}: {e}", title="seed_omc_tracker")
		if changed:
			try:
				validate_permissions_for_doctype(doctype)
				frappe.clear_cache(doctype=doctype)
			except Exception as e:
				frappe.log_error(message=f"validate_perms {doctype}: {e}", title="seed_omc_tracker")


# ─────────────────────────────────────────────────────────────────────────
#  5. Page roles via ORM (Lesson 290 — migrate won't sync page roles)
# ─────────────────────────────────────────────────────────────────────────

def seed_omc_tracker_page_roles():
	"""Apply roles[] for both pages via ORM doc.save(), filtered to roles that
	exist on this server. Skips pages that don't exist yet (UI deliverable)."""
	for page_name, roles in _PAGE_ROLES.items():
		if not frappe.db.exists("Page", page_name):
			continue
		try:
			doc = frappe.get_doc("Page", page_name)
			existing = {r.role for r in (doc.roles or [])}
			added = False
			for role in roles:
				if role in existing:
					continue
				if not frappe.db.exists("Role", role):
					continue
				doc.append("roles", {"role": role})
				added = True
			if added:
				doc.flags.ignore_permissions = True
				doc.save()
		except Exception as e:
			frappe.log_error(message=f"seed page roles {page_name}: {e}", title="seed_omc_tracker")


# ─────────────────────────────────────────────────────────────────────────
#  Top-level orchestrator (wired into hooks.py after_migrate by the orchestrator)
# ─────────────────────────────────────────────────────────────────────────

def after_migrate_omc_tracker():
	"""Run all OMC tracker seeders in dependency order. Idempotent."""
	if frappe.flags.in_install:
		# fresh-install: doctypes may not be ready; skip — runs on next migrate.
		return
	seed_omc_tracker_fields()      # Customer.ts_omc must exist before tagging
	seed_omc_list()                # TS OMC rows (tag targets)
	seed_omc_customer_tags()       # blank-only name-pattern tagger
	seed_omc_target_perms()        # DocPerm matrix
	seed_omc_tracker_page_roles()  # page roles (skips absent pages)
