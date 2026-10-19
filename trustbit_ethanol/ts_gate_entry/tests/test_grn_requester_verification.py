"""v2.67.0 GRN requester verification — targeted tests (demo only; every write is rolled back).

Covers: the one GRN gate (state x flag), the 2-working-day deadline, the
accepted/rejected split of the GRN rows, exemption by item group, the SLA
scheduler (stage timing, Auto-Proceeded, Stores bell, WhatsApp events) and
that all three GRN builders use the shared gate.

Usage:
    sudo -u frappe bench --site betulbiofuel.mkasystem.com execute \
        trustbit_ethanol.ts_gate_entry.tests.test_grn_requester_verification.run
"""

import inspect
from datetime import datetime, timedelta

import frappe
from frappe.utils import flt, now_datetime

RESULTS = []


def test(name, fn):
	try:
		fn()
		RESULTS.append((name, True, ""))
		print(f"  PASS  {name}")
	except Exception as e:  # noqa: BLE001 — a test harness reports every failure
		RESULTS.append((name, False, str(e)[:300]))
		print(f"  FAIL  {name} :: {str(e)[:300]}")
	frappe.clear_messages()


def _insp(token, status, items=(), created=None, po=None, stage=1, gate_entry=None):
	doc = frappe.get_doc({
		"doctype": "TS Material Inspection",
		"token_number": token,
		"gate_entry": gate_entry,
		"purchase_order": po,
		"status": status,
		"created_time": created or now_datetime(),
		"notification_stage": stage,
		"items": [dict(item_code=c, item_name=c, received_qty=got, rejected_qty=rej,
			approved_qty=got - rej, item_status="Approved") for (c, got, rej) in items],
	})
	doc.flags.ignore_links = True
	doc.flags.ignore_validate = True
	doc.insert(ignore_permissions=True)
	return doc


def _throws(fn):
	try:
		fn()
	except frappe.ValidationError as e:
		return str(e)
	return None


def run():
	frappe.set_user("Administrator")
	from trustbit_ethanol.ts_gate_entry import stores_receiving_api as sra
	from trustbit_ethanol.ts_gate_entry import ts_whatsapp_notify as wn
	from trustbit_ethanol.ts_gate_entry.doctype.ts_material_inspection import ts_material_inspection as mi
	from trustbit_ethanol.ts_gate_entry.doctype.ts_token import ts_token

	real_commit, real_flag, real_send = frappe.db.commit, sra._get_inspection_gate_enabled, wn.send_template
	frappe.db.commit = lambda *a, **k: None  # the code under test commits; keep everything rollback-able
	sent = []
	wn.send_template = lambda user, key, variables, **kw: sent.append((user, key))
	try:
		_run(sra, mi, ts_token, sent)
	finally:
		frappe.db.commit, sra._get_inspection_gate_enabled, wn.send_template = real_commit, real_flag, real_send
		frappe.db.rollback()
		_clear_caches()

	failed = [r for r in RESULTS if not r[1]]
	print(f"\nGRN requester verification: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
	for name, _ok, err in failed:
		print(f"  FAILED: {name} — {err}")
	return {"total": len(RESULTS), "failed": len(failed)}


def _run(sra, mi, ts_token, sent):
	po = frappe.db.get_value("Purchase Order", {"docstatus": 1}, ["name", "company"], as_dict=True,
		order_by="creation desc")
	assert po, "demo needs a submitted Purchase Order"

	# ── 1. the one gate: state x flag ──────────────────────────────────────────
	expect = {  # state: (blocks with flag 0, blocks with flag 1, fill with flag 1)
		"Pending Inspection": (False, True, False),
		"On Hold": (False, True, False),
		"Rejected": (True, True, False),
		"Approved": (False, False, True),
		"Partially Approved": (False, False, True),
		"Auto-Proceeded": (False, False, False),
	}
	for state, (b0, b1, fill1) in expect.items():
		tok = f"TEST-GRNV-{state.replace(' ', '-')}"
		_insp(tok, state, po=po.name)
		for flag, blocks in ((0, b0), (1, b1)):
			def t(tok=tok, flag=flag, blocks=blocks, state=state):
				sra._get_inspection_gate_enabled = lambda: bool(flag)
				gate = {}
				err = _throws(lambda: gate.update(sra._inspection_gate(tok)))
				assert bool(err) == blocks, f"{state} flag={flag}: blocked={bool(err)} expected {blocks} ({err})"
				if not blocks:
					assert gate["label"] == state and gate["fill"] == (fill1 and flag == 1), gate
			test(f"gate: {state} / flag {flag} → {'BLOCK' if blocks else 'allow'}", t)

	def t_none():
		for flag in (0, 1):
			sra._get_inspection_gate_enabled = lambda flag=flag: bool(flag)
			gate = sra._inspection_gate("TEST-GRNV-NO-INSPECTION")
			assert gate == {"inspection": None, "label": "Not Required", "enabled": bool(flag), "fill": False}, gate
	test("gate: no inspection → allow, no fill (both flags)", t_none)

	def t_error():
		real = sra._get_inspection_status_label
		sra._get_inspection_status_label = lambda t: "Error"
		try:
			sra._get_inspection_gate_enabled = lambda: True
			assert _throws(lambda: sra._inspection_gate("X")), "lookup error must block when the flag is on"
			sra._get_inspection_gate_enabled = lambda: False
			assert not _throws(lambda: sra._inspection_gate("X")), "flag off: as today, allowed"
		finally:
			sra._get_inspection_status_label = real
	test("gate: lookup error → block only with flag on", t_error)

	def t_message():
		sra._get_inspection_gate_enabled = lambda: True
		err = _throws(lambda: sra._inspection_gate("TEST-GRNV-Pending-Inspection"))
		assert "has not verified" in err and "after" in err, err
	test("gate: pending message names the deadline", t_message)

	def t_cancelled_ge():
		ge = frappe.db.get_value("TS Gate Entry", {"docstatus": 2}, "name")
		if not ge:
			return
		_insp("TEST-GRNV-CANCELLED-GE", "Pending Inspection", gate_entry=ge)
		assert mi.get_inspection_status_for_token("TEST-GRNV-CANCELLED-GE") is None
	test("lookup: inspection of a cancelled Gate Entry no longer gates", t_cancelled_ge)

	def t_newest():
		_insp("TEST-GRNV-TWO", "Rejected", created=now_datetime() - timedelta(days=3))
		frappe.db.sql("UPDATE `tabTS Material Inspection` SET creation = %s WHERE token_number='TEST-GRNV-TWO'",
			now_datetime() - timedelta(days=3))
		newer = _insp("TEST-GRNV-TWO", "Approved")
		assert mi.get_inspection_status_for_token("TEST-GRNV-TWO").name == newer.name
	test("lookup: newest inspection of the token wins", t_newest)

	def t_classify_rm():
		sra._get_inspection_gate_enabled = lambda: True
		tok = {"entry_type": "Material", "docstatus": 1, "status": sra.STATUS_TARE_WEIGHED,
			"purpose": "Raw Material", "name": "TEST-GRNV-CLS"}
		v = sra._classify_receivable(tok, {"purchase_order": po.name}, "Pending Inspection")
		assert not v["receivable"] and "requester" in v["reason"], v
		v = sra._classify_receivable(tok, {"purchase_order": po.name}, "Auto-Proceeded")
		assert v["receivable"], v
	test("dashboard verdict: RM-purpose token with a pending inspection is gated", t_classify_rm)

	def t_builders():
		for fn in (sra.create_grn_for_weighed_token, sra.create_grn_for_non_weighing_token, ts_token.TSToken.create_grn):
			src = inspect.getsource(fn)
			assert "_inspection_gate(" in src and "_fill_from_inspection(" in src, fn.__name__
			assert 'purpose != "Raw Material"' not in src, fn.__name__
	test("builders: Section A, Section B and the Token form all use the shared gate + fill", t_builders)

	# ── 2. working-day deadline ────────────────────────────────────────────────
	frappe.db.set_single_value("TS Settings", "inspection_4th_notify_hours", 48)
	frappe.db.set_value("Company", po.company, "default_holiday_list", None)
	cases = [  # created → expected (2026-10-17 is a Saturday)
		(datetime(2026, 10, 17, 15, 0), datetime(2026, 10, 20, 15, 0)),  # Sat → Tue
		(datetime(2026, 10, 18, 10, 0), datetime(2026, 10, 20, 10, 0)),  # Sun → Tue
		(datetime(2026, 10, 16, 15, 0), datetime(2026, 10, 19, 15, 0)),  # Fri → Mon
		(datetime(2026, 10, 19, 15, 0), datetime(2026, 10, 21, 15, 0)),  # Mon → Wed
		(datetime(2026, 10, 22, 9, 30), datetime(2026, 10, 24, 9, 30)),  # Thu → Sat
	]
	for created, want in cases:
		def t(created=created, want=want):
			got = mi.grn_allowed_after(created, po.name)
			assert got == want, f"{created:%a %d %H:%M} → {got:%a %d %H:%M}, expected {want:%a %d %H:%M}"
		test(f"deadline: {created:%a %H:%M} → {want:%a %H:%M} (Sunday off)", t)

	def t_no_po():
		assert mi.grn_allowed_after(datetime(2026, 10, 17, 15, 0), None) == datetime(2026, 10, 20, 15, 0)
	test("deadline: no PO → Sunday-only", t_no_po)

	def t_holiday():
		hl = frappe.get_doc({"doctype": "Holiday List", "holiday_list_name": "TEST-GRNV-HL",
			"from_date": "2026-01-01", "to_date": "2026-12-31",
			"holidays": [{"holiday_date": "2026-10-20", "description": "test"}]}).insert(ignore_permissions=True)
		frappe.db.set_value("Company", po.company, "default_holiday_list", hl.name)
		got = mi.grn_allowed_after(datetime(2026, 10, 19, 15, 0), po.name)
		assert got == datetime(2026, 10, 22, 15, 0), got  # Mon, Tue holiday → Wed(1) Thu(2)
		frappe.db.set_value("Company", po.company, "default_holiday_list", None)
	test("deadline: company holiday list day is skipped", t_holiday)

	def t_floor():
		frappe.db.set_single_value("TS Settings", "inspection_4th_notify_hours", 96)
		got = mi.grn_allowed_after(datetime(2026, 10, 19, 15, 0), po.name)
		frappe.db.set_single_value("TS Settings", "inspection_4th_notify_hours", 48)
		assert got == datetime(2026, 10, 23, 15, 0), got
	test("deadline: inspection_4th_notify_hours stays as a floor", t_floor)

	# ── 3. accepted / rejected split ───────────────────────────────────────────
	wh = frappe.db.get_value("Warehouse", {"company": po.company, "is_group": 0, "disabled": 0}, "name")
	part = _insp("TEST-GRNV-FILL", "Partially Approved", items=[("ITEM-A", 10, 2), ("ITEM-B", 5, 0), ("ITEM-C", 4, 4)])
	gate = {"inspection": part.name, "label": "Partially Approved", "enabled": True, "fill": True}

	def rows(a=10.0):
		return [{"item_code": "ITEM-A", "qty": a}, {"item_code": "ITEM-B", "qty": 5.0},
			{"item_code": "ITEM-C", "qty": 4.0}, {"item_code": "ITEM-D", "qty": 7.0}]

	def t_no_wh():
		frappe.db.sql("UPDATE tabWarehouse SET is_rejected_warehouse = 0 WHERE company = %s", po.company)
		r = rows()
		err = _throws(lambda: sra._fill_from_inspection(r, gate, po.company))
		assert err and "rejection warehouse" in err, err
	test("fill: no rejection warehouse → clear stop (nothing filled silently)", t_no_wh)

	frappe.db.set_value("Warehouse", wh, "is_rejected_warehouse", 1)

	def t_exact():
		r = rows()
		sra._fill_from_inspection(r, gate, po.company)
		a, b, c, d = r
		assert (a["qty"], a["rejected_qty"], a["received_qty"], a["rejected_warehouse"]) == (8, 2, 10, wh), a
		assert "rejected_qty" not in b and b["qty"] == 5, b
		assert (c["qty"], c["rejected_qty"]) == (0, 4), c
		assert d == {"item_code": "ITEM-D", "qty": 7.0}, d
	test("fill: declared qty splits exactly; untouched rows stay as built", t_exact)

	def t_weighed():
		r = rows(a=9.8)
		sra._fill_from_inspection(r, gate, po.company)
		assert flt(r[0]["rejected_qty"], 3) == 1.96 and flt(r[0]["qty"], 3) == 7.84, r[0]
		assert flt(r[0]["qty"] + r[0]["rejected_qty"], 3) == flt(r[0]["received_qty"], 3)
	test("fill: weighed qty gets the same rejected share (9.8 → 7.84 + 1.96)", t_weighed)

	def t_no_fill():
		r = rows()
		sra._fill_from_inspection(r, dict(gate, fill=False), po.company)
		assert r == rows()
	test("fill: off (flag 0 / Auto-Proceeded) → rows unchanged", t_no_fill)

	def t_pr_accepts():
		r = rows()[:1]
		r[0].update(item_code=frappe.db.get_value("Item", {"disabled": 0, "is_stock_item": 1}, "name"))
		g2 = dict(gate)
		frappe.db.set_value("TS Material Inspection Item", {"parent": part.name, "item_code": "ITEM-A"},
			"item_code", r[0]["item_code"])
		sra._fill_from_inspection(r, g2, po.company)
		pr = frappe.new_doc("Purchase Receipt")
		pr.company = po.company
		pr.append("items", dict(r[0], warehouse=frappe.db.get_value("Warehouse",
			{"company": po.company, "is_group": 0, "name": ["!=", wh]}, "name"), uom="Nos", rate=1))
		pr.validate_accepted_rejected_qty()
	test("fill: ERPNext accepts the split (received = accepted + rejected)", t_pr_accepts)

	# ── 4. exemption by item group ─────────────────────────────────────────────
	rm_item = frappe.db.sql("""SELECT it.name FROM tabItem it JOIN `tabItem Group` g ON g.name = it.item_group
		JOIN `tabItem Group` r ON r.name = 'Raw Material' WHERE g.lft >= r.lft AND g.rgt <= r.rgt LIMIT 1""")
	store_item = frappe.db.sql("""SELECT it.name FROM tabItem it JOIN `tabItem Group` g ON g.name = it.item_group
		JOIN `tabItem Group` r ON r.name = 'Raw Material' WHERE NOT (g.lft >= r.lft AND g.rgt <= r.rgt) LIMIT 1""")

	def t_exempt():
		assert rm_item and store_item, "demo needs one Raw Material and one store item"
		got = mi._exempt_item_codes([rm_item[0][0], store_item[0][0], "TEST-NO-SUCH-ITEM"])
		assert got == {rm_item[0][0]}, got
		assert mi._exempt_item_codes([]) == set()
	test("exempt: Raw Material tree item only (store item and unknown item are checked)", t_exempt)

	def t_exempt_no_root():
		real = mi.EXEMPT_ITEM_GROUP
		mi.EXEMPT_ITEM_GROUP = "TEST-NO-SUCH-GROUP"
		try:
			assert mi._exempt_item_codes([rm_item[0][0]]) == set()
		finally:
			mi.EXEMPT_ITEM_GROUP = real
	test("exempt: missing root group → nothing exempt (fail-closed)", t_exempt_no_root)

	def t_create():
		ge = frappe.db.sql("""SELECT ge.name FROM `tabTS Gate Entry` ge
			WHERE ge.docstatus = 1 AND ge.stock_direction = 'Stock IN' AND ge.material_flow = 'Raw Material'
			AND EXISTS (SELECT 1 FROM `tabTS Gate Entry Item` gi WHERE gi.parent = ge.name)
			ORDER BY ge.creation DESC LIMIT 20""", pluck=True)
		assert ge, "demo needs submitted Raw-Material Gate Entries"
		checked = 0
		for name in ge:
			frappe.db.delete("TS Material Inspection", {"gate_entry": name})
			codes = frappe.get_all("TS Gate Entry Item", filters={"parent": name}, pluck="item_code")
			left = [c for c in codes if c not in mi._exempt_item_codes(codes)]
			mi.create_material_inspection(name)
			got = frappe.get_all("TS Material Inspection", filters={"gate_entry": name}, pluck="name")
			if not left:
				assert not got, f"{name}: all items exempt but an inspection was created"
			else:
				items = frappe.get_all("TS Material Inspection Item", filters={"parent": got[0]}, pluck="item_code")
				assert sorted(items) == sorted(left), f"{name}: {items} != {left}"
			checked += 1
		assert checked
	test("create: RM-flow Gate Entries inspect only their non-exempt items", t_create)

	# ── 5. scheduler ───────────────────────────────────────────────────────────
	def t_scheduler():
		frappe.db.set_single_value("TS Settings", "enable_material_inspection", 1)
		old = _insp("TEST-GRNV-SLA-OLD", "Pending Inspection", po=po.name, stage=3,
			created=now_datetime() - timedelta(days=5))
		young = _insp("TEST-GRNV-SLA-YOUNG", "Pending Inspection", po=po.name, stage=3,
			created=now_datetime() - timedelta(hours=30))
		fresh = _insp("TEST-GRNV-SLA-FRESH", "Pending Inspection", po=po.name, stage=1,
			created=now_datetime() - timedelta(hours=9))
		ge_cancelled = frappe.db.get_value("TS Gate Entry", {"docstatus": 2}, "name")
		gone = ge_cancelled and _insp("TEST-GRNV-SLA-CANCELLED", "Pending Inspection", po=po.name, stage=3,
			created=now_datetime() - timedelta(days=5), gate_entry=ge_cancelled)
		del sent[:]
		mi.check_inspection_sla()
		if gone:
			assert frappe.db.get_value("TS Material Inspection", gone.name, "status") == "Pending Inspection", \
				"an inspection of a cancelled Gate Entry must get no reminders / Auto-Proceed"
		o, y, f = (frappe.db.get_value("TS Material Inspection", d.name, ["status", "notification_stage"], as_dict=True)
			for d in (old, young, fresh))
		assert (o.status, o.notification_stage) == ("Auto-Proceeded", 4), o
		assert (y.status, y.notification_stage) == ("Pending Inspection", 3), f"30 h < 2 working days: {y}"
		assert (f.status, f.notification_stage) == ("Pending Inspection", 2), f
		stores = set(frappe.get_all("Has Role", filters={"role": "Stores User", "parenttype": "User"}, pluck="parent"))
		bells = set(frappe.get_all("Notification Log", filters={"document_type": "TS Material Inspection",
			"document_name": old.name}, pluck="for_user"))
		assert not stores or bells & stores, f"no Stores bell for {old.name}"
		keys = {k for _u, k in sent}
		assert "inspection_grn_allowed" in keys or not stores, keys
	test("scheduler: Auto-Proceeded only after 2 working days; Stores told; reminders on time", t_scheduler)

	def t_scheduler_respects_decision():
		src = inspect.getsource(mi.check_inspection_sla)
		assert 'for_update=True) != "Pending Inspection"' in src
	test("scheduler: re-reads the status under lock before the flip", t_scheduler_respects_decision)

	def t_whatsapp_map():
		from trustbit_ethanol.ts_gate_entry.setup_whatsapp import TEMPLATE_EVENT_KEYS
		for key in ("inspection_needed", "inspection_reminder", "inspection_grn_allowed"):
			assert key in TEMPLATE_EVENT_KEYS
			from trustbit_ethanol.ts_gate_entry.ts_whatsapp_notify import ACTION_EVENT
			assert ACTION_EVENT[("TS Material Inspection", key)] == key
	test("whatsapp: 3 inspection events mapped and seeded", t_whatsapp_map)


def run_e2e():
	"""End-to-end through the real Section B "Create GRN" as a Stores User, on a real
	demo truck: blocked while pending, then a split GRN after the requester's
	decision. Everything is rolled back."""
	from trustbit_ethanol.ts_gate_entry import stores_receiving_api as sra
	from trustbit_ethanol.ts_gate_entry.doctype.ts_material_inspection import ts_material_inspection as mi

	real_commit = frappe.db.commit
	frappe.db.commit = lambda *a, **k: None
	try:
		frappe.set_user("Administrator")
		stores = frappe.db.sql("""SELECT u.name FROM tabUser u JOIN `tabHas Role` r ON r.parent = u.name
			WHERE r.role = 'Stores User' AND u.enabled = 1 AND u.name NOT IN ('Administrator', 'Guest')
			AND u.name NOT IN (SELECT parent FROM `tabHas Role` WHERE role = 'System Manager') LIMIT 1""", pluck=True)
		assert stores, "demo needs a Stores User without System Manager"
		frappe.db.set_single_value("TS Settings", "block_grn_on_inspection_pending", 1)

		done = False
		for row in sra._fetch_section_b_non_weighing():
			tok = row.get("token") or row.get("name") or row.get("token_number")
			ge = frappe.db.get_value("TS Gate Entry", {"token_number": tok, "docstatus": 1}, ["name", "purchase_order"], as_dict=True)
			if not ge or not ge.purchase_order:
				continue
			frappe.db.delete("TS Material Inspection", {"token_number": tok})
			name = mi.create_material_inspection(ge.name)
			if not name:
				continue
			company = frappe.db.get_value("Purchase Order", ge.purchase_order, "company")
			wh = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0,
				"name": ["like", "%eject%"]}, "name")
			if not wh:
				continue
			frappe.db.set_value("Warehouse", wh, "is_rejected_warehouse", 1)

			frappe.set_user(stores[0])
			err = _throws(lambda: sra.create_grn_for_non_weighing_token(tok))
			frappe.set_user("Administrator")
			test(f"e2e {tok}: pending inspection blocks Stores ({stores[0]})", lambda: _assert(err and "has not verified" in err, err))

			insp = frappe.get_doc("TS Material Inspection", name)
			first = insp.items[0]
			got = flt(first.received_qty)
			for it in insp.items:
				it.item_status = "Approved"
				it.approved_qty = flt(it.received_qty)
			if len(insp.items) > 1:
				insp.items[-1].item_status = "Rejected"
			if got > 1:
				first.approved_qty = got - 1
			insp.submit_itemwise()
			insp.reload()
			status = insp.status

			frappe.set_user(stores[0])
			res = {}
			err = _throws(lambda: res.update(sra.create_grn_for_non_weighing_token(tok)))
			frappe.set_user("Administrator")
			test(f"e2e {tok}: after the decision ({status}) Stores can create the GRN",
				lambda: _assert(not err and res.get("purchase_receipt"), err))
			if res.get("purchase_receipt"):
				pr = frappe.get_doc("Purchase Receipt", res["purchase_receipt"])
				want = {i.item_code: (flt(i.received_qty), flt(i.rejected_qty)) for i in insp.items}
				for it in pr.items:
					print(f"    PR row {it.item_code}: received {it.received_qty} = accepted {it.qty} "
						f"+ rejected {it.rejected_qty} → {it.rejected_warehouse or '-'}")

				def t_rows():
					for it in pr.items:
						if it.item_code not in want:
							continue
						got_r, rej = want[it.item_code]
						assert flt(it.received_qty, 3) == flt(got_r, 3), (it.item_code, it.received_qty, got_r)
						assert flt(it.rejected_qty, 3) == flt(rej, 3), (it.item_code, it.rejected_qty, rej)
						assert flt(it.qty + it.rejected_qty, 3) == flt(it.received_qty, 3)
						assert not rej or it.rejected_warehouse == wh, (it.item_code, it.rejected_warehouse)
					assert any(flt(i.rejected_qty) for i in pr.items) == any(r for _g, r in want.values())
				test(f"e2e {tok}: GRN rows = requester's accepted + rejected split into {wh}", t_rows)
			done = True
			break
		test("e2e: a Section B demo truck with a non-exempt item was found", lambda: _assert(done, "none"))
	finally:
		frappe.set_user("Administrator")
		frappe.db.commit = real_commit
		frappe.db.rollback()
		_clear_caches()
	failed = [r for r in RESULTS if not r[1]]
	print(f"\nE2E: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
	return {"total": len(RESULTS), "failed": len(failed)}


def _clear_caches():
	"""rollback() does not reach the Redis doc cache: drop the docs the tests wrote to."""
	frappe.clear_document_cache("TS Settings", "TS Settings")
	for dt in ("Company", "Warehouse"):
		for name in frappe.get_all(dt, pluck="name"):
			frappe.clear_document_cache(dt, name)


def _assert(cond, msg=""):
	assert cond, msg
