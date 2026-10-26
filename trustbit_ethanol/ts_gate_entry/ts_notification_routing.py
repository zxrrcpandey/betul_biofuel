# Copyright (c) 2026, Trustbit Software and contributors
# Notification Routing (v2.58.0) — narrows ROLE-WIDE notifications to the users
# IT Head / System Manager ticked for that role (TS Notification Routing, one row
# per role).
#
# Only role broadcasts come through here. Recipients named on the document
# itself (owner, requester, CC-config approver, approval chain, @mention) never
# do, so routing can never stop a personally addressed notification. A role
# with no row — or a row set to "All role holders" — notifies every enabled
# holder, which is exactly the behaviour before this module existed.
#
# v2.59.0 — TS Notification Audience (Single): per notification, which groups
# receive it (audience()), and a skip list of shared admin accounts that
# role_recipients() leaves out of every role-wide list.

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

ROUTING_DOCTYPE = "TS Notification Routing"
SELECTED = "Selected users only"
ADMIN_ROLES = ("IT Head", "System Manager")
AUTOMATIC_ROLES = ("All", "Guest", "Administrator", "Desk User")


def role_holders(role):
    """Enabled users holding `role` (Administrator/Guest excluded)."""
    if not role:
        return []
    return frappe.db.sql_list(
        """SELECT DISTINCT hr.parent
           FROM `tabHas Role` hr
           INNER JOIN `tabUser` u ON u.name = hr.parent
           WHERE hr.role = %s AND hr.parenttype = 'User'
             AND u.enabled = 1
             AND hr.parent NOT IN ('Administrator', 'Guest')""",
        role,
    )


def _ticked_users(role):
    """Ticked users when `role` is routed to selected users, else None.
    A missing table (code deployed before its migrate) reads as "not routed".
    Matched on the `role` field, not the name: a Role rename updates the Link
    field but leaves the row named after the old role."""
    try:
        users = frappe.db.sql_list(
            """SELECT ru.user
               FROM `tabTS Notification Routing` r
               INNER JOIN `tabTS Notification Routing User` ru
                 ON ru.parent = r.name AND ru.parenttype = 'TS Notification Routing'
               WHERE r.role = %s AND r.routing_mode = %s""",
            (role, SELECTED),
        )
    except Exception:
        return None
    return users or None


def narrow(role, users):
    """Keep only the ticked users of a routed role. Fail-open: when none of the
    ticked users is in `users` (all disabled / lost the role) every user in
    `users` is kept — a role is never muted by configuration."""
    users = list(dict.fromkeys(u for u in (users or []) if u))
    ticked = _ticked_users(role)
    if not ticked:
        return users
    ticked = set(ticked)
    return [u for u in users if u in ticked] or users


def role_recipients(role):
    """Who receives `role`'s role-wide notifications: its enabled holders,
    narrowed by the role's routing row, minus the skip list — unless that
    would leave nobody (v2.59.0)."""
    routed = narrow(role, role_holders(role))
    skip = skip_accounts()
    return [u for u in routed if u not in skip] or routed


def routed_out_roles(user, roles):
    """Roles among `roles` whose role-wide notifications do NOT reach `user`.
    Routing only, without the skip list. Pending Actions no longer uses this
    (v2.60.0 classifies items with role_recipients)."""
    roles = [r for r in (roles or []) if r and r not in AUTOMATIC_ROLES]
    if not roles:
        return set()
    try:
        routed = frappe.db.sql_list(
            """SELECT role FROM `tabTS Notification Routing`
               WHERE routing_mode = %s AND role IN %s""",
            (SELECTED, tuple(roles)),
        )
    except Exception:
        return set()
    return {r for r in routed if user not in narrow(r, role_holders(r))}


AUDIENCE_DOCTYPE = "TS Notification Audience"
COLUMNS = ("raised_by", "dept_head", "cc_notify", "team", "it_head")
# v2.59.0 — code-owned catalogue: label, columns that apply, the seeded ticks
# (= the recipients before v2.59.0) and the Team's roles. "action": someone has
# to act, so an unticked Team still gets it when nobody ticked holds a team role.
EVENTS = {
    "inspection_request": {"label": "Material inspection needed (first notice and reminders)",
                           "applies": ("raised_by", "dept_head"), "default": ("raised_by", "dept_head")},
    "inspection_final": {"label": "Material inspection final notice (GRN goes ahead)",
                         "applies": ("raised_by", "dept_head", "it_head"),
                         "default": ("raised_by", "dept_head", "it_head")},
    "inspection_result": {"label": "Material inspection result",
                          "applies": ("raised_by", "dept_head", "cc_notify", "team"), "default": ("team",),
                          "team_roles": ("Stores User", "G2 Gate Operator")},
    "mr_approved": {"label": "Material request approved (raise the PO)",
                    "applies": ("raised_by", "dept_head", "cc_notify", "team"),
                    "default": ("raised_by", "dept_head", "cc_notify", "team"),
                    "team_roles": ("Purchase Manager", "Purchase User"), "action": True},
    "rgp_overdue": {"label": "Returnable gate passes overdue (daily digest)",
                    "applies": ("raised_by", "team"), "default": ("raised_by", "team"),
                    "team_roles": ("Stores User", "Stores Manager")},
    "rgp_back_at_gate": {"label": "Returnable gate pass material back at the plant gate",
                         "applies": ("raised_by", "team"), "default": ("team",),
                         "team_roles": ("Stores User", "Stores Manager"), "action": True},
}


def skip_accounts():
    """Accounts on the Notification Audience skip list. Fail-open: an
    unreadable list (e.g. before its migrate) skips nobody."""
    try:
        return set(frappe.db.sql_list(
            """SELECT user FROM `tabTS Notification Routing User`
               WHERE parenttype = %s AND parentfield = 'skip_accounts'""",
            AUDIENCE_DOCTYPE))
    except Exception:
        return set()


def audience_ticks(event):
    """The ticked columns of `event`. Fail-open: no table, no row or nothing
    ticked reads as the defaults (today's recipients)."""
    spec = EVENTS[event]
    try:
        row = frappe.db.sql(
            """SELECT {0} FROM `tabTS Notification Audience Event`
               WHERE parenttype = %s AND event = %s
               ORDER BY idx LIMIT 1""".format(", ".join("`%s`" % c for c in COLUMNS)),
            (AUDIENCE_DOCTYPE, event), as_dict=True)
    except Exception:
        row = None
    ticks = {c for c in spec["applies"] if row and cint(row[0].get(c))}
    return ticks or set(spec["default"])


def team_recipients(event):
    out = []
    for role in EVENTS[event].get("team_roles", ()):
        out.extend(role_recipients(role))
    return list(dict.fromkeys(out))


def _holds_any(users, roles):
    """Does any enabled user in `users` hold one of `roles`? An error reads as
    no, so an action notification falls back to its Team."""
    users = [u for u in users if u]
    if not users or not roles:
        return False
    try:
        return bool(frappe.db.sql(
            """SELECT 1 FROM `tabHas Role` hr
               INNER JOIN `tabUser` u ON u.name = hr.parent
               WHERE hr.parenttype = 'User' AND u.enabled = 1
                 AND hr.parent IN %s AND hr.role IN %s LIMIT 1""",
            (tuple(users), tuple(roles))))
    except Exception:
        return False


def audience(event, raised_by=None, dept_heads=(), cc_notify=()):
    """Recipients of `event` as ticked in TS Notification Audience. The people
    passed in by name are never removed by routing or the skip list. Never
    raises: a notification must not undo the action that raised it (the MR
    approval calls this after its submit)."""
    out, queued = [], len(frappe.local.message_log)
    try:
        spec = EVENTS[event]
        ticks = audience_ticks(event)
        if "raised_by" in ticks and raised_by:
            out.append(raised_by)
        if "dept_head" in ticks:
            out.extend(dept_heads or ())
        if "cc_notify" in ticks:
            out.extend(cc_notify or ())
        if "team" in ticks or (spec.get("action") and not _holds_any(out, spec["team_roles"])):
            out.extend(team_recipients(event))
        if "it_head" in ticks:
            out.extend(role_recipients("IT Head"))
    except Exception:
        frappe.log_error(title="Notification audience failed: {0}".format(event),
                         message=frappe.get_traceback()[:2000])
        # Drop only this call's messages; the caller's (e.g. the MR approval's) stay.
        del frappe.local.message_log[queued:]
    return list(dict.fromkeys(u for u in out if u))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def role_search(doctype, txt, searchfield, start, page_len, filters):
    """Role picker for the routing form. IT Head usually cannot read Role, so
    the stock Link search would refuse it; this one is admin-only instead."""
    frappe.only_for(ADMIN_ROLES)
    return frappe.get_all(
        "Role",
        filters=[["disabled", "=", 0], ["desk_access", "=", 1],
                 ["name", "not in", AUTOMATIC_ROLES], ["name", "like", f"%{txt}%"]],
        fields=["name"], order_by="name asc",
        limit_start=cint(start), limit_page_length=cint(page_len) or 20, as_list=True)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def user_search(doctype, txt, searchfield, start, page_len, filters):
    """User picker for the Routing Check report. IT Head usually cannot read
    other Users, so the stock User search would list only themselves."""
    frappe.only_for(ADMIN_ROLES)
    return frappe.get_all(
        "User",
        filters=[["enabled", "=", 1], ["user_type", "=", "System User"],
                 ["name", "not in", ("Administrator", "Guest")]],
        or_filters=[["name", "like", f"%{txt}%"], ["full_name", "like", f"%{txt}%"]],
        fields=["name", "full_name"], order_by="full_name asc",
        limit_start=cint(start), limit_page_length=cint(page_len) or 20, as_list=True)


@frappe.whitelist()
def get_role_holders(role):
    """Read-only panel data for the routing form: every enabled holder of
    `role` with their 30-day notification load. IT Head / System Manager only."""
    frappe.only_for(ADMIN_ROLES)
    if not role or not frappe.db.exists("Role", role):
        return {"holders": []}
    holders = role_holders(role)
    if not holders:
        return {"holders": []}

    info = {
        r.name: r for r in frappe.get_all(
            "User", filters={"name": ["in", holders]},
            fields=["name", "full_name", "last_active"])
    }
    other_roles = dict(frappe.db.sql(
        """SELECT parent, COUNT(DISTINCT role) FROM `tabHas Role`
           WHERE parenttype = 'User' AND parent IN %s
             AND role != %s AND role NOT IN %s
           GROUP BY parent""",
        (tuple(holders), role, AUTOMATIC_ROLES)))
    # Python-side cutoff: stored timestamps are IST while prod SQL NOW() is UTC (L440).
    since = add_to_date(now_datetime(), days=-30)
    load = {
        r[0]: (cint(r[1]), cint(r[2])) for r in frappe.db.sql(
            """SELECT for_user, COUNT(*), SUM(`read`) FROM `tabNotification Log`
               WHERE for_user IN %s AND creation >= %s
               GROUP BY for_user""",
            (tuple(holders), since))
    }
    out = []
    for user in holders:
        u = info.get(user) or frappe._dict()
        bells, read = load.get(user, (0, 0))
        out.append({
            "user": user,
            "full_name": u.get("full_name") or user,
            "other_roles": cint(other_roles.get(user)),
            "bells_30d": bells,
            "read_pct": round(100.0 * read / bells) if bells else None,
            "last_active": frappe.utils.pretty_date(u.last_active) if u.get("last_active") else "",
        })
    out.sort(key=lambda h: (h["full_name"] or "").lower())
    return {"holders": out}
