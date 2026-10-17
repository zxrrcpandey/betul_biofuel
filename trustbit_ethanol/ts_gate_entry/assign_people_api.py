# Copyright (c) 2026, Trustbit Software and contributors
# v2.61.0 — server API for /app/assign-people (IT Head / System Manager).
#
# Department Heads are read from the approval configs, exactly as inspections
# pick them (ts_material_inspection._department_heads); this page never writes
# a config. Notify-only people are kept on TS Notification Audience
# (cc_notify_people), not on the config, so adding someone creates no
# cost-centre User Permission; get_cc_notify_only_users() merges them.

import frappe
from frappe import _
from frappe.utils import add_days, cstr, escape_html, nowdate

from trustbit_ethanol.ts_gate_entry.ts_notification_routing import (
	ADMIN_ROLES,
	AUDIENCE_DOCTYPE,
	EVENTS,
	audience_ticks,
)

HEAD_ROLE = "Department Head"
HEAD_ACTIONS = ("Reviewer", "Final Approver")
_SHORT = {"mr_approved": "MR approved", "inspection_result": "Inspection result"}


def _active_configs():
	"""cost centre → name of its active approval config."""
	return {c.cost_center or c.name: c.name for c in frappe.get_all(
		"TS CC Approval Config", filters={"is_active": 1}, fields=["name", "cost_center"])}


@frappe.whitelist()
def get_assign_people():
	"""Everything the page shows. Read-only."""
	frappe.only_for(ADMIN_ROLES)
	configs = _active_configs()
	cc_of = {name: cc for cc, name in configs.items()}
	rows = frappe.db.sql(
		"""SELECT cu.parent, cu.user, cu.role, cu.action_type, cu.gets_notified, u.full_name
		   FROM `tabTS CC Approval User` cu INNER JOIN `tabUser` u ON u.name = cu.user AND u.enabled = 1
		   WHERE cu.parenttype = 'TS CC Approval Config' AND cu.parent IN %(c)s""",
		{"c": tuple(cc_of) or ("",)}, as_dict=True)
	heads, fixed = {}, {}
	for r in rows:
		cc = cc_of[r.parent]
		if r.role == HEAD_ROLE and r.action_type in HEAD_ACTIONS:
			h = heads.setdefault(r.user, {"user": r.user, "full_name": r.full_name or r.user, "cost_centers": set()})
			h["cost_centers"].add(cc)
		elif r.action_type == "Notify Only" and r.gets_notified:
			fixed.setdefault(cc, {})[r.user] = r.full_name or r.user
	headed = {cc for h in heads.values() for cc in h["cost_centers"]}

	people = {}
	for p in frappe.db.sql(
			"""SELECT p.cost_center, p.user, u.full_name, u.enabled FROM `tabTS Notification CC Person` p
			   LEFT JOIN `tabUser` u ON u.name = p.user
			   WHERE p.parenttype = %s ORDER BY p.idx""", AUDIENCE_DOCTYPE, as_dict=True):
		people.setdefault(p.cost_center, []).append(
			{"user": p.user, "full_name": p.full_name or p.user, "enabled": p.enabled or 0})

	since = add_days(nowdate(), -90)
	used = set(frappe.db.sql_list(
		"""SELECT cost_center FROM `tabMaterial Request` WHERE creation >= %(d)s AND IFNULL(cost_center, '') != ''
		   UNION SELECT cost_center FROM `tabMaterial Request Item` WHERE creation >= %(d)s
		     AND IFNULL(cost_center, '') != ''""", {"d": since}))

	return {
		"heads": sorted(({**h, "cost_centers": sorted(h["cost_centers"])} for h in heads.values()),
			key=lambda h: h["full_name"].lower()),
		"no_head": sorted(cc for cc in configs if cc not in headed),
		"configs": configs,
		"can_open_configs": bool(frappe.has_permission("TS CC Approval Config", "read")),
		"cost_centers": [{"cost_center": cc, "used": cc in used, "people": people.get(cc, []),
			"fixed": [{"user": u, "full_name": n} for u, n in sorted(fixed.get(cc, {}).items())]}
			for cc in sorted(configs)],
		"unconfigured": sorted(used - set(configs)),
		"told_about": [_(_SHORT[e]) for e in EVENTS if e in _SHORT and "cc_notify" in audience_ticks(e)],
	}


@frappe.whitelist(methods=["POST"])
def save_notify_people(rows):
	"""Replace the notify-only people of the cost centres that have an active
	approval config. rows = [{"cost_center", "user"}, ...]; enabled users only.
	Rows of cost centres whose config is no longer active are kept as they are."""
	frappe.only_for(ADMIN_ROLES)
	rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
	if not isinstance(rows, list):
		frappe.throw(_("Send a list of cost centre and person rows."))
	configs = _active_configs()
	enabled = set(frappe.get_all("User", filters={"enabled": 1, "user_type": "System User",
		"name": ["not in", ["Administrator", "Guest"]]}, pluck="name"))
	clean, seen = [], set()
	for r in rows:
		r = r if isinstance(r, dict) else {}
		cc, user = cstr(r.get("cost_center")), cstr(r.get("user"))
		if cc not in configs:
			frappe.throw(_("{0} has no active approval config.").format(frappe.bold(escape_html(cc))))
		if user not in enabled:
			frappe.throw(_("{0} is not an enabled user.").format(frappe.bold(escape_html(user))))
		if (cc, user) not in seen:
			seen.add((cc, user))
			clean.append({"cost_center": cc, "user": user})
	doc = frappe.get_single(AUDIENCE_DOCTYPE)
	kept = [{"cost_center": p.cost_center, "user": p.user} for p in doc.get("cc_notify_people") or []
		if p.cost_center not in configs]
	doc.set("cc_notify_people", kept + clean)
	doc.save()
	return {"saved": len(clean)}
