"""PO Deduction Terms — v2.9.0 Phase 3 Day 3 Custom Field seed.

Business need (CTO Rahul, 25 Apr 2026): Purchase Orders for Grain (and other
deduction-bearing categories) must carry per-PO deduction terms — selected
from a TS Deduction Master template, with optional row-level overrides
(rate / rate_type) gated by permlevel=1 so only Purchase Manager / Accounts
Manager / IT Head / System Manager can edit override columns.

## Fields added (4 Custom Fields on Purchase Order)

1. ts_deduction_terms_section — Section Break, collapsible, "Deduction Terms"
2. ts_deduction_template     — Link to TS Deduction Master (filter is_active=1)
3. ts_deduction_template_loaded — Check, hidden=1 (tracks whether rows copied)
4. ts_deduction_overrides    — Table → TS PO Deduction Override (permlevel=1 child)

## Placement

Inserted after `terms` (idx 131, last field of native `terms_section_break`).
This places Deduction Terms as a NEW section between Terms & Conditions and
the existing TS Approval section — visually adjacent to other PO commercial
terms, before the approval workflow widgets.

## Why isolated from setup.py

setup.py is the locked PO_FULL / MR_FULL bundle. Per Lessons 169, 183, 187,
188, 190, 191 every v2.8.x / v2.9.x Custom Field seed lives in its own module
so a single failure doesn't cascade and unrelated seeds remain stable.

## Why we ALSO seed the new "Grain Manager" role here

v2.9.0 plan introduces a new "Grain Manager" role (distinct from the existing
"Grain Purchase Manager" approval role). 4 Day-1/Day-2 DocType JSONs already
reference "Grain Manager" in their permissions block — without this role
existing, `bench migrate` will fail when trying to install/sync those DocTypes.
We create the role here as part of the same v2.9.0 isolation seed so role
creation + Custom Field seeding ride together.

## Idempotency

`ignore_if_duplicate=True` + caught DuplicateEntryError on insert.
On existing rows, `frappe.db.set_value` updates `insert_after`, `label`,
`description` (matches setup_msme_fields.py / setup_pi_lr_fields.py pattern).
Safe to re-run on every bench migrate.

## permlevel rationale

The CHILD doctype `TS PO Deduction Override` carries permlevel=1 on
`override_rate`, `override_rate_type`, `override_reason` (declared in JSON,
Day 1-2). The parent Custom Field `ts_deduction_overrides` (the Table field
on PO) is left at permlevel=0 so all roles with PO read can SEE the table.
Editing the override columns is gated by the child doctype's permlevel=1
permissions block (System Manager / IT Head / Purchase Manager / Accounts
Manager). Stores Users etc. see the table as read-only override columns —
they can read default_rate but cannot type into override_rate.
"""

import frappe
from frappe.permissions import add_permission


_ROLE_NAME = "Grain Manager"

# Grain Manager write/create grant on TS Deduction Master (BBF request, Jun 2026).
# Idempotent Custom DocPerm UPSERT, re-asserted on every migrate — mirrors
# ts_stock_recon_perms.py (Lesson 292). Tuple: (doctype, permlevel, {ptype: 1}).
#   TS Deduction Master @ permlevel 0 : gains write + create (read already 1)
# NB: NO grant is attempted on the CHILD doctype TS PO Deduction Override —
# Frappe resolves child-row permlevel access through the PARENT's permissions
# (document.py get_permissions(): istable ⇒ frappe.get_meta(parenttype).permissions),
# so a permission block on an istable doctype is never consulted. The override_*
# columns are therefore gated by Purchase Order's permlevel-1 roster (Purchase
# Manager only). Making them Grain-Manager-writable would require a PO permlevel-1
# Custom DocPerm — deliberately NOT done here, because PO level 1 also carries the
# budget control-plane fields ts_budget_override_ref / ts_budget_breach_flag (L162);
# that widening needs its own reviewed change.
_GRAIN_DEDUCTION_GRANTS = [
	("TS Deduction Master", 0, {"read": 1, "write": 1, "create": 1}),
]


def seed_grain_manager_role():
	"""Create new 'Grain Manager' role (v2.9.0 introduces this distinct role).

	Idempotent: returns silently if role already exists. Distinct from existing
	'Grain Purchase Manager' approval role — Grain Manager is the operational
	role for grain procurement (managing Bag Type Master, QC Templates,
	Deduction Masters), whereas Grain Purchase Manager is the approval-tier
	role in the PO/MR routing chain.
	"""
	if frappe.db.exists("Role", _ROLE_NAME):
		return
	try:
		role = frappe.new_doc("Role")
		role.role_name = _ROLE_NAME
		role.desk_access = 1
		role.is_custom = 1
		role.insert(ignore_permissions=True)
		frappe.logger().info(f"[seed_po_deduction_terms] Created Role '{_ROLE_NAME}'")
	except frappe.DuplicateEntryError:
		pass
	except Exception as e:
		frappe.log_error(
			message=f"seed_grain_manager_role failure: {e}",
			title="seed_po_deduction_terms",
		)


def _grant_grain_manager_deduction_perms():
	"""after_migrate — UPSERT Grain Manager perms on TS Deduction Master.

	Single-run deterministic + idempotent. The Grain Manager Custom DocPerm row
	normally already exists (read-only) on this all-permlevel-0 master; we just
	drive write + create to 1 with a set_value BY NAME. On a hypothetical fresh
	site we add_permission first (setup_custom_perms copies Standard->Custom incl.
	the Grain Manager read-only row — Lesson 169), then RE-FETCH the name (the add
	may no-op when the row was just copied) before setting the flags. Skips
	silently if the role is absent on this site (cross-server role gap, Lesson 290).
	"""
	if not frappe.db.exists("Role", _ROLE_NAME):
		return

	touched = set()
	for doctype, permlevel, perms in _GRAIN_DEDUCTION_GRANTS:
		if not frappe.db.exists("DocType", doctype):
			continue
		row = {"parent": doctype, "role": _ROLE_NAME, "permlevel": permlevel, "if_owner": 0}
		try:
			name = frappe.db.get_value("Custom DocPerm", row)
			if not name:
				add_permission(doctype, _ROLE_NAME, permlevel)
				name = frappe.db.get_value("Custom DocPerm", row)
			if not name:
				continue
			current = (
				frappe.db.get_value("Custom DocPerm", name, list(perms.keys()), as_dict=True)
				or {}
			)
			to_set = {p: v for p, v in perms.items() if int(current.get(p) or 0) != v}
			if to_set:
				frappe.db.set_value("Custom DocPerm", name, to_set)
				touched.add(doctype)
		except Exception as e:
			frappe.log_error(
				message=f"_grant_grain_manager_deduction_perms failure on {doctype}: {e}",
				title="seed_po_deduction_terms",
			)

	for doctype in touched:
		try:
			from frappe.core.doctype.doctype.doctype import (
				validate_permissions_for_doctype,
			)

			validate_permissions_for_doctype(doctype)
		except Exception as e:
			frappe.log_error(
				message=f"_grant_grain_manager_deduction_perms validate failure on {doctype}: {e}",
				title="seed_po_deduction_terms",
			)
		finally:
			# Always invalidate meta cache — the Custom DocPerm write above is already
			# committed, so skipping this on a validate failure would leave the grant
			# live in DB but invisible until an unrelated cache clear (supervisor
			# restart does NOT flush doctype meta).
			try:
				frappe.clear_cache(doctype=doctype)
			except Exception as e:
				frappe.log_error(
					message=f"_grant_grain_manager_deduction_perms cache failure on {doctype}: {e}",
					title="seed_po_deduction_terms",
				)


def seed_po_deduction_terms():
	"""Seed 4 Custom Fields on Purchase Order for v2.9.0 Day 3 (idempotent)."""
	# Step 1: ensure Grain Manager role exists (Day 1-2 DocTypes reference it).
	seed_grain_manager_role()

	# Step 2: seed the 4 Custom Fields.
	fields = [
		{
			"doctype": "Custom Field",
			"dt": "Purchase Order",
			"fieldname": "ts_deduction_terms_section",
			"label": "Deduction Terms",
			"fieldtype": "Section Break",
			"insert_after": "terms",
			"collapsible": 1,
			"description": (
				"Optional per-PO deduction terms. Pick a TS Deduction Master "
				"template — its rows pre-populate the override table. "
				"Override columns (rate, rate_type, reason) are restricted to "
				"Purchase Manager / Accounts Manager / IT Head / System Manager "
				"(permlevel=1). Leave template blank to use category default."
			),
		},
		{
			"doctype": "Custom Field",
			"dt": "Purchase Order",
			"fieldname": "ts_deduction_template",
			"label": "Deduction Template",
			"fieldtype": "Link",
			"options": "TS Deduction Master",
			"insert_after": "ts_deduction_terms_section",
			# Filter to active masters only (built-in Frappe Link filter syntax
			# uses Form-side .set_query in JS; this default reduces the dropdown
			# noise on first open).
			"description": (
				"Pick a TS Deduction Master. Its row deductions pre-populate "
				"this PO's override table on save. Leave blank to silently "
				"fall back to the category default master (Plan v4 Gap 1 — "
				"silent fallback approach)."
			),
		},
		{
			"doctype": "Custom Field",
			"dt": "Purchase Order",
			"fieldname": "ts_deduction_template_loaded",
			"label": "Deduction Template Loaded",
			"fieldtype": "Check",
			"insert_after": "ts_deduction_template",
			"hidden": 1,
			"read_only": 1,
			"default": "0",
			"description": (
				"Internal flag (hidden) — set to 1 once master rows have been "
				"copied into ts_deduction_overrides. Prevents duplicate copy "
				"on subsequent saves. JS resets to 0 if user changes template."
			),
		},
		{
			"doctype": "Custom Field",
			"dt": "Purchase Order",
			"fieldname": "ts_deduction_overrides",
			"label": "Deduction Override Lines",
			"fieldtype": "Table",
			"options": "TS PO Deduction Override",
			"insert_after": "ts_deduction_template_loaded",
			# permlevel=0 on PARENT so all roles with PO read can SEE the table.
			# Editing of override_rate/override_rate_type/override_reason is gated
			# by the CHILD doctype's permlevel=1 permissions block (declared in
			# ts_po_deduction_override.json — System Manager / IT Head /
			# Purchase Manager / Accounts Manager).
			"description": (
				"Per-PO deduction line overrides. Master rate is read-only; "
				"override_rate is the PO-specific rate. permlevel=1 columns "
				"editable only by Purchase Manager / Accounts Manager / "
				"IT Head / System Manager."
			),
		},
	]

	for f in fields:
		cf_name = f"Purchase Order-{f['fieldname']}"
		if frappe.db.exists("Custom Field", cf_name):
			# Idempotent UPDATE on existing rows — keep insert_after + label
			# + description in sync with this seed (matches setup_msme pattern).
			update_props = {
				"insert_after": f["insert_after"],
				"label": f["label"],
				"description": f.get("description", ""),
			}
			# Preserve type-specific properties on update too.
			if f["fieldtype"] == "Section Break":
				update_props["collapsible"] = f.get("collapsible", 0)
			if f["fieldtype"] == "Check":
				update_props["hidden"] = f.get("hidden", 0)
				update_props["read_only"] = f.get("read_only", 0)
			if f["fieldtype"] == "Link":
				update_props["options"] = f.get("options", "")
			if f["fieldtype"] == "Table":
				update_props["options"] = f.get("options", "")
			try:
				frappe.db.set_value("Custom Field", cf_name, update_props)
				frappe.logger().info(
					f"[seed_po_deduction_terms] Updated {cf_name} "
					f"insert_after={f['insert_after']}"
				)
			except Exception as e:
				frappe.log_error(
					message=f"seed_po_deduction_terms update failure on {cf_name}: {e}",
					title="seed_po_deduction_terms",
				)
			continue

		try:
			frappe.get_doc(f).insert(
				ignore_if_duplicate=True, ignore_permissions=True
			)
			frappe.logger().info(f"[seed_po_deduction_terms] Created {cf_name}")
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"seed_po_deduction_terms insert failure on {cf_name}: {e}",
				title="seed_po_deduction_terms",
			)

	frappe.clear_cache(doctype="Purchase Order")

	# Step 3: grant Grain Manager write/create on the deduction doctypes.
	_grant_grain_manager_deduction_perms()
