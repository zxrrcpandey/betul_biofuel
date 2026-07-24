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

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, date_diff, get_datetime, getdate, now_datetime, today

VERSION = "usage_report.v1"

# Narrowed 24 Jul on user instruction: "only permission to CEO/MD/Super Admin
# and MD Akram" — Akram's accounts carry the dedicated Usage Report Viewer role
# (no hardcoded emails; see the role seeding step in the deploy notes)
ALLOWED_ROLES = ("CEO", "MD", "Super Admin", "Usage Report Viewer")

DECISION_ACTIONS = ("Approved", "Final Approved", "Rejected")

MAX_SPAN_DAYS = 90
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
