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
from frappe.utils import add_to_date, cint, cstr, get_datetime, now_datetime, time_diff_in_hours
from frappe.utils import format_datetime, get_url_to_form, strip_html

# v2.29.0 accountability trail — fail-soft stamp writers (no import cycle:
# ts_notification_trail's own imports are frappe-only at module level)
from trustbit_ethanol.ts_gate_entry.ts_notification_trail import stamp_read, stamp_shown
from trustbit_ethanol.ts_gate_entry.ts_notification_routing import role_recipients, skip_accounts

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
_PENDING_SCOPES = ("all", "assigned", "open")
# Per-source ceiling. It cuts BEFORE items are classified, so it sits far above
# any real queue (prod's largest is ~350) — a lower one would silently hide the
# newest items (L441); bounded so a runaway role can't build an unbounded list.
_PENDING_SOURCE_CAP = 1000


@frappe.whitelist()
def get_pending_actions(category="all", start=0, page_length=50, scope="all"):
    """v2.60.0 — everything the user can act on, in two lists: "assigned" (it
    waits on them: named on it, or holding the current step's role without being
    left out by Notification Routing or the skip list; the badge counts these)
    and "open" (their roles can act, but it waits on someone else). Each item
    says why. "all" (the default, for a page script cached before v2.60.0)
    returns both lists together."""
    _guard()
    user = frappe.session.user
    roles = set(frappe.get_roles(user))
    role_why = _role_why_fn(user)
    sla_hours = cint(frappe.db.get_single_value("TS Settings", "approval_sla_hours")) or 0
    category = category if category in _PENDING_CATEGORIES else "all"
    scope = scope if scope in _PENDING_SCOPES else "all"
    start = max(cint(start), 0)
    page_length = cint(page_length)
    page_length = 50 if page_length <= 0 else min(page_length, 50)

    items = []
    items += _pending_approvals("Purchase Order", user, roles, sla_hours, role_why)
    items += _pending_approvals("Material Request", user, roles, sla_hours, role_why)
    items += _pending_inspections(user)
    items += _pending_grain_ds(roles, role_why)
    items += _pending_budget(roles, role_why)
    items += _pending_production(roles, role_why)
    # Longest-waiting first, on the stored timestamp (not the clock-derived age
    # or overdue flag, which move between two "Load more" calls); the name breaks
    # ties, so paging never repeats or skips an item.
    items.sort(key=lambda x: (x.pop("_since"), x["doc_type"], x["doc_name"]))
    counts = {"assigned": 0, "open": 0}
    for it in items:
        counts[it["scope"]] += 1
    if scope != "all":
        items = [it for it in items if it["scope"] == scope]

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
            "total": total, "category": category, "scope": scope, "counts": counts}


def _role_why_fn(user):
    """Per request: role → None when the role's role-wide items are this user's
    (the user is among its recipients — Notification Routing and the skip list,
    exactly as for the bells), else (basis, reason). Administrator never gets
    role-wide items."""
    memo, on_skip = {}, user in skip_accounts()

    def why(role):
        if role not in memo:
            if user == "Administrator":
                memo[role] = ("admin", _("The Administrator account gets only items that name it"))
            elif user in role_recipients(role):
                memo[role] = None
            elif on_skip:
                memo[role] = ("skip", _("Your account is on the skip list"))
            else:
                memo[role] = ("routing", _("Notification Routing sends {0} items to selected people").format(role))
        return memo[role]
    return why


def _by_role(held, role_why, mine=None, later_step=None):
    """(scope, basis, why) for an item the user reaches through a role. `held` =
    the user's roles that act on it now: "assigned" when one of them is the user's
    (`mine` names it; default: the role's queue), else "open" with the reason;
    none held now → their role acts at `later_step`."""
    mine = mine or _("Waiting in the {0} queue")
    reason = None
    for role in held:
        why = role_why(role)
        if why is None:
            return "assigned", "role", mine.format(role)
        reason = reason or why
    if reason:
        return ("open",) + reason
    return "open", "later", _("One of your roles acts later, at step {0}").format(later_step)


def _item(category, doc_type, doc_name, since_dt, cls, overdue_after=0):
    """since_dt = when the doc started waiting (modified/created). Server
    computes age AND the exact date so the client never date-maths (L311).
    cls = (scope, basis, why) — v2.60.0."""
    age_hours = time_diff_in_hours(now_datetime(), since_dt) if since_dt else 0
    return {"category": category, "doc_type": doc_type, "doc_name": doc_name,
            "scope": cls[0], "basis": cls[1], "why": cls[2],
            "age_hours": round(age_hours, 1), "age_display": _hours_display(age_hours),
            "since_display": format_datetime(since_dt, "dd MMM yyyy, HH:mm") if since_dt else "",
            "overdue": 1 if (overdue_after and age_hours >= overdue_after) else 0,
            "route": _route_of(doc_type, doc_name),
            "_since": str(get_datetime(since_dt)) if since_dt else ""}  # sort key, dropped before sending


def _hours_display(hours):
    if hours < 1:
        return "{0}m".format(max(int(hours * 60), 1))
    if hours < 24:
        return "{0}h".format(int(hours))
    return "{0}d".format(int(hours / 24))


def _pending_approvals(doctype, user, roles, sla_hours, role_why):
    """POs/MRs the session user can act on, by the approval check's own rule: a
    role at the current or a later step (only the current one at a Strict Step)
    and, for an MR, the cost-centre config (_mr_class). SAFE rewrite of the
    get_user_pending structure: self-scoped (no user param),
    confidential_sql_clause injected, per-doc has_permission drop, and the
    payload carries NO supplier/amount/cost-center.
    v2.60.0 — each item is classified: assigned only when it waits on the user
    at its CURRENT step."""
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
    skip_f = "ts_self_skip_impossible" if is_po else "ts_mr_self_skip_impossible"
    step_tbl = "tabTS PO Approval Step" if is_po else "tabTS MR Approval Step"
    conf_sql = confidential_sql_clause("d", user=user, doctype=doctype) if is_po else ""

    # v2.52.0 Strict Step — an MR route step ticked `strict_step` admits only its own
    # role while the MR is pending there (ts_po_approval._mr_actionable_steps), so only
    # that row may satisfy the EXISTS. Column-guarded: if this code ever runs before the
    # migrate that adds the column, the predicate is simply omitted (today's behaviour)
    # instead of the whole pending list failing with an unknown-column error.
    strict_sql = ""
    if not is_po and frappe.db.has_column("TS MR Approval Step", "strict_step"):
        strict_sql = (
            "AND (s.step_order = IFNULL(d.{step_f}, 1) "
            "OR IFNULL((SELECT cs.strict_step FROM `{step_tbl}` cs "
            "WHERE cs.parent = d.{rule_f} AND cs.parenttype = 'TS MR Approval Route' "
            "AND cs.step_order = IFNULL(d.{step_f}, 1) ORDER BY cs.idx LIMIT 1), 0) = 0)"
        ).format(step_f=step_f, rule_f=rule_f, step_tbl=step_tbl)

    # An MR's cost centre as the approval check reads it: the header, else the
    # first item's (ts_po_approval._get_mr_cost_center).
    cc_sql = "" if is_po else (
        ", COALESCE(NULLIF(d.cost_center, ''), (SELECT i.cost_center FROM `tabMaterial Request Item` i"
        " WHERE i.parent = d.name AND IFNULL(i.cost_center, '') != '' ORDER BY i.idx LIMIT 1)) AS cost_center")
    query = """SELECT d.name, d.modified, d.{status_f} AS status,
                  IFNULL(d.{step_f}, 1) AS step, d.{rule_f} AS rule{cc}
           FROM `{tbl}` d
           WHERE d.docstatus = 0
             AND d.{status_f} LIKE 'Pending%%'
             AND d.{rule_f} IS NOT NULL
             AND EXISTS (
               SELECT 1 FROM `{step_tbl}` s
               WHERE s.parent = d.{rule_f}
                 AND s.step_order >= IFNULL(d.{step_f}, 1)
                 {strict}
                 AND s.role IN %(roles)s)
             AND (IFNULL(d.{skip_f}, 0) = 1
                  OR (IFNULL(d.{submitted_f}, '') != %(user)s AND d.owner != %(user)s))
             {conf}
           ORDER BY d.modified ASC
           LIMIT {cap}""".format(
            tbl=tbl, status_f=status_f, rule_f=rule_f, step_f=step_f,
            step_tbl=step_tbl, submitted_f=submitted_f, skip_f=skip_f, conf=conf_sql,
            cap=_PENDING_SOURCE_CAP,
            cc=cc_sql,
            strict=strict_sql)
    rows = frappe.db.sql(query, {"roles": tuple(role_list), "user": user}, as_dict=True)
    if not rows:
        return []

    strict_col = "IFNULL(strict_step, 0)" if strict_sql else "0"
    steps = {}
    for s in frappe.db.sql(
            "SELECT parent, step_order, role, {0} AS strict_step FROM `{1}` WHERE parent IN %(p)s "
            "ORDER BY parent, idx".format(strict_col, step_tbl), {"p": tuple({r.rule for r in rows})},
            as_dict=True):
        steps.setdefault(s.parent, []).append(s)
    deputy = False
    if not is_po and "CEO" in roles:
        from trustbit_ethanol.ts_gate_entry.ts_avp_deputy import _is_deputy_mode_enabled
        deputy = _is_deputy_mode_enabled()

    out, cfgs, memo = [], {}, {}
    for r in rows:
        cur, rs = cint(r.step), steps.get(r.rule, [])
        # As _mr_actionable_steps: the first row at the current step decides strictness.
        current = next((s for s in rs if cint(s.step_order) == cur), None)
        strict = bool(current and cint(current.strict_step))
        later = [] if strict else sorted({cint(s.step_order) for s in rs if cint(s.step_order) > cur})
        mine_later = [cint(s.step_order) for s in rs if cint(s.step_order) in later and s.role in roles]
        now_roles = [current.role] if strict else [s.role for s in rs if cint(s.step_order) == cur]
        cls = _by_role([x for x in now_roles if x in roles], role_why,
                       _("You hold the current step's role: {0}"), min(mine_later or [0]))
        named = []
        if not is_po:
            cls, named = _mr_class(r, user, cur, later, cls, deputy, cfgs)
            if not cls:
                continue
        # Per-doc permission drop (L297/L224) — confidential POs the user
        # cannot read never appear, even by name. After the cheap filters: it
        # loads the document.
        if not frappe.has_permission(doctype, "read", doc=r.name, user=user):
            continue
        if (cls[0] == "open" and user != "Administrator" and (named or now_roles)
                and not _anyone_now(named, now_roles, memo)):
            # Nobody is waited on at this step: it counts for everyone who can act.
            # A step with no row at all (a gap in the route) stays Open.
            cls = ("assigned", "unassigned", _("Nobody is assigned to this step; you can act on it"))
        out.append(_item("PO" if is_po else "MR", doctype, r.name, r.modified, cls,
                         overdue_after=sla_hours))
    return out


def _anyone_now(named, now_roles, memo):
    """Is anyone waited on at the current step: an enabled named approver when
    the cost centre names some, else a recipient of the step's role?"""
    def has(key, fn):
        if key not in memo:
            memo[key] = bool(fn())
        return memo[key]
    if named:
        return any(has(("user", u), lambda u=u: frappe.db.get_value("User", u, "enabled")) for u in named)
    return any(has(("role", x), lambda x=x: role_recipients(x)) for x in now_roles)


def _mr_class(r, user, cur, later, by_role, deputy, cfgs):
    """The approval check's cost-centre rule (_validate_user_can_act_on_mr): when
    the MR's config names approvers for the current step, only they, or someone
    named for a later step, can act; otherwise the role decides (`by_role`).
    Returns (class, the approvers named for the current step); class None = the
    user cannot act. The CEO can act at Pending AVP while AVP deputy mode is on,
    whatever the config says."""
    from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
        get_cc_approvers_for_step,
    )
    cfg = _mr_cfg(r.cost_center, cfgs)  # same lookup as get_cc_config()
    named = get_cc_approvers_for_step(cfg, cur, route=r.rule) if cfg else []
    if not named:
        return by_role, named
    if user in named:
        return ("assigned", "named", _("Named approver for this cost centre at this step")), named
    if any(user in get_cc_approvers_for_step(cfg, s, route=r.rule) for s in later):
        return ("open", "named_later", _("Named approver for this cost centre at a later step")), named
    if deputy and r.status == "Pending AVP":
        return ("open", "deputy", _("Waiting for the AVP; you can act as AVP deputy")), named
    return None, named


def _pending_inspections(user):
    # v2.59.0 — also the heads an inspection is shared with for writing, and
    # only inspections the user can open. All of them name the user (v2.60.0).
    rows = frappe.db.sql(
        """SELECT i.name, i.created_time, i.requester FROM `tabTS Material Inspection` i
           WHERE i.status = 'Pending Inspection'
             AND (i.requester = %(user)s OR i.department_head = %(user)s
                  OR i.name IN (SELECT s.share_name FROM `tabDocShare` s
                                WHERE s.share_doctype = 'TS Material Inspection'
                                  AND s.user = %(user)s AND s.write = 1))
           ORDER BY i.modified DESC LIMIT {cap}""".format(cap=_PENDING_SOURCE_CAP),
        {"user": user}, as_dict=True)
    raised = ("assigned", "raised", _("You raised the request"))
    head = ("assigned", "head", _("You head the department it belongs to"))
    return [_item("Inspection", "TS Material Inspection", r.name, r.created_time,
                  raised if r.requester == user else head)
            for r in rows if frappe.has_permission("TS Material Inspection", "read", doc=r.name, user=user)]



def _pending_grain_ds(roles, role_why):
    if "Grain Manager" not in roles or not frappe.has_permission("TS Deduction Suggestion", "read"):
        return []
    cls = _by_role(["Grain Manager"], role_why)
    rows = frappe.get_all("TS Deduction Suggestion", filters={"docstatus": 0},
                          fields=["name", "creation"], limit_page_length=_PENDING_SOURCE_CAP)
    return [_item("Inspection", "TS Deduction Suggestion", r.name, r.creation, cls)
            for r in rows]


def _pending_budget(roles, role_why):
    # Role-wide queues list only doctypes the user can read (an MD-only user
    # holds the role but cannot open a Post Dated Entry Request).
    out = []
    if "CEO" in roles and frappe.has_permission("TS Budget Proposal", "read"):
        cls = _by_role(["CEO"], role_why)
        for r in frappe.get_all("TS Budget Proposal", filters={"status": "Pending CEO"},
                                fields=["name", "modified"], limit_page_length=_PENDING_SOURCE_CAP):
            out.append(_item("Budget", "TS Budget Proposal", r.name, r.modified, cls))
    held = [r for r in ("CEO", "MD", "Managing Director") if r in roles]
    if held and frappe.has_permission("TS Post Dated Entry Request", "read"):
        cls = _by_role(held, role_why)
        for r in frappe.get_all("TS Post Dated Entry Request",
                                filters={"status": "Pending Approval"},
                                fields=["name", "modified"], limit_page_length=_PENDING_SOURCE_CAP):
            out.append(_item("Other", "TS Post Dated Entry Request", r.name, r.modified, cls))
    return out


def _pending_production(roles, role_why):
    if "Stores Manager" not in roles or not frappe.has_permission("TS Production Entry", "read"):
        return []
    if not cint(frappe.db.get_single_value("TS Settings", "ts_production_entry_enabled") or 0):
        return []
    cls = _by_role(["Stores Manager"], role_why)
    rows = frappe.get_all(
        "TS Production Entry",
        filters={"ts_variance_status": ["in", ["Pending Stores Release", "Pending Material Request"]]},
        fields=["name", "modified"], limit_page_length=_PENDING_SOURCE_CAP)
    return [_item("Production", "TS Production Entry", r.name, r.modified, cls)
            for r in rows]


# ═══════════════════════════════════════════════════════════════════════
#  My Requests (v2.62.0) — the session user's own Material Requests
# ═══════════════════════════════════════════════════════════════════════

_MY_STATES = ("approval", "late", "back", "decided", "draft")
_MY_DECIDED_DAYS = 14  # approved in the last 14 days
_MY_RECENT_DAYS = 30   # sent back / not sent: changed in the last 30 days
_MY_CAP = 500
_MY_WHO_SHOWN = 3


@frappe.whitelist()
def get_my_requests(state="approval", start=0, page_length=50):
    """v2.62.0 — Material Requests the session user raised or submitted, and
    where each one is: who it waits on now (the people whose "Assigned to me"
    holds it), since when, and whether it is past the approval target; plus the
    ones sent back, approved in the last 14 days, or not sent for approval yet.
    Self-scoped (no user param), read-only."""
    _guard()
    user = frappe.session.user
    state = state if state in _MY_STATES else "approval"
    start = max(cint(start), 0)
    page_length = cint(page_length)
    page_length = 50 if page_length <= 0 else min(page_length, 50)
    sla = cint(frappe.db.get_single_value("TS Settings", "approval_sla_hours")) or 0
    now = now_datetime()
    rows = frappe.db.sql(
        """SELECT d.name, d.docstatus, IFNULL(d.ts_mr_status, '') AS status,
                  IFNULL(d.ts_mr_current_step, 1) AS step, d.ts_mr_approval_route AS rule,
                  d.ts_mr_held_by AS held_by, d.ts_mr_approved_by AS approved_by,
                  d.ts_mr_approved_date AS approved_date, d.modified,
                  COALESCE(NULLIF(d.cost_center, ''), (SELECT i.cost_center FROM `tabMaterial Request Item` i
                    WHERE i.parent = d.name AND IFNULL(i.cost_center, '') != '' ORDER BY i.idx LIMIT 1)) AS cost_center
           FROM `tabMaterial Request` d
           WHERE (d.owner = %(user)s OR d.ts_mr_submitted_by = %(user)s)
             AND ((d.docstatus = 0 AND (d.ts_mr_status LIKE 'Pending%%' OR d.ts_mr_status = 'On Hold'))
               OR (d.docstatus = 0 AND IFNULL(d.ts_mr_status, '') IN ('', 'Not Submitted', 'Revised', 'Rejected')
                   AND d.modified >= %(recent)s)
               OR (d.docstatus = 1 AND d.ts_mr_approved_date >= %(decided)s))
           ORDER BY d.modified DESC
           LIMIT {cap}""".format(cap=_MY_CAP),
        {"user": user, "recent": add_to_date(now, days=-_MY_RECENT_DAYS),
         "decided": add_to_date(now, days=-_MY_DECIDED_DAYS)}, as_dict=True)
    rows = [r for r in rows if frappe.has_permission("Material Request", "read", doc=r.name, user=user)]

    last = {}
    if rows:
        for lg in frappe.db.sql(
                """SELECT parent, action, action_by, action_by_name, action_date, comment
                   FROM `tabTS Approval Log` WHERE parenttype = 'Material Request'
                     AND parentfield = 'ts_mr_log' AND parent IN %(p)s
                   ORDER BY action_date, idx""", {"p": tuple(r.name for r in rows)}, as_dict=True):
            last[lg.parent] = lg
    steps = {}
    routes = tuple({r.rule for r in rows if r.rule})
    if routes:
        strict_col = ("IFNULL(strict_step, 0)" if frappe.db.has_column("TS MR Approval Step", "strict_step")
                      else "0")
        for s in frappe.db.sql(
                "SELECT parent, step_order, role, {0} AS strict_step FROM `tabTS MR Approval Step` "
                "WHERE parent IN %(p)s ORDER BY parent, idx".format(strict_col), {"p": routes}, as_dict=True):
            steps.setdefault(s.parent, []).append(s)

    items, cfgs = [], {}
    counts = dict.fromkeys(_MY_STATES, 0)
    for r in rows:
        lg = last.get(r.name)
        if r.docstatus == 1:
            st, since, who, label = "decided", r.approved_date, [r.approved_by], "approved"
        elif r.status.startswith("Pending") or r.status == "On Hold":
            st, since = "approval", (lg.action_date if lg else None) or r.modified
            who, label = _my_waiting_on(r, steps, cfgs)
            who = [u for u in who if u != user]
            if not who and label == "with":
                label = "nobody"
        elif r.status in ("Revised", "Rejected"):
            st, since = "back", (lg.action_date if lg else None) or r.modified
            who, label = ([lg.action_by] if lg else []), "back"
        else:
            st, since, who, label = "draft", r.modified, [], "draft"
        hours = time_diff_in_hours(now, since) if since else 0
        late = 1 if (st == "approval" and r.status != "On Hold" and sla and hours >= sla) else 0
        counts[st] += 1
        counts["late"] += late
        items.append({"doc_name": r.name, "route": _route_of("Material Request", r.name),
                      "status": r.status, "state": st, "late": late, "who_label": label, "who": who,
                      "note": (cstr(strip_html(lg.comment or ""))[:160] if (st == "back" and lg) else ""),
                      "cost_center": r.cost_center or "",
                      "age_display": _hours_display(hours),
                      "since_display": format_datetime(since, "dd MMM yyyy, HH:mm") if since else "",
                      "_since": str(get_datetime(since)) if since else ""})

    # Waiting longest first while in approval; most recent first otherwise.
    shown = [it for it in items if (it["late"] if state == "late" else it["state"] == state)]
    shown.sort(key=lambda x: (x["_since"], x["doc_name"]), reverse=state not in ("approval", "late"))
    page = shown[start:start + page_length]
    names = {u for it in page for u in it["who"] if u}
    users = {u.name: u for u in frappe.get_all(
        "User", filters={"name": ["in", list(names)]}, fields=["name", "full_name", "enabled"])} if names else {}
    for it in items:
        it.pop("_since")
    for it in page:
        ppl = [cstr(users[u].full_name or u) for u in it["who"] if u in users
               and (users[u].enabled or it["who_label"] != "with")]
        if not ppl and it["who_label"] == "with":
            it["who_label"] = "nobody"
        it["who"], it["who_more"] = ppl[:_MY_WHO_SHOWN], max(len(ppl) - _MY_WHO_SHOWN, 0)
    return {"items": page, "total": len(shown), "state": state, "counts": counts, "sla_hours": sla}


def _mr_cfg(cc, cfgs):
    """The active TS CC Approval Config for a cost centre, memoised; existence
    first — get_doc on a missing name queues a "not found" message."""
    if cc not in cfgs:
        active = cc and cint(frappe.db.get_value("TS CC Approval Config", cc, "is_active"))
        cfgs[cc] = frappe.get_doc("TS CC Approval Config", cc) if active else None
    return cfgs[cc]


def _my_waiting_on(r, steps, cfgs):
    """(users, label) a pending MR waits on: the holder for On Hold, the CEO
    for a budget override, else the current step's named approvers or — when
    the cost centre names none — its role's recipients (Notification Routing
    and the skip list applied), exactly as the approver's "Assigned to me"."""
    from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
        get_cc_approvers_for_step,
    )
    if r.status == "On Hold":
        return ([r.held_by] if r.held_by else []), "held"
    if r.status == "Pending Budget Override":
        return role_recipients("CEO"), "budget"
    cur, rs = cint(r.step), steps.get(r.rule, [])
    current = next((s for s in rs if cint(s.step_order) == cur), None)
    if not current:
        return [], "with"
    cfg = _mr_cfg(r.cost_center, cfgs)
    named = get_cc_approvers_for_step(cfg, cur, route=r.rule) if cfg else []
    if named:
        return list(named), "with"
    roles = [current.role] if cint(current.strict_step) else [s.role for s in rs if cint(s.step_order) == cur]
    return list(dict.fromkeys(u for role in roles for u in role_recipients(role))), "with"


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
    for fn in (_seed_settings_field, _seed_workspace_shortcut, _seed_notification_audience,
               _seed_inspection_result_readers):
        try:
            fn()
        except Exception:
            frappe.log_error(title="notification center seeder: {0}".format(fn.__name__),
                             message=frappe.get_traceback()[:2000])


def _seed_notification_audience():
    """v2.59.0 — one TS Notification Audience row per code event. Insert-only:
    an existing row keeps the admin's ticks; only its code-owned label, team
    roles and applicable columns are refreshed."""
    from trustbit_ethanol.ts_gate_entry.ts_notification_routing import AUDIENCE_DOCTYPE, COLUMNS, EVENTS

    if not frappe.db.exists("DocType", AUDIENCE_DOCTYPE):
        return
    doc = frappe.get_single(AUDIENCE_DOCTYPE)
    have = {r.event: r for r in doc.events}
    changed = False
    for key, spec in EVENTS.items():
        code = {"event_label": spec["label"], "team_roles": ", ".join(spec.get("team_roles", ())),
                "applies": ",".join(spec["applies"])}
        row = have.get(key)
        if not row:
            doc.append("events", dict(code, event=key, **{c: int(c in spec["default"]) for c in COLUMNS}))
            changed = True
        elif any(cstr(row.get(f)) != v for f, v in code.items()):
            row.update(code)
            changed = True
    if changed:
        doc.save(ignore_permissions=True)


INSPECTION_RESULT_READERS = ("Stores User", "G2 Gate Operator")


def _seed_inspection_result_readers():
    """v2.59.0 — the inspection-result bell goes to Stores User and G2 Gate
    Operator, whom TS Material Inspection's own permissions let read it; the
    Custom DocPerm rows seeded on 2 Apr 2026 replaced those (L169). Give both
    roles read back. Upsert, read only: never adds write, never removes a row."""
    from frappe.permissions import add_permission, update_permission_property

    doctype = "TS Material Inspection"
    for role in INSPECTION_RESULT_READERS:
        if not frappe.db.exists("Role", role):
            continue
        row = {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0}
        if not frappe.db.exists("Custom DocPerm", row):
            # add_permission() runs setup_custom_perms() first (L169) and returns a
            # name only for a row it created — those carry Custom DocPerm's default
            # export=1, which these roles do not get.
            if add_permission(doctype, role, 0):
                update_permission_property(doctype, role, 0, "export", 0)
        elif not cint(frappe.db.get_value("Custom DocPerm", row, "read")):
            update_permission_property(doctype, role, 0, "read", 1)


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
