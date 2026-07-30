# Copyright (c) 2026, Trustbit Software and contributors
# Notification Read Accountability Trail (v2.29.0)
#
# Stamps WHEN a notification was first delivered to its owner's UI and WHEN
# (and how) it was marked read — server-written, first-write-wins, tamper-
# resistant. Answers "did user X ever get / open notification Y" for the
# role-gated TS Notification Read Audit report.
#
# Design contract (plan v2, user-approved 30 Jul 2026):
#   - A stamp failure must NEVER break delivery, display, or the read flip:
#     every stamp is a separate fail-soft statement (L238/276), never folded
#     into a business UPDATE.
#   - `modified` is NEVER touched: it drives BOTH the core bell ordering
#     (get_notification_logs ORDER BY modified desc) AND the 180-day
#     clear_old_logs cutoff — a stamp must be retention-neutral.
#   - Evidence is written ONLY on CSRF-protected requests (POST family) or
#     from server context (console / scheduler / tests, frappe.request None).
#     A forged top-level GET navigation must never fabricate evidence.
#   - Stamps only ever land on the session user's OWN rows
#     (AND for_user = %(user)s) — even through core mark_as_read, which has
#     no ownership check of its own.
#   - First-write-wins (AND <field> IS NULL): a stamp cannot be moved later.

import frappe
from frappe import _
from frappe.utils import now_datetime

KILL_SWITCH_FIELD = "ts_notification_trail_enabled"
_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")  # frappe/auth.py CSRF-checked verbs

# Only these column pairs may ever be interpolated into _stamp's SQL — the
# .format() below is safe because the identifiers come from this closed set,
# never from a request.
_STAMP_FIELDS = {
    ("ts_first_shown_at", "ts_first_shown_via"),
    ("ts_read_at", "ts_read_via"),
}


def _trail_enabled():
    """Kill-switch — fail-OPEN, raw tabSingles (clone of
    notification_center_api._enabled; L172: get_single_value casts a missing
    Check to 0, which would silently kill the trail). Missing row = ON."""
    try:
        rows = frappe.db.sql(
            """SELECT value FROM `tabSingles`
               WHERE doctype = 'TS Settings' AND field = %s LIMIT 1""",
            (KILL_SWITCH_FIELD,))
    except Exception:
        return True
    if not rows or rows[0][0] is None:
        return True
    from frappe.utils import cint
    return bool(cint(rows[0][0]))


def _stampable_request():
    """True when evidence may be written: a CSRF-protected HTTP verb, or no
    HTTP request at all (bench console / scheduler / tests). Belt-and-braces
    behind the methods=["POST"] decorators — if a future caller re-opens GET,
    the trail still refuses to fabricate evidence on it."""
    req = getattr(frappe.local, "request", None)
    return (req is None) or (getattr(req, "method", "GET") in _WRITE_METHODS)


def _stamp(names, at_field, via_field, via, user=None):
    """ONE conditional UPDATE — never read-then-write (two tabs would both see
    NULL and both write). Timestamp = Python now_datetime() (site tz), never
    SQL NOW(). Raises on SQL trouble; _safe() absorbs it."""
    if (at_field, via_field) not in _STAMP_FIELDS:
        raise ValueError("unknown stamp field pair")
    if not names or not _trail_enabled() or not _stampable_request():
        return
    if getattr(frappe.flags, "read_only", 0):
        return  # replica parity with core mark_as_read's early return
    user = user or frappe.session.user
    now = now_datetime()
    names = list(dict.fromkeys(names))
    for i in range(0, len(names), 1000):  # chunk: an unbounded IN() can blow
        frappe.db.sql(                    # max_allowed_packet (security L3)
            """UPDATE `tabNotification Log`
                  SET `{at}` = %(now)s, `{via}` = %(via)s
                WHERE name IN %(names)s AND for_user = %(user)s
                  AND `{at}` IS NULL""".format(at=at_field, via=via_field),
            {"names": tuple(names[i:i + 1000]), "now": now, "via": via,
             "user": user})


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:
        try:
            frappe.log_error(title="notification trail stamp",       # L237 kwargs
                             message=frappe.get_traceback()[:2000])
            frappe.clear_messages()                                  # L276
        except Exception:
            # even the error-logging must not break the inbox (security L1 —
            # e.g. an aborted transaction refusing the Error Log insert)
            pass


def stamp_shown(names, via):
    _safe(_stamp, names, "ts_first_shown_at", "ts_first_shown_via", via)


def stamp_read(names, via):
    _safe(_stamp, names, "ts_read_at", "ts_read_via", via)


# ═══════════════════════════════════════════════════════════════════════
#  Core-endpoint override wrappers (hooks.py override_whitelisted_methods)
#  The stock desk bell calls the CORE dotted paths via frappe.call (default
#  type POST — request.js:108); the handler remaps them here. Read flip runs
#  FIRST, stamp second: a failed flip must not leave a read-stamp on an
#  unread row, while a failed stamp on a flipped row surfaces honestly in
#  the report as "Read flag set outside trail".
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist(methods=["POST"])
def mark_as_read(docname: str = ""):
    """Bell single-row read. Deliberate POST-only tightening over core (L175):
    core's GET-callable flip was CSRF-forgeable. Also closes core's missing
    ownership check — a foreign docname is rejected, not silently flipped."""
    from frappe.desk.doctype.notification_log.notification_log import (
        mark_as_read as _core_mark_as_read)

    if frappe.flags.read_only:
        return
    docname = str(docname or "")
    if docname:
        owner = frappe.db.get_value("Notification Log", docname, "for_user")
        # Administrator carve-out (security M1): core PQC shows Administrator
        # EVERY user's rows in the bell, so a foreign-row click there is a
        # legitimate admin action — flip it like core always did (the stamp
        # still self-scopes, so it surfaces honestly as outside-trail).
        if (owner and owner != frappe.session.user
                and frappe.session.user != "Administrator"):
            frappe.throw(_("Not permitted."), frappe.PermissionError)
    result = _core_mark_as_read(docname)
    if docname:
        stamp_read([docname], "Bell Click")
    return result


@frappe.whitelist(methods=["POST"])
def mark_all_as_read():
    """Bell bulk read. Unread names are captured BEFORE core flips them
    (afterwards the unread set is empty); stamped only after the flip
    succeeds."""
    from frappe.desk.doctype.notification_log.notification_log import (
        mark_all_as_read as _core_mark_all_as_read)

    if frappe.flags.read_only:
        return
    names = frappe.get_all(
        "Notification Log",
        filters={"for_user": frappe.session.user, "read": 0}, pluck="name")
    result = _core_mark_all_as_read()
    if names:
        stamp_read(names, "Bell Mark All")
    return result


# ═══════════════════════════════════════════════════════════════════════
#  Monthly evidence archive (decision D-2) — extends the trail beyond core
#  clear_old_logs' 180-day deletion. Private CSV File, one per month,
#  idempotent, fail-soft. No subject column (confidentiality — subjects
#  carry grain/maize PO amounts). Scheduler context = Administrator.
#
#  BOUNDED BY DESIGN (predictor finding, 30 Jul): each run archives only the
#  AGING-OUT TRANCHE — rows 100-179 days old. Every row spends ~79 days in
#  that window (≥2 monthly runs — tolerates ONE missed month) before core
#  deletes it at 180d, and by then its first-write-wins stamps are final —
#  so monthly runs capture the complete trail while the job stays ~2.6
#  months of rows (safe on the default scheduler queue's 300s timeout;
#  a full-table dump was not).
#  Retrieval for the report audience: download_snapshot() in the report
#  module (same _check_role gate) — the raw File is Administrator-only.
#  Both the idempotency guard here and the download lookup pin
#  owner=Administrator + unattached (security H1): role All can CREATE File
#  rows with any file_name, so filename alone must never identify evidence.
# ═══════════════════════════════════════════════════════════════════════

_SNAPSHOT_HEADERS = ("name", "for_user", "category", "document_type",
                     "document_name", "created", "read",
                     "first_shown_at", "first_shown_via",
                     "read_at", "read_via", "trail_status")
# ~79-day window vs ~31-day cadence (security M3): tolerates ONE missed
# month with full coverage (two consecutive misses lose a ~14-day band to
# the 180d deletion), while staying bounded to ~2.6 months of rows per run.
SNAPSHOT_WINDOW_OLD_DAYS = 179   # tranche lower creation bound (now - 179d)
SNAPSHOT_WINDOW_NEW_DAYS = 100   # tranche upper creation bound (now - 100d)

# The ONLY row shape trusted as an evidence archive (security H1): owner is
# server-written (Document.set_user_and_timestamp) and the scheduler runs as
# Administrator, so a desk user's planted upload can never match this.
# PREFIX match, not exact (code-tester finding): Frappe dedups the on-disk
# path and REWRITES File.file_name (e.g. …_2026_07d2ebda.csv) when a squatter
# holds the path — an exact-name lookup would silently lose the real archive.
# The month token is always \d{4}_\d{2} (regex-enforced on the download path,
# strftime-generated on the scheduler path), so the prefix cannot bleed into
# another month and carries no LIKE wildcards.
def _snapshot_file_filters(month):
    return {"file_name": ["like",
                          "notification_trail_snapshot_{0}%".format(month)],
            "is_private": 1, "owner": "Administrator",
            "attached_to_doctype": ["is", "not set"]}


def monthly_audit_snapshot():
    try:
        _monthly_audit_snapshot()
    except Exception:
        frappe.log_error(title="notification trail snapshot",
                         message=frappe.get_traceback()[:2000])


def _monthly_audit_snapshot():
    import csv
    import io

    from frappe.utils import add_to_date

    # lazy imports — notification_center_api imports THIS module at top level
    from trustbit_ethanol.ts_gate_entry.notification_center_api import _row_category
    from trustbit_ethanol.ts_gate_entry.report.ts_notification_read_audit.ts_notification_read_audit import (
        _trail_start, derive_trail_status)

    month_tag = now_datetime().strftime("%Y_%m")
    fname = "notification_trail_snapshot_{0}.csv".format(month_tag)
    # pinned filters (H1): a planted upload must neither suppress this run…
    if frappe.db.exists("File", _snapshot_file_filters(month_tag)):
        return
    rows = frappe.db.sql(
        """SELECT name, for_user, document_type, document_name, creation,
                  `read`, `type` AS ntype,
                  ts_first_shown_at, ts_first_shown_via, ts_read_at, ts_read_via
             FROM `tabNotification Log`
            WHERE creation >= %(oldest)s AND creation < %(newest)s
              AND for_user != 'Administrator'
            ORDER BY creation""",
        {"oldest": add_to_date(now_datetime(), days=-SNAPSHOT_WINDOW_OLD_DAYS),
         "newest": add_to_date(now_datetime(), days=-SNAPSHOT_WINDOW_NEW_DAYS)},
        as_dict=True)
    trail_start = _trail_start()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_SNAPSHOT_HEADERS)
    for r in rows:
        writer.writerow([
            r.name, r.for_user, _row_category(r.document_type, r.ntype),
            r.document_type or "", r.document_name or "", r.creation,
            r.read, r.ts_first_shown_at or "", r.ts_first_shown_via or "",
            r.ts_read_at or "", r.ts_read_via or "",
            derive_trail_status(r, trail_start)])
    doc = frappe.get_doc({
        "doctype": "File",
        "file_name": fname,
        "is_private": 1,
        "content": buf.getvalue(),
    })
    doc.insert(ignore_permissions=True)
    # Retrievability canary (code-tester finding): the archive must be
    # findable through the SAME filters the download uses — a silent
    # dedup-rename outside the prefix would otherwise strand evidence with
    # zero symptoms. Never let that class of failure be quiet again.
    if not frappe.db.exists("File", _snapshot_file_filters(month_tag)):
        frappe.log_error(
            title="notification trail snapshot",
            message="Archive written as File {0} (file_name={1}) but NOT "
                    "retrievable via the canonical snapshot filters — "
                    "investigate File dedup/rename.".format(doc.name, doc.file_name))
