# Copyright (c) 2026, Trustbit Software and contributors
# BBPL Approvals executive PWA (v2.32.0) — push fan-out.
#
# Hooked on Notification Log.after_insert, so EVERY existing bell producer
# (approval hub, escalations, budget, post-dated, coverage) can reach a phone
# without any producer being modified.
#
# Two facts about that hook point that would each be a shipped bug if missed:
#  1. Most bells are created inside the RQ job `make_notification_logs`, so this
#     usually runs in a BACKGROUND WORKER: frappe.local.request is None and
#     frappe.session.user is the ENQUEUING user, not the recipient. Nothing here
#     may depend on either — always use doc.for_user.
#  2. after_insert runs BEFORE commit. A plain frappe.enqueue could start a
#     worker that cannot see the row yet, and a rolled-back insert would still
#     push. enqueue_after_commit=True is mandatory, not a nicety.
#
# The gate is ACTIONABILITY, not an event allowlist: "is this document sitting
# in this user's own approval queue right now?" That reuses the approval
# engine's own vocabulary, so it can never drift from it — and against the real
# data it drops the CEO's ~21 bells/day to a couple of meaningful pushes.

import frappe
from frappe.utils import cint, now_datetime

from trustbit_ethanol.ts_gate_entry.ts_push_api import (
    _flag_on_strict,
    dry_run_on,
    push_enabled,
)

DOCTYPE = "TS Push Subscription"

# Only these can be actionable in the app; everything else is bell-only noise.
_PUSH_DOCTYPES = (
    "Purchase Order",
    "Material Request",
    "TS Budget Override Approval",
    "TS Post Dated Entry Request",
)

_DEDUPE_SECONDS = 900  # 15 min — the same PO must not buzz twice


def _dedupe_key(user, doctype, name):
    return "exec_push:{0}:{1}:{2}".format(user, doctype, name)


def _is_actionable_for(user, doctype, name):
    """FAIL CLOSED. Any doubt ⇒ no push.

    Mirrors ts_exec_api.get_inbox's membership test rather than re-deriving it,
    so the push filter and the app's own inbox can never disagree.
    """
    try:
        from trustbit_ethanol.ts_gate_entry.ts_exec_api import (
            _EXTRA_QUEUES,
            _extra_queue_statuses,
            _status_field,
        )
        from trustbit_ethanol.ts_gate_entry.ts_my_approvals_api import (
            ROLE_STATUS_MAP,
            _get_user_statuses,
        )

        if doctype in ROLE_STATUS_MAP:
            statuses = _get_user_statuses(doctype, user)
        elif doctype in _EXTRA_QUEUES:
            statuses = _extra_queue_statuses(doctype, user)
        else:
            return False

        field = _status_field(doctype)
        current = frappe.db.get_value(doctype, name, field)
        if current and current in statuses:
            return True

        # The CEO-only state where a PO awaits "Send to MD": it is in nobody's
        # status queue, but only the CEO can act on it.
        if doctype == "Purchase Order" and "CEO" in frappe.get_roles(user):
            if cint(frappe.db.get_value(doctype, name, "ts_can_send_to_md")):
                return True
        return False
    except Exception:
        return False


def _payload_for(doc):
    """Lock-screen content.

    Document type + name ONLY by default. Real subjects carry money — this
    system handles ₹4.64 Cr POs and phones get handed to drivers and visitors —
    and the codebase already made this call once (ts_notification_coverage:
    "Subject is name-only — NO amount/supplier"). Supplier and cost centre are
    never included and have no switch: grain-PO confidentiality is a whole
    feature of this app, and a switch that COULD put a counterparty on a lock
    screen is a leak waiting to be flipped.
    """
    title = "Approval needed"
    body = "{0} {1}".format(doc.document_type or "", doc.document_name or "").strip()

    if _flag_on_strict("ts_push_preview_amounts"):
        amount = None
        if doc.document_type == "Purchase Order":
            amount = frappe.db.get_value("Purchase Order", doc.document_name, "grand_total")
        elif doc.document_type == "TS Budget Override Approval":
            amount = frappe.db.get_value(
                "TS Budget Override Approval", doc.document_name, "source_amount")
        if amount:
            # Compact form only: shorter for a truncating lock screen AND an
            # order of magnitude less precise — a privacy dividend at no cost.
            body = "{0} · {1}".format(body, _compact_inr(amount))

    return {
        "t": title,
        "b": body,
        "dt": doc.document_type or "",
        "dn": doc.document_name or "",
        "nl": doc.name,
    }


def _compact_inr(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if abs(n) >= 1e7:
        return "₹{0:.2f} Cr".format(n / 1e7)
    if abs(n) >= 1e5:
        return "₹{0:.2f} L".format(n / 1e5)
    return "₹{0:,.0f}".format(n)


def deliver_to_user(user, payload):
    """Send `payload` to every Active subscription of `user`. Returns the count
    delivered. Prunes dead subscriptions. Never raises."""
    from trustbit_ethanol.ts_gate_entry import ts_webpush

    subs = frappe.get_all(
        DOCTYPE,
        filters={"user": user, "status": "Active"},
        # fail_count MUST be selected: without it sub.get("fail_count") is None
        # on every row, the increment below always lands on 1, and the
        # prune_dead_subscriptions ">= 10" branch becomes unreachable — a
        # permanently-failing endpoint would be retried forever.
        fields=["name", "endpoint", "p256dh", "auth", "fail_count"],
        limit_page_length=10,
        ignore_permissions=True,
    )
    sent = 0
    for sub in subs:
        # The bookkeeping below is INSIDE this try on purpose. `sub` holds the
        # endpoint, p256dh and auth of up to 10 devices; if a db.set_value or
        # delete_doc raised out of here, test_push() (the one caller that does
        # not wrap this) would return 500 and Frappe would snapshot a traceback
        # WITH LOCALS — the same leak class fixed in ts_webpush, one frame out.
        try:
            status, _reason = ts_webpush.send(sub.endpoint, sub.p256dh, sub.auth, payload)

            if status in (200, 201, 202):
                sent += 1
                frappe.db.set_value(DOCTYPE, sub.name, {
                    "last_success_at": now_datetime(), "fail_count": 0,
                    "last_error_code": None,
                }, update_modified=False)
            elif status in (404, 410):
                # The subscription is gone for good — delete, do not retry forever.
                frappe.delete_doc(DOCTYPE, sub.name, ignore_permissions=True, force=True)
            elif status in (401, 403):
                frappe.db.set_value(DOCTYPE, sub.name, {
                    "status": "Revoked", "last_error_at": now_datetime(),
                    "last_error_code": str(status),
                }, update_modified=False)
            else:
                frappe.db.set_value(DOCTYPE, sub.name, {
                    "last_error_at": now_datetime(), "last_error_code": str(status),
                    "fail_count": cint(sub.get("fail_count")) + 1,
                }, update_modified=False)
        except Exception:
            # Includes a stored row that no longer passes the SSRF gate, and any
            # DB fault during the bookkeeping above. Never re-raise.
            try:
                # Increment here too, else a row that fails by EXCEPTION
                # (connect timeout, or a stored endpoint that no longer passes
                # the SSRF allowlist) never reaches the prune threshold and is
                # retried forever — the sibling of the fail_count defect.
                frappe.db.set_value(DOCTYPE, sub.name, {
                    "last_error_at": now_datetime(),
                    "last_error_code": "send-error",
                    "fail_count": cint(sub.get("fail_count")) + 1,
                }, update_modified=False)
            except Exception:
                frappe.clear_messages()
            continue
    return sent


def _deliver(user, payload, log_name=None):
    """Enqueued worker entry point. Fail-soft by contract."""
    try:
        deliver_to_user(user, payload)
    except Exception:
        frappe.clear_messages()


def on_notification_log_insert(doc, method=None):
    """doc_events hook. MUST never raise — a push problem must not break the
    approval flow that produced the bell (Lessons 238/276)."""
    try:
        # G0 — never during migrate/install/patch
        if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
            return
        # G1 — O(1), no DB
        if not push_enabled():
            return
        from trustbit_ethanol.ts_gate_entry.ts_webpush import push_capable
        if not push_capable():
            return
        # G2 — doctype allowlist (this alone kills every Email Queue row)
        if (doc.document_type or "") not in _PUSH_DOCTYPES:
            return
        user = doc.for_user
        if not user or user in ("Administrator", "Guest"):
            return
        # G2.5 — app access (v2.34.0). The endpoint gate stops a denied user
        # SUBSCRIBING, but it does not retire a device subscribed BEFORE the
        # gate existed — that phone would keep buzzing with approval subjects
        # on its lock screen. Delivery is the app's last data-egress path and
        # needs the same boundary. exec_app_allowed() takes an explicit user
        # and is fail-closed, so a lookup failure silently declines to send.
        from trustbit_ethanol.ts_gate_entry.ts_exec_api import exec_app_allowed

        if not exec_app_allowed(user):
            return
        # G3 — does this user have a device at all?
        if not frappe.db.exists(DOCTYPE, {"user": user, "status": "Active"}):
            return
        # G4 — actionability, fail-closed
        if not _is_actionable_for(user, doc.document_type, doc.document_name):
            return
        # G5 — can this user even read the document? fail-closed
        try:
            if not frappe.has_permission(doc.document_type, "read",
                                         doc=doc.document_name, user=user):
                return
        except Exception:
            return
        # G6 — dedupe: the same document must not buzz twice in 15 minutes.
        # ⚠ set_value(..., expires_in_sec=...) writes ONLY to redis, never to
        # frappe.local.cache, while get_value checks the local cache FIRST and
        # has already cached the miss. Within one process the second read would
        # therefore see the stale None and buzz again — and bells are batched
        # inside a single RQ job (make_notification_logs), so that is the
        # common case, not the rare one. Mirror the value into the local cache.
        key = _dedupe_key(user, doc.document_type, doc.document_name)
        try:
            cache = frappe.cache()
            if cache.get_value(key):
                return
            cache.set_value(key, 1, expires_in_sec=_DEDUPE_SECONDS)
            try:
                frappe.local.cache[cache.make_key(key)] = 1
            except Exception:
                pass  # redis still holds it; cross-process dedupe unaffected
        except Exception:
            pass

        payload = _payload_for(doc)

        # G7 — dry run: log what would have been sent, send nothing.
        # Defaults ON when unset (see dry_run_on) so a fresh site rehearses.
        if dry_run_on():
            frappe.log_error(
                title="exec push dry-run",
                message="user={0} doctype={1} name={2} payload={3}".format(
                    user, doc.document_type, doc.document_name, payload),
            )
            return

        # after_insert runs BEFORE commit — enqueue_after_commit is mandatory.
        frappe.enqueue(
            "trustbit_ethanol.ts_gate_entry.ts_push_notify._deliver",
            queue="short",
            enqueue_after_commit=True,
            user=user,
            payload=payload,
            log_name=doc.name,
        )
    except Exception:
        try:
            frappe.clear_messages()
        except Exception:
            pass


def prune_dead_subscriptions():
    """Daily scheduler: retire subscriptions that have failed repeatedly or were
    minted under a VAPID key we no longer use."""
    try:
        from trustbit_ethanol.ts_gate_entry.ts_push_api import _vapid_key_id
        current = _vapid_key_id()
        rows = frappe.get_all(
            DOCTYPE, fields=["name", "fail_count", "vapid_key_id"],
            limit_page_length=0, ignore_permissions=True)
        for r in rows:
            if cint(r.fail_count) >= 10 or (current and r.vapid_key_id and
                                            r.vapid_key_id != current):
                frappe.delete_doc(DOCTYPE, r.name, ignore_permissions=True, force=True)
    except Exception:
        frappe.clear_messages()
