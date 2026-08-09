# Copyright (c) 2026, Trustbit Software and contributors
# BBPL Approvals executive PWA (v2.32.0) — push subscription endpoints.
#
# The client owns a browser PushSubscription; these endpoints persist it,
# retire it, and report the per-user state the Settings screen renders.
#
# Ownership rule: `user` is ALWAYS the session user. No endpoint accepts a user
# parameter, so one executive can never register or read another's device.

import hashlib

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cstr

from trustbit_ethanol.ts_gate_entry.ts_webpush import (
    push_capable,
    validate_endpoint,
    vapid_config,
)

DOCTYPE = "TS Push Subscription"
KILL_SWITCH_FIELD = "ts_push_enabled"


def _require_post():
    """L366 — methods=["POST"] is enforced on the TOP-LEVEL cmd only; core's
    bare-whitelist mapper trampolines can otherwise reach these with a GET and
    no CSRF. Request-less contexts (console/jobs/tests) stay allowed."""
    _req = getattr(frappe.local, "request", None)
    if _req is not None and _req.method != "POST":
        raise frappe.PermissionError


def _check_session():
    """Session + app-access gate (v2.34.0).

    Push endpoints are PWA-owned, so they carry the same access boundary as the
    rest of the app: without it any of the 48 enabled System Users could
    register a device and subscribe themselves to executive alerts. Lazy import
    to avoid a cycle — ts_exec_api does not import this module, but keeping the
    import local matches the pattern used elsewhere here.
    """
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    from trustbit_ethanol.ts_gate_entry.ts_exec_api import exec_app_allowed

    if not exec_app_allowed():
        frappe.throw(
            _("BBPL Approvals is limited to approved users."),
            frappe.PermissionError,
        )


def _flag_on_strict(field):
    """Fail-CLOSED, deliberately inverting the Phase-1 house pattern.

    Every other switch in this app fails OPEN because the risk there is
    accidentally hiding a feature. Push has the opposite risk direction:
    accidentally sending lock-screen messages about approvals to two
    executives' personal phones. An unseeded or unreadable flag means OFF.
    """
    try:
        rows = frappe.db.sql(
            """SELECT value FROM `tabSingles`
               WHERE doctype = 'TS Settings' AND field = %s LIMIT 1""",
            (field,),
        )
    except Exception:
        return False
    if not rows or rows[0][0] is None:
        return False
    try:
        return bool(int(rows[0][0]))
    except (TypeError, ValueError):
        return False


def push_enabled():
    return _flag_on_strict(KILL_SWITCH_FIELD)


def dry_run_on():
    """Dry run defaults ON when unset — the OPPOSITE default to the others.

    `_flag_on_strict` treats a missing row as False, which is the safe answer
    for "is the feature on?" but the DANGEROUS one for "is this only a
    rehearsal?". Custom Field defaults are not materialised into `tabSingles`
    until something writes them, so a freshly migrated production site has NO
    row here — and arming with `set_single_value("ts_push_enabled", 1)` would
    then start sending live on the first bell, silently skipping the
    volume-measuring window this flag exists to provide.
    """
    try:
        rows = frappe.db.sql(
            """SELECT value FROM `tabSingles`
               WHERE doctype = 'TS Settings' AND field = %s LIMIT 1""",
            ("ts_push_dry_run",),
        )
    except Exception:
        return True
    if not rows or rows[0][0] is None:
        return True
    try:
        return bool(int(rows[0][0]))
    except (TypeError, ValueError):
        return True


def _vapid_key_id():
    _, public, _ = vapid_config()
    if not public:
        return ""
    return hashlib.sha256(public.encode("utf-8")).hexdigest()[:12]


def _platform_from_ua(ua):
    ua = (ua or "").lower()
    if "android" in ua:
        return "Android"
    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        return "iOS"
    if any(k in ua for k in ("windows", "macintosh", "linux", "cros")):
        return "Desktop"
    return "Other"


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_push_state():
    """State for the Settings screen. NEVER returns endpoint/p256dh/auth."""
    _check_session()
    _, public, _ = vapid_config()
    user = frappe.session.user
    subs = frappe.get_all(
        DOCTYPE,
        filters={"user": user, "status": "Active"},
        fields=["name", "platform", "last_success_at", "vapid_key_id"],
        limit_page_length=10,
        ignore_permissions=True,  # self-scoped by the filter above
    )
    return {
        "enabled": bool(push_enabled()),
        "configured": bool(push_capable()),
        # The VAPID PUBLIC key is public by definition — it travels to Google
        # and Apple in every Authorization header.
        "vapid_public_key": public or "",
        "vapid_key_id": _vapid_key_id(),
        "subscriptions": subs,
        "dry_run": bool(dry_run_on()),
    }


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def subscribe(endpoint, p256dh, auth, user_agent=None):
    """Register (or re-home) this browser's push subscription.

    ⚠ CONFIDENTIALITY: an endpoint identifies a browser's service-worker
    registration, NOT a person. Executive A subscribes, logs out, B logs in on
    the same phone — the browser hands back the SAME endpoint. If this INSERTed
    blindly, B's lock screen would start showing A's approval subjects. So an
    existing hash is REASSIGNED to the session user rather than duplicated.
    """
    _require_post()
    _check_session()
    if not push_enabled():
        frappe.throw(_("Alerts on this phone are switched off by IT."))

    validate_endpoint(endpoint)
    if not p256dh or not auth:
        frappe.throw(_("Subscription is missing its encryption keys."))

    user = frappe.session.user
    endpoint_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
    # frappe.get_request_header dereferences frappe.request unguarded, so it
    # raises AttributeError in request-less contexts (console, jobs, tests).
    # cstr() because a JSON body can deliver a list/dict/int here: slicing or
    # substring-testing those raises, and an exception without an
    # http_status_code becomes a 500, which makes Frappe snapshot a traceback
    # WITH LOCALS — and this is the one function where endpoint, p256dh and
    # auth are all live locals at once. Same leak class as ts_webpush's header.
    ua = cstr(user_agent
              or (getattr(frappe.local, "request", None)
                  and frappe.get_request_header("User-Agent"))
              or "")[:500]

    existing = frappe.db.get_value(DOCTYPE, {"endpoint_hash": endpoint_hash}, "name")
    if existing:
        doc = frappe.get_doc(DOCTYPE, existing)
        doc.user = user  # re-home, never leave it pointing at the old owner
        doc.endpoint = endpoint
        doc.p256dh = p256dh
        doc.auth = auth
        doc.user_agent = ua
        doc.platform = _platform_from_ua(ua)
        # Deliberately NOT re-stamping vapid_key_id here. A browser that does
        # not expose subscription.options.applicationServerKey cannot detect a
        # key rotation client-side; if a re-home refreshed this field, the row
        # would masquerade as current and escape the rotation pruner. Leaving
        # it makes the server-side prune authoritative regardless of what the
        # browser exposes. First insert (below) sets it once.
        doc.status = "Active"
        doc.fail_count = 0
        doc.last_error_code = None
        doc.last_seen_at = frappe.utils.now_datetime()
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({
            "doctype": DOCTYPE,
            "user": user,
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": ua,
            "platform": _platform_from_ua(ua),
            "vapid_key_id": _vapid_key_id(),
            "status": "Active",
            "last_seen_at": frappe.utils.now_datetime(),
        }).insert(ignore_permissions=True)

    return {"ok": 1, "name": doc.name, "platform": doc.platform}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def unsubscribe(endpoint=None):
    """Retire this browser's subscription. Called on sign-out and when the user
    turns alerts off. Only ever deletes rows owned by the session user."""
    _require_post()
    # ⚠ Session-only ON PURPOSE — NOT _check_session(), which now also enforces
    # app access. Revoking someone's app access must not trap their device:
    # this endpoint only ever deletes rows the session user already owns, so
    # requiring the very access they are being denied would leave them unable
    # to stop notifications they never chose to keep. Deleting your own record
    # is not a privilege (security scan #2, v2.34.0).
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    user = frappe.session.user

    if endpoint:
        endpoint_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        names = frappe.get_all(
            DOCTYPE,
            filters={"endpoint_hash": endpoint_hash, "user": user},
            pluck="name",
            ignore_permissions=True,
        )
    else:
        names = frappe.get_all(
            DOCTYPE, filters={"user": user}, pluck="name", ignore_permissions=True)

    for name in names:
        frappe.delete_doc(DOCTYPE, name, ignore_permissions=True, force=True)
    return {"ok": 1, "removed": len(names)}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60)
def test_push():
    """Send a test alert to the session user's own devices."""
    _require_post()
    _check_session()
    if not push_enabled():
        frappe.throw(_("Alerts are switched off by IT."))
    if not push_capable():
        frappe.throw(_("Alerts are not set up on the server yet."))

    from trustbit_ethanol.ts_gate_entry.ts_push_notify import deliver_to_user

    sent = deliver_to_user(
        frappe.session.user,
        {"t": "Test alert", "b": "Alerts are working on this phone.", "u": 0},
    )
    if not sent:
        frappe.throw(_("No device is registered for alerts on this account yet."))
    return {"ok": 1, "sent": sent}
