"""System Usage & User Activity Report — read-only dashboard API.

Spec: PLAN_user_activity_usage_report.md v2 (post adversarial review, approved
24 Jul 2026). House dashboard pattern (ts_dashboard_omc_supply / ts_dashboard_ceo):
role gate FIRST, one whitelisted read returning one nested dict, all-NAMED
%(param)s SQL, no caching, no mutations (plain @frappe.whitelist(); L175 POST
is for writes only).

Honesty contract (plan §2): this reports RECORDED actions only — never "hours
worked". Entries are non-retroactive (first-in-chain creations; amendments and
since-cancelled are separate labelled columns). Confidential docs (PO / PR /
MR) contribute COUNTS ONLY — no doc names, suppliers, amounts, cost centers.
A failed source surfaces in meta.failed_sources and renders "n/a", never 0.
Failed-login attempted usernames are NEVER selected or serialized.
"""

import base64
import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, date_diff, get_datetime, getdate, now_datetime, today

from trustbit_ethanol.ts_gate_entry.ts_confidential_po import (
    _doc_has_maize,
    confidential_sql_clause,
    user_sees_confidential,
)

VERSION = "usage_report.v1"

# Narrowed 24 Jul on user instruction: "only permission to CEO/MD/Super Admin
# and MD Akram" — Akram's accounts carry the dedicated Usage Report Viewer role
# (no hardcoded emails; see the role seeding step in the deploy notes)
ALLOWED_ROLES = ("CEO", "MD", "Super Admin", "Usage Report Viewer")

DECISION_ACTIONS = ("Approved", "Final Approved", "Rejected")

MAX_SPAN_DAYS = 90
SESSION_EVENT_CAP = 300  # newest-first timeline rows per response; total still reported
# Activity Log is pruned at 90 days (daily 00:00 IST); the 89-day floor keeps the
# partially-pruned edge day out. A pre-retention window REFUSES, never fakes zeros.
RETENTION_FLOOR_DAYS = 89

# System/non-person accounts ONLY (user decision 24 Jul: "all real logins" count —
# admin/ops accounts like erp.admin [Kr Dhotte, 45 logins/30d on prod] are real
# people and must appear; hiding them made the report lie)
_STATIC_EXCLUDED = (
    "Administrator", "Guest",
    # demo-only test accounts — inert on prod (nonexistent users never match)
    "g1@test.com", "g2@test.com", "sharda@gmail.com", "raja.b@trustbit.in",
)

# Role → department buckets, FIRST match wins (rank order). A user matching ≥2
# of ranks 1-11 (i.e. everything above the generic Stock bucket) is flagged
# multi_department. Role strings verified against tabRole on demo, 24 Jul 2026.
DEPT_RULES = (
    ("Executive", ("CEO", "MD", "AVP", "General Manager")),
    ("Department Head", ("Department Head", "PM", "Grain PM", "Grain Purchase Manager")),
    ("IT", ("IT Head", "Super Admin", "System Manager")),
    ("Purchase", ("Purchase Manager", "Purchase User")),
    ("Accounts", ("Accounts Manager", "Accounts User")),
    ("Quality", ("Quality Inspector", "Quality Manager")),
    ("Stores", ("Stores Manager", "Stores User")),
    ("Weighbridge", ("Weighbridge Operator",)),
    ("Gate & Security", ("G1 Security", "G2 Gate Operator", "Admin Reception")),
    ("Production", ("Manufacturing Manager", "Manufacturing User")),
    ("Asset & Returns", ("Asset Controller", "Asset Custodian", "Return Item Controller", "Return Item Custodian")),
    ("Stock", ("Stock Manager", "Stock User")),
)
DEPT_BUCKETS = tuple(r[0] for r in DEPT_RULES) + ("Other",)
MULTI_DEPT_RANKS = 11

FLOWS = ("Gate", "Stores", "Quality", "Purchase", "MR", "Production")

# ── FLOW_MAP — SINGLE SOURCE OF TRUTH (plan §4.1) ────────────────────────────
# The identifiers below are the ONLY strings ever placed inside SQL text; every
# runtime value binds via named params. Gate activity lives on TS Token /
# TS Gate Entry / TS Weighbridge Log — tabTS G1/G2 Entry tables DO NOT exist on
# prod (1146). "confidential" sources (PO / PR / MR) are counts-only by
# construction: no column other than the actor is ever selected from them.
# kind: entry = doc creations (owner-style actor) · decision = an approval act
# recorded in explicit approver fields · approval_log = TS Approval Log child
# rows split into decisions vs other flow actions (routing ≠ deciding).
FLOW_MAP = (
    {"label": "token", "table": "tabTS Token", "actor": "owner", "ts": "creation", "flow": "Gate", "kind": "entry", "submittable": False},
    {"label": "gate_entry", "table": "tabTS Gate Entry", "actor": "owner", "ts": "creation", "flow": "Gate", "kind": "entry", "submittable": True},
    {"label": "wb_gross", "table": "tabTS Weighbridge Log", "actor": "gross_operator", "ts": "gross_weight_time", "flow": "Gate", "kind": "entry", "submittable": False},
    {"label": "wb_tare", "table": "tabTS Weighbridge Log", "actor": "tare_operator", "ts": "tare_weight_time", "flow": "Gate", "kind": "entry", "submittable": False},
    {"label": "purchase_receipt", "table": "tabPurchase Receipt", "actor": "owner", "ts": "creation", "flow": "Stores", "kind": "entry", "submittable": True},
    {"label": "stock_entry", "table": "tabStock Entry", "actor": "owner", "ts": "creation", "flow": "Stores", "kind": "entry", "submittable": True},
    {"label": "stock_recon", "table": "tabStock Reconciliation", "actor": "owner", "ts": "creation", "flow": "Stores", "kind": "entry", "submittable": True},
    {"label": "item_request", "table": "tabTS Item Creator", "actor": "requested_by", "ts": "creation", "flow": "Stores", "kind": "entry", "submittable": False},
    {"label": "item_approval", "table": "tabTS Item Creator", "actor": "approved_by", "ts": "approved_on", "flow": "Stores", "kind": "decision",
     "extra": "status IN ('Created', 'Rejected')"},
    {"label": "quality_inspection", "table": "tabTS Quality Inspection", "actor": "COALESCE(inspector, owner)", "ts": "creation", "flow": "Quality", "kind": "entry", "submittable": True},
    {"label": "deduction_sheet", "table": "tabTS Deduction Sheet", "actor": "filled_by", "ts": "filled_at", "flow": "Quality", "kind": "entry", "submittable": True},
    {"label": "material_inspection", "table": "tabTS Material Inspection", "actor": "inspection_by", "ts": "inspection_time", "flow": "Quality", "kind": "decision"},
    {"label": "purchase_order", "table": "tabPurchase Order", "actor": "owner", "ts": "creation", "flow": "Purchase", "kind": "entry", "submittable": True},
    {"label": "po_approvals", "table": "tabTS Approval Log", "actor": "action_by", "ts": "action_date", "flow": "Purchase", "kind": "approval_log", "parenttype": "Purchase Order"},
    {"label": "material_request", "table": "tabMaterial Request", "actor": "owner", "ts": "creation", "flow": "MR", "kind": "entry", "submittable": True},
    {"label": "mr_approvals", "table": "tabTS Approval Log", "actor": "action_by", "ts": "action_date", "flow": "MR", "kind": "approval_log", "parenttype": "Material Request"},
    {"label": "production_entry", "table": "tabTS Production Entry", "actor": "owner", "ts": "creation", "flow": "Production", "kind": "entry", "submittable": False},
    {"label": "production_release", "table": "tabTS Production Entry", "actor": "released_by", "ts": "released_at", "flow": "Production", "kind": "decision"},
    {"label": "dept_entry", "table": "tabTS Production Department Entry", "actor": "submitted_by", "ts": "logged_at", "flow": "Production", "kind": "entry", "submittable": False},
)

_METRIC_KEYS = ("entries", "amendments", "since_cancelled", "decisions", "flow_actions", "logins")


def _check_role():
    if frappe.session.user == "Administrator":
        return  # system account — ops/testing access
    if not any(r in frappe.get_roles(frappe.session.user) for r in ALLOWED_ROLES):
        frappe.throw(_("Not authorized to view the Usage Report"), frappe.PermissionError)


def _excluded_users():
    """Static list + the live cascade-executor user, falsy-filtered: an empty
    executor field must never inject None into NOT IN (a NULL there poisons
    every query to zero rows). Asserted non-empty before use."""
    executor = frappe.db.get_single_value("TS Settings", "ts_cascade_executor_user")
    excluded = tuple(u for u in _STATIC_EXCLUDED + (executor,) if u)
    assert excluded, "EXCLUDED_USERS resolved empty"
    return excluded


def _resolve_range(from_date, to_date):
    t = getdate(today())
    try:
        to_d = getdate(to_date) if to_date else t
        from_d = getdate(from_date) if from_date else add_days(to_d, -29)
    except Exception:
        frappe.throw(_("Invalid date."), frappe.ValidationError)
    if from_d > to_d:
        frappe.throw(_("From Date must be on or before To Date."), frappe.ValidationError)
    if to_d > t:
        frappe.throw(_("To Date cannot be in the future."), frappe.ValidationError)
    if date_diff(to_d, from_d) + 1 > MAX_SPAN_DAYS:
        frappe.throw(_("The date range cannot exceed {0} days.").format(MAX_SPAN_DAYS), frappe.ValidationError)
    if date_diff(t, from_d) > RETENTION_FLOOR_DAYS:
        frappe.throw(
            _("From Date cannot be older than {0} days — login history is only retained 90 days.").format(RETENTION_FLOOR_DAYS),
            frappe.ValidationError,
        )
    return from_d, to_d


def _pass_sql(src):
    # SQL text is assembled ONLY from FLOW_MAP constants above — never from
    # request args. All runtime values bind via named params (plan §4.2).
    where = "{ts} >= %(from_ts)s AND {ts} < %(to_ts)s AND {actor} NOT IN %(excluded)s".format(
        ts=src["ts"], actor=src["actor"]
    )
    if src.get("parenttype"):
        where += " AND parenttype = %(parenttype)s"
    if src.get("extra"):
        where += " AND " + src["extra"]
    if src["kind"] == "approval_log":
        select = ("SUM(action IN %(decisions)s) AS decisions, "
                  "SUM(action NOT IN %(decisions)s) AS flow_actions")
    elif src["kind"] == "decision":
        select = "COUNT(*) AS decisions"
    elif src.get("submittable"):
        select = ("SUM(amended_from IS NULL) AS entries, "
                  "SUM(amended_from IS NOT NULL) AS amendments, "
                  "SUM(docstatus = 2) AS since_cancelled")
    else:
        select = "COUNT(*) AS entries"
    return (
        "SELECT {actor} AS user, WEEKDAY({ts}) AS d, HOUR({ts}) AS h, {select} "
        "FROM `{table}` WHERE {where} GROUP BY {actor}, d, h"
    ).format(actor=src["actor"], ts=src["ts"], select=select, table=src["table"], where=where)


def _load_users(excluded):
    rows = frappe.db.sql(
        """SELECT name, full_name, enabled, last_active, last_login
           FROM `tabUser`
           WHERE user_type = 'System User' AND name NOT IN %(excluded)s""",
        {"excluded": excluded}, as_dict=True,
    )
    users = {r.name: r for r in rows}
    role_rows = frappe.db.sql(
        """SELECT parent, role FROM `tabHas Role`
           WHERE parenttype = 'User' AND parent IN %(users)s""",
        {"users": tuple(users) or ("",)}, as_dict=True,
    )
    roles_by_user = {}
    for r in role_rows:
        roles_by_user.setdefault(r.parent, set()).add(r.role)
    return users, roles_by_user


def _department_of(roles):
    matches = [b for b, wanted in DEPT_RULES if roles & set(wanted)]
    bucket = matches[0] if matches else "Other"
    ranked = [b for b, wanted in DEPT_RULES[:MULTI_DEPT_RANKS] if roles & set(wanted)]
    return bucket, len(ranked) >= 2


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_usage_report(from_date=None, to_date=None, user=None, department=None, flow=None):
    """One read: cards, per-user rows, heatmap, flow breakdown, inactive panel.
    department/flow are validated against server-side enums; `user` failures
    return the identical generic empty shape (no enumeration oracle)."""
    _check_role()
    from_d, to_d = _resolve_range(from_date, to_date)
    if department and department not in DEPT_BUCKETS:
        frappe.throw(_("Unknown department."), frappe.ValidationError)
    if flow and flow not in FLOWS:
        frappe.throw(_("Unknown flow."), frappe.ValidationError)

    excluded = _excluded_users()
    users, roles_by_user = _load_users(excluded)
    if user and (user in excluded or user not in users):
        users = {}
    elif user:
        users = {user: users[user]}

    dept_of, multi = {}, {}
    for name in users:
        dept_of[name], multi[name] = _department_of(roles_by_user.get(name, set()))
    if department:
        users = {n: r for n, r in users.items() if dept_of[n] == department}
    enabled = {n for n, r in users.items() if r.enabled}

    params = {
        "from_ts": str(from_d) + " 00:00:00",
        "to_ts": str(add_days(to_d, 1)) + " 00:00:00",
        "excluded": excluded,
        "decisions": DECISION_ACTIONS,
    }
    per_user = {n: dict.fromkeys(_METRIC_KEYS, 0) for n in enabled}
    heat = [[0] * 24 for _ in range(7)]
    flow_agg = {f: {"entries": 0, "decisions": 0, "by_user": {}} for f in FLOWS}
    failed = []

    def _tally(name, row, flow_key):
        # rows for deleted / disabled / filtered-out accounts are dropped
        # everywhere at once, so cards, table, heatmap and flows always agree
        if name not in enabled:
            return
        u = per_user[name]
        fa = flow_agg[flow_key]
        for k in ("entries", "amendments", "since_cancelled", "decisions", "flow_actions"):
            u[k] += int(row.get(k) or 0)
        fa["entries"] += int(row.get("entries") or 0)
        fa["decisions"] += int(row.get("decisions") or 0)
        fa["by_user"][name] = fa["by_user"].get(name, 0) + sum(
            int(row.get(k) or 0) for k in ("entries", "decisions")
        )
        if row.get("d") is not None and row.get("h") is not None:
            # heatmap legend: recorded actions = entries + decisions + logins
            heat[int(row["d"])][int(row["h"])] += int(row.get("entries") or 0) + int(row.get("decisions") or 0)

    for src in FLOW_MAP:
        if flow and src["flow"] != flow:
            continue
        try:
            src_params = dict(params, parenttype=src["parenttype"]) if src.get("parenttype") else params
            for row in frappe.db.sql(_pass_sql(src), src_params, as_dict=True):
                _tally(row.user, row, src["flow"])
        except Exception:
            frappe.clear_messages()  # a caught DB error still leaks via _server_messages (L276)
            frappe.log_error(title="Usage Report source failed: " + src["label"],
                             message=frappe.get_traceback())
            failed.append(src["label"])

    logins_ok = True
    try:
        for row in frappe.db.sql(
            """SELECT al.user AS user, WEEKDAY(al.creation) AS d, HOUR(al.creation) AS h, COUNT(*) AS n
               FROM `tabActivity Log` al
               INNER JOIN `tabUser` u ON u.name = al.user
               WHERE al.operation = 'Login' AND al.status = 'Success'
                 AND al.creation >= %(from_ts)s AND al.creation < %(to_ts)s
                 AND al.user NOT IN %(excluded)s
               GROUP BY al.user, d, h""",
            params, as_dict=True,
        ):
            if row.user in enabled:
                per_user[row.user]["logins"] += int(row.n)
                heat[int(row.d)][int(row.h)] += int(row.n)
    except Exception:
        frappe.clear_messages()
        frappe.log_error(title="Usage Report source failed: logins", message=frappe.get_traceback())
        failed.append("logins")
        logins_ok = False

    # login/logout timeline paired into SESSIONS (UAT: the raw event stream
    # read as "login, login, login, logout, logout…"). FIFO pairing per user;
    # a logout at ~midnight is Frappe's session-expiry sweep → flagged
    # "expired" and NO duration is claimed (the user stopped earlier); a login
    # with no logout row (browser closed) shows logout None. Explicit-logout
    # honesty unchanged; logouts whose login predates the range pair to None.
    sessions = {"sessions": [], "total_sessions": 0, "total_events": 0, "cap": SESSION_EVENT_CAP}
    try:
        open_logins, paired = {}, []
        for row in frappe.db.sql(
            """SELECT al.user AS user, al.operation AS op, al.creation AS ts
               FROM `tabActivity Log` al
               INNER JOIN `tabUser` u ON u.name = al.user
               WHERE al.operation IN ('Login', 'Logout')
                 AND (al.operation = 'Logout' OR al.status = 'Success')
                 AND al.creation >= %(from_ts)s AND al.creation < %(to_ts)s
                 AND al.user NOT IN %(excluded)s
               ORDER BY al.creation ASC""",
            params, as_dict=True,
        ):
            if row.user not in enabled:
                continue
            sessions["total_events"] += 1
            if row.op == "Login":
                open_logins.setdefault(row.user, []).append(row.ts)
            else:
                q = open_logins.get(row.user)
                paired.append((row.user, q.pop(0) if q else None, row.ts))
        for u, opens in open_logins.items():
            for ts in opens:
                paired.append((u, ts, None))
        paired.sort(key=lambda p: p[1] or p[2], reverse=True)
        sessions["total_sessions"] = len(paired)
        for u, li, lo in paired[:SESSION_EVENT_CAP]:
            is_exp = bool(lo is not None and lo.hour == 0 and lo.minute <= 5)
            mins = int((lo - li).total_seconds() // 60) if (li and lo and not is_exp and lo >= li) else None
            sessions["sessions"].append({
                "user": u, "full_name": users[u].full_name or u,
                "login": str(li)[:16] if li else None,
                "logout": str(lo)[:16] if lo else None,
                "expired": is_exp, "minutes": mins,
            })
    except Exception:
        frappe.clear_messages()
        frappe.log_error(title="Usage Report source failed: sessions", message=frappe.get_traceback())
        failed.append("sessions")
        sessions = None

    failed_logins = None
    if not (user or department or flow):
        try:
            # ONE anonymous scalar — attempted usernames are credential-adjacent
            # and are never selected, attributed, or serialized (plan §2).
            failed_logins = int(frappe.db.sql(
                """SELECT COUNT(*) FROM `tabActivity Log`
                   WHERE operation = 'Login' AND status = 'Failed'
                     AND creation >= %(from_ts)s AND creation < %(to_ts)s""",
                params,
            )[0][0])
        except Exception:
            frappe.clear_messages()
            frappe.log_error(title="Usage Report source failed: failed_logins", message=frappe.get_traceback())
            failed.append("failed_logins")

    now = now_datetime()
    buckets = {"d1": [], "d7": [], "d30": [], "over30": [], "never": []}
    for n in sorted(enabled):
        # last_active is a Datetime but last_login is a varchar Data field —
        # the fallback must coerce or (now - str) TypeErrors the whole report
        seen = users[n].last_active or users[n].last_login
        if not seen:
            buckets["never"].append(n)
            continue
        days = (now - get_datetime(seen)).days
        key = "d1" if days <= 1 else "d7" if days <= 7 else "d30" if days <= 30 else "over30"
        buckets[key].append(n)

    user_rows = [
        {
            "user": n, "full_name": users[n].full_name or n,
            "dept": dept_of[n], "multi_department": multi[n],
            "last_active": str(users[n].last_active or users[n].last_login or ""),
            **{k: (None if k == "logins" and not logins_ok else per_user[n][k]) for k in _METRIC_KEYS},
        }
        for n in sorted(enabled, key=lambda x: -(per_user[x]["entries"] + per_user[x]["decisions"]))
    ]
    # active = any recorded action family in range (entries, amendments,
    # decisions, flow actions, logins) — routing counts as using the system
    active = {n for n in enabled if any(per_user[n][k] for k in _METRIC_KEYS)}

    return {
        "cards": {
            "active_users": len(active),
            "total_entries": sum(per_user[n]["entries"] for n in enabled),
            "total_decisions": sum(per_user[n]["decisions"] for n in enabled),
            "total_logins": (sum(per_user[n]["logins"] for n in enabled) if logins_ok else None),
            "failed_logins": failed_logins,
            "inactive_over_30d": len(buckets["over30"]) + len(buckets["never"]),
        },
        "users": user_rows,
        "heatmap": heat,
        "flows": [
            {
                "flow": f,
                "entries": (None if any(s["label"] in failed for s in FLOW_MAP if s["flow"] == f) else flow_agg[f]["entries"]),
                "decisions": flow_agg[f]["decisions"],
                "top_users": [
                    {"user": n, "full_name": users[n].full_name or n, "n": c}
                    for n, c in sorted(flow_agg[f]["by_user"].items(), key=lambda kv: -kv[1])[:5]
                ],
            }
            for f in FLOWS if not flow or f == flow
        ],
        "sessions": sessions,
        "inactive": {
            "buckets": buckets,
            "disabled": sorted(n for n, r in users.items() if not r.enabled),
        },
        "meta": {
            "version": VERSION,
            "from_date": str(from_d), "to_date": str(to_d),
            "failed_sources": failed,
            "dept_buckets": list(DEPT_BUCKETS), "flows": list(FLOWS),
            "generated_at": str(now_datetime()),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Activity Detail drill-down — get_user_activity
#  Spec: PLAN_usage_report_activity_drilldown.md v2 (approved 24 Jul 2026).
#  This IS the detail endpoint the MAIZE_PO_CONFIDENTIAL carve-out anticipates:
#  confidential_sql_clause + per-doc has_permission + coarse withheld
#  accounting ship here from day one (lock if_changing SPLIT-REGIME text).
#  get_usage_report above stays byte-identical (test P / OQ2 Reading A).
#  Field VALUES are never serialized: the parser reads field NAMES only and
#  never destructures diff tuple indices 1/2 (plan §3.4).
# ═══════════════════════════════════════════════════════════════════════════

ACTIVITY_PAGE_TOUCHES = 20
ACTIVITY_MAX_PAGES = 25
ACTIVITY_FILL_ROUNDS = 3
CONFIDENTIAL_DOCTYPES = ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Material Request")
# 14 FLOW_MAP doc tables + Purchase Invoice (edits-only; confidential machinery covers it)
ACTIVITY_DOCTYPES = tuple(sorted(
    {s["table"][3:] for s in FLOW_MAP if s["table"] != "tabTS Approval Log"} | {"Purchase Invoice"}
))
_NOISE_FIELDS = {"modified", "modified_by", "_comments", "idx", "naming_series", "ts_confidential"}


def _act_cursor_decode(cursor):
    if not cursor:
        return None, 1
    try:
        tok = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        ts = get_datetime(tok["t"])
        page = int(tok["p"])
        assert ts is not None and 1 <= page <= ACTIVITY_MAX_PAGES
        return ts, page
    except Exception:
        frappe.throw(_("Invalid cursor."), frappe.ValidationError)


def _act_cursor_encode(ts, page):
    return base64.urlsafe_b64encode(json.dumps({"t": str(ts), "p": page}).encode()).decode()


def _act_labels(dt, rows):
    """Parse Version.data blobs for one doc-touch → (action, labels, system_ref,
    import_id, noisy). NAMES ONLY — tuple indices 1/2 (old/new values) are
    never read; child entries contribute the table label + row count only."""
    meta = frappe.get_meta(dt)
    fields, action, system_ref, import_id = [], "Changed", None, None
    child_counts = {}
    for r in rows:
        try:
            data = json.loads(r.get("data") or "{}")
        except Exception:
            continue
        if data.get("updater_reference"):
            ref = data["updater_reference"]
            if isinstance(ref, dict) and ref.get("doctype") in CONFIDENTIAL_DOCTYPES:
                system_ref = ref.get("doctype")  # never emit a confidential ref's docname
            else:
                system_ref = ref if isinstance(ref, str) else "%s %s" % (
                    (ref.get("doctype") or ""), (ref.get("docname") or ref.get("name") or ""))
        if data.get("data_import"):
            import_id = data["data_import"]
        for t in data.get("changed") or []:
            fn = t[0] if t else None
            if not fn:
                continue
            if fn == "docstatus":
                action = "Submitted"  # refined below via added/removed? docstatus 2 handled by caller state
                continue
            if fn == "amended_from":
                action = "Amended"
                continue
            if fn in _NOISE_FIELDS:
                continue
            fields.append(fn)
        for key in ("added", "removed", "row_changed"):
            for t in data.get(key) or []:
                if t:
                    child_counts[t[0]] = child_counts.get(t[0], 0) + 1
    labels, seen = [], set()
    for fn in fields:
        if fn in seen:
            continue
        seen.add(fn)
        df = meta.get_field(fn)
        labels.append((df.label if df and df.label else fn))
    for tf, n in child_counts.items():
        df = meta.get_field(tf)
        labels.append("%s (%d %s)" % ((df.label if df and df.label else tf), n, _("rows") if n != 1 else _("row")))
    noisy = not labels and action == "Changed"
    return action, labels, system_ref, import_id, noisy


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def get_user_activity(user=None, from_date=None, to_date=None, cursor=None, doctype=None):
    """Per-user activity detail feed (plan §4): Version doc-touches + FLOW_MAP
    creations + decisions, keyset-paginated, confidential clause + per-doc
    has_permission enforced, labels-only diffs, coarse range-level redaction."""
    _check_role()
    viewer = frappe.session.user
    from_d, to_d = _resolve_range(from_date, to_date)
    if doctype and doctype not in ACTIVITY_DOCTYPES:
        return {"rows": [], "has_more": False, "cursor": None, "first_page": None, "meta": {"failed_sources": []}}
    excluded = _excluded_users()
    users, _roles = _load_users(excluded)
    if not user or user in excluded or user not in users:
        return {"rows": [], "has_more": False, "cursor": None, "first_page": None, "meta": {"failed_sources": []}}
    cursor_ts, page_no = _act_cursor_decode(cursor)
    if page_no >= ACTIVITY_MAX_PAGES:
        return {"rows": [], "has_more": False, "cursor": None, "first_page": None, "meta": {"failed_sources": []}}

    dts = (doctype,) if doctype else ACTIVITY_DOCTYPES
    params = {
        "user": user,
        "from_ts": str(from_d) + " 00:00:00",
        "to_ts": str(add_days(to_d, 1)) + " 00:00:00",
        "dts": dts,
    }
    failed, page_rows, not_shown, system_rows = [], [], 0, {}
    belt_withheld = 0
    perm_memo = {}

    def _perm(dt, dn):
        key = (dt, dn)
        if key not in perm_memo:
            try:
                perm_memo[key] = bool(frappe.has_permission(dt, ptype="read", doc=dn, user=viewer))
            except frappe.DoesNotExistError:
                perm_memo[key] = "missing"
            except Exception:
                perm_memo[key] = False
        return perm_memo[key]

    # ── (a) Version doc-touches, cursor-bounded fill loop ────────────────────
    try:
        sees = {dt: user_sees_confidential(dt, viewer) for dt in CONFIDENTIAL_DOCTYPES}
        boundary, rounds = cursor_ts, 0
        touches = []
        while len(touches) < ACTIVITY_PAGE_TOUCHES and rounds < ACTIVITY_FILL_ROUNDS:
            rounds += 1
            p = dict(params, lim=ACTIVITY_PAGE_TOUCHES * 2)
            bound_sql = ""
            if boundary is not None:
                bound_sql = " AND v.modified < %(bound)s"
                p["bound"] = str(boundary)
            cands = frappe.db.sql(
                """SELECT v.ref_doctype AS dt, v.docname AS dn, DATE(v.modified) AS d,
                          MAX(v.modified) AS ts, COUNT(*) AS save_count
                   FROM `tabVersion` v
                   WHERE v.owner = %(user)s AND v.ref_doctype IN %(dts)s
                     AND v.modified >= %(from_ts)s AND v.modified < %(to_ts)s{b}
                   GROUP BY v.ref_doctype, v.docname, DATE(v.modified)
                   ORDER BY ts DESC LIMIT %(lim)s""".format(b=bound_sql),
                p, as_dict=True)
            if not cands:
                break
            boundary = cands[-1].ts
            by_dt = {}
            for c in cands:
                by_dt.setdefault(c.dt, []).append(c)
            parent = {}
            for dt, items in by_dt.items():
                cols = "name, docstatus, ts_confidential, owner" if dt in CONFIDENTIAL_DOCTYPES else "name, docstatus"
                for row in frappe.db.sql(
                    "SELECT {c} FROM `tab{t}` WHERE name IN %(names)s".format(c=cols, t=dt),
                    {"names": tuple({i.dn for i in items})}, as_dict=True,
                ):
                    parent[(dt, row.name)] = row
            for c in cands:
                if len(touches) >= ACTIVITY_PAGE_TOUCHES:
                    break
                pdoc = parent.get((c.dt, c.dn))
                if pdoc is None:
                    # since-deleted: docname withheld for ALL doctypes (§3.1)
                    touches.append(dict(c, since_deleted=True))
                    continue
                if c.dt in CONFIDENTIAL_DOCTYPES and not sees[c.dt] and pdoc.owner != viewer:
                    if pdoc.ts_confidential:
                        continue  # counted range-level by the withheld SQL (flag=1)
                    if _act_maize_belt(c.dt, c.dn):
                        # flag-falsy maize doc (§3.7 belt): the range SQL keys on
                        # the flag so it misses these — count them here so the
                        # honest-withheld promise holds for loaded pages
                        belt_withheld += 1
                        continue
                perm = _perm(c.dt, c.dn)
                if perm == "missing":
                    touches.append(dict(c, since_deleted=True))
                elif perm:
                    touches.append(dict(c, _ds=pdoc.docstatus))
                else:
                    not_shown += 1
            if len(cands) < ACTIVITY_PAGE_TOUCHES * 2:
                break
        # member rows → labels (bounded: page touches only)
        for dt in {t["dt"] for t in touches if not t.get("since_deleted")}:
            keys = [t for t in touches if t["dt"] == dt and not t.get("since_deleted")]
            members = frappe.db.sql(
                """SELECT docname, DATE(modified) AS d, data FROM `tabVersion`
                   WHERE owner = %(user)s AND ref_doctype = %(dt)s AND docname IN %(names)s
                     AND modified >= %(from_ts)s AND modified < %(to_ts)s""",
                dict(params, dt=dt, names=tuple({t["dn"] for t in keys})), as_dict=True)
            grouped = {}
            for m in members:
                grouped.setdefault((m.docname, str(m.d)), []).append(m)
            for t in keys:
                action, labels, sysref, import_id, noisy = _act_labels(dt, grouped.get((t["dn"], str(t["d"])), []))
                if action == "Submitted" and t.get("_ds") == 2:
                    # labels-only parser can't tell 0→1 from 1→2; the parent's
                    # CURRENT docstatus disambiguates (code-tester #1 fix path)
                    action = "Cancelled"
                if noisy:
                    t["drop"] = True
                    continue
                if import_id:
                    k = (import_id, dt)
                    system_rows[k] = system_rows.get(k, 0) + 1
                    t["drop"] = True
                    continue
                t.update({"action": action, "labels": labels[:3], "label_more": labels[3:], "system": bool(sysref), "system_ref": sysref})
        for t in touches:
            if t.get("drop"):
                continue
            if t.get("since_deleted"):
                page_rows.append({"since_deleted": True, "doctype": t["dt"], "day": str(t["d"]), "ts": str(t["ts"])[:16], "_fts": str(t["ts"])})
            else:
                page_rows.append({
                    "_fts": str(t["ts"]),
                    "ts": str(t["ts"])[:16], "doctype": t["dt"], "docname": t["dn"],
                    "action": t.get("action", "Changed"), "fields": t.get("labels") or [],
                    "more_fields": t.get("label_more") or [], "save_count": int(t["save_count"]),
                    "system": bool(t.get("system")), "system_ref": t.get("system_ref"),
                })
        for (import_id, dt), n in system_rows.items():
            page_rows.append({"system": True, "doctype": dt, "action": _("Data Import: {0} {1} records").format(n, dt), "ts": None})
        version_boundary = boundary
    except Exception:
        frappe.clear_messages()
        frappe.log_error(title="Usage activity source failed: versions", message=frappe.get_traceback())
        failed.append("versions")
        version_boundary = None

    # ── (b)+(c) creations + decisions, cursor-bounded, same regime ───────────
    try:
        for src in FLOW_MAP:
            dt = src["table"][3:]
            if doctype and dt != doctype and not (src["kind"] == "approval_log" and doctype in ("Purchase Order", "Material Request")):
                continue
            p = dict(params, lim=ACTIVITY_PAGE_TOUCHES)
            bound_sql = ""
            if cursor_ts is not None:
                bound_sql = " AND {ts} < %(bound)s".format(ts=src["ts"] if src["kind"] != "approval_log" else "action_date")
                p["bound"] = str(cursor_ts)
            if src["kind"] == "approval_log":
                pdt = src["parenttype"]
                if doctype and pdt != doctype:
                    continue
                clause = confidential_sql_clause("d", viewer, pdt) if pdt in CONFIDENTIAL_DOCTYPES else ""
                conf_sql = " AND (d.name IS NULL OR (TRUE{c}))".format(c=clause) if clause else ""
                rows = frappe.db.sql(
                    """SELECT al.parent AS dn, al.action AS action, al.action_date AS ts, d.name IS NULL AS missing
                       FROM `tabTS Approval Log` al LEFT JOIN `tab{p}` d ON d.name = al.parent
                       WHERE al.parenttype = %(pdt)s AND al.action_by = %(user)s
                         AND al.action IN %(dec)s
                         AND al.action_date >= %(from_ts)s AND al.action_date < %(to_ts)s{b}{c}
                       ORDER BY al.action_date DESC LIMIT %(lim)s""".format(p=pdt, b=bound_sql, c=conf_sql),
                    dict(p, pdt=pdt, dec=DECISION_ACTIONS), as_dict=True)
                for r in rows:
                    if r.missing:
                        page_rows.append({"since_deleted": True, "doctype": pdt, "day": str(r.ts)[:10], "ts": str(r.ts)[:16], "_fts": str(r.ts)})
                        continue
                    perm = _perm(pdt, r.dn)
                    if perm is True:
                        page_rows.append({"_fts": str(r.ts), "ts": str(r.ts)[:16], "doctype": pdt, "docname": r.dn,
                                          "action": _(r.action), "fields": [], "more_fields": [], "save_count": 1,
                                          "system": False, "decision": True})
                    elif perm == "missing":
                        page_rows.append({"since_deleted": True, "doctype": pdt, "day": str(r.ts)[:10], "ts": str(r.ts)[:16], "_fts": str(r.ts)})
                    else:
                        not_shown += 1
                continue
            if src["actor"] == "owner" or src["kind"] in ("entry", "decision"):
                clause = confidential_sql_clause("t", viewer, dt) if dt in CONFIDENTIAL_DOCTYPES else ""
                extra = " AND " + src["extra"] if src.get("extra") else ""
                rows = frappe.db.sql(
                    """SELECT name AS dn, {ts} AS ts FROM `tab{t}` t
                       WHERE {actor} = %(user)s AND {ts} >= %(from_ts)s AND {ts} < %(to_ts)s{b}{x}{c}
                       ORDER BY {ts} DESC LIMIT %(lim)s""".format(
                        ts=src["ts"], t=dt, actor=src["actor"], b=bound_sql, x=extra, c=clause),
                    p, as_dict=True)
                label = _("Created") if src["kind"] == "entry" else _("Approved / actioned")
                for r in rows:
                    perm = _perm(dt, r.dn)
                    if perm is True:
                        page_rows.append({"_fts": str(r.ts), "ts": str(r.ts)[:16], "doctype": dt, "docname": r.dn,
                                          "action": label, "fields": [], "more_fields": [], "save_count": 1,
                                          "system": False, "decision": src["kind"] == "decision"})
                    elif perm == "missing":
                        page_rows.append({"since_deleted": True, "doctype": dt, "day": str(r.ts)[:10], "ts": str(r.ts)[:16], "_fts": str(r.ts)})
                    else:
                        not_shown += 1
    except Exception:
        frappe.clear_messages()
        frappe.log_error(title="Usage activity source failed: passes", message=frappe.get_traceback())
        failed.append("passes")

    frappe.clear_messages()  # permission hooks may push docname-bearing messages (§4.7)

    dated = [r for r in page_rows if r.get("ts")]
    dated.sort(key=lambda r: r["ts"], reverse=True)
    undated = [r for r in page_rows if not r.get("ts")]
    page = dated[:ACTIVITY_PAGE_TOUCHES] + undated
    has_more = len(dated) > ACTIVITY_PAGE_TOUCHES or (version_boundary is not None and len(dated) >= ACTIVITY_PAGE_TOUCHES)
    next_cursor = None
    if has_more and page and page_no < ACTIVITY_MAX_PAGES:
        # full-precision boundary — a minute-truncated cursor re-serves the
        # boundary minute's rows on the next page (code-tester #2)
        fulls = [r["_fts"] for r in page if r.get("_fts")]
        if fulls:
            next_cursor = _act_cursor_encode(min(fulls), page_no + 1)
    for r in page:
        r.pop("_fts", None)

    first_page = None
    if cursor is None:
        first_page = {"withheld_confidential_total": None, "not_shown_other": not_shown,
                      "system_count": sum(system_rows.values()) + sum(1 for r in page if r.get("system")),
                      "facets": {}, "history_floor": None, "withheld_unavailable": False}
        try:
            total = 0
            for dt in CONFIDENTIAL_DOCTYPES:
                if sees.get(dt):
                    continue
                clause = confidential_sql_clause("d", viewer, dt)
                if not clause:
                    continue
                base = """SELECT COUNT(DISTINCT v.docname, DATE(v.modified)) FROM `tabVersion` v
                          LEFT JOIN `tab{t}` d ON d.name = v.docname
                          WHERE v.owner = %(user)s AND v.ref_doctype = %(dt)s
                            AND v.modified >= %(from_ts)s AND v.modified < %(to_ts)s"""
                all_n = frappe.db.sql(base.format(t=dt), dict(params, dt=dt))[0][0]
                vis_n = frappe.db.sql((base + " AND (d.name IS NULL OR (TRUE{c}))").format(t=dt, c=clause), dict(params, dt=dt))[0][0]
                total += int(all_n) - int(vis_n)
            # range SQL keys on ts_confidential=1; belt hits (flag-falsy maize,
            # §3.7) are added from the loaded page so they are never uncounted
            first_page["withheld_confidential_total"] = total + belt_withheld
        except Exception:
            frappe.clear_messages()
            frappe.log_error(title="Usage activity source failed: withheld", message=frappe.get_traceback())
            failed.append("withheld")
            first_page["withheld_unavailable"] = True
        try:
            for row in frappe.db.sql(
                """SELECT ref_doctype, COUNT(DISTINCT docname, DATE(modified)) AS n FROM `tabVersion`
                   WHERE owner = %(user)s AND ref_doctype IN %(dts)s
                     AND modified >= %(from_ts)s AND modified < %(to_ts)s GROUP BY ref_doctype""",
                params, as_dict=True):
                first_page["facets"][row.ref_doctype] = int(row.n)
            floor = frappe.db.sql("SELECT MIN(modified) FROM `tabVersion` WHERE ref_doctype IN %(dts)s", params)
            first_page["history_floor"] = str(floor[0][0])[:10] if floor and floor[0][0] else None
        except Exception:
            frappe.clear_messages()
            failed.append("aggregates")

    return {"rows": page, "has_more": bool(next_cursor), "cursor": next_cursor,
            "first_page": first_page, "meta": {"failed_sources": failed}}


def _act_maize_belt(dt, dn):
    """§3.7 belt: a confidential-class doc whose flag is falsy (e.g. cancelled
    before backfill) gets a live maize check; hits are withheld + flagged."""
    try:
        doc = frappe.get_doc(dt, dn)
        if _doc_has_maize(doc):
            frappe.log_error(title="Usage activity: unflagged maize doc withheld",
                             message="%s %s passed flag check but has maize CC — backfill candidate" % (dt, dn))
            return True
    except Exception:
        return False
    return False
