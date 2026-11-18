"""AI Checker, MST report, step 2: the lines of the workbook set against the ERP's Material Issues of the month.

An MST line carries no ERP document number, item code or date, so a line is matched by its cost centre and
its item NAME inside the month of the report. Rules E1 to E7; code only, no AI.

`fetch` reads the ERP in the submitter's own name (the job runs as them): what they may not read is not
read, and someone who may read only a part gets no comparison at all. `compare` is plain arithmetic over
what `fetch` gave, and everything `fetch` returns is texts, numbers and lists."""

import re
from collections import defaultdict
from difflib import SequenceMatcher

import frappe
from frappe.utils import add_months, cstr, flt, formatdate, get_first_day, get_last_day, getdate

from trustbit_ethanol.ts_gate_entry.ts_ai_check_rules import CHECK, NOTE, money

MAX_ISSUES = 20000  # issue lines of three months; a month has some 700. More is refused, never cut
SHOWN = 12  # items named in one point; the count and the money of the rest are always said
AMOUNT_TOL = 1.0  # rupees
QTY_TOL = 0.0051  # a quantity copied to two decimals may be half a hundredth off, on every line of the sheet
UNITS = {"no": "nos", "nos": "nos", "pc": "nos", "pcs": "nos", "piece": "nos", "pieces": "nos", "each": "nos", "unit": "nos",
	"kg": "kg", "kgs": "kg", "kilogram": "kg", "mt": "mt", "ton": "mt", "tons": "mt", "tonne": "mt",
	"ltr": "l", "ltrs": "l", "litre": "l", "litres": "l", "liter": "l", "l": "l", "mtr": "m", "meter": "m", "metre": "m", "m": "m"}
PER = {("kg", "mt"): 0.001, ("mt", "kg"): 1000.0}  # (the ERP's unit, the sheet's unit): one of the first in the second


def cc_key(text, abbrs=()):
	"""'21 - DDGS - BBPL', '21. DDGS (BBPL)', 'DDGS – BBPL' and 'ddgs' are one cost centre: the number in front and
	the company's short name at the end are no part of the name."""
	text = re.sub(r"\s+", " ", cstr(text).replace("–", "-").replace("—", "-")).strip().lower()
	text = re.sub(r"^\(?\d{1,3}\)?\s*[-.):]\s*", "", text).rstrip(" .")
	for abbr in abbrs:
		cut = re.sub(r"(\s*-\s*|\s+|\s*\(\s*)" + re.escape(cstr(abbr).lower()) + r"\s*\)?$", "", text) if abbr else text
		if cut != text and cut:
			text = cut
			break
	return re.sub(r"[^a-z0-9]+", " ", text).strip()


def item_key(text):
	return re.sub(r"[^a-z0-9]+", "", cstr(text).lower())


def unit_key(text):
	"""'' for a unit that is blank or shows an error: nothing is known of it."""
	text = "" if cstr(text).strip().startswith("#") else re.sub(r"[^a-z]+", "", cstr(text).lower())
	return UNITS.get(text, text)


def _fig(value):
	return f"{value:,.3f}".rstrip("0").rstrip(".")


def _tokens(text):
	return {word[:-1] if word.endswith("s") and len(word) > 3 else word for word in re.findall(r"[a-z0-9]+", cstr(text).lower())}


def _sizes(text):
	return set(re.findall(r"\d+", cstr(text)))


def _per(rows, unit):
	"""What one unit of these ERP lines is in the sheet's `unit` (1 when they are the same, or the sheet names none);
	None when the ERP lines are in several units, or in another one."""
	theirs = {unit_key(row["uom"]) for row in rows}
	if len(theirs) != 1:
		return None
	own = next(iter(theirs))
	return 1.0 if not unit or own == unit else PER.get((own, unit))


def _like(name, qty, unit, lines, pool):
	"""The one item of `pool` ({item key: its ERP lines}) that is `name` written another way, or None.
	By the name: one holds the other, as a whole or word for word ('PAC' and 'Poly Aluminium Chloride (PAC)'),
	and no size (number) of the shorter one is missing in the longer; or the same sizes and a letter or two
	apart. A unit that is another unit rules a candidate out, unless only a number sets the two names apart.
	Of several candidates the one with exactly the sheet's sizes, then with the sheet's quantity, is taken.
	Failing all that: the same quantity in the same unit under a name that shares a word and names no other
	size. `lines` is the number of sheet lines the quantity is added up from."""
	item, mine, sizes = item_key(name), _tokens(name), _sizes(name)
	same_qty = lambda rows: qty is not None and _per(rows, unit) is not None and abs(  # noqa: E731
		sum(row["qty"] for row in rows) * _per(rows, unit) - qty) <= QTY_TOL * lines
	near = []
	for key, rows in pool.items():
		other, theirs = _tokens(rows[0]["name"]), _sizes(rows[0]["name"])
		if not key or not other or not mine:
			continue
		wordless = lambda words: {word for word in words if not word.isdigit()}  # noqa: E731
		if _per(rows, unit) is None and wordless(mine) != wordless(other):
			continue
		held = (min(len(key), len(item)) >= 6 and (key in item or item in key)) or mine <= other or other <= mine
		if (held and (sizes <= theirs or theirs <= sizes)) or (sizes == theirs and SequenceMatcher(None, key, item).ratio() >= 0.9):
			near.append(key)
	if len(near) > 1:
		near = [key for key in near if _sizes(pool[key][0]["name"]) == sizes] or near
	if len(near) > 1:
		near = [key for key in near if same_qty(pool[key])]
		near = near if len(near) == 1 else [None]
	if near:
		return near[0]
	if qty is None or (qty <= 5 and float(qty).is_integer()):
		return None  # one piece, two pieces: the same count proves nothing
	shared = lambda rows: {word for word in mine & _tokens(rows[0]["name"]) if len(word) >= 3}  # noqa: E731
	apart = lambda rows: bool(sizes and _sizes(rows[0]["name"]) and not (sizes <= _sizes(rows[0]["name"]) or _sizes(rows[0]["name"]) <= sizes))  # noqa: E731
	same = [key for key, rows in pool.items() if key and same_qty(rows) and not apart(rows)
		and (shared(rows) or SequenceMatcher(None, key, item).ratio() >= 0.6)]
	return same[0] if len(same) == 1 else None


def fetch(company, day):
	"""Every submitted Material Issue line of the company, from the month before the report's month to the
	month after it. `problem` says why there is nothing to compare with."""
	day = getdate(day)
	out = {"month": day.strftime("%Y-%m"), "label": formatdate(day, "MMMM yyyy"), "company": cstr(company), "rows": [],
		"abbrs": [abbr for abbr in [frappe.db.get_value("Company", company, "abbr")] if abbr], "problem": ""}
	if not frappe.has_permission("Stock Entry", "read"):
		out["problem"] = "access"
		return out
	from trustbit_ethanol.ts_gate_entry.report import report_utils as ru

	# Frappe hands the user-permission clause over with every "%" already doubled, as a query run with values needs it
	base = ["se.docstatus = 1", "se.purpose = 'Material Issue'", "se.company = %(company)s", "se.posting_date BETWEEN %(lo)s AND %(hi)s"]
	own = ru.conf_match_clauses("Stock Entry", "se")
	tables = "`tabStock Entry` se JOIN `tabStock Entry Detail` sed ON sed.parent = se.name AND sed.parenttype = 'Stock Entry'"
	values = {"company": company, "lo": get_first_day(add_months(day, -1)), "hi": get_last_day(add_months(day, 1))}
	rows = frappe.db.sql(f"""
		SELECT se.name AS doc, se.posting_date AS day, sed.item_name AS name, sed.item_code AS code, sed.stock_uom AS uom,
			sed.transfer_qty AS qty, sed.amount AS amount, sed.cost_center AS cc
		FROM {tables}
		WHERE {" AND ".join([*base, *own])}
		ORDER BY se.posting_date, se.name, sed.idx
		LIMIT {MAX_ISSUES + 1}""", values, as_dict=True)
	if len(rows) > MAX_ISSUES:
		out["problem"] = "many"
		return out
	# someone whose access leaves a part of the issues out would be told of "the ERP" what is true of that part
	# only: a bare count says whether that is so, and then nothing is compared
	if own and frappe.db.sql(f"SELECT COUNT(*) FROM {tables} WHERE {' AND '.join(base)}", values)[0][0] != len(rows):
		out["problem"] = "part"
		return out
	out["rows"] = [{"doc": cstr(row.doc), "day": cstr(row.day), "name": cstr(row.name or row.code), "uom": cstr(row.uom),
		"qty": flt(row.qty), "amount": flt(row.amount), "cc": cstr(row.cc)} for row in rows]
	return out


def compare(lines, erp, whole=False):
	"""`lines` are the workbook's lines as `check_book` gives them; `whole` says the file has its summary sheet.
	Returns the points, what the ERP says for each line ({line_no: text}), the sentences for the cover,
	`found`: the lines whose item the ERP issued in the month (their quantity is judged against it), and
	`ran`: False when nothing could be compared (rule E7)."""
	out = frappe._dict(points=[], says={}, coverage=[], found=set(), ran=True)
	label, abbrs = erp["label"], erp.get("abbrs") or ()

	def add(level, rule, title, detail, line_no=0, sheet="", refs=()):
		out.points.append({"line_no": line_no, "level": level, "source": "Rule", "rule": rule, "title": title,
			"detail": (f"{sheet}: " if sheet and not line_no else "") + detail, "refs": ", ".join(list(dict.fromkeys(refs))[:20])})

	stop = {"access": "The person who submitted the file has no access to stock entries, so its lines were not set against the "
		"ERP's Material Issues.", "many": f"The ERP holds more than {MAX_ISSUES:,} issue lines around {label}: too many to compare. "
		"Its lines were not set against the ERP's Material Issues.", "part": "The person who submitted the file may read only a "
		"part of the company's stock entries, so its lines were not set against the ERP's Material Issues: a line could be "
		"called missing that is only out of their view."}.get(erp.get("problem"))
	month = [row for row in erp["rows"] if row["day"][:7] == erp["month"]]
	if not stop and not month:
		stop = (f"The ERP holds no Material Issue of {erp['company']} dated {label}, so nothing could be compared. "
			"Is the month of the report, and the company, right?")
	if stop:
		add(CHECK, "E7", "Not compared with the ERP", stop)
		out.coverage.append("The lines were not compared with the issues recorded in the ERP.")
		out.ran = False
		return out

	# what the ERP has: by cost centre and item for the month, by item for the month, and the months beside it
	in_cc, in_month, beside, cc_name, cc_beside = defaultdict(list), defaultdict(list), defaultdict(list), {}, {}
	for row in erp["rows"]:
		row["cc_key"], row["item"] = cc_key(row["cc"], abbrs), item_key(row["name"])
		if row["day"][:7] == erp["month"]:
			in_cc[(row["cc_key"], row["item"])].append(row)
			in_month[row["item"]].append(row)
			cc_name.setdefault(row["cc_key"], row["cc"])
		else:
			beside[row["item"]].append(row)
			cc_beside.setdefault(row["cc_key"], row["cc"])
	known = set(in_month) | set(beside)

	# the lines that can be compared: an item with a quantity, in a main table. A line of 0 and no money says nothing.
	# A sheet where no line names an item the ERP issued, nor a cost centre it issued to, is a sheet of typed
	# expenses (a bill, petty cash) and is left alone
	fit = lambda line: bool(line["main"] and not line.get("summary") and line["qty"] is not None and item_key(line["name"])  # noqa: E731
		and (line["qty"] or line["amount"]))
	able = [line for line in lines if fit(line)]
	# an item named on a line that is not compared is on the sheet all the same: its issues are not "on no line"
	unsaid = {item_key(line["name"]) for line in lines if not line.get("summary") and not fit(line)} - {""}
	sheets = defaultdict(list)
	for line in able:
		sheets[line["sheet"]].append(line)
	typed = [name for name, rows in sheets.items() if not any(item_key(line["name"]) in known or cc_key(line["cc"], abbrs) in cc_name for line in rows)]
	able = [line for line in able if line["sheet"] not in typed]
	if not able:
		out.coverage.append(f"No line of this file names an item the ERP issued around {label}: nothing was compared with the ERP.")
		return out
	# an item that stands on a line with no cost centre is compared over all cost centres together
	anywhere = {item_key(line["name"]) for line in able if not cc_key(line["cc"], abbrs)}
	groups, as_typed = defaultdict(list), {}
	for line in able:
		item, key = item_key(line["name"]), cc_key(line["cc"], abbrs)
		as_typed.setdefault(key, line["cc"])
		groups[("" if item in anywhere else key, item)].append(line)
	shown_cc = lambda key: cc_name.get(key) or cc_beside.get(key) or as_typed.get(key) or key  # noqa: E731

	# first every item by its own name; then, for what is left, the one item the ERP writes another way. Every
	# group names its candidate before any is taken: two groups after one item get it only by the same quantity
	theirs_of = {key: (in_cc.get(key, []) if key[0] else in_month.get(key[1], [])) for key in groups}
	taken, written, wanted = {id(row) for found in theirs_of.values() for row in found}, {}, {}
	total = lambda rows: sum(line["qty"] for line in rows)  # noqa: E731
	one_unit = lambda rows: next(iter({unit_key(line["uom"]) for line in rows})) if len({unit_key(line["uom"]) for line in rows}) == 1 else None  # noqa: E731
	for (cc, item), rows in groups.items():
		# the item's own name in a month beside is the better answer (E5) than a look-alike in this one
		if theirs_of[(cc, item)] or any(not cc or row["cc_key"] == cc for row in beside.get(item, ())):
			continue
		pool = defaultdict(list)
		for row in month:
			if id(row) not in taken and (not cc or row["cc_key"] == cc):
				pool[row["item"]].append(row)
		pick = _like(rows[0]["name"], total(rows), one_unit(rows) or "", len(rows), pool) if one_unit(rows) is not None else None
		if pick:
			wanted[(cc, item)] = (pick, pool[pick])
	for key, (pick, found) in wanted.items():
		rivals = [other for other, (theirs, rows) in wanted.items() if theirs == pick and {id(row) for row in rows} & {id(row) for row in found}]
		if len(rivals) > 1:
			unit = one_unit(groups[key])
			exact = [other for other in rivals if _per(wanted[other][1], one_unit(groups[other])) is not None and abs(
				sum(row["qty"] for row in wanted[other][1]) * _per(wanted[other][1], one_unit(groups[other])) - total(groups[other]))
				<= QTY_TOL * len(groups[other])]
			if exact != [key] or unit is None:
				continue
		theirs_of[key], written[key] = found, found[0]["name"]
		taken.update(id(row) for row in found)
	hit = sum(1 for key in groups if theirs_of[key])
	if len(groups) >= 10 and hit * 5 < len(groups):
		add(CHECK, "E7", "Not compared with the ERP", f"Only {hit} of the {len(groups)} items of this file were found among the ERP's "
			f"issues of {label}. The lines do not look like that month's issues, so they were not compared one by one. Is the month "
			"of the report right?")
		out.coverage.append("The lines were not compared with the issues recorded in the ERP.")
		out.ran = False
		return out

	def said(rows, to=None):
		"""Quantity and money of ERP lines, as words; the quantity in the sheet's unit when the two are known."""
		units = {unit_key(row["uom"]) for row in rows}
		unit = rows[0]["uom"] if len(units) == 1 else ""
		qty = sum(row["qty"] for row in rows) if len(units) == 1 else None
		if qty is not None and to and (next(iter(units)), to) in PER:
			qty, unit = qty * PER[(next(iter(units)), to)], to.upper() if to == "mt" else to
		return qty, unit, sum(row["amount"] for row in rows)

	def docs(rows):
		names = list(dict.fromkeys(row["doc"] for row in rows))
		return ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")

	def words(qty, unit, amount):
		return (f"{_fig(qty)} {'' if cstr(unit).startswith('#') else unit}".strip() + ", " if qty is not None else "") + money(amount)

	def at(rows):
		return ", ".join(f"{line['sheet']} row {line['row']}" for line in rows[:8]) + (f" and {len(rows) - 8} more" if len(rows) > 8 else "")

	def also(rows, first, name, why):
		# every line the point is about is marked, not only the one that carries its words
		for line in rows:
			if line is not first:
				add(CHECK, why[0], why[1], f"{line['sheet']} row {line['row']}: {name}: {why[2]} See the point on line {first['line_no']}.", line["line_no"])

	counts = {"same": 0, "differ": 0, "none": 0}
	for (cc, item), rows in groups.items():
		first, name = rows[0], rows[0]["name"]
		place = f"under '{shown_cc(cc)}'" if cc else "under any cost centre"
		unit = one_unit(rows)
		qty, amount = total(rows), sum(line["amount"] or 0 for line in rows)
		priced = any(line["rate"] is not None for line in rows)  # the sheet works its amount out from a typed rate
		# amounts written in whole rupees may be half a rupee off on every line
		slack = max(AMOUNT_TOL, 0.51 * len(rows)) if all(float(line["amount"] or 0).is_integer() for line in rows) else AMOUNT_TOL
		theirs = theirs_of[(cc, item)]
		if theirs:
			per = _per(theirs, unit) if unit is not None else None
			their_qty, their_unit, their_amount = said(theirs, unit)
			alias = f" The ERP writes the item '{written[(cc, item)]}'." if (cc, item) in written else ""
			text = f"ERP, {label}, {place}: {words(their_qty, their_unit, their_amount)} on {len(theirs)} issue line(s) ({docs(theirs)}).{alias}"
			refs = [row["doc"] for row in theirs]
			many = f"on {len(rows)} lines " if len(rows) > 1 else ""
			basis = lambda where: (f"{where}: {name}: {many}the sheet says {words(qty, first['uom'], amount)}; the ERP's issues of "  # noqa: E731
				f"{label} {place} come to {words(their_qty, their_unit, their_amount)} ({len(theirs)} issue line(s): {docs(theirs)}).{alias}")
			amount_off = abs(amount - their_amount) > slack
			if per is None:  # other units, or several: only the money can be set side by side
				text += " The units differ, so the quantity was not compared."
				if priced or amount_off:
					counts["differ"] += len(rows)
					add(CHECK, "E2", "Amount differs from the ERP" if not priced else "Units differ from the ERP", basis(at(rows))
						+ (" The units differ and the sheet's amount is worked out from a typed rate: neither could be compared."
						if priced else f" The amount differs by {money(abs(amount - their_amount))}. The units differ, so the "
						"quantity was not compared."), first["line_no"], refs=refs)
					also(rows, first, name, ("E2", "Units differ from the ERP" if priced else "Amount differs from the ERP", "one of the lines of this item."))
				else:
					counts["same"] += len(rows)
			else:
				out.found.update(line["line_no"] for line in rows)
				# which lines have an issue of their own (the same quantity, and money unless it is from a typed rate)
				spare, loose = list(theirs), []
				for line in rows:
					mate = next((row for row in spare if abs(row["qty"] * per - line["qty"]) <= QTY_TOL
						and (priced or abs(row["amount"] - (line["amount"] or 0)) <= AMOUNT_TOL)), None)
					spare.remove(mate) if mate else loose.append(line)
				qty_off = abs(qty - their_qty) > QTY_TOL * len(rows)
				if qty_off or (amount_off and not priced):
					odd = loose or [first]
					counts["differ"] += len(odd)
					counts["same"] += len(rows) - len(odd)
					more = ""
					if loose and len(loose) < len(rows):
						more += f" {len(rows) - len(loose)} of the lines have an issue of their own; none for: {at(loose)}."
					if spare and len(spare) < len(theirs):
						more += f" Issues that no line matches: {docs(spare)} ({words(*said(spare, unit))})."
					title = "Quantity differs from the ERP" if qty_off else "Amount differs from the ERP"
					add(CHECK, "E2", title, basis(f"{odd[0]['sheet']} row {odd[0]['row']}") + more + (
						f" The quantity differs by {_fig(abs(qty - their_qty))}."
						+ (" The amount is worked out from a typed rate and was not compared." if priced else "")
						if qty_off else f" The amount differs by {money(abs(amount - their_amount))}."), odd[0]["line_no"], refs=refs)
					also(odd, odd[0], name, ("E2", title, "this line has no issue of its own in the ERP."))
				else:
					counts["same"] += len(rows)
					if amount_off:
						add(NOTE, "E6", "Valued at another rate than the ERP", basis(at(rows)) + " The quantity is the same; the sheet's "
							"amount is worked out from a typed rate.", first["line_no"], refs=refs)
			for line in rows:
				out.says[line["line_no"]] = text[:1000]
			continue
		counts["none"] += len(rows)
		whose = f"{at(rows)}: {name} ({words(qty, first['uom'], amount)}): no Material Issue of an item of this name {place} in {label}."
		other = in_month.get(item, [])
		if other:  # only reached with a cost centre: the item was issued in the month, under another one
			by_cc = defaultdict(list)
			for row in other:
				by_cc[row["cc_key"]].append(row)
			# the same quantity and money under ONE other cost centre: that issue is this line, and not one left out there
			equal = [found for found in by_cc.values() if _per(found, unit or "") is not None
				and abs(sum(row["qty"] for row in found) * _per(found, unit or "") - qty) <= QTY_TOL * len(rows)
				and abs(sum(row["amount"] for row in found) - amount) <= slack]
			if len(equal) == 1:
				taken.update(id(row) for row in equal[0])
			seen = "; ".join(f"'{shown_cc(key)}': {words(*said(found, unit))} ({docs(found)})" for key, found in list(by_cc.items())[:4]) + (
				f"; and {len(by_cc) - 4} more cost centre(s)" if len(by_cc) > 4 else "")
			why = ("E4", "Issued under another cost centre in the ERP", "one of the lines of this item.")
			add(CHECK, why[0], why[1], whose + f" The ERP has it under {seen}.", first["line_no"], refs=[row["doc"] for row in other])
			text = f"ERP, {label}: no issue {place}; issued under {seen}."
		elif beside.get(item):
			parts = defaultdict(list)
			for row in beside[item]:
				parts[(row["day"][:7], row["cc_key"])].append(row)
			near = {key: found for key, found in parts.items() if not cc or key[1] == cc} or parts
			day = lambda row: getdate(row["day"]).strftime("%d %b %Y").lstrip("0")  # noqa: E731
			seen = "; ".join(f"on {', '.join(dict.fromkeys(day(row) for row in found[:3]))}{' and other days' if len({row['day'] for row in found}) > 3 else ''}"
				+ ("" if cc and key[1] == cc else f" under '{shown_cc(key[1])}'") + f": {words(*said(found, unit))} ({docs(found)})"
				for key, found in list(near.items())[:4]) + (f"; and {len(near) - 4} more" if len(near) > 4 else "")
			why = ("E5", "Issued in another month in the ERP", "one of the lines of this item.")
			add(CHECK, why[0], why[1], whose + f" The ERP has it {seen}.", first["line_no"], refs=[row["doc"] for found in near.values() for row in found])
			text = f"ERP: no issue {place} in {label}; issued {seen}."
		else:
			why = ("E1", "No issue in the ERP", "one of the lines of this item.")
			add(CHECK, why[0], why[1], whose + " Nor in the month before or the month after.", first["line_no"])
			text = f"ERP: no issue of an item of this name {place} in {label}, nor in the month before or after."
		also(rows, first, name, why)
		for line in rows:
			out.says[line["line_no"]] = text[:1000]

	# what the ERP issued under a sheet's cost centre and the sheet does not show: said once for a cost centre, on the
	# sheet that has it as its own (most used) cost centre; of several such sheets, the one with most lines of it
	used, homes, few = {}, {}, []
	for name, rows in sheets.items():
		if name in typed:
			continue
		used[name] = defaultdict(int)
		for line in rows:
			if cc_key(line["cc"], abbrs):
				used[name][cc_key(line["cc"], abbrs)] += 1
	own = {name: max(counted, key=counted.get) for name, counted in used.items() if counted}
	for name, key in own.items():
		if used[name][key] > homes.get(key, ("", 0))[1]:
			homes[key] = (name, used[name][key])
	few = sorted({shown_cc(key) for counted in used.values() for key in counted if key not in homes})
	for key, (name, _count) in homes.items():
		left = [row for row in month if row["cc_key"] == key and id(row) not in taken and row["item"] not in anywhere and row["item"] not in unsaid]
		if not left:
			continue
		items = defaultdict(list)
		for row in left:
			items[row["item"]].append(row)
		ranked = sorted(items.values(), key=lambda rows: -sum(row["amount"] for row in rows))
		shown = "; ".join(f"{rows[0]['name']} ({words(*said(rows))}, {docs(rows)})" for rows in ranked[:SHOWN])
		rest = ranked[SHOWN:]
		add(CHECK, "E3", "Issued in the ERP, not on the sheet", f"Under '{shown_cc(key)}' the ERP has {len(left)} issue line(s) "
			f"of {label} ({money(sum(row['amount'] for row in left))}) of {len(ranked)} item(s) that stand on no line of this sheet: {shown}"
			+ (f"; and {len(rest)} more item(s) ({money(sum(row['amount'] for rows in rest for row in rows))})" if rest else "") + ".",
			sheet=name, refs=[row["doc"] for rows in ranked[:SHOWN] for row in rows])

	out.coverage.append(f"Set against the ERP's Material Issues of {label} ({erp['company']}: {len(month)} issue line(s), "
		f"{money(sum(row['amount'] for row in month))}), by cost centre and item name: {len(able)} line(s) on "
		f"{len(sheets) - len(typed)} sheet(s). The same in both: {counts['same']}. Differing: {counts['differ']}. "
		f"With no issue in that month: {counts['none']}.")
	out.coverage.append("Not set against the ERP: lines with no quantity or of zero, tables beside the main one, a machine table, heads "
		"taken from the summary" + (", and the sheets where no line names an item the ERP issued (" + ", ".join(f"'{name}'" for name in typed[:12])
		+ ")" if typed else "") + ". Bills, hours, diesel and petty cash are not issues of the ERP.")
	if few:
		out.coverage.append("Cost centres named on a few lines only, for which the ERP's other issues were not listed: "
			+ ", ".join(f"'{name}'" for name in few[:8]) + ".")
	if whole:
		named = {cc_key(line["cc"], abbrs) for line in able}
		spare = defaultdict(list)
		for row in month:
			if row["cc_key"] not in named and row["item"] not in anywhere:
				spare[row["cc_key"]].append(row)
		if spare:
			ranked = sorted(spare.items(), key=lambda entry: -sum(row["amount"] for row in entry[1]))
			out.coverage.append(f"The ERP also has issues of {label} under cost centres that no line of this file names: "
				+ "; ".join(f"'{shown_cc(key)}' ({len(rows)} line(s), {money(sum(row['amount'] for row in rows))})" for key, rows in ranked[:10])
				+ (f"; and {len(ranked) - 10} more" if len(ranked) > 10 else "") + ".")
	return out
