# Copyright (c) 2026, Trustbit Software and contributors
"""TS Notification Read Audit — role-gated accountability report (v2.29.0).

Answers, per notification per user: was it delivered to their inbox, was it
delivered to their UI (when, via which channel), and was it marked read
(when, how). The 7-state Trail Status vocabulary keeps the evidence honest —
"bulk cleared without ever being displayed" is never conflated with "opened".

SECURITY CONTRACT (plan v2 §3, binding):
  - _check_role() is the LITERAL FIRST STATEMENT of execute() — an
    unauthorised caller can never probe filter-validation behaviour.
  - The report body is raw SQL over tabNotification Log and therefore
    BYPASSES the core PQC (for_user = self) BY DESIGN — an authority must
    see other users' rows. Per Lesson 224, that waiver is paired with the
    explicit role gate that throws first. The .json roles[] array is menu
    visibility only, NOT the gate.
  - Every filter is parameterised or validated against a closed set; ORDER
    BY and LIMIT are static literals; `status` never reaches SQL.
  - `subject` is deliberately NOT selected (confidential grain/maize PO
    amounts live in subjects); rows are identified by doctype + name, and
    confidential-doc rows are dropped for viewers outside the existing
    confidentiality allow-list (reuses ts_confidential_po, no second engine).
"""

import frappe
from frappe import _
from frappe.utils import (add_days, add_to_date, cint, get_datetime, getdate,
                          now_datetime, time_diff_in_hours)

VERSION = "2.29.0"
ALLOWED_ROLES = ("CEO", "MD", "System Manager", "IT Head")  # decision D-1
MAX_SPAN_DAYS = 180
ROW_LIMIT = 5000  # static literal

_CONFIDENTIAL_DOCTYPES = ("Purchase Order", "Purchase Receipt",
                          "Purchase Invoice", "Material Request")

STATUS_PRE_TRAIL = "Pre-trail — no data possible"
STATUS_READ_OPENED = "Read — opened by user"
STATUS_READ_BULK_AFTER = "Read — bulk cleared after delivery"
STATUS_READ_BULK_BLIND = "Read — bulk cleared, no prior UI delivery"
STATUS_SHOWN_UNREAD = "Delivered to UI — not read"
STATUS_NO_DELIVERY = "No UI delivery recorded"
STATUS_OUTSIDE_TRAIL = "Read flag set outside trail"

ALL_STATUSES = (STATUS_PRE_TRAIL, STATUS_READ_OPENED, STATUS_READ_BULK_AFTER,
                STATUS_READ_BULK_BLIND, STATUS_SHOWN_UNREAD,
                STATUS_NO_DELIVERY, STATUS_OUTSIDE_TRAIL)


def _check_role():
    """Clone of ts_usage_report_api._check_role — Administrator bypasses
    (system account); everyone else needs an ALLOWED_ROLES membership."""
    if frappe.session.user == "Administrator":
        return
    if not any(r in frappe.get_roles(frappe.session.user) for r in ALLOWED_ROLES):
        frappe.throw(_("Not authorized to view the Notification Read Audit"),
                     frappe.PermissionError)


def _trail_start():
    """Trail recording started when the ts_read_at Custom Field was created
    on this server (plan v2 rebuttal R-2). None ⇒ pre-trail status disabled
    (fails toward the non-accusatory bucket)."""
    return frappe.db.get_value(
        "Custom Field", {"dt": "Notification Log", "fieldname": "ts_read_at"},
        "creation")


def derive_trail_status(row, trail_start):
    """The 7-state vocabulary — exact strings from plan v2 §5.

    ORDER, not presence (code-tester blocker, 30 Jul): the Center's default
    view lists already-read rows, so a blind-cleared row gets a delivery
    stamp on the user's NEXT visit. "After delivery" is asserted only when
    the delivery stamp is actually ≤ the read stamp — otherwise the
    accusatory bulk-blind state must NOT decay into the exculpatory one."""
    if (trail_start and row.creation
            and get_datetime(row.creation) < get_datetime(trail_start)):
        return STATUS_PRE_TRAIL
    if row.ts_read_at:
        if row.ts_read_via in ("Center Click", "Bell Click"):
            return STATUS_READ_OPENED
        if (row.ts_first_shown_at
                and get_datetime(row.ts_first_shown_at) <= get_datetime(row.ts_read_at)):
            return STATUS_READ_BULK_AFTER
        return STATUS_READ_BULK_BLIND
    if cint(row.read):
        return STATUS_OUTSIDE_TRAIL
    if row.ts_first_shown_at:
        return STATUS_SHOWN_UNREAD
    return STATUS_NO_DELIVERY


def _validate_filters(filters):
    """Server-side re-validation — the .js defaults are advisory only
    (query_report.run accepts arbitrary filter JSON)."""
    from trustbit_ethanol.ts_gate_entry.notification_center_api import _CATEGORIES

    f = frappe._dict(filters or {})
    to_date = getdate(f.to_date) if f.to_date else getdate(now_datetime())
    from_date = getdate(f.from_date) if f.from_date else getdate(add_days(to_date, -30))
    if to_date < from_date:
        frappe.throw(_("To Date cannot be before From Date."))
    if (to_date - from_date).days > MAX_SPAN_DAYS:
        frappe.throw(_("Date span cannot exceed {0} days.").format(MAX_SPAN_DAYS))

    user = (f.user or "").strip()
    if user and not frappe.db.exists("User", user):
        frappe.throw(_("Unknown user."))

    category = f.category if f.category in _CATEGORIES else "all"
    status = f.status if f.status in ALL_STATUSES else ""
    return frappe._dict(user=user, from_date=from_date, to_date=to_date,
                        category=category, status=status,
                        only_unread=cint(f.only_unread))


def _where(f):
    from trustbit_ethanol.ts_gate_entry.notification_center_api import _cat_condition

    # Administrator is a shared SYSTEM account — its rows are noise in a
    # staff-accountability report and are hidden everywhere (user request,
    # 31 Jul; same stance as ts_usage_report_api._excluded_users)
    conds = ["nl.creation >= %(from_date)s",
             "nl.creation < DATE_ADD(%(to_date)s, INTERVAL 1 DAY)",
             "nl.for_user != 'Administrator'"]
    params = {"from_date": f.from_date, "to_date": f.to_date}
    if f.user:
        conds.append("nl.for_user = %(user)s")
        params["user"] = f.user
    if f.only_unread:
        conds.append("nl.`read` = 0")
    return " AND ".join(conds) + _cat_condition(f.category), params


def _fetch(f):
    where, params = _where(f)
    return frappe.db.sql(
        """SELECT nl.name, nl.for_user, nl.document_type, nl.document_name,
                  nl.creation, nl.`read`, nl.`type` AS ntype,
                  nl.ts_first_shown_at, nl.ts_first_shown_via,
                  nl.ts_read_at, nl.ts_read_via
             FROM `tabNotification Log` nl
            WHERE """ + where + """
            ORDER BY nl.creation DESC
            LIMIT {0}""".format(int(ROW_LIMIT)), params, as_dict=True)


def _count_in_range(f):
    where, params = _where(f)
    return frappe.db.sql(
        "SELECT COUNT(*) FROM `tabNotification Log` nl WHERE " + where,
        params)[0][0]


def _confidential_visible():
    """Per-doctype visibility for the session user — reuses the existing
    confidentiality engine (never a second one)."""
    from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential
    return {dt: user_sees_confidential(dt, frappe.session.user)
            for dt in _CONFIDENTIAL_DOCTYPES}


def _notif_enabled_map(users):
    if not users:
        return {}
    rows = frappe.db.sql(
        """SELECT name, enabled FROM `tabNotification Settings`
            WHERE name IN %(users)s""", {"users": tuple(users)}, as_dict=True)
    return {r.name: cint(r.enabled) for r in rows}


def execute(filters=None):
    _check_role()  # FIRST STATEMENT — before any filter parsing
    from trustbit_ethanol.ts_gate_entry.notification_center_api import _row_category

    f = _validate_filters(filters)
    raw = _fetch(f)
    trail_start = _trail_start()
    conf_visible = _confidential_visible()

    dropped_confidential = 0
    rows = []
    for r in raw:
        if r.document_type in conf_visible and not conf_visible[r.document_type]:
            dropped_confidential += 1
            continue
        r.category = _row_category(r.document_type, r.ntype)
        r.trail_status = derive_trail_status(r, trail_start)
        if f.status and r.trail_status != f.status:
            continue
        r.lag_hours = (round(time_diff_in_hours(r.ts_read_at, r.creation), 1)
                       if r.ts_read_at else None)
        rows.append(r)

    enabled_map = _notif_enabled_map({r.for_user for r in rows})
    for r in rows:
        r.notif_enabled = enabled_map.get(r.for_user, 1)

    # honesty (live-data M-1): when capped, say how much of the range the
    # 5000 rows actually cover — a capped 30-day view silently answers for ~8
    in_range = _count_in_range(f) if len(raw) >= ROW_LIMIT else len(raw)
    return _columns(), rows, _banner(trail_start, len(raw), dropped_confidential,
                                     in_range)


def _columns():
    return [
        {"label": _("User"), "fieldname": "for_user", "fieldtype": "Link",
         "options": "User", "width": 200},
        {"label": _("Category"), "fieldname": "category", "fieldtype": "Data", "width": 90},
        {"label": _("Document Type"), "fieldname": "document_type", "fieldtype": "Data", "width": 140},
        {"label": _("Document"), "fieldname": "document_name", "fieldtype": "Dynamic Link",
         "options": "document_type", "width": 170},
        {"label": _("Created (Delivered to Inbox)"), "fieldname": "creation",
         "fieldtype": "Datetime", "width": 165},
        {"label": _("First Delivered to UI"), "fieldname": "ts_first_shown_at",
         "fieldtype": "Datetime", "width": 165},
        {"label": _("Delivery Channel"), "fieldname": "ts_first_shown_via",
         "fieldtype": "Data", "width": 110},
        {"label": _("Marked Read At"), "fieldname": "ts_read_at",
         "fieldtype": "Datetime", "width": 165},
        {"label": _("Marked Read Via"), "fieldname": "ts_read_via",
         "fieldtype": "Data", "width": 120},
        {"label": _("Response Lag (h)"), "fieldname": "lag_hours",
         "fieldtype": "Float", "precision": 1, "width": 110},
        {"label": _("Trail Status"), "fieldname": "trail_status",
         "fieldtype": "Data", "width": 260},
        {"label": _("Notifications Enabled"), "fieldname": "notif_enabled",
         "fieldtype": "Check", "width": 90},
    ]


@frappe.whitelist()
def download_snapshot(month=None):
    """Gated retrieval of a monthly evidence archive (predictor finding: the
    raw private File is Administrator-only, which locked out the very
    audience it exists for). Same role gate as the report; read-only, so the
    L175 POST rule does not apply. `month` = 'YYYY_MM' (default: current)."""
    import re

    _check_role()  # FIRST — same audience as the report
    month = (month or now_datetime().strftime("%Y_%m")).strip()
    if not re.fullmatch(r"\d{4}_\d{2}", month, re.ASCII):
        frappe.throw(_("Invalid month — use YYYY_MM."))
    from trustbit_ethanol.ts_gate_entry.ts_notification_trail import _snapshot_file_filters

    # pinned filters + oldest-first (security H1): role All can CREATE File
    # rows under any file_name, and get_value's default ORDER BY modified DESC
    # would hand a freshly planted upload to the auditor. owner is
    # server-written and the scheduler runs as Administrator — unspoofable.
    # PREFIX match (not exact): a path-squatter makes Frappe RENAME the real
    # archive's file_name at insert; the prefix still finds it (code-tester).
    file_id = frappe.db.get_value("File", _snapshot_file_filters(month),
                                  order_by="creation asc")
    if not file_id:
        frappe.throw(_("No snapshot exists for {0}.").format(month))
    content = frappe.get_doc("File", file_id).get_content()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    # serve under the CANONICAL name even if the stored file_name was
    # dedup-renamed — month is regex-validated, so no header injection
    frappe.response.filename = "notification_trail_snapshot_{0}.csv".format(month)
    frappe.response.filecontent = _confidential_filter_csv(content)
    frappe.response.type = "download"


def _confidential_filter_csv(content):
    """Disclosure-time confidentiality (security M2): the archive stores
    document_name for confidential grain/maize PO/MR rows viewer-agnostically;
    on the way OUT, drop rows the caller's roles may not see — the SAME rule
    execute() applies. Keeps report and download symmetric if ALLOWED_ROLES
    is ever widened. Reuses the existing engine, never a second one."""
    import csv
    import io

    visible = _confidential_visible()
    if all(visible.values()):
        return content
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        return content
    header = rows[0]
    try:
        dt_idx = header.index("document_type")
    except ValueError:
        # fail CLOSED (L297): a confidentiality control must never respond to
        # an unrecognised format by disclosing everything
        frappe.throw(_("Snapshot format not recognised."))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for r in rows[1:]:
        dt = r[dt_idx] if dt_idx < len(r) else ""
        if dt in visible and not visible[dt]:
            continue
        writer.writerow(r)
    return out.getvalue()


def _canary_7d():
    return frappe.db.sql(
        """SELECT COUNT(*) FROM `tabNotification Log`
            WHERE ts_first_shown_at >= %(cutoff)s OR ts_read_at >= %(cutoff)s""",
        {"cutoff": add_to_date(now_datetime(), days=-7)})[0][0]


def _banner(trail_start, fetched, dropped_confidential, in_range=None):
    from trustbit_ethanol.ts_gate_entry.notification_center_api import _enabled as center_on
    from trustbit_ethanol.ts_gate_entry.ts_notification_trail import _trail_enabled

    parts = [
        _("<b>What this report proves — and what it does not.</b>"),
        _("<b>\"Delivered to UI\"</b> means the server returned this notification to that "
          "user's own Notification Center list or push toast at that time. It does "
          "<b>not</b> prove the user looked at the screen."),
        _("<b>\"Marked Read\"</b> means a read action was recorded from that user's own "
          "session, by the method shown."),
        _("<b>A blank delivery stamp does NOT prove the user never saw the notification.</b> "
          "The desk bell dropdown, the Notification Log list and form views, WhatsApp, "
          "e-mail and direct links to the document are not stamped."),
        _("Trail recording started <b>{0}</b>; older rows are marked "
          "\"Pre-trail — no data possible\". Frappe deletes notification rows "
          "<b>180 days</b> after creation; a monthly CSV snapshot (private File) "
          "archives the trail columns beyond that.").format(
            frappe.utils.format_datetime(trail_start) if trail_start else _("(fields not yet seeded)")),
        _("All times are <b>server time (Asia/Kolkata)</b>. Delivery and read "
          "timestamps are recorded automatically by the system and cannot be "
          "created, changed, or removed by any user. System accounts are not "
          "shown in this report."),
        _("Trail switch: <b>{0}</b> · Notification Center switch: <b>{1}</b> · "
          "Rows stamped in the last 7 days: <b>{2}</b> (0 means nothing has "
          "been stamped yet — either nobody opened the Notification Center in "
          "that period, or the trail is off).").format(
            _("ON") if _trail_enabled() else _("OFF"),
            _("ON") if center_on() else _("OFF"),
            cint(_canary_7d())),
    ]
    if fetched >= ROW_LIMIT:
        parts.append(_("⚠ Showing the newest {0} of <b>{1}</b> rows in this "
                       "range — older rows in range are NOT shown. Narrow the "
                       "date range or filter by user.").format(
            ROW_LIMIT, cint(in_range if in_range is not None else fetched)))
    if dropped_confidential:
        # honest about the granularity (live-data M-2): the drop is by
        # DOCUMENT TYPE, not per-row confidential flag — say so
        parts.append(_("{0} rows for confidential document types (PO / PR / "
                       "PI / MR) are hidden from your account.")
                     .format(dropped_confidential))
    return "<br>".join(parts)
