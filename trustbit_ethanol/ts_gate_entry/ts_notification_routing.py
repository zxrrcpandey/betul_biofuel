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
    narrowed by the role's routing row."""
    return narrow(role, role_holders(role))


def routed_out_roles(user, roles):
    """Roles among `roles` whose role-wide notifications do NOT reach `user`."""
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
    return {r for r in routed if user not in role_recipients(r)}


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
