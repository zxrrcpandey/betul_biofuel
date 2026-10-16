# Copyright (c) 2026, Trustbit Software and contributors
# v2.58.0 — "why does this person get these notifications?": one row per role
# the chosen user holds, with that role's routing and whether its role-wide
# notifications reach the user. Read-only.

import frappe
from frappe import _

from trustbit_ethanol.ts_gate_entry.ts_notification_routing import (
	ADMIN_ROLES,
	AUTOMATIC_ROLES,
	SELECTED,
	_ticked_users,
	narrow,
	role_holders,
	skip_accounts,
)


def execute(filters=None):
	frappe.only_for(ADMIN_ROLES)
	filters = frappe._dict(filters or {})
	user = filters.get("user")
	# A dict here would make db.exists a column probe on User, so names only.
	if not isinstance(user, str) or not user or user in ("Administrator", "Guest") or not frappe.db.exists("User", user):
		return _columns(), [], None

	roles = sorted(r for r in frappe.get_roles(user) if r not in AUTOMATIC_ROLES)
	routing = {
		r.role: r for r in frappe.get_all(
			"TS Notification Routing", filters={"role": ["in", roles or [""]]},
			fields=["role", "routing_mode", "ticked_count"])
	}
	enabled = frappe.db.get_value("User", user, "enabled")
	skip = skip_accounts()

	data = []
	for role in roles:
		row = routing.get(role)
		holders = role_holders(role)
		routed = narrow(role, holders)
		# Same rule as role_recipients(): the skip list never leaves a role with nobody.
		notified = [u for u in routed if u not in skip] or routed
		if len(notified) < len(holders):
			who = _("{0} of {1} holders").format(len(notified), len(holders))
		elif len(holders) < 2:
			who = _("1 holder") if holders else _("No enabled holders")
		else:
			who = _("All {0} holders").format(len(holders))
		if not row:
			gets, mode = True, _("All role holders (not set up)")
			reason = _("Not set up in Notification Routing, so every holder is notified.")
		elif row.routing_mode != SELECTED:
			gets, mode = True, _("All role holders")
			reason = _("Set to All role holders, so every holder is notified.")
		else:
			mode = _("Selected users only")
			gets = user in notified
			if not gets:
				reason = _("Not ticked. This role's role-wide notifications are routed away from this user.")
			elif user in set(_ticked_users(role) or []):
				reason = _("Ticked.")
			else:
				reason = _("No ticked user still holds the role, so every holder is notified until the row is fixed.")
		if user in skip and user in routed:
			gets = user in notified
			reason = _("On the skip list in Notification Audience, so this role's role-wide notifications go to the other holders.") if not gets \
				else _("On the skip list, but every other holder is skipped too, so this account is still notified.")
		if not enabled:
			gets, reason = False, _("User is disabled.")
		data.append({
			"role": role, "routing": mode, "notified": who,
			"gets": _("Yes") if gets else _("No"), "reason": reason,
			"_sort": (0 if row else 1, role),
		})
	data.sort(key=lambda d: d.pop("_sort"))

	message = _("Notifications that name {0} personally are always delivered, whatever the routing says: documents they created or requested, cost-centre approval steps that list them, approval chains they acted in, @mentions and assignments.").format(
		frappe.bold(frappe.utils.escape_html(frappe.utils.get_fullname(user))))
	return _columns(), data, message


def _columns():
	return [
		# Data, not Link: a Link → Role column needs Role read, which IT Head lacks.
		{"fieldname": "role", "label": _("Role Held"), "fieldtype": "Data", "width": 200},
		{"fieldname": "routing", "label": _("Routing for the Role"), "fieldtype": "Data", "width": 230},
		{"fieldname": "notified", "label": _("Who Is Notified"), "fieldtype": "Data", "width": 150},
		{"fieldname": "gets", "label": _("Gets Role-Wide Notifications"), "fieldtype": "Data", "width": 190},
		{"fieldname": "reason", "label": _("Reason"), "fieldtype": "Data", "width": 460},
	]
