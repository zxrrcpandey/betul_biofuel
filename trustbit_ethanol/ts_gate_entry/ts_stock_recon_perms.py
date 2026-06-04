"""Stock Reconciliation — lock 'doing' to the IT Head role (BBF policy, Jun 2026).

Only **IT Head** may create / write / submit / cancel / delete / amend a Stock
Reconciliation. **Stock Manager** keeps full *view* access (read / report / export
/ print / email / share) but loses every mutating permission. **CEO / MD** are
unchanged — they already sit in the read-only view tier.

Kept in this separate module (NOT setup.py, which is locked under MR_FULL/PO_FULL)
— same pattern as ts_se_header_dimensions.py / the v2.16.4 vehicle_number seeder.

The seeder is fully DECLARATIVE + IDEMPOTENT: it re-asserts the exact Custom
DocPerm matrix below on every migrate, self-healing any drift. Custom DocPerm
already exists for this doctype on demo + prod, so we edit *within* an existing
custom matrix (we never drop Standard DocPerm — Lesson 169). Note the app's
generic seed_custom_docperm() is INSERT-ONLY and would silently no-op the Stock
Manager downgrade; this seeder UPSERTs each permission flag instead.
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

DOCTYPE = "Stock Reconciliation"
PERMLEVEL = 0

# Full doer — the sole role allowed to act on a Stock Reconciliation.
_FULL_DOER = {
    "read": 1, "write": 1, "create": 1, "delete": 1, "submit": 1, "cancel": 1,
    "amend": 1, "report": 1, "export": 1, "print": 1, "email": 1, "share": 1,
}
# Read-only view tier — mirrors the existing CEO/MD row convention exactly.
_READ_ONLY = {
    "read": 1, "write": 0, "create": 0, "delete": 0, "submit": 0, "cancel": 0,
    "amend": 0, "report": 1, "export": 1, "print": 1, "email": 1, "share": 1,
}

# Role -> target Custom DocPerm row at permlevel 0. Re-asserted every migrate.
_TARGET_MATRIX = {
    "IT Head": _FULL_DOER,
    "Stock Manager": _READ_ONLY,
    "CEO": _READ_ONLY,
    "MD": _READ_ONLY,
}


def _row_filter(role):
    return {"parent": DOCTYPE, "role": role, "permlevel": PERMLEVEL, "if_owner": 0}


def seed_stock_recon_permissions():
    """after_migrate — enforce the Stock Reconciliation Custom DocPerm matrix.

    Idempotent: creates a missing Custom DocPerm row, then UPSERTs every
    permission flag to its exact target (read first, so no transient invalid
    state). Skips roles that don't exist on this site (cross-server role gap).
    """
    changed = False
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

    if changed:
        from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype

        validate_permissions_for_doctype(DOCTYPE)
        frappe.clear_cache(doctype=DOCTYPE)
