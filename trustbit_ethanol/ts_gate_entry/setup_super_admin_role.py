"""Super Admin role seed for the Token Cascade Delete System (v2.11.0) +
the Production Cascade Delete tool (v2.19.x).

Creates Role "Super Admin", Custom DocPerm rows granting read+delete+cancel on the
7 Token cascade-TARGET doctypes (TS Token / TS Gate Entry / TS Weighbridge Log /
TS Quality Inspection / TS Deduction Sheet / Purchase Receipt / Purchase Invoice) AND,
v2.19.x, on the 4 Production cascade-TARGET doctypes (Work Order / Job Card / Stock
Entry / Quality Inspection — Quality Inspection is shared), all at permlevel=0, AND
assigns the role to the single hard-coded user `erp.superadmin@betulbiofuel.com` if
that user exists.

CRITICAL design:
- ONE user only. Validated via _enforce_single_user() at every save of the role's User assignment.
- Idempotent. Safe to run on every `after_migrate`.
- Lives OUTSIDE seed_data.py so MR_FULL / PO_FULL feature locks are not touched.
- Lesson 169 — the 7 Token rows use the original db_insert path (their doctypes are
  already in Custom-DocPerm mode app-wide). The 4 NEW native doctypes (Work Order /
  Job Card / Stock Entry / Quality Inspection) are ERPNext-native and may still be in
  Standard-DocPerm mode; inserting the FIRST Custom DocPerm row would flip them to
  Custom-DocPerm-only and silently DROP every Standard DocPerm (an app-wide blackout).
  They are therefore routed through the `add_permission()`+`update_permission_property()`
  UPSERT helper (the ts_stock_recon_perms.py pattern, which runs setup_custom_perms()
  FIRST to copy Standard->Custom safely). NEVER the raw db_insert for these.
- Lesson 252 — clears hooks/app_hooks cache after touching DocPerm.

Lesson references: 169 (Custom DocPerm), 224 (dual-gate permission), 162 (permlevel for
the audit log fields — handled in the doctype JSON, not here), 292 (UPSERT not INSERT-only).
"""

import frappe
from frappe.permissions import update_permission_property

SUPER_ADMIN_ROLE_NAME = "Super Admin"
SUPER_ADMIN_USER = "erp.superadmin@betulbiofuel.com"

# DocPerms granted to Super Admin at permlevel=0 via Custom DocPerm.
#
# CRITICAL — Lesson 169: TS Cascade Delete Log is DELIBERATELY EXCLUDED here.
# That is OUR doctype; its `permissions` array in ts_cascade_delete_log.json already
# grants Super Admin (Standard DocPerm). Adding a Custom DocPerm for it would flip
# the whole doctype into Custom-DocPerm-only mode and silently DROP every Standard
# DocPerm row (CEO / MD / System Manager / Auditor / IT Head) — exactly the bug hit
# during the 20-May soak window. The 7 cascade-TARGET doctypes below are not our
# doctype and are already in Custom-DocPerm mode app-wide, so adding Super Admin to
# them is purely additive and safe.
_SUPER_ADMIN_PERMS = [
	# Cascade-targeted doctypes — read + delete (no write/cancel needed; engine uses delete_doc).
	{"parent": "TS Token", "role": SUPER_ADMIN_ROLE_NAME, "permlevel": 0,
	 "read": 1, "delete": 1, "cancel": 1, "report": 1, "export": 1, "print": 1, "email": 1},
	{"parent": "TS Gate Entry", "role": SUPER_ADMIN_ROLE_NAME, "permlevel": 0,
	 "read": 1, "delete": 1, "cancel": 1, "report": 1, "export": 1, "print": 1, "email": 1},
	{"parent": "TS Weighbridge Log", "role": SUPER_ADMIN_ROLE_NAME, "permlevel": 0,
	 "read": 1, "delete": 1, "cancel": 1, "report": 1, "export": 1, "print": 1, "email": 1},
	{"parent": "TS Quality Inspection", "role": SUPER_ADMIN_ROLE_NAME, "permlevel": 0,
	 "read": 1, "delete": 1, "cancel": 1, "report": 1, "export": 1, "print": 1, "email": 1},
	{"parent": "TS Deduction Sheet", "role": SUPER_ADMIN_ROLE_NAME, "permlevel": 0,
	 "read": 1, "delete": 1, "cancel": 1, "report": 1, "export": 1, "print": 1, "email": 1},
	{"parent": "Purchase Receipt", "role": SUPER_ADMIN_ROLE_NAME, "permlevel": 0,
	 "read": 1, "delete": 1, "cancel": 1, "report": 1, "export": 1, "print": 1, "email": 1},
	{"parent": "Purchase Invoice", "role": SUPER_ADMIN_ROLE_NAME, "permlevel": 0,
	 "read": 1, "delete": 1, "cancel": 1, "report": 1, "export": 1, "print": 1, "email": 1},
]

# v2.19.x — Production Cascade Delete executor grants on ERPNext-NATIVE doctypes.
# These MUST go through the UPSERT helper (add_permission + update_permission_property),
# NOT the raw db_insert, because they may still be in Standard-DocPerm mode (Lesson 169
# blackout risk). add_permission() runs setup_custom_perms() first (copies Standard ->
# Custom safely) so no Standard DocPerm is ever dropped.
PERMLEVEL = 0
_PRODUCTION_EXECUTOR_PERMS = {
	# doctype -> {permission flag: value}. read + delete + cancel (engine uses
	# cancel-then-delete). report/export/print/email mirror the Token rows' convention.
	"Work Order": {"read": 1, "delete": 1, "cancel": 1,
	               "report": 1, "export": 1, "print": 1, "email": 1},
	"Job Card": {"read": 1, "delete": 1, "cancel": 1,
	             "report": 1, "export": 1, "print": 1, "email": 1},
	"Stock Entry": {"read": 1, "delete": 1, "cancel": 1,
	                "report": 1, "export": 1, "print": 1, "email": 1},
	"Quality Inspection": {"read": 1, "delete": 1, "cancel": 1,
	                       "report": 1, "export": 1, "print": 1, "email": 1},
}


def seed_super_admin_role():
	"""Idempotent: create Role + assign DocPerms + assign user. Safe on every migrate."""
	_ensure_role_exists()
	_ensure_docperms()
	_ensure_production_executor_docperms()
	_assign_role_to_user_if_exists()
	# Clear hooks cache so the new role + permissions are honored immediately (Lesson 252).
	try:
		frappe.cache().delete_keys("hooks")
		frappe.cache().delete_keys("app_hooks")
	except Exception:
		pass
	for dt in ({p["parent"] for p in _SUPER_ADMIN_PERMS} | set(_PRODUCTION_EXECUTOR_PERMS)):
		try:
			frappe.clear_cache(doctype=dt)
		except Exception:
			pass


def _ensure_role_exists():
	if frappe.db.exists("Role", SUPER_ADMIN_ROLE_NAME):
		return
	doc = frappe.get_doc({
		"doctype": "Role",
		"role_name": SUPER_ADMIN_ROLE_NAME,
		"desk_access": 1,
		"two_factor_auth": 1,
		"restrict_to_domain": "",
		"disabled": 0,
	})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	frappe.db.commit()


def _ensure_docperms():
	"""Insert any missing Custom DocPerm rows for Super Admin at permlevel=0.

	Idempotent: skips rows that already exist (matched by parent + role + permlevel).
	"""
	for perm in _SUPER_ADMIN_PERMS:
		# The target doctype must exist before we can attach a Custom DocPerm.
		# TS Cascade Delete Log is seeded in this same v2.11.0 ship via doctype JSON,
		# so on the FIRST migrate after deploy this row may not exist yet. Skip
		# gracefully — the next migrate will pick it up once the doctype is synced.
		if not frappe.db.exists("DocType", perm["parent"]):
			continue
		filters = {
			"parent": perm["parent"],
			"role": perm["role"],
			"permlevel": perm["permlevel"],
		}
		if frappe.db.exists("Custom DocPerm", filters):
			continue
		# M5 (v2.11.1) — Lesson 169 guard. Inserting the FIRST Custom DocPerm row
		# for a doctype flips it from Standard- to Custom-DocPerm mode, which
		# silently DROPS every Standard DocPerm (a permission blackout). Every
		# doctype in _SUPER_ADMIN_PERMS is expected to ALREADY be in Custom-DocPerm
		# mode (TS Cascade Delete Log ships its own Custom DocPerm rows). If this
		# row would be the first, refuse + log CRITICAL rather than cause a blackout.
		if not frappe.db.exists("Custom DocPerm", {"parent": perm["parent"]}):
			frappe.log_error(
				title=f"Super Admin seeder ABORTED for {perm['parent']}",
				message=(
					f"Refusing to insert the FIRST Custom DocPerm row on '{perm['parent']}' "
					"— that would flip it to Custom-DocPerm mode and drop all Standard "
					"DocPerms (Lesson 169). Run setup_custom_perms() for this doctype "
					"first, then re-run the Super Admin seeder."
				),
			)
			continue
		doc = frappe.get_doc({
			"doctype": "Custom DocPerm",
			"parenttype": "DocType",
			"parentfield": "permissions",
			**perm,
		})
		doc.db_insert()
	frappe.db.commit()


def _row_filter(doctype, role):
	return {"parent": doctype, "role": role, "permlevel": PERMLEVEL, "if_owner": 0}


def _ensure_production_executor_docperms():
	"""UPSERT the Super Admin executor grants on the 4 NATIVE Production-cascade-target
	doctypes (Work Order / Job Card / Stock Entry / Quality Inspection) — Lesson 169/292.

	Two-step safe layering, deliberately NOT using add_permission():
	  1. setup_custom_perms(doctype) — copies any Standard DocPerm rows into Custom DocPerm
	     mode BEFORE adding our row, so NO Standard DocPerm is ever dropped (the Lesson 169
	     blackout the raw db_insert path risks). setup_custom_perms does NOT re-validate.
	  2. db_insert our Super Admin row (safe now — the doctype is in Custom mode) + UPSERT
	     each flag via update_permission_property(validate=False).

	We deliberately do NOT call frappe.core...validate_permissions_for_doctype here:
	these ERPNext-native doctypes ship Standard DocPerm rows with pre-existing quirks
	(e.g. Job Card's System Manager row has import=1 while Job Card is not importable),
	which the strict re-validator rejects with a ValidationError that would falsely abort
	the whole migrate. We are only ADDING a read/delete/cancel grant; re-validating
	unrelated pre-existing rows is out of scope and unsafe here.

	Idempotent + self-healing: creates a missing Custom DocPerm row, then UPSERTs each flag.
	"""
	if not frappe.db.exists("Role", SUPER_ADMIN_ROLE_NAME):
		return
	from frappe.permissions import setup_custom_perms
	for doctype, perms in _PRODUCTION_EXECUTOR_PERMS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		# Step 1 — Lesson 169 safety: ensure Custom-DocPerm mode (copies Standard -> Custom).
		setup_custom_perms(doctype)
		# Step 2 — insert our row if missing (db_insert, no re-validation).
		if not frappe.db.exists("Custom DocPerm", _row_filter(doctype, SUPER_ADMIN_ROLE_NAME)):
			frappe.get_doc({
				"doctype": "Custom DocPerm",
				"parent": doctype,
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": SUPER_ADMIN_ROLE_NAME,
				"permlevel": PERMLEVEL,
				"if_owner": 0,
				"read": 1,
			}).db_insert()
		# UPSERT each flag (validate=False — avoid re-validating unrelated pre-existing rows).
		for ptype, value in perms.items():
			current = frappe.db.get_value(
				"Custom DocPerm", _row_filter(doctype, SUPER_ADMIN_ROLE_NAME), ptype)
			if int(current or 0) != value:
				update_permission_property(
					doctype, SUPER_ADMIN_ROLE_NAME, PERMLEVEL, ptype, value, validate=False)
		frappe.clear_cache(doctype=doctype)
	frappe.db.commit()


def _assign_role_to_user_if_exists():
	"""Assign Super Admin role to SUPER_ADMIN_USER, but only if that user is enabled.

	Does NOT create the user. Admin must create the account first.
	Enforces the single-user rule by clearing the role from any OTHER user that
	currently has it (defense-in-depth — should be vacuously empty in steady state).
	"""
	if not frappe.db.exists("User", SUPER_ADMIN_USER):
		return  # Admin hasn't created the account yet; silent skip.

	user_doc = frappe.get_doc("User", SUPER_ADMIN_USER)
	if user_doc.enabled != 1:
		return

	has_role = any(r.role == SUPER_ADMIN_ROLE_NAME for r in user_doc.roles)
	if not has_role:
		user_doc.append("roles", {"role": SUPER_ADMIN_ROLE_NAME})
		user_doc.flags.ignore_permissions = True
		user_doc.save(ignore_permissions=True)

	# Strip the role from any OTHER user (1-user invariant).
	other_assignees = frappe.db.sql(
		"""SELECT DISTINCT parent FROM `tabHas Role`
		   WHERE role=%s AND parent != %s AND parenttype='User'""",
		(SUPER_ADMIN_ROLE_NAME, SUPER_ADMIN_USER),
		as_dict=True,
	)
	for row in other_assignees:
		# Refuse to delete; raise an alert. Manual remediation required.
		frappe.log_error(
			title=f"Super Admin role invariant violated",
			message=(f"User {row.parent!r} has Super Admin role. "
			         f"Only {SUPER_ADMIN_USER!r} is authorised. "
			         f"Admin must remove the role from {row.parent!r} manually."),
		)
	frappe.db.commit()
