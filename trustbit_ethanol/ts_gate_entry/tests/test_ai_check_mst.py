"""AI Checker, MST report tests: reading the monthly cost workbook, the checks of its own arithmetic, who
may send it, the job, the report and the bells. Demo only; every write is rolled back.

Every workbook here is invented and built in memory. Each rule gets a case that must fire and a
look-alike that must stay quiet. The job part reuses the stand-ins of `test_ai_check_step1.py` (no queue,
no real commit, no AI).

Usage (same shape as test_regression.py):
    cd /home/frappe/frappe-bench/sites && sudo -u frappe ../env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_ai_check_mst.py
Exit code: 0 = all passed, 1 = failures.
"""

import os
import sys

import frappe

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ai_check_step1 as s1  # noqa: E402 — the shared harness

check, eq, RESULTS = s1.check, s1.eq, s1.RESULTS
REPORT = s1.REPORT
OK, CHECK, SERIOUS = "No issue found", "To check", "Serious"
MST = "MST Report"
REAL = frappe._dict()
ai = mst = reader = issues = None
NOT_MST = ("No MST sheet was found in the file. A sheet needs its column titles: Item Name and Grand Total, "
	"or Cost Center, Amount and Per Ltr on the summary.")
EXCEL_ONLY = "An MST report is checked from its Excel file. Attach the Excel file."
ACCOUNTS_ONLY = "A payment list is sent by Accounts. Choose 'MST Report' as the check type."
COAL_HEAD = ["Item Name", "Stock UOM", "Out Qty", "Unit Rate", "Grand Total", "Cost Center", "Define use & Location", "Item Remark"]
STORE_HEAD = ["Item Name", "Stock UOM", "Out Qty", "Grand Total", "Cost Center", "Define use & Location", "Item Remark"]
HEADS = ((1, "Coal", 3000, 4000), (2, "Stores", 1500, 2000), (3, "Electricity Bill", 2500, 2500))
OUTSIDE = ((1, "Depreciation", 900, 900),)  # under the total, numbered afresh: it points at no sheet


def erp_as_file(company, day, change=None):
	"""The ERP's issues exactly as the default workbook has them, in the month asked for: a file with no mistake
	stays one. `change` types over one issue: {document: {field: value}}."""
	day = frappe.utils.getdate(day)
	rows = [("SE-T1", "Coal A", "MT", 10, 2000, "Boiler - T"), ("SE-T2", "Coal B", "MT", 5, 1000, "Boiler - T"),
		("SE-T3", "Bolt", "Nos", 10, 500, "Stores - T"), ("SE-T4", "Nut", "Nos", 20, 400, "Stores - T"), ("SE-T5", "Belt", "Nos", 1, 600, "Stores - T")]
	rows = [{**dict(zip(("doc", "name", "uom", "qty", "amount", "cc"), row)), "day": day.strftime("%Y-%m-15")} for row in rows]
	for row in rows:
		row.update((change or {}).get(row["doc"], {}))
	return {"month": day.strftime("%Y-%m"), "label": frappe.utils.formatdate(day, "MMMM yyyy"), "company": company, "abbrs": ["T"],
		"problem": "", "rows": rows}


def calm():
	s1.calm()
	if ai:
		ai.review = REAL.review
		issues.fetch = erp_as_file  # the demo's own stock entries are no part of these tests, but for the one that reads them


def test(name, fn):
	try:
		fn()
		RESULTS.append((name, True, ""))
		print(f"  PASS  {name}")
	except Exception as e:  # noqa: BLE001 — a test harness reports every failure
		RESULTS.append((name, False, str(e)[:300]))
		print(f"  FAIL  {name} :: {type(e).__name__} {str(e)[:300]}")
	finally:
		calm()


# ---- invented workbooks


def master(heads=HEADS, litres=100000, total=None, outside=OUTSIDE, fix=None):
	"""The summary sheet. Every figure follows from the amounts unless `fix` says otherwise: {head: {column: value}}."""
	def one(sr, name, amount, budget):
		row = {"sr": sr, "amount": amount, "per": amount / litres if litres else None,
			"budget_litre": budget / litres if litres else None, "budget": budget, "surplus": budget - amount}
		row.update((fix or {}).get(name, {}))
		return [row["sr"], name, row["amount"], row["per"], None, row["budget_litre"], row["budget"], row["surplus"]]

	inside = sum(head[2] for head in heads) if total is None else total
	return [([None] * 6 + ["Total Production", litres]) if litres else ["Monthly cost"], [],
		["Sr.No.", "Cost Center", "Amount", "Per Ltr", None, "Budget per Litre", "Total for month", "Surplus/Deficit"],
		*[one(*head) for head in heads], ["Total Amount", None, inside, inside / litres if litres else None], [],
		*[one(*head) for head in outside]]


def coal(lines=None, total=None):
	lines = lines or [["Coal A", "MT", 10, 200, 2000, "Boiler - T", "Boiler", "day"], ["Coal B", "MT", 5, 200, 1000, "Boiler - T", "Boiler", "night"]]
	return [COAL_HEAD, *lines, ["TOTAL", None, None, None, 3000 if total is None else total]]


def stores(lines=None, total=None):
	lines = lines or [["Bolt", "Nos", 10, 500, "Stores - T", "Shed", "a"], ["Nut", "Nos", 20, 400, "Stores - T", "Shed", "b"],
		["Belt", "Nos", 1, 600, "Stores - T", "Shed", "c"]]
	return [STORE_HEAD, *lines, ["TOTAL", None, None, 1500 if total is None else total]]


GROUP9 = ["Hours", "Days", "RATE", "AMT", "DIESEL P/H", "TOTAL DIESEL", "D RATE", "AMOUNT", "TOTAL AMOUNT"]
MACHINE_HEADS = HEADS[:2] + ((3, "Machinery For Fuel", 4800, 5000), (4, "Machinery Other", 50, 100))


def machinery(fix=None, pad=0, total=True, name="MACHINE NAME", subs=None, edit=None):
	"""A machine table: two machines; a group with hire and diesel, a group with one amount, a second hire group,
	`pad` groups nobody used, then each row's hours and amount. Under it, a figure worked out from the table.
	`fix` types over one cell: {(machine or "TOTAL", column index): value}; `subs` are other titles for the nine
	columns; `edit` changes the finished rows (0 title, 1 groups, 2 column titles, 3 Loader, 4 Dozer, 5 TOTAL, 6 blank, 7 net)."""
	def nine(hours=None, days=None, rate=None, per=None, diesel=None, d_rate=None):
		amt = (hours if hours is not None else days) * rate if rate else None
		amount = diesel * d_rate if diesel else None
		return [hours, days, rate, amt, per, diesel, d_rate, amount, (amt or 0) + (amount or 0) if rate else None]

	wide = [None] * 9 * pad
	rows = {"Loader": [1, "Loader", "MP 01", *nine(10, None, 100, 2, 20, 90), 5, 200, 1000, *[None] * 9, *wide, 15, 3800],
		"Dozer": [2, "Dozer", "MP 02", *nine(None, 2, 500), None, None, None, *nine(4, None, 100, None, 5, 90), *wide, 4, 1850]}
	if total:
		rows["TOTAL"] = [None, "TOTAL", None, *[None] * 8, 3800, None, None, 1000, *[None] * 8, 850, *wide, 19, 5650]
	for (row, col), value in (fix or {}).items():
		rows[row][col] = value
	groups = ["S.NO", name, "V NAME", "Coal", *[None] * 8, "Plant (fixed)", None, None, "Farm", *[None] * 8]
	for nth in range(pad):
		groups += [f"Unit {nth + 1}", *[None] * 8]
	nine_titles = subs or GROUP9
	out = [["MACHINERY, HIRE AND DIESEL"], [*groups, "TOTAL (H)", "TOTAL AMOUNT"],
		[None, None, None, *nine_titles, "HOURS/DIESEL", "RATE", "AMOUNT", *nine_titles, *nine_titles * pad], *rows.values(), [],
		[None, "Machinery net of the farm", *[None] * 7, 4850]]
	if edit:
		edit(out)
	return out


def machine_book(rows=None, heads=MACHINE_HEADS):
	return book(master(heads), extra=[("3. Machinery", rows or machinery(), False)])


def book(master_rows=None, coal_rows=None, store_rows=None, extra=()):
	return s1.xlsx(sheets=[("Master Sheet", master_rows or master(), False), ("1. COAL", coal_rows or coal(), False),
		("2. Stores", store_rows or stores(), False), *extra])


def read(data, kind="xlsx"):
	# the job gets the book from the reader's own process as JSON: every test reads it the same way
	import json

	return mst.report(json.loads(json.dumps(mst.read_book(data, kind))), kind)


def found(data):
	"""The points of a workbook as (rule, level), sorted."""
	return sorted((point["rule"], point["level"]) for point in read(data)[1].findings)


def point(data, rule):
	return next(p for p in read(data)[1].findings if p["rule"] == rule)


# ---- the reading and the arithmetic (no site data)


def _read():
	def t_clean():
		result, checked = read(book())
		eq(checked.findings, [], "points on a workbook with no mistake")
		eq([(line["section_text"], line["supplier_text"], line["amount"]) for line in result.lines], [
			("1. COAL", "Coal A", 2000.0), ("1. COAL", "Coal B", 1000.0), ("2. Stores", "Bolt", 500.0), ("2. Stores", "Nut", 400.0),
			("2. Stores", "Belt", 600.0), ("Master Sheet", "Electricity Bill", 2500.0), ("Master Sheet", "Depreciation", 900.0)])
		eq(result.lines[0]["details_text"], "10 MT · rate 200 · Boiler - T · Boiler")
		eq((result.lines[0]["remarks_text"], result.lines[0]["source_row"]), ("day", 2))
		eq([line["read_note"] for line in result.lines[5:]], ["Taken from the summary: no sheet of this head was read."] * 2,
			"a head with no sheet of its own is a line from the summary")
		eq((result.list_total, result.read_by, result.file_kind, checked.n_ok, checked.n_check, checked.n_serious, checked.amt_ok),
			(7900.0, "Code", "Excel", 7, 0, 0, 7900.0))
		eq({extra["verdict"] for extra in checked.lines}, {OK})
		check(checked.summary.startswith("7 lines on 2 sheet(s), ₹7,900"), checked.summary)
		check("Summary sheet 'Master Sheet': 4 heads, production 100,000.00 litres." in checked.coverage, checked.coverage)
		check("The lines are not compared with the issues recorded in the ERP." in checked.coverage, "the report says what was not done")
	test("mst: a workbook with no mistake is read line by line and gives no point", t_clean)

	def t_total():
		wrong = book(master(HEADS[:1] + ((2, "Stores", 1600, 2000),) + HEADS[2:]), store_rows=stores(total=1600))
		eq(found(wrong), [("F1", SERIOUS)], "a sheet's total that its lines do not add up to")
		p = point(wrong, "F1")
		eq((p["line_no"], p["detail"]), (0, "2. Stores: Row 5 (TOTAL) says ₹1,600; the lines above it add up to ₹1,500. Difference ₹100."))
		eq(found(book(store_rows=stores(total=1500.4))), [], "forty paise is not a difference")
	test("mst: F1 a total that does not match its lines", t_total)

	def t_summary_vs_sheet():
		wrong = book(master(((1, "Coal", 3500, 4000),) + HEADS[1:]))
		eq(found(wrong), [("F2", SERIOUS)])
		check("row 4 (Coal) shows ₹3,500; the total of sheet '1. COAL' come to ₹3,000" in point(wrong, "F2")["detail"], point(wrong, "F2")["detail"])
		no_total = book(master(((1, "Coal", 3500, 4000),) + HEADS[1:]), coal_rows=coal()[:-1])
		eq(found(no_total), [("F2", SERIOUS)], "a sheet with no total row is compared by its lines")
		check("the lines of sheet" in point(no_total, "F2")["detail"])
		eq(found(book(master(outside=((1, "Depreciation", 77, 77),)))), [], "a row under the total points at no sheet")
	test("mst: F2 the summary against each head's sheet", t_summary_vs_sheet)

	def t_summary_total():
		eq(found(book(master(total=7100))), [("F3", SERIOUS)])
		eq(found(book(master(total=7000.3))), [])
	test("mst: F3 the summary total against its heads", t_summary_total)

	def t_per_litre():
		wrong = book(master(fix={"Coal": {"per": 0.031}}))
		eq(found(wrong), [("F4", CHECK)])
		check("₹3,000 ÷ 100,000.00 L = 0.0300, the sheet says 0.0310" in point(wrong, "F4")["detail"], point(wrong, "F4")["detail"])
		eq(found(book(master(fix={"Coal": {"per": 0.03001}}))), [], "a rounded per-litre figure")
		none = book(master(litres=None))
		eq(found(none), [("F4", CHECK)], "no litres on the summary: one point, not one per head")
		eq(point(none, "F4")["title"], "Production litres were not found")
		on_line = book(master(fix={"Electricity Bill": {"per": 0.5}}))
		eq((point(on_line, "F4")["line_no"], read(on_line)[1].lines[5]["verdict"]), (6, CHECK), "on a head that is a line, the point is the line's")
	test("mst: F4 per-litre figures against the litres", t_per_litre)

	def t_qty_rate():
		lines = [["Coal A", "MT", 10, 200, 2100, "Boiler - T", "Boiler", "day"], ["Coal B", "MT", 5, 200, 1000, "Boiler - T", "Boiler", "night"]]
		wrong = book(master(((1, "Coal", 3100, 4000),) + HEADS[1:]), coal_rows=coal(lines, 3100))
		eq(found(wrong), [("F5", CHECK)])
		eq((point(wrong, "F5")["line_no"], read(wrong)[1].lines[0]["verdict"], read(wrong)[1].n_check), (1, CHECK, 1))
		lines[0][4] = 2000.9
		eq(found(book(coal_rows=coal(lines, 3000.9), master_rows=master(((1, "Coal", 3000.9, 4000),) + HEADS[1:]))), [], "within a rupee")
	test("mst: F5 amount against quantity × rate", t_qty_rate)

	def t_no_value():
		lines = [["Bolt", "Nos", 10, "#REF!", "Stores - T", "Shed", "a"], ["Nut", "Nos", 20, 400, "Stores - T", "Shed", "b"],
			["Belt", "Nos", 1, 600, "Stores - T", "Shed", "c"]]
		broken = book(master(((1, "Coal", 3000, 4000), (2, "Stores", 1000, 2000), HEADS[2])), store_rows=stores(lines, 1000))
		eq(found(broken), [("F6", CHECK)])
		eq((read(broken)[0].lines[2]["unclear"], read(broken)[0].lines[2]["amount"]), ("amount", 0.0))
		eq(found(book(coal_rows=[*coal()[:-1], ["TOTAL", None, None, None, None]])), [("F6", CHECK)], "a total that was never calculated")
	test("mst: F6 a line or a total with no value", t_no_value)

	def t_other_cc():
		lines = stores()[1:-1]
		lines[2] = ["Belt", "Nos", 1, 600, "Boiler - T", "Shed", "c"]
		eq(found(book(store_rows=stores(lines))), [("F7", CHECK)])
		check("'Boiler - T'" in point(book(store_rows=stores(lines)), "F7")["detail"])
		two = [["Coal A", "MT", 10, 200, 2000, "Boiler - T", "Boiler", "day"], ["Coal B", "MT", 5, 200, 1000, "Stores - T", "Boiler", "night"]]
		eq(found(book(coal_rows=coal(two))), [], "two lines do not make a usual cost centre")
	test("mst: F7 a line under another cost centre than the rest of its sheet", t_other_cc)

	def t_twice():
		lines = [*stores()[1:-1], ["Belt", "Nos", 1, 600, "Stores - T", "Shed", "c"]]
		twice = book(master(((1, "Coal", 3000, 4000), (2, "Stores", 2100, 2000), HEADS[2])), store_rows=stores(lines, 2100))
		eq(found(twice), [("F8", CHECK)])
		check("row 5 repeats row 4" in point(twice, "F8")["detail"], point(twice, "F8")["detail"])
		lines[3] = ["Belt", "Nos", 1, 600, "Stores - T", "Shed", "second issue"]
		eq(found(book(master(((1, "Coal", 3000, 4000), (2, "Stores", 2100, 2000), HEADS[2])), store_rows=stores(lines, 2100))), [],
			"the same item with another remark is another issue")
	test("mst: F8 the same line twice on one sheet", t_twice)

	def t_surplus():
		eq(found(book(master(fix={"Stores": {"surplus": 900}}))), [("F9", CHECK)])
		eq(found(book(master(fix={"Stores": {"surplus": 500.2}}))), [])
	test("mst: F9 surplus against budget less amount", t_surplus)

	def t_sections():
		one = s1.xlsx(sheets=[("2. Stores", stores(), False)])
		result, checked = read(one)
		eq((len(result.lines), checked.findings, result.list_total), (3, [], 1500.0), "one sheet sent on its own")
		check("No summary sheet in this file" in checked.coverage, checked.coverage)
		eq(found(s1.xlsx(sheets=[("Anything", stores(total=1400), False)])), [("F1", SERIOUS)], "found by its titles, not by the sheet's name")
		only = s1.xlsx(sheets=[("Master Sheet", master(), False)])
		result, checked = read(only)
		eq(([line["supplier_text"] for line in result.lines], checked.findings),
			(["Coal", "Stores", "Electricity Bill", "Depreciation"], []), "the summary sent on its own: its heads are the lines")
		eq(found(s1.xlsx(sheets=[("Master Sheet", master(total=7100), False)])), [("F3", SERIOUS)])
		hidden = s1.xlsx(sheets=[("2. Stores", stores(), False), ("old", stores(total=1), True)])
		eq((len(read(hidden)[0].lines), found(hidden)), (3, []), "a hidden sheet is not read")
	test("mst: one sheet, the summary alone, a hidden sheet", t_sections)

	def t_stacks():
		blocks = [STORE_HEAD, ["Acid", "Kg", 5, 100, "Process - T"], ["TOTAL", None, None, 100], [],
			STORE_HEAD, ["Enzyme", "Kg", 2, 300, "Process - T"], ["TOTAL", None, None, 300], ["GRAND TOTAL", None, None, 400]]
		stacked = s1.xlsx(sheets=[("3. Process", blocks, False)])
		eq((len(read(stacked)[0].lines), found(stacked)), (2, []), "a grand total adds the totals above it")
		blocks[-1] = ["GRAND TOTAL", None, None, 450]
		eq(found(s1.xlsx(sheets=[("3. Process", blocks, False)])), [("F1", SERIOUS)])
		beside = [[*STORE_HEAD, None, "Party Name", "Amount"], ["Bolt", "Nos", 10, 500, "Stores - T", "Shed", "a", None, "Carrier one", 70],
			["Nut", "Nos", 20, 400, "Stores - T", "Shed", "b", None, "Carrier two", 30], ["TOTAL", None, None, 900, None, None, None, None, "TOTAL", 150]]
		side = s1.xlsx(sheets=[("2. Stores", beside, False)])
		result, checked = read(side)
		eq([line["section_text"] for line in result.lines], ["2. Stores", "2. Stores", "2. Stores (side table)", "2. Stores (side table)"])
		eq(found(side), [("F1", CHECK)], "a wrong total on a side table is a point To check, not Serious")
		eq((result.list_total, checked.amt_ok, len(result.lines)), (900.0, 900.0, 4), "the side table's lines are listed, and stay out of the total")
		check("2 of the lines stand in a table beside or under the main one (₹100): they are checked, and not counted in the total." in checked.summary, checked.summary)
	test("mst: tables stacked under each other and a table beside the main one", t_stacks)

	def t_not_mst():
		s1.unreadable(lambda: mst.read_book(s1.xlsx(s1.listing()), "xlsx"), NOT_MST)
		s1.unreadable(lambda: mst.read_book(s1.xlsx(sheets=[("Master Sheet", master(heads=(), outside=()), False)]), "xlsx"), NOT_MST)
		s1.unreadable(lambda: reader.prepare(s1.typed_pdf(), ai.limits(), mst=True), EXCEL_ONLY)
		s1.unreadable(lambda: reader.prepare(s1.xlsx(s1.listing()), ai.limits(), mst=True), NOT_MST)
	test("mst: a file that is no MST workbook is refused in plain words", t_not_mst)

	def t_own_process():
		data = book(store_rows=stores(total=1400), master_rows=master(((1, "Coal", 3000, 4000), (2, "Stores", 1400, 2000), HEADS[2])))
		at_submit, in_job = reader.prepare(data, ai.limits(), mst=True), reader.prepare(data, ai.limits(), full=True, mst=True)
		eq((at_submit.kind, at_submit.book), ("xlsx", None), "Submit only asks whether the file can be read")
		eq(in_job.book, mst.read_book(data, "xlsx"), "the limited process hands back what the reader read")
		eq(sorted((p["rule"], p["level"]) for p in mst.report(in_job.book, in_job.kind)[1].findings), [("F1", SERIOUS)])
		machines = reader.prepare(machine_book(machinery({("Loader", 5): 110})), ai.limits(), full=True, mst=True)
		result, checked = mst.report(machines.book, machines.kind)
		eq(([(p["rule"], p["level"]) for p in checked.findings], result.list_total, len(result.lines)), ([("F5", CHECK)], 10250.0, 12),
			"a machine table comes back from the limited process as it was read")
		eq(reader.prepare(s1.xlsx(s1.listing()), ai.limits(), full=True).table.list_total,
			reader.read_table(s1.xlsx(s1.listing()), "xlsx").list_total, "a payment list still comes back as its table")
	test("mst: the workbook is read in the reader's own limited process", t_own_process)

	def t_file_text():
		lines = [["<script>alert(1)</script> Bolt", "Nos", 10, 500, "Stores - T", "Shed", "=1+1"], ["Nut", "Nos", 20, 400, "Stores - T", "Shed", "b"],
			["Belt", "Nos", 1, 600, "<b>CC</b>", "Shed", "c"]]
		data = s1.xlsx(sheets=[("2. <Stores>", stores(lines), False)])
		result, checked = read(data)
		text = frappe.as_json([result.lines, checked.findings, checked.coverage, checked.summary])
		check("<" not in text and ">" not in text, "no angle bracket from the file reaches the report")
		eq(result.lines[0]["supplier_text"], "‹script›alert(1)‹/script› Bolt")
	test("mst: text from the file is stored as text", t_file_text)

	def t_caps():
		line = lambda i: ["Item %d" % i, "Nos", 1, 10, "Stores - T", "Shed", "r%d" % i]  # noqa: E731
		long = s1.xlsx(sheets=[("Master Sheet", master(((1, "Stores", 7000, 7000),), outside=()), False),
			("1. Stores", [STORE_HEAD, *[line(i) for i in range(700)], ["TOTAL", None, None, 7000]], False)])
		result, checked = read(long)
		eq((len(result.lines), checked.findings), (700, []), "a table of 700 lines is read whole, with its total")
		sheets = [(f"{n}. Sheet", [STORE_HEAD, *[line(i) for i in range(599)], ["TOTAL", None, None, 5990]], False) for n in range(1, 5)]
		s1.unreadable(lambda: mst.read_book(s1.xlsx(sheets=sheets), "xlsx"), "The workbook has more than 2,000 lines. Send it in parts, a few sheets at a time.")
		tall = [STORE_HEAD, *[line(i) for i in range(1999)], ["TOTAL", None, None, 19990], ["note"]]
		s1.unreadable(lambda: mst.read_book(s1.xlsx(sheets=[("1. Stores", tall, False)]), "xlsx"),
			"Sheet '1. Stores' has more than 2,000 filled rows. Remove what is not part of the report and attach again.")
		totals = [STORE_HEAD, line(1), *[["Total"] for _ in range(mst.MAX_TOTALS + 1)]]
		s1.unreadable(lambda: mst.read_book(s1.xlsx(sheets=[("1. Stores", totals, False)]), "xlsx"),
			"The file has more total rows than an MST report holds. Check the file and attach again.")
	test("mst: a long table is read whole; a file over a limit is refused, never read in part", t_caps)

	def t_beside():
		lower = [[*STORE_HEAD], ["Bolt", "Nos", 10, 500, "Stores - T", "Shed", "a"], ["Nut", "Nos", 20, 400, "Stores - T", "Shed", "b", None, "Party Name", "Amount"],
			["Belt", "Nos", 1, 600, "Stores - T", "Shed", "c", None, "Carrier one", 70], ["Pipe", "Nos", 2, 300, "Stores - T", "Shed", "d", None, "Carrier two", 30],
			["TOTAL", None, None, 1800, None, None, None, None, "TOTAL", 100]]
		data = book(master(((1, "Coal", 3000, 4000), (2, "Stores", 1800, 2000), HEADS[2])), store_rows=lower)
		result, checked = read(data)
		eq(([line["supplier_text"] for line in result.lines if line["section_text"] == "2. Stores"], checked.findings),
			(["Bolt", "Nut", "Belt", "Pipe"], []), "a side table that starts lower does not end the main table")
		longer = [[*STORE_HEAD[:5], None, "Party Name", "Amount"], ["Bolt", "Nos", 10, 500, "Stores - T", None, "Carrier one", 70],
			["Bolt", "Nos", 10, 500, "Stores - T", None, "Carrier two", 30], [None, None, None, None, None, None, "Carrier three", 50],
			[None, None, None, None, None, None, "TOTAL", 150], ["TOTAL", None, None, 1000]]
		result, checked = read(s1.xlsx(sheets=[("2. Stores", longer, False)]))
		eq(sorted((p["rule"], p["level"]) for p in checked.findings), [("F8", CHECK)], "the side table's total and names stay its own")
		eq([(line["section_text"], line["remarks_text"]) for line in result.lines][:2], [("2. Stores", ""), ("2. Stores", "")])
		ledger = [[*STORE_HEAD[:4], None, "Party Name", "Amount"], ["Acid", "Kg", 5, 100, None, "Carrier one", 10], ["TOTAL", None, None, 100], [],
			[*STORE_HEAD[:4]], ["Enzyme", "Kg", 2, 300, None, "Carrier two", 40], ["TOTAL", None, None, 300, None, "Carrier three", 50],
			["GRAND TOTAL", None, None, 400, None, "TOTAL", 90]]
		eq(found(s1.xlsx(sheets=[("3. Process", ledger, False)])), [("F1", CHECK)], "a ledger beside two stacked blocks is read to its own total")
	test("mst: tables beside each other keep their own rows, totals and words", t_beside)

	def t_total_words():
		for label in ("Total :", "TOTAL:", "Totals", "Sub Total", "Net Total", "Total Rs.", "Total (A)", "Total Amount (Rs.)", "G. Total"):
			result, checked = read(book(coal_rows=[*coal()[:-1], [label, None, None, None, 3000]]))
			eq((len(result.lines), checked.findings), (7, []), f"'{label}' is a total row")
		eq(len(read(book(store_rows=[STORE_HEAD, *stores()[1:3], ["Total station prism", "Nos", 1, 600, "Stores - T", "Shed", "c"], ["TOTAL", None, None, 1500]]))[0].lines), 7,
			"an item whose name starts with Total is a line")
	test("mst: a total row is known by its label, however it is written", t_total_words)

	def t_merged():
		wide = [["Item Name", None, "Stock UOM", "Out Qty", "Grand Total", "Cost Center"], ["Bearing", None, "Nos", 2, 500, "Stores - T"],
			["Belt", None, "Nos", 2, 500, "Stores - T"], ["Rope", None, "Kg", 5, 200, "Stores - T"], ["TOTAL", None, None, None, 1300]]
		result, checked = read(s1.xlsx(sheets=[("2. Stores", wide, False)]))
		eq(([line["supplier_text"] for line in result.lines], sorted((p["rule"], p["level"]) for p in checked.findings)),
			(["Bearing", "Belt", "Rope"], [("F1", SERIOUS)]), "a title merged over two columns")
		down = [STORE_HEAD, ["Bolt", "Nos", 10, 500, "Stores - T", "Shed", "a"], [None, "Nos", 4, 200, "Stores - T", "Mill", "b"],
			["Nut", "Nos", 20, 300, "Stores - T", "Shed", "c"], [None, None, None, 1000], ["TOTAL", None, None, 1000]]
		result, checked = read(s1.xlsx(sheets=[("2. Stores", down, False)]))
		eq(([(line["supplier_text"], line["amount"]) for line in result.lines], checked.findings),
			([("Bolt", 500.0), ("Bolt", 200.0), ("Nut", 300.0)], []), "a name merged down two rows; a bare sum row is no line")
	test("mst: merged title and name cells", t_merged)

	def t_summary_gaps():
		broken = s1.xlsx(sheets=[("Master Sheet", [*master(((1, "Coal", 3000, 4000),), outside=())[:3], [1, "Coal", 3000, 0.03], [2, "Stores", "#REF!", "#REF!"],
			["Utilities"], [3, "Electricity Bill", 2500, 0.025], ["Total Amount", None, "#REF!"]], False), ("1. COAL", coal(), False)])
		result, checked = read(broken)
		eq(sorted((p["rule"], p["level"], p["title"]) for p in checked.findings),
			[("F6", CHECK, "A head has no amount"), ("F6", CHECK, "The summary total has no value")])
		eq([(line["supplier_text"], line["unclear"], extra["verdict"]) for line, extra in zip(result.lines, checked.lines)][2:],
			[("Stores", "amount", CHECK), ("Electricity Bill", "", OK)], "the head is kept as a line with no amount; a title between heads is not a head")
		one = s1.xlsx(sheets=[("MST", [*master(((1, "Coal", 3000, 4000),), outside=()), [], *coal()], False)])
		result, checked = read(one)
		eq(([line["supplier_text"] for line in result.lines], checked.findings), (["Coal A", "Coal B", "Coal"], []),
			"a table under the summary on the same sheet is a table, not more heads")
	test("mst: a summary head or total with no value is reported; a table under the summary is read", t_summary_gaps)

	def t_stack_forms():
		tail = [STORE_HEAD, ["A", "Kg", 1, 100, "P - T"], ["TOTAL", None, None, 100], ["B", "Kg", 1, 200, "P - T"], ["GRAND TOTAL", None, None, 300]]
		eq(found(s1.xlsx(sheets=[("3. Process", tail, False)])), [], "lines after the last total count in the grand total")
		tail[-1] = ["GRAND TOTAL", None, None, 350]
		eq(found(s1.xlsx(sheets=[("3. Process", tail, False)])), [("F1", SERIOUS)])
		open_end = [STORE_HEAD, ["A", "Kg", 1, 100, "P - T"], ["TOTAL", None, None, 100], [], STORE_HEAD, ["B", "Kg", 1, 200, "P - T"]]
		eq(found(s1.xlsx(sheets=[("Master Sheet", master(((1, "Process", 300, 300),), outside=()), False), ("1. Process", open_end, False)])), [],
			"a second block with no total row: the summary may equal the lines")
		each = [STORE_HEAD, ["A", "Kg", 1, 100, "P - T"], ["Grand Total", None, None, 100], [], STORE_HEAD, ["B", "Kg", 1, 200, "P - T"], ["Grand Total", None, None, 200]]
		eq(found(s1.xlsx(sheets=[("Master Sheet", master(((1, "Process", 300, 300),), outside=()), False), ("1. Process", each, False)])), [],
			"each block closed by its own row called Grand Total")
	test("mst: other correct ways to total stacked blocks", t_stack_forms)

	def t_pairing():
		sub = s1.xlsx(sheets=[("Master Sheet", master(((5.1, "Boiler", 100, 100), (5.2, "Turbine", 200, 200)), outside=()), False),
			("5.1 Boiler", [STORE_HEAD, ["A", "Kg", 1, 100, "B - T"], ["TOTAL", None, None, 100]], False),
			("5.2 Turbine", [STORE_HEAD, ["B", "Kg", 1, 200, "T - T"], ["TOTAL", None, None, 200]], False)])
		eq((found(sub), read(sub)[0].list_total), ([], 300.0), "sub-numbered heads and sheets")
		for title in ("1-Stores", "1", "(1) Stores", "Stores"):
			named = s1.xlsx(sheets=[("Master Sheet", master(((1, "Stores", 1500, 2000),), outside=()), False), (title, stores(), False)])
			eq((found(named), read(named)[0].list_total), ([], 1500.0), f"sheet '{title}' is the head's own: no line is counted twice")
		on = s1.xlsx(sheets=[("Master Sheet", master(((1, "Coal", 3000, 4000),), outside=((2, "Stores", 1400, 2000),)), False),
			("1. COAL", coal(), False), ("2. Stores", stores(), False)])
		eq((found(on), read(on)[0].list_total), ([("F2", SERIOUS)], 4500.0), "rows under the total that go on counting point at their sheets")
		again = book(master(outside=((1, "Depreciation", 77, 77), (2, "Interest", 5, 5))))
		eq((found(again), len(read(again)[0].lines)), ([], 8), "rows under the total numbered afresh point at no sheet")
		twice = book(master(((1, "Coal", 3000, 4000), (2, "Stores", 1500, 2000), (1, "Salary", 2500, 2500))))
		eq(found(twice), [], "a serial number used twice above the total pairs nothing by number")
		lost = s1.xlsx(sheets=[("Master Sheet", master(((1, "Coal", 3000, 4000), (2, "Stores", 1500, 2000)), outside=()), False),
			("1. COAL", coal(), False), ("Sheet7", stores(), False)])
		result, checked = read(lost)
		eq(([(p["rule"], p["level"], p["title"]) for p in checked.findings], result.list_total),
			([("F2", CHECK, "Some heads and sheets could not be matched")], 4500.0), "an unmatched head and an unmatched sheet: said, not counted twice")
		check("No sheet was found for: Stores. No head points at: 'Sheet7'." in checked.findings[0]["detail"], checked.findings[0]["detail"])
	test("mst: which sheet a summary row points at", t_pairing)

	def t_figures():
		typed = s1.xlsx(sheets=[("Master Sheet", master(((1, "Coal", 4567890, 5000000), (2, "Chem", 234567, 250000)), litres=3123456, outside=(),
			fix={"Coal": {"per": 1.46}, "Chem": {"per": 0.08}}), False)])
		eq(found(typed), [], "per-litre figures typed to two decimals")
		eq(found(s1.xlsx(sheets=[("Master Sheet", master(((1, "Coal", 4567890, 5000000),), litres=3123456, outside=(), fix={"Coal": {"per": 1.47}}), False)])),
			[("F4", CHECK)])
		text = [COAL_HEAD, ["Dye", "Kg", "0.125", "4,000", "500.00", "P - T", "Lab", "x"], ["TOTAL", None, None, None, 500]]
		eq(found(s1.xlsx(sheets=[("1. Lab", text, False)])), [], "a quantity written as text keeps its decimals")
		petty = [["Sr. No.", "Particular", "Amount"], [1, "Tea", 200], [2, "Tea", 200], ["TOTAL", None, 400]]
		eq(found(s1.xlsx(sheets=[("14. Petty", petty, False)])), [], "typed expenses: the same particular and amount twice is not a point")
	test("mst: figures as people type them", t_figures)


	def t_other_total_labels():
		for label in ("Total Cost", "Total of Coal", "Gross Total", "Sheet Total", "Grand Total (A+B)", "Tot.", "T O T A L", "TOTAL COAL",
				"Total Amount in Rs", "Sum", "Totel"):
			result, checked = read(book(coal_rows=[*coal()[:-1], [label, None, None, None, 3000]]))
			eq((len(result.lines), result.list_total, checked.findings), (7, 7900.0, []), f"'{label}' is a total row, not a line")
		eq(found(book(coal_rows=[*coal()[:-1], ["Gross Total", None, None, None, 3400]])), [("F1", SERIOUS)], "and a wrong one is caught")
		qty = [*coal()[:-1], ["Total Qty", None, 15], ["TOTAL", None, None, None, 3000]]
		eq((len(read(book(coal_rows=qty))[0].lines), found(book(coal_rows=qty))), (7, []), "a total of quantities is no line and no point")
		for label in ("Total Cost", "Total Expenses", "TOTAL (1 to 3)"):
			rows = master()
			rows[6][0] = label
			result, checked = read(book(rows))
			eq((len(result.lines), result.list_total, checked.findings), (7, 7900.0, []), f"summary: '{label}' is the total row")
		under = master()
		under.append(["Total Production (Ltr)", None, 100000])
		eq((len(read(book(under))[0].lines), found(book(under))), (7, []), "the litres written under the summary are no head")
	test("mst: a total row under any usual label is not a line", t_other_total_labels)

	def t_closing_total():
		subs = [STORE_HEAD, ["Group A"], ["Acid", "Kg", 1, 100, "P - T"], ["Lime", "Kg", 1, 300, "P - T"], ["Sub Total", None, None, 400],
			["Group B"], ["Salt", "Kg", 1, 250, "P - T"], ["Soda", "Kg", 1, 350, "P - T"], ["Sub Total", None, None, 600], ["Total", None, None, 1000]]
		one = lambda rows, head=1000: s1.xlsx(sheets=[("Master Sheet", master(((1, "Process", head, head),), outside=()), False), ("1. Process", rows, False)])  # noqa: E731
		eq((found(one(subs)), read(one(subs))[0].list_total), ([], 1000.0), "sub totals closed by a plain Total")
		eq(found(one(subs, 2000)), [("F2", SERIOUS)], "the head is set against the closing total, not against every total row added up")
		check("the total of sheet '1. Process' come to ₹1,000" in point(one(subs, 2000), "F2")["detail"])
		subs[-1] = ["Total", None, None, 1100]
		eq(found(one(subs, 1100)), [("F1", SERIOUS)])
		check("Row 10 (Total) says ₹1,100; the totals above it add up to ₹1,000" in point(one(subs, 1100), "F1")["detail"])
		twice = [STORE_HEAD, ["Acid", "Kg", 1, 400, "P - T"], ["TOTAL", None, None, 400], ["TOTAL AMOUNT", None, None, 400]]
		eq(found(one(twice, 400)), [], "the same total written again under it")
	test("mst: a plain Total under the block totals adds them up", t_closing_total)

	def t_summary_layouts():
		closed = [*master(outside=((4, "Depreciation", 900, 900),)), ["Grand Total", None, 7900]]
		eq((found(book(closed)), read(book(closed))[0].list_total), ([], 7900.0), "a last total row under the rows below the total")
		closed[-1] = ["Grand Total", None, 8100]
		eq(found(book(closed)), [("F3", SERIOUS)])
		check("Row 10 says ₹8,100; the heads above it add up to ₹7,900" in point(book(closed), "F3")["detail"], point(book(closed), "F3")["detail"])
		first = [*master(total=7200, outside=((4, "Depreciation", 900, 900),)), ["Grand Total", None, 7900]]
		check("Row 7 says ₹7,200; the heads above it add up to ₹7,000" in point(book(first), "F3")["detail"], "the first total row is still checked")
		beside = master()
		beside[2] += [None, "Particulars", "Amount"]
		beside[3] += [None, "Sale of ash", 900000]
		beside[4] += [None, "Sale of scrap", 100000]
		eq((found(book(beside)), read(book(beside))[0].list_total), ([], 7900.0), "a small table on the summary's own title row")
		lower = master()
		lower[4] += [None, None, "Particulars", "Amount"]
		lower[5] += [None, None, "Sale of ash", 900000]
		result, checked = read(book(lower))
		eq((checked.findings, result.list_total, [line["supplier_text"] for line in result.lines if line["section_text"] == "Master Sheet (side table)"]),
			([], 7900.0, ["Sale of ash"]),
			"a small table beside the summary that starts lower is read, and stays out of the total")
		nothing = master(((1, "Coal", 3000, 4000), (2, "Stores", 1500, 2000), (3, "DG Fuel", 0, 0)), outside=())
		for blank in (None, "-", "NIL"):
			nothing[5][2] = blank
			eq(found(book(nothing)), [], f"a head with {blank!r} for an amount has spent nothing")
		long = master(tuple((n, f"Head {n}", 100, 100) for n in range(1, 71)), outside=())
		result, checked = read(s1.xlsx(sheets=[("Master Sheet", long, False)]))
		eq((len(result.lines), result.list_total, checked.findings), (70, 7000.0, []), "a summary of seventy heads is read to its total row")
	test("mst: other ways a summary sheet is laid out", t_summary_layouts)

	def t_loud_not_silent():
		under = [*stores(), ["Pipe", "Nos", 2, 250, "Stores - T", "Shed", "d"]]
		eq(found(book(store_rows=under)), [("F1", CHECK)])
		p = point(book(store_rows=under), "F1")
		eq((p["title"], p["detail"]), ("Lines under the last total row", "2. Stores: Row 6 stands under the total row (row 5) and in no total: ₹250."))
		extra = book(master(HEADS[:2], outside=()), extra=[("7. Workshop", [STORE_HEAD, ["Rod", "Kg", 1, 700, "W - T"], ["TOTAL", None, None, 700]], False)])
		eq(found(extra), [("F2", CHECK)])
		p = point(extra, "F2")
		eq(p["title"], "A sheet has no head on the summary")
		check("No row of the summary points at: '7. Workshop'." in p["detail"], p["detail"])
		words = [["Sr. No.", "Particular", "Amount"], [1, "Tea", 200], [2, "Snacks", "about 300"], ["TOTAL", None, 500]]
		result, checked = read(s1.xlsx(sheets=[("14. Petty", words, False)]))
		eq((len(result.lines), sorted((p["rule"], p["line_no"]) for p in checked.findings)), (2, [("F1", 0), ("F6", 2)]))
		check("the amount 'about 300' could not be read as a number" in checked.findings[0]["detail"] + checked.findings[1]["detail"])
		big = [COAL_HEAD, ["Coal", "Kg", 1234567, 4.05, 5005000, "Boiler - T", "Boiler", "x"], ["TOTAL", None, None, None, 5005000]]
		result, checked = read(s1.xlsx(sheets=[("1. COAL", big, False)]))
		eq(checked.findings, [], "a rate typed to two decimals: the amount may sit half a paisa a kilo away")
		eq(result.lines[0]["details_text"], "1234567 Kg · rate 4.05 · Boiler - T · Boiler", "a large quantity is written out in full")
		big[1][3:5], big[2][4] = [4.054321, 5007330.91], 5007330.91
		result, checked = read(s1.xlsx(sheets=[("1. COAL", big, False)]))
		eq([p["rule"] for p in checked.findings], ["F5"], "a rate written out in full: two thousand rupees on fifty lakh is a point")
		check(checked.findings[0]["detail"].startswith("1. COAL row 2: 1234567 × 4.0543 = ₹50,05,330.9"), checked.findings[0]["detail"])
		many = [row for n in range(mst.MAX_TABLES + 1) for row in (STORE_HEAD, [f"Item {n}", "Nos", 1, 10, "S - T"])]
		s1.unreadable(lambda: mst.read_book(s1.xlsx(sheets=[("1. Stores", many, False)]), "xlsx"),
			"Sheet '1. Stores' has more tables than an MST sheet holds. Check the file and attach again.")
		note = read(book(store_rows=[*stores()[:-1], ["see the register"], stores()[-1]]))[0].notes
		eq(note, ["'2. Stores': 1 row(s) were skipped (no item name, or neither an amount nor a quantity)."], "a skipped row is said with its sheet")
	test("mst: what cannot be placed is said, never dropped", t_loud_not_silent)

	def t_neighbours():
		touching = [[*STORE_HEAD, "Party Name", "Amount"], ["Bolt", "Nos", 10, 500, "Stores - T", "Shed", "a", "Carrier one", 70],
			["Nut", "Nos", 20, 400, "Stores - T", "Shed", "b", "Carrier two", 30], ["TOTAL", None, None, 950, None, None, None, "TOTAL", 100]]
		result, checked = read(s1.xlsx(sheets=[("2. Stores", touching, False)]))
		eq(([line["amount"] for line in result.lines], result.list_total, sorted((p["rule"], p["level"]) for p in checked.findings)),
			([500.0, 400.0, 70.0, 30.0], 900.0, [("F1", SERIOUS)]), "a ledger set against the main table with no blank column between")
		unknown = [[*STORE_HEAD[:5], None, "Trader", "Bill value"], ["Bolt", "Nos", 10, 500, "Stores - T", None, "Trader one", 70],
			["Nut", "Nos", 20, 400, "Stores - T", None, "Trader two", 30], [None, None, None, None, None, None, "Total", 100], ["TOTAL", None, None, 900]]
		result, checked = read(s1.xlsx(sheets=[("2. Stores", unknown, False)]))
		eq(([line["remarks_text"] for line in result.lines], checked.findings), (["", ""], []), "a box with titles of its own, one blank column away")
		mixed = [COAL_HEAD, ["Coal A", "MT", 10, 200, 2000, "Boiler - T"], ["TOTAL", None, None, None, 2000], [],
			STORE_HEAD, ["Husk", "MT", 5, 1000, "Boiler - T"], ["TOTAL", None, None, 1000], ["GRAND TOTAL", None, None, None, 3000]]
		eq(found(s1.xlsx(sheets=[("1. COAL", mixed, False)])), [], "a grand total typed under the first block's amount column")
		shifted = [["Sr. No.", "Particular", "Amount"], [1, "Tea", 200], [2, "Water", 300], ["TOTAL", None, 500], [],
			[None, "Particular", "Amount"], [None, "Courier", 700], [None, "TOTAL", 700], ["GRAND TOTAL", None, 1200]]
		one = s1.xlsx(sheets=[("Master Sheet", master(((14, "Petty", 1200, 1200),), outside=()), False), ("14. Petty", shifted, False)])
		eq((found(one), read(one)[0].list_total, {line["section_text"] for line in read(one)[0].lines}), ([], 1200.0, {"14. Petty"}),
			"a second block typed without its first title is the same stack, not a side table")
		left = [[None, "Item Name", "Stock UOM", "Out Qty", "Grand Total"], [1, "Bolt", "Nos", 10, 500], [2, "Nut", "Nos", 20, 400], ["TOTAL", None, None, None, 950]]
		eq(found(s1.xlsx(sheets=[("2. Stores", left, False)])), [("F1", SERIOUS)], "TOTAL typed under an untitled serial column")
	test("mst: tables set against each other, and blocks that do not line up", t_neighbours)


	def t_second_look():
		"""Correct sheets that the wider rules read worse than the first ones did."""
		one = lambda title, rows, head: s1.xlsx(sheets=[("Master Sheet", master(((1, title.split(". ")[1], head, head),), outside=()), False), (title, rows, False)])  # noqa: E731
		first = [["Sr. No.", "Amount", "Particulars"], [1, 1000, "Pump repair"], [2, 500, "Motor rewinding"], ["Total", 1500, None]]
		result, checked = read(s1.xlsx(sheets=[("9. Repairs", first, False)]))
		eq(([(line["supplier_text"], line["amount"]) for line in result.lines], checked.findings), ([("Pump repair", 1000.0), ("Motor rewinding", 500.0)], []),
			"the amount's title before the name's")
		salary = s1.xlsx(sheets=[("Master Sheet", [["Monthly cost"], [], ["Sr.No.", "Cost Center", "Amount", "Per Ltr"], [1, "Coal", 3000, None],
			[None, "Total Salary", 1500, None], [2, "Stores", 500, None], ["Total Amount", None, 5000, None]], False)])
		result, checked = read(salary)
		eq((len(result.lines), result.list_total, [p["rule"] for p in checked.findings]), (3, 5000.0, ["F4"]), "a head named 'Total Salary' with no serial number")
		wrong = s1.xlsx(sheets=[("Master Sheet", [["Monthly cost"], [], ["Sr.No.", "Cost Center", "Amount", "Per Ltr"], [1, "Coal", 3000, None],
			[2, "Stores", 500, None], ["Total Expenses", None, 3900, None]], False)])
		result, checked = read(wrong)
		eq((len(result.lines), sorted(p["rule"] for p in checked.findings)), (2, ["F3", "F4"]), "a wrong total under such a label is still the total")
		eq(found(s1.xlsx(sheets=[("1. COAL", [COAL_HEAD, ["Coal", "MT", 2, 150000, 300700, "B - T"], ["TOTAL", None, None, None, 300700]], False)])),
			[("F5", CHECK)], "whole numbers are exact: seven hundred rupees on two units is a point")
		gst = [["Sr. No.", "Particular", "Amount"], [1, "Repair bill", 1000], [2, "Service bill", 500], ["Total", None, 1500], [None, "GST 18%", 270],
			["Total incl. GST", None, 1770]]
		eq((found(one("9. Repairs", gst, 1770)), read(one("9. Repairs", gst, 1770))[0].list_total), ([], 1770.0), "a total, a line more, and a total of both")
		lakh = [*stores(), ["Total (Rs. in Lakhs)", None, None, 0.015]]
		eq((found(book(store_rows=lakh)), len(read(book(store_rows=lakh))[0].lines)), ([], 7), "the total written again in lakhs")
		bills = [["Sr. No.", "Particular", "Amount"], [1, "Tea", 900], [2, "Water", 600], ["Total", None, 1500], [],
			[None, "Party Name", "Bill No", "Amount"], [None, "Trader one", "B-1", 900], [None, "Trader two", "B-2", 600], [None, "Total", None, 1500]]
		result, checked = read(one("14. Petty", bills, 1500))
		eq((checked.findings, result.list_total, len(result.lines)), ([], 1500.0, 4), "a bill list under the table, one column right: a table of its own")
		beside = master()
		beside[2] += [None, "Particulars", "Amount"]
		beside[3] += [None, "Sale of ash", 700]
		beside[4] += [None, "Sale of scrap", 300]
		beside[5] += [None, "Total", 900]
		result, checked = read(book(beside))
		eq((result.list_total, sorted((p["rule"], p["level"]) for p in checked.findings),
			[line["supplier_text"] for line in result.lines if line["section_text"] == "Master Sheet (side table)"]),
			(7900.0, [("F1", CHECK)], ["Sale of ash", "Sale of scrap"]), "a table on the summary's own title row is read and checked")
		levels = [STORE_HEAD, ["A", "Kg", 1, 100, "P - T"], ["Sub Total", None, None, 100], ["B", "Kg", 1, 200, "P - T"], ["Sub Total", None, None, 200],
			["Total (A)", None, None, 300], ["C", "Kg", 1, 400, "P - T"], ["Sub Total", None, None, 400], ["Total (B)", None, None, 400],
			["Grand Total (A+B)", None, None, 700]]
		eq((found(one("3. Process", levels, 700)), read(one("3. Process", levels, 700))[0].list_total), ([], 700.0), "three levels of totals")
		qty = [STORE_HEAD, ["Bolt", "Nos", 10, 900, "S - T"], ["Nut", "Nos", 5, 600, "S - T"], ["TOTAL", None, 15, None], ["TOTAL AMOUNT", None, None, 1500]]
		eq(found(one("2. Stores", qty, 1500)), [("F6", CHECK)], "a total row with the quantity only, then the amount's: no false total point")
	test("mst: correct sheets the wider rules must not read worse", t_second_look)


	def t_said_not_passed_over():
		"""What the first real workbook showed: error cells outside the amount, a sheet with no table, a copy of the summary."""
		rows = stores()
		rows[1][1], rows[2][4] = "#ERROR!", "#REF!"
		rows.append([None, None, "#VALUE!"])
		result, checked = read(book(store_rows=rows))
		eq([(p["rule"], p["level"], p["line_no"], p["title"]) for p in checked.findings], [("F6", CHECK, 3, "A cell of this line shows an error"),
			("F6", CHECK, 4, "A cell of this line shows an error"), ("F6", CHECK, 0, "Cells that show an error")])
		eq([p["detail"] for p in checked.findings], ["2. Stores row 2: B2 shows #ERROR!. Correct the formula in the workbook and submit again.",
			"2. Stores row 3: E3 shows #REF!. Correct the formula in the workbook and submit again.",
			"2. Stores: C6 (#VALUE!). Each is a formula that no longer works. Correct it in the workbook and submit again."])
		eq(([extra["verdict"] for extra in checked.lines][2:5], result.list_total), ([CHECK, CHECK, OK], 7900.0), "the lines keep their money")
		rows = stores()
		rows[1][3] = "#REF!"
		eq([p["title"] for p in read(book(store_rows=rows))[1].findings if p["rule"] == "F6"], ["A line has no amount"], "an error in the amount is said once")
		matrix = [["Machine", "Day 1", None, "Day 2", None], [None, "Hours", "Diesel", "Hours", "Diesel"], *[[f"Loader {n}", 5, 20, 6, 24] for n in range(1, 5)]]
		with_matrix = book(extra=[("11. Machinery", matrix, False), ("Notes", [["Prepared in the first week"], ["Checked by Accounts"]], False)])
		result, checked = read(with_matrix)
		eq([(p["rule"], p["level"], p["line_no"], p["title"]) for p in checked.findings], [("F6", CHECK, 0, "A sheet was not read")])
		check("was found on: '11. Machinery'. Nothing on it was checked" in checked.findings[0]["detail"], checked.findings[0]["detail"])
		copy = book(extra=[("Copy of Master Sheet", master(), False)])
		result, checked = read(copy)
		eq((checked.findings, result.list_total, len(result.lines)), ([], 7900.0, 7), "a second summary sheet adds nothing and is no point")
		check("A second summary sheet was set against it: 'Copy of Master Sheet' (4 heads, the same amounts)." in checked.coverage, checked.coverage)
		old = book(extra=[("Copy of Master Sheet", master(((1, "Coal", 2800, 4000),) + HEADS[1:]), False)])
		result, checked = read(old)
		eq([(p["rule"], p["level"], p["title"]) for p in checked.findings], [("F2", CHECK, "A second summary sheet differs from the summary")])
		eq(checked.findings[0]["detail"], "Copy of Master Sheet: Against 'Master Sheet': Coal (row 4: ₹2,800, there ₹3,000).")
		eq((result.list_total, len(result.lines)), (7900.0, 7), "the copy's figures are in no total")
	test("mst: an error cell anywhere, a sheet with no table and a copy of the summary are said", t_said_not_passed_over)


# ---- who may send it, the job, the report, the bells


	def t_machines():
		table = "3. Machinery (machine table)"
		result, checked = read(machine_book())
		eq(checked.findings, [], "points on a correct machine table")
		eq([(line["section_text"], line["supplier_text"], line["amount"], line["details_text"]) for line in result.lines[5:9]], [
			(table, "Loader (MP 01) · Coal", 2800.0, "10 hours · rate 100"), (table, "Loader (MP 01) · Plant (fixed)", 1000.0, "5 hours · rate 200"),
			(table, "Dozer (MP 02) · Coal", 1000.0, "2 days · rate 500"), (table, "Dozer (MP 02) · Farm", 850.0, "4 hours · rate 100")])
		eq([(line["supplier_text"], line["amount"], line["read_note"][:44]) for line in result.lines[9:]], [
			("Machinery For Fuel", 4800.0, "Taken from the summary: its sheet is a machi"),
			("Machinery Other", 50.0, "Taken from the summary: its sheet is a machi"), ("Depreciation", 900.0, "Taken from the summary: no sheet of this hea")])
		eq((result.list_total, len(result.lines)), (10250.0, 12), "the heads are counted, the table's lines are not")
		check("4 of the lines are from a machine table (₹5,650): they are checked, and not counted in the total" in checked.summary, checked.summary)
		check("'3. Machinery' was read as a machine table: 2 machines, 3 groups" in checked.coverage, checked.coverage)
		check("head 'Machinery For Fuel' is the total of Coal + Plant (fixed); heads 'Machinery For Fuel', 'Machinery Other' together "
			"are the figure in J8. What stands under the table (from row 7) was not checked." in checked.coverage, checked.coverage)

		rate = machine_book(machinery({("Loader", 5): 110}))
		eq(found(rate), [("F5", CHECK)], "hours × rate")
		eq((point(rate, "F5")["line_no"], point(rate, "F5")["detail"]), (6, "3. Machinery row 4, Coal: 10 × 110 = ₹1,100, the sheet says ₹1,000."))
		diesel = machine_book(machinery({("Loader", 9): 95}))
		eq(found(diesel), [("F5", CHECK)], "diesel × its rate")
		check("diesel 20 × 95 = ₹1,900, the sheet says ₹1,800" in point(diesel, "F5")["detail"], point(diesel, "F5")["detail"])
		eq(found(machine_book(machinery({("Dozer", 5): 510}))), [("F5", CHECK)], "days × rate")
		typed = read(machine_book(machinery({("Loader", 11): 2900})))[1].findings
		eq([(p["rule"], p["level"], p["line_no"], p["detail"]) for p in typed], [
			("F1", CHECK, 6, "3. Machinery row 4, Coal: hire ₹1,000 and diesel ₹1,800 add up to ₹2,800, the sheet says ₹2,900."),
			("F1", CHECK, 0, "3. Machinery: Row 4 (Loader (MP 01)): its groups add up to ₹3,900, the row's total says ₹3,800."),
			("F1", SERIOUS, 0, "3. Machinery: L6 says 3800; the machine rows above it add up to 3900.")])
		eq(found(machine_book(machinery({("TOTAL", 11): 3900}))), [("F1", SERIOUS), ("F2", CHECK)],
			"a total row that the machines do not add up to; the head no longer follows from it")
		eq(found(machine_book(machinery({("TOTAL", 24): 20}))), [("F1", CHECK)], "the hours of the total row")

		over = machine_book(heads=HEADS[:2] + ((3, "Machinery For Fuel", 4700, 5000), (4, "Machinery Other", 50, 100)))
		eq(found(over), [("F2", CHECK)], "a head typed over")
		eq(point(over, "F2")["detail"], "row 6 (Machinery For Fuel) shows ₹4,700. That is not the total of any groups of the machine "
			"table on '3. Machinery', nor a figure worked out under that table.")
		result, checked = read(machine_book(machinery(total=False)))
		eq([(p["rule"], p["level"], p["title"]) for p in checked.findings], [("F1", CHECK, "The machine table has no total row")])
		check("is the total of Coal + Plant (fixed)" in checked.coverage, "with no total row the groups are added up from the machines")

		result, checked = read(machine_book(machinery(pad=7)))
		eq((checked.findings, result.list_total, len(result.lines)), ([], 10250.0, 12), "a table 89 columns wide is read to its last column")
		check("2 machines, 10 groups" in checked.coverage, checked.coverage)
		result, checked = read(machine_book(machinery(name="Particulars")))
		eq((checked.findings, len(result.lines)), ([], 12), "the row of group titles is not read as a table of its own")
		result, checked = read(s1.xlsx(sheets=[("3. Machinery", machinery(), False)]))
		eq((checked.findings, result.list_total, [line["section_text"] for line in result.lines]), ([], 5650.0, ["3. Machinery"] * 4),
			"sent alone, the table's lines are the lines that count")
		check("machine table (" not in checked.summary, checked.summary)
		check(("F6", CHECK) in found(machine_book(machinery({("Loader", 11): "#REF!"}))), "a total that shows an error")

		far = coal()
		far[0] = [*COAL_HEAD, *[None] * 122, "more"]
		far[1] = [*far[1], *[None] * 60, "#REF!"]
		eq(read(book(coal_rows=far))[1].findings, [], "any other sheet is read to its first 60 columns, as it always was")
		wide = machinery(edit=lambda rows: rows[3].extend([*[None] * 104, "more"]))
		eq([(p["rule"], p["level"], p["detail"]) for p in read(machine_book(wide))[1].findings], [("F6", CHECK, "3. Machinery: Only the first "
			"130 columns (up to DZ) were read. What stands further right was not checked.")])
	test("mst: a machine table (machines down, users across) is read, checked and set against its heads", t_machines)


	def t_machine_variants():
		clean = lambda rows, heads=MACHINE_HEADS: (lambda out: (out[1].findings, out[0].list_total))(read(machine_book(rows, heads)))  # noqa: E731
		fine = ([], 10250.0)
		gap8, gap9 = [None] * 8, [None] * 9
		# correct tables, the way they are typed
		sub = [None, "Sub Total", None, *gap8, 2800, None, None, 1000, *gap9, 15, 3800]
		result, checked = read(machine_book(machinery(edit=lambda rows: rows.insert(4, sub))))
		eq((checked.findings, result.list_total, len(result.lines)), ([], 10250.0, 12), "a Sub Total row between the machines")
		check("2 machines, 3 groups" in checked.coverage and "together are the figure in J9" in checked.coverage, checked.coverage)
		eq(clean(machinery(edit=lambda rows: rows.insert(5, []))), fine, "a blank row above the total row")
		eq(clean(machinery(edit=lambda rows: rows.insert(5, [3, None, None]))), fine, "a numbered row nobody filled")
		result, checked = read(machine_book(machinery({("Loader", 1): "407"})))
		eq((checked.findings, result.lines[5]["supplier_text"]), ([], "407 (MP 01) · Coal"), "a machine whose name is a number")
		result, checked = read(machine_book(machinery({("Dozer", 1): None, ("Dozer", 2): None})))
		eq((checked.findings, len(result.lines), result.lines[7]["supplier_text"]), ([], 12, "Loader (MP 01) · Coal"), "a name merged over two rows")
		other = ["Hrs", "Days", "Rate/Hr", "AMT (RS)", "Diesel P/H", "Total Diesel", "D.Rate", "Amount (Rs)", "TOTAL"]
		eq(clean(machinery(subs=other)), fine, "the column titles in other words")

		def gst(rows):
			rows[1] += ["GST AMOUNT", "NET AMOUNT"]
			for row in rows[3:6]:
				row += [row[-1] * 0.18, row[-1] * 1.18]
		eq(clean(machinery(edit=gst)), fine, "more columns after the row's total")

		def swap(rows):
			for row in (rows[1], *rows[3:6]):
				row[-2], row[-1] = row[-1], row[-2]
		eq(clean(machinery(edit=swap)), fine, "the row's hours typed after its amount")
		eq(clean(machinery({("TOTAL", 23): None}), HEADS[:2] + ((3, "Machinery For Fuel", 850, 900), (4, "Machinery Other", 4000, 4000))), fine,
			"a head that is one group, with no sum of that group in the total row")
		block = [[None, "Particulars", "Amount"], [None, "Coal and plant", 4800], [None, "Other", 50], [None, "TOTAL", 4850]]
		result, checked = read(machine_book(machinery(edit=lambda rows: rows.extend(block))))
		eq((checked.findings, result.list_total), fine, "a titled block under the table is listed, and counts nothing twice")
		eq([(line["section_text"], line["amount"]) for line in result.lines if "side table" in line["section_text"]],
			[("3. Machinery (side table)", 4800.0), ("3. Machinery (side table)", 50.0)])
		check("apart from the table(s) read there" in checked.coverage, checked.coverage)

		def beside(rows):  # the same block to the right of the table, its titles on the first machine's row
			for i, row in enumerate(rows):
				row += [None] * (28 - len(row)) + (block[i - 3][1:] if 3 <= i < 7 else [])
		result, checked = read(machine_book(machinery(edit=beside)))
		eq((checked.findings, result.list_total), fine, "a titled block beside the machine rows does not end the table")
		check("2 machines, 3 groups" in checked.coverage, checked.coverage)

		def gst(rows):  # under the total row: GST on the whole, then a grand total
			rows[6:6] = [[None, "GST 18%", *[None] * 23, 1017], [None, "Grand Total", *[None] * 23, 6667]]
		result, checked = read(machine_book(machinery(edit=gst)))
		eq((checked.findings, result.list_total), fine, "a grand total that adds GST to the total row is no mistake")

		def gst_wrong(rows):
			gst(rows)
			rows[7][25] = 6700
		_result, checked = read(machine_book(machinery(edit=gst_wrong)))
		eq([(p["rule"], p["level"]) for p in checked.findings], [("F1", SERIOUS)], "a grand total that is neither sum is said")
		no_total = [["HIRE"], ["S.NO", "MACHINE NAME", "Coal", *[None] * 5, "Farm", *[None] * 5, "TOTAL AMOUNT"],
			[None, None, *["Hours", "RATE", "AMT", "TOTAL DIESEL", "D RATE", "AMOUNT"] * 2],
			[1, "Loader", 10, 100, 1000, 20, 90, 1800, *[None] * 6, 2800], [2, "Dozer", *[None] * 6, 4, 100, 400, 5, 90, 450, 850],
			[None, "TOTAL", None, None, 1000, None, None, 1800, None, None, 400, None, None, 450, 3650]]
		result, checked = read(s1.xlsx(sheets=[("3. Machinery", no_total, False)]))
		eq((checked.findings, result.list_total, [line["amount"] for line in result.lines]), ([], 3650.0, [2800.0, 850.0]),
			"groups that keep no total of their own: hire and diesel are added")

		eq(clean(machinery({("Loader", 2): "Total Infra"})), fine, "a vehicle of a firm called Total")
		eq(clean(machinery({("Loader", 1): "Total Station"})), fine, "a machine called Total Station")

		def blocks(rows):
			rows[4:4] = [sub, [], [None, "OWN MACHINES"]]
		result, checked = read(machine_book(machinery(edit=blocks)))
		eq((checked.findings, result.list_total, len(result.lines)), ([], 10250.0, 12), "two blocks, a blank row and a block title between them")
		gst = [None, "GST and others", None, *gap8, None, None, None, None, *gap9, None, 350]
		eq(clean(machinery(edit=lambda rows: rows.insert(6, gst)), HEADS[:2] + ((3, "Machinery For Fuel", 350, 400), (4, "Machinery Other", 4500, 4500))),
			fine, "a head that is a figure typed right under the total row")
		both = ["Hours", "Days", "RATE", "AMOUNT", "DIESEL P/H", "TOTAL DIESEL", "D RATE", "AMOUNT", "TOTAL AMOUNT"]
		eq(clean(machinery(subs=both)), fine, "hire and diesel both titled Amount")
		eq(clean(machinery({("Loader", 23): 450, ("Loader", 25): 4250, ("TOTAL", 23): 1300, ("TOTAL", 25): 6100})), fine,
			"a fixed charge typed straight into a group's total")
		result, checked = read(machine_book(machinery({("TOTAL", 1): None})))
		eq((checked.findings, len(result.lines)), ([], 12), "a total row typed without its word")
		low = [["HIRE"], ["S.NO", "MACHINE NAME", "Coal", None, None, "Farm"],
			[None, None, "Hours", "RATE", "AMOUNT", "Hours", "RATE", "AMOUNT", "TOTAL (H)", "TOTAL AMOUNT"],
			[1, "Loader", 10, 100, 1000, 5, 100, 500, 15, 1500], [2, "Dozer", 8, 100, 800, None, None, None, 8, 800],
			[None, "TOTAL", None, None, 1800, None, None, 500, 23, 2300]]
		result, checked = read(s1.xlsx(sheets=[("3. Machinery", low, False)]))
		eq((checked.findings, result.list_total, [line["amount"] for line in result.lines]), ([], 2300.0, [1000.0, 500.0, 800.0]),
			"the row's totals titled on the lower row are no part of the last group")
		low[1], low[2] = [*low[1], None, None, "TOTAL"], [*low[2][:8], "Hours", "AMOUNT"]
		result, checked = read(s1.xlsx(sheets=[("3. Machinery", low, False)]))
		eq((checked.findings, result.list_total, len(result.lines)), ([], 2300.0, 3), "TOTAL over Hours | Amount is the row's total, not a group")
		many = [["HIRE"], ["S.NO", "MACHINE NAME", *[cell for nth in range(16) for cell in (f"Unit {nth + 1}", None, None)], "TOTAL AMOUNT"],
			[None, None, *["Hours", "RATE", "AMOUNT"] * 16], [1, "Loader", *[1, 100, 100] * 16, 1600], [2, "Dozer", *[2, 100, 200] * 16, 3200],
			[None, "TOTAL", *[None, None, 300] * 16, 4800]]
		one = lambda amount: HEADS[:2] + ((3, "Machinery For Fuel", amount, 5000),)  # noqa: E731
		eq([found(machine_book(many, one(amount))) for amount in (4800, 4500, 600, 4650)], [[], [], [], [("F2", CHECK)]],
			"sixteen groups: the whole table, all but one group, two groups; and a head that is none of these")
		broken = master(MACHINE_HEADS, fix={"Machinery For Fuel": {"amount": "#REF!"}})
		result, checked = read(book(broken, extra=[("3. Machinery", machinery(), False)]))
		eq(([(p["rule"], p["title"]) for p in checked.findings], result.list_total), ([("F6", "A head has no amount")], 11100.0),
			"a head that shows an error: the machine table's own lines are the ones counted")

		# look-alikes that are no machine table read as they always did
		shifts = [[None, None, "Day shift", None, None, "Night shift"], ["Sr", "Particulars", "Days", "Rate", "Amount", "Days", "Rate", "Amount", "Total Amount"],
			[1, "Helpers", 10, 500, 5000, 5, 600, 3000, 8000], [2, "Fitters", 4, 1000, 4000, 2, 1500, 3000, 7000], [None, "Total", None, None, None, None, None, None, 15000]]
		result, checked = read(s1.xlsx(sheets=[("4. Labour", shifts, False)]))
		eq((checked.findings, result.list_total, len(result.lines), "machine table" in checked.coverage), ([], 15000.0, 2, False))
		ledger = [[None, None, "Opening", None, "Closing"], ["Sr", "Particulars", "Rate", "Amount", "Rate", "Amount"], [1, "Bolt", 5, 100, 5, 60], [2, "Nut", 2, 50, 2, 40]]
		result, checked = read(s1.xlsx(sheets=[("4. Stock", ledger, False)]))
		eq((result.list_total, len(result.lines), "machine table" in checked.coverage), (100.0, 2, False))

		# mistakes that must be said
		over = lambda a, b: HEADS[:2] + ((3, "Machinery For Fuel", a, 5000), (4, "Machinery Other", b, 100))  # noqa: E731
		eq(found(machine_book(heads=over(4700, 150))), [("F2", CHECK)], "a head typed over, though the two heads together still fit")
		eq(found(machine_book(heads=over(0, 50))), [("F2", CHECK)], "a head of nothing against a table that holds money")
		eq(found(machine_book(heads=over(100, 50))), [("F2", CHECK)], "a head that only equals a rate on the sheet")
		late = [3, "Roller", "MP 03", 2, None, 100, 200, None, None, None, None, 200, None, None, None, *gap9, 2, 200]
		under = read(machine_book(machinery(edit=lambda rows: rows.insert(6, late))))[1].findings
		eq([(p["rule"], p["level"], p["detail"]) for p in under], [("F1", CHECK, "3. Machinery: Row 7 stands under the total row (row 6) and in no total: ₹200.")])
		words = read(machine_book(machinery({("Loader", 3): "10 hrs"})))[1].findings
		eq([(p["rule"], p["level"], p["title"], p["detail"][:14]) for p in words], [("F6", CHECK, "A figure could not be read", "3. Machinery: ")])
		check("D4 ('10 hrs')" in words[0]["detail"], words[0]["detail"])
		owed = read(machine_book(machinery({("Loader", 15): 3, ("Loader", 17): 100})))[1].findings
		eq([(p["rule"], p["level"], p["detail"]) for p in owed], [("F6", CHECK, "3. Machinery row 4, Farm: 3 × 100 is not worked out: the amount is empty.")])
		eq(found(machine_book(machinery({("Loader", 6): 1004}))), [("F1", CHECK), ("F5", CHECK)], "four rupees off on whole hours and a whole rate")
		eq(found(machine_book(machinery({("TOTAL", 25): 5700}))), [("F1", SERIOUS)], "the total row's own total typed over")

		# a sheet no head points at: the heads of the machine table stay in the report
		loose = read(book(master(MACHINE_HEADS), extra=[("3. Machinery", machinery(), False), ("9. Extra", stores(), False)]))
		names = [line["supplier_text"] for line in loose[0].lines]
		check("Machinery For Fuel" in names and "Machinery Other" in names and "Depreciation" not in names, str(names))
		eq(sorted(p["title"] for p in loose[1].findings), ["Some heads and sheets could not be matched"])

		# the second summary sheet, head by head
		twice = HEADS + ((4, "Misc", 100, 100), (5, "Misc", 200, 200))
		same = read(book(master(twice), extra=[("Copy of Master Sheet", master(twice), False)]))[1]
		eq(same.findings, [], "a head name that stands twice")
		check("'Copy of Master Sheet' (6 heads, the same amounts)" in same.coverage, same.coverage)
		more = read(book(master(twice), extra=[("Copy of Master Sheet", master(twice[:4] + ((6, "New head", 10, 10),)), False)]))[1]
		eq(more.findings, [], "a head on one of the two sheets only is small print, not a point")
		check("'Copy of Master Sheet' (5 heads, the same amounts; 2 head(s) stand on one of the two only)" in more.coverage, more.coverage)
		old = read(book(master(twice), extra=[("Copy of Master Sheet", master(((1, "Coal", 2800, 4000),) + twice[1:]), False)]))[1]
		check("'Copy of Master Sheet' (it differs)" in old.coverage, old.coverage)
	test("mst: machine tables as they are typed, their look-alikes, and what must be said", t_machine_variants)

	def t_erp():
		import json

		def erp(rows, month="2026-09", problem=""):
			return {"month": month, "label": "September 2026", "company": "Test Co", "abbrs": ["T"], "problem": problem,
				"rows": [dict(zip(("doc", "day", "name", "uom", "qty", "amount", "cc"), row)) for row in rows]}

		def against(rows, data=None, **more):
			# the ERP's lines reach the comparison the way the job hands them over: plain texts, numbers and lists
			result, checked = mst.report(json.loads(json.dumps(mst.read_book(data or book(), "xlsx"))), "xlsx", erp=json.loads(json.dumps(erp(rows, **more))))
			return result, checked, sorted((p["rule"], p["level"], p["line_no"]) for p in checked.findings)

		same = [("SE-1", "2026-09-02", "Coal A", "MT", 10, 2000, "1 - Boiler - T"), ("SE-2", "2026-09-12", "Coal B", "MT", 5, 1000, "1 - Boiler - T"),
			("SE-3", "2026-09-05", "Bolt", "Nos", 4, 200, "Stores - T"), ("SE-4", "2026-09-20", "BOLT", "Nos", 6, 300, "Stores - T"),
			("SE-5", "2026-09-21", "Nut", "Nos", 20, 400, "Stores - T"), ("SE-6", "2026-09-22", "Belt", "Nos", 1, 600, "Stores - T")]
		swap = lambda doc, **new: [tuple(new.get(key, value) for key, value in zip(("doc", "day", "name", "uom", "qty", "amount", "cc"), row))  # noqa: E731
			if row[0] == doc else row for row in same]
		result, checked, points = against(same)
		eq(points, [], "every line as the ERP has it")
		eq(checked.lines[2]["erp_says"], "ERP, September 2026, under 'Stores - T': 10 Nos, ₹500 on 2 issue line(s) (SE-3, SE-4).")
		eq(checked.lines[5]["erp_says"], "", "a head taken from the summary is not an issue")
		check("(Test Co: 6 issue line(s), ₹4,500), by cost centre and item name: 5 line(s) on 2 sheet(s). The same in both: 5. "
			"Differing: 0. With no issue in that month: 0." in checked.coverage, checked.coverage)
		check("not compared with the issues recorded in the ERP" not in checked.coverage, checked.coverage)
		result, checked, points = against(swap("SE-5", qty=25, amount=500))
		eq((points, checked.lines[3]["verdict"], checked.n_check), ([("E2", CHECK, 4)], CHECK, 1), "the ERP issued more than the sheet says")
		eq(checked.findings[0]["title"], "Quantity differs from the ERP")
		check("2. Stores row 3: Nut: the sheet says 20 Nos, ₹400; the ERP's issues of September 2026 under 'Stores - T' come to 25 Nos, ₹500 "
			"(1 issue line(s): SE-5). The quantity differs by 5." == checked.findings[0]["detail"], checked.findings[0]["detail"])
		eq(checked.findings[0]["refs"], "SE-5")
		_result, checked, points = against(swap("SE-6", amount=650))
		eq((points, checked.findings[0]["title"]), ([("E2", CHECK, 5)], "Amount differs from the ERP"))
		_result, checked, points = against(swap("SE-1", amount=1500))
		eq((points, checked.lines[0]["verdict"], checked.findings[0]["title"]), ([("E6", "Note", 1)], OK, "Valued at another rate than the ERP"),
			"a typed rate is no mistake: the quantity is the ERP's")
		_result, checked, points = against(swap("SE-1", qty=12, amount=2400))
		eq(points, [("E2", CHECK, 1)])
		check(checked.findings[0]["detail"].endswith("The quantity differs by 2. The amount is worked out from a typed rate and was not compared."),
			checked.findings[0]["detail"])
		eq(against(swap("SE-1", uom="Kg", qty=10000))[2], [], "tonnes on the sheet, kilograms in the ERP")
		_result, checked, points = against(swap("SE-6", uom="Box", qty=3))
		eq(points, [])
		check(checked.lines[4]["erp_says"].endswith("The units differ, so the quantity was not compared."), checked.lines[4]["erp_says"])
		# a line the ERP does not have in the month
		_result, checked, points = against(same[:5])
		eq((points, checked.findings[0]["title"]), ([("E1", CHECK, 5)], "No issue in the ERP"))
		check(checked.findings[0]["detail"] == "2. Stores row 4: Belt (1 Nos, ₹600): no Material Issue of an item of this name under "
			"'Stores - T' in September 2026. Nor in the month before or the month after.", checked.findings[0]["detail"])
		_result, checked, points = against(swap("SE-6", day="2026-10-01"))
		eq((points, checked.findings[0]["title"]), ([("E5", CHECK, 5)], "Issued in another month in the ERP"))
		check("The ERP has it on 1 Oct 2026: 1 Nos, ₹600 (SE-6)." in checked.findings[0]["detail"], checked.findings[0]["detail"])
		_result, checked, points = against(swap("SE-6", cc="Electrical - T"))
		eq((points, checked.findings[0]["title"]), ([("E4", CHECK, 5)], "Issued under another cost centre in the ERP"))
		check("The ERP has it under 'Electrical - T': 1 Nos, ₹600 (SE-6)." in checked.findings[0]["detail"], checked.findings[0]["detail"])
		_result, checked, points = against(swap("SE-6", cc="Boiler - T"))
		eq(points, [("E4", CHECK, 5)], "written on the Stores sheet, issued to the boiler: said on the line, and not again on the Coal sheet")
		# the same line twice: a slip, unless the ERP issued the item that often
		twice = stores([["Bolt", "Nos", 5, 250, "Stores - T", "Shed", "a"], ["Bolt", "Nos", 5, 250, "Stores - T", "Shed", "a"],
			["Nut", "Nos", 20, 400, "Stores - T", "Shed", "b"], ["Belt", "Nos", 1, 600, "Stores - T", "Shed", "c"]])
		eq(against(same, book(store_rows=twice))[2], [], "two issues of five bolts each")
		eq(against(swap("SE-4", qty=1, amount=50), book(store_rows=twice))[2], [("E2", CHECK, 3), ("E2", CHECK, 4)],
			"the ERP issued five, not ten: neither line has an issue of its own, and both are marked")
		eq(against([row for row in same if row[0] not in ("SE-3", "SE-4")], book(store_rows=twice))[2], [("E1", CHECK, 3), ("E1", CHECK, 4), ("F8", CHECK, 4)],
			"with no issue of the item at all, both lines are marked and the repeated line is still said")
		more = [*same, ("SE-7", "2026-09-25", "Gasket", "Nos", 2, 900, "Stores - T"), ("SE-8", "2026-09-26", "Gasket", "Nos", 1, 450, "Stores - T"),
			("SE-9", "2026-09-27", "Oil", "Ltr", 5, 100, "Stores - T"), ("SE-10", "2026-09-28", "Hose", "Nos", 1, 70, "Garden - T")]
		_result, checked, points = against(more)
		eq(points, [("E3", CHECK, 0)])
		eq(checked.findings[0]["detail"], "2. Stores: Under 'Stores - T' the ERP has 3 issue line(s) of September 2026 (₹1,450) of 2 item(s) that "
			"stand on no line of this sheet: Gasket (3 Nos, ₹1,350, SE-7, SE-8); Oil (5 Ltr, ₹100, SE-9).")
		check("The ERP also has issues of September 2026 under cost centres that no line of this file names: 'Garden - T' (1 line(s), ₹70)."
			in checked.coverage, checked.coverage)
		check(checked.summary.endswith("On a sheet or on the whole file: 0 Serious, 1 To check."), checked.summary)
		# an item on a line with no cost centre is compared over all cost centres
		loose = stores([["Bolt", "Nos", 10, 500, None, "Shed", "a"], ["Nut", "Nos", 20, 400, "Stores - T", "Shed", "b"],
			["Belt", "Nos", 1, 600, "Stores - T", "Shed", "c"]])
		_result, checked, points = against(swap("SE-4", cc="Garden - T"), book(store_rows=loose))
		eq(points, [])
		check(checked.lines[2]["erp_says"].startswith("ERP, September 2026, under any cost centre: 10 Nos, ₹500 on 2 issue line(s)"), checked.lines[2]["erp_says"])
		# a sheet of typed expenses is left alone, and said
		typed = s1.xlsx(sheets=[("1. COAL", coal(), False), ("9. Diesel", [STORE_HEAD, ["Diesel for DG", "Ltr", 100, 9000, None, "DG", "x"],
			["TOTAL", None, None, 9000]], False)])
		_result, checked, points = against(same[:2], typed)
		eq(points, [])
		check("the sheets where no line names an item the ERP issued ('9. Diesel')" in checked.coverage, checked.coverage)
		# nothing to compare with
		for rows, more, words in (([], {}, "The ERP holds no Material Issue of Test Co dated September 2026"),
				(same, {"month": "2026-08"}, "The ERP holds no Material Issue of Test Co dated"),
				([], {"problem": "access"}, "has no access to stock entries"), ([], {"problem": "many"}, "too many to compare"),
				([], {"problem": "part"}, "may read only a part of the company's stock entries")):
			_result, checked, points = against(rows, **more)
			eq(points, [("E7", CHECK, 0)], words)
			check(words in checked.findings[0]["detail"], checked.findings[0]["detail"])
			eq({extra["erp_says"] for extra in checked.lines}, {""})
			check("The lines were not compared with the issues recorded in the ERP." in checked.coverage, checked.coverage)
		many = stores([[f"Part {n}", "Nos", 1, 100, "Stores - T", "Shed", "a"] for n in range(12)], 1200)
		_result, checked, points = against([("SE-1", "2026-09-02", "Part 3", "Nos", 1, 100, "Stores - T")],
			book(master(((1, "Coal", 3000, 4000), (2, "Stores", 1200, 2000), HEADS[2])), store_rows=many))
		eq(points, [("E7", CHECK, 0)], "the wrong month gives one point, not a point on every line")
		check("Only 1 of the 12 items of this file were found among the ERP's issues of September 2026." in checked.findings[0]["detail"], checked.findings[0]["detail"])
	test("mst: every line is set against the ERP's issues of the month", t_erp)

	def t_erp_names():
		import json

		from trustbit_ethanol.ts_gate_entry import ts_ai_check_issues as issues

		row = lambda name, qty=7, uom="Kg": [{"doc": "SE-1", "day": "2026-09-02", "name": name, "uom": uom, "qty": qty, "amount": 10, "cc": "X - T"}]  # noqa: E731
		pool = lambda *names, **more: {issues.item_key(name): row(name, **more) for name in names}  # noqa: E731
		like = lambda name, names, qty=None, unit="kg", **more: issues._like(name, qty, unit, 1, pool(*names, **more))  # noqa: E731
		eq(like("Cartridge Fillter 40 Inch ( 5 Micron )", ["Cartridge Filter 40 Inch ( 5 Micron )", "Cartridge Filter 40 Inch ( 20 Micron )"]),
			issues.item_key("Cartridge Filter 40 Inch ( 5 Micron )"), "a letter too many; the other size is another item")
		eq(like("PAC", ["Poly Aluminium Chloride (PAC)", "Polyelectrolyte"]), issues.item_key("Poly Aluminium Chloride (PAC)"))
		eq(like("LIQUOR AMMONIA 25%", ["Liquor Ammonia"]), "liquorammonia")
		eq(like("Bearing", ["Bearing 6306", "Bearing 6209"]), None, "two items it could be: no answer")
		eq(like("Gasket", ["Valve", "Pump"]), None)
		eq(like("PP BAG 26X42 INCH", ["PP Woven Bags", "Thread"], qty=7, unit=""), "ppwovenbags", "the same quantity, and a word in common")
		eq(like("PP BAG 26X42 INCH", ["PP Woven Bags"], qty=8), None, "another quantity")
		eq(like("Seal kit", ["Oil seal"], qty=2), None, "two pieces prove nothing")
		eq(like("FORMAKDEHYDE(FORMOLINE)", ["Formaldehyde", "Salt"], qty=7), "formaldehyde")
		eq((issues.cc_key("21 - DDGS - BBPL", ["BBPL"]), issues.cc_key("DDGS - BBPL", ["BBPL"]), issues.cc_key(" ddgs ", ["BBPL"])), ("ddgs",) * 3)
		eq(issues.cc_key("Boiler - Bed", ["BBPL"]), "boiler bed", "only the company's own short name is cut off")
		eq((issues.unit_key("Kgs"), issues.unit_key("NOS."), issues.unit_key("#ERROR!"), issues.unit_key("Drum")), ("kg", "nos", "", "drum"))

		def against(rows, sheets):
			erp = {"month": "2026-09", "label": "September 2026", "company": "Test Co", "abbrs": ["T"], "problem": "",
				"rows": [dict(zip(("doc", "day", "name", "uom", "qty", "amount", "cc"), row)) for row in rows]}
			result, checked = mst.report(json.loads(json.dumps(mst.read_book(s1.xlsx(sheets=sheets), "xlsx"))), "xlsx", erp=json.loads(json.dumps(erp)))
			return result, checked, sorted((p["rule"], p["level"], p["line_no"]) for p in checked.findings)

		bags = [COAL_HEAD, ["PP BAG 26X42 INCH", "Nos", 4580, 2, 9160, "21 - DDGS - T", "Bagging", "x"], ["Thread", "Nos", 29, 10, 290, "21 - DDGS - T", "Bagging", "y"],
			["TOTAL", None, None, None, 9450]]
		_result, checked, points = against([("SE-1", "2026-09-30", "PP Woven Bags", "Nos", 4580, 18000, "DDGS - T")], [("3. DDGS", bags, False)])
		eq(points, [("E1", CHECK, 2), ("E6", "Note", 1)], "the bags are the ERP's under another name; the thread was never issued")
		check("The ERP writes the item 'PP Woven Bags'." in checked.lines[0]["erp_says"], checked.lines[0]["erp_says"])
		# no item of the sheet is the ERP's, but its cost centre is: the sheet is compared, both ways
		_result, checked, points = against([("SE-1", "2026-09-30", "Hopper liner", "Nos", 3, 18000, "DDGS - T")], [("3. DDGS", bags, False)])
		eq(points, [("E1", CHECK, 1), ("E1", CHECK, 2), ("E3", CHECK, 0)])
	test("mst: an item the sheet writes another way than the ERP is still found", t_erp_names)

	def t_erp_fair():
		import json

		from trustbit_ethanol.ts_gate_entry import ts_ai_check_issues as issues

		ST, BO = "Stores - T", "Boiler - T"
		sheet = lambda *lines: [STORE_HEAD, *[[*line, "Shed", "x"] for line in lines], ["TOTAL", None, None, sum(line[3] or 0 for line in lines)]]  # noqa: E731
		rated = lambda *lines: [COAL_HEAD, *[[*line, "Shed", "x"] for line in lines], ["TOTAL", None, None, None, sum(line[4] or 0 for line in lines)]]  # noqa: E731

		def against(rows, *sheets):
			erp = {"month": "2026-09", "label": "September 2026", "company": "Test Co", "abbrs": ["T"], "problem": "",
				"rows": [dict(zip(("doc", "day", "name", "uom", "qty", "amount", "cc"), row)) for row in rows]}
			data = s1.xlsx(sheets=[(name, rows, False) for name, rows in sheets])
			_result, checked = mst.report(json.loads(json.dumps(mst.read_book(data, "xlsx"))), "xlsx", erp=json.loads(json.dumps(erp)))
			return checked, sorted((p["rule"], p["level"], p["line_no"]) for p in checked.findings if p["rule"].startswith("E"))

		first = lambda checked: next(p for p in checked.findings if p["rule"].startswith("E"))  # noqa: E731
		day = "2026-09-10"
		nut = ("SE-N", day, "Nut", "Nos", 20, 400, ST)
		# a name that holds another name is not that item when unit, quantity and money are another item's
		erp = [("SE-1", day, "Lithon EP-2", "Kg", 20, 6000, ST), ("SE-2", day, "Grease Nipple 1/4", "Nos", 12, 240, ST), nut]
		grease, nipple = ["Grease", "Kg", 20, 6000, ST], ["Grease Nipple", "Nos", 12, 240, ST]
		eq(against(erp, ("2. Stores", sheet(grease, nipple, nut[2:])))[1], [("E1", CHECK, 1), ("E3", CHECK, 0)])
		eq(against(erp, ("2. Stores", sheet(nipple, grease, nut[2:])))[1], [("E1", CHECK, 2), ("E3", CHECK, 0)], "whichever line comes first")
		# the item's own name under another cost centre does not stop the look for its other name under this one
		erp = [("SE-1", day, "Diesel", "Ltr", 100, 9000, ST), ("SE-2", day, "HSD (Diesel)", "Ltr", 50, 4500, BO), ("SE-3", day, "Nut", "Nos", 20, 400, BO)]
		checked, points = against(erp, ("2. Stores", sheet(["Diesel", "Ltr", 100, 9000, ST])),
			("3. Boiler", sheet(["Diesel", "Ltr", 50, 4500, BO], ["Nut", "Nos", 20, 400, BO])))
		eq(points, [])
		check(checked.lines[1]["erp_says"].endswith("The ERP writes the item 'HSD (Diesel)'."), checked.lines[1]["erp_says"])
		# names with the item code behind them: the month is right, and a difference among them is still found
		parts = ["Bolt", "Nut", "Washer", "Gasket", "Gear Oil", "Cotton Waste", "Hose Pipe", "Clamp", "Bush", "Coupling", "Flange", "Elbow"]
		erp = [(f"SE-{n}", day, name, "Nos", 10 + n, 100.0 * (n + 1), ST) for n, name in enumerate(parts)]
		coded = [[f"{name} - ITM-01{n:02d}", "Nos", 10 + n, 100.0 * (n + 1), ST] for n, name in enumerate(parts)]
		eq(against(erp, ("2. Stores", sheet(*coded)))[1], [], "twelve items, none written as the ERP writes it")
		coded[4][2] = 99
		eq(against(erp, ("2. Stores", sheet(*coded)))[1], [("E2", CHECK, 5)])
		# a line of nothing claims nothing; a line with no quantity is on the sheet all the same
		erp = [("SE-1", day, "Bolt", "Nos", 10, 500, ST), ("SE-2", "2026-08-05", "Washer", "Nos", 4, 80, ST), ("SE-3", day, "Gasket", "Nos", 2, 90, BO)]
		eq(against(erp, ("2. Stores", sheet(["Bolt", "Nos", 10, 500, ST], ["Nut", "Nos", 0, 0, ST], ["Washer", "Nos", 0, 0, ST],
			["Gasket", "Nos", 0, 0, ST])))[1], [], "three lines of 0 and no money")
		eq(against([("SE-1", day, "Bolt", "Nos", 10, 500, ST), nut], ("2. Stores", sheet(["Bolt", "Nos", None, 500, ST], nut[2:])))[1], [],
			"the bolts stand on a line of the sheet: the ERP's issue of them is not 'on no line'")
		# a cost centre is the same however its number, its dashes and the company's short name are typed
		eq({issues.cc_key(text, ["T"]) for text in ("Stores - T", "Stores T", "Stores – T", "3. Stores - T", "Stores - T.", "Stores (T)", "(3) Stores-T")},
			{"stores"})
		eq((issues.cc_key("Boiler - Bed", ["T"]), issues.cc_key("T", ["T"])), ("boiler bed", "t"))
		# allowances: half a rupee a line for amounts in whole rupees; a quantity only by the sheet's own lines
		erp = [(f"SE-{n}", day, "Oil", "Ltr", 1.5, 333.45, ST) for n in range(3)]
		eq(against(erp, ("2. Stores", sheet(*[["Oil", "Ltr", 1.5, 333, ST]] * 3)))[1], [], "three amounts rounded to the rupee")
		eq(against(erp, ("2. Stores", sheet(*[["Oil", "Ltr", 1.5, 333.05, ST]] * 3)))[1], [("E2", CHECK, 1)], "paise are written: one rupee, no more")
		erp = [(f"SE-{n}", day, "Caustic Soda", "Kg", 50, 2000, ST) for n in range(30)]
		checked, points = against(erp, ("2. Stores", rated(["Caustic Soda", "MT", 1.35, 44000, 59400, ST])))
		eq(points, [("E2", CHECK, 1)], "150 kg short over thirty issues is not a rounding")
		erp = [(f"SE-{n}", day, "Welding Rod", "Nos", 1, 10, ST) for n in range(200)]
		eq(against(erp, ("2. Stores", rated(["Welding Rod", "Nos", 199, 10, 1990, ST])))[1], [("E2", CHECK, 1)], "199 pieces are not 200")
		# a typed rate and another unit: nothing can be compared, and that is said, not passed
		checked, points = against([("SE-1", day, "Acid", "Litre", 80, 400, ST), nut], ("2. Stores", rated(["Acid", "Kg", 100, 5, 500, ST],
			["Nut", "Nos", 20, 20, 400, ST])))
		eq((points, first(checked)["title"]), ([("E2", CHECK, 1)], "Units differ from the ERP"))
		check("The same in both: 1. Differing: 1." in checked.coverage, checked.coverage)
		# two sizes are two items, however alike the names and the count; the same size typed another way is one
		erp = [("SE-1", day, "V Belt A-40", "Nos", 6, 5400, BO), ("SE-2", day, "Gasket", "Nos", 4, 800, BO)]
		eq(against(erp, ("3. Boiler", sheet(["V Belt B-52", "Nos", 6, 1500, BO], ["Gasket", "Nos", 4, 800, BO])))[1], [("E1", CHECK, 1), ("E3", CHECK, 0)])
		erp = [("SE-1", day, "V Belt B 52", "Nos", 2, 900, ST), ("SE-2", day, "V Belt B 65", "Nos", 3, 1500, ST), nut]
		eq(against(erp, ("2. Stores", sheet(["V Belts B-52", "Nos", 2, 900, ST], ["V Belts B-65", "Nos", 3, 1500, ST], nut[2:])))[1], [])
		# two lines after one item of the ERP: it goes to the one with its quantity
		erp = [("SE-1", day, "Bearing 6306", "Nos", 7, 700, ST), nut]
		eq(against(erp, ("2. Stores", sheet(["Bearing 6306 SKF", "Nos", 4, 400, ST], ["Bearing 6306 ZZ", "Nos", 7, 700, ST], nut[2:])))[1], [("E1", CHECK, 1)])
		# the line that has no issue of its own carries the point, wherever it stands
		erp = [("SE-3", day, "Bolt", "Nos", 4, 200, ST), ("SE-4", day, "Bolt", "Nos", 6, 300, ST), nut]
		checked, points = against(erp, ("2. Stores", sheet(["Bolt", "Nos", 4, 200, ST], nut[2:], ["Bolt", "Nos", 9, 450, ST])))
		eq(points, [("E2", CHECK, 3)])
		check("1 of the lines have an issue of their own; none for: 2. Stores row 4. Issues that no line matches: SE-4 (6 Nos, ₹300)."
			in first(checked)["detail"], first(checked)["detail"])
		eq([line["verdict"] for line in checked.lines], [OK, OK, CHECK])
		# a sheet's own cost centre keeps its list of what is left out, though another sheet has more lines of it
		erp = [("SE-1", day, "Bolt", "Nos", 1, 10, ST), ("SE-2", day, "Nut", "Nos", 2, 20, ST), ("SE-3", day, "Belt", "Nos", 3, 30, ST),
			("SE-4", day, "Hose", "Nos", 4, 40, ST), ("SE-5", day, "Coal", "MT", 5, 50, BO), ("SE-6", day, "Lime", "MT", 6, 60, BO),
			("SE-7", day, "Sand", "MT", 7, 70, BO), ("SE-8", day, "Ash", "MT", 8, 80, BO), ("SE-9", day, "Grate", "Nos", 9, 90, BO),
			("SE-10", day, "Nozzle", "Nos", 1, 500, BO)]
		lines = [[row[2], row[3], row[4], row[5], row[6]] for row in erp]
		checked, points = against(erp, ("2. Stores", sheet(*lines[:7])), ("3. Boiler", sheet(*lines[7:9])))
		eq(points, [("E3", CHECK, 0)])
		check(first(checked)["detail"].startswith("3. Boiler: Under 'Boiler - T' the ERP has 1 issue line(s)") and "Nozzle" in first(checked)["detail"],
			first(checked)["detail"])
		# another cost centre, another month: each named, each with its own figure
		erp = [("SE-1", "2026-08-01", "Grease", "Kg", 2, 200, ST), ("SE-2", "2026-08-02", "Grease", "Kg", 2, 200, ST),
			("SE-3", "2026-10-01", "Grease", "Kg", 2, 200, ST), nut]
		checked, points = against(erp, ("2. Stores", sheet(["Grease", "Kg", 2, 200, ST], nut[2:])))
		eq(points, [("E5", CHECK, 1)])
		check("The ERP has it on 1 Aug 2026, 2 Aug 2026: 4 Kg, ₹400 (SE-1, SE-2); on 1 Oct 2026: 2 Kg, ₹200 (SE-3)." in first(checked)["detail"],
			first(checked)["detail"])
		checked, points = against([("SE-1", "2026-10-02", "Grease", "Kg", 2, 200, BO), nut], ("2. Stores", sheet(["Grease", "Kg", 2, 200, ST], nut[2:])))
		check("The ERP has it on 2 Oct 2026 under 'Boiler - T': 2 Kg, ₹200 (SE-1)." in first(checked)["detail"], first(checked)["detail"])
		erp = [(f"SE-{n}", day, "Grease", "Kg", 2, 200, f"Unit {n} - T") for n in range(5)] + [nut]
		checked, points = against(erp, ("2. Stores", sheet(["Grease", "Kg", 2, 200, ST], nut[2:])))
		eq(points, [("E4", CHECK, 1)])
		check(first(checked)["detail"].endswith("; and 1 more cost centre(s)."), first(checked)["detail"])
	test("mst: the comparison with the ERP neither raises a false point nor passes a real difference", t_erp_fair)

	def t_months():
		import json

		from trustbit_ethanol.ts_gate_entry import ts_ai_check_review as review

		def run(data, earlier=None, erp=None, day="2026-09-30", of=1):
			# last month's figures reach the comparison the way the report stores them: as JSON text
			before = json.loads(json.dumps({"label": "August 2026", "report": "AICHK-T-1" if earlier else "", "now": earlier, "of": of if earlier else 0}))
			result, checked = mst.report(json.loads(json.dumps(mst.read_book(data, "xlsx"))), "xlsx", erp=erp, month={"day": day, "before": before})
			figures = json.loads(checked.figures)
			return result, checked, figures, sorted((p["rule"], p["level"], p["line_no"]) for p in checked.findings if p["rule"].startswith("P"))

		_result, checked, figures, points = run(book(), day="2026-08-31")
		august = figures["now"]
		eq((points, figures["trend"], checked.trend), ([], None, None), "the first month has nothing before it")
		check("No checked MST report of August 2026 for this company: nothing was compared month on month." in checked.coverage, checked.coverage)
		eq(figures["limits"], [
			"A bill, a hire charge, hours, diesel or petty cash typed into the sheet is no issue of the ERP: its arithmetic is checked, not whether its amount is right.",
			"No line of this file was compared with the issues recorded in the ERP.",
			"No checked MST report of August 2026 for this company: nothing was compared month on month.",
			"The AI's summary and the points marked AI are the AI's own reading: code checks that each figure in them is in this report, not that the conclusion is right.",
		], "the limits of the check are stored for the MD's report; the first month says it has nothing before it")
		eq((august["month"], august["litres"], august["total"], [(head["name"], head["amount"], head["sheet"]) for head in august["heads"]]),
			("2026-08", 100000.0, 7000.0, [("Coal", 3000.0, "1. COAL"), ("Stores", 1500.0, "2. Stores"), ("Electricity Bill", 2500.0, ""),
			("Depreciation", 900.0, "")]))
		eq(august["sheets"]["1. COAL"], {"total": 3000.0, "items": [["Coal A", "MT", 10.0, 2000.0, 1], ["Coal B", "MT", 5.0, 1000.0, 1]]})

		# coal costs half as much again for the same production
		dear = lambda **more: book(master(((1, "Coal", 4500, 4000), HEADS[1], HEADS[2]), **more),  # noqa: E731
			coal_rows=coal([["Coal A", "MT", 10, 350, 3500, "Boiler - T", "Boiler", "day"], ["Coal B", "MT", 5, 200, 1000, "Boiler - T", "Boiler", "night"]], 4500))
		result, checked, figures, points = run(dear(), august)
		eq(points, [("P1", CHECK, 0), ("P3", "Note", 2)])
		first = next(p for p in checked.findings if p["rule"] == "P1")
		eq((first["title"], first["refs"]), ("Cost per litre moved against last month", "AICHK-T-1"))
		eq(first["detail"], "'Coal': ₹3,000 (₹0.0300 a litre) in August 2026, ₹4,500 (₹0.0450 a litre) in this report: up 50% a litre. "
			"The items of its sheet that moved most: 'Coal A' ₹2,000 → ₹3,500.")
		trend = figures["trend"]
		eq(figures["limits"][2:4], [
			"A head's move against August 2026 raises a point only when it is 25% or more a litre and at least 1% of this report's total. "
			"A smaller move raises none; neither does the same money over more or fewer litres.",
			"A file sent under the wrong month is noticed only when its figures are the same as the other month's to the paisa.",
		], "with last month's report at hand the limits name the thresholds")
		eq((trend["before"], trend["report"], trend["by"], trend["litres"], trend["total"]), ("August 2026", "AICHK-T-1", "head", [100000.0, 100000.0], [7000.0, 8500.0]))
		eq([(row["head"], row["was"], row["now"], row["change"], row["flag"], row["outside"]) for row in trend["rows"]],
			[("Coal", 3000.0, 4500.0, 0.5, "moved", False), ("Stores", 1500.0, 1500.0, 0.0, "", False),
			("Electricity Bill", 2500.0, 2500.0, 0.0, "", False), ("Depreciation", 900.0, 900.0, 0.0, "", True)])
		# the Total row is the summary's own total: the head written under it is no part of it, and is marked
		eq((trend["total_row"]["was"], trend["total_row"]["now"], trend["total_row"]["change"], trend["total_row"]["said"]), (7000.0, 8500.0, 0.2143,
			"₹7,000 (₹0.0700 a litre) in August 2026, ₹8,500 (₹0.0850 a litre) in this report: up 21% a litre"))
		eq(trend["rows"][1]["said"], "₹1,500 (₹0.0150 a litre) in August 2026, ₹1,500 (₹0.0150 a litre) in this report: no change")
		check("Set against the MST report of August 2026 (AICHK-T-1): 4 head(s) in both, by cost per litre. A point is raised when one moved by "
			"25% or more and by at least ₹85 (1% of this report's total). Typed lines of ₹1,000 or more with last month's amount to the paisa: 1."
			in checked.coverage, checked.coverage)
		check(checked.summary.endswith("On a sheet or on the whole file: 0 Serious, 1 To check."), checked.summary)
		eq(run(dear(), august, erp=erp_as_file("Test Co", "2026-09-30"))[3], [("P1", CHECK, 0)], "a line the ERP has spoken of is no typed line")
		# twice the litres for the same money is the plant's doing, not a head's: the table shows it, no point is raised
		_result, checked, figures, points = run(book(master(litres=200000)), august)
		eq((points, figures["trend"]["rows"][0]["change"], figures["trend"]["rows"][0]["flag"]), ([("P3", "Note", 1), ("P3", "Note", 2)], -0.5, ""))
		# a jump that is small change
		tea = lambda amount: book(master((*HEADS, (4, "Tea", amount, amount))))  # noqa: E731
		eq(run(tea(20), run(tea(10), day="2026-08-31")[2]["now"])[3], [("P3", "Note", 1), ("P3", "Note", 2)], "twice the tea is ten rupees")
		# a head that is gone, a head that is new
		other = s1.xlsx(sheets=[("Master Sheet", master(((1, "Coal", 3000, 4000), HEADS[2], (4, "Chemicals", 800, 900))), False), ("1. COAL", coal(), False)])
		_result, checked, figures, points = run(other, august)
		eq(points, [("P2", "Note", 0), ("P2", CHECK, 0), ("P3", "Note", 1), ("P3", "Note", 2)])
		eq([(row["head"], row["flag"]) for row in figures["trend"]["rows"] if row["flag"]], [("Chemicals", "new"), ("Stores", "gone")])
		# one sheet sent by itself, both months: the sheets are compared by their totals
		alone = lambda rows: s1.xlsx(sheets=[("2. Stores", rows, False)])  # noqa: E731
		was = run(alone(stores()), day="2026-08-31")[2]["now"]
		more = stores([["Bolt", "Nos", 10, 2500, "Stores - T", "Shed", "a"], ["Nut", "Nos", 20, 400, "Stores - T", "Shed", "b"],
			["Belt", "Nos", 1, 600, "Stores - T", "Shed", "c"]], 3500)
		_result, checked, figures, points = run(alone(more), was)
		eq((points, figures["trend"]["by"], figures["trend"]["litres"], figures["trend"]["total"]), ([("P1", CHECK, 0)], "sheet", None, None))
		eq(next(p for p in checked.findings if p["rule"] == "P1")["detail"], "'2. Stores': ₹1,500 in August 2026, ₹3,500 in this report: up 133%. "
			"The items of its sheet that moved most: 'Bolt' ₹500 → ₹2,500.")
		_result, checked, figures, points = run(s1.xlsx(sheets=[("1. COAL", coal(), False)]), was)
		eq((points, figures["trend"]), ([], None))
		check("The MST report of August 2026 (AICHK-T-1) has none of this file's sheets" in checked.coverage, checked.coverage)

		# what the review of this step found
		detail = lambda got, title: [p["detail"] for p in got[1].findings if p["title"] == title]  # noqa: E731
		# two heads of one name are two rows: the one under the total doubles, the one above it stays
		two = lambda second: book(master(outside=((1, "Coal", second, 900),)))  # noqa: E731
		got = run(two(1800), run(two(900), day="2026-08-31")[2]["now"])
		eq([(row["head"], row["was"], row["now"], row["flag"], row["outside"]) for row in got[2]["trend"]["rows"] if row["head"] == "Coal"],
			[("Coal", 3000.0, 3000.0, "", False), ("Coal", 900.0, 1800.0, "moved", True)])
		eq(detail(got, "Cost per litre moved against last month"), ["'Coal': ₹900 (₹0.0090 a litre) in August 2026, ₹1,800 (₹0.0180 a litre) in this "
			"report: up 100% a litre."])
		# a head that stood at nothing: no percentage can say it, a point does
		hire = lambda amount: book(master((*HEADS, (4, "Hire", amount, 500))))  # noqa: E731
		got = run(hire(2000), run(hire(0), day="2026-08-31")[2]["now"])
		eq(detail(got, "Amount moved against last month"), ["'Hire': nothing in August 2026, ₹2,000 in this report."])
		eq([(row["change"], row["flag"]) for row in got[2]["trend"]["rows"] if row["head"] == "Hire"], [(None, "moved")])
		# a head renamed on the same sheet is the same head: its rise is raised, and nothing is 'gone' or 'new'
		renamed = book(master(((1, "Coal and Fuel", 4500, 4000), HEADS[1], HEADS[2])), coal_rows=coal([["Coal A", "MT", 10, 350, 3500, "Boiler - T",
			"Boiler", "day"], ["Coal B", "MT", 5, 200, 1000, "Boiler - T", "Boiler", "night"]], 4500))
		got = run(renamed, august)
		eq(got[3], [("P1", CHECK, 0), ("P3", "Note", 2)])
		check(detail(got, "Cost per litre moved against last month")[0].startswith("'Coal and Fuel': ₹3,000 (₹0.0300 a litre) in August 2026, ₹4,500"), str(got[3]))
		check("'Coal and Fuel' was taken as last month's 'Coal': it stands on the same sheet." in got[1].coverage, got[1].coverage)
		# a head whose amount shows an error is still a head: no 'gone' this month, no 'new' the next
		broken = book(master(fix={"Electricity Bill": {"amount": "#REF!"}}))
		got = run(broken, august)
		eq((got[3], [(row["was"], row["now"], row["flag"]) for row in got[2]["trend"]["rows"] if row["head"] == "Electricity Bill"]),
			([("P3", "Note", 1), ("P3", "Note", 2)], [(2500.0, None, "")]))
		eq([p for p in run(book(), got[2]["now"])[3] if p[0] != "P3"], [], "the month after the error")
		# the same file under two months
		got = run(book(), august)
		eq(detail(got, "Last month's report holds the same figures as this one"), ["Every head, and the litres, are the same to the paisa in this report "
			"and in the report of August 2026. Was one of the two files sent with the wrong month?"])
		# several reports of last month: the cover says which was used
		check("3 checked reports of August 2026 have figures; the one with the most heads was used." in run(dear(), august, of=3)[1].coverage,
			run(dear(), august, of=3)[1].coverage)
		# the ERP comparison did not run: no line is known to be a typed one, so none is called a repeated charge
		part = {**erp_as_file("Test Co", "2026-09-30"), "problem": "part"}
		got = run(dear(), august, erp=part)
		eq(got[3], [("P1", CHECK, 0)])
		check("none is known to be a typed line: repeated amounts were not looked for" in got[1].coverage, got[1].coverage)
		# a name typed in the file stands in quotes and is cut short inside a sentence of code
		long_name = "Coal A ₹2,000 → ₹2,000 (no change); Coal B ₹1,000 → ₹1,000 (no change). Not counted: freight"
		typed = book(master(((1, "Coal", 4500, 4000), HEADS[1], HEADS[2])), coal_rows=coal([[long_name, "MT", 10, 350, 3500, "Boiler - T", "Boiler", "day"],
			["Coal B", "MT", 5, 200, 1000, "Boiler - T", "Boiler", "night"]], 4500))
		check("'Coal A ₹2,000 → ₹2,000 (no change); Coal B ₹1,000 → ₹1,000 (…' ₹0 → ₹3,500" in detail(run(typed, august), "Cost per litre moved against last month")[0],
			detail(run(typed, august), "Cost per litre moved against last month")[0])

		# the AI's review: what it is given, and what is kept of its answer
		result, checked, figures, points = run(dear(), august)
		doc, seen = frappe._dict(name="AICHK-T-2", check_type=MST, company="Test Co", list_date="2026-09-30"), []

		def call(system, content, schema, **more):
			seen.append((system, content[0]["text"]))
			return s1.outcome({"summary": "Coal moved the cost a litre most: ₹3,000 then, ₹4,500 now. Over the year that is ₹9,99,999.", "points": [
				{"line_no": 0, "level": CHECK, "title": "Coal rose with the same quantity", "detail": "Coal A went from ₹2,000 to ₹3,500.", "refs": ["AICHK-T-1"]},
				{"line_no": 3, "level": "Note", "title": "Bolts under Stores", "detail": "Ten bolts for the shed.", "refs": []},
				{"line_no": 0, "level": CHECK, "title": "A sum of its own", "detail": "Together ₹77,777.", "refs": []},
				{"line_no": 99, "level": "Note", "title": "A line that is not there", "detail": "Nothing.", "refs": []},
				{"line_no": 1, "level": "Note", "title": "A document nobody gave", "detail": "See it.", "refs": ["SE-404"]}]})

		got = review.run(doc, result, checked, call)
		eq(got.summary, "Coal moved the cost a litre most: ₹3,000 then, ₹4,500 now.")
		eq([(p["line_no"], p["level"], p["source"], p["rule"], p["refs"]) for p in got.points], [(0, CHECK, "AI", "AI", "AICHK-T-1"), (3, "Note", "AI", "AI", "")])
		check(got.note.startswith("3 AI point(s) were left out") and "1 sentence(s) of the AI summary were left out" in got.note, got.note)
		eq(seen[0][0], review.SYSTEM_MST)
		head, facts = seen[0][1].split("\n", 1)
		facts = json.loads(facts)
		eq((head, facts["month"], facts["month_on_month"]["before"], facts["line_columns"], facts["lines"][0]), ("Review this checked MST report.",
			"September 2026", "August 2026", ["line_no", "sheet", "item", "the_sheet_says", "amount", "remark"],
			[1, "1. COAL", "Coal A", "10 MT · rate 350 · Boiler - T · Boiler", 3500.0, "day"]))
		eq(sorted(p["title"] for p in facts["points_by_code"]), ["Cost per litre moved against last month", "The same amount as last month"])
		eq(got.meta["model_used"], s1.MODEL, "the model that answered is the one recorded")
		check("month_on_month" in review.SYSTEM_MST and "at most 140 characters" in review.SYSTEM_MST and "at most 500" in review.SYSTEM_MST, "the limits are told")
		eq(facts["month_on_month"]["rows"][0]["said"], "₹3,000 (₹0.0300 a litre) in August 2026, ₹4,500 (₹0.0450 a litre) in this report: up 50% a litre")

		# a rate, a percentage or a quantity is held to the facts like an amount; what is true of them stays
		def kept(*sentences, points=()):
			answer = {"summary": " ".join(sentences), "points": [{"line_no": 0, "level": CHECK, "title": "A point", "detail": text, "refs": []} for text in points]}
			got = review.run(doc, result, checked, lambda *args, **more: s1.outcome(answer))
			return got.summary, [p["detail"] for p in got.points]

		true = ["Coal rose 50% a litre, from ₹0.0300 to ₹0.0450.", "Against Aug-26 only Coal moved.", "Lines 1,2 are the two coal lines.",
			"The whole report is ₹8,500, up 21% a litre against AICHK-T-1.", "Coal A is 10 MT at a rate of 350.", "Coal is ₹0.045 a litre now."]
		eq(kept(*true)[0], " ".join(true))
		for false in ("Stores rose 12% a litre.", "Coal is ₹0.19 a litre.", "Coal is ₹0.0310 a litre.", "Coal B is 750 MT.", "Coal is about ₹4.5 lakh.",
				"The same rise is in AICHK-T-10.", "Issue MAT-STE-2026-00077 covers it.", "The whole report is ₹86 a litre.",
				"Stores rose 12 percent a litre.", "Stores rose 12 per cent.", "Coal moved by rows 7,77,777."):
			eq(kept(true[0], false), (true[0], []), false)
			eq(kept(points=[false])[1], [], false)
		# a text over the limit is judged whole, then cut at a word and marked as cut
		long_text = "Coal A rose. " * 45 + "It is ₹3,500 now against ₹2,000."
		eq(kept(points=[long_text])[1], [("Coal A rose. " * 45)[:499].rsplit(" ", 1)[0].rstrip(" ,;:₹") + "…"])
		eq(kept(points=["Coal A rose. " * 45 + "It is ₹77,777 now."])[1], [], "a made-up figure past the limit is still seen")
		# many points of code: the first are given whole, the rest by their count, and the note says so
		kept_points, review.PACK_POINTS = review.PACK_POINTS, 1
		try:
			seen.clear()
			got = review.run(doc, result, checked, call)
			facts = json.loads(seen[0][1].split("\n", 1)[1])
			eq((len(facts["points_by_code"]), facts["more_points_by_title"]), (1, {"The same amount as last month": 1}))
			check("The AI was given the first 1 of the 2 points; the rest by their count." in got.note, got.note)
		finally:
			review.PACK_POINTS = kept_points
		# a pack that is too long is given fewer lines, never sent as it is
		kept_size, review.MAX_FACTS = review.MAX_FACTS, 10
		try:
			seen.clear()
			got = review.run(doc, result, checked, call)
			eq(len(json.loads(seen[0][1].split("\n", 1)[1])["lines"]), min(len(result.lines), 37))
		finally:
			review.MAX_FACTS = kept_size
		# no review: the note says so, and IT finds the reason in the Error Log (the title holds no text of the file)
		review.frappe = s1.AsSeen(log_error=s1.fake_review_log)
		s1.Fake.review_logged.clear()
		eq(review.run(doc, result, checked, lambda *args, **more: s1.outcome(ok=False, error="down", detail="prompt is too long")).note,
			review.NO_REVIEW, "the AI is down")
		eq(s1.Fake.review_logged, [("AI Checker: review AICHK-T-2", "prompt is too long")])
		eq(review.run(doc, result, checked, lambda *args, **more: s1.outcome({"summary": "x", "points": []}, model="another-model")).meta["model_used"],
			"another-model")
	test("mst: the report is set against last month's, and the AI reviews what code found", t_months)





def send(who, data, ext="xlsx", **over):
	"""`data` attached, saved and submitted by `who` as an MST report. Returns the submission's name."""
	return s1.submit(who, s1.draft(who, s1.attach(who, data, ext).url, **{"check_type": MST, **over}).name).name


def own(who, docname):
	"""The form load itself, as `who`: what reaches the browser under `__onload`."""
	from frappe.desk.form.load import getdoc

	frappe.set_user(who)
	try:
		frappe.response.docs = []
		getdoc(s1.SUB, docname)
		return frappe.response.docs[-1].get("__onload").get("own_points")
	finally:
		frappe.response.docs = []
		frappe.set_user("Administrator")


def bells(doctype, name, user):
	return frappe.get_all("Notification Log", filters={"document_type": doctype, "document_name": name, "for_user": user}, pluck="subject")


def _job(u):
	sto, stm, acc, md, blank = u.sto, u.stm, u.acc, u.md, u.blank
	wrong = book(master(((1, "Coal", 3000, 4000), (2, "Stores", 1400, 2000), HEADS[2]), fix={"Coal": {"surplus": 5}}),
		store_rows=stores(total=1400))  # F1 on the Stores sheet, F9 on the summary

	def t_who_sends():
		for at, who in enumerate((sto, stm, acc)):  # three different files: the same file is not checked twice at once
			name = send(who, book(extra=[("note", [["copy", at]], False)]))
			eq(s1.state(name).status, "Being checked", f"{who} sends an MST report")
		url = s1.attach(sto, s1.xlsx(s1.listing()), "xlsx").url
		s1.refused(lambda: s1.draft(sto, url), ACCOUNTS_ONLY)
		s1.refused(lambda: s1.draft(sto, url, check_type="Payment List"), ACCOUNTS_ONLY)
		kept = s1.draft(sto, s1.attach(sto, book(), "xlsx").url, check_type=MST).name
		s1.refused(lambda: s1.submit(sto, kept, check_type="Payment List"), ACCOUNTS_ONLY)
		s1.refused(lambda: s1.draft(blank, s1.attach(blank, book(), "xlsx").url, check_type=MST), exc=s1.DENIED)
		check(s1.draft(acc, s1.attach(acc, s1.xlsx(s1.listing()), "xlsx").url).name, "Accounts still saves a payment list")
	test("perm: Stores and Accounts send an MST report; a payment list stays with Accounts", t_who_sends)

	def t_wrong_kind():
		s1.refused(lambda: send(acc, s1.xlsx(s1.listing())), NOT_MST)
		s1.refused(lambda: send(acc, s1.typed_pdf(), "pdf"), EXCEL_ONLY)
		url = s1.attach(acc, book(), "xlsx").url
		s1.refused(lambda: s1.submit(acc, s1.draft(acc, url).name), s1.M11)
	test("job: a file of the other kind is refused at Submit", t_wrong_kind)

	def t_report():
		name = send(sto, wrong, list_date="2026-09-30")
		s1.run_job(name)
		s = s1.state(name)
		eq((s.status, s.status_note, s.lines_read, s.model_used, s.cost_usd), ("Report sent", None, 7, None, 0))
		eq([call.system for call in s1.Fake.calls], [REAL.review.SYSTEM_MST], "one AI call: the review of the checked report")
		r = frappe.get_doc(REPORT, name)
		eq((r.check_type, r.submitted_by, str(r.list_date), r.read_by, r.lines_total, r.list_total, r.n_ok, r.n_check, r.n_serious),
			(MST, sto, "2026-09-30", "Code", 7, 7900.0, 7, 0, 0))  # the lines as written: the wrong total is a point, not a line
		eq(sorted((f.rule, f.level, f.line_no, f.source) for f in r.findings), [("F1", SERIOUS, 0, "Rule"), ("F9", CHECK, 0, "Rule")])
		eq([(row.section_text, row.supplier_text, row.verdict, row.kind) for row in r.lines][:2],
			[("1. COAL", "Coal A", OK, "Other"), ("1. COAL", "Coal B", OK, "Other")])
		check(r.summary.endswith("On a sheet or on the whole file: 1 Serious, 1 To check."), r.summary)
		eq((r.ai_summary or "", r.ai_note), ("", REAL.review.NO_REVIEW), "the AI gave no answer: the report says so and is whole without it")
		eq((s1.can(md, REPORT, "read", r), s1.can(sto, REPORT, "read", r), s1.can(acc, REPORT, "read", r)), (True, False, False),
			"the report is the MD's")
		eq((s1.can(sto, s1.SUB, "read", name), s1.can(stm, s1.SUB, "read", name), s1.can(acc, s1.SUB, "read", name)), (True, False, False),
			"a submission is its sender's own")
	test("job: an MST report is checked by code and stored as a report for the MD", t_report)

	def t_bells():
		name = send(sto, wrong, list_date="2026-09-30")
		s1.run_job(name)
		eq(bells(REPORT, name, md), ["MST report September 2026 is checked: 7 lines. 0 Serious, 0 To check, 7 with no issue. "
			"1 Serious point(s) on a sheet or the whole file."])
		eq(bells(s1.SUB, name, sto), ["Your MST report of September 2026 is checked. 2 point(s) for you to look at."])
		clean = send(acc, book(), list_date="2026-09-01")
		s1.run_job(clean)
		eq(bells(s1.SUB, clean, acc), ["Your MST report of September 2026 is checked. No point for you to correct."])
	test("job: the MD's bell and the uploader's bell name the month, with counts only", t_bells)

	def t_own_points():
		name = send(sto, wrong)
		s1.run_job(name)
		got = own(sto, name)
		eq([(p["line_no"], p["level"], p["title"]) for p in got],
			[(0, SERIOUS, "A total does not match its lines"), (0, CHECK, "Surplus is not budget less amount")])
		check(got[0]["detail"].startswith("2. Stores: Row 5 (TOTAL) says ₹1,400"), got[0]["detail"])
		lines = [["Coal A", "MT", 10, 200, 2100, "Boiler - T", "Boiler", "day"], ["Coal B", "MT", 5, 200, 1000, "Boiler - T", "Boiler", "night"]]
		on_line = send(sto, book(master(((1, "Coal", 3100, 4000),) + HEADS[1:]), coal_rows=coal(lines, 3100)))
		s1.run_job(on_line)
		eq([(p["line_no"], p["supplier"], p["amount"], p["title"]) for p in own(sto, on_line)], [(1, "Coal A", "₹2,100", "Amount is not quantity × rate")])
		eq(s1.state(on_line).status, "Report sent")
		eq(frappe.db.get_value(REPORT, on_line, ["n_check", "amt_check"]), (1, 2100.0))
		frappe.set_user(md)
		try:
			from frappe.desk.form.load import getdoc

			frappe.response.docs = []
			getdoc(s1.SUB, name)
			check("own_points" not in (frappe.response.docs[-1].get("__onload") or {}), "the MD's form of it carries no such list")
		finally:
			frappe.response.docs = []
			frappe.set_user("Administrator")
	test("own: the uploader's form carries the points of their workbook", t_own_points)

	def t_daily_limit():
		midnight = frappe.utils.now_datetime().strftime("%Y-%m-%d 00:00:00")
		today = lambda kind: frappe.db.count(s1.SUB, {"docstatus": 1, "check_type": kind, "submitted_at": (">=", midnight)})  # noqa: E731
		note = lambda words: [("note", [[words]], False)]  # noqa: E731
		s1.setting(max_checks_per_day=1000)
		s1.waiting(acc)
		send(sto, book(extra=note("one of each kind")))
		if today(MST) <= today("Payment List"):
			for n in range(today("Payment List") - today(MST) + 1):
				send(sto, book(extra=note(f"more MST reports than payment lists {n}")))
		lists, reports = today("Payment List"), today(MST)
		check(0 < lists < reports, f"{lists} payment lists and {reports} MST reports today")
		s1.setting(max_checks_per_day=reports)
		s1.refused(lambda: send(stm, book(extra=note("at the limit"))), s1.M17.format(reports))
		eq(s1.state(s1.sent(acc, s1.xlsx(s1.listing(3)), "xlsx")).status, "Being checked", "a payment list still goes: the MST reports did not use up its limit")
		s1.setting(max_checks_per_day=lists + 1)
		s1.refused(lambda: s1.sent(acc, s1.xlsx(s1.listing(4)), "xlsx"), s1.M17.format(lists + 1))
	test("job: each kind of file has the day's limit to itself", t_daily_limit)

	def t_print():
		name = send(sto, wrong, list_date="2026-09-30")
		s1.run_job(name)
		layout = frappe.db.get_value("Print Format", "TS AI Check Report", "html")
		page = frappe.render_template(layout, {"doc": frappe.get_doc(REPORT, name)})
		for words in ("MST report check", "Month September 2026", "Item on the sheet", "The sheet says", "10 MT · rate 200 · Boiler - T · Boiler"):
			check(words in page, f"the print has: {words}")
		check("Payment list check" not in page and "Supplier on the list" not in page, "and none of the payment list's words")
	test("job: the MD's print of an MST report speaks of sheets and items", t_print)

	def t_payment_untouched():
		name = s1.waiting(acc)
		s1.run_job(name)
		eq((s1.state(name).status, frappe.db.get_value(REPORT, name, "check_type")), ("Report sent", "Payment List"))
		check(bells(s1.SUB, name, acc)[0].startswith("Your payment list of "), "the payment list keeps its own words")
		check(bells(REPORT, name, md)[0].startswith("Payment list "), "and so does the MD's bell")
	test("job: a payment list goes the way it went before", t_payment_untouched)

	def t_form_words():
		here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "doctype")
		form = open(os.path.join(here, "ts_ai_check_submission", "ts_ai_check_submission.js")).read()
		view = open(os.path.join(here, "ts_ai_check_report", "ts_ai_check_report.js")).read()
		for words in ('"MST Report"', "Month of the report", "MST report file", "check_type(frm)"):
			check(words in form, f"the form script has {words}")
		check('doc.check_type === "MST Report"' in view and "The sheet says" in view, "the MD's view knows an MST report")
		eq(frappe.get_meta(s1.SUB).get_field("check_type").options, "Payment List\nMST Report")
	test("own: the form and the MD's view speak of an MST report", t_form_words)

	def t_erp_job():
		issues.fetch = lambda company, day: erp_as_file(company, day, {"SE-T4": {"qty": 25, "amount": 500}, "SE-T1": {"amount": 1500}})
		name = send(sto, book(), list_date="2026-09-30")
		s1.run_job(name)
		r = frappe.get_doc(REPORT, name)
		eq(sorted((f.rule, f.level, f.line_no) for f in r.findings), [("E2", CHECK, 4), ("E6", "Note", 1)])
		eq((r.n_ok, r.n_check, r.n_serious, r.lines[3].verdict, r.lines[0].verdict), (6, 1, 0, CHECK, OK))
		eq(r.lines[3].erp_says, "ERP, September 2026, under 'Stores - T': 25 Nos, ₹500 on 1 issue line(s) (SE-T4).")
		check("Set against the ERP's Material Issues of September 2026" in r.coverage, r.coverage)
		got = own(sto, name)
		eq([(p["line_no"], p["level"], p["title"], p["supplier"]) for p in got], [(4, CHECK, "Quantity differs from the ERP", "Nut")],
			"the uploader sees the difference, and not the note on the rate")
		eq(bells(s1.SUB, name, sto), ["Your MST report of September 2026 is checked. 1 point(s) for you to look at."])
		eq(len(s1.Fake.calls), 1, "one AI call: the review")
	test("job: the report carries what the ERP says on each line, and the uploader sees what differs", t_erp_job)

	def t_erp_read():
		keys = {"doc", "day", "name", "uom", "qty", "amount", "cc"}
		last = frappe.db.sql("select max(posting_date) from `tabStock Entry` where docstatus = 1 and purpose = 'Material Issue' and company = %s",
			s1.COMPANY)[0][0]
		got = REAL.fetch(s1.COMPANY, last or "2026-09-30")
		eq((got["problem"], got["company"], got["month"]), ("", s1.COMPANY, frappe.utils.getdate(last or "2026-09-30").strftime("%Y-%m")))
		check(all(set(row) == keys for row in got["rows"]), "every issue line has its seven fields")
		if last:
			month = [row for row in got["rows"] if row["day"][:7] == got["month"]]
			asked = frappe.db.sql("""select count(*) from `tabStock Entry Detail` d join `tabStock Entry` s on s.name = d.parent
				where s.docstatus = 1 and s.purpose = 'Material Issue' and s.company = %s and date_format(s.posting_date, '%%Y-%%m') = %s""",
				(s1.COMPANY, got["month"]))[0][0]
			eq(len(month), asked, "every submitted issue line of the month, and no other")
			lo, hi = frappe.utils.add_months(frappe.utils.get_first_day(last), -1), frappe.utils.get_last_day(frappe.utils.add_months(last, 1))
			check(all(str(lo) <= row["day"] <= str(hi) for row in got["rows"]), "nothing outside the three months")
		import json

		json.dumps(got)  # texts, numbers and lists only
		eq(REAL.fetch(s1.COMPANY, "1990-01-15")["rows"], [], "a month with no issue")
		frappe.set_user(blank)
		try:
			eq((REAL.fetch(s1.COMPANY, last or "2026-09-30")["problem"], REAL.fetch(s1.COMPANY, last or "2026-09-30")["rows"]), ("access", []),
				"someone with no right on stock entries gets none of them")
		finally:
			frappe.set_user("Administrator")
		busy = frappe.db.sql("""select min(s.posting_date) from `tabStock Entry Detail` d join `tabStock Entry` s on s.name = d.parent
			where s.docstatus = 1 and s.purpose = 'Material Issue' and s.company = %s group by date_format(s.posting_date, '%%Y-%%m')
			order by count(distinct s.name) desc limit 1""", s1.COMPANY)
		if busy and len(REAL.fetch(s1.COMPANY, busy[0][0])["rows"]) > 1:
			busy = busy[0][0]
			one = frappe.db.get_value("Stock Entry", {"docstatus": 1, "purpose": "Material Issue", "company": s1.COMPANY, "posting_date": busy}, "name")
			seen = len([row for row in REAL.fetch(s1.COMPANY, busy)["rows"] if row["doc"] == one])
			frappe.set_user(sto)
			try:
				eq(REAL.fetch(s1.COMPANY, busy)["problem"], "", "a Stores user reads the company's issues")
			finally:
				frappe.set_user("Administrator")
			if seen < len(REAL.fetch(s1.COMPANY, busy)["rows"]):
				# someone who may read one stock entry only: what they would be told of "the ERP" is true of that entry alone
				perm = frappe.get_doc({"doctype": "User Permission", "user": sto, "allow": "Stock Entry", "for_value": one,
					"apply_to_all_doctypes": 1}).insert(ignore_permissions=True)
				frappe.set_user(sto)
				try:
					got = REAL.fetch(s1.COMPANY, busy)
					eq((got["problem"], got["rows"]), ("part", []), "a part of the issues is no ERP to compare with")
				finally:
					frappe.set_user("Administrator")
					perm.delete(ignore_permissions=True)
				frappe.set_user(sto)
				try:
					eq(REAL.fetch(s1.COMPANY, busy)["problem"], "", "and with the limit gone, all of them again")
				finally:
					frappe.set_user("Administrator")
			kept, issues.MAX_ISSUES = issues.MAX_ISSUES, 0
			try:
				got = REAL.fetch(s1.COMPANY, busy)
				eq((got["problem"], got["rows"]), ("many", []), "more lines than the limit: refused, never cut")
			finally:
				issues.MAX_ISSUES = kept

		def broken(company, day):
			raise RuntimeError("the ERP could not be read")

		issues.fetch = broken
		name = send(sto, book(extra=[("note", [["a failed read"]], False)]), list_date="2026-09-30")
		if name:
			s1.run_job(name)
			eq((s1.state(name).status, bool(frappe.db.exists(REPORT, name))), ("Check failed", False),
				"the ERP cannot be read: the check fails whole, and no half report is kept")
		issues.fetch = REAL.fetch
		name = send(sto, book(extra=[("note", [["the real read"]], False)]), list_date="1990-01-15")
		if name:
			s1.run_job(name)
			eq(sorted((f.rule, f.level) for f in frappe.get_doc(REPORT, name).findings), [("E7", CHECK)], "the job itself reads the ERP")
	test("job: the ERP's issue lines are read whole, for three months, with the reader's own access", t_erp_read)

	def t_months_job():
		import json

		first = send(sto, book(extra=[("note", [["march"]], False)]), list_date="2031-03-31")
		s1.run_job(first)
		r = frappe.get_doc(REPORT, first)
		check("No checked MST report of February 2031 for this company" in r.coverage, r.coverage)
		eq((json.loads(r.figures)["trend"], json.loads(r.figures)["now"]["month"], r.ai_summary or "", r.ai_note), (None, "2031-03", "", REAL.review.NO_REVIEW))
		dear = book(master(((1, "Coal", 4500, 4000), HEADS[1], HEADS[2])),
			coal_rows=coal([["Coal A", "MT", 10, 350, 3500, "Boiler - T", "Boiler", "day"], ["Coal B", "MT", 5, 200, 1000, "Boiler - T", "Boiler", "night"]], 4500))
		s1.Fake.calls.clear()
		# a part of March's workbook is sent after the whole file: the whole file stays the one April is set against
		part = send(sto, s1.xlsx(sheets=[("2. Stores", stores(), False)]), list_date="2031-03-20")
		s1.run_job(part)
		eq(json.loads(frappe.get_doc(REPORT, part).figures)["now"]["heads"], [])
		from trustbit_ethanol.ts_gate_entry import ts_ai_check_months

		before = ts_ai_check_months.before(frappe.db.get_value(REPORT, first, "company"), "2031-04-30")
		eq((before["report"], before["of"], before["label"]), (first, 2, "March 2031"))
		s1.Fake.calls.clear()
		s1.Fake.answer = lambda content: s1.outcome({"summary": "Coal is behind the rise: ₹3,000 then, ₹4,500 now. Next year ₹8,88,888. Stores rose 12%.",
			"points": [{"line_no": 0, "level": CHECK, "title": "Coal rose with the same quantity", "detail": "Coal A went from ₹2,000 to ₹3,500.", "refs": [first]},
			{"line_no": 0, "level": CHECK, "title": "A sum of its own", "detail": "Together ₹77,777.", "refs": []}]}, model="model-that-answered")
		name = send(acc, dear, list_date="2031-04-30")
		s1.run_job(name)
		r = frappe.get_doc(REPORT, name)
		trend = json.loads(r.figures)["trend"]
		eq((trend["report"], trend["before"], trend["rows"][0]["flag"], trend["total"]), (first, "March 2031", "moved", [7000.0, 8500.0]))
		eq(sorted((f.rule, f.level, f.line_no, f.source) for f in r.findings if f.rule[0] in "PA"), [("AI", CHECK, 0, "AI"), ("P1", CHECK, 0, "Rule")],
			"last month's report is the one this one is set against; the coal lines are the ERP's, so none is a typed line")
		eq(r.ai_summary, "Coal is behind the rise: ₹3,000 then, ₹4,500 now.")
		check("1 AI point(s) were left out" in r.ai_note and "2 sentence(s) of the AI summary were left out" in r.ai_note, r.ai_note)
		check("2 checked reports of March 2031 have figures; the one with the most heads was used." in r.coverage, r.coverage)
		s = s1.state(name)
		eq((s.status, s.model_used, s.cost_usd, s.input_tokens, s.output_tokens), ("Report sent", "model-that-answered", 0.0221, 1300, 300),
			"the review's use is recorded, under the model that answered")
		eq([call.system for call in s1.Fake.calls], [REAL.review.SYSTEM_MST])
		eq([p["title"] for p in own(acc, name)], [], "the month comparison and the AI's points are the MD's, not the uploader's")
		check(any(subject.endswith("1 point(s) to check on a sheet or the whole file.") for subject in bells(REPORT, name, md)),
			"the bell counts code's point, not the AI's: " + str(bells(REPORT, name, md)))
		printed = frappe.render_template(frappe.db.get_value("Print Format", "TS AI Check Report", "html"), {"doc": frappe.get_doc(REPORT, name)})
		check(frappe.render_template("{{ first | e }} &middot; sent by {{ frappe.get_fullname(sto) | e }} on ", {"first": first, "sto": sto}) in printed,
			"the print names who sent last month's report, and when")
		# the print endpoint also renders a document made from the caller's own JSON: such a document gets no look-up at all
		layout = frappe.db.get_value("Print Format", "TS AI Check Report", "html")
		for asked in (first, {"name": first, "check_type": "MST Report"}, {"name": first, "check_type": "Payment List"}):
			forged = frappe.get_doc({"doctype": "ToDo", "description": "x", "name": "X", "lines": [], "findings": [], "list_date": "2031-04-30",
				"check_type": "MST Report", "submitted_on": "2031-04-30 10:00:00", "list_total": 0, "lines_total": 0, "n_ok": 0, "n_check": 0,
				"n_serious": 0, "amt_ok": 0, "amt_check": 0, "amt_serious": 0, "figures": json.dumps({"trend": {"before": "x", "report": asked,
				"rows": [{"head": "x", "was": None, "now": None, "flag": "", "change": None}]}})})
			page = frappe.render_template(layout, {"doc": forged})
			check("Against the MST report of x" in page and "sent by" not in page and "no longer in the system" not in page,
				f"a document that is no report learns nothing of a report: {asked}")
		limits = json.loads(r.figures)["limits"]
		eq((len(limits), limits[0][:7], "not compared" in " ".join(limits)), (4, "A bill,", False), "the coal lines were set against the ERP: no limit says otherwise")
		check("<b>Limits of this check.</b>" in printed and all(frappe.render_template("<b>{{ t | e }}</b>", {"t": text}) in printed for text in limits),
			"the print states every limit in bold")
		check("To check (AI): Coal rose with the same quantity." in printed and "₹ 0.0450" in printed and "+21%" in printed, "the print marks the AI's point "
			"on the whole file, writes a rate below ₹1 with four decimals and fills the Total row's change")
		# a file sent again for the same month is set against March once more, never against itself
		again = send(sto, book(extra=[("note", [["april again"]], False)]), list_date="2031-04-15")
		s1.run_job(again)
		eq(json.loads(frappe.get_doc(REPORT, again).figures)["trend"]["report"], first)
	test("job: a report keeps its figures, the next month's is set against them, and the AI's review is stored for the MD", t_months_job)


def run():
	global ai, mst, reader, issues
	frappe.init(site=s1.SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	frappe.local.conf.disable_global_search = 1
	from trustbit_ethanol.ts_gate_entry import ts_ai_check, ts_ai_check_issues, ts_ai_check_mst, ts_ai_check_reader, ts_ai_check_review

	ai, mst, reader, issues = ts_ai_check, ts_ai_check_mst, ts_ai_check_reader, ts_ai_check_issues
	REAL.update(review=ts_ai_check_review, fetch=ts_ai_check_issues.fetch)
	ts_ai_check_review.frappe = s1.AsSeen(log_error=s1.fake_review_log)  # the review's own Error Log rows are recorded, not written
	s1.ai, s1.reader = ts_ai_check, ts_ai_check_reader
	s1.KEPT.update({name: getattr(ai, name) for name in s1.SWAPPED})
	ai.READS_PER_HOUR = 10 ** 6
	frappe.db.commit = lambda *args, **kwargs: s1.Fake.commits.append(sys._getframe(1).f_code.co_name)
	users = frappe._dict()
	try:
		calm()
		s1.COMPANY = frappe.db.get_value("Company", {}, "name")
		users.update(sto=s1.user("sto", ["Stores User"]), stm=s1.user("stm", ["Stores Manager"]), acc=s1.user("acc", ["Accounts User"]),
			md=s1.user("md", ["MD"]), blank=s1.user("blank", []))
		s1.baseline()
		for label, group in (("mst", _read), ("job", lambda: _job(users))):
			try:
				group()
			except Exception as e:  # noqa: BLE001 — one group's broken stage must not hide the other group
				RESULTS.append((f"{label}: the group ran to its end", False, str(e)[:300]))
				print(f"  FAIL  {label}: the group stopped early :: {type(e).__name__} {str(e)[:300]}")
				calm()
		test("job: nothing in this run asked the database for a real commit", lambda: eq(s1.Fake.commits, []))
	finally:
		for name, value in s1.KEPT.items():
			setattr(ai, name, value)
		issues.fetch = REAL.fetch
		del frappe.db.commit
		frappe.set_user("Administrator")
		frappe.db.rollback()
		s1.tidy(users)
	failed = [r for r in RESULTS if not r[1]]
	for group in ("mst", "perm", "job", "own"):
		rows = [r for r in RESULTS if r[0].startswith(group + ":")]
		print(f"  {group}: {sum(1 for r in rows if r[1])}/{len(rows)}")
	print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
	sys.exit(1 if failed else 0)


if __name__ == "__main__":
	run()
