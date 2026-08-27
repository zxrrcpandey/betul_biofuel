"""Activity trail — read-only access for CEO / MD (BBF request, 22 Aug 2026).

Frappe ships Version / Activity Log / Comment / View Log / Access Log as
System-Manager-only. The form timeline still renders change entries to any
reader, but the moment a CEO/MD who is NOT also a System Manager clicks one
(opens /app/version/<name>) or browses the log lists, they get a
PermissionError. This seeder grants the two executive roles a pure VIEW tier
(read / report / export / print / email) on those five doctypes — never write,
create, delete or share — so they can inspect who did what on every document.

Same declarative UPSERT pattern as ts_stock_recon_perms.py (Lesson 292):
re-asserted on every migrate, never drops Standard DocPerm (Lesson 169), skips
roles missing on the target site. Row-level confidentiality is unaffected:
CEO/MD are already in ts_confidential_po._ALLOW_ROLES.
"""

import frappe
from frappe.permissions import add_permission, setup_custom_perms, update_permission_property

TRAIL_DOCTYPES = ("Version", "Activity Log", "Comment", "View Log", "Access Log")
ROLES = ("CEO", "MD")
PERMLEVEL = 0

_VIEW_ONLY = {
    "read": 1, "report": 1, "export": 1, "print": 1, "email": 1,
    "write": 0, "create": 0, "delete": 0, "submit": 0, "cancel": 0, "amend": 0, "share": 0,
}


def _row_filter(doctype, role):
    return {"parent": doctype, "role": role, "permlevel": PERMLEVEL, "if_owner": 0}


def seed_activity_trail_permissions():
    """after_migrate — CEO/MD read-only Custom DocPerm on the audit-trail doctypes."""
    from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype

    roles = [r for r in ROLES if frappe.db.exists("Role", r)]
    for doctype in TRAIL_DOCTYPES:
        try:
            _seed_doctype(doctype, roles, validate_permissions_for_doctype)
        except Exception:
            # Never abort a prod migrate over one doctype's drifted legacy perm rows.
            frappe.log_error(title=f"Activity trail perms: {doctype}", message=frappe.get_traceback())


def _seed_doctype(doctype, roles, validate_permissions_for_doctype):
    changed = False
    # Standard rows carry a legacy `import=1` on both sites that the shipped
    # JSON does not have. validate_permissions_for_doctype() — run by
    # add_permission() — rejects it twice over (import without create; import
    # on a non-importable doctype), so even Permission Manager throws on these
    # doctypes. The flag is inert here; clear it on standard + copied rows.
    setup_custom_perms(doctype)
    for table in ("tabDocPerm", "tabCustom DocPerm"):
        frappe.db.sql(f"update `{table}` set `import`=0 where parent=%s and `import`=1", doctype)
    for role in roles:
        if not frappe.db.exists("Custom DocPerm", _row_filter(doctype, role)):
            add_permission(doctype, role, PERMLEVEL)  # runs setup_custom_perms() first (L169)
            changed = True
        for ptype, value in _VIEW_ONLY.items():
            current = frappe.db.get_value("Custom DocPerm", _row_filter(doctype, role), ptype)
            if int(current or 0) != value:
                update_permission_property(doctype, role, PERMLEVEL, ptype, value, validate=False)
                changed = True
    if changed:
        validate_permissions_for_doctype(doctype)
    frappe.clear_cache(doctype=doctype)
