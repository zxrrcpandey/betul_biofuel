# Copyright (c) 2026, Trustbit Software and contributors
# BBPL Approvals executive PWA — after_migrate seeder (v2.31.0, rebuilt v2.40).
#
# v2.40 (12 Aug 2026, user decision): ALL exec-PWA switches live in the
# INDEPENDENT Singles doctype "TS Exec App Settings" — not in TS Settings.
# This file no longer creates TS Settings Custom Fields; instead it:
#   1. copies any stored TS Settings switch values into the new doctype ONCE
#      (INSERT-ONLY — a deliberate OFF stays OFF, an armed push switch stays
#      exactly as stored, and re-running can never overwrite anything);
#   2. only after ALL nine values are verified present, deletes the old
#      TS Settings Custom Fields + stored rows (with the L234 session-safe
#      cache flush);
#   3. seeds the grantable app-access role (unchanged from v2.34.0).
#
# Fail postures are unchanged and live in the READERS, not here:
# fail-OPEN app/tab switches (ts_exec_api.flag_on) and fail-CLOSED push
# switches (ts_push_api._flag_on_strict / dry_run_on). The new doctype's JSON
# carries NO defaults — L227: a Singles JSON default OVERWRITES the stored
# value on every migrate.

import frappe

APP_SETTINGS = "TS Exec App Settings"

# field → value to seed when NEITHER the new doctype NOR TS Settings has one.
# These are the documented ship postures: app/tab switches ON (fail-open
# family), push OFF + dry-run ON + no amounts (fail-closed family).
_SWITCH_DEFAULTS = {
    "ts_exec_pwa_enabled": "1",
    "ts_exec_pwa_sw_enabled": "1",
    "ts_exec_bell_enabled": "1",
    "ts_exec_login_alert_enabled": "1",
    "ts_exec_today_enabled": "1",
    "ts_exec_usage_enabled": "1",
    "ts_push_enabled": "0",
    "ts_push_dry_run": "1",
    "ts_push_preview_amounts": "0",
}

# Everything this feature ever added to TS Settings, removed once migrated.
_OLD_TS_SETTINGS_FIELDS = tuple(_SWITCH_DEFAULTS) + ("ts_exec_section",)


def after_migrate_exec_pwa():
    """Idempotent — safe to run on every migrate (hooks.py after_migrate)."""
    _migrate_switch_values()
    _seed_exec_app_role()
    _drop_legacy_usage_settings()


def _migrate_switch_values():
    try:
        migrated = 0
        for field, fallback in _SWITCH_DEFAULTS.items():
            exists = frappe.db.sql(
                """SELECT 1 FROM `tabSingles`
                   WHERE doctype = %s AND field = %s LIMIT 1""",
                (APP_SETTINGS, field),
            )
            if exists:
                migrated += 1
                continue  # INSERT-ONLY: never overwrite a stored value
            old = frappe.db.sql(
                """SELECT value FROM `tabSingles`
                   WHERE doctype = 'TS Settings' AND field = %s LIMIT 1""",
                (field,),
            )
            value = old[0][0] if (old and old[0][0] is not None) else fallback
            frappe.db.sql(
                """INSERT INTO `tabSingles` (doctype, field, value)
                   VALUES (%s, %s, %s)""",
                (APP_SETTINGS, field, value),
            )
            migrated += 1
        frappe.db.commit()

        # Cleanup runs ONLY when every value is safely across — if the copy
        # ever fails partway, the old rows stay and the readers that still
        # find nothing fall back to their documented fail postures.
        if migrated == len(_SWITCH_DEFAULTS):
            _drop_old_ts_settings_fields()
    except Exception:
        frappe.clear_messages()  # after_migrate must never break a deploy


def _drop_old_ts_settings_fields():
    """Remove the pre-v2.40 TS Settings Custom Fields + stored rows."""
    dropped = False
    for field in _OLD_TS_SETTINGS_FIELDS:
        cf = "TS Settings-" + field
        if frappe.db.exists("Custom Field", cf):
            frappe.delete_doc(
                "Custom Field", cf, ignore_permissions=True, force=True
            )
            dropped = True
    frappe.db.sql(
        """DELETE FROM `tabSingles`
           WHERE doctype = 'TS Settings' AND field IN %(fields)s""",
        {"fields": _OLD_TS_SETTINGS_FIELDS},
    )
    frappe.db.commit()
    if dropped:
        # L234 — session-preserving meta flush, never bench clear-cache
        frappe.clear_cache(doctype="TS Settings")
        from frappe.cache_manager import clear_global_cache

        clear_global_cache()


def _drop_legacy_usage_settings():
    """The short-lived 'TS Exec Usage Settings' doctype existed only on demo
    for a few hours on 12 Aug 2026 (superseded by the consolidated doctype
    before anything was committed). Harmless no-op everywhere else."""
    try:
        if frappe.db.exists("DocType", "TS Exec Usage Settings"):
            frappe.db.sql(
                "DELETE FROM `tabSingles` WHERE doctype = 'TS Exec Usage Settings'"
            )
            frappe.delete_doc(
                "DocType", "TS Exec Usage Settings",
                ignore_permissions=True, force=True,
            )
            frappe.db.commit()
    except Exception:
        frappe.clear_messages()


def _seed_exec_app_role():
    """Create the grantable app-access role (v2.34.0).

    The gate allows Administrator + CEO + MD implicitly; this role is how the
    business adds anyone else LATER, from the ordinary User form, with no
    deploy and an audit trail in tabHas Role.

    It MUST exist before it can be granted — assigning a non-existent role is a
    silent no-op (L281/L290), which would look like "I added them and it did
    nothing". desk=0 so it never clutters the desk permission UI as a module
    role, and it is deliberately created EMPTY: holding it grants access to
    this app only, never any doctype permission.
    """
    try:
        from trustbit_ethanol.ts_gate_entry.ts_exec_api import EXEC_APP_ROLE

        if frappe.db.exists("Role", EXEC_APP_ROLE):
            return
        doc = frappe.get_doc({
            "doctype": "Role",
            "role_name": EXEC_APP_ROLE,
            "desk_access": 1,   # holders are System Users who also use the desk
            "is_custom": 1,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        # after_migrate must never break a deploy. Without the role the gate
        # still works — it simply falls back to Administrator/CEO/MD only.
        frappe.clear_messages()
