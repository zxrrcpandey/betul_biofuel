# Copyright (c) 2026, Trustbit Software and contributors
# BBPL Approvals executive PWA (v2.31.0) — sign-in alert for CEO/MD accounts.
#
# on_session_creation hook. frappe.auth.run_trigger has NO exception handling
# of its own, so an uncaught error here would be a SITE-WIDE login outage —
# the ENTIRE body is wrapped, and this module must stay dependency-light.
#
# Channel is the in-app bell only (Notification Log via _coverage_bell).
# WhatsApp is deliberately NOT wired: an unknown event_key writes a "Skipped"
# TS WhatsApp Log row, so it would ship silently inert until the provider
# approves a new template — add it as a follow-up once the template exists.
#
# Role-driven, not a hardcoded account list (L221): any holder of CEO or MD
# gets the alert on their own account.

import frappe


def on_session_creation_login_alert(login_manager=None):
    try:
        user = frappe.session.user
        if not user or user in ("Guest", "Administrator"):
            return
        roles = set(frappe.get_roles(user))
        if not ({"CEO", "MD"} & roles):
            return

        from trustbit_ethanol.ts_gate_entry.ts_exec_api import flag_on

        if not flag_on("ts_exec_login_alert_enabled"):
            return

        from frappe.utils import format_datetime, now_datetime

        ip = getattr(frappe.local, "request_ip", None) or "unknown IP"
        when = format_datetime(now_datetime())

        from trustbit_ethanol.ts_gate_entry.ts_notification_coverage import (
            _coverage_bell,
        )

        _coverage_bell(
            [user],
            "New sign-in to your account ({0})".format(ip),
            "User",
            user,
            body=(
                "Your account signed in at {0} from {1}. If this was not "
                "you, contact IT immediately so the session can be revoked."
            ).format(when, ip),
        )
    except Exception:
        try:
            frappe.clear_messages()
        except Exception:
            pass
