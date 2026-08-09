# Copyright (c) 2026, Trustbit Software and contributors
# BBPL Approvals executive PWA (v2.32.0) — in-app alerts bell.
#
# A THIN WRAPPER over notification_center_api, not a second implementation:
# the self-scoping (for_user = session user), the confidentiality posture and
# the accountability-trail stamping all stay in the one audited module. This
# file adds exactly three things:
#   1. an exec-specific kill switch that can turn the bell off on its own
#      (the desk Center's switch remains a master switch over both — see
#      _bell_on)
#   2. a noise exclusion (Email Queue rows — 77/30d for the CEO alone) applied
#      SERVER-SIDE so the list and the badge can never disagree
#   3. exec-specific trail via-labels, so PWA reads never masquerade as
#      evidence that the desk Center delivered something
#
# Naming: the app calls these "Alerts", deliberately never "Notifications" —
# "Approvals" (Inbox tab) is what needs a decision; "Alerts" is what happened.

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from trustbit_ethanol.ts_gate_entry import notification_center_api as nc
from trustbit_ethanol.ts_gate_entry.ts_exec_api import exec_app_allowed, flag_on

KILL_SWITCH_FIELD = "ts_exec_bell_enabled"

# Document types an executive never needs alerting about in the app. Email
# Queue rows are delivery plumbing (a failed send is IT's problem, not the
# MD's) and are the single largest noise source in the real data.
_EXCLUDE_TYPES = ("Email Queue",)

_DAYS = "30"
_PAGE = 20


def _bell_on():
    """Both switches must be ON for the bell to work.

    `ts_exec_bell_enabled` is fail-OPEN (house pattern — an unseeded field must
    not silently hide the feature) and turns the bell off on its own. But every
    read/mutation below delegates into notification_center_api, whose _guard()
    also enforces `ts_notification_center_enabled` — so the desk switch is a de
    facto master switch for the bell too. The badge honours BOTH, otherwise a
    disabled Center would leave a live unread count that errors on every tap."""
    return flag_on(KILL_SWITCH_FIELD) and nc._enabled()


def _guard():
    """⚠ This is a LOCAL guard — it is NOT ts_exec_api._guard(). The two were
    written to look alike, and that cost a real hole: when the v2.34.0 app-access
    gate was added to ts_exec_api._guard(), get_alerts / mark_alert_read /
    mark_all_alerts_read silently kept serving denied users, because they call
    THIS one. Any gate added there must be mirrored here (or these should call
    through) — verified by testing every endpoint per user, not by reading."""
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    if not flag_on(KILL_SWITCH_FIELD):
        frappe.throw(_("Alerts are switched off."))
    if not exec_app_allowed():
        frappe.throw(
            _("BBPL Approvals is limited to approved users."),
            frappe.PermissionError,
        )


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=120, seconds=60)
def get_alerts(start=0, page_length=_PAGE):
    """Alerts list for the bell sheet. Rows are stamped 'Exec List' in the
    accountability trail. POST-only: the response IS delivered-to-UI evidence
    and evidence must never be writable via a CSRF-exempt GET (L175)."""
    _guard()
    return nc.get_notifications(
        category="all",
        days=_DAYS,
        start=start,
        page_length=page_length,
        exclude_types=list(_EXCLUDE_TYPES),
        shown_via="Exec List",
    )


@frappe.whitelist()
@rate_limit(limit=120, seconds=60)
def alert_count():
    """Badge count. Uses the SAME exclusion as get_alerts — a client-side
    filter here would guarantee the badge and the list disagree."""
    if not frappe.session.user or frappe.session.user == "Guest":
        return 0
    # v2.34.0 app-access gate. Returns 0 rather than throwing: this is a badge
    # poller, and a 403 storm from a non-approved user's stale tab would be
    # noise, not protection. The alert LIST endpoints go through _guard() and
    # do throw — no count is disclosed here that the list would not.
    if not exec_app_allowed():
        return 0
    if not _bell_on():
        return 0
    return nc._unread_total(frappe.session.user, list(_EXCLUDE_TYPES))


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=120, seconds=60)
def mark_alert_read(log_name):
    """Mark one alert read. Ownership is enforced inside mark_read (it throws
    PermissionError when for_user != session user), so no row belonging to
    another executive can be touched from here."""
    _guard()
    return nc.mark_read(
        log_name,
        read_via="Exec Click",
        exclude_types=list(_EXCLUDE_TYPES),
    )


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def mark_all_alerts_read():
    """Mark every alert in the current 30-day window read.

    Delegates to the shared mark_all_read, which is self-scoped. Note it marks
    the user's unread rows in the window WITHOUT the exec exclusion — that is
    deliberate and correct: 'clear my alerts' should not leave invisible
    Email Queue rows behind inflating the desk badge."""
    _guard()
    return nc.mark_all_read(category="all", days=_DAYS, read_via="Exec Mark All")
