"""v2.61.0 Assign People page — standalone test suite. Every write happens inside
one transaction that is ROLLED BACK at the end (commits are stubbed).

Covers:
  1. access: IT Head / System Manager only (read and save); save is POST-only;
     the page's roles
  2. Department Heads: exactly the enabled Department Head Reviewers / Final
     Approvers on active configs, the same people inspections pick; no_head is
     every other active config
  3. save: rows land on TS Notification Audience, never on a config (config rows
     and User Permissions unchanged); bad cost centre / user refused with escaped
     names; duplicates dropped; rows of inactive configs kept
  4. merge: get_cc_notify_only_users() adds the page's enabled people once, after
     the config's own Notify Only rows; the MR-approved audience includes them
  5. "Told about" follows the Notify-only users tick

Usage (same shape as test_regression.py):
    cd /home/frappe/frappe-bench/sites && sudo -u frappe env FRAPPE_SITE=<site> \
        /home/frappe/frappe-bench/env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_assign_people.py
Exit code: 0 = all passed, 1 = failures.
"""

import os
import sys

import frappe

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
CFG = "TS CC Approval Config"
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
		frappe.clear_messages()


def _expect_throw(fn, needle, exc=frappe.ValidationError):
	try:
		fn()
	except exc as e:
		assert needle.lower() in str(e).lower(), f"wrong message: {e}"
		frappe.clear_messages()
		return str(e)
	raise AssertionError("expected an error containing: " + needle)


def run():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.db.commit = lambda *a, **k: None

	from trustbit_ethanol.ts_gate_entry import assign_people_api as ap
	from trustbit_ethanol.ts_gate_entry import ts_notification_routing as nr
	from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
		get_cc_notify_only_users)

	AUD = nr.AUDIENCE_DOCTYPE
	configs = ap._active_configs()
	if not configs:
		print("no active approval config on this site")
		sys.exit(1)
	enabled = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User",
		"name": ["not in", ["Administrator", "Guest"]]}, pluck="name", order_by="creation desc")
	admins = set(nr.role_holders("System Manager")) | set(nr.role_holders("IT Head"))
	plain = next((u for u in enabled if u not in admins), None)
	it_head = next((u for u in nr.role_holders("IT Head") if "System Manager" not in frappe.get_roles(u)), None)
	cc1, cc2 = sorted(configs)[0], sorted(configs)[-1]
	p1, p2 = enabled[0], enabled[1]

	def page_rows():
		return [(p.cost_center, p.user) for p in frappe.get_single(AUD).get("cc_notify_people") or []]

	# ── 1. access ──
	def t_access_plain():
		if not plain:
			raise Skip("no enabled user outside IT Head / System Manager")
		frappe.set_user(plain)
		_expect_throw(ap.get_assign_people, "", frappe.PermissionError)
		_expect_throw(lambda: ap.save_notify_people([]), "", frappe.PermissionError)
	test("access: a user without IT Head / System Manager can neither read nor save", t_access_plain)

	def t_access_it_head():
		if not it_head:
			raise Skip("no IT Head without System Manager")
		frappe.set_user(it_head)
		assert ap.get_assign_people()["configs"], "IT Head got no configs"
		assert ap.save_notify_people([])["saved"] == 0
	test("access: IT Head reads and saves", t_access_it_head)

	def t_post_only():
		methods = frappe.allowed_http_methods_for_whitelisted_func.get(ap.save_notify_people)
		assert methods == ["POST"], methods
	test("save is POST-only", t_post_only)

	def t_page_roles():
		if not frappe.db.exists("Page", "assign-people"):
			raise Skip("page not migrated yet")
		roles = {r.role for r in frappe.get_doc("Page", "assign-people").roles}
		assert roles == {"IT Head", "System Manager"}, roles
	test("page roles are IT Head + System Manager", t_page_roles)

	# ── 2. Department Heads ──
	def t_heads_match_configs():
		data = ap.get_assign_people()
		want = {}
		for cc, name in configs.items():
			for r in frappe.get_doc(CFG, name).users:
				if r.user and r.role == "Department Head" and r.action_type in ("Reviewer", "Final Approver") \
						and frappe.db.get_value("User", r.user, "enabled"):
					want.setdefault(r.user, set()).add(cc)
		got = {h["user"]: set(h["cost_centers"]) for h in data["heads"]}
		assert got == want, (sorted(set(got) ^ set(want)))[:5]
		assert set(data["no_head"]) == set(configs) - {cc for s in want.values() for cc in s}
		assert set(data["configs"]) == set(configs)
		assert {r["cost_center"] for r in data["cost_centers"]} == set(configs)
		assert not set(data["unconfigured"]) & set(configs)
	test("heads: exactly the enabled Department Head approvers on active configs", t_heads_match_configs)

	def t_heads_never_written():
		before = {name: frappe.db.get_value(CFG, name, "modified") for name in configs.values()}
		ap.get_assign_people()
		ap.save_notify_people([{"cost_center": cc1, "user": p1}])
		after = {name: frappe.db.get_value(CFG, name, "modified") for name in configs.values()}
		assert before == after, "an approval config was modified"
	test("heads: the page never writes an approval config", t_heads_never_written)

	# ── 3. save ──
	def t_save_roundtrip():
		ups = frappe.db.count("User Permission", {"user": p2})
		cfg_users = [(r.user, r.action_type) for r in frappe.get_doc(CFG, configs[cc2]).users]
		res = ap.save_notify_people([{"cost_center": cc1, "user": p1}, {"cost_center": cc2, "user": p2},
			{"cost_center": cc2, "user": p2}])
		assert res["saved"] == 2, res
		assert set(page_rows()) >= {(cc1, p1), (cc2, p2)}
		assert len(page_rows()) == len(set(page_rows())), "duplicate rows saved"
		assert frappe.db.count("User Permission", {"user": p2}) == ups, "a User Permission was created"
		assert [(r.user, r.action_type) for r in frappe.get_doc(CFG, configs[cc2]).users] == cfg_users
		row = next(r for r in ap.get_assign_people()["cost_centers"] if r["cost_center"] == cc2)
		assert p2 in [p["user"] for p in row["people"]]
	test("save: rows land on the audience, duplicates dropped, no User Permission", t_save_roundtrip)

	def t_save_replaces():
		ap.save_notify_people([{"cost_center": cc1, "user": p1}])
		ap.save_notify_people([])
		assert not [r for r in page_rows() if r[0] in configs], page_rows()
	test("save: the list sent replaces the active cost centres' rows", t_save_replaces)

	def t_save_refuses():
		_expect_throw(lambda: ap.save_notify_people([{"cost_center": "No Such CC <b>x</b>", "user": p1}]),
			"no active approval config")
		msg = _expect_throw(lambda: ap.save_notify_people([{"cost_center": "<img src=x onerror=alert(1)>",
			"user": p1}]), "no active approval config")
		assert "<img" not in msg, msg
		for bad in ("Administrator", "Guest", "nobody@example.invalid"):
			_expect_throw(lambda bad=bad: ap.save_notify_people([{"cost_center": cc1, "user": bad}]),
				"not an enabled user")
		disabled = frappe.db.get_value("User", {"enabled": 0, "user_type": "System User"}, "name")
		if disabled:
			_expect_throw(lambda: ap.save_notify_people([{"cost_center": cc1, "user": disabled}]),
				"not an enabled user")
		_expect_throw(lambda: ap.save_notify_people('{"a": 1}'), "send a list")
		ap.save_notify_people('[{"cost_center": "%s", "user": "%s"}]' % (cc1, p1))  # JSON string form
		assert (cc1, p1) in page_rows()
	test("save: unknown cost centre, disabled/system users and bad payloads refused (escaped)", t_save_refuses)

	def t_inactive_kept():
		ghost = frappe.db.get_value("Cost Center", {"name": ["not in", list(configs)], "is_group": 0}, "name")
		if not ghost:
			raise Skip("every cost centre has an active config")
		doc = frappe.get_single(AUD)
		doc.append("cc_notify_people", {"cost_center": ghost, "user": p1})
		doc.save(ignore_permissions=True)
		ap.save_notify_people([{"cost_center": cc1, "user": p2}])
		assert (ghost, p1) in page_rows(), "row of an inactive cost centre was dropped"
	test("save: rows of cost centres without an active config are kept", t_inactive_kept)

	def t_form_save_keeps_rows():
		ap.save_notify_people([{"cost_center": cc1, "user": p1}])
		frappe.get_single(AUD).save(ignore_permissions=True)
		assert (cc1, p1) in page_rows()
	test("audience form save keeps the notify-only people", t_form_save_keeps_rows)

	# ── 4. merge ──
	def t_merge():
		cfg = frappe.get_doc(CFG, configs[cc1])
		own = list(dict.fromkeys(r.user for r in cfg.users if r.action_type == "Notify Only" and r.gets_notified))
		dup = own[0] if own else p2
		ap.save_notify_people([{"cost_center": cc1, "user": p1}, {"cost_center": cc1, "user": dup}])
		got = get_cc_notify_only_users(cfg)
		assert got[:len(own)] == own, "config rows no longer first"
		assert p1 in got and got.count(dup) == 1, got
		frappe.db.set_value("User", p1, "enabled", 0, update_modified=False)
		try:
			assert p1 not in get_cc_notify_only_users(cfg), "disabled person still notified"
		finally:
			frappe.db.set_value("User", p1, "enabled", 1, update_modified=False)
		other = frappe.get_doc(CFG, configs[cc2]) if cc2 != cc1 else None
		if other:
			own_other = {r.user for r in other.users if r.action_type == "Notify Only" and r.gets_notified}
			assert p1 in own_other or p1 not in get_cc_notify_only_users(other), "leaked to another cost centre"
	test("merge: page people added once, enabled only, this cost centre only", t_merge)

	def t_merge_staff_only():
		# A row written past the page (REST on the form) still reaches staff only.
		web = frappe.db.get_value("User", {"enabled": 1, "user_type": "Website User"}, "name")
		doc = frappe.get_single(AUD)
		doc.set("cc_notify_people", [{"cost_center": cc1, "user": u} for u in ("Administrator", web) if u])
		doc.save(ignore_permissions=True)
		got = get_cc_notify_only_users(frappe.get_doc(CFG, configs[cc1]))
		assert "Administrator" not in got and (not web or web not in got), got
	test("merge: Administrator, Guest and website users never added", t_merge_staff_only)

	def t_mr_approved_audience():
		ap.save_notify_people([{"cost_center": cc1, "user": p1}])
		if "cc_notify" not in nr.audience_ticks("mr_approved"):
			raise Skip("Notify-only users unticked for MR approved on this site")
		got = nr.audience("mr_approved", raised_by=p2, cc_notify=get_cc_notify_only_users(frappe.get_doc(CFG, configs[cc1])))
		assert p1 in got, got
	test("MR approved reaches the page's notify-only people", t_mr_approved_audience)

	# ── 5. told about ──
	def t_told_about():
		doc = frappe.get_single(AUD)
		row = next(r for r in doc.events if r.event == "mr_approved")
		row.cc_notify, row.raised_by = 0, 1
		doc.save(ignore_permissions=True)
		told = ap.get_assign_people()["told_about"]
		assert "MR approved" not in told, told
		row.cc_notify = 1
		doc.save(ignore_permissions=True)
		assert "MR approved" in ap.get_assign_people()["told_about"]
	test("told about follows the Notify-only users tick", t_told_about)

	frappe.db.rollback()
	passed = sum(1 for _n, ok, _e in RESULTS if ok)
	print()
	print("=" * 60)
	print(f"ASSIGN PEOPLE: {passed}/{len(RESULTS)} passed, {len(SKIPPED)} skipped (rolled back)")
	for name, ok, err in RESULTS:
		if not ok:
			print(f"  FAILED: {name} :: {err}")
	sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
	run()
