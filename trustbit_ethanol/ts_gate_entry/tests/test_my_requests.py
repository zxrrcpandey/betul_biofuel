"""v2.62.0 My Requests (Notification Center) — standalone test suite. Read-only;
the transaction is rolled back at the end anyway (commits are stubbed).

Covers:
  1. self-scoped: no user parameter; every row is the caller's own MR (owner or
     submitter); bad state / page_length fall back
  2. completeness: every pending / On Hold MR the caller raised and can still
     open is in "In approval"; counts match the lists; "Past target" is a subset
  3. the oracle: for a pending MR the people "With …" names are exactly the
     people whose "Assigned to me" holds that MR (named approver or a recipient
     of the current step's role)
  4. paging: pages never repeat or skip a row

Usage (same shape as test_regression.py):
    cd /home/frappe/frappe-bench/sites && sudo -u frappe env FRAPPE_SITE=<site> \
        /home/frappe/frappe-bench/env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_my_requests.py
Exit code: 0 = all passed, 1 = failures.
"""

import inspect
import os
import sys

import frappe
from frappe.utils import cint

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
		frappe.clear_messages()


def run():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.db.commit = lambda *a, **k: None

	from trustbit_ethanol.ts_gate_entry import notification_center_api as nca

	# Requesters with something in approval, busiest first.
	requesters = [r[0] for r in frappe.db.sql(
		"""SELECT u.name FROM `tabMaterial Request` d JOIN `tabUser` u ON u.name = d.owner
		   WHERE d.docstatus = 0 AND u.enabled = 1 AND u.name != 'Administrator'
		     AND (d.ts_mr_status LIKE 'Pending%%' OR d.ts_mr_status = 'On Hold')
		   GROUP BY u.name ORDER BY COUNT(*) DESC LIMIT 6""")]
	if not requesters:
		print("no pending Material Request on this site")
		sys.exit(1)

	def mine(user, state):
		frappe.set_user(user)
		try:
			out, start = [], 0
			while True:
				p = nca.get_my_requests(state=state, start=start, page_length=50)
				out += p["items"]
				start += 50
				if start >= p["total"]:
					return out, p
		finally:
			frappe.set_user("Administrator")

	# ── 1. self-scoped ──
	def t_no_user_param():
		params = inspect.signature(nca.get_my_requests).parameters
		assert "user" not in params, list(params)
	test("self-scoped: get_my_requests takes no user parameter", t_no_user_param)

	def t_only_own():
		for u in requesters:
			for st in ("approval", "back", "decided", "draft"):
				for it in mine(u, st)[0]:
					owner, sub = frappe.db.get_value("Material Request", it["doc_name"], ["owner", "ts_mr_submitted_by"])
					assert u in (owner, sub), (u, it["doc_name"], owner, sub)
	test("self-scoped: every row is the caller's own MR", t_only_own)

	def t_fallbacks():
		frappe.set_user(requesters[0])
		p = nca.get_my_requests(state="'; DROP", page_length=-5)
		assert p["state"] == "approval" and len(p["items"]) <= 50, (p["state"], len(p["items"]))
	test("bad state and page_length fall back to In approval / 50", t_fallbacks)

	# ── 2. completeness ──
	def t_complete():
		for u in requesters:
			# Only MRs the caller can still open (a cost-centre User Permission can
			# change after they raised it) — the row's Open link must work.
			want = {n for n in frappe.db.sql_list(
				"""SELECT name FROM `tabMaterial Request` WHERE docstatus = 0
				   AND (owner = %(u)s OR ts_mr_submitted_by = %(u)s)
				   AND (ts_mr_status LIKE 'Pending%%' OR ts_mr_status = 'On Hold')""", {"u": u})
				if frappe.has_permission("Material Request", "read", doc=n, user=u)}
			got, p = mine(u, "approval")
			names = [it["doc_name"] for it in got]
			assert set(names) == want, (u, sorted(want - set(names))[:5], sorted(set(names) - want)[:5])
			assert len(names) == len(set(names)) == p["counts"]["approval"], (u, len(names), p["counts"])
			late, _ = mine(u, "late")
			assert {it["doc_name"] for it in late} <= set(names), u
			assert len(late) == p["counts"]["late"] == sum(it["late"] for it in got), (u, len(late), p["counts"])
	test("every pending / On Hold MR of the caller is listed; counts match; Past target is a subset",
		t_complete)

	# ── 3. oracle: "With …" = whose Assigned to me holds it ──
	def assigned_mrs(user, memo={}):
		if user not in memo:
			frappe.set_user(user)
			try:
				got, start = set(), 0
				while True:
					p = nca.get_pending_actions(scope="assigned", category="MR", start=start, page_length=50)
					got |= {i["doc_name"] for i in p["items"]}
					start += 50
					if start >= p["total"]:
						break
				memo[user] = got
			finally:
				frappe.set_user("Administrator")
		return memo[user]

	def t_oracle():
		checked = 0
		for u in requesters:
			for name in frappe.db.sql_list(
					"""SELECT name FROM `tabMaterial Request` WHERE docstatus = 0 AND owner = %s
					   AND ts_mr_status LIKE 'Pending%%' AND ts_mr_status != 'Pending Budget Override'
					   ORDER BY modified DESC LIMIT 4""", u):
				d = frappe.db.get_value("Material Request", name,
					["name", "ts_mr_status AS status", "ts_mr_current_step AS step", "ts_mr_approval_route AS rule",
					 "cost_center", "owner", "ts_mr_submitted_by AS sub", "ts_mr_held_by AS held_by"], as_dict=True)
				d.step = cint(d.step) or 1
				d.cost_center = d.cost_center or frappe.db.get_value(
					"Material Request Item", {"parent": name, "cost_center": ["is", "set"]}, "cost_center")
				steps = {}
				for s in frappe.get_all("TS MR Approval Step", filters={"parent": d.rule},
						fields=["parent", "step_order", "role", "strict_step"], order_by="idx asc"):
					steps.setdefault(s.parent, []).append(s)
				who, label = nca._my_waiting_on(d, steps, {})
				who = [w for w in who if w not in (d.owner, d.sub)
					and frappe.db.get_value("User", w, "enabled")][:3]
				for w in who:
					assert name in assigned_mrs(w), (name, d.status, w, "listed as 'With' but not in their Assigned to me")
					checked += 1
		if not checked:
			raise Skip("no pending MR with an approver to check")
		print(f"        ({checked} MR × approver pairs checked)")
	test("oracle: everyone named 'With' has the MR in their Assigned to me", t_oracle)

	# ── 4. paging ──
	def t_paging():
		u = requesters[0]
		frappe.set_user(u)
		full = nca.get_my_requests(state="approval", page_length=50)["items"]
		parts = []
		for s in range(0, len(full), 7):
			parts += nca.get_my_requests(state="approval", start=s, page_length=7)["items"]
		assert [i["doc_name"] for i in parts] == [i["doc_name"] for i in full], u
	test("paging never repeats or skips a row", t_paging)

	frappe.db.rollback()
	passed = sum(1 for _n, ok, _e in RESULTS if ok)
	print()
	print("=" * 60)
	print(f"MY REQUESTS: {passed}/{len(RESULTS)} passed, {len(SKIPPED)} skipped (rolled back)")
	for name, ok, err in RESULTS:
		if not ok:
			print(f"  FAILED: {name} :: {err}")
	sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
	run()
