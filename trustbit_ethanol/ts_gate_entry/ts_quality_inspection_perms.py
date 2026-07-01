"""TS Quality Inspection — restrict access to a fixed allow-list (BBF policy, Jun 2026).

Business rule (user request, 29 Jun 2026): the Quality Reports (TS Quality
Inspection records — they carry the auto-generated "Quality Report No"
BBPL-QR-YY-#####) must be **accessible only** to a defined set of roles:

  * Management ........ CEO, MD (kept at their existing full access)
  * Oversight ......... AVP (read-only view)
  * Purchase .......... Purchase User, Purchase Manager, Grain Purchase Manager,
                        Grain Manager  ("Purchase user/manager", all four)
  * Accounts dept ..... Accounts User, Accounts Manager (read-only view)

Plus the OPERATIONAL CREATORS are deliberately kept so the quality workflow
does not break (they create/submit the inspections that drive deductions):

  * Quality Inspector .. create + submit
  * Stores User ........ create draft
  * Grain Manager ...... create + submit   (also a "Purchase" role above)
  * Grain Purchase Mgr . create + submit   (also a "Purchase" role above)

REVOKED (had view access, NOT on the allow-list): G1 Security, G2 Gate Operator,
Weighbridge Operator, Quality Manager. (No user is locked out by revoking
Quality Manager — every QM holder also has Quality Inspector / Stores / Grain /
admin and keeps access through those.)

PRESERVED, never touched: the platform-admin roles (System Manager, IT Head,
Super Admin, Administrator) so system administration is unaffected.

Mechanism — same proven pattern as ts_stock_recon_perms.py (Lesson 292):
Custom DocPerm already exists for this doctype on demo + prod, so Frappe ignores
Standard DocPerm entirely (Lesson 169) and Custom DocPerm is the only correct
lever. The generic seed_custom_docperm() is INSERT-ONLY and can neither downgrade
nor REMOVE a row, so this seeder is declarative + idempotent: it UPSERTs every
allow-list flag and DELETEs the permlevel-0 row of any role that is neither
on the allow-list nor in the admin PRESERVE set. Re-asserted on every migrate,
self-healing any drift (true "only" enforcement).
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

DOCTYPE = "TS Quality Inspection"
PERMLEVEL = 0

# --- permission profiles (permlevel 0) ------------------------------------
# Operational creator — read/write/create/submit + view-class flags.
_CREATOR = {
    "read": 1, "write": 1, "create": 1, "submit": 1, "report": 1, "export": 1,
    "print": 1, "email": 1, "share": 1, "cancel": 0, "delete": 0, "amend": 0,
}
# Stores — creates a draft inspection but does not submit it.
_STORES = {
    "read": 1, "write": 1, "create": 1, "submit": 0, "report": 0, "export": 0,
    "print": 0, "email": 0, "share": 0, "cancel": 0, "delete": 0, "amend": 0,
}
# Management — full control (CEO / MD already sit here; re-assert is a no-op).
_FULL = {
    "read": 1, "write": 1, "create": 1, "submit": 1, "report": 1, "export": 1,
    "print": 1, "email": 1, "share": 1, "cancel": 1, "delete": 1, "amend": 1,
}
# Read-only viewer — the oversight / purchase / accounts consumers.
_VIEW = {
    "read": 1, "write": 0, "create": 0, "submit": 0, "report": 1, "export": 1,
    "print": 1, "email": 1, "share": 1, "cancel": 0, "delete": 0, "amend": 0,
}

# Role -> target Custom DocPerm row at permlevel 0. Re-asserted every migrate.
_TARGET_MATRIX = {
    # creators (keep operational access)
    "Quality Inspector": _CREATOR,
    "Stores User": _STORES,
    "Grain Manager": _CREATOR,
    "Grain Purchase Manager": _CREATOR,
    # management (full)
    "CEO": _FULL,
    "MD": _FULL,
    # read-only consumers
    "AVP": _VIEW,
    "Purchase User": _VIEW,
    "Purchase Manager": _VIEW,
    "Accounts User": _VIEW,
    "Accounts Manager": _VIEW,
}

# Platform-admin roles: never assert, never revoke — leave exactly as-is so
# system administration is unaffected by this business-access policy.
_PRESERVE = {"System Manager", "IT Head", "Super Admin", "Administrator"}


def _row_filter(role):
    return {"parent": DOCTYPE, "role": role, "permlevel": PERMLEVEL, "if_owner": 0}


def seed_quality_inspection_permissions():
    """after_migrate — enforce the TS Quality Inspection Custom DocPerm allow-list.

    Idempotent. Two phases:
      1. UPSERT every allow-list role to its target row (read first via
         add_permission's default, so no transient invalid state).
      2. REVOKE — delete the permlevel-0 Custom DocPerm row of any role that is
         neither on the allow-list nor in the admin PRESERVE set.
    Skips roles that don't exist on this site (cross-server role gap).
    """
    changed = False

    # --- phase 1: grant / re-assert the allow-list ------------------------
    for role, perms in _TARGET_MATRIX.items():
        if not frappe.db.exists("Role", role):
            continue
        if not frappe.db.exists("Custom DocPerm", _row_filter(role)):
            # add_permission() runs setup_custom_perms() first (Lesson 169 guard)
            add_permission(DOCTYPE, role, PERMLEVEL)
            changed = True
        for ptype, value in perms.items():
            current = frappe.db.get_value("Custom DocPerm", _row_filter(role), ptype)
            if int(current or 0) != value:
                update_permission_property(
                    DOCTYPE, role, PERMLEVEL, ptype, value, validate=False
                )
                changed = True

    # --- phase 2: revoke everyone outside the allow-list (true "only") ----
    allowed = set(_TARGET_MATRIX) | _PRESERVE
    existing_roles = frappe.get_all(
        "Custom DocPerm",
        filters={"parent": DOCTYPE, "permlevel": PERMLEVEL},
        pluck="role",
    )
    for role in set(existing_roles):
        if role in allowed:
            continue
        frappe.db.delete(
            "Custom DocPerm",
            {"parent": DOCTYPE, "role": role, "permlevel": PERMLEVEL},
        )
        frappe.logger().info(
            f"[ts_quality_inspection_perms] revoked '{role}' from {DOCTYPE}"
        )
        changed = True

    if changed:
        from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype

        validate_permissions_for_doctype(DOCTYPE)
        frappe.clear_cache(doctype=DOCTYPE)
