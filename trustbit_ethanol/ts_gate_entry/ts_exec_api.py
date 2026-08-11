# Copyright (c) 2026, Trustbit Software and contributors
# BBPL Approvals executive PWA (v2.31.0) — read API for the /exec frontend.
#
# This module is a THIN LENS over the existing approval engine:
#  - queue vocabulary is IMPORTED from ts_my_approvals_api.ROLE_STATUS_MAP
#    (single source of truth — never restated here, v2.9.5 lesson)
#  - every list read is permission-checked (ignore_permissions=False) so the
#    confidential-PO PQC (ts_confidential_po) and User Permissions apply to
#    the app exactly as they do to the desk — NO raw SQL in this module, ever
#  - it exposes NO mutation: the SPA calls the existing workflow endpoints
#    (ts_po_approval / ts_budget_override / ts_post_dated / ts_avp_deputy)
#
# Kill switch: TS Settings.ts_exec_pwa_enabled (Custom Field seeded by
# setup_exec_pwa.py). Fail-OPEN per L171/L172 — only an explicit stored 0
# disables; reads tabSingles directly because get_single_value casts a
# missing Check field to 0, which would silently disable the app.

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import (
    add_days,
    add_to_date,
    cint,
    cstr,
    format_datetime,
    getdate,
    now_datetime,
    time_diff_in_hours,
    today,
)

from trustbit_ethanol.ts_gate_entry.ts_my_approvals_api import (
    ROLE_STATUS_MAP,  # REUSE — the one authoritative role → status vocabulary
    _get_user_statuses,
)
from trustbit_ethanol.ts_gate_entry.ts_usage_report_api import (
    DEPT_RULES,  # REUSE — the single role → department vocabulary; never restate
    get_usage_report,
)

KILL_SWITCH_FIELD = "ts_exec_pwa_enabled"
SW_FLAG_FIELD = "ts_exec_pwa_sw_enabled"

# The ONE queue the shared map cannot express: budget overrides live on
# TS Budget Override Approval (submittable, status "Pending CEO") — a
# DIFFERENT doctype from the TS Budget Proposal entry in ROLE_STATUS_MAP.
# Both CEO and MD may act (ts_budget_override.APPROVER_ROLES = CEO/MD/System
# Manager), so both see the queue. Kept local so the desk's workspace tiles
# (which consume ROLE_STATUS_MAP) are not changed by this feature.
_EXTRA_QUEUES = {
    "TS Budget Override Approval": {
        "field": "status",
        "roles": {"CEO": ["Pending CEO"], "MD": ["Pending CEO"]},
        "extra_filters": {"docstatus": 1},
    },
}

# The 4 executive queues the app surfaces (subset of ROLE_STATUS_MAP + extra).
_APP_DOCTYPES = (
    "Purchase Order",
    "Material Request",
    "TS Post Dated Entry Request",
    "TS Budget Override Approval",
)

_LIST_FIELDS = {
    "Purchase Order": [
        "name", "supplier", "supplier_name", "grand_total", "currency",
        "transaction_date", "ts_approval_status", "ts_po_on_hold",
        "ts_purchase_category", "ts_submitted_by", "modified",
    ],
    "Material Request": [
        "name", "material_request_type", "transaction_date", "schedule_date",
        "ts_mr_status", "ts_mr_submitted_by", "modified",
    ],
    "TS Post Dated Entry Request": [
        "name", "request_type", "status", "requested_by", "request_date",
        "reason", "from_date", "to_date", "modified",
    ],
    "TS Budget Override Approval": [
        "name", "title", "reference_doctype", "reference_name",
        "source_amount", "cost_center", "breach_type", "submitted_by",
        "submitted_at", "submission_reason", "status", "modified",
    ],
}


def flag_on(field):
    """Fail-OPEN Singles read (L171/L172): only an explicit stored 0 disables.
    Shared by www/exec.py, ts_exec_notifications and ts_exec_login_alert.
    Reads the INDEPENDENT TS Exec App Settings doctype — every exec-PWA
    switch was consolidated out of TS Settings (user decision 12 Aug 2026);
    setup_exec_pwa._migrate_switch_values copies stored values across once."""
    try:
        rows = frappe.db.sql(
            """SELECT value FROM `tabSingles`
               WHERE doctype = 'TS Exec App Settings' AND field = %s LIMIT 1""",
            (field,),
        )
    except Exception:
        return True
    if not rows or rows[0][0] is None:
        return True
    return bool(cint(rows[0][0]))


# ── Who may USE the app at all (v2.34.0, user decision 10 Aug 2026) ─────────
#
# This is a DIFFERENT question from "what may they approve". Approval authority
# lives in the workflow endpoints (ts_po_approval, ts_budget_override,
# ts_post_dated), which are SHARED WITH THE DESK and are deliberately NOT gated
# here — doing so would break desk approvals for 16 Department Heads, 14
# Accounts Managers, 13 Purchase Managers and 7 AVPs on prod.
#
# Before this gate existed, `_guard()` only rejected Guest, so all 48 enabled
# System Users on prod could open the executive app; on demo a Department Head
# was measured getting a working inbox of 53 items.
EXEC_APP_ROLES = frozenset({"CEO", "MD"})
# Grantable on the ordinary User form, so people can be added later with NO
# deploy and an audit trail in tabHas Role. Seeded by setup_exec_pwa.py — a
# role that does not exist is a silent no-op (L281/L290).
EXEC_APP_ROLE = "BBPL Exec App"


def exec_app_allowed(user=None):
    """May this user open the BBPL Approvals app? FAIL-CLOSED.

    NEVER raises: it is called from the www context module that also renders
    the GUEST login screen, so an exception here would take sign-in down for
    everyone. Administrator is allowed unconditionally — it is the break-glass
    account and must never be lockable out of its own app.
    """
    try:
        u = user or frappe.session.user
        if not u or u == "Guest":
            return False
        if u == "Administrator":
            return True
        roles = set(frappe.get_roles(u))
        return bool(roles & EXEC_APP_ROLES) or EXEC_APP_ROLE in roles
    except Exception:
        frappe.clear_messages()
        return False


def _check_session():
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _app_gate():
    """The access boundary for every PWA-owned endpoint. Separate from the kill
    switch: 'switched off for everyone' and 'you are not on the list' are
    different states and must not share a message."""
    if not exec_app_allowed():
        frappe.throw(
            _("BBPL Approvals is limited to approved users."),
            frappe.PermissionError,
        )


def _guard():
    _check_session()
    if not flag_on(KILL_SWITCH_FIELD):
        frappe.throw(_("BBPL Approvals is temporarily disabled."))
    _app_gate()


def _status_field(doctype):
    if doctype in ROLE_STATUS_MAP:
        return ROLE_STATUS_MAP[doctype]["field"]
    return _EXTRA_QUEUES[doctype]["field"]


def _extra_queue_statuses(doctype, user):
    cfg = _EXTRA_QUEUES[doctype]
    user_roles = set(frappe.get_roles(user))
    statuses = set()
    for role, role_statuses in cfg["roles"].items():
        if role in user_roles:
            statuses.update(role_statuses)
    return sorted(statuses)


def _mr_estimated_values(names):
    """SUM(items.amount) per MR (L160 — MR has no grand_total). The parent
    list is already permission-checked, so a child read scoped to exactly
    those vetted parents may skip the (parent-less) child permission model."""
    if not names:
        return {}
    rows = frappe.get_all(
        "Material Request Item",
        filters={"parent": ["in", names], "parenttype": "Material Request"},
        fields=["parent", "sum(amount) as total"],
        group_by="parent",
        ignore_permissions=True,
    )
    return {r.parent: float(r.total or 0) for r in rows}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_inbox():
    """Unified pending-approvals list for the session user, oldest first.

    Role-driven via ROLE_STATUS_MAP: a user with no approver role gets an
    empty inbox. CEO additionally sees POs at 'Awaiting Send to …' via the
    tamper-guarded ts_can_send_to_md flag (set at exactly one site in
    ts_po_approval.py:1101, cleared at every exit path) — that state is in
    NOBODY's status queue although only the CEO can act on it.
    """
    _guard()
    user = frappe.session.user
    is_ceo = "CEO" in frappe.get_roles(user)

    items = []
    queues = []
    for doctype in _APP_DOCTYPES:
        if doctype in ROLE_STATUS_MAP:
            statuses = _get_user_statuses(doctype, user)
        else:
            statuses = _extra_queue_statuses(doctype, user)

        field = _status_field(doctype)
        filters = {}
        or_filters = None

        if doctype in ("Purchase Order", "Material Request"):
            # Pending states only exist on drafts; Approved submits the doc.
            filters["docstatus"] = 0
        if doctype in _EXTRA_QUEUES:
            filters.update(_EXTRA_QUEUES[doctype].get("extra_filters") or {})

        if doctype == "Purchase Order" and is_ceo:
            or_filters = [
                [field, "in", statuses or [""]],
                ["ts_can_send_to_md", "=", 1],
            ]
        elif statuses:
            filters[field] = ["in", statuses]
        else:
            continue  # no role on this queue → skip entirely

        try:
            rows = frappe.get_list(
                doctype,
                filters=filters,
                or_filters=or_filters,
                fields=_LIST_FIELDS[doctype],
                order_by="modified asc",
                # 200, not 50: the chip counts read len(rows), so the cap must
                # sit far above any realistic executive queue or the counts lie
                # (ui-designer note B; demo max observed queue = 38).
                limit_page_length=200,
                ignore_permissions=False,  # PQC + User Permissions apply
            )
        except Exception:
            # Fail-closed (empty queue) but NEVER silently: a permission
            # misconfiguration must not present as "nothing pending".
            frappe.log_error(
                title="exec inbox queue failed",
                message="doctype={0} user={1}\n{2}".format(
                    doctype, user, frappe.get_traceback()
                ),
            )
            frappe.clear_messages()
            rows = []

        mr_values = (
            _mr_estimated_values([r.name for r in rows])
            if doctype == "Material Request"
            else {}
        )
        for r in rows:
            card = dict(r)
            card["doctype"] = doctype
            card["status"] = r.get(field)
            if doctype == "Material Request":
                card["estimated_value"] = mr_values.get(r.name)
            items.append(card)
        queues.append({
            "doctype": doctype,
            "count": len(rows),
            "statuses": statuses,
        })

    items.sort(key=lambda d: str(d.get("modified") or ""))
    return {
        "enabled": True,
        "user": user,
        "queues": queues,
        "items": items,
        "total": len(items),
    }


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_my_actions(search=None):
    """Self-scoped approval history for the History tab: the session user's
    OWN TS Approval Log rows (PO/MR actions they performed). Hard-filtered by
    action_by = session user — no parameter accepts another user, so this can
    never read someone else's trail. ignore_permissions=True is safe under
    that self-scoping: every row IS the user's own past action (they were a
    participant on the document when they acted); tapping through to the
    document still goes via get_document, which permission-fences.

    `search` is SERVER-side on purpose. The default page is the 50 most recent
    actions, but a real executive has far more (799 for the prod CEO on
    10 Aug 2026), so a client-side filter over the loaded page would answer
    "no results" for ~94% of his own history — confidently and wrongly.

    ⚠ The search widens WHICH of the user's rows match; it must never widen
    WHOSE. `or_filters` is safe for that: frappe's DatabaseQuery collects it
    into `grouped_or_conditions`, wraps it in parentheses and ANDs it with the
    normal filters (db_query.py:291-294), so the final WHERE stays
    `action_by = <me> AND parenttype IN (...) AND (<search terms>)`.
    Do NOT hand-build this as a raw OR string.
    """
    _guard()
    user = frappe.session.user

    filters = {
        "action_by": user,
        "parenttype": ["in", ["Purchase Order", "Material Request"]],
    }
    or_filters = None
    limit = 50

    term = (cstr(search) or "").strip()[:100]  # bounded: no unbounded LIKE input
    if term:
        # Escape LIKE metacharacters so the box behaves like a search box:
        # unescaped, "%" matches EVERYTHING (a user typing it gets the whole
        # cap back) and "_" matches any single character, while a literal "%"
        # in a comment ("approved 100%") becomes unsearchable. Backslash first,
        # or it would double-escape the escapes. Not a security issue — the
        # action_by scope is a separate AND — purely correctness.
        esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = "%{0}%".format(esc)
        # Document number is what an executive actually searches for; the rest
        # are cheap bonuses on the same row.
        or_filters = [
            ["parent", "like", like],
            ["comment", "like", like],
            ["action", "like", like],
            ["to_state", "like", like],
        ]
        # A search that only looked at the newest 50 would be the very bug this
        # parameter exists to fix.
        limit = 200

    rows = frappe.get_all(
        "TS Approval Log",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "parent", "parenttype", "action", "from_state", "to_state",
            "action_date", "comment", "po_amount",
        ],
        order_by="action_date desc",
        limit_page_length=limit,
        ignore_permissions=True,
    )
    return {
        "user": user,
        "search": term,
        # The UI must be able to say "your 50 most recent" rather than implying
        # this is everything — and to warn when a search itself hit the cap.
        "limit": limit,
        "capped": len(rows) >= limit,
        "total_actions": frappe.db.count(
            "TS Approval Log",
            {"action_by": user, "parenttype": ["in", ["Purchase Order", "Material Request"]]},
        ),
        "items": [
            {
                "doctype": r.parenttype,
                "name": r.parent,
                "action": r.action,
                "from_state": r.from_state,
                "to_state": r.to_state,
                "action_date": str(r.action_date or ""),
                "comment": r.comment,
                "amount": r.po_amount,
            }
            for r in rows
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
#  OVERVIEW TAB (v2.33.0) — company-wide read-only executive cards
#
#  Named "Overview", not "Today": the approvals pipeline is a 150-day backlog,
#  so a "Today" label would promise a freshness the data cannot deliver.
#
#  TWO cards in this release (user's scope cut): approvals pipeline + gate.
#  Spend and ethanol stock are deferred — each is a whole function here, not
#  a stitch, so adding them later touches nothing that ships now.
# ══════════════════════════════════════════════════════════════════════════

OVERVIEW_KILL_SWITCH = "ts_exec_today_enabled"

# Stages whose documents legitimately live in ANOTHER queue rather than being
# abandoned — display metadata only, so the UI can say "actionable in the
# Budget queue" instead of implying total neglect.
_STAGE_COMPANION = {"Pending Budget Override": "TS Budget Override Approval"}

# Stages that ROLE_STATUS_MAP does not describe but which a real role can still
# act on. "Pending Stores Manager" has 15 enabled Stores Managers and a complete
# approve/reject workflow in ts_mr_transfer.py — it is simply absent from the
# map, which only lists Stores Manager under TS Production Entry. Without this,
# 5 live MRs were reported to the CEO as "in nobody's queue", which is a false
# alarm about documents that do have an owner (live-data audit, 9 Aug).
_EXTRA_ROUTED_STAGES = {"Material Request": ("Pending Stores Manager",)}

# Who may open the company-wide Overview. Deliberately OWNED HERE and NOT
# borrowed from stores_receiving_api.READ_ROLES: that set exists for a stores
# receiving dashboard and includes Stores User, so on demo it admitted HR,
# electrical/instrumentation and accounts-only accounts to an executive screen.
# Worse, it is a drift vector — whoever next widens READ_ROLES for a stores
# reason would silently widen this, with no reviewer in the loop.
#
# ⚠ NARROWER than app access on purpose (user decision, 10 Aug 2026). Holding
# EXEC_APP_ROLE lets you into the app — your OWN approvals and history — but
# NOT into the company-wide figures (total pending, spend-adjacent amounts,
# gate activity). IT Head and System Manager were removed here too: they are
# already denied by the outer app gate, and leaving them listed implied an
# access level they do not have.
OVERVIEW_ROLES = frozenset({"CEO", "MD", "Administrator"})

# Statuses meaning "this vehicle is no longer inside".
# ⚠ Config-derived: TS Token's status options come from a Property Setter
# (setup_two_pass_gates), NOT the doctype JSON — validated at runtime below.
#
# ⚠ "Plant Exited" is DELIBERATELY ABSENT — it means "left the plant area, still
# on campus, awaiting G1 final exit", i.e. still inside. Every other module in
# this app agrees (vehicles_in_plant_api.ON_PREMISES_STATUSES, ts_dashboard_ceo,
# ts_dashboard_md, ts_dashboard_gate, ts_user_management, ts_live_vehicle_tracker).
# An earlier version of this tuple excluded it, which made this card report a
# LOWER truck count than the desk dashboards the same CEO and MD already use —
# two screens, one company, two numbers, no explanation (predictor audit, 9 Aug).
# "Token Generated" is retained for clarity even though the queries also require
# g2_link_time IS NOT NULL, which no such row has.
_TOKEN_EXITED = ("Token Generated", "Campus Exited", "Exited")

_GRN_OVERDUE_HOURS = 6
_HELD_LIST_CAP = 50
_QUEUE_FETCH_CAP = 2000


def _overview_audience_gate():
    """`www/exec.py` has NO page-level role gate, so this endpoint needs its
    own — it returns company-wide figures, not just the caller's own queue."""
    if not (OVERVIEW_ROLES & set(frappe.get_roles(frappe.session.user))):
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _log_once(key, title, message):
    """Bounded diagnostics. Each get_overview call has 6 independent except
    branches, and the endpoint allows 60 calls/min — a persistent failure could
    write ~360 Error Log rows per minute per user, into a log that is already a
    live operational concern on this project. One row per user+key per 5 minutes
    is plenty to diagnose, and losing the duplicates costs nothing."""
    try:
        ckey = "exec_ov_err:{0}:{1}".format(frappe.session.user, key)
        cache = frappe.cache()
        if cache.get_value(ckey):
            return
        cache.set_value(ckey, 1, expires_in_sec=300)
        # Mirror into the PROCESS-LOCAL cache too. get_value() memoises its MISS
        # as frappe.local.cache[key] = None, and set_value() with expires_in_sec
        # writes ONLY to redis — so a second call in the SAME process keeps
        # reading the memoised None and the throttle never fires. Across HTTP
        # requests redis alone would do (frappe.local resets per request), but a
        # scheduled job or any in-process loop would bypass it entirely.
        try:
            frappe.local.cache[cache.make_key(ckey)] = 1
        except Exception:
            pass
    except Exception:
        pass  # never let the throttle itself suppress a genuine first report
    frappe.log_error(title=title, message=message)


def overview_visible():
    """Should the Overview TAB be rendered for this user? Nav affordance only —
    `get_overview` re-checks everything server-side, so this can never be the
    security boundary.

    Fails CLOSED and NEVER raises: it is called from the www context module,
    which also renders the Guest login screen. An exception here would take the
    whole sign-in page down, not just a tab.
    """
    try:
        if frappe.session.user in (None, "", "Guest"):
            return False
        if not flag_on(OVERVIEW_KILL_SWITCH):
            return False
        return bool(OVERVIEW_ROLES & set(frappe.get_roles(frappe.session.user)))
    except Exception:
        frappe.clear_messages()
        return False


def _resolve_as_of(date=None):
    """Return (as_of_datetime, business_date, is_backdated).

    A `date` override is the ONLY way to UAT this tab: demo's newest gate data
    is days stale, so every 'today' card reads zero there. Rejects the future
    and anything unparseable as a ValidationError — NEVER a bare ValueError,
    which has no http_status_code and would make Frappe answer 500 and dump
    every frame's local into the Error Log (L379)."""
    today = frappe.utils.getdate()
    if not date:
        return now_datetime(), today, False
    try:
        d = frappe.utils.getdate(date)
    except Exception:
        raise frappe.ValidationError(_("Invalid date."))
    # getdate() does NOT raise for every bad input — is_invalid_date_string()
    # short-circuits "0000-00-00", "0001-01-01" and ANY non-str type, returning
    # None instead. Without this guard `None > date` raises a bare TypeError,
    # which carries no http_status_code: Frappe answers 500 and log_error_snapshot
    # writes get_traceback(with_context=True) — every frame's locals — into
    # tabError Log, at up to the rate limit per minute (L379). Verified live:
    # 7 of 10 malformed inputs took this path before the guard existed.
    if not d:
        raise frappe.ValidationError(_("Invalid date."))
    if d > today:
        raise frappe.ValidationError(_("Date cannot be in the future."))
    if d == today:
        return now_datetime(), today, False
    return frappe.utils.get_datetime("{0} 23:59:59".format(d)), d, True


def _age_bucket(hours):
    """Pure, DB-free, so every boundary is a deterministic unit test."""
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return "unknown"
    if h < 24:
        return "d0_1"
    if h < 72:
        return "d1_3"
    if h < 168:
        return "d3_7"
    if h < 720:
        return "d7_30"
    return "d30_plus"


def _routed_stages(doctype):
    """The set of stages that at least one ROLE can actually see.

    DERIVED, never hardcoded (L221): status enums are config-driven and are
    force-reverted on every migrate by _enforce_status_option_enums, so a
    literal list would rot silently and the '26 unrouted' number would drift
    into fiction. This is what makes 'unrouted' an OUTPUT, not a constant."""
    from trustbit_ethanol.ts_gate_entry.ts_my_approvals_api import ROLE_STATUS_MAP

    cfg = ROLE_STATUS_MAP.get(doctype) or _EXTRA_QUEUES.get(doctype) or {}
    out = set()
    for statuses in (cfg.get("roles") or {}).values():
        out.update(statuses)
    out.update(_EXTRA_ROUTED_STAGES.get(doctype, ()))
    # Compare CASE-INSENSITIVELY. MySQL's default collation is case-insensitive,
    # so a mis-cased status is still returned by every real queue query — the
    # document genuinely IS in the approver's inbox. A case-sensitive Python
    # `in` test would report it as unrouted, so this card would contradict the
    # Inbox tab of the same app, in the same session. That row exists on demo:
    # PR-MEC-26-00028 carries "Pending Ceo" vs the config's "Pending CEO", and
    # the CEO's inbox does list it (live-data audit, 9 Aug).
    return {s.casefold() for s in out if s}


def _pipeline_queue(doctype, status_field, filters, as_of, sla_cutoff):
    """ONE lean row fetch per queue; every metric derived in Python.

    Deliberate: stage counts + aging + oldest + breaches + unrouted detection
    would otherwise be ~8 separate aggregates that can disagree with each
    other. One fetch of 3 permlevel-0 columns is cheaper AND single-sourced.
    ignore_permissions=False so the confidential-PO PQC applies for free —
    frappe.get_all would silently override it (L381).
    """
    rows = frappe.get_list(
        doctype,
        filters=filters,
        fields=["name", "{0} as stage".format(status_field), "modified"],
        order_by="modified asc",
        limit_page_length=_QUEUE_FETCH_CAP,
        ignore_permissions=False,
    )

    routed = _routed_stages(doctype)
    stages, aging = {}, {"d0_1": 0, "d1_3": 0, "d3_7": 0, "d7_30": 0, "d30_plus": 0}
    past_sla = 0
    oldest = None

    for r in rows:
        hours = time_diff_in_hours(as_of, r.modified)
        bucket = _age_bucket(hours)
        if bucket in aging:
            aging[bucket] += 1
        if r.modified and r.modified < sla_cutoff:
            past_sla += 1
        st = stages.setdefault(r.stage or "", {"count": 0, "oldest_hours": 0.0})
        st["count"] += 1
        st["oldest_hours"] = max(st["oldest_hours"], hours)
        if oldest is None or hours > oldest["hours"]:
            oldest = {"name": r.name, "stage": r.stage or "", "hours": hours}

    stage_rows, unrouted = [], []
    for stage, agg in sorted(stages.items(), key=lambda kv: -kv[1]["count"]):
        is_routed = (stage or "").casefold() in routed
        row = {
            "stage": stage,
            "count": agg["count"],
            "days": round(agg["oldest_hours"] / 24.0, 1),
            "routed": is_routed,
            "companion": _STAGE_COMPANION.get(stage),
        }
        stage_rows.append(row)
        if not is_routed:
            unrouted.append(dict(row, doctype=doctype))

    return {
        "doctype": doctype,
        "ok": True,
        "error": None,
        "total": len(rows),
        # A guard, never a display cap: if it ever fires the UI must say so
        # rather than printing a truncated len() as if it were a total.
        "capped": len(rows) >= _QUEUE_FETCH_CAP,
        "stages": stage_rows,
        "aging": aging,
        "past_sla": past_sla,
        "oldest": (
            {"name": oldest["name"], "stage": oldest["stage"],
             "days": round(oldest["hours"] / 24.0, 1)} if oldest else None
        ),
    }, unrouted


def _card_approvals(as_of):
    """Card 1 — company-wide approvals pipeline.

    The hero counts PO + MR ONLY. The pending TS Budget Override Approval docs
    are deliberately excluded from the total: 16 of them ARE the reason 16 POs
    sit at 'Pending Budget Override' and are already inside the PO count.
    Adding them would double-count the same business item.
    """
    from trustbit_ethanol.ts_gate_entry.ts_my_approvals_api import ROLE_STATUS_MAP

    sla = cint(frappe.db.get_single_value("TS Settings", "approval_sla_hours")) or 24
    # Python cutoff, never SQL NOW(): SQL NOW() runs 5.5h behind IST here, which
    # would silently turn a 24h SLA into 29.5h (L365).
    sla_cutoff = add_to_date(as_of, hours=-sla)

    queues, unrouted, errors = [], [], []
    spec = (
        ("Purchase Order", ROLE_STATUS_MAP["Purchase Order"]["field"],
         {"docstatus": 0, "ts_approval_status": ["like", "Pending%"]}),
        ("Material Request", ROLE_STATUS_MAP["Material Request"]["field"],
         {"docstatus": 0, "ts_mr_status": ["like", "Pending%"]}),
    )
    for doctype, field, filters in spec:
        try:
            q, un = _pipeline_queue(doctype, field, filters, as_of, sla_cutoff)
            queues.append(q)
            unrouted.extend(un)
        except Exception:
            # get_list THROWS PermissionError (it does not return []) for a
            # doctype the caller cannot read — one queue failing must never
            # blank the card, and must never look like "nothing pending".
            _log_once(
                "queue:{0}".format(doctype),
                "exec overview queue failed",
                "doctype={0} user={1}\n{2}".format(
                    doctype, frappe.session.user, frappe.get_traceback()),
            )
            frappe.clear_messages()
            errors.append(doctype)
            queues.append({"doctype": doctype, "ok": False,
                           "error": "Couldn't read this queue.", "total": 0,
                           "stages": [], "aging": {}, "past_sla": 0,
                           "oldest": None, "capped": False})

    total = sum(q["total"] for q in queues if q["ok"])
    over_30 = sum((q["aging"] or {}).get("d30_plus", 0) for q in queues if q["ok"])
    oldest = max(
        (q["oldest"] for q in queues if q["ok"] and q["oldest"]),
        key=lambda o: o["days"], default=None,
    )

    return {
        "ok": True,
        "error": None,
        # PARTIAL is a real state: a silently low total is the cardinal sin
        # wearing a different costume.
        "partial": bool(errors),
        "state": "ok" if total else "empty",
        "total_pending": total,
        "over_30_days": over_30,
        "oldest": oldest,
        "sla_hours": sla,
        "queues": queues,
        "unrouted": {
            "count": sum(u["count"] for u in unrouted),
            "items": sorted(unrouted, key=lambda u: -u["count"]),
        },
    }


def _orphaned_and_hidden(as_of):
    """The two extra classes of invisible work the user opted into.

    orphaned — parked at 'Pending Budget Override' with NO live override
               document at all: they cannot move without intervention and
               appear on no screen anywhere.
    hidden   — docstatus=1 but still carrying a Pending status, which every
               existing query's docstatus=0 filter hides.
    """
    # `partial` is part of the contract here, not just on the queue card: with
    # half this scan dead the old code reported hidden=8 (true value 22) and
    # orphaned=0 as plain fact. A halved number presented as whole is exactly
    # what `partial` exists to prevent (code-tester D2d/D2e).
    out = {
        "orphaned": {"count": 0, "items": []},
        "hidden": {"count": 0, "items": []},
        "partial": False,
    }
    try:
        live = set(frappe.get_all(
            "TS Budget Override Approval",
            filters={"docstatus": 1, "status": ["like", "Pending%"]},
            pluck="reference_name", limit_page_length=0, ignore_permissions=True,
        ) or [])
    except Exception:
        _log_once("override_set", "exec overview override scan failed",
                  frappe.get_traceback())
        frappe.clear_messages()
        out["partial"] = True
        return out

    # Per-doctype, so one unreadable queue degrades ONLY its own half and says so.
    for doctype, field in (("Purchase Order", "ts_approval_status"),
                           ("Material Request", "ts_mr_status")):
        try:
            parked = frappe.get_list(
                doctype,
                filters={"docstatus": 0, field: "Pending Budget Override"},
                fields=["name", "modified"], limit_page_length=200,
                ignore_permissions=False,
            )
            for r in parked:
                if r.name not in live:
                    out["orphaned"]["items"].append({
                        "doctype": doctype, "name": r.name,
                        "days": round(time_diff_in_hours(as_of, r.modified) / 24.0, 1),
                    })

            hidden = frappe.get_list(
                doctype,
                filters={"docstatus": 1, field: ["like", "Pending%"]},
                fields=["name"], limit_page_length=200, ignore_permissions=False,
            )
            out["hidden"]["count"] += len(hidden)
        except Exception:
            _log_once("orphan:{0}".format(doctype),
                      "exec overview orphan scan failed",
                      "doctype={0} user={1}\n{2}".format(
                          doctype, frappe.session.user, frappe.get_traceback()))
            frappe.clear_messages()
            out["partial"] = True

    out["orphaned"]["items"].sort(key=lambda x: -x["days"])
    # Set OUTSIDE any try: previously this lived on the success path, so a
    # failure left count=0 while items held real rows — and the UI gates the
    # whole block on count, so genuinely stuck documents rendered as nothing.
    out["orphaned"]["count"] = len(out["orphaned"]["items"])
    return out


def _token_status_vocab_ok():
    """Config-drift alarm. TS Token's status options live in a Property Setter,
    so a hardcoded exited-set can silently stop matching. False ⇒ the UI warns
    instead of quietly reporting a wrong number."""
    try:
        opts = (frappe.get_meta("TS Token").get_field("status").options or "").split("\n")
        # Must cover the ON-SITE literals too, not just _TOKEN_EXITED. Since
        # "Plant Exited" was removed from the exited tuple it became a
        # load-bearing string in three separate queries (in-plant, and both
        # grn_overdue arms) that this alarm no longer watched — a rename there
        # would silently skew the counts with the drift alarm still green.
        watched = set(_TOKEN_EXITED) | {"Plant Exited"}
        return all(s in opts for s in watched if s != "Exited")
    except Exception:
        return False


def _card_gate(as_of):
    """Card 2 — gate activity.

    ⚠ NEVER reuse vehicles_in_plant_api.get_vehicles_in_plant(): it is 311 ms
    (a per-token get_value loop), throws PermissionError for CEO, and its
    ON_PREMISES_STATUSES starts at 'G2 Entered' — a status that NEVER occurs
    because ts_two_pass_gates_enabled=0 on both servers, so it silently drops
    ~40% of the trucks.
    """
    card = {"ok": True, "error": None, "state": "ok"}
    try:
        cutoff = add_to_date(as_of, hours=-24)
        # g2_link_time, not g1_entry_time: the latter is stamped in
        # TSToken.before_insert (i.e. at token creation) so EVERY row has one.
        rows = frappe.db.sql("""
            SELECT SUM(CASE WHEN t.g2_link_time >= %(cutoff)s THEN 1 ELSE 0 END) AS active,
                   COUNT(*) AS total
            FROM `tabTS Token` t
            WHERE t.entry_type = 'Material'
              AND t.g2_link_time IS NOT NULL
              AND t.g2_link_time <= %(as_of)s
              AND t.docstatus <> 2
              AND t.status NOT IN %(exited)s
        """, {"cutoff": cutoff, "as_of": as_of, "exited": _TOKEN_EXITED}, as_dict=True)
        r = rows[0] if rows else {}
        active = cint(r.get("active"))
        total = cint(r.get("total"))
        oldest_stale = frappe.db.sql("""
            SELECT MIN(t.g2_link_time) FROM `tabTS Token` t
            WHERE t.entry_type = 'Material' AND t.g2_link_time IS NOT NULL
              AND t.g2_link_time < %(cutoff)s AND t.docstatus <> 2
              AND t.status NOT IN %(exited)s
        """, {"cutoff": cutoff, "exited": _TOKEN_EXITED})
        card["in_plant"] = {
            # Wording matters: the stale ones are UNCLOSED RECORDS, not trucks
            # parked in the yard. An MD who reads "393 in plant", walks outside
            # and sees an empty yard never opens this tab again.
            "arrived_today": active,
            "no_exit_recorded": max(total - active, 0),
            "oldest_stale": str((oldest_stale[0][0] if oldest_stale and oldest_stale[0] else "") or ""),
            "vocab_ok": _token_status_vocab_ok(),
        }
    except Exception:
        _log_once("in_plant", "exec overview gate in-plant failed",
                  frappe.get_traceback())
        frappe.clear_messages()
        card["in_plant"] = None
        card["ok"] = False
        card["error"] = "Couldn't read gate activity."

    # Grain held at G2 — reuse ts_grain_defer.get_section_g verbatim. It already
    # self-gates on user_sees_confidential and exposes no PO/supplier/amount.
    try:
        from trustbit_ethanol.ts_gate_entry.ts_grain_defer import get_section_g

        held = get_section_g() or []
        truncated = len(held) >= _HELD_LIST_CAP
        items = []
        for h in held[:_HELD_LIST_CAP]:
            # ⚠ h["age_hours"] is computed with SQL NOW() and is therefore
            # ~5.5h UNDERSTATED (a truck held 7h reports 1.5h). Recompute from
            # tare_time. Existing live display bug, logged not fixed.
            hours = time_diff_in_hours(as_of, h.get("tare_time")) if h.get("tare_time") else 0
            items.append({
                "token": h.get("token"), "vehicle": h.get("vehicle"),
                "material": h.get("material"), "hours": round(hours, 1),
                "released": cint(h.get("released")),
                # Section G deliberately mixes TWO populations (v2.30.0):
                # status 'Tare Weighed' = still standing at the gate, and
                # released-and-exited = long gone but still owing a GRN.
                # Counting both as "held at the gate" is false the moment
                # Stores releases anyone, so the split is carried per row.
                "departed": (h.get("status") or "") in ("Plant Exited", "Campus Exited"),
            })
        waiting = [i for i in items if not i["departed"]]
        departed = [i for i in items if i["departed"]]
        card["grain_held"] = {
            # `count` is now STILL-AT-THE-GATE only — the number the label claims.
            "count": len(waiting),
            "over_6h": len([i for i in waiting if i["hours"] > _GRN_OVERDUE_HOURS]),
            "oldest_hours": round(max([i["hours"] for i in waiting], default=0), 1),
            # Reported separately rather than folded into the hold count.
            "departed_owing_grn": len(departed),
            "truncated": truncated,
            "items": items,
            # get_section_g does NOT read this flag, so a hold could be shown
            # that is not actually being enforced.
            "enforced": bool(flag_on("ts_grain_defer_enabled")),
        }
    except Exception:
        _log_once("grain", "exec overview grain-held failed",
                  frappe.get_traceback())
        frappe.clear_messages()
        card["grain_held"] = None

    # Released to exit but the receipt is still owed. Based on the v2.30.0
    # escalation predicate MINUS the fire-once stamp (ts_pre_grn_escalated_at is
    # never cleared and is NULL for a truck that is overdue but unscanned —
    # wrong in both directions).
    #
    # ⚠ BOTH release paths, not just grain. v2.30.0 shipped two ways for Stores
    # to let a vehicle leave before its GRN: the grain pre-GRN release, and
    # Non-RM post-exit receivability. Counting only grain made this card report
    # a reassuring 0 while 12 Non-RM trucks on demo had exited owing a receipt.
    # The 6h BELL stays grain-only (recorded user decision, 7 Aug) — that is a
    # notification-scope choice and is not a licence for the dashboard to
    # under-report. The two arms are UNIONed and de-duplicated by token, since
    # one vehicle can in principle carry both flags.
    try:
        overdue = frappe.db.sql("""
            SELECT t.name, t.ts_pre_grn_exit_approved_at AS since, 'grain' AS kind
            FROM `tabTS Token` t
            JOIN `tabTS Gate Entry` ge
              ON ge.token_number = t.name AND ge.docstatus = 1 AND ge.ts_po_deferred = 1
            WHERE t.ts_pre_grn_exit_approved = 1
              AND t.status IN ('Plant Exited', 'Campus Exited')
              AND (t.purchase_receipt IS NULL OR t.purchase_receipt = '')
              AND t.ts_pre_grn_exit_approved_at IS NOT NULL
              AND t.ts_pre_grn_exit_approved_at <= %(as_of)s
            UNION ALL
            SELECT t.name, t.non_rm_exit_approved_at AS since, 'non_rm' AS kind
            FROM `tabTS Token` t
            WHERE t.non_rm_exit_approved = 1
              AND t.status IN ('Plant Exited', 'Campus Exited')
              AND (t.purchase_receipt IS NULL OR t.purchase_receipt = '')
              AND t.non_rm_exit_approved_at IS NOT NULL
              AND t.non_rm_exit_approved_at <= %(as_of)s
              AND t.docstatus <> 2
        """, {"as_of": as_of}, as_dict=True)

        seen, rows = set(), []
        for o in overdue:
            if o.name in seen:
                continue
            seen.add(o.name)
            rows.append(o)

        late = [
            (o, time_diff_in_hours(as_of, o.since))
            for o in rows if o.since
        ]
        late = [(o, h) for o, h in late if h >= _GRN_OVERDUE_HOURS]
        card["grn_overdue"] = {
            "count": len(late),
            "threshold_hours": _GRN_OVERDUE_HOURS,
            "oldest_hours": round(max([h for _o, h in late]), 1) if late else None,
            # Split so the UI never has to guess which release path is behind it.
            "grain": len([1 for o, _h in late if o.kind == "grain"]),
            "non_rm": len([1 for o, _h in late if o.kind == "non_rm"]),
        }
    except Exception:
        _log_once("grn", "exec overview grn-overdue failed",
                  frappe.get_traceback())
        frappe.clear_messages()
        card["grn_overdue"] = None

    return card


# POST-only although this is a pure read: the app's single network chokepoint
# (data/session.js) always POSTs, so it costs nothing, and it keeps company-wide
# figures out of GET access logs and any intermediary cache.
#
# ⚠ This does NOT close the L376 mapper trampoline. `make_mapped_doc` is itself a
# bare @frappe.whitelist() that calls method(source_name), and is_valid_http_method
# is enforced on the TOP-LEVEL cmd only — so a 1-positional function like this one
# stays GET-reachable through it regardless of `methods=`. That is harmless here
# and the decorator is still worth having: this endpoint writes nothing, every gate
# (_guard → kill switch → audience) runs INSIDE the body where the verb cannot
# matter, and a cross-origin GET cannot read the JSON response.
@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def get_overview(date=None):
    """Company-wide executive Overview cards. READ-ONLY — performs no writes.

    Live, uncached: the whole payload is a few milliseconds of DB work, and a
    dashboard that is an hour stale is worse than no dashboard.
    """
    _guard()
    if not flag_on(OVERVIEW_KILL_SWITCH):
        frappe.throw(_("Overview is switched off."))
    _overview_audience_gate()

    as_of, business_date, is_backdated = _resolve_as_of(date)

    from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential

    approvals = _card_approvals(as_of)
    extras = _orphaned_and_hidden(as_of)
    approvals["orphaned"] = extras["orphaned"]
    approvals["hidden"] = extras["hidden"]
    # A degraded orphan/hidden scan makes the WHOLE card partial — those counts
    # are rendered as fact alongside the queue totals.
    approvals["partial"] = bool(approvals.get("partial") or extras.get("partial"))

    return {
        "enabled": True,
        "user": frappe.session.user,
        "date": str(business_date),
        "as_of": str(as_of),
        "as_of_display": format_datetime(as_of, "d MMM, h:mm a"),
        "is_backdated": is_backdated,
        # Surfaced so a non-allow-listed viewer sees "partial view" rather than
        # a silently smaller number — the exact failure mode of the existing
        # CEO/MD desk dashboards, which apply no confidentiality filter at all.
        "confidential_scope": "full" if user_sees_confidential("Purchase Order") else "restricted",
        "approvals": approvals,
        "gate": _card_gate(as_of),
    }


def _budget_override_context(doc, user):
    roles = set(frappe.get_roles(user))
    actionable = (
        doc.docstatus == 1
        and (doc.status or "") == "Pending CEO"
        and bool(roles & {"CEO", "MD", "System Manager"})
    )
    return {
        "enabled": True,
        "can_approve": actionable,
        "can_reject": actionable,
        "is_on_hold": False,
        "approval_chain": [],
        "reason_required_for_reject": True,
        "reject_reason_min_length": 10,
    }


def _post_dated_context(doc, user):
    roles = set(frappe.get_roles(user))
    is_approver = bool(roles & {"CEO", "MD"})
    not_self = (doc.requested_by or doc.owner) != user
    actionable = (doc.status or "") == "Pending Approval" and is_approver and not_self
    return {
        "enabled": True,
        "can_approve": actionable,
        "can_reject": actionable,
        "is_on_hold": False,
        "approval_chain": [],
        "reason_required_for_reject": True,
    }


@frappe.whitelist()
@rate_limit(limit=120, seconds=60)
def get_document(doctype, docname):
    """Single-document payload for the detail screen: header fields, item
    lines, approval log and the per-user capability context that drives the
    action buttons. Opens with an explicit doc-level permission fence — the
    engine's get_approval_context deliberately has none of its own."""
    _guard()
    if doctype not in _APP_DOCTYPES:
        frappe.throw(_("Unsupported doctype."), frappe.PermissionError)
    frappe.has_permission(doctype, "read", doc=docname, throw=True)

    doc = frappe.get_doc(doctype, docname)
    user = frappe.session.user
    payload = {"doctype": doctype, "name": doc.name}

    if doctype in ("Purchase Order", "Material Request"):
        # Lazy import: ts_po_approval is a heavy locked module — same pattern
        # the notification hub uses for the WhatsApp adapter.
        from trustbit_ethanol.ts_gate_entry.ts_po_approval import (
            get_approval_context,
        )

        payload["context"] = get_approval_context(doctype, docname)
        payload["items"] = [
            {
                "item_code": d.item_code,
                "item_name": d.item_name,
                "qty": d.qty,
                "uom": d.uom,
                "rate": getattr(d, "rate", None),
                "amount": getattr(d, "amount", None),
                "cost_center": getattr(d, "cost_center", None),
            }
            for d in (doc.get("items") or [])
        ]
        # The TS Approval Log child table hangs off DIFFERENT parentfields:
        # Purchase Order → ts_approval_log, Material Request → ts_mr_log
        # (verified in meta + tabTS Approval Log.parentfield; live-data BUG-1 —
        # reading ts_approval_log on an MR silently returns an empty history).
        log_field = "ts_approval_log" if doctype == "Purchase Order" else "ts_mr_log"
        payload["approval_log"] = [
            {
                "action": d.action,
                "from_state": d.from_state,
                "to_state": d.to_state,
                "action_by": d.action_by,
                "action_by_name": d.action_by_name,
                "action_by_role": d.action_by_role,
                "action_date": str(d.action_date or ""),
                "comment": d.comment,
                "step_order": d.step_order,
            }
            for d in (doc.get(log_field) or [])
        ]

    if doctype == "Purchase Order":
        payload["doc"] = {
            "supplier": doc.supplier,
            "supplier_name": doc.supplier_name,
            "transaction_date": str(doc.transaction_date or ""),
            "grand_total": doc.grand_total,
            "currency": doc.currency,
            "ts_approval_status": doc.ts_approval_status,
            "ts_po_on_hold": cint(doc.get("ts_po_on_hold")),
            "ts_purchase_category": doc.get("ts_purchase_category"),
            "ts_submitted_by": doc.get("ts_submitted_by"),
            "ts_amount_at_submission": doc.get("ts_amount_at_submission"),
            "docstatus": doc.docstatus,
        }
    elif doctype == "Material Request":
        payload["doc"] = {
            "material_request_type": doc.material_request_type,
            "transaction_date": str(doc.transaction_date or ""),
            "schedule_date": str(doc.schedule_date or ""),
            "ts_mr_status": doc.ts_mr_status,
            "ts_mr_submitted_by": doc.get("ts_mr_submitted_by"),
            "estimated_value": sum(float(d.amount or 0) for d in (doc.get("items") or [])),
            "docstatus": doc.docstatus,
        }
    elif doctype == "TS Budget Override Approval":
        payload["doc"] = {
            "title": doc.get("title"),
            "reference_doctype": doc.reference_doctype,
            "reference_name": doc.reference_name,
            "source_amount": doc.source_amount,
            "cost_center": doc.get("cost_center"),
            "breach_type": doc.get("breach_type"),
            "annual_breach_delta": doc.get("annual_breach_delta"),
            "monthly_breach_delta": doc.get("monthly_breach_delta"),
            "submitted_by": doc.get("submitted_by"),
            "submitted_at": str(doc.get("submitted_at") or ""),
            "submission_reason": doc.get("submission_reason"),
            "status": doc.status,
            "docstatus": doc.docstatus,
        }
        payload["context"] = _budget_override_context(doc, user)
        payload["items"] = []
        payload["approval_log"] = []
    elif doctype == "TS Post Dated Entry Request":
        payload["doc"] = {
            "request_type": doc.request_type,
            "status": doc.status,
            "requested_by": doc.requested_by,
            "request_date": str(doc.get("request_date") or ""),
            "reason": doc.get("reason"),
            "from_date": str(doc.get("from_date") or ""),
            "to_date": str(doc.get("to_date") or ""),
            "valid_from": str(doc.get("valid_from") or ""),
            "valid_until": str(doc.get("valid_until") or ""),
            "token_number": doc.get("token_number"),
            "docstatus": doc.docstatus,
        }
        payload["context"] = _post_dated_context(doc, user)
        payload["items"] = []
        payload["approval_log"] = []

    return payload


# ══════════════════════════════════════════════════════════════════════════
#  SYSTEM USE — department-wise usage for the Usage tab (v2.40.0).
#
#  A gate + a fold over ts_usage_report_api.get_usage_report — that module is
#  NOT edited (its byte-identity proof and Usage Report Viewer audience stay
#  intact). This endpoint exists because calling the desk endpoint from the
#  phone would (a) bypass _guard()/the PWA kill switch, and (b) ship a ~67 KB
#  payload whose sessions block and inactive.* arrays contain every staff
#  e-mail address — a roster of exactly the class v2.36.0 closed. The fold
#  returns ~2 KB and ZERO e-mail addresses, names, or document references.
#
#  Design contract (mockup approved 12 Aug 2026, 6 screens):
#  - counts of RECORDED actions only, never "hours worked";
#  - a department with people whose work IS measured but recorded nothing is
#    "idle" (signed in) or "absent" (nobody signed in) — two different states;
#  - a department whose people recorded nothing this screen can count in the
#    last 90 days is "blind" (teal): the measurement gap is OURS, not theirs.
#    Data-driven — the day Purchase Invoice joins FLOW_MAP, Accounts stops
#    being blind with no change here;
#  - IT / Production / Asset & Returns have no primary members (rank-1
#    Executive absorbs their role-holders) and are returned as folded teams —
#    multi-membership counts that overlap the primary buckets and must NEVER
#    be summed with them.
# ══════════════════════════════════════════════════════════════════════════

USAGE_KILL_SWITCH = "ts_exec_usage_enabled"  # field on TS Exec App Settings

# Audience — user decision 12 Aug 2026: "IT Head shouldn't have access to this
# app." CEO / MD / Administrator only, the OVERVIEW_ROLES class; deliberately
# NARROWER than the desk report's ALLOWED_ROLES (which adds Super Admin and
# Usage Report Viewer). Owned here, never borrowed (see OVERVIEW_ROLES note).
USAGE_ROLES = frozenset(OVERVIEW_ROLES)  # independent copy — widening
# OVERVIEW_ROLES later must be a deliberate choice for THIS audience too

_USAGE_DAYS = (7, 30, 90)

# The three buckets rank-order leaves empty on prod. Folded-team stats are
# computed for exactly these; the primary list stays single-membership so the
# hero total keeps reconciling to the row sum.
_FOLDED_TEAMS = ("IT", "Production", "Asset & Returns")

_USAGE_DOC_KEYS = ("entries", "amendments", "since_cancelled", "decisions", "flow_actions")


def _usage_audience_gate():
    if not (USAGE_ROLES & set(frappe.get_roles(frappe.session.user))):
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def usage_visible():
    """Should the Usage TAB render for this user? Nav affordance only — the
    endpoint re-checks everything server-side. Fails CLOSED and NEVER raises
    (called from www/exec.py, which also renders the Guest login screen)."""
    try:
        if frappe.session.user in (None, "", "Guest"):
            return False
        if not flag_on(USAGE_KILL_SWITCH):
            return False
        return bool(USAGE_ROLES & set(frappe.get_roles(frappe.session.user)))
    except Exception:
        frappe.clear_messages()
        return False


def _usage_inner(from_d, to_d):
    """Call the desk report's function DIRECTLY (undecorated). Inside an HTTP
    request the inner @rate_limit would otherwise share the outer endpoint's
    redis key (rl:{cmd}:{ip}) and charge the SAME bucket once per inner call —
    3 hits/request at days<90, cutting the real budget to a third
    (frappe/rate_limiter.py:155). Its in-body _check_role still runs —
    USAGE_ROLES ⊂ its ALLOWED_ROLES ∪ {Administrator}, so it can never fire
    first past our own gate."""
    fn = get_usage_report
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn(from_date=from_d, to_date=to_d)


def _fold_departments(user_rows):
    """users[] → per-primary-bucket aggregate. Never sums across a None
    (failed logins source): a department's logins become None, not 0."""
    depts = {}
    for u in user_rows:
        d = depts.setdefault(u["dept"], {
            "dept": u["dept"], "users": 0, "active": 0, "signed_in": 0,
            "entries": 0, "amendments": 0, "since_cancelled": 0,
            "decisions": 0, "flow_actions": 0, "logins": 0, "logins_na": False,
        })
        d["users"] += 1
        acted = any(u.get(k) for k in _USAGE_DOC_KEYS) or bool(u.get("logins"))
        if acted:
            d["active"] += 1
        if u.get("logins"):
            d["signed_in"] += 1
        for k in _USAGE_DOC_KEYS:
            d[k] += u.get(k) or 0
        if u.get("logins") is None:
            d["logins_na"] = True
        else:
            d["logins"] += u["logins"]
    for d in depts.values():
        if d["logins_na"]:
            d["logins"] = None
    return depts


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def get_usage_departments(days=7):
    """Department-wise system use for the Usage tab. READ-ONLY.

    Two inner reads when days < 90 (selected window + a 90-day context window
    that powers the measured/blind test and the "this is a change" line); one
    when days == 90. ~300-600 ms warm on prod — a manual-refresh screen for
    ≤3 accounts, uncached by design.
    """
    _guard()
    if not flag_on(USAGE_KILL_SWITCH):
        frappe.throw(_("System use is switched off."))
    _usage_audience_gate()

    days = cint(days)
    if days not in _USAGE_DAYS:
        frappe.throw(_("Invalid window."), frappe.ValidationError)

    to_d = getdate(today())
    inner = _usage_inner(add_days(to_d, -(days - 1)), to_d)
    ctx90 = inner if days == 90 else _usage_inner(add_days(to_d, -89), to_d)

    depts = _fold_departments(inner["users"])
    doc90 = {}
    for u in ctx90["users"]:
        doc90[u["dept"]] = doc90.get(u["dept"], 0) + (u.get("entries") or 0) + (u.get("decisions") or 0)

    prior = {}
    for d in depts.values():
        cur = d["entries"] + d["decisions"]
        if cur > 0:
            d["state"], d["measured"] = "ok", True
        elif doc90.get(d["dept"], 0) == 0:
            # Nothing this screen counts in 90 days: the gap is the screen's.
            d["state"], d["measured"] = "blind", False
        else:
            d["measured"] = True
            # logins None (source failed) reads as idle, never absent — an
            # "absent" verdict must rest on evidence, not on a dead source.
            d["state"] = "absent" if d["logins"] == 0 else "idle"
            prior[d["dept"]] = doc90[d["dept"]]

    # ── Folded teams: multi-membership over the three absorbed buckets ──────
    folded = None
    try:
        rules = dict(DEPT_RULES)
        team_roles = {t: rules[t] for t in _FOLDED_TEAMS}
        all_roles = tuple({r for roles in team_roles.values() for r in roles})
        by_user = {u["user"]: u for u in inner["users"]}
        rows = frappe.db.sql(
            """SELECT parent, role FROM `tabHas Role`
               WHERE parenttype = 'User' AND role IN %(roles)s
                 AND parent IN %(users)s""",
            {"roles": all_roles, "users": tuple(by_user) or ("",)},
        )
        membership = {}
        for parent, role in rows:
            membership.setdefault(parent, set()).add(role)
        md_holders = {
            r[0] for r in frappe.db.sql(
                """SELECT parent FROM `tabHas Role`
                   WHERE parenttype = 'User' AND role = 'MD' AND parent IN %(users)s""",
                {"users": tuple(by_user) or ("",)},
            )
        }
        folded = []
        for team in _FOLDED_TEAMS:
            wanted = set(team_roles[team])
            members = [by_user[u] for u, held in membership.items() if held & wanted]
            entry = {
                "team": team,
                # On prod rank-order absorbs every holder today, but that is an
                # observation, not a guarantee — a holder with no higher-ranked
                # role keeps a primary row, and the UI must reconcile the two.
                "primary_members": depts.get(team, {}).get("users", 0),
                "people": len(members),
                "active": sum(1 for m in members if any(m.get(k) for k in _USAGE_DOC_KEYS) or m.get("logins")),
                "doc_actions": sum((m.get("entries") or 0) + (m.get("decisions") or 0) for m in members),
                "logins": sum(m.get("logins") or 0 for m in members),
            }
            if team == "Production":
                # ts_production_entry_enabled is read LIVE (0 on prod, may be
                # 1 on demo); the
                # Check may be unset → None (L171/172). Off unless explicitly 1.
                entry["module_off"] = not bool(cint(frappe.db.get_single_value("TS Settings", "ts_production_entry_enabled") or 0))
                # 28 of the team's 30d actions were the MD's own approvals on
                # prod — shown untagged the MD reads his own work as someone
                # else's, so the split ships with the number.
                entry["md_own_decisions"] = sum(
                    m.get("decisions") or 0 for m in members if m["user"] in md_holders
                )
            if team == "Asset & Returns":
                # FLOW_MAP has no Return Item source at all — structural.
                entry["unmeasured"] = True
            folded.append(entry)
    except Exception:
        frappe.clear_messages()  # L276 — a caught exception still queues its message
        _log_once("usage_folded", "Exec usage: folded-teams fold failed", frappe.get_traceback())
        folded = None

    cards = inner["cards"]
    buckets = inner["inactive"]["buckets"]
    signed_in_no_records = sum(
        1 for u in inner["users"]
        if u.get("logins") and not any(u.get(k) for k in _USAGE_DOC_KEYS)
    )

    failed = inner["meta"]["failed_sources"]
    return {
        "range": {"from": str(inner["meta"]["from_date"]), "to": str(inner["meta"]["to_date"]), "days": days},
        "totals": {
            "entries": cards["total_entries"],
            "decisions": cards["total_decisions"],
            "actions": cards["total_entries"] + cards["total_decisions"],
            "logins": cards["total_logins"],
            "active_users": cards["active_users"],
            "total_users": len(inner["users"]),
        },
        # Ranked list order is decided client-side; ship deterministic order.
        "departments": sorted(
            depts.values(), key=lambda d: -(d["entries"] + d["decisions"])
        ),
        "folded_teams": folded,
        "people": {
            # COUNTS only — the e-mail lists behind these never leave the server.
            "inactive_over_30d": len(buckets["over30"]),
            "never_signed_in": len(buckets["never"]),
            "signed_in_no_records": signed_in_no_records,
        },
        "prior_90d": prior,
        "partial": bool(failed),
        "failed_source_count": len(failed),
        "logins_available": cards["total_logins"] is not None,
        "generated_at": str(now_datetime()),
    }
