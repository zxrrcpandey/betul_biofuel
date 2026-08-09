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
                        permlevel=1,
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
                        permlevel=1,
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
                        permlevel=1,
                        description=(
                            "In-app alerts bell in the BBPL Approvals app. "
                            "Deliberately independent of the desk Notification "
                            "Center switch. Fail-open: unset counts as ON."
                        ),
                    ),
                    dict(
                        fieldname="ts_exec_login_alert_enabled",
                        fieldtype="Check",
                        label="Bell Alert on CEO/MD Sign-in",
                        default="1",
                        insert_after="ts_exec_bell_enabled",
                        permlevel=1,
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
