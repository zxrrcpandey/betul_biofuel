"""Admin cancel permissions — v2.8.3.2.

Grants System Manager role full cancel / amend / delete perms on TS operational
doctypes so "Admin" users (Rahul & team) can unblock stuck documents without
needing to switch to the IT Head role.

Idempotent via `ignore_if_duplicate=True` + guard check. Kept isolated from
`setup.py` because that file contains MR_FULL / PO_FULL locked seed functions
(Lesson 169 caution).

Why System Manager and not also CTO / Administrator?
- Administrator auto-bypasses all permission checks (Frappe core) — no seeding needed.
- CTO is a custom role without cancel perm anywhere today; granting it here would
  widen scope beyond what the current ask requires. If operations decides CTO
  should also cancel, add in a follow-up commit.
"""

import frappe

# v2.8.3.2: doctypes where System Manager gets cancel/amend/delete.
# TS Token + TS Weighbridge Log + TS Material Inspection are all docstatus=0
# forever — cancel is a no-op, but delete IS useful for correcting bad records.
# TS Gate Entry is submittable — cancel + amend are the real wins here.
_ADMIN_CANCEL_DOCTYPES = (
	"TS Token",
	"TS Gate Entry",
	"TS Weighbridge Log",
	"TS Material Inspection",
)

_ROLE = "System Manager"


def seed_admin_cancel_perms():
	"""after_migrate seed — idempotent. Adds System Manager Custom DocPerm rows
	with cancel/amend/delete/write/read/create all set to 1 at permlevel 0.

	Safe to run repeatedly — `ignore_if_duplicate=True` prevents row duplication,
	and an explicit `frappe.db.exists` guard keeps the payload identical across
	runs (we don't want this seed to silently demote a permission the user later
	widened via UI).
	"""
	created = 0
	for dt in _ADMIN_CANCEL_DOCTYPES:
		# Skip if a Custom DocPerm row for (dt, System Manager, permlevel=0) already exists.
		existing = frappe.db.get_value(
			"Custom DocPerm",
			{"parent": dt, "role": _ROLE, "permlevel": 0},
			["name", "cancel", "amend", "delete"],
			as_dict=True,
		)
		if existing and existing.cancel and existing.amend and existing.delete:
			# Already correctly set — nothing to do.
			continue

		payload = {
			"doctype": "Custom DocPerm",
			"parent": dt,
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": _ROLE,
			"permlevel": 0,
			"read": 1,
			"write": 1,
			"create": 1,
			"submit": 1 if dt == "TS Gate Entry" else 0,  # only GE is submittable
			"cancel": 1,
			"amend": 1 if dt == "TS Gate Entry" else 0,  # amend only meaningful on submittable
			"delete": 1,
			"report": 1,
			"export": 1,
			"import": 0,
			"share": 1,
			"print": 1,
			"email": 1,
		}

		if existing:
			# Update the existing row in place rather than inserting a duplicate.
			try:
				frappe.db.set_value(
					"Custom DocPerm", existing.name,
					{
						"cancel": payload["cancel"],
						"amend": payload["amend"],
						"delete": payload["delete"],
					},
				)
				created += 1
				frappe.logger().info(f"[seed_admin_cancel] updated {dt}.{_ROLE} cancel/amend/delete")
			except Exception as e:
				frappe.log_error(
					message=f"seed_admin_cancel update failure on {dt}: {e}",
					title="seed_admin_cancel_perms",
				)
		else:
			# Insert new row.
			try:
				frappe.get_doc(payload).insert(
					ignore_if_duplicate=True, ignore_permissions=True,
				)
				created += 1
				frappe.logger().info(f"[seed_admin_cancel] created {dt}.{_ROLE}")
			except frappe.DuplicateEntryError:
				pass
			except Exception as e:
				frappe.log_error(
					message=f"seed_admin_cancel insert failure on {dt}: {e}",
					title="seed_admin_cancel_perms",
				)

	if created:
		# Clear DocType meta cache so the new perms take effect without restart.
		for dt in _ADMIN_CANCEL_DOCTYPES:
			frappe.clear_cache(doctype=dt)
