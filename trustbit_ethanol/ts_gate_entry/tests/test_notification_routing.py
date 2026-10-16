"""v2.58.0 Notification Routing — standalone test suite. Every write happens
inside one transaction that is ROLLED BACK at the end (no data left behind).

Covers:
  1. resolver: no row / All mode / Selected / fail-open / dedup / routed_out_roles
  2. TS Notification Routing.validate(): automatic role, zero ticks, non-holder,
     duplicates, ticked_count, list kept in All mode
  3. wiring: every rerouted helper returns the routed list; the inspection
     department-head lookup (a ROUTING decision) is unaffected
  4. a real bell producer (MR transfer → Stores Manager) bells only ticked users
  5. My Pending Actions: a routed-out role drops its PO queue; a CC-named MR stays
  6. access: get_role_holders + report + DocPerm are IT Head / System Manager only

Usage (same shape as test_regression.py):
    cd /home/frappe/frappe-bench/sites && sudo -u frappe env FRAPPE_SITE=<site> \
        /home/frappe/frappe-bench/env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_notification_routing.py
Exit code: 0 = all passed, 1 = failures.
"""

import os
import sys

import frappe

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
RESULTS = []
SKIPPED = []


class Skip(Exception):
	pass


def test(name, fn):
	frappe.set_user("Administrator")
	try:
		fn()
		RESULTS.append((name, True, ""))
		print(f"  PASS  {name}")
	except Skip as e:
		SKIPPED.append((name, str(e)))
		print(f"  SKIP  {name} :: {e}")
	except Exception as e:  # noqa: BLE001 — a test harness reports every failure
		RESULTS.append((name, False, str(e)[:300]))
		print(f"  FAIL  {name} :: {str(e)[:300]}")
	finally:
		frappe.set_user("Administrator")


def _route(role, users, mode="Selected users only"):
	if frappe.db.exists("TS Notification Routing", role):
		frappe.delete_doc("TS Notification Routing", role, ignore_permissions=True, force=True)
	doc = frappe.get_doc({"doctype": "TS Notification Routing", "role": role, "routing_mode": mode,
		"users": [{"user": u} for u in users]})
	doc.insert(ignore_permissions=True)
	return doc


def _unroute(role):
	if frappe.db.exists("TS Notification Routing", role):
		frappe.delete_doc("TS Notification Routing", role, ignore_permissions=True, force=True)


def _expect_throw(fn, needle):
	try:
		fn()
	except frappe.ValidationError as e:
		assert needle.lower() in str(e).lower(), f"wrong message: {e}"
		frappe.clear_messages()
		return
	raise AssertionError("expected a validation error")


def run():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	from trustbit_ethanol.ts_gate_entry import ts_notification_routing as nr

	# A role with ≥3 enabled holders to route in the generic tests.
	role = next((r for r in ("Purchase User", "Stores Manager", "Stores User", "Department Head", "IT Head")
		if len(nr.role_holders(r)) >= 3), None)
	if not role:
		print("No role with 3+ holders on this site — cannot run.")
		sys.exit(1)
	holders = nr.role_holders(role)
	a, b, c = holders[0], holders[1], holders[2]
	print(f"Generic role: {role} ({len(holders)} holders)")

	# ── 1. resolver ───────────────────────────────────────────────────────────
	def t_no_row():
		_unroute(role)
		assert nr.role_recipients(role) == holders
		assert nr.routed_out_roles(a, [role]) == set()
	test("resolver: no row = every enabled holder (old behaviour)", t_no_row)

	def t_all_mode():
		_route(role, [a], mode="All role holders")
		assert nr.role_recipients(role) == holders
		_unroute(role)
	test("resolver: All role holders row = every holder", t_all_mode)

	def t_selected():
		_route(role, [a, b])
		got = nr.role_recipients(role)
		assert sorted(got) == sorted([a, b]), got
		assert nr.routed_out_roles(c, [role]) == {role}
		assert nr.routed_out_roles(a, [role, "All", "Guest"]) == set()
		_unroute(role)
	test("resolver: Selected users only = exactly the ticked users", t_selected)

	def t_fail_open():
		_route(role, [a])
		frappe.db.set_value("User", a, "enabled", 0, update_modified=False)
		try:
			got = nr.role_recipients(role)
			assert a not in got, "disabled user must never be notified"
			assert sorted(got) == sorted(nr.role_holders(role)), "no eligible ticked user → every holder"
			assert nr.routed_out_roles(b, [role]) == set(), "fail-open means b is notified"
		finally:
			frappe.db.set_value("User", a, "enabled", 1, update_modified=False)
			_unroute(role)
	test("resolver: fail-open when no ticked user is still an enabled holder", t_fail_open)

	def t_narrow():
		_route(role, [a])
		assert nr.narrow(role, [b, a, a, None, c]) == [a], "keeps only ticked, dedups, drops blanks"
		assert nr.narrow(role, [b, c]) == [b, c], "no ticked user in the list → list unchanged"
		assert nr.narrow("Role That Does Not Exist", [b, b]) == [b]
		_unroute(role)
	test("resolver: narrow() dedups, drops blanks, fails open", t_narrow)

	def t_role_renamed():
		# A Role rename updates the Link field but not the row's name.
		_route(role, [a])
		frappe.db.sql("UPDATE `tabTS Notification Routing` SET name = %s WHERE name = %s",
			("Old Name Of " + role, role))
		frappe.db.sql("UPDATE `tabTS Notification Routing User` SET parent = %s WHERE parent = %s",
			("Old Name Of " + role, role))
		assert nr.role_recipients(role) == [a], "routing must follow the role field"
		assert nr.routed_out_roles(b, [role]) == {role}
		frappe.db.sql("DELETE FROM `tabTS Notification Routing User` WHERE parent = %s", ("Old Name Of " + role,))
		frappe.db.sql("DELETE FROM `tabTS Notification Routing` WHERE name = %s", ("Old Name Of " + role,))
	test("resolver: routing survives a Role rename (matched on the role field)", t_role_renamed)

	# ── 2. validation ─────────────────────────────────────────────────────────
	test("validate: automatic role refused",
		lambda: _expect_throw(lambda: _route("All", [a]), "automatic role"))
	test("validate: Selected with zero ticks refused (a role cannot be muted)",
		lambda: _expect_throw(lambda: _route(role, []), "at least one"))

	def t_non_holder():
		outsider = frappe.db.sql_list(
			"""SELECT name FROM tabUser WHERE enabled = 1 AND user_type = 'System User'
			   AND name NOT IN ('Administrator', 'Guest') AND name NOT IN %s LIMIT 1""", (tuple(holders),))
		if not outsider:
			raise Skip("every system user holds the role")
		_expect_throw(lambda: _route(role, [a, outsider[0]]), "no longer hold")
	test("validate: user who does not hold the role refused", t_non_holder)

	def t_dedup_count():
		doc = _route(role, [a, a, b])
		assert [r.user for r in doc.users] == [a, b]
		assert doc.ticked_count == 2
		doc.routing_mode = "All role holders"
		doc.save(ignore_permissions=True)
		assert doc.ticked_count == 0 and len(doc.users) == 2, "list kept, count 0 in All mode"
		_unroute(role)
	test("validate: duplicates removed, ticked_count, list kept in All mode", t_dedup_count)

	# ── 3. wiring: every rerouted helper follows the routing row ─────────────
	from trustbit_ethanol.ts_gate_entry import ts_budget, ts_budget_override, ts_po_approval, ts_whatsapp_reminders
	from trustbit_ethanol.ts_gate_entry.doctype.ts_deduction_suggestion.ts_deduction_suggestion import (
		get_grain_manager_recipients,
	)

	def t_helpers():
		_route(role, [a])
		for fn in (ts_po_approval._get_role_users, ts_whatsapp_reminders._role_users,
				   ts_budget._get_role_users, ts_budget_override._role_users):
			assert fn(role) == [a], f"{fn.__module__}.{fn.__name__} → {fn(role)}"
		_unroute(role)
		for fn in (ts_po_approval._get_role_users, ts_whatsapp_reminders._role_users,
				   ts_budget._get_role_users, ts_budget_override._role_users):
			assert sorted(fn(role)) == sorted(holders), f"{fn.__name__} unrouted"
	test("wiring: PO/MR, WhatsApp-escalation, budget, budget-override helpers", t_helpers)

	def t_grain():
		gm = nr.role_holders("Grain Manager")
		if len(gm) < 2:
			raise Skip("needs 2+ Grain Managers")
		assert sorted(get_grain_manager_recipients()) == sorted(gm)
		_route("Grain Manager", [gm[0]])
		assert get_grain_manager_recipients() == [gm[0]]
		_unroute("Grain Manager")
	test("wiring: deduction-suggestion Grain Manager recipients", t_grain)

	def t_budget_override_enabled():
		_unroute(role)
		frappe.db.set_value("User", c, "enabled", 0, update_modified=False)
		try:
			assert c not in ts_budget_override._role_users(role), "disabled user was belled before v2.58.0"
		finally:
			frappe.db.set_value("User", c, "enabled", 1, update_modified=False)
	test("wiring: budget-override recipients are now enabled-only", t_budget_override_enabled)

	def t_stage_recipients():
		step = frappe.db.sql(
			"""SELECT s.parent, s.step_order, s.role FROM `tabTS PO Approval Step` s
			   WHERE s.parenttype = 'TS PO Approval Rule' AND IFNULL(s.role, '') != '' ORDER BY s.parent, s.step_order""",
			as_dict=True)
		pick = next((s for s in step if len(nr.role_holders(s.role)) >= 2), None)
		if not pick:
			raise Skip("no PO step role with 2+ holders")
		h = nr.role_holders(pick.role)
		row = frappe._dict(ts_approval_rule=pick.parent, ts_current_step=pick.step_order)
		frappe.clear_document_cache("TS PO Approval Rule", pick.parent)
		before = ts_whatsapp_reminders._stage_recipients(row, 1, True, "ts_current_step", "ts_approval_rule")
		assert sorted(before) == sorted(h), (before, h)
		_route(pick.role, [h[1]])
		after = ts_whatsapp_reminders._stage_recipients(row, 1, True, "ts_current_step", "ts_approval_rule")
		assert after == [h[1]], after
		_unroute(pick.role)
	test("wiring: overdue-approval stage recipients (bells + WhatsApp escalation)", t_stage_recipients)

	def t_overdue_mr_keeps_cc_named():
		from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
			get_cc_config,
			get_cc_users_for_step,
		)
		mrs = frappe.db.sql(
			"""SELECT mr.name, mr.cost_center, mr.ts_mr_current_step, mr.ts_mr_approval_route
			   FROM `tabMaterial Request` mr
			   WHERE mr.docstatus = 0 AND mr.ts_mr_status LIKE 'Pending%%'
			     AND IFNULL(mr.ts_mr_approval_route, '') != '' AND IFNULL(mr.cost_center, '') != ''
			   ORDER BY mr.modified DESC LIMIT 60""", as_dict=True)
		for mr in mrs:
			cfg = get_cc_config(mr.cost_center)
			if not cfg:
				continue
			route = frappe.get_cached_doc("TS MR Approval Route", mr.ts_mr_approval_route)
			step = next((s for s in route.approval_steps
				if s.step_order == frappe.utils.cint(mr.ts_mr_current_step) and s.role), None)
			if not step:
				continue
			hs = nr.role_holders(step.role)
			named = [u for u in get_cc_users_for_step(cfg, step.step_order, route=route) if u in hs]
			others = [u for u in hs if u not in named]
			if not named or not others:
				continue
			row = frappe._dict(name=mr.name, ts_mr_current_step=mr.ts_mr_current_step,
				ts_mr_approval_route=mr.ts_mr_approval_route)
			args = (row, 1, False, "ts_mr_current_step", "ts_mr_approval_route")
			assert sorted(ts_whatsapp_reminders._stage_recipients(*args)) == sorted(hs), "no rows → unchanged"
			_route(step.role, [others[0]])
			got = ts_whatsapp_reminders._stage_recipients(*args)
			_unroute(step.role)
			assert others[0] in got and set(named) <= set(got), (got, named)
			assert set(got) == {others[0], *named}, got
			return
		raise Skip("no pending CC-config MR with both a named and an un-named step-role holder")
	test("wiring: overdue MR reminder keeps the cost-centre-named approver", t_overdue_mr_keeps_cc_named)

	def t_department_head_untouched():
		from trustbit_ethanol.ts_gate_entry.doctype.ts_material_inspection import ts_material_inspection as mi
		dh = nr.role_holders("Department Head")
		if len(dh) < 2:
			raise Skip("needs 2+ Department Heads")
		# Routing must never change who is ASSIGNED as department head.
		_route("Department Head", [dh[0]])
		assert sorted(mi._get_role_users("Department Head")) == sorted(dh), "assignment lookup must stay unrouted"
		assert mi.role_recipients("Department Head") == [dh[0]]
		_unroute("Department Head")
	test("wiring: inspection department-head ASSIGNMENT lookup stays unrouted", t_department_head_untouched)

	# ── 4. a real producer end to end (no email/WhatsApp in this path) ───────
	def t_mr_transfer_bells():
		from trustbit_ethanol.ts_gate_entry import ts_mr_transfer
		sm = nr.role_holders("Stores Manager")
		mr = frappe.db.get_value("Material Request", {"docstatus": ["<", 2]}, "name")
		if len(sm) < 2 or not mr:
			raise Skip("needs 2+ Stores Managers and an MR")

		def belled():
			return set(frappe.get_all("Notification Log", filters={
				"document_type": "Material Request", "document_name": mr,
				"subject": ["like", "%pending your approval"], "creation": [">=", start]}, pluck="for_user"))
		start = frappe.utils.now_datetime()
		_unroute("Stores Manager")
		ts_mr_transfer._notify_stores_managers_system(mr)
		assert belled() == set(sm), belled()
		frappe.db.delete("Notification Log", {"document_type": "Material Request", "document_name": mr,
			"creation": [">=", start]})
		_route("Stores Manager", [sm[1]])
		ts_mr_transfer._notify_stores_managers_system(mr)
		assert belled() == {sm[1]}, belled()
		_unroute("Stores Manager")
	test("producer: MR transfer bells only the ticked Stores Manager", t_mr_transfer_bells)

	# ── 5. My Pending Actions ────────────────────────────────────────────────
	from trustbit_ethanol.ts_gate_entry import notification_center_api as nc

	def _pending(user, category):
		frappe.set_user(user)
		try:
			return {i["doc_name"] for i in nc.get_pending_actions(category=category, page_length=50)["items"]}
		finally:
			frappe.set_user("Administrator")

	def t_pending_po():
		# A user whose ONLY PO-step role is R, with R held by someone else too.
		step_roles = set(frappe.db.sql_list(
			"SELECT DISTINCT role FROM `tabTS PO Approval Step` WHERE IFNULL(role, '') != ''"))
		for r in step_roles:
			hs = nr.role_holders(r)
			if len(hs) < 2:
				continue
			for u in hs:
				if set(frappe.get_roles(u)) & step_roles != {r}:
					continue
				before = _pending(u, "PO")
				if not before:
					continue
				other = next(x for x in hs if x != u)
				_route(r, [other])
				after = _pending(u, "PO")
				_unroute(r)
				assert after == set(), f"{u} still sees {after} after {r} was routed away"
				assert _pending(u, "PO") == before, "unrouting restores the queue"
				return
		raise Skip("no pending PO for a single-step-role user")
	test("pending actions: a routed-out role no longer feeds the PO queue", t_pending_po)

	def t_pending_mr_cc_named():
		rows = frappe.db.sql(
			"""SELECT cu.user, cu.role, mr.name FROM `tabMaterial Request` mr
			   INNER JOIN `tabTS CC Approval Config` cc ON cc.cost_center = mr.cost_center AND cc.is_active = 1
			   INNER JOIN `tabTS CC Approval User` cu ON cu.parent = cc.name
			     AND cu.action_type IN ('Reviewer', 'Final Approver') AND IFNULL(cu.role, '') != ''
			   WHERE mr.docstatus = 0 AND mr.ts_mr_status LIKE 'Pending%%' LIMIT 50""", as_dict=True)
		for row in rows:
			before = _pending(row.user, "MR")
			hs = nr.role_holders(row.role)
			if row.name not in before or len(hs) < 2:
				continue
			other = next(x for x in hs if x != row.user)
			_route(row.role, [other])
			after = _pending(row.user, "MR")
			_unroute(row.role)
			assert row.name in after, f"{row.user} lost CC-named MR {row.name}"
			return
		raise Skip("no pending CC-config MR visible to a named reviewer")
	test("pending actions: an MR whose cost centre names the user stays", t_pending_mr_cc_named)

	def t_pending_unchanged_without_rows():
		frappe.db.delete("TS Notification Routing User")
		frappe.db.delete("TS Notification Routing")
		u = holders[0]
		assert nc.routed_out_roles(u, frappe.get_roles(u)) == set()
	test("pending actions: no routing rows → nothing routed out", t_pending_unchanged_without_rows)

	# ── 6. access ────────────────────────────────────────────────────────────
	admin_roles = {"IT Head", "System Manager"}
	plain = frappe.db.sql_list(
		"""SELECT u.name FROM tabUser u WHERE u.enabled = 1 AND u.user_type = 'System User'
		   AND u.name NOT IN ('Administrator', 'Guest')
		   AND NOT EXISTS (SELECT 1 FROM `tabHas Role` h WHERE h.parent = u.name
		     AND h.role IN ('IT Head', 'System Manager')) LIMIT 1""")
	it_head = next((u for u in nr.role_holders("IT Head")), None)

	def t_getter_denied():
		if not plain:
			raise Skip("no non-admin user")
		frappe.set_user(plain[0])
		try:
			nr.get_role_holders(role)
		except frappe.PermissionError:
			frappe.clear_messages()
			return
		raise AssertionError("non-admin read role holders")
	test("access: get_role_holders refused for a non-admin", t_getter_denied)

	def t_getter_ok():
		if not it_head:
			raise Skip("no IT Head")
		frappe.set_user(it_head)
		got = nr.get_role_holders(role)["holders"]
		assert sorted(h["user"] for h in got) == sorted(holders)
		assert all(set(h) >= {"full_name", "other_roles", "bells_30d", "read_pct", "last_active"} for h in got)
	test("access: get_role_holders works for IT Head", t_getter_ok)

	def t_user_search():
		it_only = next((u for u in nr.role_holders("IT Head")
			if "System Manager" not in frappe.get_roles(u)), None)
		if not it_only:
			raise Skip("no IT Head without System Manager")
		frappe.set_user(it_only)
		got = [r[0] for r in nr.user_search("User", "", "name", 0, 500, {})]
		assert len(got) > 1 and not {"Administrator", "Guest"} & set(got), got[:5]
		if plain:
			frappe.set_user(plain[0])
			try:
				nr.user_search("User", "", "name", 0, 20, {})
				raise AssertionError("non-admin listed users")
			except frappe.PermissionError:
				frappe.clear_messages()
		frappe.set_user("Administrator")
	test("access: report User picker lists everyone for IT Head, refused for others", t_user_search)

	def t_report():
		from trustbit_ethanol.ts_gate_entry.report.ts_notification_routing_check import (
			ts_notification_routing_check as rep,
		)
		_route(role, [a])
		frappe.set_user(it_head or "Administrator")
		cols, data, msg = rep.execute({"user": b})
		row = next(d for d in data if d["role"] == role)
		assert row["gets"] == "No" and "Not ticked" in row["reason"], row
		assert row["notified"] == "1 of {0} holders".format(len(nr.role_holders(role))), row
		other = next((d for d in data if d["role"] != role), None)
		assert not other or " of " not in other["notified"], other
		cols, data, msg = rep.execute({"user": a})
		assert next(d for d in data if d["role"] == role)["gets"] == "Yes"
		assert data[0]["role"] == role, "routed roles listed first"
		assert rep.execute({"user": {"name": a}})[1] == [], "dict user filter must be refused"
		if plain:
			frappe.set_user(plain[0])
			try:
				rep.execute({"user": a})
				raise AssertionError("non-admin ran the report")
			except frappe.PermissionError:
				frappe.clear_messages()
		frappe.set_user("Administrator")
		_unroute(role)
	test("access: Routing Check report — IT Head yes, others no; reasons correct", t_report)

	# IT Head WITHOUT System Manager usually has no read on Role — the whole
	# feature must still work for them (role picker, save, report).
	it_only = next((u for u in nr.role_holders("IT Head")
		if "System Manager" not in frappe.get_roles(u)), None)

	def t_it_head_only_flow():
		if not it_only:
			raise Skip("no IT-Head-only user")
		from frappe.desk.query_report import run as run_report
		from frappe.desk.search import search_widget
		frappe.set_user(it_only)
		found = [r[0] for r in search_widget("Role", role[:4], query=(
			"trustbit_ethanol.ts_gate_entry.ts_notification_routing.role_search"))]
		assert role in found, found
		assert not set(found) & {"All", "Guest", "Administrator", "Desk User"}
		doc = frappe.get_doc({"doctype": "TS Notification Routing", "role": role,
			"routing_mode": "Selected users only", "users": [{"user": a}]}).insert()
		assert doc.ticked_count == 1
		res = run_report("TS Notification Routing Check", filters={"user": b})
		assert next(r for r in res["result"] if r["role"] == role)["gets"] == "No"
		frappe.set_user("Administrator")
		_unroute(role)
	test("access: IT-Head-only user can pick a role, save a row and run the report", t_it_head_only_flow)

	def t_role_search_denied():
		if not plain:
			raise Skip("no non-admin user")
		frappe.set_user(plain[0])
		try:
			nr.role_search("Role", "", "name", 0, 20, None)
		except frappe.PermissionError:
			frappe.clear_messages()
			return
		raise AssertionError("non-admin listed roles")
	test("access: role picker refused for a non-admin", t_role_search_denied)

	def t_docperm():
		if plain:
			assert not frappe.has_permission("TS Notification Routing", "read", user=plain[0])
			assert not frappe.has_permission("TS Notification Routing", "write", user=plain[0])
		if it_head:
			for p in ("read", "write", "create", "delete", "report"):
				assert frappe.has_permission("TS Notification Routing", p, user=it_head), p
		perm_roles = {p.role for p in frappe.get_meta("TS Notification Routing").permissions}
		assert perm_roles == admin_roles, perm_roles
	test("access: DocPerm = IT Head + System Manager only", t_docperm)

	frappe.db.rollback()
	passed = sum(1 for r in RESULTS if r[1])
	print("\n" + "=" * 60)
	print(f"NOTIFICATION ROUTING: {passed}/{len(RESULTS)} passed, {len(SKIPPED)} skipped (rolled back)")
	for name, ok, err in RESULTS:
		if not ok:
			print(f"  FAILED: {name} :: {err}")
	sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
	run()
