# Copyright (c) 2026, Trustbit Software and contributors
# Notification Center — server API for /app/notification-center.
#
# Everyone's inbox: every endpoint is SELF-SCOPED to frappe.session.user and
# takes NO user parameter (deliberate contrast with the IDOR-shaped
# get_user_pending(email) pattern). Section 1 reads run through
# confidential_sql_clause + per-doc has_permission (L297/L224); mutations are
# POST-only (L175) and only touch the session user's own rows.

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime, time_diff_in_hours
from frappe.utils import format_datetime, get_url_to_form, strip_html

# v2.29.0 accountability trail — fail-soft stamp writers (no import cycle:
# ts_notification_trail's own imports are frappe-only at module level)
from trustbit_ethanol.ts_gate_entry.ts_notification_trail import stamp_read, stamp_shown

KILL_SWITCH_FIELD = "ts_notification_center_enabled"
WORKSPACE_NAME = "BBPL Ethanol"
PAGE_NAME = "notification-center"
SHORTCUT_LABEL = "Notification Center"

_CATEGORY = {
    "Purchase Order": "PO",
    "Material Request": "MR",
    "TS Material Inspection": "Inspection",
    "TS Quality Inspection": "Inspection",
    "TS Deduction Suggestion": "Inspection",
    "TS Token": "Gate",
    "TS Budget Proposal": "Budget",
    "TS Budget Override Approval": "Budget",
}
_PROD_PREFIX = "TS Production"
_CATEGORIES = ("all", "PO", "MR", "Inspection", "Production", "Gate", "Budget", "Mention", "Other")
_DAYS = ("7", "30", "90", "all")
# Mentions (core comment @mention → Notification Log type="Mention") are their
# OWN category regardless of the document they land on.
_MENTION_EXCL = " AND COALESCE(nl.`type`, '') <> 'Mention'"

# v2.32.0 — accountability-trail via-labels. `_stamp` writes with raw SQL, so an
# off-vocabulary value becomes filter-invisible debris rather than an error:
# every caller-supplied label is validated against these sets and falls back to
# the desk default. Must stay in sync with setup_notification_trail.py's Select
# options (which self-heal on migrate via create_custom_fields(update=True)).
_SHOWN_VIA = ("Center List", "Toast Push", "Bell Dropdown", "Exec List")
_READ_VIA = ("Center Click", "Center Mark All", "Bell Click", "Bell Mark All",
             "Exec Click", "Exec Mark All")


def _category_of(document_type):
    if not document_type:
        return "Other"
    if document_type in _CATEGORY:
        return _CATEGORY[document_type]
    if document_type.startswith(_PROD_PREFIX):
        return "Production"
    return "Other"


def _row_category(document_type, ntype):
    """A comment @mention is its own category regardless of the document it's
    on; everything else buckets by document type."""
    if ntype == "Mention":
        return "Mention"
    return _category_of(document_type)


def _check_read():
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _enabled():
    """Kill-switch — fail-OPEN (an unseeded field never disables the page).
    Reads tabSingles directly: get_single_value casts a missing Check field to
    0, not None (L172), which would silently disable the page. Missing row =
    enabled; only an explicit stored 0 disables."""
    try:
        rows = frappe.db.sql(
            """SELECT value FROM `tabSingles`
               WHERE doctype = 'TS Settings' AND field = %s LIMIT 1""",
            (KILL_SWITCH_FIELD,))
    except Exception:
        return True
    if not rows or rows[0][0] is None:
        return True
    return bool(cint(rows[0][0]))


@frappe.whitelist()
def notification_center_enabled():
    """Getter for page JS — operators lack TS Settings read perm (L168)."""
    _check_read()
    return 1 if _enabled() else 0


def _guard():
    _check_read()
    if not _enabled():
        frappe.throw(_("Notification Center is temporarily disabled."))


def _age_display(dt):
    try:
        hours = time_diff_in_hours(now_datetime(), dt)
    except Exception:
        return ""
    if hours < 1:
        return "{0}m".format(max(int(hours * 60), 1))
    if hours < 24:
        return "{0}h".format(int(hours))
    return "{0}d".format(int(hours / 24))


def _route_of(doc_type, doc_name, link=None):
    if link:
        return link
    if doc_type and doc_name:
        try:
            return get_url_to_form(doc_type, doc_name)
        except Exception:
            return ""
    return ""


def _cat_condition(category):
    """Static SQL fragment per validated category (never user input)."""
    if category == "all":
        return ""
    if category == "Mention":
        return " AND nl.`type` = 'Mention'"
    mapped = ", ".join(frappe.db.escape(d) for d in _CATEGORY)
    frags = {
        "PO": " AND nl.document_type = 'Purchase Order'",
        "MR": " AND nl.document_type = 'Material Request'",
        "Inspection": " AND nl.document_type IN ('TS Material Inspection', 'TS Quality Inspection', 'TS Deduction Suggestion')",
        "Production": " AND nl.document_type LIKE 'TS Production%%'",
        "Gate": " AND nl.document_type = 'TS Token'",
        "Budget": " AND nl.document_type IN ('TS Budget Proposal', 'TS Budget Override Approval')",
        "Other": (" AND (nl.document_type IS NULL OR nl.document_type = '' OR "
                  "(nl.document_type NOT IN ({0}) AND nl.document_type NOT LIKE 'TS Production%%'))"
                  ).format(mapped),
    }
    # Mentions are their OWN category — keep them out of the document-type buckets.
    return frags[category] + _MENTION_EXCL


# ═══════════════════════════════════════════════════════════════════════
#  SECTION 2 — My Notifications (bell inbox, uncapped)
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist(methods=["POST"])
def get_notifications(category="all", unread=0, days="30", start=0, page_length=20, search="",
                      exclude_types=None, shown_via="Center List"):
    # POST-only (L175 + trail): the returned rows are stamped as delivered-to-UI
    # evidence, and evidence must never be writable via a CSRF-exempt GET.
    # All callers use frappe.call (default type POST — request.js:108).
    _guard()
    user = frappe.session.user
    category = category if category in _CATEGORIES else "all"
    days = str(days) if str(days) in _DAYS else "30"
    start = max(cint(start), 0)
    page_length = min(max(cint(page_length) or 20, 1), 50)
    search = (search or "").strip()[:100]

    conds = ["nl.for_user = %(user)s"]  # hard self-scope — never trust PQC alone
    params = {"user": user}
    # v2.32.0 — server-side type exclusion so a caller's list and badge share ONE
    # predicate (a client-side .filter() guarantees they disagree). Defaults to
    # no exclusion, so the desk Center is unchanged. Values are bound, never
    # interpolated; the exec caller passes a module-level constant.
    exclude_types = _norm_exclude_types(exclude_types)
    excl_sql = ""
    if exclude_types:
        keys = []
        for i, t in enumerate(exclude_types):
            k = "excl{0}".format(i)
            params[k] = t
            keys.append("%({0})s".format(k))
        excl_sql = " AND COALESCE(nl.document_type, '') NOT IN ({0})".format(", ".join(keys))
    if days != "all":
        conds.append("nl.creation >= %(cutoff)s")
        params["cutoff"] = add_to_date(now_datetime(), days=-cint(days))
    base = "FROM `tabNotification Log` nl WHERE " + " AND ".join(conds)
    cat_sql = _cat_condition(category)
    unread_sql = " AND nl.`read` = 0" if cint(unread) else ""
    # Free-text search — parameterized LIKE (self-scoped, no injection surface).
    # Applied to the row list + its total ONLY; category_counts stay the
    # unfiltered navigator so the chips still show what's in the range.
    search_sql = ""
    if search:
        params["search"] = "%" + search + "%"
        search_sql = " AND (nl.subject LIKE %(search)s OR nl.document_name LIKE %(search)s)"

    rows = frappe.db.sql(
        """SELECT nl.name, nl.subject, nl.document_type, nl.document_name,
                  nl.creation, nl.`read` AS is_read, nl.link, nl.`type` AS ntype
           {base}{cat}{unread}{search}{excl}
           ORDER BY nl.creation DESC
           LIMIT %(start)s, %(page_length)s""".format(
            base=base, cat=cat_sql, unread=unread_sql, search=search_sql, excl=excl_sql),
        {**params, "start": start, "page_length": page_length}, as_dict=True)
    total = frappe.db.sql(
        "SELECT COUNT(*) {base}{cat}{unread}{search}{excl}".format(
            base=base, cat=cat_sql, unread=unread_sql, search=search_sql, excl=excl_sql),
        params)[0][0]

    # Accountability trail (v2.29.0): the rows above are about to be rendered
    # in the owner's Center list — stamp first-delivery. Fail-soft; separate
    # statement so a trail problem can never break the inbox.
    stamp_shown([r.name for r in rows], shown_via if shown_via in _SHOWN_VIA else "Center List")

    counts_raw = frappe.db.sql(
        "SELECT nl.document_type, nl.`type` AS ntype, nl.`read` AS is_read, COUNT(*) AS c {base}{excl} "
        "GROUP BY nl.document_type, nl.`type`, nl.`read`".format(base=base, excl=excl_sql),
        params, as_dict=True)
    category_counts = {c: {"total": 0, "unread": 0} for c in _CATEGORIES}
    for r in counts_raw:
        for key in (_row_category(r.document_type, r.ntype), "all"):
            category_counts[key]["total"] += r.c
            if not cint(r.is_read):
                category_counts[key]["unread"] += r.c

    return {
        "rows": [{
            "name": r.name,
            "category": _row_category(r.document_type, r.ntype),
            "doc_type": r.document_type or "",
            "doc_name": r.document_name or "",
            "subject": strip_html(r.subject or ""),
            "age_display": _age_display(r.creation),
            "created": str(r.creation),
            "created_display": format_datetime(r.creation, "dd MMM yyyy, HH:mm"),
            "read": cint(r.is_read),
            "route": _route_of(r.document_type, r.document_name, r.link),
        } for r in rows],
        "total": total,
        # ALL-TIME unread — must agree with the navbar badge (unread_count) and
        # mark_read's response; the range-scoped numbers live in category_counts.
        # Same exclusion as the rows above, so list and badge can never disagree.
        "unread_total": _unread_total(user, exclude_types),
        "category_counts": category_counts,
    }


@frappe.whitelist(methods=["POST"])
def mark_read(log_name, read_via="Center Click", exclude_types=None):
    _guard()
    user = frappe.session.user
    owner = frappe.db.get_value("Notification Log", log_name, "for_user")
    if not owner:
        frappe.throw(_("Notification not found."))
    if owner != user:
        frappe.throw(_("Not permitted."), frappe.PermissionError)
    frappe.db.set_value("Notification Log", log_name, "read", 1, update_modified=False)
    # trail: flip first, stamp second (a failed flip must leave no read-stamp)
    stamp_read([log_name], read_via if read_via in _READ_VIA else "Center Click")
    return {"ok": 1, "unread_total": _unread_total(user, exclude_types)}


@frappe.whitelist(methods=["POST"])
def mark_all_read(category="all", days="30", read_via="Center Mark All"):
    """Marks only the session user's unread rows matching the current filter."""
    _guard()
    user = frappe.session.user
    category = category if category in _CATEGORIES else "all"
    days = str(days) if str(days) in _DAYS else "30"
    conds = ["nl.for_user = %(user)s", "nl.`read` = 0"]
    params = {"user": user}
    if days != "all":
        conds.append("nl.creation >= %(cutoff)s")
        params["cutoff"] = add_to_date(now_datetime(), days=-cint(days))
    where = " AND ".join(conds) + _cat_condition(category)
    # trail (v2.29.0): SELECT the names (count = len) so the read flip can be
    # stamped afterwards; the business UPDATE itself stays byte-identical and
    # is NEVER coupled to the trail columns or the trail kill-switch.
    names = [r[0] for r in frappe.db.sql(
        "SELECT nl.name FROM `tabNotification Log` nl WHERE " + where, params)]
    marked = len(names)
    if marked:
        frappe.db.sql(
            "UPDATE `tabNotification Log` nl SET nl.`read` = 1 WHERE " + where, params)
        stamp_read(names, read_via if read_via in _READ_VIA else "Center Mark All")
    return {"ok": 1, "marked": marked, "unread_total": _unread_total(user)}


def _norm_exclude_types(exclude_types):
    """Coerce to a bounded list of strings. These endpoints are whitelisted, so
    a caller can send anything: a nested dict raises TypeError inside pymysql
    and a huge list would build an unbounded IN() (the same max_allowed_packet
    lesson _stamp already learned). Cap at 20 — the real callers pass one."""
    out = []
    for t in (exclude_types or []):
        if isinstance(t, (str, bytes)) and t:
            out.append(frappe.as_unicode(t)[:140])
        if len(out) >= 20:
            break
    return out


def _unread_total(user, exclude_types=None):
    """v2.32.0 — optional exclude_types (list of document_type) so a caller can
    scope the badge to the same rows its list shows. Default None = unchanged
    desk behaviour, byte for byte."""
    filters = {"for_user": user, "read": 0}
    exclude_types = _norm_exclude_types(exclude_types)
    if exclude_types:
        filters["document_type"] = ["not in", exclude_types]
    return frappe.db.count("Notification Log", filters)


@frappe.whitelist()
def unread_count():
    """Navbar badge — self-scoped unread total (indexed count, one row).
    Returns 0 when the center is disabled so the badge quietly hides."""
    _check_read()
    if not _enabled():
        return 0
    return _unread_total(frappe.session.user)


@frappe.whitelist(methods=["POST"])
def latest_unread():
    """Toast payload — the session user's newest unread notification.
    Self-scoped; None when nothing unread or center disabled. POST-only:
    the toast displays the subject, so the row is stamped delivered-to-UI
    (trail evidence must never be writable via a CSRF-exempt GET). Caller
    is frappe.xcall → POST (request.js:108)."""
    _check_read()
    if not _enabled():
        return None
    rows = frappe.get_all(
        "Notification Log",
        filters={"for_user": frappe.session.user, "read": 0},
        fields=["name", "subject", "document_type", "document_name", "link"],
        order_by="creation desc", limit_page_length=1)
    if not rows:
        return None
    r = rows[0]
    stamp_shown([r.name], "Toast Push")
    return {"subject": strip_html(r.subject or ""),
            "route": _route_of(r.document_type, r.document_name, r.link)}


# ═══════════════════════════════════════════════════════════════════════
#  SECTION 1 — My Pending Actions (self-scoped, permission-aware)
# ═══════════════════════════════════════════════════════════════════════

_CARD_LABELS = {"PO": "PO Approvals", "MR": "MR Approvals", "Inspection": "Inspections",
                "Production": "Production", "Budget": "Budget", "Other": "Post-Dated"}
_PENDING_CATEGORIES = ("all", "PO", "MR", "Inspection", "Production", "Budget", "Other")
# Per-source ceiling — high enough that "showing X of N" is honest for any real
# personal queue, bounded so a runaway role can't build an unbounded list.
_PENDING_SOURCE_CAP = 200


@frappe.whitelist()
def get_pending_actions(category="all", start=0, page_length=50):
    _guard()
    user = frappe.session.user
    roles = set(frappe.get_roles(user))
    sla_hours = cint(frappe.db.get_single_value("TS Settings", "approval_sla_hours")) or 0
    category = category if category in _PENDING_CATEGORIES else "all"
    start = max(cint(start), 0)
    page_length = min(max(cint(page_length) or 50, 1), 50)

    items = []
    items += _pending_approvals("Purchase Order", user, roles, sla_hours)
    items += _pending_approvals("Material Request", user, roles, sla_hours)
    items += _pending_inspections(user)
    items += _pending_grain_ds(roles)
    items += _pending_budget(roles)
    items += _pending_production(roles)
    # Overdue first, then longest-waiting first — what needs action floats up.
    items.sort(key=lambda x: (0 if x["overdue"] else 1, -x["age_hours"]))

    # Cards count the FULL per-category set (not the visible page) so the
    # numbers stay honest even when the list below is paginated/filtered.
    cards, order = {}, []
    for it in items:
        if it["category"] not in cards:
            cards[it["category"]] = {"key": it["category"],
                                     "label": _CARD_LABELS.get(it["category"], it["category"]),
                                     "count": 0}
            order.append(it["category"])
        cards[it["category"]]["count"] += 1

    filtered = items if category == "all" else [it for it in items if it["category"] == category]
    total = len(filtered)
    return {"cards": [cards[k] for k in order],
            "items": filtered[start:start + page_length],
            "total": total, "category": category}


def _item(category, doc_type, doc_name, since_dt, overdue_after=0):
    """since_dt = when the doc started waiting (modified/created). Server
    computes age AND the exact date so the client never date-maths (L311)."""
    age_hours = time_diff_in_hours(now_datetime(), since_dt) if since_dt else 0
    return {"category": category, "doc_type": doc_type, "doc_name": doc_name,
            "age_hours": round(age_hours, 1), "age_display": _hours_display(age_hours),
            "since_display": format_datetime(since_dt, "dd MMM yyyy, HH:mm") if since_dt else "",
            "overdue": 1 if (overdue_after and age_hours >= overdue_after) else 0,
            "route": _route_of(doc_type, doc_name)}


def _hours_display(hours):
    if hours < 1:
        return "{0}m".format(max(int(hours * 60), 1))
    if hours < 24:
        return "{0}h".format(int(hours))
    return "{0}d".format(int(hours / 24))


def _pending_approvals(doctype, user, roles, sla_hours):
    """POs/MRs where the session user's role sits at/after the current step.
    SAFE rewrite of the get_user_pending structure: self-scoped (no user
    param), confidential_sql_clause injected, per-doc has_permission drop,
    and the payload carries NO supplier/amount/cost-center."""
    from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause
    role_list = [r for r in roles if r not in ("All", "Guest", "Desk User")]
    if not role_list:
        return []
    is_po = doctype == "Purchase Order"
    tbl = "tabPurchase Order" if is_po else "tabMaterial Request"
    status_f = "ts_approval_status" if is_po else "ts_mr_status"
    step_f = "ts_current_step" if is_po else "ts_mr_current_step"
    rule_f = "ts_approval_rule" if is_po else "ts_mr_approval_route"
    submitted_f = "ts_submitted_by" if is_po else "ts_mr_submitted_by"
    step_tbl = "tabTS PO Approval Step" if is_po else "tabTS MR Approval Step"
    conf_sql = confidential_sql_clause("d", user=user, doctype=doctype) if is_po else ""

    rows = frappe.db.sql(
        """SELECT d.name, d.modified{cc}
           FROM `{tbl}` d
           WHERE d.docstatus = 0
             AND d.{status_f} LIKE 'Pending%%'
             AND d.{rule_f} IS NOT NULL
             AND EXISTS (
               SELECT 1 FROM `{step_tbl}` s
               WHERE s.parent = d.{rule_f}
                 AND s.step_order >= IFNULL(d.{step_f}, 1)
                 AND s.role IN %(roles)s)
             AND d.{submitted_f} != %(user)s
             {conf}
           ORDER BY d.modified ASC
           LIMIT {cap}""".format(
            tbl=tbl, status_f=status_f, rule_f=rule_f, step_f=step_f,
            step_tbl=step_tbl, submitted_f=submitted_f, conf=conf_sql,
            cap=_PENDING_SOURCE_CAP,
            cc=", d.cost_center" if not is_po else ""),
        {"roles": tuple(role_list), "user": user}, as_dict=True)

    if not is_po:
        rows = [r for r in rows if _mr_cc_allows(r, user)]

    out = []
    for r in rows:
        # Per-doc permission drop (L297/L224) — confidential POs the user
        # cannot read never appear, even by name.
        if not frappe.has_permission(doctype, "read", doc=r.name, user=user):
            continue
        out.append(_item("PO" if is_po else "MR", doctype, r.name, r.modified,
                         overdue_after=sla_hours))
    return out


def _mr_cc_allows(row, user):
    """CC-config filter (same semantics as the approval route): if the MR's
    cost center has an active config, the user must be a Reviewer/Final
    Approver in it; no config = role-only fallback."""
    cc_config = frappe.db.get_value(
        "TS CC Approval Config", {"cost_center": row.cost_center, "is_active": 1}, "name")
    if not cc_config:
        return True
    return bool(frappe.db.sql(
        """SELECT 1 FROM `tabTS CC Approval User`
           WHERE parent = %s AND user = %s
             AND action_type IN ('Reviewer', 'Final Approver') LIMIT 1""",
        (cc_config, user)))


def _pending_inspections(user):
    rows = frappe.get_all(
        "TS Material Inspection",
        filters={"status": "Pending Inspection"},
        or_filters={"requester": user, "department_head": user},
        fields=["name", "created_time"], limit_page_length=_PENDING_SOURCE_CAP)
    return [_item("Inspection", "TS Material Inspection", r.name, r.created_time)
            for r in rows]


def _pending_grain_ds(roles):
    if "Grain Manager" not in roles:
        return []
    rows = frappe.get_all("TS Deduction Suggestion", filters={"docstatus": 0},
                          fields=["name", "creation"], limit_page_length=_PENDING_SOURCE_CAP)
    return [_item("Inspection", "TS Deduction Suggestion", r.name, r.creation)
            for r in rows]


def _pending_budget(roles):
    out = []
    if "CEO" in roles:
        for r in frappe.get_all("TS Budget Proposal", filters={"status": "Pending CEO"},
                                fields=["name", "modified"], limit_page_length=_PENDING_SOURCE_CAP):
            out.append(_item("Budget", "TS Budget Proposal", r.name, r.modified))
    if roles & {"CEO", "MD", "Managing Director"}:
        for r in frappe.get_all("TS Post Dated Entry Request",
                                filters={"status": "Pending Approval"},
                                fields=["name", "modified"], limit_page_length=_PENDING_SOURCE_CAP):
            out.append(_item("Other", "TS Post Dated Entry Request", r.name, r.modified))
    return out


def _pending_production(roles):
    if "Stores Manager" not in roles:
        return []
    if not cint(frappe.db.get_single_value("TS Settings", "ts_production_entry_enabled") or 0):
        return []
    rows = frappe.get_all(
        "TS Production Entry",
        filters={"ts_variance_status": ["in", ["Pending Stores Release", "Pending Material Request"]]},
        fields=["name", "modified"], limit_page_length=_PENDING_SOURCE_CAP)
    return [_item("Production", "TS Production Entry", r.name, r.modified)
            for r in rows]


# ═══════════════════════════════════════════════════════════════════════
#  SECTION 3 — My WhatsApp messages (self-scoped, read-only)
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_whatsapp_log(start=0, page_length=20):
    """Self-scoped tail of TS WhatsApp Log. Bypasses the Log's SysMan/IT-Head
    DocPerm the same way Notification Log self-scopes: recipient_user =
    session user only, safe columns only (no variables_sent, no numbers)."""
    _guard()
    user = frappe.session.user
    start = max(cint(start), 0)
    page_length = min(max(cint(page_length) or 20, 1), 50)
    filters = {"recipient_user": user}
    total = frappe.db.count("TS WhatsApp Log", filters)
    rows = frappe.get_all(
        "TS WhatsApp Log", filters=filters,
        fields=["template_key", "ref_doctype", "ref_name", "status", "is_sandbox", "creation"],
        order_by="creation desc", limit_start=start, limit_page_length=page_length)
    try:
        opted_in = bool(cint(frappe.db.get_value("User", user, "ts_whatsapp_opt_in") or 0))
    except Exception:
        opted_in = False
    return {
        "rows": [{
            "template_key": r.template_key or "",
            "ref_doctype": r.ref_doctype or "",
            "ref_name": r.ref_name or "",
            "status": r.status or "",
            "status_display": r.status or "Queued",
            "age_display": _age_display(r.creation),
            "created_display": format_datetime(r.creation, "dd MMM yyyy, HH:mm"),
            "is_sandbox": cint(r.is_sandbox),
            "route": _route_of(r.ref_doctype, r.ref_name),
        } for r in rows],
        "total": total,
        "opted_in": opted_in,
    }


# ═══════════════════════════════════════════════════════════════════════
#  after_migrate seeders (registered in hooks.py) — idempotent
# ═══════════════════════════════════════════════════════════════════════

def after_migrate_notification_center():
    for fn in (_seed_settings_field, _seed_workspace_shortcut):
        try:
            fn()
        except Exception:
            frappe.log_error(title="notification center seeder: {0}".format(fn.__name__),
                             message=frappe.get_traceback()[:2000])


def _seed_settings_field():
    """Kill-switch Custom Field on TS Settings. default '1' + fail-open getter:
    a Custom Field default is NOT re-written to tabSingles on migrate (unlike a
    doctype-JSON default, L227), and it keeps the form checkbox pre-checked so
    an admin Save writes 1, not a silent 0."""
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    meta = frappe.get_meta("TS Settings")
    if meta.has_field(KILL_SWITCH_FIELD):
        return
    create_custom_fields({"TS Settings": [{
        "fieldname": KILL_SWITCH_FIELD,
        "label": "Enable Notification Center",
        "fieldtype": "Check",
        "default": "1",
        "permlevel": 1,
        "insert_after": "ts_grain_defer_enabled",
        "description": "Instant off-switch for the /app/notification-center page (fail-open).",
    }]}, ignore_validate=True)


def _seed_workspace_shortcut():
    """Notification Center tile in the BBPL Ethanol workspace — shortcut child
    row AND matching content block (Lesson 173: migrate never syncs these)."""
    import json

    if not frappe.db.exists("Workspace", WORKSPACE_NAME) or not frappe.db.exists("Page", PAGE_NAME):
        return
    ws = frappe.get_doc("Workspace", WORKSPACE_NAME)
    if any(s.label == SHORTCUT_LABEL or s.link_to == PAGE_NAME for s in ws.shortcuts):
        return
    ws.append("shortcuts", {"type": "Page", "link_to": PAGE_NAME,
                            "label": SHORTCUT_LABEL, "color": "Blue"})
    try:
        content = json.loads(ws.content or "[]")
    except Exception:
        content = []
    content.append({"id": "sc_notification_center", "type": "shortcut",
                    "data": {"shortcut_name": SHORTCUT_LABEL, "col": 4}})
    ws.content = json.dumps(content)
    ws.flags.ignore_permissions = True
    # ignore_links: a full ws.save() re-validates EVERY legacy row — prod's
    # workspace carries a stale Number Card link that is not ours to fix, and
    # our own link_to Page is existence-checked above (L317).
    ws.flags.ignore_links = True
    ws.save()
