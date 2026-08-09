# Copyright (c) 2026, Trustbit Software and contributors
# BBPL Approvals executive PWA (v2.31.0) — after_migrate seeder.
#
# Seeds the three kill-switch Custom Fields on TS Settings. Deliberately
# Custom Fields, NOT DocFields in ts_settings.json: that file carries another
# session's uncommitted WIP, and a Singles JSON `default` OVERWRITES the
# stored value on every migrate (L227) — an admin's deliberate "off" would
# silently flip back on. create_custom_fields(update=True) is idempotent and
# self-healing without ever touching stored tabSingles values.
#
# Readers are fail-OPEN (ts_exec_api.flag_on, L171/L172): a missing row means
# enabled; only an explicit stored 0 disables.
#
# ⚠ PERMLEVEL 9 = ADMINISTRATOR ONLY, and it is deliberate (user decision,
# 9 Aug 2026). No role holds permlevel 9 on TS Settings, and Administrator
# bypasses field-level permission checks, so these seven switches are visible
# and editable to Administrator alone — not IT Head (14 holders), not System
# Manager (12), both of whom DO hold permlevel 1 where these fields used to
# live. That is exactly how the app was accidentally switched off: a routine
# Save on the TS Settings form by a permlevel-1 holder zeroed every unset
# Check. At permlevel 9 those users' saves simply drop these fields, so the
# stored values survive untouched.
# NOTE: the other ~43 permlevel-1 fields on TS Settings belong to other
# features and are deliberately left alone.
# NOTE: every server-side reader (flag_on / _flag_on_strict / dry_run_on)
# queries tabSingles with raw SQL, which is not permlevel-gated — so the app
# keeps working for ALL users regardless of who can see the switches.

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_migrate_exec_pwa():
    """Idempotent — safe to run on every migrate (hooks.py after_migrate)."""
    try:
        create_custom_fields(
            {
                "TS Settings": [
                    dict(
                        fieldname="ts_exec_section",
                        fieldtype="Section Break",
                        label="Executive PWA (BBPL Approvals)",
                        collapsible=1,
                    ),
                    dict(
                        fieldname="ts_exec_pwa_enabled",
                        fieldtype="Check",
                        label="Enable Executive PWA (/exec)",
                        default="1",
                        insert_after="ts_exec_section",
                        permlevel=9,
                        description=(
                            "Master switch for the BBPL Approvals app at /exec. "
                            "OFF = API returns disabled and the shell shows a "
                            "maintenance notice. Fail-open: unset counts as ON."
                        ),
                    ),
                    dict(
                        fieldname="ts_exec_pwa_sw_enabled",
                        fieldtype="Check",
                        label="Enable Executive PWA Service Worker",
                        default="1",
                        insert_after="ts_exec_pwa_enabled",
                        permlevel=9,
                        description=(
                            "OFF = installed phones unregister their service "
                            "worker on next launch (emergency cache disarm, no "
                            "deploy needed). Fail-open: unset counts as ON."
                        ),
                    ),
                    dict(
                        fieldname="ts_exec_bell_enabled",
                        fieldtype="Check",
                        label="Enable Executive PWA Alerts Bell",
                        default="1",
                        insert_after="ts_exec_pwa_sw_enabled",
                        permlevel=9,
                        description=(
                            "In-app alerts bell in the BBPL Approvals app. "
                            "Deliberately independent of the desk Notification "
                            "Center switch. Fail-open: unset counts as ON."
                        ),
                    ),
                    # v2.32.0 push switches. ts_push_enabled + ts_push_dry_run
                    # + ts_push_preview_amounts are read FAIL-CLOSED by
                    # ts_push_api._flag_on_strict (deliberately inverting the
                    # fail-open house pattern — the risk here is accidentally
                    # putting approval messages on personal lock screens, not
                    # accidentally hiding a feature). Defaults are therefore
                    # OFF / dry-run / no-amounts: push must be armed manually.
                    dict(
                        fieldname="ts_push_enabled",
                        fieldtype="Check",
                        label="Enable Lock-Screen Alerts (Web Push)",
                        default="0",
                        insert_after="ts_exec_bell_enabled",
                        permlevel=9,
                        description=(
                            "Master switch for push notifications to installed "
                            "phones. Fail-CLOSED: unset counts as OFF. Requires "
                            "VAPID keys in site_config.json."
                        ),
                    ),
                    dict(
                        fieldname="ts_push_dry_run",
                        fieldtype="Check",
                        label="Push Dry Run (log only, send nothing)",
                        default="1",
                        insert_after="ts_push_enabled",
                        permlevel=9,
                        description=(
                            "ON = the gate runs and logs what it WOULD send "
                            "without sending. Use for a volume-measuring window "
                            "before arming. Fail-CLOSED: unset counts as ON."
                        ),
                    ),
                    dict(
                        fieldname="ts_push_preview_amounts",
                        fieldtype="Check",
                        label="Show Amount on Lock Screen",
                        default="0",
                        insert_after="ts_push_dry_run",
                        permlevel=9,
                        description=(
                            "OFF (default) = lock screen shows document type + "
                            "number only. ON adds a rounded figure (₹4.64 Cr). "
                            "Supplier and cost centre are NEVER included."
                        ),
                    ),
                    dict(
                        fieldname="ts_exec_today_enabled",
                        fieldtype="Check",
                        label="Enable Executive Overview Tab",
                        default="1",
                        insert_after="ts_push_preview_amounts",
                        permlevel=9,
                        description=(
                            "The company-wide Overview tab in the BBPL "
                            "Approvals app. OFF = the tab disappears from every "
                            "phone on next launch and the endpoint refuses, "
                            "WITHOUT taking the approvals inbox down with it. "
                            "Fail-open: unset counts as ON."
                        ),
                    ),
                    dict(
                        fieldname="ts_exec_login_alert_enabled",
                        fieldtype="Check",
                        label="Bell Alert on CEO/MD Sign-in",
                        default="1",
                        insert_after="ts_exec_today_enabled",
                        permlevel=9,
                        description=(
                            "In-app bell to the executive's own account on every "
                            "new sign-in (any client, not just the PWA). "
                            "Fail-open: unset counts as ON."
                        ),
                    ),
                ]
            },
            update=True,
        )
    except Exception:
        # after_migrate must never break a deploy — the readers fail open, so
        # missing fields only mean the switches cannot be turned OFF yet.
        frappe.clear_messages()

    _seed_push_switch_rows()
    _seed_exec_app_role()


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


def _seed_push_switch_rows():
    """Materialise every exec-PWA switch into tabSingles with explicit values.

    L350 class, and it BIT US LIVE on 9 Aug: a Custom Field `default` is never
    written to tabSingles — the row only appears when something saves it. So
    the fail-OPEN switches (ts_exec_*) sat with no row at all, working purely
    on "missing ⇒ enabled". The moment anyone opened TS Settings in the desk
    and pressed Save, Frappe coerced those unset Checks to an explicit **0**
    (base_document Check coercion) and the whole app went to its maintenance
    screen with nobody having chosen to disable it.

    The same mechanism in the other direction is why the push switches are
    here: an unset ts_push_dry_run reads as "rehearse", but a form save would
    store 0 and silently arm live sending.

    Writing the intended values once removes the ambiguity in both directions.
    INSERT-ONLY: an existing row is never touched, so an operator's deliberate
    choice (and a later arming) always survives a migrate.
    """
    wanted = {
        # Fail-OPEN app switches — seeded ON so a form save cannot zero them.
        "ts_exec_pwa_enabled": "1",
        "ts_exec_pwa_sw_enabled": "1",
        "ts_exec_bell_enabled": "1",
        "ts_exec_today_enabled": "1",
        "ts_exec_login_alert_enabled": "1",
        # Fail-CLOSED push switches — seeded to the safe posture.
        "ts_push_enabled": "0",       # must be armed deliberately
        "ts_push_dry_run": "1",       # rehearse until a human says otherwise
        "ts_push_preview_amounts": "0",  # no rupee amounts on lock screens
    }
    for field, value in wanted.items():
        try:
            exists = frappe.db.sql(
                """SELECT 1 FROM `tabSingles`
                   WHERE doctype = 'TS Settings' AND field = %s LIMIT 1""",
                (field,),
            )
            if exists:
                continue
            frappe.db.sql(
                """INSERT INTO `tabSingles` (doctype, field, value)
                   VALUES ('TS Settings', %s, %s)""",
                (field, value),
            )
        except Exception:
            frappe.clear_messages()
