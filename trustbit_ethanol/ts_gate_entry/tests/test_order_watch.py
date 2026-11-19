"""TS Order Watch tests. Part A: the rules on invented orders (pure: no site, no database). Part B: the report
on the demo database as an MD-only user; every write is rolled back at the end.

Part A runs on its own when OW_PART_A_ONLY=1 is set or when no Frappe site can be reached (a small stand-in
takes frappe's place when it is not installed, so any Python 3.10+ will do).

Usage (same shape as test_regression.py):
    cd /home/frappe/frappe-bench/sites && sudo -u frappe ../env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_order_watch.py
    Part A only: OW_PART_A_ONLY=1 python3 .../ts_gate_entry/tests/test_order_watch.py
    Part B read-only (no test users; B3 and the B4 lookup): OW_READ_ONLY=1
Exit code: 0 = all passed, 1 = failures.
"""

import copy
import importlib.util
import json
import os
import sys
import types
from datetime import date, datetime, timedelta

try:
	import frappe
	from frappe.utils import add_days, flt, today
except ImportError:  # Part A only: the rules take nothing from frappe but _()
	frappe = None
	_stand_in = types.ModuleType("frappe")
	_stand_in._ = lambda text, *args, **kwargs: text
	sys.modules["frappe"] = _stand_in

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
RESULTS = []
_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ts_order_watch_rules.py")
_spec = importlib.util.spec_from_file_location("ts_order_watch_rules_under_test", _path)
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)
# frappe's own _() needs a site: without one it writes an error to the bench log on every call. This copy of the
# rules (loaded under its own name, so the report's copy is untouched) gets the plain stand-in instead.
rules._ = lambda text, *args, **kwargs: text

S1, S2, S3 = "Sunrise Traders", "Hilltop Engineering Works", "Riverbend Supplies"
C1, C2 = "Test Company A", "Test Company B"
T0, DAY = datetime(2026, 10, 5, 10, 0), timedelta(days=1)
NAMES = {"ITM-DSL": "High Speed Diesel", "ITM-A": "Bearing Set", "ITM-B": "Valve Kit", "ITM-C": "Pump Seal",
	"ITM-M": "Gear Box", "ITM-X": "Fly Ash Bags", "ITM-Y": "Lime Powder", "ITM-MISC": "General Spares"}
LINES = {"Store": {"md": 600000.0, "pm": 100000.0}, "Grain": {"md": 600000.0, "pm": None}, "Coal": {"md": 3000000.0, "pm": None}}
PM, CEO, MD = ({"step_order": 1, "role": "Purchase Manager"}, {"step_order": 2, "role": "CEO"}, {"step_order": 3, "role": "MD"})
STEPS = {"Store-Under 1L": [dict(CEO, step_order=1)], "Store-1L to 6L": [PM, CEO], "Store-Above 6L": [PM, CEO, MD],
	"Grain-Under 6L": [dict(PM, role="Grain Purchase Manager"), CEO], "Grain-Above 6L": [dict(PM, role="Grain Purchase Manager"), CEO, MD],
	"Coal-Under 30L": [dict(CEO, step_order=1)], "Coal-Above 30L": [dict(CEO, step_order=1), dict(MD, step_order=2)]}
KEYS = {"checks", "level", "summary", "supplier", "company", "category", "line", "total", "over_by", "orders", "tie",
	"facts_against", "extra_rows", "line_used", "limit"}
LIMIT_KEYS = {"category", "direction", "old", "new", "at", "back_at", "back_to", "by_role", "back_by_role", "same_account",
	"range", "steps", "marks", "marks_cut"}
# the rule table behind LINES and STEPS, as the loader reads it: every rule made on one day, never changed
SETUP = datetime(2026, 4, 1, 12, 50)
BANDS = {"Store-Under 1L": (0, 99999), "Store-1L to 6L": (100000, 599999), "Store-Above 6L": (600000, 0),
	"Grain-Under 6L": (0, 599999), "Grain-Above 6L": (600000, 0), "Coal-Under 30L": (0, 2999999), "Coal-Above 30L": (3000000, 0)}
PAIRS = []  # (points with today's lines only, points with the rebuilt one-stretch history) of every Part A world
RANK_KEYS = {"kind", "key", "label", "d30", "d90", "fy", "orders", "largest", "marks"}
WORDS = ("fraud", "bypass", "cheat", "suspicious", "violation")
ALL = []  # every result of Part A, for the checks that hold on every point


def test(name, fn):
	try:
		fn()
		RESULTS.append((name, True, ""))
		print(f"  PASS  {name}")
	except Exception as e:  # noqa: BLE001 — a test harness reports every failure
		RESULTS.append((name, False, str(e)[:300]))
		print(f"  FAIL  {name} :: {type(e).__name__} {str(e)[:300]}")


def check(cond, msg="check failed"):
	if not cond:
		raise AssertionError(msg)


def eq(got, want, what="value"):
	if got != want:
		raise AssertionError(f"{what}: got {got!r}, expected {want!r}")


# -- invented orders --
def world():
	return {"today": date(2026, 11, 14), "from_date": date(2026, 8, 16), "to_date": date(2026, 11, 14),
		"lines": copy.deepcopy(LINES), "orders": {}, "items": [], "mr_items": {}, "logs": {}, "rule_steps": copy.deepcopy(STEPS),
		"receipts": {}, "gate_first": {}, "allowance_edits": [], "allowance_now": {}, "stock_allowance": 10.0, "item_receipts": {}}


def rule_for(category, v):
	if category == "Store":
		return "Store-Under 1L" if v < 100000 else ("Store-1L to 6L" if v < 600000 else "Store-Above 6L")
	top = 3000000 if category == "Coal" else 600000
	return "{0}-{1} {2}".format(category, "Above" if v >= top else "Under", "30L" if category == "Coal" else "6L")


def order(w, name, value, at, supplier=S1, category="Store", item="ITM-A", qty=1, mr=None, mri=None, md=False,
		docstatus=1, company=C1, approved=None, logs=None, **kw):
	approved = approved or (at + timedelta(hours=2) if docstatus == 1 else None)
	o = dict(name=name, company=company, supplier=supplier, category=category, currency="INR", company_currency="INR",
		conversion_rate=1.0, grand_total=value, base_grand_total=value, base_net_total=value, creation=at,
		transaction_date=at.date(), docstatus=docstatus, status="To Receive and Bill" if docstatus == 1 else "Draft",
		approval_status="Approved" if docstatus == 1 else "Pending CEO", per_received=0, approval_rule=rule_for(category, value),
		submitted_by="acct-buyer", submitted_by_role="Purchase User", self_skip_impossible=0, amount_at_submission=value,
		approved_date=approved, md_approved=md, final_approved_at=approved,
		final_approver_role=("MD" if md else "CEO") if approved else None, entered_role="Purchase User")
	o.update(kw)
	w["orders"][name] = o
	w["items"].append(dict(po=name, item_code=item, item_name=NAMES.get(item, item), stock_qty=qty, qty=qty,
		base_rate=value / qty, base_net_amount=value, material_request=mr, material_request_item=mri))
	w["logs"][name] = logs if logs is not None else [log("Submitted", at, value)] + (
		[log("Final Approved", approved, value, 3 if md else 2, "acct-md" if md else "acct-ceo")] if approved else [])
	return o


def log(action, at, amount, step=1, by="acct-buyer"):
	return dict(action=action, action_by=by, action_by_role="", action_date=at, po_amount=amount, step_order=step)


def receipt(w, po, doc, at, qty, amount, item="ITM-A", doctype="Purchase Receipt", is_return=0):
	w["receipts"].setdefault(po, []).append(dict(doctype=doctype, doc=doc, posting=at, is_return=is_return,
		update_stock=0, item_code=item, qty=qty, base_net_amount=amount, token=None))


def table():
	"""Today's rule table: {rule: {...}} in the loader's shape; each step row has its own name."""
	return {name: {"cat": name.split("-")[0], "low": float(low), "high": float(high), "on": 1, "creation": SETUP + timedelta(seconds=k),
		"owner": "acct-admin", "steps": [dict(s, name=f"{name}#{s['step_order']}") for s in STEPS[name]]}
		for k, (name, (low, high)) in enumerate(BANDS.items())}


def version(rule, at, by="acct-admin", **diff):
	return {"docname": rule, "at": at, "by": by, "data": diff}


def with_history(w, hist, roles=None):
	"""The world as the loader hands it over once the limits are rebuilt by date."""
	return dict(w, line_history=hist["lines"], rule_history=hist["rules"], history_gaps=hist["gaps"],
		change_roles=roles or {"acct-admin": "IT Head", "acct-other": "System Manager"})


def find(w):
	res = rules.find_points(w)
	ALL.append(res)
	if "line_history" not in w:  # acceptance 2: a never-changed rule table must give the same points
		flat = rules.find_points(with_history(w, rules.history(table())))
		PAIRS.append((res, flat))
	return res


def pts(res, chk):
	return [p for p in res["points"] if chk in p["checks"]]


def one(res, chk):
	found = pts(res, chk)
	eq(len(found), 1, f"{chk} points")
	return found[0]


def ranked(res, kind, key):
	return next((r for r in res["ranked"] if r["kind"] == kind and r["key"] == key), None)


def part_a():
	print("part A: the rules")

	def t_r30_item():
		w = world()
		order(w, "PO-T-0001", 320000, T0, item="ITM-DSL", qty=4000)
		order(w, "PO-T-0002", 320000, T0 + 4 * DAY, item="ITM-DSL", qty=4000, company=C2)
		p = one(find(w), "R30")
		eq((p["level"], p["orders"], p["total"], p["over_by"]), ("To check", ["PO-T-0001", "PO-T-0002"], 640000, 40000), "group")
		check("High Speed Diesel" in p["tie"] and "48 hours" not in p["tie"], f"item tie only: {p['tie']}")
		eq(p["company"], f"{C1}, {C2}", "every company is printed")
		w["orders"]["PO-T-0002"].update(creation=T0 + 8 * DAY, transaction_date=(T0 + 8 * DAY).date())
		eq(pts(find(w), "R30"), [], "8 days apart")
	test("R30 (a) one item carries both orders; 8 days apart stays quiet", t_r30_item)

	def t_r30_hours():
		w = world()
		for k, (v, item) in enumerate(((210000, "ITM-A"), (210000, "ITM-B"), (200000, "ITM-C"))):
			order(w, f"PO-T-001{k}", v, T0 + k * 20 * timedelta(hours=1), item=item)
		p = one(find(w), "R30")
		check(p["level"] == "To check" and "48 hours" in p["tie"] and "half" not in p["tie"], p["tie"])
		w["orders"]["PO-T-0012"]["base_grand_total"] = 179000
		eq(pts(find(w), "R30"), [], "together 1,000 under the line")
	test("R30 (b) three orders in 48 hours; a sum just under the line stays quiet", t_r30_hours)

	def t_r30_request():
		w = world()
		order(w, "PO-T-0021", 350000, T0, mr="MR-T-0001", mri="MRI-1")
		o = order(w, "PO-T-0022", 350000, T0 + 5 * DAY, item="ITM-B", mr="MR-T-0001", mri="MRI-2")
		res = find(w)
		eq(len(res["points"]), 1, "one point for the group")
		p = res["points"][0]
		check(p["checks"] == ["R30", "R29"] and p["level"] == "To check" and "MR-T-0001" in p["tie"], p)
		o["md_approved"] = True
		res = find(w)
		eq(pts(res, "R30") + pts(res, "R29"), [], "an MD-approved order is never a member")
	test("R30 (c) a shared request; an MD-approved member stays quiet", t_r30_request)

	def t_r30_sitting():
		w = world()
		order(w, "PO-T-0031", 350000, T0, approved=T0 + 6 * DAY + timedelta(hours=1))
		order(w, "PO-T-0032", 350000, T0 + 6 * DAY, item="ITM-B", approved=T0 + 6 * DAY + timedelta(minutes=70))
		p = one(find(w), "R30")
		check(p["level"] == "To check" and "30 minutes" in p["tie"] and "by the CEO" in p["tie"], p["tie"])
		w["orders"]["PO-T-0031"]["final_approved_at"] = T0 + timedelta(hours=2)
		p = one(find(w), "R30")
		eq((p["level"], p["tie"]), ("Note", ""), "nothing ties them: a Note")
		check(p["summary"].endswith(". Each is under the MD line of ₹6,00,000. Together they are ₹7,00,000, which is ₹1,00,000 above the line."
			" None came to the MD."), p["summary"])
	test("R30 (d) one approval sitting; approved days apart is a Note", t_r30_sitting)

	def t_r30_typed():
		w = world()
		order(w, "PO-T-0041", 350000, T0)
		o = order(w, "PO-T-0042", 350000, T0 + 20 * DAY, item="ITM-B", transaction_date=(T0 + 2 * DAY).date())
		p = one(find(w), "R30")
		check(p["level"] == "Note" and "typed PO date only" in p["summary"], p["summary"])
		o["transaction_date"] = (T0 + 20 * DAY).date()
		eq(pts(find(w), "R30"), [], "typed dates 20 days apart")
	test("R30 a group on the typed PO date only is a Note; typed dates apart stay quiet", t_r30_typed)

	def t_facts():
		w = world()
		order(w, "PO-T-0051", 320000, T0, item="ITM-DSL", qty=4000)
		order(w, "PO-T-0052", 320000, T0 + 4 * DAY, item="ITM-DSL", qty=4000)
		p = one(find(w), "R30")
		eq(p["facts_against"], ["Orders for High Speed Diesel (ITM-DSL) to this supplier in the 8 weeks before: 0",
			"Nothing received yet", "The MD approved another order to this supplier in the last 30 days: no"], "facts")
		order(w, "PO-T-0053", 90000, T0 - 30 * DAY, item="ITM-DSL", qty=1000)
		order(w, "PO-T-0054", 700000, T0 - 10 * DAY, item="ITM-B", md=True)
		order(w, "PO-T-0055", 90000, T0 - 30 * DAY, supplier=S2, item="ITM-DSL", qty=1000)
		receipt(w, "PO-T-0051", "PR-T-0051", T0 + DAY, 4000, 320000, "ITM-DSL")
		receipt(w, "PO-T-0052", "PR-T-0052", T0 + 5 * DAY, 4000, 320000, "ITM-DSL")
		p = one(find(w), "R30")
		eq(p["facts_against"], ["Orders for High Speed Diesel (ITM-DSL) to this supplier in the 8 weeks before: 1",
			"Received as 2 separate deliveries", "The MD approved another order to this supplier in the last 30 days: yes"], "facts")
	test("facts against a split: regular supply, deliveries, the MD's recent order", t_facts)

	def t_r29():
		w = world()
		order(w, "PO-T-0061", 350000, T0, mr="MR-T-0002", mri="MRI-21")
		o = order(w, "PO-T-0062", 350000, T0 + 20 * DAY, item="ITM-B", mr="MR-T-0002", mri="MRI-22")
		p = one(find(w), "R29")
		check(p["checks"] == ["R29"] and p["level"] == "To check" and "MR-T-0002" in p["tie"], p)
		o["creation"] = T0 + 40 * DAY
		eq(pts(find(w), "R29"), [], "40 days apart")
	test("R29 one request, two orders 20 days apart; 40 days apart stays quiet", t_r29)

	def t_r29_cut():
		w = world()
		order(w, "PO-T-0071", 350000, T0, qty=500, mr="MR-T-0003", mri="MRI-31")
		order(w, "PO-T-0072", 350000, T0 + 20 * DAY, qty=500, mr="MR-T-0003", mri="MRI-31")
		w["mr_items"]["MRI-31"] = {"material_request": "MR-T-0003", "item_code": "ITM-A", "stock_qty": 1000}
		p = one(find(w), "R29")
		check(p["level"] == "Serious" and "cut across 2 orders" in p["tie"], p["tie"])
		w["mr_items"]["MRI-31"]["stock_qty"] = 600
		eq(one(find(w), "R29")["level"], "To check", "ordered 1,000 against 600 asked for")
	test("R29 Serious when one request line is cut; ordered over 110% is To check", t_r29_cut)

	def t_r29_suppliers():
		w = world()
		order(w, "PO-T-0081", 350000, T0, mr="MR-T-0004", mri="MRI-41")
		o = order(w, "PO-T-0082", 350000, T0 + 20 * DAY, supplier=S2, item="ITM-B", mr="MR-T-0004", mri="MRI-42")
		p = one(find(w), "R29")
		check(p["level"] == "To check" and p["supplier"] is None and "2 suppliers" in p["summary"], p)
		w["items"][-1]["material_request"] = "MR-T-0005"
		eq(pts(find(w), "R29"), [], "two requests")
		check(o, "")
	test("R29 one request across two suppliers; two requests stay quiet", t_r29_suppliers)

	def t_r31():
		w = world()
		order(w, "PO-T-0091", 350000, T0, item="ITM-DSL", qty=3500)
		o = order(w, "PO-T-0092", 350000, T0 + 3 * DAY, supplier=S2, item="ITM-DSL", qty=3500)
		p = one(find(w), "R31")
		check(p["level"] == "To check" and "High Speed Diesel" in p["tie"] and p["supplier"] is None, p)
		o["company"] = C2
		eq(pts(find(w), "R31"), [], "two companies")
	test("R31 same item from two suppliers in 3 days; another company stays quiet", t_r31)

	def t_r32_supplier():
		w = world()
		order(w, "PO-T-0101", 350000, T0)
		o = order(w, "PO-T-0102", 350000, T0 + 20 * DAY, item="ITM-B")
		r = ranked(find(w), "supplier", S1)
		check(r and (r["d90"], r["orders"], r["largest"], r["marks"]) == (700000, 2, 350000, []), r)
		check(set(r) == RANK_KEYS, f"ranked keys {sorted(r)}")
		o["base_grand_total"] = 200000
		eq(ranked(find(w), "supplier", S1), None, "550,000 in 90 days")
	test("R32 a supplier over the line in 90 days is listed; under it is not", t_r32_supplier)

	def t_r32_catch_all():
		w = world()
		for k, (q, rate) in enumerate(((100, 2000), (100, 2500), (10, 25000))):
			order(w, f"PO-T-011{k}", q * rate, T0 + k * 10 * DAY, supplier=S2, item="ITM-MISC", qty=q)
		r = ranked(find(w), "item", "ITM-MISC")
		check(r and r["d90"] is None and "catch-all" in r["marks"][0], r)
		w["items"][-1].update(qty=100, stock_qty=100, base_rate=3000, base_net_amount=300000)
		w["orders"]["PO-T-0112"]["base_grand_total"] = 300000
		r = ranked(find(w), "item", "ITM-MISC")
		check(r and r["d90"] == 750000 and not r["marks"], r)
	test("R32 a catch-all code is marked and not summed; steady rates are summed", t_r32_catch_all)

	def t_r32_request():
		w = world()
		for k in range(3):
			order(w, f"PO-T-012{k}", 240000, T0 + k * 10 * DAY, qty=300, mr="MR-T-0006", mri="MRI-61")
		w["mr_items"]["MRI-61"] = {"material_request": "MR-T-0006", "item_code": "ITM-A", "stock_qty": 1000}
		r = ranked(find(w), "request", "MR-T-0006")
		check(r and r["d90"] == 720000 and len(r["marks"]) == 1 and "8,00,000" in r["marks"][0], r)
		order(w, "PO-T-0123", 240000, T0 + 25 * DAY, qty=300, mr="MR-T-0006", mri="MRI-61", md=True)
		eq(ranked(find(w), "request", "MR-T-0006")["marks"], [], "the MD approved one order on that line")
	test("R32 a request line worth more than the line is marked; an MD order on it is not", t_r32_request)

	def t_r32_coal():
		w = world()
		o = order(w, "PO-T-0131", 2740000, T0, supplier=S3, category="Coal", item="ITM-C")
		r = ranked(find(w), "supplier", S3)
		check(r and "Coal needs the MD only from 30,00,000" in r["marks"][0], r)
		o["base_grand_total"] = 500000
		eq(ranked(find(w), "supplier", S3), None, "a coal order of 5 lakh")
	test("R32 lists a coal order between the other line and the coal line; a small one is not", t_r32_coal)

	def t_r35_note():
		w = world()
		o = order(w, "PO-T-0141", 580000, T0, qty=1234)
		p = one(find(w), "R35")
		check(p["level"] == "Note" and "3.3% under" in p["summary"] and p["over_by"] == 0, p)
		o["base_grand_total"] = 550000
		eq(pts(find(w), "R35"), [], "8% under the line")
	test("R35 within 5% under the line is a Note; 8% under stays quiet", t_r35_note)

	def t_r35_sibling():
		w = world()
		order(w, "PO-T-0151", 580000, T0, qty=1234)
		o = order(w, "PO-T-0152", 10000, T0 - 3 * DAY, item="ITM-B")
		p = one(find(w), "R35")
		check(p["checks"] == ["R35"] and p["level"] == "To check" and "PO-T-0152" in p["tie"], p)
		o["creation"] = T0 - 10 * DAY
		eq(one(find(w), "R35")["level"], "Note", "the other order 10 days away")
	test("R35 (a) another order to the supplier within 7 days; 10 days away is a Note", t_r35_sibling)

	def t_r35_cut():
		w = world()
		order(w, "PO-T-0161", 500000, T0, qty=1234, logs=[log("Submitted", T0, 700000), log("Resubmitted", T0 + DAY, 500000),
			log("Final Approved", T0 + 2 * DAY, 500000, 2, "acct-ceo")])
		p = one(find(w), "R35")
		check(p["level"] == "To check" and "first submitted at 7,00,000" in p["tie"], p["tie"])
		w["logs"]["PO-T-0161"] = [log("Submitted", T0, 500000), log("Final Approved", T0 + DAY, 500000, 2, "acct-ceo")]
		eq(pts(find(w), "R35"), [], "never above the line")
	test("R35 (b) cut to fit after a submission over the line; never over stays quiet", t_r35_cut)

	def t_r35_skip():
		w = world()
		o = order(w, "PO-T-0171", 490000, T0, qty=1234, submitted_by_role="Purchase Manager",
			logs=[log("Submitted", T0, 490000, 2), log("Final Approved", T0 + DAY, 490000, 2, "acct-ceo")])
		res = find(w)
		eq(len(res["points"]), 1, "one point for the order")
		p = res["points"][0]
		check(p["checks"] == ["R35", "R39"] and "Purchase Manager review was skipped" in p["tie"], p)
		w["items"][-1]["material_request"] = "MR-T-0009"
		res = find(w)
		eq(pts(res, "R35") + pts(res, "R39"), [], "an order with a request")
		check(o, "")
	test("R35 (c) no request and the review skipped; with a request stays quiet", t_r35_skip)

	def t_r35_round():
		w = world()
		order(w, "PO-T-0181", 599940, T0, qty=99000)
		p = one(find(w), "R35")
		check(p["level"] == "To check" and "99,000" in p["tie"] and "99,100" in p["tie"], p["tie"])
		w["items"][-1]["qty"] = 99001
		eq(one(find(w), "R35")["level"], "Note", "99,001 is not round")
		w = world()
		order(w, "PO-T-0182", 510000, T0, qty=20)
		eq(one(find(w), "R35")["level"], "To check", "20 units at 85%, 30 would pass the line")
		w = world()
		order(w, "PO-T-0183", 510000, T0, qty=990)
		eq(pts(find(w), "R35"), [], "990 units at 85%: 1,000 would still be under the line")
		w = world()
		order(w, "PO-T-0184", 510000, T0, qty=12)
		eq(pts(find(w), "R35"), [], "12 units is not a round quantity")
	test("R35 (d) the largest round quantity under the line; not round or room left stays", t_r35_round)

	def t_r36_currency():
		w = world()
		o = order(w, "PO-T-0191", 1673316, T0, grand_total=17540, currency="USD", conversion_rate=95.4, amount_at_submission=17540)
		p = one(find(w), "R36")
		check(p["level"] == "Serious" and "foreign currency" in p["tie"] and "raised" not in p["tie"], p["tie"])
		o["md_approved"] = True
		eq(pts(find(w), "R36"), [], "MD approved")
	test("R36 a dollar order over the line without the MD; MD-approved stays quiet", t_r36_currency)

	def t_r36_raised():
		w = world()
		o = order(w, "PO-T-0201", 650000, T0, amount_at_submission=550000)
		p = one(find(w), "R36")
		check("raised after approval: 5,50,000 at submission, 6,50,000 now" in p["tie"] and "foreign" not in p["tie"], p["tie"])
		o.update(docstatus=0, approval_status="Pending MD")
		eq(pts(find(w), "R36"), [], "still in approval")
	test("R36 raised after approval; an order still in approval is never R36", t_r36_raised)

	def t_r36_outside():
		w = world()
		o = order(w, "PO-T-0211", 700000, T0, logs=[])
		check("outside the approval chain" in one(find(w), "R36")["tie"], "cause")
		o["base_grand_total"] = 590000
		eq(pts(find(w), "R36"), [], "under the line")
	test("R36 submitted outside the approval chain; under the line stays quiet", t_r36_outside)

	def t_r38_supplier():
		w = world()
		order(w, "PO-T-0221", 60000, T0, supplier=S3)
		order(w, "PO-T-0222", 60000, T0 + 3 * DAY, supplier=S3, item="ITM-B")
		p = one(find(w), "R38/R30")
		check(p["level"] == "Note" and p["line"] == 100000 and p["checks"] == ["R38/R30"], p)
		check("Each is under the review line of ₹1,00,000." in p["summary"] and "MD line" not in p["summary"],
			f"a group at the 1-lakh line names the review line, not the MD line: {p['summary']}")
		for o in w["orders"].values():
			o["category"] = "Grain"
		eq([p for p in find(w)["points"] if any(c.startswith("R38") for c in p["checks"])], [], "Grain has no 1-lakh line")
	test("R38 two orders past the 1-lakh line; a category without that line stays quiet", t_r38_supplier)

	def t_r38_request():
		w = world()
		order(w, "PO-T-0231", 60000, T0, qty=100, mr="MR-T-0007", mri="MRI-71")
		order(w, "PO-T-0232", 60000, T0 + 10 * DAY, qty=100, mr="MR-T-0007", mri="MRI-71")
		w["mr_items"]["MRI-71"] = {"material_request": "MR-T-0007", "item_code": "ITM-A", "stock_qty": 200}
		eq(one(find(w), "R38/R29")["level"], "To check", "a request line cut at the 1-lakh line")
		w["mr_items"]["MRI-71"]["stock_qty"] = 100
		eq(one(find(w), "R38/R29")["level"], "Note", "ordered twice what was asked for")
	test("R38 a request line cut under 1 lakh is To check; otherwise a Note", t_r38_request)

	def t_r39_skip():
		w = world()
		o = order(w, "PO-T-0241", 250000, T0, submitted_by_role="Purchase Manager",
			logs=[log("Submitted", T0, 250000, 2), log("Final Approved", T0 + DAY, 250000, 2, "acct-ceo")])
		p = one(find(w), "R39")
		check(p["level"] == "To check" and "entered by Purchase Manager; the Purchase Manager review was skipped" in p["summary"], p)
		o["self_skip_impossible"] = 1
		eq(pts(find(w), "R39"), [], "the rule allows nothing else")
	test("R39 a review skipped by the account entering it; self-skip-impossible stays quiet", t_r39_skip)

	def t_r39_own():
		w = world()
		order(w, "PO-T-0251", 250000, T0, logs=[log("Submitted", T0, 250000), log("Final Approved", T0 + DAY, 250000, 2)])
		p = one(find(w), "R39")
		check(p["level"] == "Serious" and "final approval given by the account that entered it" in p["summary"], p)
		w["logs"]["PO-T-0251"][-1]["action_by"] = "acct-ceo"
		eq(pts(find(w), "R39"), [], "approved by another account")
	test("R39 final approval by the account that entered it; another account stays quiet", t_r39_own)

	def t_r41_note():
		w = world()
		order(w, "PO-T-0261", 500000, T0, item="ITM-M", qty=100)
		receipt(w, "PO-T-0261", "PR-T-0261", T0 + 5 * DAY, 102, 510000, "ITM-M")
		p = one(find(w), "R41")
		eq((p["level"], p["line"], p["total"], p["over_by"]), ("Note", 500000, 510000, 10000), "R41 Note")
		w["receipts"]["PO-T-0261"][0]["base_net_amount"] = 501000
		eq(pts(find(w), "R41"), [], "0.2% more")
	test("R41 received 2% above is a Note; 0.2% stays quiet", t_r41_note)

	def t_r41_split():
		w = world()
		order(w, "PO-T-0271", 500000, T0, item="ITM-M", qty=100)
		receipt(w, "PO-T-0271", "PR-T-0271", T0 + 5 * DAY, 60, 300000, "ITM-M")
		receipt(w, "PO-T-0271", "PR-T-0272", T0 + 9 * DAY, 50, 300000, "ITM-M")
		p = one(find(w), "R41")
		check(p["level"] == "To check" and "Extra from quantity: 50,000" in p["extra_rows"]
			and "Extra from rate: 50,000" in p["extra_rows"] and "Not billed yet" in p["extra_rows"], p)
		w["allowance_now"]["ITM-M"] = 30
		eq(one(find(w), "R41")["level"], "Note", "inside the item's 30% allowance")
	test("R41 above the allowance is To check, quantity and rate apart; within it a Note", t_r41_split)

	def t_r42():
		w = world()
		order(w, "PO-T-0281", 300000, T0, item="ITM-X", qty=1000)
		at = T0 + DAY
		w["allowance_edits"] = [dict(item_code="ITM-X", at=at, old=10, new=200), dict(item_code="ITM-X", at=at + timedelta(minutes=40), old=200, new=10)]
		w["item_receipts"] = {"ITM-X": [dict(posting=at + timedelta(minutes=12), doc="PR-T-0281", po="PO-T-0281")]}
		p = one(find(w), "R42")
		check(p["level"] == "To check" and "from 10% to 200%" in p["summary"] and p["orders"] == ["PO-T-0281"]
			and any(x.startswith("Set back to 10% at") for x in p["extra_rows"]), p)
		w["item_receipts"]["ITM-X"][0]["posting"] = at + timedelta(minutes=45)
		eq(pts(find(w), "R42"), [], "receipt 45 minutes later")
		w["item_receipts"]["ITM-X"][0]["posting"] = at + timedelta(minutes=52)
		eq(pts(find(w), "R42"), [], "the setting back is not a raise")
	test("R42 allowance raised, receipt in 12 minutes, set back; 45 minutes stays quiet", t_r42)

	def t_r42_settings():
		w = world()
		order(w, "PO-T-0291", 300000, T0, item="ITM-Y", qty=1000)
		at = T0 + DAY
		w["allowance_edits"] = [dict(item_code=None, at=at, old=10, new=50)]
		w["item_receipts"] = {"ITM-Y": [dict(posting=at + timedelta(minutes=5), doc="PR-T-0291", po="PO-T-0291")]}
		check("Stock Settings" in one(find(w), "R42")["summary"], "Stock Settings edit")
		w["allowance_edits"][0]["item_code"] = "ITM-X"
		eq(pts(find(w), "R42"), [], "another item's allowance")
	test("R42 a Stock Settings raise matches any item; another item's raise stays quiet", t_r42_settings)

	def t_r43():
		w = world()
		order(w, "PO-T-0301", 300000, T0, category="Grain", item="ITM-C")
		w["gate_first"]["PO-T-0301"] = T0 - 3 * DAY
		receipt(w, "PO-T-0301", "PR-T-0301", T0 + DAY, 10, 300000, "ITM-C")
		order(w, "PO-T-0302", 300000, T0, supplier=S2, approved=T0 + 5 * DAY)
		receipt(w, "PO-T-0302", "PR-T-0302", T0 + DAY, 1, 300000)
		order(w, "PO-T-0303", 300000, T0, supplier=S3)
		w["gate_first"]["PO-T-0303"] = T0 + timedelta(hours=1)
		receipt(w, "PO-T-0303", "PR-T-0303", T0 + DAY, 1, 300000)
		got = {p["orders"][0]: p["level"] for p in pts(find(w), "R43")}
		eq(got, {"PO-T-0301": "Note", "PO-T-0302": "To check"}, "grain gate before order, store receipt before approval")
	test("R43 grain before the order is a Note, store a To check; in order stays quiet", t_r43)

	def t_r44():
		w = world()
		order(w, "PO-T-0311", 700000, datetime(2026, 8, 19, 10), item="ITM-M", qty=100, md=True, approved=datetime(2026, 8, 20, 10))
		o = order(w, "PO-T-0312", 700000, datetime(2026, 11, 5, 10), item="ITM-M", qty=100, md=True)
		p = one(find(w), "R44")
		check(p["level"] == "To check" and "PO-T-0312" in p["tie"] and "86 days" in p["summary"], p)
		w["items"][-1].update(qty=50, stock_qty=50, base_net_amount=350000)
		eq(one(find(w), "R44")["level"], "Note", "the newer order is half the quantity")
		w["orders"]["PO-T-0311"]["approved_date"] = datetime(2026, 10, 15, 10)
		eq(pts(find(w), "R44"), [], "approved 30 days ago")
		check(o, "")
	test("R44 approved, unused and repeated is To check; not repeated a Note; young stays quiet", t_r44)

	def t_sort():
		w = world()
		order(w, "PO-T-0321", 700000, T0, supplier=S2, logs=[])
		order(w, "PO-T-0322", 900000, T0, supplier=S2, item="ITM-B", logs=[])
		order(w, "PO-T-0323", 320000, T0, item="ITM-DSL", qty=4000)
		order(w, "PO-T-0324", 320000, T0 + 4 * DAY, item="ITM-DSL", qty=4000)
		order(w, "PO-T-0325", 580000, T0, supplier=S3, qty=1234)
		res = find(w)
		eq([(p["level"], p["orders"][0]) for p in res["points"]], [("Serious", "PO-T-0322"), ("Serious", "PO-T-0321"),
			("To check", "PO-T-0323"), ("Note", "PO-T-0325")], "Serious, To check, Note; then the amount over the line")
	test("points sort by level, then by how much they pass the line", t_sort)

	def t_one_point():
		w = world()
		order(w, "PO-T-0331", 350000, T0, qty=500, mr="MR-T-0008", mri="MRI-81")
		order(w, "PO-T-0332", 350000, T0 + 2 * DAY, qty=500, mr="MR-T-0008", mri="MRI-81")
		w["mr_items"]["MRI-81"] = {"material_request": "MR-T-0008", "item_code": "ITM-A", "stock_qty": 1000}
		res = find(w)
		eq(len(res["points"]), 1, "one point for the group")
		p = res["points"][0]
		eq((p["checks"], p["level"]), (["R30", "R29"], "Serious"), "highest level, every check named")
		check(any(x.startswith("R29: ") for x in p["extra_rows"]), "the joined check is printed")
	test("one group gives one point at its highest level naming every check", t_one_point)

	def t_window():
		w = world()
		order(w, "PO-T-0341", 350000, datetime(2026, 8, 10, 10))
		order(w, "PO-T-0342", 350000, datetime(2026, 8, 12, 10), item="ITM-B")
		eq(pts(find(w), "R30"), [], "a group before the window")
		w["orders"]["PO-T-0342"]["creation"] = datetime(2026, 8, 16, 9)
		eq(one(find(w), "R30")["orders"], ["PO-T-0341", "PO-T-0342"], "a group across the window start is whole")
	test("a group before the window is not shown; one across its start is shown whole", t_window)

	# look-alikes that broke the first version of the rules (kept so they stay fixed)
	def t_x_one_machine():
		w = world()
		order(w, "PO-T-0351", 510000, T0, item="ITM-M", qty=1)
		eq(pts(find(w), "R35"), [], "one machine at 85% of the line is not a round quantity")
		w = world()
		order(w, "PO-T-0352", 510000, T0, item="ITM-M", qty=40)
		eq(one(find(w), "R35")["level"], "To check", "40 units at 85% of the line: 50 would pass it")
	test("look-alike: one machine at 85% of the line is not cut to fit; 40 units are", t_x_one_machine)

	def t_x_resubmitted():
		w = world()
		order(w, "PO-T-0361", 250000, T0, logs=[log("Submitted", T0, 250000), log("Revised", T0 + DAY, 250000, 2, "acct-ceo"),
			log("Resubmitted", T0 + 2 * DAY, 250000, 2, "acct-other"), log("Final Approved", T0 + 3 * DAY, 250000, 2, "acct-ceo")])
		eq(pts(find(w), "R39"), [], "entered at the first step; another account's resubmission is not the entry's skip")
		lg = w["logs"]["PO-T-0361"]
		lg[0]["step_order"], lg[2]["step_order"] = 2, 1
		w["orders"]["PO-T-0361"]["submitted_by_role"] = "Purchase Manager"
		eq(one(find(w), "R39")["level"], "To check", "entered past the review, later resubmitted at the first step")
	test("look-alike: the first submission decides R39, not a later resubmission", t_x_resubmitted)

	def t_x_r36_ranked():
		w = world()
		for k in range(3):
			order(w, f"PO-T-040{k}", 240000, T0 + k * 10 * DAY, qty=300, mr="MR-T-0010", mri="MRI-101")
		order(w, "PO-T-0403", 700000, T0 + 25 * DAY, qty=300, mr="MR-T-0010", mri="MRI-101", logs=[])
		w["mr_items"]["MRI-101"] = {"material_request": "MR-T-0010", "item_code": "ITM-A", "stock_qty": 1000}
		res = find(w)
		eq(one(res, "R36")["orders"], ["PO-T-0403"], "the big order went past the MD")
		eq(ranked(res, "supplier", S1)["d90"], 1420000, "it still counts in the supplier's standing orders")
		check(ranked(res, "request", "MR-T-0010")["marks"], "the request line is still ordered only without the MD")
	test("look-alike: an order over the line without the MD (R36) still counts in R32", t_x_r36_ranked)

	def t_x_pending_md():
		w = world()
		order(w, "PO-T-0371", 700000, T0, docstatus=0, approval_status="Pending MD")
		eq(find(w)["ranked"], [], "an order on its way to the MD is not a standing small order")
	test("look-alike: an order pending the MD is not counted as a standing small order", t_x_pending_md)

	def t_x_no_lines():
		w = world()
		order(w, "PO-T-0381", 500000, T0, item="ITM-M", qty=100)
		w["items"] = []
		receipt(w, "PO-T-0381", "PR-T-0381", T0 + DAY, 102, 510000, "ITM-M")
		eq(one(find(w), "R41")["level"], "Note", "2% over, inside the general 10% allowance")
	test("look-alike: received 2% over with no order lines loaded is a Note, not To check", t_x_no_lines)

	def t_x_same_day():
		w = world()
		order(w, "PO-T-0391", 350000, T0)
		order(w, "PO-T-0392", 350000, T0 + 20 * DAY, item="ITM-B", transaction_date=T0.date())
		check("2 orders dated the same day" in one(find(w), "R30")["summary"], "plain words for one typed date")
	test("look-alike: two typed dates on one day read 'dated the same day'", t_x_same_day)

	# -- limits by date (20 Nov 2026) --
	CH = T0 + 2 * DAY  # a change of the Store MD limit, two days after T0
	MD_ROW = "Store-Above 6L"

	def lowered(at=CH, old="₹ 6,00,000.00", new="₹ 1,00,000.00", **kw):
		"""Today the Store MD rule starts at 1 lakh; it was changed from 6 lakh at `at`."""
		now = table()
		now[MD_ROW]["low"] = rules._amount(new)
		return now, [version(MD_ROW, at, changed=[["min_amount", old, new]], **kw)]

	def raised_then_back(up=CH, back=CH + DAY, by_back="acct-admin"):
		"""6 lakh -> 10 lakh at `up`, -> 6 lakh at `back` (None: never set back)."""
		now = table()
		vs = [version(MD_ROW, up, changed=[["min_amount", "₹ 6,00,000.00", "₹ 10,00,000.00"]])]
		if back:
			vs.append(version(MD_ROW, back, by=by_back, changed=[["min_amount", "₹ 10,00,000.00", "₹ 6,00,000.00"]]))
		else:
			now[MD_ROW]["low"] = 1000000.0
		return rules.history(now, vs)

	def md_run(hist, cat="Store"):
		return [(e["from"], e["line"]) for e in hist["lines"][cat]["md"]]

	def t_h_flat():
		hist = rules.history(table())
		eq({c: {k: [e["line"] for e in v] for k, v in h.items()} for c, h in hist["lines"].items()},
			{c: {k: [v] for k, v in l.items()} for c, l in LINES.items()}, "one stretch per line, equal to today's lines")
		check(all(e["from"] is None and e["since"].date() == SETUP.date() for h in hist["lines"].values() for v in h.values() for e in v),
			"rules made one after another on the setup day are one starting stretch")
		eq(hist["lines"]["Store"]["md"][0]["since"], SETUP, "it is dated from the category's first rule")
		eq(hist["gaps"], [], "no gap")
		eq(rules.lines_of(table().values()), LINES, "lines_of gives the loader's old answer")
	test("history: a never-changed rule table is one stretch per category (setup day merged)", t_h_flat)

	def t_h_currency():
		hist = rules.history(*lowered())
		eq(md_run(hist), [(None, 600000.0), (CH, 100000.0)], "the MD limit before and after the change")
		eq(hist["lines"]["Store"]["md"][1]["by"], "acct-admin", "who changed it stays in the data")
		eq(md_run(hist, "Grain"), [(None, 600000.0)], "another category is untouched")
		eq(hist["gaps"], [], "the record adds up")
		eq((rules._amount("₹ 6,00,000.00"), rules._amount(0), rules._amount(None), rules._amount("12,50,000")), (600000.0, 0.0, 0.0, 1250000.0), "amounts")
	test("history: a Currency change is read back from its printed text", t_h_currency)

	def t_h_open_band():
		now = table()
		now["Store-1L to 6L"]["high"] = 599999.0
		hist = rules.history(now, [version("Store-1L to 6L", CH, changed=[["max_amount", 0, "₹ 5,99,999.00"]])])
		eq([(s["from"], s["high"]) for s in hist["rules"]["Store-1L to 6L"]], [(SETUP + timedelta(seconds=1), 0.0), (CH, 599999.0)],
			"a raw 0 is an open band")
		eq(hist["gaps"], [], "no gap")
	test("history: a raw 0 in the record is an open-ended band, not an unreadable value", t_h_open_band)

	def t_h_steps():
		# (1) the MD step was added to a rule; before it the rule had none
		now = table()
		now["Store-1L to 6L"]["steps"].append({"name": "row-md", "step_order": 3, "role": "MD"})
		hist = rules.history(now, [version("Store-1L to 6L", CH, added=[["approval_steps", {"name": "row-md", "step_order": 3, "role": "MD"}]])])
		eq(md_run(hist), [(None, 600000.0), (CH, 100000.0)], "added: the MD limit dropped to that rule's start")
		# (2) the MD step was removed from the top rule: no MD limit after it
		now = table()
		gone = now[MD_ROW]["steps"].pop()
		hist = rules.history(now, [version(MD_ROW, CH, removed=[["approval_steps", gone]])])
		eq(md_run(hist), [(None, 600000.0), (CH, None)], "removed: no MD limit after the change")
		# (3) a step's role was changed from MD to CEO
		now = table()
		now[MD_ROW]["steps"][2]["role"] = "CEO"
		hist = rules.history(now, [version(MD_ROW, CH, row_changed=[["approval_steps", 2, f"{MD_ROW}#3", [["role", "MD", "CEO"]]]])])
		eq(md_run(hist), [(None, 600000.0), (CH, None)], "row changed: the step was the MD's before")
		eq(hist["gaps"], [], "all three add up")
		# a bulk re-save (every row removed and added again under new names, same roles) is no change of the limit
		now = table()
		old = [dict(s) for s in now[MD_ROW]["steps"]]
		for s in now[MD_ROW]["steps"]:
			s["name"] = "new-" + s["name"]
		hist = rules.history(now, [version(MD_ROW, CH, added=[["approval_steps", dict(s)] for s in now[MD_ROW]["steps"]],
			removed=[["approval_steps", s] for s in old])])
		eq((md_run(hist), hist["gaps"]), ([(None, 600000.0)], []), "re-saved rows: the limit did not change")
	test("history: step rows added, removed and changed are undone; a re-save of the same roles is no change", t_h_steps)

	def t_h_deleted():
		now = table()
		doc = {"name": "Store-Special", "creation": str(SETUP + 10 * DAY), "modified": str(SETUP + 10 * DAY), "owner": "acct-admin",
			"purchase_category": "Store", "min_amount": 300000, "max_amount": 0, "is_active": 1,
			"approval_steps": [{"name": "x1", "step_order": 1, "role": "MD"}]}
		hist = rules.history(now, [], [{"name": "Store-Special", "at": SETUP + 20 * DAY, "by": "acct-other", "doc": doc}])
		eq(md_run(hist), [(None, 600000.0), (SETUP + 10 * DAY, 300000.0), (SETUP + 20 * DAY, 600000.0)], "it counted while it lived")
		eq(hist["lines"]["Store"]["md"][2]["by"], "acct-other", "its end is the deleter's change")
		eq(hist["gaps"], [], "made and deleted unchanged: no gap")
		doc["modified"] = str(SETUP + 15 * DAY)
		gaps = rules.history(now, [], [{"name": "Store-Special", "at": SETUP + 20 * DAY, "by": "acct-other", "doc": doc}])["gaps"]
		eq([(g["cat"], g["from"], g["to"], g["why"]) for g in gaps], [("Store", SETUP + 10 * DAY, SETUP + 15 * DAY, "deleted")],
			"edited before it was deleted: its edits are gone with its record")
	test("history: a deleted rule lived from its making to its deletion; its lost edits are a gap", t_h_deleted)

	def t_h_chain():
		now, vs = lowered(new="₹ 2,00,000.00")
		now[MD_ROW]["low"] = 100000.0  # today 1 lakh, but the last record says it became 2 lakh
		hist = rules.history(now, vs)
		eq([(g["from"], g["to"], g["why"]) for g in hist["gaps"]], [(CH, None, "chain")], "a change after the last record has no record")
		eq(md_run(hist)[0], (None, 600000.0), "before the record it is still read from the record")
		now, vs = lowered(old="6.00.000,00")
		hist = rules.history(now, vs)
		eq([(g["from"], g["to"], g["why"]) for g in hist["gaps"]], [(None, CH, "unreadable")], "another number format: a gap, not a guess")
		eq(md_run(hist), [(None, 100000.0)], "the value is left as it is")
		hist = rules.history(table(), [{"docname": MD_ROW, "at": CH, "by": "acct-admin", "data": None}])
		eq([g["why"] for g in hist["gaps"]], ["unreadable"], "a record that cannot be read at all")
	test("history: a record that does not add up or cannot be read is a gap, never a crash or a guess", t_h_chain)

	def t_h_same_time():
		now = table()
		now[MD_ROW]["low"], now["Store-1L to 6L"]["high"] = 100000.0, 99999.0
		hist = rules.history(now, [version(MD_ROW, CH, changed=[["min_amount", "₹ 6,00,000.00", "₹ 1,00,000.00"]]),
			version("Store-1L to 6L", CH, changed=[["max_amount", "₹ 5,99,999.00", "₹ 99,999.00"]]),
			version("Store-1L to 6L", CH + timedelta(hours=1), changed=[["priority", 2, 3]])])
		eq(md_run(hist), [(None, 600000.0), (CH, 100000.0)], "two records at one moment are one change; a later one that moves no limit is none")
	test("history: two changes at the same moment are one; a change that moves no limit adds no stretch", t_h_same_time)

	def t_flat_same():
		check(len(PAIRS) > 60, f"only {len(PAIRS)} worlds compared")
		bad = [k for k, (a, b) in enumerate(PAIRS) if a["points"] != b["points"] or a["ranked"] != b["ranked"]]
		eq(bad, [], "worlds whose points differ once the limits are rebuilt by date")
		check(all(not b["gaps"] and all(len(l["rows"]) == 1 and not l["changed"] for l in b["limits"]) for a, b in PAIRS),
			"a never-changed table has one row per category and no gap sentence")
		check(all(p["line_used"] is None and p["limit"] is None for a, b in PAIRS for p in b["points"]), "and no limit sentence")
	test("acceptance: every world gives the same points with a never-changed rule table as with today's lines", t_flat_same)

	def t_route_time():
		hist = rules.history(*lowered())  # Store MD limit: 6 lakh until T0 + 2 days, 1 lakh after
		w = world()
		order(w, "PO-T-0501", 350000, T0)
		order(w, "PO-T-0502", 350000, T0 + DAY, item="ITM-B")
		p = one(find(with_history(w, hist)), "R30")
		eq((p["line"], p["orders"]), (600000.0, ["PO-T-0501", "PO-T-0502"]), "both sent before the change: the 6 lakh limit")
		eq(p["line_used"], "MD line used: ₹6,00,000, the Store limit in force from 01-04-2026 to 07-10-2026 10:00. Today's limit is ₹1,00,000.",
			"the limit used is said in words")
		today_only = copy.deepcopy(w)
		today_only["lines"]["Store"] = {"md": 100000.0, "pm": None}  # what the report measured before: today's limit for every date
		eq(pts(rules.find_points(today_only), "R30"), [], "control: against today's 1 lakh the same orders were not small orders at all")
		# sent again after the change: measured against the limit of its last submission
		w["logs"]["PO-T-0502"] = [log("Submitted", T0 + DAY, 350000), log("Revised", CH, 350000, 2, "acct-ceo"),
			log("Resubmitted", CH + DAY, 350000)]
		eq(pts(find(with_history(w, hist)), "R30"), [], "resubmitted after the change: over the new limit, so no small order")
		w = world()
		order(w, "PO-T-0503", 60000, CH + DAY)
		order(w, "PO-T-0504", 60000, CH + 2 * DAY, item="ITM-B")
		p = one(find(with_history(w, hist)), "R30")
		eq((p["line"], p["line_used"]), (100000.0, "MD line used: ₹1,00,000, the Store limit in force since 07-10-2026 10:00."),
			"sent after the change: today's limit, and no 'today's limit' tail")
	test("an order is measured against the limit in force when it was last sent for approval", t_route_time)

	def t_no_mix():
		w = world()
		order(w, "PO-T-0511", 350000, T0 + DAY)
		order(w, "PO-T-0512", 350000, T0 + 4 * DAY, item="ITM-B")
		eq(one(find(w), "R30")["orders"], ["PO-T-0511", "PO-T-0512"], "control: one group when the limit never changed")
		now = table()
		now[MD_ROW]["low"] = 1000000.0
		up = rules.history(now, [version(MD_ROW, CH, changed=[["min_amount", "₹ 6,00,000.00", "₹ 10,00,000.00"]])])
		eq(pts(find(with_history(w, up)), "R30"), [], "one order before a change and one after it are not one group")
		eq(pts(find(with_history(w, raised_then_back())), "R30"), [], "nor across a raise that was set back to the same value")
		order(w, "PO-T-0513", 350000, T0 + 5 * DAY, item="ITM-C")
		eq(one(find(with_history(w, raised_then_back())), "R30")["orders"], ["PO-T-0512", "PO-T-0513"], "two after the set-back are a group")
	test("a group never mixes orders measured against two stretches of the limit", t_no_mix)

	def t_steps_then():
		hist = rules.history(table())
		w = world()
		o = order(w, "PO-T-0521", 490000, T0, qty=1234, submitted_by_role="Purchase Manager",
			logs=[log("Submitted", T0, 490000, 2), log("Final Approved", T0 + DAY, 490000, 2, "acct-ceo")])
		check("R39" in one(find(with_history(w, hist)), "R35")["checks"], "control: the review step is skipped under today's steps")
		o["steps"] = [dict(CEO, step_order=2)]  # as the rule stood when it was sent: the CEO alone, at step 2
		res = find(with_history(w, hist))
		eq(pts(res, "R39"), [], "no review step existed then, so none was skipped")
	test("a skipped review is judged on the rule's steps as they stood then", t_steps_then)

	def t_r32_then():
		hist = raised_then_back(up=datetime(2026, 12, 1, 9), back=None)  # raised to 10 lakh after the selected dates end (14 Nov)
		w = world()
		for k in range(3):
			order(w, f"PO-T-053{k}", 250000, T0 + k * 10 * DAY)
		eq(ranked(find(with_history(w, hist)), "supplier", S1)["d90"], 750000, "7.5 lakh passes the 6 lakh limit of the dates' end")
		w.update(to_date=date(2026, 12, 20), today=date(2026, 12, 20))
		eq(ranked(find(with_history(w, hist)), "supplier", S1), None, "dates ending after the raise: 7.5 lakh is under 10 lakh")
	test("the standing-order list uses the limits at the end of the selected dates", t_r32_then)

	def t_limits_gaps():
		hist = rules.history(*lowered())
		w = world()
		order(w, "PO-T-0541", 350000, T0)
		res = find(with_history(w, hist))
		store = next(l for l in res["limits"] if l["category"] == "Store")
		eq((store["changed"], store["rows"]), (1, [{"line": 600000.0, "from": SETUP, "to": CH, "today": False},
			{"line": 100000.0, "from": CH, "to": None, "today": True}]), "the Store rows")
		eq([(l["category"], l["changed"], len(l["rows"])) for l in res["limits"] if l["category"] != "Store"],
			[("Coal", 0, 1), ("Grain", 0, 1)], "the others are one row, not changed")
		eq(res["gaps"], [], "the order fits the band of its rule")
		w.update(from_date=date(2026, 10, 20))
		eq(next(l for l in find(with_history(w, hist))["limits"] if l["category"] == "Store")["rows"],
			[{"line": 100000.0, "from": CH, "to": None, "today": True}], "only the stretches that touch the dates")
		w.update(from_date=date(2026, 8, 16))
		w["logs"]["PO-T-0541"][0]["po_amount"] = 50000  # sent at 50,000 under a rule that starts at 1 lakh
		g = find(with_history(w, hist))["gaps"]
		check(len(g) == 1 and g[0].startswith("1 order(s) of these dates do not fit") and "PO-T-0541" in g[0], g)
		now, vs = lowered(new="₹ 2,00,000.00")
		now[MD_ROW]["low"] = 100000.0  # the record does not add up after the change (see the chain test)
		w["logs"]["PO-T-0541"][0]["po_amount"] = 350000
		eq(find(with_history(w, rules.history(now, vs)))["gaps"], [], "no order of the dates was sent in the uncovered time: nothing is said")
		order(w, "PO-T-0542", 350000, CH + DAY)
		g = find(with_history(w, rules.history(now, vs)))["gaps"]
		check(any(x.startswith("The record of the Store limits is not complete: 1 order(s)") for x in g), g)
	test("the limits rows cover the selected dates; a gap is said only when an order of the dates is touched", t_limits_gaps)

	def t_r45_levels():
		w = world()
		order(w, "PO-T-0551", 800000, CH + timedelta(hours=3))  # between 6 and 10 lakh, sent while the limit was 10 lakh
		order(w, "PO-T-0552", 800000, CH + timedelta(hours=4), item="ITM-B", md=True)  # the MD saw this one
		order(w, "PO-T-0553", 300000, CH + timedelta(hours=5), item="ITM-C")  # under the old limit
		p = one(find(with_history(w, raised_then_back())), "R45")
		eq((p["level"], p["orders"], p["total"], p["line"], p["supplier"], p["category"]), ("Serious", ["PO-T-0551"], 800000, None, None, "Store"),
			"raised, set back, one order between: Serious; the MD-approved and the small order are not counted")
		eq(set(p["limit"]), LIMIT_KEYS, "limit keys")
		lim = p["limit"]
		eq((lim["direction"], lim["old"], lim["new"], lim["at"], lim["back_at"], lim["back_to"], lim["same_account"]),
			("raised", 600000.0, 1000000.0, CH, CH + DAY, 600000.0, True), "the change")
		eq((lim["by_role"], lim["back_by_role"]), ("IT Head", "IT Head"), "people by role")
		eq([(m["name"], m["between"], m["line"]) for m in lim["marks"]], [("PO-T-0551", True, 1000000.0), ("PO-T-0552", False, 1000000.0),
			("PO-T-0553", False, 1000000.0)], "every Store order of the chart's days is a mark; one is flagged")
		eq([(x["line"]) for x in lim["steps"]], [600000.0, 1000000.0, 600000.0], "the chart's steps")
		check(lim["range"]["from"] <= lim["steps"][0]["from"] and lim["steps"][-1]["to"] <= lim["range"]["to"] and lim["marks_cut"] == 0, lim["range"])
		check("raised from ₹6,00,000 to ₹10,00,000 on 07-10-2026 10:00" in p["summary"] and "set back to ₹6,00,000 on 08-10-2026 10:00" in p["summary"]
			and "1 order(s) at or over the old limit, ₹8,00,000 together, did not come to the MD" in p["summary"], p["summary"])
		check("Set back after 24 hours, by the same account." in p["extra_rows"], p["extra_rows"])
		p = one(find(with_history(w, raised_then_back(by_back="acct-other"))), "R45")
		check(p["limit"]["same_account"] is False and "Set back after 24 hours, by an account with the role System Manager." in p["extra_rows"],
			p["extra_rows"])
		p = one(find(with_history(w, raised_then_back(back=None))), "R45")
		eq((p["level"], p["orders"], p["limit"]["back_at"]), ("To check", ["PO-T-0551"], None), "raised and not set back: To check")
		del w["orders"]["PO-T-0551"]
		p = one(find(with_history(w, raised_then_back())), "R45")
		eq((p["level"], p["orders"], p["total"]), ("Note", [], 0), "raised and set back with no order between: a Note")
		check("no order at or over the old limit passed without the MD" in p["summary"], p["summary"])
	test("R45 raised + set back + an order between is Serious; not set back To check; no order a Note", t_r45_levels)

	def t_r45_other():
		w = world()
		order(w, "PO-T-0561", 800000, CH + timedelta(hours=3))
		p = one(find(with_history(w, rules.history(*lowered()))), "R45")
		eq((p["level"], p["limit"]["direction"], p["orders"]), ("Note", "lowered", []), "a lowered limit is a Note with no orders")
		check("lowered from ₹6,00,000 to ₹1,00,000" in p["summary"], p["summary"])
		# the MD step taken out, and put back a day later
		now = table()
		gone = dict(now[MD_ROW]["steps"][2])
		hist = rules.history(now, [version(MD_ROW, CH, removed=[["approval_steps", gone]]),
			version(MD_ROW, CH + DAY, added=[["approval_steps", gone]])])
		p = one(find(with_history(w, hist)), "R45")
		eq((p["level"], p["limit"]["direction"], p["limit"]["new"], p["limit"]["back_to"], p["orders"]),
			("Serious", "removed", None, 600000.0, ["PO-T-0561"]), "the MD step taken out and put back, an order in between")
		check("The MD step was taken out of the Store limits" in p["summary"], p["summary"])
		# an MD limit where there was none
		now = table()
		row = {"name": "row-md", "step_order": 2, "role": "MD"}
		now["Grain-Under 6L"]["steps"] = [s for s in now["Grain-Under 6L"]["steps"]]
		del now["Grain-Above 6L"]
		hist = rules.history(now, [])
		now["Grain-Under 6L"]["steps"].append(row)
		hist = rules.history(now, [version("Grain-Under 6L", CH, added=[["approval_steps", row]])])
		p = one(find(with_history(w, hist)), "R45")
		eq((p["level"], p["limit"]["direction"], p["limit"]["old"], p["limit"]["new"]), ("Note", "introduced", None, 0.0), "an MD limit set where none was")
	test("R45 a lowered limit and a new MD limit are Notes; the MD step taken out counts as a raise", t_r45_other)

	def t_fold():
		# a script takes every step out of the MD rule and puts them back 41 seconds later (demo, 3 May 2026)
		now = table()
		rows = [dict(r) for r in now[MD_ROW]["steps"]]
		hist = rules.history(now, [version(MD_ROW, CH, removed=[["approval_steps", r] for r in rows]),
			version(MD_ROW, CH + timedelta(seconds=41), added=[["approval_steps", r] for r in rows])])
		eq(md_run(hist), [(None, 600000.0), (CH, None), (CH + timedelta(seconds=41), 600000.0)], "the record itself keeps the 41 seconds")
		w = world()
		order(w, "PO-T-0581", 350000, CH - DAY)
		order(w, "PO-T-0582", 350000, CH + DAY, item="ITM-B")
		res = find(with_history(w, hist))
		eq((pts(res, "R45"), [l for l in res["limits"] if l["category"] == "Store"][0]), ([], {"category": "Store", "changed": 0,
			"rows": [{"line": 600000.0, "from": SETUP, "to": None, "today": True}]}), "no order was sent in them: not a limit, not a finding")
		eq(one(res, "R30")["orders"], ["PO-T-0581", "PO-T-0582"], "and a group runs across them")
		order(w, "PO-T-0583", 800000, CH + timedelta(seconds=20), item="ITM-C")  # an order sent inside the 41 seconds
		res = find(with_history(w, hist))
		p = one(res, "R45")
		eq((p["level"], p["orders"], p["limit"]["direction"]), ("Serious", ["PO-T-0583"], "removed"), "an order was sent in them: it is shown")
		check("Set back after 41 seconds, by the same account." in p["extra_rows"], p["extra_rows"])
	test("a stretch of under 5 minutes with no order sent in it is not a limit; with an order it is shown", t_fold)

	def t_restore():
		def down_up(days):
			return rules.history(table(), [version(MD_ROW, CH, changed=[["min_amount", "₹ 6,00,000.00", "₹ 1,00,000.00"]]),
				version(MD_ROW, CH + days * DAY, changed=[["min_amount", "₹ 1,00,000.00", "₹ 6,00,000.00"]])])
		w = world()
		order(w, "PO-T-0591", 350000, CH + 12 * DAY)  # sent after the limit was back at 6 lakh
		p = one(find(with_history(w, down_up(4))), "R45")
		eq((p["level"], p["limit"]["direction"], p["limit"]["back_at"], p["limit"]["back_to"], p["orders"]),
			("Note", "lowered", CH + 4 * DAY, 600000.0, []), "lowered and put back within a week: one Note, no orders")
		check("lowered from ₹6,00,000 to ₹1,00,000 on 07-10-2026 10:00. It was put back to ₹6,00,000 on 11-10-2026 10:00." in p["summary"]
			and "Set back after 4 days, by the same account." in p["extra_rows"], (p["summary"], p["extra_rows"]))
		res = find(with_history(w, down_up(10)))
		eq(sorted((p["level"], p["limit"]["direction"], tuple(p["orders"])) for p in pts(res, "R45")),
			[("Note", "lowered", ()), ("To check", "raised", ("PO-T-0591",))], "a limit that stood 10 days and was then raised is a raise")
		up = next(p for p in pts(res, "R45") if p["limit"]["direction"] == "raised")
		check("Before 07-10-2026 10:00 the limit was ₹6,00,000; it stood at ₹1,00,000 for 10 days." in up["extra_rows"], up["extra_rows"])
	test("R45 a lowering put back within a week is one Note; a later raise back is a raise and says what stood before", t_restore)

	def t_r45_later():
		now = table()
		now[MD_ROW]["low"] = 800000.0
		hist = rules.history(now, [version(MD_ROW, CH, changed=[["min_amount", "₹ 6,00,000.00", "₹ 10,00,000.00"]]),
			version(MD_ROW, CH + 3 * DAY, changed=[["min_amount", "₹ 10,00,000.00", "₹ 8,00,000.00"]])])
		w = world()
		order(w, "PO-T-0601", 700000, CH + 4 * DAY)  # sent after the second change: between 6 lakh and the 8 lakh of its day
		got = sorted((p["limit"]["direction"], p["limit"]["at"], p["level"], tuple(p["orders"])) for p in pts(find(with_history(w, hist)), "R45"))
		eq(got, [("lowered", CH + 3 * DAY, "Note", ()), ("raised", CH, "To check", ("PO-T-0601",))],
			"a raise that was never set back does not hide the change after it")
		w.update(from_date=(CH + 2 * DAY).date())
		got = [(p["limit"]["direction"], p["limit"]["at"]) for p in pts(find(with_history(w, hist)), "R45")]
		eq(got, [("lowered", CH + 3 * DAY)], "dates that start after the raise still show the later change")
	test("R45 a raise that stays does not swallow a later change of the limit", t_r45_later)

	def t_r45_dates():
		w = world()
		order(w, "PO-T-0571", 800000, CH + timedelta(hours=3))
		early = datetime(2026, 6, 1, 10)
		eq(pts(find(with_history(w, raised_then_back(up=early, back=early + DAY))), "R45"), [], "a change and its set-back before the dates")
		p = one(find(with_history(w, raised_then_back(up=early, back=CH + DAY))), "R45")
		eq((p["level"], p["orders"]), ("Serious", ["PO-T-0571"]), "raised before the dates, set back inside them: shown")
		eq(pts(find(with_history(w, raised_then_back(up=early, back=None))), "R45"), [], "raised long before and still in force: the limit, not a finding")
		w.update(to_date=date(2026, 10, 6))
		eq(pts(find(with_history(w, raised_then_back())), "R45"), [], "a change after the dates end")
	test("R45 is shown only when the change or its set-back falls in the selected dates", t_r45_dates)

	def t_shape():
		points = [p for res in ALL for p in res["points"]]
		check(len(points) > 40, f"only {len(points)} points seen")
		for p in points:
			eq(set(p), KEYS, "point keys")
			eq(p["extra_rows"][-1], rules.LAST_LINE, f"last line of {p['checks']}")
			check(p["level"] in rules.LEVELS and p["over_by"] >= 0, p)
			check(p["limit"] is None or (p["checks"] == ["R45"] and set(p["limit"]) == LIMIT_KEYS), p["limit"])
	test("every point has the spec's keys and ends with the fixed last line", t_shape)

	def t_words():
		blob = json.dumps(ALL, default=str)
		check("acct-" not in blob and "@" not in blob, "an account id reached the output")
		low = blob.lower()
		check(not [x for x in WORDS if x in low], "a loaded word reached the output")
		roles = {p["summary"].split("entered by ")[1].split(";")[0] for res in ALL for p in res["points"] if "entered by " in p["summary"]}
		check(roles and roles <= {"Purchase Manager", "Purchase User"}, f"people by role only: {roles}")
	test("no account id, no person and no loaded word in any output; roles only", t_words)


# -- Part B: the report on the demo database, through the real import path; everything rolled back --
MOD = "trustbit_ethanol.ts_gate_entry.report.ts_order_watch.ts_order_watch"
RULES_MOD = "trustbit_ethanol.ts_gate_entry.ts_order_watch_rules"
REFUSAL = "Order watch is for the MD."
PM_ROLE = "Purchase Manager"
LABEL_ROLES = ("MD", "CEO", "AVP", "Purchase Manager", "Grain Purchase Manager", "Purchase User")
UNREADABLE = ("Purchase Receipt", "Purchase Invoice", "TS Token", "TS Gate Entry", "Version")
MD_LOGIN = {"MD", "All", "Guest", "Desk User"}  # the roles an MD-only login holds
# rows a test user can leave: (doctype, field holding the user's id); counted for this run's users only
LEFT = (("User", "name"), ("Has Role", "parent"), ("Contact", "email_id"), ("Version", "docname"),
	("User Permission", "user"), ("DocShare", "user"), ("Notification Settings", "name"))
SP = "orderwatch_partb"
TAG = os.urandom(3).hex()  # no earlier run's or real user's account has this name
MD_LINE = """SELECT r.purchase_category AS cat, MIN(r.min_amount) AS line FROM `tabTS PO Approval Rule` r
	WHERE r.is_active = 1 AND EXISTS (SELECT 1 FROM `tabTS PO Approval Step` s
		WHERE s.parent = r.name AND s.parenttype = 'TS PO Approval Rule' AND s.role = 'MD')
	GROUP BY r.purchase_category"""
PM_LINE = """SELECT x.cat, x.line FROM (SELECT r.purchase_category AS cat, MIN(r.min_amount) AS line
		FROM `tabTS PO Approval Rule` r WHERE r.is_active = 1 AND EXISTS (SELECT 1 FROM `tabTS PO Approval Step` s
			WHERE s.parent = r.name AND s.parenttype = 'TS PO Approval Rule' AND s.role = %(pm)s)
		GROUP BY r.purchase_category) x
	WHERE EXISTS (SELECT 1 FROM `tabTS PO Approval Rule` r2 WHERE r2.is_active = 1 AND r2.purchase_category = x.cat
		AND r2.min_amount < x.line AND NOT EXISTS (SELECT 1 FROM `tabTS PO Approval Step` s2
			WHERE s2.parent = r2.name AND s2.parenttype = 'TS PO Approval Rule' AND s2.role = %(pm)s))"""
MD_APPROVED = """SELECT po.name FROM `tabPurchase Order` po WHERE po.name IN %(n)s AND EXISTS (
	SELECT 1 FROM `tabTS Approval Log` l WHERE l.parent = po.name AND l.parenttype = 'Purchase Order'
		AND l.action IN ('Approved', 'Final Approved')
		AND IF(EXISTS (SELECT 1 FROM `tabTS PO Approval Rule` r WHERE r.name = po.ts_approval_rule),
			EXISTS (SELECT 1 FROM `tabTS PO Approval Step` s WHERE s.parent = po.ts_approval_rule
				AND s.parenttype = 'TS PO Approval Rule' AND s.step_order = l.step_order AND s.role = 'MD'),
			l.action_by_role = 'MD'))"""


def record(name, ok, info=""):
	"""Part B's check: one named result and what was seen (never an account id)."""
	RESULTS.append((name, bool(ok), str(info)[:300]))
	print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" :: {info}" if info else ""))


def _left():
	like = f"orderwatch.%.{TAG}@example.com"
	return {dt: frappe.db.count(dt, {field: ["like", like]}) for dt, field in LEFT}


def part_b(check, writes=True):
	"""Test users are made inside a savepoint; the savepoint and then the whole transaction are rolled back. No
	commit and no Error Log row can happen meanwhile (both are recorded instead). writes=False runs only the
	read-only checks (B3, and the B4 lookup)."""
	print("part B: the report on this site")
	ow = importlib.import_module(MOD)
	frappe.set_user("Administrator")
	kept, kept_log, commits, logged, made = frappe.db.__dict__.get("commit"), frappe.log_error, [], [], []
	kept_search = frappe.local.conf.get("disable_global_search")
	frappe.db.commit = lambda *a, **k: commits.append(1)
	frappe.log_error = lambda *a, **k: logged.append(1)  # Error Log rows survive a rollback; a failing B2 would write one
	frappe.local.conf.disable_global_search = 1  # a saved User would put a search row on a queue
	frappe.db.savepoint(SP)
	try:
		_roles_and_lines(check, ow)
		_limits_by_date(check, ow)
		_inputs(check, ow)
		if writes:
			_refused(check, ow, made)
			_md_only(check, made)
			_page(check, ow, made)
			_confidential(check, ow, made)
		else:
			po = _confidential_po()
			check("B4 dry run: a confidential order to test with", True, po.name if po else "none on this site")
	finally:
		frappe.set_user("Administrator")
		frappe.clear_messages()
		frappe.db.rollback(save_point=SP)
		frappe.db.rollback()
		if kept is None:
			del frappe.db.commit
		else:
			frappe.db.commit = kept
		frappe.log_error = kept_log
		frappe.local.conf.disable_global_search = kept_search
		for name in made:
			frappe.clear_cache(user=name)
	left = {dt: n for dt, n in _left().items() if n}
	check("B0 nothing left behind: no test-user row, no commit, no Error Log row", not left and not commits,
		f"commits {len(commits)}; rows left {left or 'none'}; Error Log rows held back {len(logged)}")


def _user(tag, roles, made):
	frappe.set_user("Administrator")
	email = f"orderwatch.{tag}.{TAG}@example.com"
	roles = [r for r in roles if frappe.db.exists("Role", r)]
	frappe.get_doc({"doctype": "User", "email": email, "first_name": f"OW {tag}", "send_welcome_email": 0,
		"roles": [{"role": r} for r in roles]}).insert(ignore_permissions=True)
	made.append(email)
	return email


def _roles_and_lines(check, ow):
	"""B3: the line of each live category and md_approved match hand queries; the role names in use."""
	from frappe.permissions import get_doctype_roles

	step_roles = sorted(r[0] for r in frappe.db.sql(
		"SELECT DISTINCT role FROM `tabTS PO Approval Step` WHERE parenttype = 'TS PO Approval Rule'"))
	missing = [r for r in LABEL_ROLES if not frappe.db.exists("Role", r)]
	check("B3 role names: MD and CEO exist, Purchase Manager is a step role",
		"MD" not in missing and "CEO" not in missing and PM_ROLE in step_roles,
		f"step roles {step_roles}; label roles missing on this site: {missing or 'none'}")
	# query_report.run refuses a user who cannot read or select a Link column's doctype once that column has a value
	links = {c["options"] for c in ow._columns() if c["fieldtype"] == "Link"}
	shut = sorted(dt for dt in links if not MD_LOGIN & set(get_doctype_roles(dt, "read") + get_doctype_roles(dt, "select")))
	check("B3 every Link column opens for an MD-only login", not shut, f"no read for the MD on: {shut or 'none'}")
	data = ow.load({"from_date": add_days(today(), -365), "to_date": today()})
	got_md = {c: v["md"] for c, v in data["lines"].items() if v["md"] is not None}
	got_pm = {c: v["pm"] for c, v in data["lines"].items() if v["pm"] is not None}
	hand_md = {r.cat: flt(r.line) for r in frappe.db.sql(MD_LINE, as_dict=True)}
	hand_pm = {r.cat: flt(r.line) for r in frappe.db.sql(PM_LINE, {"pm": PM_ROLE}, as_dict=True)}
	check("B3 the MD line of each live category matches a hand query", got_md == hand_md, f"loader {got_md}; hand {hand_md}")
	check("B3 the 1-lakh line matches a hand query", got_pm == hand_pm, f"loader {got_pm}; hand {hand_pm}")
	orders, hand = data["orders"], set()
	names = sorted(orders)
	for i in range(0, len(names), 1000):
		hand |= {r[0] for r in frappe.db.sql(MD_APPROVED, {"n": tuple(names[i:i + 1000])})}
	got = {n for n, o in orders.items() if o["md_approved"]}
	check("B3 md_approved matches a hand query", got == hand,
		f"{len(got)} of {len(orders)} loaded orders MD-approved; differ: {sorted(got ^ hand)[:5]}")
	keys = {"today", "from_date", "to_date", "lines", "orders", "items", "mr_items", "logs", "rule_steps", "receipts",
		"gate_first", "allowance_edits", "allowance_now", "stock_allowance", "item_receipts", "line_history", "rule_history",
		"history_gaps", "change_roles"}
	check("B3 the loader gives exactly the contract keys", set(data) == keys, str(sorted(set(data) ^ keys)))


def _limits_by_date(check, ow):
	"""B7 (limits by date, 20 Nov): the limits rebuilt from this site's own change record. Read-only."""
	data = ow.load({"from_date": add_days(today(), -365), "to_date": today()})
	hist, lines, orders = data["line_history"], data["lines"], data["orders"]
	ends = {c: {k: v[-1]["line"] for k, v in h.items()} for c, h in hist.items()}
	wrong = sorted(c for c in set(ends) | set(lines) if ends.get(c, {}) != (lines.get(c) or {"md": None, "pm": None}))
	check("B7 the rebuilt limits of every category end at today's rule table", bool(ends) and not wrong,
		f"{len(ends)} categories; differ: {wrong or 'none'}")
	firsts = [v[0] for h in hist.values() for v in h.values()]
	check("B7 every category starts with one stretch that also holds for older orders",
		all(e["from"] is None and e.get("since") for e in firsts) and all(e["from"] for h in hist.values() for v in h.values() for e in v[1:]),
		f"{len(firsts)} first stretches")
	# an order sent for approval after its category's last change must carry today's limit; one sent before it must not
	# be measured against a limit that was not yet in force
	late = early = bad = 0
	for o in orders.values():
		run = (hist.get(o["category"]) or {}).get("md") or []
		if not run:
			continue
		last = run[-1]["from"]
		if last is None or o["routed_at"] >= last:
			late += 1
			bad += o["md_line"] != run[-1]["line"]
		else:
			early += 1
			own = [e for e in run if e["from"] is None or e["from"] <= o["routed_at"]][-1]
			bad += o["md_line"] != own["line"] or own is run[-1]
	check("B7 each order carries the MD limit of the time it was sent for approval", late + early > 0 and not bad,
		f"{late} sent after their category's last change, {early} before it; wrong: {bad}")
	out = ow.rules.find_points(data)
	mixed = [p["checks"] for p in out["points"] if p["checks"][0] in ("R30", "R29", "R31") and len(p["orders"]) > 1
		and len({orders[n]["md_line"] for n in p["orders"] if n in orders}) != 1]
	check("B7 no group mixes orders measured against two limits", not mixed, f"{len(mixed)} mixed groups")
	rows = {l["category"]: l["rows"] for l in out["limits"]}
	now = {c: r[-1]["line"] for c, r in rows.items() if r and r[-1]["today"]}
	check("B7 the limits rows name every category, and the row marked today is today's limit",
		set(rows) == set(hist) and all(rows.values()) and now == {c: e["md"] for c, e in ends.items()},
		f"{len(rows)} categories, {sum(l['changed'] for l in out['limits'])} change(s) in the last 365 days")
	shown = json.dumps([out["limits"], out["gaps"], [p for p in out["points"] if p["limit"]]], default=str)
	accounts = {e.get("by") for h in hist.values() for v in h.values() for e in v} - {None, "Administrator"}
	check("B7 who changed a limit is said by role: no account reaches the output",
		not any(a in shown for a in accounts) and "@" not in shown and set(data["change_roles"]) >= accounts,
		f"{len(accounts)} account(s) kept inside the data; limit findings: {sum(1 for p in out['points'] if p['limit'])}")
	rr = ow._rows(out, data)
	check("B7 the table starts with the limits block and says the limit used under a finding that has one",
		rr[0]["found"].startswith("Approval limits in these dates") and sum(1 for r in rr if r.get("indent") == 1 and str(r.get("found") or "").startswith(
			("MD line used:", "Review line used:"))) == sum(1 for p in out["points"] if p["line_used"]),
		f"{sum(1 for p in out['points'] if p['line_used'])} finding(s) say the limit used")


def _inputs(check, ow):
	"""B5 (security re-check, 14 Nov): what a caller can put in or route through. Read-only."""
	try:
		ow.load({"company": '<img src=x onerror="alert(1)">'})
		check("B5 an HTML company name comes back escaped", False, "not refused")
	except frappe.ValidationError as e:
		check("B5 an HTML company name comes back escaped", "<img" not in str(e) and "&lt;img" in str(e), str(e)[:120])
	finally:
		frappe.clear_messages()
	kept = getattr(frappe.local, "job", None)
	frappe.local.job = frappe._dict(job_name="a scheduled Auto Email Report")
	try:
		ow.execute({})
		check("B5 Administrator inside a background job (Auto Email Report) is refused", False, "not refused")
	except frappe.PermissionError as e:
		check("B5 Administrator inside a background job (Auto Email Report) is refused", str(e) == REFUSAL, str(e)[:120])
	finally:
		frappe.clear_messages()
		frappe.local.job = kept
	cases = {'<b></b>&#61;HYPERLINK("x")': True, "+91 98": True, "-5": True, "@SUM(A1)": True, "\t=1": True,
		"\x01=1": True, "\ufeff=1": True,
		"Ab Bioscience": False, "Steel × 2; +3 more": False, 5: False}
	wrong = [v for v, inert in cases.items() if (ow._inert(v) != v) != inert]
	check("B5 export text cannot start a formula (=, +, -, @, also after HTML is stripped)", not wrong, f"wrong: {wrong}")


def _refused(check, ow, made):
	"""B1: CEO and System Manager without MD are refused, with the fixed sentence."""
	u = _user("nomd", ["CEO", "System Manager"], made)
	frappe.set_user(u)
	try:
		ow.execute({})
		check("B1 a user without MD (CEO + System Manager) is refused", False, "not refused")
	except frappe.PermissionError as e:
		check("B1 a user without MD (CEO + System Manager) is refused", str(e) == REFUSAL, str(e)[:200])
	finally:
		frappe.clear_messages()
		frappe.set_user("Administrator")


def _md_only(check, made):
	"""B2: an MD-only user runs it through the desk path and gets rows, though it cannot read receipts etc."""
	from frappe.desk.query_report import run as run_report

	u = _user("md", ["MD"], made)
	cannot = [dt for dt in UNREADABLE if not frappe.has_permission(dt, "read", user=u)]
	frappe.set_user(u)
	try:
		res = run_report("TS Order Watch", filters={"from_date": add_days(today(), -90), "to_date": today()})
		rows = [r for r in res.get("result") or [] if isinstance(r, dict)]
		links = {c.get("options") for c in res.get("columns") or [] if c.get("fieldtype") == "Link"}
		cells = [v for r in rows for v in r.values() if isinstance(v, str)]
		check("B2 an MD-only user gets rows through query_report.run", bool(rows),
			f"{len(rows)} rows; this user cannot read: {', '.join(cannot) or 'none of them'}")
		check("B2 Link columns only to Purchase Order", links <= {"Purchase Order"}, str(links))
		check("B2 no account id in any cell", not any("@" in v for v in cells), "")
		money = [c["fieldname"] for c in res.get("columns") or [] if c.get("fieldtype") == "Currency"]
		gaps = sum(1 for r in rows for f in money if f not in r)  # a missing key shows as "₹ 0.00" in the desk
		check("B2 every row carries every money column (blank, not ₹ 0.00)", bool(money) and not gaps,
			f"{money}; rows missing one: {gaps}")
		frappe.local.job = frappe._dict(job_name="Export in Background")  # its file would be readable by others
		try:
			importlib.import_module(MOD).execute({})
			check("B2 the MD too is refused inside a background job", False, "not refused")
		except frappe.PermissionError as e:
			check("B2 the MD too is refused inside a background job", str(e) == REFUSAL, str(e)[:120])
		finally:
			frappe.local.job = None
			frappe.clear_messages()
	except Exception as e:  # noqa: BLE001 — the scenario reports any failure
		check("B2 an MD-only user gets rows through query_report.run", False, f"{type(e).__name__}: {str(e)[:200]}")
	finally:
		frappe.clear_messages()
		frappe.set_user("Administrator")


PAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "page", "order_watch")
PAGE_KEYS = {"from_date", "to_date", "company", "companies", "limits", "gaps", "last_line", "money", "findings", "ranked"}
FINDING_KEYS = {"level", "kind", "kinds", "review_line", "summary", "supplier", "company", "line", "total", "over_by", "facts",
	"orders", "line_used", "limit"}
ORDER_KEYS = {"name", "supplier", "entered_at", "po_date", "value", "items", "request", "entered_by", "approved_by",
	"approved_at", "status", "received", "line", "routed_at"}


def _page(check, ow, made):
	"""B6: the Order Watch page. Its data method gives the report's findings, to the MD only; the page's files
	are complete and its only role is MD."""
	import re

	span = {"from_date": add_days(today(), -90), "to_date": today()}
	check("B6 the page's data method can be called from the desk", ow.get_page_data in frappe.whitelisted, "")
	for tag, job in (("nomd", None), ("job", "Export in Background")):
		u = _user("pg" + tag, ["CEO", "System Manager"] if tag == "nomd" else ["MD"], made)
		frappe.set_user(u)
		frappe.local.job = frappe._dict(job_name=job) if job else None
		name = "B6 page data: a user without MD is refused" if tag == "nomd" else "B6 page data: refused inside a background job"
		try:
			ow.get_page_data(**span)
			check(name, False, "not refused")
		except frappe.PermissionError as e:
			check(name, str(e) == REFUSAL, str(e)[:120])
		finally:
			frappe.local.job = None
			frappe.clear_messages()
			frappe.set_user("Administrator")
	u = _user("pgmd", ["MD"], made)
	frappe.set_user(u)
	try:
		page = ow.get_page_data(**span)
		rows = ow.execute(span)[1]
		found = page["findings"]
		points = [r for r in rows if r.get("indent") == 0 and r.get("level") and r.get("checks") != "R32"]
		check("B6 page data: one finding per point of the report", set(page) == PAGE_KEYS and len(found) == len(points),
			f"page {len(found)}, report {len(points)}; keys {sorted(set(page) ^ PAGE_KEYS)}")
		bad = [f for f in found if set(f) != FINDING_KEYS or f["kind"] not in set(ow.PAGE_KINDS.values())
			or f["level"] not in ("Serious", "To check", "Note") or any(set(o) != ORDER_KEYS for o in f["orders"])]
		check("B6 page data: every finding and order has the agreed fields, a known kind and level", not bad, f"{len(bad)} wrong")
		known = set(ow.PAGE_KINDS.values())
		odd = [f for f in found if not f["kinds"] or f["kinds"][0] != f["kind"] or set(f["kinds"]) - known
			or len(set(f["kinds"])) != len(f["kinds"])]
		two = ow._finding({"checks": ["R35", "R39"], "level": "Serious", "orders": []}, {}, {}, {})
		check("B6 page data: a finding with two reasons carries both kinds, so neither drops out of a count",
			not odd and two["kind"] == "near" and two["kinds"] == ["near", "self"], f"{len(odd)} wrong; merged {two['kinds']}")
		names = set(frappe.get_all("Company", pluck="name"))
		check("B6 page data: the company pick list is every company, and each standing-order row says what it is",
			set(page["companies"]) == names and all(r.get("kind") in ("supplier", "item", "request") for r in page["ranked"]),
			f"{len(page['companies'])} of {len(names)} companies")
		texts = [t for f in found for t in [f["summary"], *f["facts"]]]
		coded = [t for t in texts if re.search(r"\bR\d\d\b", t or "")]
		check("B6 page data: no check code in any sentence, and the closing line is not repeated in the facts",
			not coded and not any(t == page["last_line"] for f in found for t in f["facts"]), f"{coded[:2]}")
		check("B6 page data: the limits rows and each limit finding come through as the rules give them",
			page["limits"] == ow.rules.find_points(ow.load(span))["limits"] and isinstance(page["gaps"], list)
			and all((f["kind"] == "limit") == bool(f["limit"]) and (not f["limit"] or (f["line"] is None and f["over_by"] is None
				and set(f["limit"]) == LIMIT_KEYS)) for f in found), f"{sum(1 for f in found if f['limit'])} limit finding(s)")
		check("B6 page data: the same orders as the report, no account id anywhere",
			{o["name"] for f in found for o in f["orders"]} == {r["po"] for r in rows if r.get("po")}
			and "@" not in json.dumps(page, default=str), "")
		worth = {o["name"]: o["value"] for f in found if f["level"] in ("Serious", "To check") for o in f["orders"]}
		check("B6 page data: money in question counts each order once", abs(flt(page["money"]["both"]) - sum(worth.values())) < 1,
			f"{page['money']['both']} against {sum(worth.values())}")
		try:
			ow.get_page_data(company='<img src=x onerror="alert(1)">', **span)
			check("B6 page data: an HTML company name comes back escaped", False, "not refused")
		except frappe.ValidationError as e:
			check("B6 page data: an HTML company name comes back escaped", "<img" not in str(e), str(e)[:120])
	except Exception as e:  # noqa: BLE001 — the scenario reports any failure
		check("B6 page data: one finding per point of the report", False, f"{type(e).__name__}: {str(e)[:200]}")
	finally:
		frappe.clear_messages()
		frappe.set_user("Administrator")
	with open(os.path.join(PAGE_DIR, "order_watch.json"), encoding="utf-8") as f:
		roles = [r["role"] for r in json.load(f)["roles"]]
	with open(os.path.join(PAGE_DIR, "order_watch.js"), encoding="utf-8") as f:
		js = f.read()
	parts = re.findall(r'\{% include "trustbit_ethanol/ts_gate_entry/page/order_watch/([\w.]+)" %\}', js)
	check("B6 the page is for the MD role only, and each of its 3 view files is there", roles == ["MD"] and len(parts) == 3
		and all(os.path.exists(os.path.join(PAGE_DIR, p)) for p in parts), f"roles {roles}; views {parts}")


def _confidential_po():
	if not frappe.db.has_column("Purchase Order", "ts_confidential"):
		return None
	rows = frappe.db.sql("""SELECT name, company, creation FROM `tabPurchase Order`
		WHERE ts_confidential = 1 AND docstatus = 1 ORDER BY creation DESC LIMIT 1""", as_dict=True)
	return rows[0] if rows else None


def _confidential(check, ow, made):
	"""B4: a confidential order is absent for a user outside the allow-list, loader and rules both."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential

	name = "B4 a confidential order is absent for a user outside the allow-list"
	po = _confidential_po()
	if not po:
		check(name, True, "skipped: no submitted confidential order on this site")
		return
	f = {"from_date": po.creation.date(), "to_date": po.creation.date(), "company": po.company}
	shown = po.name in ow.load(f)["orders"]  # positive control, as Administrator
	u = _user("outside", ["Purchase User"], made)
	frappe.set_user(u)
	try:
		data = ow.load(f)
		leaks = [k for k in ("orders", "logs", "receipts", "gate_first") if po.name in data[k]]
		leaks += ["items"] if any(i["po"] == po.name for i in data["items"]) else []
		out = importlib.import_module(RULES_MOD).find_points(data)
		leaks += ["points"] if any(po.name in str(p) for p in out["points"] + out["ranked"]) else []
		ok = shown and not leaks and not user_sees_confidential("Purchase Order", u)
		check(name, ok, f"Administrator sees it: {shown}; found for the outside user in: {leaks or 'nothing'}")
	except Exception as e:  # noqa: BLE001
		check(name, False, f"{type(e).__name__}: {str(e)[:200]}")
	finally:
		frappe.clear_messages()
		frappe.set_user("Administrator")


def run():
	part_a()
	n_a = len(RESULTS)
	if os.environ.get("OW_PART_A_ONLY") == "1" or frappe is None:
		print("part B: not run (Part A only)")
	else:
		try:
			frappe.init(site=SITE)
			frappe.connect()
		except Exception as e:  # noqa: BLE001 — no site here: Part A only
			print(f"part B: not run, no site ({type(e).__name__})")
			frappe.destroy()
		else:
			try:
				frappe.set_user("Administrator")
				part_b(record, writes=os.environ.get("OW_READ_ONLY") != "1")
			except Exception as e:  # noqa: BLE001 — e.g. the report is not installed on this site yet
				record("part B ran to its end", False, f"{type(e).__name__}: {str(e)[:200]}")
			finally:
				frappe.db.rollback()
				frappe.destroy()
	failed = [r for r in RESULTS if not r[1]]
	print(f"  part A: {sum(1 for r in RESULTS[:n_a] if r[1])}/{n_a}")
	if len(RESULTS) > n_a:
		print(f"  part B: {sum(1 for r in RESULTS[n_a:] if r[1])}/{len(RESULTS) - n_a}")
	print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
	sys.exit(1 if failed else 0)


if __name__ == "__main__":
	run()
