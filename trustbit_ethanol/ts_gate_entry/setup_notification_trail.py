# Copyright (c) 2026, Trustbit Software and contributors
"""after_migrate seeder — Notification Read Accountability Trail (v2.29.0).

Mirrors setup_confidential_po.py: idempotent, declarative,
`create_custom_fields` (insert-or-update ⇒ attributes SELF-HEAL every
migrate), clears the doctype cache. The TS Settings kill-switch field is
insert-ONLY (clone of notification_center_api._seed_settings_field) so an
admin who unticks it is never overwritten on migrate (L227 class of bug).
Report roles are applied via ORM (L281/290 — migrate does NOT sync a
Report's `roles` child), filtered to roles that exist on this server.

The `creation` timestamp of the ts_read_at Custom Field doubles as the
TRAIL-START instant for the report's "Pre-trail — no data possible" status
(plan v2 §2.1 / rebuttal R-2) — create_custom_fields(update=True) updates
rather than recreates, so it is stable per server.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from trustbit_ethanol.ts_gate_entry.ts_notification_trail import KILL_SWITCH_FIELD

REPORT_NAME = "TS Notification Read Audit"
REPORT_ROLES = ("CEO", "MD", "System Manager", "IT Head")  # decision D-1

_SHOWN_HONESTY = (
    "Server-written evidence: when this notification was first returned to "
    "its owner's Notification Center list or push toast. Does NOT prove the "
    "user looked at the screen. Not user-editable."
)

_NL_FIELDS = {
    "Notification Log": [
        {
            "fieldname": "ts_first_shown_at",
            "label": "First Delivered to UI",
            "fieldtype": "Datetime",
            "insert_after": "read",
            "read_only": 1,
            "no_copy": 1,
            "description": _SHOWN_HONESTY,
        },
        {
            "fieldname": "ts_first_shown_via",
            "label": "Delivery Channel",
            "fieldtype": "Select",
            # leading blank keeps pre-trail NULL rows valid; "Bell Dropdown"
            # is RESERVED for the deferred opt-in bell beacon (decision D-3)
            "options": "\nCenter List\nToast Push\nBell Dropdown",
            "insert_after": "ts_first_shown_at",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": "ts_read_at",
            "label": "Marked Read At",
            "fieldtype": "Datetime",
            "insert_after": "ts_first_shown_via",
            "read_only": 1,
            "no_copy": 1,
            "description": (
                "Server-written evidence: when a read action was recorded "
                "from the owner's own session. Not user-editable."
            ),
        },
        {
            "fieldname": "ts_read_via",
            "label": "Marked Read Via",
            "fieldtype": "Select",
            "options": "\nCenter Click\nCenter Mark All\nBell Click\nBell Mark All",
            "insert_after": "ts_read_at",
            "read_only": 1,
            "no_copy": 1,
        },
    ]
}


def seed_notification_trail_fields():
    create_custom_fields(_NL_FIELDS, ignore_validate=True)
    frappe.clear_cache(doctype="Notification Log")


def _seed_settings_field():
    """Trail kill-switch on TS Settings — insert-only + fail-open getter
    (ts_notification_trail._trail_enabled). default '1' keeps the form
    checkbox pre-checked so an admin Save writes 1, not a silent 0."""
    meta = frappe.get_meta("TS Settings")
    if meta.has_field(KILL_SWITCH_FIELD):
        return
    create_custom_fields({"TS Settings": [{
        "fieldname": KILL_SWITCH_FIELD,
        "label": "Enable Notification Read Trail",
        "fieldtype": "Check",
        "default": "1",
        "permlevel": 1,
        "insert_after": "ts_notification_center_enabled",
        "description": ("Instant off-switch for notification read/delivery "
                        "timestamping (fail-open). Display and read actions "
                        "keep working either way."),
    }]}, ignore_validate=True)
    frappe.clear_cache(doctype="TS Settings")


def seed_notification_audit_report_roles():
    """Report roles via ORM (L281/290), filtered to roles that exist on this
    server (cross-server role gap). Skips silently until the Report row has
    been synced by migrate."""
    if not frappe.db.exists("Report", REPORT_NAME):
        return
    doc = frappe.get_doc("Report", REPORT_NAME)
    existing = {r.role for r in (doc.roles or [])}
    added = False
    for role in REPORT_ROLES:
        if role in existing or not frappe.db.exists("Role", role):
            continue
        doc.append("roles", {"role": role})
        added = True
    if added:
        doc.flags.ignore_permissions = True
        doc.save()


def after_migrate_notification_trail():
    for fn in (seed_notification_trail_fields, _seed_settings_field,
               seed_notification_audit_report_roles):
        try:
            fn()
        except Exception:
            frappe.log_error(
                title="notification trail seeder: {0}".format(fn.__name__),
                message=frappe.get_traceback()[:2000])
