"""Back-dated Production Entry (plan v6, 14 Sep 2026) — standalone, read-only test suite.

No DB writes are committed: every check runs inside one transaction that ends with rollback.
Covers: posting-time slots, the date guards in validate_entry (G1/G2/G4/G7 + Draft scoping),
the exempt-list parser, the Material Issue edge query on live data, G0/G10 early returns,
and site invariants (seeded fields, hook wiring, native back-date controls still blank).

Usage (same shape as test_regression.py):
    cd /home/frappe/frappe-bench/sites
    su -s /bin/bash frappe -c 'cd /home/frappe/frappe-bench/sites && \
        /home/frappe/frappe-bench/env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_backdated_production.py'
Exit code: 0 = all passed, 1 = failures.
"""

import os
import sys

import frappe

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
RESULTS = []


def test(name, fn):
	try:
		fn()
		RESULTS.append((name, True, ""))
		print(f"  PASS  {name}")
	except Exception as e:  # noqa: BLE001 — a test harness reports every failure
		RESULTS.append((name, False, str(e)[:300]))
		print(f"  FAIL  {name} :: {str(e)[:300]}")


def _throws(fn, needle):
	try:
		fn()
	except frappe.ValidationError as e:
		assert needle in str(e), f"wrong message: {str(e)[:160]}"
		frappe.clear_messages()
		return
	raise AssertionError(f"expected a refusal containing {needle!r}")


def run():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	from frappe.utils import add_days, nowdate, getdate
	from trustbit_ethanol.ts_gate_entry import ts_production_api as api
	from trustbit_ethanol.ts_gate_entry import ts_production_wo as wo

	today = getdate(nowdate())
	company = frappe.db.get_value("Company", {}, "name")

	# ── 1. posting-time slots ────────────────────────────────────────────
	def t_slot_today():
		d, t = wo.posting_slot(today, "release")
		assert d == today, (d, today)
	test("slot: today posts now", t_slot_today)

	def t_slot_past():
		past = add_days(today, -3)
		assert wo.posting_slot(past, "release") == (getdate(past), "23:57:00.000000")
		assert wo.posting_slot(past, "manufacture")[1] == "23:58:00.000000"
		assert wo.posting_slot(past, "surplus")[1] == "23:59:00.000000"
	test("slot: past date uses 23:57 / 23:58 / 23:59", t_slot_past)

	def t_slot_floor():
		past = add_days(today, -3)
		real = frappe.db.get_value
		frappe.db.get_value = lambda *a, **k: frappe._dict(posting_date=getdate(past), posting_time="23:59:59.500000")
		try:
			d, t = wo.posting_slot(past, "surplus", after_se="X")
		finally:
			frappe.db.get_value = real
		assert d == getdate(past) and t == "23:59:59.999999", (d, t)
	test("slot: +1 s floor never rolls to the next day", t_slot_floor)

	def t_stamp_after_legacy():
		real = frappe.db.get_value
		frappe.db.get_value = lambda *a, **k: frappe._dict(posting_date=today, set_posting_time=0)
		try:
			se = frappe._dict()
			assert wo.stamp_after(se, "manufacture", "X") is False and "set_posting_time" not in se
		finally:
			frappe.db.get_value = real
	test("stamp_after: unstamped predecessor leaves the successor untouched", t_stamp_after_legacy)

	# ── 2. date guards in validate_entry ─────────────────────────────────
	def new_run(**kw):
		doc = frappe.new_doc("TS Production Entry")
		doc.update(dict(company=company, flow_type="Single", posting_date=today))
		doc.update(kw)
		return doc

	test("G1: future date refused", lambda: _throws(
		lambda: api._validate_production_date(new_run(posting_date=add_days(today, 1))), "cannot be in the future"))
	test("G7: Multiple run refused a past date", lambda: _throws(
		lambda: api._validate_production_date(new_run(flow_type="Multiple", posting_date=add_days(today, -1))),
		"Multiple (BOM Connector)"))

	def t_no_day_limit():
		from erpnext.accounts.utils import get_fiscal_year
		for back in (1, 60, 150):
			d = add_days(today, -back)
			if get_fiscal_year(d, company=company, boolean=True):
				api._validate_production_date(new_run(posting_date=d))  # no day limit: must pass
	test("no day limit: 1, 60 and 150 days back pass inside an open financial year", t_no_day_limit)

	def locked_run(status, old, new, flow="Single"):
		doc = frappe.get_doc({"doctype": "TS Production Entry", "name": "PROD-TEST-NOSAVE",
		                      "company": company, "flow_type": flow, "posting_date": new})
		doc._doc_before_save = frappe._dict(ts_variance_status=status, posting_date=old)
		return doc

	test("G2: date change refused once status is not Draft", lambda: _throws(
		lambda: api._validate_production_date(locked_run("Pending Stores Release", today, add_days(today, -2))),
		"Production Date is locked"))
	test("G2 scoping: a later save with the same old date passes (Multiple, Awaiting Distribution)",
	     lambda: api._validate_production_date(
	         locked_run("Awaiting Distribution", add_days(today, -400), add_days(today, -400), "Multiple")))

	def t_forced_draft():
		doc = new_run(ts_variance_status="Completed")
		api.validate_entry(doc)
		assert doc.ts_variance_status == "Draft", doc.ts_variance_status
	test("insert: a new run always starts as Draft (G10 counts active runs)", t_forced_draft)

	def t_bilingual():
		out = api._bi("EN", "HI")
		assert out == "EN<br><br>HI", out
	test("messages: English block, blank line, Hindi block", t_bilingual)

	# ── 3. exempt list + edges ───────────────────────────────────────────
	def t_exempt():
		real = api._setting
		api._setting = lambda f: "WTP - BBPL\n  CPU - BBPL | 10-40-0029 \ntype: Material Issue X\ntype:\n|\n"
		try:
			rules = api._exempt_rules()
		finally:
			api._setting = real
		assert api._is_exempt("wtp - bbpl", "ANY", "", rules)
		assert api._is_exempt("CPU - BBPL", "10-40-0029", "", rules)
		assert not api._is_exempt("CPU - BBPL", "10-10-0001", "", rules)
		assert api._is_exempt("", "ANY", "Material Issue X", rules)
		assert not api._is_exempt("", "ANY", "", rules), "a blank 'type:' line must not exempt everything"
	test("exempt list: cost center, pair, type; blank lines ignored", t_exempt)

	def t_edges_live():
		items = frappe.db.sql_list("""SELECT DISTINCT sed.item_code FROM `tabStock Entry Detail` sed
			JOIN `tabStock Entry` se ON se.name = sed.parent
			WHERE se.purpose = 'Material Issue' AND se.docstatus = 1 AND se.company = %s LIMIT 5""", company)
		edges = api._issue_edges(company, items)
		for lst in edges.values():
			keys = [(e.date, e.creation) for e in lst]
			assert keys == sorted(keys), "edges must be sorted by date"
			assert all(e.docstatus in (1, 2) for e in lst), "drafts are never edges"
	test("edges: live query runs, sorted, no drafts", t_edges_live)

	test("G0: Multiple run is not checked in v1", lambda: (
		api._assert_no_issue_covers_date(new_run(flow_type="Multiple")) == [] or (_ for _ in ()).throw(AssertionError())))

	def t_g10_other_purpose():
		se = frappe.new_doc("Stock Entry")
		se.purpose = "Material Transfer"
		assert api.assert_issue_not_consumed(se) is None
	test("G10: non Material Issue returns at once", t_g10_other_purpose)

	# ── 4. site invariants ───────────────────────────────────────────────
	def t_fields():
		for f in ("ts_production_backdate_section", "ts_production_cutover_date",
		          "ts_production_issue_exempt_cost_centers"):
			cf = frappe.db.get_value("Custom Field", "TS Settings-" + f, ["fieldname", "default"], as_dict=True)
			assert cf, f"missing Custom Field {f}"
			assert not cf.default, f"{f} must ship without a default (L227)"
	test("site: 3 seeded TS Settings fields exist, no defaults", t_fields)

	def t_hook():
		hooks = frappe.get_hooks("doc_events").get("Stock Entry", {}).get("before_submit") or []
		hooks = hooks if isinstance(hooks, list) else [hooks]
		assert any("require_issue_fields_on_submit" in h for h in hooks), hooks
	test("site: G10 rides the existing Stock Entry before_submit hook", t_hook)

	def t_native_controls_blank():
		ss = frappe.get_single("Stock Settings")
		assert not ss.get("stock_frozen_upto"), "stock_frozen_upto must stay blank"
		assert not ss.get("role_allowed_to_create_edit_back_dated_transactions"), \
			"back-dated role must stay blank (the chain runs as Administrator)"
	test("site: native freeze / back-dated role still blank", t_native_controls_blank)

	def t_settings_api():
		s = api.get_production_settings()
		assert "cutover_date" in s and "max_backdate_days" not in s, sorted(s)
	test("api: settings expose cutover_date, no day limit", t_settings_api)


if __name__ == "__main__":
	try:
		run()
	finally:
		try:
			frappe.db.rollback()
			frappe.destroy()
		except Exception:
			pass
	failed = [r for r in RESULTS if not r[1]]
	print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
	sys.exit(1 if failed else 0)
