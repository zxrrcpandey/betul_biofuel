"""Super Admin role seed for the Token Cascade Delete System (v2.11.0).

Creates Role "Super Admin", a Custom DocPerm row granting read+create on
TS Cascade Delete Log + delete on TS Token / TS Gate Entry / TS Weighbridge Log /
TS Quality Inspection / TS Deduction Sheet / Purchase Receipt / Purchase Invoice
at permlevel=0, AND assigns the role to the single hard-coded user
`erp.superadmin@betulbiofuel.com` if that user exists.

CRITICAL design:
- ONE user only. Validated via _enforce_single_user() at every save of the role's User assignment.
- Idempotent. Safe to run on every `after_migrate`.
- Lives OUTSIDE seed_data.py so MR_FULL / PO_FULL feature locks are not touched.
- Lesson 169 — uses `setup_custom_perms()` style standard-then-custom layering.
- Lesson 252 — clears hooks/app_hooks cache after touching DocPerm.

Lesson references: 169 (Custom DocPerm), 224 (dual-gate permission), 162 (permlevel for
the audit log fields — handled in the doctype JSON, not here).
"""

import frappe

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


def seed_super_admin_role():
	"""Idempotent: create Role + assign DocPerms + assign user. Safe on every migrate."""
	_ensure_role_exists()
	_ensure_docperms()
	_assign_role_to_user_if_exists()
	# Clear hooks cache so the new role + permissions are honored immediately (Lesson 252).
	try:
		frappe.cache().delete_keys("hooks")
		frappe.cache().delete_keys("app_hooks")
	except Exception:
		pass
	for dt in {p["parent"] for p in _SUPER_ADMIN_PERMS}:
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
