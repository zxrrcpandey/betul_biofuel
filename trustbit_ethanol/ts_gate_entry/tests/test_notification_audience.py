"""v2.59.0 Notification Audience + material inspection access — standalone test
suite. Every write happens inside one transaction that is ROLLED BACK at the end
(no data left behind; commits inside the code under test are stubbed).

Covers:
  1. seeder: fresh install, insert-only (admin ticks survive), missing row re-added;
     Stores User + G2 Gate Operator read on inspections (upsert, read only)
  2. parity: with the seeded defaults every event resolves to exactly the
     recipients of the v2.58.0 code, over real inspections / approved MRs
  3. ticks: unticking a group, the action-event Team fallback, validate()
  4. skip list: subtracts from role-wide lists only, never empties a role, the
     save guard for approval roles, Pending Actions routing unchanged
  5. fail-open: unreadable tables read as the defaults; audience() never raises
  6. inspection: share grants access, can_inspect, forged payload (requester,
     foreign/unnamed rows, status), plain save blocked, quantities, POST-only,
     HTML cleaned, department heads from the approval config, Pending Actions,
     a Rejected inspection blocks the Stores dashboard GRN whatever the setting
  7. RGP overdue digest: Team ticked = one full digest; unticked = own passes
  8. Routing Check report shows the skip list; access = IT Head / System Manager
  9. v2.60.0 Pending Actions: "Assigned to me" / "Open to my roles" — every PO/MR
     listed exactly when the approval check lets the user act, Assigned = waits on
     the user at the current step, Administrator never gets role-wide items

Usage (same shape as test_regression.py):
    cd /home/frappe/frappe-bench/sites && sudo -u frappe env FRAPPE_SITE=<site> \
        /home/frappe/frappe-bench/env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_notification_audience.py
Exit code: 0 = all passed, 1 = failures.
"""

import os
import re
import sys

import frappe
from frappe.utils import cint, flt

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
MI = "TS Material Inspection"
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
		return
	raise AssertionError("expected an error containing: " + needle)


def run():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	# Code under test commits mid-request (create_material_inspection); stub it
	# so the final rollback really removes everything.
	frappe.db.commit = lambda *a, **k: None

	from frappe.share import add_docshare

	from trustbit_ethanol.ts_gate_entry import notification_center_api as nca
	from trustbit_ethanol.ts_gate_entry import ts_notification_routing as nr
	from trustbit_ethanol.ts_gate_entry import ts_rgp_overdue as ro
	from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
		get_cc_config, get_cc_notify_only_users)
	from trustbit_ethanol.ts_gate_entry.doctype.ts_material_inspection import ts_material_inspection as mi

	AUD = nr.AUDIENCE_DOCTYPE
	rr = nr.role_recipients

	def reset():
		frappe.db.delete("TS Notification Audience Event", {"parenttype": AUD})
		frappe.db.delete("TS Notification Routing User", {"parenttype": AUD})
		nca._seed_notification_audience()

	def set_ticks(event, **ticks):
		doc = frappe.get_single(AUD)
		row = next(r for r in doc.events if r.event == event)
		row.update(ticks)
		doc.save(ignore_permissions=True)

	def set_skip(users):
		doc = frappe.get_single(AUD)
		doc.set("skip_accounts", [{"user": u} for u in users])
		doc.save(ignore_permissions=True)

	sent = []
	mi._send_inspection_notification = lambda doc, users, subject, message: sent.append((list(users), subject))
	mi._send_inspection_notification_raw = lambda users, subject, message, name: None

	reset()

	# ── 1. seeder ─────────────────────────────────────────────────────────────
	def t_seed_fresh():
		doc = frappe.get_single(AUD)
		assert [r.event for r in doc.events] == list(nr.EVENTS), [r.event for r in doc.events]
		for r in doc.events:
			spec = nr.EVENTS[r.event]
			assert {c for c in nr.COLUMNS if r.get(c)} == set(spec["default"]), r.event
			assert r.applies == ",".join(spec["applies"]), r.event
			assert (r.team_roles or "") == ", ".join(spec.get("team_roles", ())), r.event
		assert not doc.skip_accounts
	test("seeder: fresh install = one row per event with the default ticks", t_seed_fresh)

	def t_seed_insert_only():
		set_ticks("mr_approved", team=0)
		frappe.db.set_value("TS Notification Audience Event", {"parenttype": AUD, "event": "mr_approved"},
			"event_label", "old label", update_modified=False)
		nca._seed_notification_audience()
		nca._seed_notification_audience()
		rows = frappe.get_all("TS Notification Audience Event", filters={"parenttype": AUD, "event": "mr_approved"},
			fields=["team", "event_label"])
		assert len(rows) == 1, rows
		assert rows[0].team == 0, "the admin's untick must survive the seeder"
		assert rows[0].event_label == nr.EVENTS["mr_approved"]["label"], "code-owned label refreshed"
		reset()
	test("seeder: insert-only — admin tick survives 2 runs, label refreshed", t_seed_insert_only)

	def t_seed_missing_row():
		set_ticks("inspection_request", raised_by=0)
		frappe.db.delete("TS Notification Audience Event", {"parenttype": AUD, "event": "rgp_overdue"})
		assert nr.audience_ticks("rgp_overdue") == set(nr.EVENTS["rgp_overdue"]["default"]), "missing row = defaults"
		nca._seed_notification_audience()
		assert frappe.db.count("TS Notification Audience Event", {"parenttype": AUD}) == len(nr.EVENTS)
		assert nr.audience_ticks("inspection_request") == {"dept_head"}
		reset()
	test("seeder: a deleted row reads as defaults and is re-added", t_seed_missing_row)

	def t_seed_result_readers():
		readers = nca.INSPECTION_RESULT_READERS
		frappe.db.delete("Custom DocPerm", {"parent": MI, "role": ("in", readers)})
		nca._seed_inspection_result_readers()
		nca._seed_inspection_result_readers()
		for role in readers:
			flags = ("write", "create", "delete", "submit", "cancel", "amend", "report", "export",
				"share", "print", "email")
			rows = frappe.get_all("Custom DocPerm", filters={"parent": MI, "role": role},
				fields=["permlevel", "read", *flags])
			assert len(rows) == 1 and rows[0].permlevel == 0 and rows[0].read == 1, (role, rows)
			assert not any(rows[0].get(f) for f in flags), (role, rows)  # read only
		frappe.db.set_value("Custom DocPerm", {"parent": MI, "role": readers[1]}, "read", 0)
		nca._seed_inspection_result_readers()
		assert frappe.db.get_value("Custom DocPerm", {"parent": MI, "role": readers[1]}, "read") == 1
		assert frappe.db.exists("Custom DocPerm", {"parent": MI, "role": "IT Head"}), "other rows untouched"
	test("seeder: Stores User + G2 Gate Operator get read only on inspections; idempotent; read re-asserted",
		t_seed_result_readers)

	# ── 2. parity with the v2.58.0 recipients ─────────────────────────────────
	def t_parity_inspection():
		insps = frappe.get_all(MI, fields=["name", "requester", "department_head", "material_request"],
			order_by="creation desc", limit=300)
		if not insps:
			raise Skip("no inspections on this site")
		it = set(rr("IT Head"))
		store = set(rr("Stores User")) | set(rr("G2 Gate Operator"))
		for i in insps:
			heads = mi._inspection_heads(i.name, i.requester, i.department_head)
			base = {u for u in [i.requester, *heads] if u}
			assert set(nr.audience("inspection_request", raised_by=i.requester, dept_heads=heads)) == base, i.name
			assert set(nr.audience("inspection_final", raised_by=i.requester, dept_heads=heads)) == base | it, i.name
			assert set(nr.audience("inspection_result", raised_by=i.requester, dept_heads=heads,
				cc_notify=["someone@example.com"])) == store, i.name
		print(f"        {len(insps)} inspections")
	test("parity: inspection notices = requester + heads (+ IT Head), result = Stores + G2", t_parity_inspection)

	def t_parity_mr_approved():
		mrs = frappe.get_all("Material Request", filters={"docstatus": 1}, fields=["name", "owner"],
			order_by="modified desc", limit=300)
		if not mrs:
			raise Skip("no submitted MRs")
		purchase = set(rr("Purchase Manager")) | set(rr("Purchase User"))
		for m in mrs:
			cfg = mi._mr_cc_config(m.name)
			notify = get_cc_notify_only_users(cfg) if cfg else []
			want = {m.owner, *notify} | purchase
			got = set(nr.audience("mr_approved", raised_by=m.owner, cc_notify=notify))
			assert got == {u for u in want if u}, m.name
		print(f"        {len(mrs)} MRs")
	test("parity: MR approved = owner + CC notify-only + Purchase Manager/User", t_parity_mr_approved)

	def t_parity_rgp():
		team = set(rr("Stores User")) | set(rr("Stores Manager"))
		assert set(nr.team_recipients("rgp_overdue")) == team
		assert set(nr.audience("rgp_back_at_gate", raised_by="nobody@example.com")) == team, "raised_by unticked by default"
		assert "Administrator" not in team, "Administrator no longer gets the store team's RGP bells"
	test("parity: RGP team = Stores User + Stores Manager holders (no Administrator)", t_parity_rgp)

	# ── 3. ticks ──────────────────────────────────────────────────────────────
	purchase_user = next(iter(rr("Purchase User") or rr("Purchase Manager")), None)
	outsider = frappe.db.sql_list(
		"""SELECT u.name FROM `tabUser` u WHERE u.enabled = 1 AND u.user_type = 'System User'
		     AND u.name NOT IN ('Administrator', 'Guest')
		     AND NOT EXISTS (SELECT 1 FROM `tabHas Role` hr WHERE hr.parent = u.name AND hr.parenttype = 'User'
		       AND hr.role IN ('Purchase Manager', 'Purchase User', 'Stores User', 'Stores Manager',
		                       'System Manager', 'IT Head', 'Department Head', 'G2 Gate Operator',
		                       'Accounts Manager', 'CEO', 'MD', 'Stock User', 'Stock Manager'))
		   ORDER BY u.name LIMIT 4""")

	def t_untick():
		set_ticks("inspection_request", raised_by=0)
		assert nr.audience("inspection_request", raised_by="r@example.com", dept_heads=["h@example.com"]) == ["h@example.com"]
		set_ticks("inspection_final", it_head=0)
		assert set(nr.audience("inspection_final", raised_by="r@example.com", dept_heads=[])) == {"r@example.com"}
		reset()
	test("ticks: unticking a group removes exactly that group", t_untick)

	def t_action_fallback():
		if not purchase_user or not outsider:
			raise Skip("need a purchase-role holder and a user with none of the team roles")
		set_ticks("mr_approved", team=0)
		purchase = set(rr("Purchase Manager")) | set(rr("Purchase User"))
		assert nr.audience("mr_approved", raised_by=purchase_user) == [purchase_user], "a ticked person holds a team role"
		got = set(nr.audience("mr_approved", raised_by=outsider[0]))
		assert got == {outsider[0]} | purchase, "nobody ticked can raise the PO → the Team still gets it"
		set_ticks("mr_approved", team=1, raised_by=0, cc_notify=0)
		assert set(nr.audience("mr_approved", raised_by=outsider[0])) == purchase
		reset()
	test("ticks: action event falls back to its Team when nobody ticked holds a team role", t_action_fallback)

	def t_validate():
		_expect_throw(lambda: set_ticks("rgp_overdue", raised_by=0, team=0), "tick at least one group")
		reset()
		set_ticks("mr_approved", it_head=1, dept_head=1, applies="raised_by,dept_head,it_head")  # n/a here
		row = frappe.get_all("TS Notification Audience Event", filters={"parenttype": AUD, "event": "mr_approved"},
			fields=["it_head", "dept_head", "applies"])[0]
		assert row.it_head == 0 and row.dept_head == 0, row
		assert row.applies == "raised_by,cc_notify,team", "applicable columns come from the code"
		doc = frappe.get_single(AUD)
		doc.append("events", {"event": "made_up", "raised_by": 1})
		doc.append("events", {"event": "mr_approved", "raised_by": 1})
		doc.save(ignore_permissions=True)
		events = [r.event for r in frappe.get_single(AUD).events]
		assert events == list(nr.EVENTS), events
		reset()
	test("validate: empty row refused; n/a ticks cleared; unknown + duplicate rows dropped", t_validate)

	# ── 4. skip list ──────────────────────────────────────────────────────────
	# Demo may carry routing rows from the v2.58.0 check: work on the routed list.
	role4 = next((r for r in ("Stores User", "Purchase User", "G2 Gate Operator", "Department Head")
		if len(nr.narrow(r, nr.role_holders(r))) >= 3), None)

	def skip_one(role):
		"""Skip one notified holder of `role` whose skip the save guard accepts."""
		if not role:
			raise Skip("no role with 3+ notified holders")
		for user in nr.narrow(role, nr.role_holders(role)):
			try:
				set_skip([user])
				return user
			except frappe.ValidationError:
				frappe.clear_messages()
		raise Skip(f"every holder of {role} is the last one notified for some approval role")

	def t_skip_subtracts():
		routed = nr.narrow(role4, nr.role_holders(role4)) if role4 else []
		a = skip_one(role4)
		assert rr(role4) == [u for u in routed if u != a], rr(role4)
		assert a in nr.audience("inspection_request", raised_by=a), "a person named on the document is never skipped"
		assert nca._role_why_fn(a)(role4)[0] == "skip", "Pending Actions moves the role's items to Open"
		reset()
	test("skip list: role-wide lists drop the account; by-name kept; Pending Actions says why", t_skip_subtracts)

	def t_skip_never_empty():
		if not role4:
			raise Skip("no role with 3+ notified holders")
		routed = nr.narrow(role4, nr.role_holders(role4))
		for i, u in enumerate(routed):  # straight to the table: the save guard would refuse
			frappe.get_doc({"doctype": "TS Notification Routing User", "parent": AUD, "parenttype": AUD,
				"parentfield": "skip_accounts", "user": u, "idx": i + 1}).db_insert()
		assert rr(role4) == routed, "every notified holder skipped → they are all notified"
		reset()
	test("skip list: never leaves a role with nobody", t_skip_never_empty)

	def t_skip_guard():
		routed = nr.narrow("CEO", nr.role_holders("CEO"))
		if not routed:
			raise Skip("no CEO holder")
		_expect_throw(lambda: set_skip(routed), "nobody else would be notified")
		assert not frappe.db.count("TS Notification Routing User", {"parenttype": AUD})
		reset()
	test("skip list: save refused when an approval role would have nobody else", t_skip_guard)

	# ── 5. fail-open ──────────────────────────────────────────────────────────
	def t_fail_open_tables():
		set_ticks("mr_approved", team=0)
		if role4:
			skip_one(role4)
		real_sql = frappe.db.sql

		def broken(query, *args, **kwargs):
			if "TS Notification Audience Event" in str(query) or "skip_accounts" in str(query):
				raise Exception("simulated: table missing")
			return real_sql(query, *args, **kwargs)

		frappe.db.sql = broken
		try:
			assert nr.audience_ticks("mr_approved") == set(nr.EVENTS["mr_approved"]["default"])
			assert nr.skip_accounts() == set()
		finally:
			del frappe.db.sql
		reset()
	test("fail-open: unreadable audience/skip tables read as today's defaults", t_fail_open_tables)

	def t_never_raises():
		def boom(event):
			frappe.msgprint("queued inside the failed lookup")
			raise ZeroDivisionError("simulated")

		real = nr.team_recipients
		nr.team_recipients = boom
		before = frappe.db.count("Error Log")
		frappe.msgprint("queued by the caller before the bell")
		try:
			got = nr.audience("mr_approved", raised_by="owner@example.com")
		finally:
			nr.team_recipients = real
		assert got == ["owner@example.com"], got
		assert frappe.db.count("Error Log") == before + 1
		log = [str(m) for m in frappe.local.message_log]
		assert any("queued by the caller" in m for m in log), "the caller's own messages must survive"
		assert not any("failed lookup" in m for m in log), "the failed call's messages must not reach the user"
		# tabError Log is MyISAM: the final rollback cannot remove this row.
		frappe.db.delete("Error Log", {"name": ["in", frappe.get_all("Error Log", filters={
			"method": "Notification audience failed: mr_approved"}, order_by="creation desc", limit=1, pluck="name")]})
	test("fail-open: audience() logs and returns what it has instead of raising", t_never_raises)

	# ── 6. material inspection ────────────────────────────────────────────────
	no_access = [u for u in frappe.db.sql_list(
		"""SELECT name FROM `tabUser` WHERE enabled = 1 AND user_type = 'System User'
		   AND name NOT IN ('Administrator', 'Guest') ORDER BY name""")
		if not frappe.has_permission(MI, "read", user=u)][:3]
	item = frappe.db.get_value("Item", {"disabled": 0, "is_stock_item": 1}, ["name", "item_name", "stock_uom"], as_dict=True)

	def new_insp(requester, head=None, qty=10):
		doc = frappe.get_doc({"doctype": MI, "status": "Pending Inspection", "requester": requester,
			"department_head": head, "notification_stage": 1,
			"items": [{"item_code": item.name, "item_name": item.item_name, "received_qty": qty,
				"uom": item.stock_uom, "item_status": "Pending"}]})
		doc.insert(ignore_permissions=True)
		return doc

	def need_users():
		if len(no_access) < 3 or not item:
			raise Skip("need 3 users without inspection access and a stock item")
		return no_access

	def t_share_access():
		req, head, other = need_users()
		doc = new_insp(req)
		assert not frappe.has_permission(MI, "read", doc=doc.name, user=req)
		mi._share_with_inspectors(doc.name, [req, head, "Administrator"])
		for u in (req, head):
			assert frappe.has_permission(MI, "read", doc=doc.name, user=u), u
			assert frappe.has_permission(MI, "write", doc=doc.name, user=u), u
		assert not frappe.has_permission(MI, "read", doc=doc.name, user=other)
		assert not frappe.db.exists("Notification Log", {"document_name": doc.name, "type": "Share"}), "notify=0"
		assert mi._inspection_heads(doc.name, req, None) == [head]
	test("inspection: share gives requester + head read/write, nobody else, no bell", t_share_access)

	def t_can_inspect():
		req, head, other = need_users()
		doc = new_insp(req)
		mi._share_with_inspectors(doc.name, [req, head])
		add_docshare(MI, doc.name, other, read=1, write=0, flags={"ignore_share_permission": True})
		for user, want in ((req, True), (head, True), (other, False)):
			frappe.set_user(user)
			d = frappe.get_doc(MI, doc.name)
			d.run_method("onload")
			assert d._can_inspect() is want, user
			assert d.get_onload().can_inspect is want, user
		frappe.set_user("Administrator")
	test("inspection: can_inspect = requester + write-shared heads (onload flag too)", t_can_inspect)

	def t_forged_requester():
		req, head, other = need_users()
		doc = new_insp(req)
		mi._share_with_inspectors(doc.name, [req])
		add_docshare(MI, doc.name, other, read=1, write=0, flags={"ignore_share_permission": True})
		forged = frappe.get_doc(doc.as_dict())
		forged.requester = other
		forged.department_head = other
		frappe.set_user(other)
		_expect_throw(forged.approve_all, "do not have permission")
		frappe.set_user("Administrator")
		assert frappe.db.get_value(MI, doc.name, "status") == "Pending Inspection"
	test("inspection H1: a forged requester in the payload is ignored", t_forged_requester)

	def t_forged_rows():
		req, head, other = need_users()
		doc = new_insp(req)
		victim = new_insp(other)
		mi._share_with_inspectors(doc.name, [req])
		forged = frappe.get_doc(doc.as_dict())
		forged.items[0].item_status = "Approved"
		forged.items[0].approved_qty = 10
		forged.append("items", {"name": victim.items[0].name, "item_code": item.name, "received_qty": 10,
			"item_status": "Rejected", "rejected_qty": 10})
		forged.append("items", {"item_code": item.name, "received_qty": 5, "item_status": "Approved", "approved_qty": 5})
		frappe.set_user(req)
		forged.submit_itemwise()
		frappe.set_user("Administrator")
		assert frappe.db.get_value(MI, doc.name, "status") == "Approved"
		assert frappe.db.count("TS Material Inspection Item", {"parent": doc.name}) == 1
		v = frappe.db.get_value("TS Material Inspection Item", victim.items[0].name, ["parent", "item_status"], as_dict=True)
		assert v.parent == victim.name and v.item_status == "Pending", v
	test("inspection H1: foreign and unnamed rows in the payload are ignored", t_forged_rows)

	def t_forged_status():
		req, head, other = need_users()
		doc = new_insp(req)
		mi._share_with_inspectors(doc.name, [req])
		frappe.db.set_value(MI, doc.name, "status", "Approved")
		forged = frappe.get_doc(doc.as_dict())
		forged.status = "Pending Inspection"
		frappe.set_user(req)
		_expect_throw(lambda: forged.reject_all(reason="x"), "cannot reject from status 'approved'")
	test("inspection H1: a forged status in the payload is ignored", t_forged_status)

	def t_plain_save_blocked():
		req, head, other = need_users()
		doc = new_insp(req)
		mi._share_with_inspectors(doc.name, [req])
		frappe.set_user(req)
		d = frappe.get_doc(MI, doc.name)
		d.requester = other
		_expect_throw(d.save, "approve all, reject all or submit item-wise")
		frappe.set_user("Administrator")
		d = frappe.get_doc(MI, doc.name)
		d.rejection_reason = "admin fix"
		d.save()
	test("inspection: a plain save is refused for inspectors, allowed for System Manager", t_plain_save_blocked)

	def t_quantities():
		req, head, other = need_users()
		doc = new_insp(req)
		mi._share_with_inspectors(doc.name, [req])
		frappe.set_user(req)
		d = frappe.get_doc(MI, doc.name)
		d.items[0].item_status = "Held"
		d.items[0].approved_qty = 3
		_expect_throw(d.submit_itemwise, "but received qty is")
		d = frappe.get_doc(MI, doc.name)
		d.items[0].item_status = "Approved"
		d.items[0].approved_qty = 15
		_expect_throw(d.submit_itemwise, "cannot be negative")
		d = frappe.get_doc(MI, doc.name)
		d.items[0].item_status = "Bogus"
		_expect_throw(d.submit_itemwise, "still pending")
		frappe.set_user("Administrator")
		assert frappe.db.get_value(MI, doc.name, "status") == "Pending Inspection"
		dec = new_insp(req, qty=10.3)
		mi._share_with_inspectors(dec.name, [req])
		frappe.set_user(req)
		d = frappe.get_doc(MI, dec.name)
		d.items[0].item_status = "Held"
		d.items[0].approved_qty = 10.1
		d.items[0].held_qty = 0.2
		d.submit_itemwise()
		frappe.set_user("Administrator")
		assert frappe.db.get_value(MI, dec.name, "status") == "On Hold", "10.1 + 0.2 = 10.3 at the field's precision"
	test("inspection: quantities re-checked (sum, negative, unknown status, decimal split)", t_quantities)

	def t_post_only():
		for m in ("approve_all", "reject_all", "submit_itemwise"):
			fn = getattr(mi.TSMaterialInspection, m)
			assert frappe.allowed_http_methods_for_whitelisted_func[fn] == ["POST"], m
		# GET /api/resource/...?run_method= skips the decorator check, so the methods refuse GET themselves.
		req, head, other = need_users()
		doc = new_insp(req)
		mi._share_with_inspectors(doc.name, [req])
		frappe.set_user(req)
		try:
			for m, kw in (("approve_all", {}), ("reject_all", {"reason": "x"}), ("submit_itemwise", {})):
				frappe.local.request = frappe._dict(method="GET")
				_expect_throw(lambda: getattr(frappe.get_doc(MI, doc.name), m)(**kw), "", frappe.PermissionError)
			frappe.local.request = frappe._dict(method="POST")
			frappe.get_doc(MI, doc.name).approve_all()
		finally:
			frappe.local.request = None
			frappe.set_user("Administrator")
		assert frappe.db.get_value(MI, doc.name, "status") == "Approved", "POST still works"
	test("inspection: decision methods refuse GET even when reached directly; POST works", t_post_only)

	def t_text_cleaned():
		req, head, other = need_users()
		rej, dec = new_insp(req), new_insp(req)
		mi._share_with_inspectors(rej.name, [req])
		mi._share_with_inspectors(dec.name, [req])
		frappe.set_user(req)
		frappe.get_doc(MI, rej.name).reject_all(reason='"<img src=x onerror=alert(1)>"')  # parses as JSON
		d = frappe.get_doc(MI, dec.name)
		d.items[0].item_status = "Approved"
		d.items[0].approved_qty = d.items[0].received_qty
		d.items[0].remark = "<img src=x onerror=alert(2)>"
		d.submit_itemwise()
		frappe.set_user("Administrator")
		reason = frappe.db.get_value(MI, rej.name, "rejection_reason")
		remark = frappe.db.get_value("TS Material Inspection Item", {"parent": dec.name}, "remark")
		assert "onerror" not in reason and "onerror" not in remark, (reason, remark)
		for raw in ("<!--><img src=x onerror=alert(1)>-->", "<![CDATA[><img src=x onerror=alert(1)>]]>",
				"<img src=x onerror=alert(1) "):  # markup a tag finder misses, a browser does not
			assert not re.search(r"<[^>]*onerror", mi._clean_text(raw)), raw
		for text in ("moisture > 14% & R&D", "a < b && c > d; x<5; <3 ok", "R&D <- 5 <= 6"):
			assert mi._clean_text(text) == text, text
	test("inspection: reject reason + item remark cleaned of HTML (JSON-shaped too); plain text kept", t_text_cleaned)

	def t_grn_blocked_on_rejected():
		from trustbit_ethanol.ts_gate_entry import stores_receiving_api as sra
		for gate in (False, True):
			assert sra._inspection_blocks_grn("Rejected", gate)
			assert not sra._inspection_blocks_grn("Approved", gate) and not sra._inspection_blocks_grn("Not Required", gate)
			assert sra._inspection_blocks_grn("Pending Inspection", gate) == gate
		tok = {"entry_type": "Material", "docstatus": 1, "status": "Inside Plant", "purpose": "Store", "name": None}
		ge = {"purchase_order": "PO", "material_flow": sra.FLOW_NON_RM, "requires_weighing": 0}
		real = sra._get_inspection_gate_enabled
		sra._get_inspection_gate_enabled = lambda: False  # the setting is off on both sites
		try:
			assert not sra._classify_receivable(tok, ge, "Rejected")["receivable"], "dashboard path B"
			assert sra._classify_receivable(tok, ge, "Pending Inspection")["receivable"]
			tok.update(status=sra.STATUS_TARE_WEIGHED)
			assert not sra._classify_receivable(tok, ge, "Rejected")["receivable"], "dashboard path A"
		finally:
			sra._get_inspection_gate_enabled = real
	test("GRN: a Rejected inspection blocks the dashboard GRN whatever the setting; Pending only with it on",
		t_grn_blocked_on_rejected)

	def t_department_heads():
		rows = frappe.db.sql(
			"""SELECT c.name AS cc, u.user FROM `tabTS CC Approval Config` c
			   JOIN `tabTS CC Approval User` u ON u.parent = c.name
			   JOIN `tabUser` us ON us.name = u.user AND us.enabled = 1
			   WHERE c.is_active = 1 AND u.role = 'Department Head'
			     AND u.action_type IN ('Reviewer', 'Final Approver')""", as_dict=True)
		by_cc = {}
		for r in rows:
			by_cc.setdefault(r.cc, []).append(r.user)
		mrs = frappe.get_all("Material Request", filters={"cost_center": ["in", list(by_cc) or [""]]},
			fields=["name", "cost_center", "owner"], order_by="creation desc", limit=50)
		mrs = [m for m in mrs if set(by_cc[m.cost_center]) - {m.owner}]
		if not mrs:
			raise Skip("no MR on a cost centre whose config has a Department Head")
		for m in mrs:
			heads = mi._department_heads(m.name, m.owner)
			assert set(heads) == set(by_cc[m.cost_center]) - {m.owner} and len(heads) == len(set(heads)), (m.name, heads)
		assert mi._department_heads(None, "nobody@example.com") == []
		print(f"        {len(mrs)} MRs")
	test("inspection: heads = enabled Department Head approvers on the MR cost centre's config", t_department_heads)

	def t_no_config_message():
		cc = frappe.db.sql_list(
			"""SELECT cc.name FROM `tabCost Center` cc
			   WHERE NOT EXISTS (SELECT 1 FROM `tabTS CC Approval Config` c WHERE c.name = cc.name) LIMIT 1""")
		mr = frappe.db.get_value("Material Request", {}, "name")
		if not cc or not mr:
			raise Skip("need a cost centre without an approval config and an MR")
		frappe.db.set_value("Material Request", mr, "cost_center", cc[0], update_modified=False)
		frappe.clear_messages()
		assert mi._mr_cc_config(mr) is None
		assert mi._department_heads(mr, "nobody@example.com") is not None
		assert not frappe.local.message_log, "a missing config must not reach the user's screen"
	test("inspection: a cost centre without an approval config leaves no 'not found' message", t_no_config_message)

	def t_create_shares_and_bells():
		if not frappe.db.get_single_value("TS Settings", "enable_material_inspection"):
			raise Skip("material inspection is switched off on this site")
		ge = frappe.db.sql(
			"""SELECT ge.name FROM `tabTS Gate Entry` ge JOIN `tabTS Material Inspection` i ON i.gate_entry = ge.name
			   WHERE ge.material_flow != 'Raw Material' AND IFNULL(ge.purchase_order, '') != ''
			   ORDER BY ge.creation DESC LIMIT 1""")
		if not ge:
			raise Skip("no non-RM gate entry with an inspection")
		ge = ge[0][0]
		frappe.db.delete(MI, {"gate_entry": ge})
		sent.clear()
		name = mi.create_material_inspection(ge)
		doc = frappe.get_doc(MI, name)
		heads = mi._inspection_heads(name, doc.requester, doc.department_head)
		shared = set(frappe.get_all("DocShare", filters={"share_doctype": MI, "share_name": name, "write": 1}, pluck="user"))
		assert shared == {u for u in [doc.requester, *heads] if u and u != "Administrator"}, (shared, heads)
		assert sent and set(sent[0][0]) == {u for u in [doc.requester, *heads] if u}, sent
	test("inspection: create shares with requester + heads and tells exactly them", t_create_shares_and_bells)

	def t_pending_actions():
		req, head, other = need_users()
		doc = new_insp(req)
		mi._share_with_inspectors(doc.name, [req, head])
		add_docshare(MI, doc.name, other, read=1, write=0, flags={"ignore_share_permission": True})
		assert doc.name in {i["doc_name"] for i in nca._pending_inspections(head)}
		assert doc.name in {i["doc_name"] for i in nca._pending_inspections(req)}
		assert doc.name not in {i["doc_name"] for i in nca._pending_inspections(other)}, "a read-only share is not a head"
		basis = {u: next(i for i in nca._pending_inspections(u) if i["doc_name"] == doc.name) for u in (req, head)}
		assert (basis[req]["scope"], basis[req]["basis"]) == ("assigned", "raised"), basis[req]
		assert (basis[head]["scope"], basis[head]["basis"]) == ("assigned", "head"), basis[head]
	test("inspection: Pending Actions lists it for the requester and the shared heads", t_pending_actions)

	# ── 7. RGP overdue digest ─────────────────────────────────────────────────
	def t_rgp_digest():
		due = ro._overdue_rows()
		if not due:
			raise Skip("no overdue returnable gate passes")
		calls = []
		real = ro._bell_once
		ro._bell_once = lambda recipients, subject, body, **kw: calls.append(set(recipients))
		try:
			owners = set()
			for r in due:
				owner = frappe.db.get_value("TS Returnable Gate Pass", r.name, "requested_by")
				if owner and frappe.db.get_value("User", owner, "enabled"):
					owners.add(owner)
			team = set(nr.team_recipients("rgp_overdue"))
			ro.remind_rgp_overdue()
			assert calls == ([team | owners] if team | owners else []), "defaults: store team + requesters, one digest"
			set_ticks("rgp_overdue", team=0)
			calls.clear()
			ro.remind_rgp_overdue()
			assert calls == ([owners] if owners else []), "Team unticked: requesters only"
			set_ticks("rgp_overdue", team=1, raised_by=0)
			calls.clear()
			ro.remind_rgp_overdue()
			assert calls == ([team] if team else []), "Requesters unticked: store team only"
		finally:
			ro._bell_once = real
			reset()
	test("RGP digest: one digest to the ticked groups (store team / requesters)", t_rgp_digest)

	# ── 8. report + access ────────────────────────────────────────────────────
	def t_report_skip():
		from trustbit_ethanol.ts_gate_entry.report.ts_notification_routing_check.ts_notification_routing_check import execute
		a = skip_one(role4)
		_cols, data, _msg = execute({"user": a})
		row = next(r for r in data if r["role"] == role4)
		assert row["gets"] == "No" and "skip list" in row["reason"].lower(), row
		reset()
	test("report: Routing Check explains the skip list", t_report_skip)

	def t_access():
		perms = {p.role for p in frappe.get_meta(AUD).permissions if p.read}
		assert perms == {"IT Head", "System Manager"}, perms
		if no_access:
			assert not frappe.has_permission(AUD, "read", user=no_access[0])
		it_only = next((u for u in nr.role_holders("IT Head") if "System Manager" not in frappe.get_roles(u)), None)
		if it_only:
			frappe.set_user(it_only)
			doc = frappe.get_single(AUD)
			next(r for r in doc.events if r.event == "inspection_result").raised_by = 1
			doc.save()
			frappe.set_user("Administrator")
			assert "raised_by" in nr.audience_ticks("inspection_result")
			reset()
	test("access: IT Head / System Manager only; an IT-Head-only user can save ticks", t_access)

	# ── 9. Pending Actions: Assigned to me / Open to my roles (v2.60.0) ─────────
	from trustbit_ethanol.ts_gate_entry import ts_po_approval as tpa

	def pa(user):
		"""Every item of both lists for `user`, paged 50 at a time, plus the counts."""
		frappe.set_user(user)
		try:
			out, counts = {}, None
			for scope in ("assigned", "open"):
				start, total = 0, 1
				while start < total:
					r = nca.get_pending_actions(scope=scope, start=start, page_length=50)
					counts, total = r["counts"], r["total"]
					assert r["scope"] == scope and total == counts[scope], r
					for it in r["items"]:
						key = (it["doc_type"], it["doc_name"])
						assert key not in out, f"{user}: {key} listed twice"
						assert it["scope"] == scope, it
						out[key] = it
					start += 50
			return out, counts
		finally:
			frappe.set_user("Administrator")

	users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User",
		"name": ["not in", ["Administrator", "Guest"]]}, pluck="name")
	listed = {}

	def t_pa_lists():
		listed.update({u: pa(u)[0] for u in users})
		assigned_basis, open_basis = {"named", "role", "raised", "head", "unassigned"}, {
			"later", "named_later", "routing", "skip", "admin", "deputy"}
		for u, items in listed.items():
			for it in items.values():
				assert it["basis"] in (assigned_basis if it["scope"] == "assigned" else open_basis), (u, it)
				assert it["why"], (u, it)
		if not any(listed.values()):
			raise Skip("nobody has a pending item on this site")
	test("pending: two lists, no item twice, every row says why, counts = list totals, paging complete", t_pa_lists)

	def t_pa_matches_approval_check():
		if len(listed) != len(users):
			raise Skip("the lists were not built (see the test above)")
		checks = 0
		for dt, status_f, rule_f, sub_f, validate in (
				("Material Request", "ts_mr_status", "ts_mr_approval_route", "ts_mr_submitted_by",
				 tpa._validate_user_can_act_on_mr),
				("Purchase Order", "ts_approval_status", "ts_approval_rule", "ts_submitted_by",
				 tpa._validate_user_can_act_on_po)):
			for name in frappe.get_all(dt, filters={"docstatus": 0, status_f: ["like", "Pending%"],
					rule_f: ["is", "set"]}, pluck="name", order_by="modified desc", limit=40):
				doc = frappe.get_doc(dt, name)
				for u in users:
					frappe.set_user(u)
					try:
						validate(doc)
						can = True
					except frappe.ValidationError:
						can = False
					finally:
						frappe.clear_messages()
						frappe.set_user("Administrator")
					# The approve step refuses the creator and the submitter unless
					# self-skip was impossible (_approve_mr / _validate_not_self_approving).
					own = u in (doc.owner, doc.get(sub_f)) and not cint(doc.get(
						"ts_self_skip_impossible" if dt == "Purchase Order" else "ts_mr_self_skip_impossible"))
					can = can and not own and frappe.has_permission(dt, "read", doc=name, user=u)
					it = listed[u].get((dt, name))
					if it and it["basis"] == "deputy":
						assert doc.get(status_f) == "Pending AVP" and "CEO" in frappe.get_roles(u), it
						continue
					assert bool(it) == can, f"{u} · {name}: listed={bool(it)}, approval check allows={can}"
					checks += 1
		if not checks:
			raise Skip("no pending PO/MR on this site")
	test("pending: a PO/MR is listed exactly when the approval check lets the user act", t_pa_matches_approval_check)

	def t_pa_assigned_is_current_step():
		n = 0
		for u, items in listed.items():
			roles = set(frappe.get_roles(u))
			for (dt, name), it in items.items():
				if dt not in ("Purchase Order", "Material Request") or it["scope"] != "assigned":
					continue
				doc = frappe.get_doc(dt, name)
				po = dt == "Purchase Order"
				rule = frappe.get_doc("TS PO Approval Rule" if po else "TS MR Approval Route",
					doc.ts_approval_rule if po else doc.ts_mr_approval_route)
				cur = doc.ts_current_step if po else doc.ts_mr_current_step
				cur = 1 if cur is None else cur  # as the query reads it: IFNULL(step, 1); 0 stays 0
				now = {s.role for s in rule.approval_steps if s.step_order == cur}
				if it["basis"] == "unassigned":
					assert po is False or not any(rr(r) for r in now), (u, name, now)  # PO: no step role has anyone
				elif it["basis"] == "named":
					cfg = get_cc_config(tpa._get_mr_cost_center(doc))
					from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
						get_cc_approvers_for_step)
					assert u in get_cc_approvers_for_step(cfg, cur, route=rule), (u, name)
				else:
					assert any(r in roles and u in rr(r) for r in now), (u, name, now)
				n += 1
		if not n:
			raise Skip("no assigned PO/MR on this site")
	test("pending: Assigned = named for the current step, or holds its role and is notified for it", t_pa_assigned_is_current_step)

	def t_pa_administrator():
		items, counts = pa("Administrator")
		by_role = [i for i in items.values() if i["scope"] == "assigned" and i["basis"] == "role"]
		assert not by_role, by_role[:3]
		assert all(i["basis"] in ("named", "raised", "head") for i in items.values() if i["scope"] == "assigned")
		assert counts["assigned"] + counts["open"] == len(items)
	test("pending: Administrator gets only items that name it in Assigned", t_pa_administrator)

	def t_pa_default_all():
		u = max(listed, key=lambda x: len(listed[x]), default=None) if listed else None
		if not u or not listed[u]:
			raise Skip("nobody has a pending item on this site")
		frappe.set_user(u)
		try:
			r = nca.get_pending_actions(page_length=50)  # a page script cached before v2.60.0
		finally:
			frappe.set_user("Administrator")
		assert r["scope"] == "all" and r["total"] == len(listed[u]) == sum(r["counts"].values()), r["counts"]
	test("pending: no scope (old page script) returns both lists together", t_pa_default_all)

	def t_pa_nobody_assigned():
		# A PO whose current-step role nobody holds counts for everyone who can act.
		rows = frappe.db.sql("""SELECT d.name, d.ts_approval_rule AS rule, IFNULL(d.ts_current_step, 1) AS step,
				d.ts_submitted_by AS sub, d.owner FROM `tabPurchase Order` d
			WHERE d.docstatus = 0 AND d.ts_approval_status LIKE 'Pending%%' AND d.ts_approval_rule IS NOT NULL
			ORDER BY d.modified DESC LIMIT 20""", as_dict=True)
		for row in rows:
			steps = frappe.get_all("TS PO Approval Step", filters={"parent": row.rule},
				fields=["name", "step_order", "role"])
			now = [s for s in steps if s.step_order == row.step]
			now_roles = {s.role for s in now}
			u = next((h for s in steps if s.step_order > row.step for h in rr(s.role)
				if h not in (row.sub, row.owner) and not set(frappe.get_roles(h)) & now_roles
				and frappe.has_permission("Purchase Order", "read", doc=row.name, user=h)), None)
			if not now or not u:
				continue
			ghost = "TS Test Nobody " + frappe.generate_hash(length=6)
			frappe.get_doc({"doctype": "Role", "role_name": ghost, "desk_access": 1}).insert(ignore_permissions=True)
			try:
				for s in now:
					frappe.db.set_value("TS PO Approval Step", s.name, "role", ghost, update_modified=False)
				frappe.set_user(u)
				got = {i["doc_name"]: i["basis"] for i in nca.get_pending_actions(
					scope="assigned", category="PO", page_length=50)["items"]}
			finally:
				frappe.set_user("Administrator")
				for s in now:
					frappe.db.set_value("TS PO Approval Step", s.name, "role", s.role, update_modified=False)
			assert got.get(row.name) == "unassigned", (u, row.name, got.get(row.name))
			return
		raise Skip("no pending PO with a later-step holder who can read it")
	test("pending: a step whose role nobody holds counts for everyone who can act", t_pa_nobody_assigned)

	def t_pa_step_gap_stays_open():
		# A current step with no step row (a gap in the route) stays Open for later-step holders.
		rows = frappe.db.sql("""SELECT d.name, d.ts_approval_rule AS rule, IFNULL(d.ts_current_step, 1) AS step,
				d.ts_submitted_by AS sub, d.owner FROM `tabPurchase Order` d
			WHERE d.docstatus = 0 AND d.ts_approval_status LIKE 'Pending%%' AND d.ts_approval_rule IS NOT NULL
			ORDER BY d.modified DESC LIMIT 20""", as_dict=True)
		for row in rows:
			steps = frappe.get_all("TS PO Approval Step", filters={"parent": row.rule},
				fields=["name", "step_order", "role"])
			now = [s for s in steps if s.step_order == row.step]
			u = next((h for s in steps if s.step_order > row.step for h in rr(s.role)
				if h not in (row.sub, row.owner)
				and frappe.has_permission("Purchase Order", "read", doc=row.name, user=h)), None)
			if not now or not u:
				continue
			try:
				for s in now:
					frappe.db.set_value("TS PO Approval Step", s.name, "step_order", 0, update_modified=False)
				frappe.set_user(u)
				got = {}
				for sc in ("assigned", "open"):
					got[sc], start = {}, 0
					while True:
						p = nca.get_pending_actions(scope=sc, category="PO", start=start, page_length=50)
						got[sc].update({i["doc_name"]: i["basis"] for i in p["items"]})
						start += 50
						if start >= p["total"]:
							break
			finally:
				frappe.set_user("Administrator")
				for s in now:
					frappe.db.set_value("TS PO Approval Step", s.name, "step_order", s.step_order, update_modified=False)
			assert row.name not in got["assigned"], (u, row.name, got["assigned"].get(row.name))
			assert got["open"].get(row.name) not in (None, "unassigned"), (u, row.name, got["open"].get(row.name))
			return
		raise Skip("no pending PO with a later-step holder who can read it")
	test("pending: a gap in the route (no row at the current step) stays Open", t_pa_step_gap_stays_open)

	frappe.db.rollback()
	frappe.clear_cache(doctype=MI)  # the permission seeder test cached rows the rollback removed
	passed = sum(1 for _n, ok, _e in RESULTS if ok)
	print()
	print("=" * 60)
	print(f"NOTIFICATION AUDIENCE: {passed}/{len(RESULTS)} passed, {len(SKIPPED)} skipped (rolled back)")
	for name, ok, err in RESULTS:
		if not ok:
			print(f"  FAILED: {name} :: {err}")
	sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
	run()
