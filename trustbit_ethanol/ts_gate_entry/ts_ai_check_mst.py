"""AI Checker, MST report: reading the monthly cost workbook and checking its own arithmetic.

An MST workbook is a summary sheet (one row per cost head) and one sheet per head. A head's sheet lists
what was issued or spent: item name, quantity, amount, cost centre. No line carries an ERP document
number, an item code or a date, so nothing here looks a line up by number.

`read_book` runs in the reader's own limited process (no site, no database): it finds every table on
every sheet by its column titles, never by a sheet's name or position, so one sheet sent on its own (a
"section") is read the same way as the whole workbook. `check_book` is plain arithmetic on what was
read: totals against their lines, the summary against the sheets, per-litre figures against the litres.

The cell values are the ones Excel last calculated and saved. A formula that was never calculated has no
value, and is reported as such."""

import json
import re
from itertools import combinations

import frappe

from trustbit_ethanol.ts_gate_entry import ts_ai_check_reader as reader

OK, CHECK, SERIOUS, NOTE = "No issue found", "To check", "Serious", "Note"
MAX_LINES = 2000  # lines kept from one workbook, all sheets together
MAX_TOTALS = 500  # total rows kept from one workbook: far more than its sheets and blocks can have
MAX_TABLES = 60  # title rows of tables on one sheet
MAX_ERRORS = 200  # cells showing an error value that are kept from one sheet
TOL = 0.50  # rupees: two sums that differ by less are the same sum
# column titles as the preparers write them, lower case with dots and extra spaces removed
TITLES = {
	"name": ("item name", "particular", "particulars", "party name", "materials name"),
	"amount": ("grand total", "amount", "total amount"),
	"qty": ("out qty",),
	"uom": ("stock uom",),
	"rate": ("unit rate",),
	"cc": ("cost center", "cost centre"),
	"place": ("define use & location", "define in use location"),
	"remark": ("item remark",),
	"bill": ("bill no",),
}
SUMMARY_TITLES = {"head": ("cost center", "cost centre"), "amount": ("amount",), "per_litre": ("per ltr", "per litre"),
	"budget_litre": ("budget per litre",), "budget": ("total for month",), "surplus": ("surplus/deficit",), "sr": ("srno", "sr no")}
# "Total", "TOTAL :", "Grand Total", "Sub Total", "Net Total", "Total Rs.", "Total (A)", "Total Amount (Rs.)"; never an item's name
TOTAL_WORDS = re.compile(r"^(grand |g |net |sub[ -]?)?totals?( amount| amt)?( ?\(? ?(rs|inr|₹|[a-z]) ?\)?)?[ :=-]*$", re.I)
# any other label of a total row, taken as one only on a row with no unit and no cost centre: "Total Cost",
# "Total of Coal", "Gross Total", "Sheet Total", "Grand Total (A+B)", "Tot.", "T O T A L", "Sum"
LOOSE_TOTAL = re.compile(r"^((grand|g|net|sub|gross|sheet|final|overall) ?-? ?)?(totals?|totel|tot|t o t a l)( |$|[(:=/-])|^sum( total)?$| total$")
GRAND_WORDS = ("grand", "g ", "net", "gross", "sheet", "final", "overall")
SKIP_WORDS = re.compile(r"^(per\s+l(i)?t(re|r)\s+cost.*|cost\s+per\s+l(i)?t(re|r).*|balance|per\s+ltr\s+ethanol.*|production\s*\(?\s*(in\s+)?(ltr|litres?)\s*\)?)$", re.I)
NOTHING = ("-", "–", "—", "--", "nil", "na", "n/a", "n a")
LITRES_WORDS = re.compile(r"total\s+production", re.I)
SHEET_NO = re.compile(r"^\s*\(?(\d{1,2}(?:\.\d{1,2})?)(?!\d)[\s.)_:\-]*")  # "8.DG Fuel", "5.1 Boiler", "1-Stores", "(1) Coal"
TOO_LARGE = "The file is too large to read. Send one sheet at a time."
# a machine table: one row per machine, one group of columns per user of the machines, these titles under each group title
MATRIX = {"hours": ("hours", "hrs", "hour", "hr", "hours/diesel", "hrs/diesel", "working hours"), "days": ("days", "day"), "rate": ("rate",),
	"amt": ("amt", "hire amount", "hire amt", "hire charges", "hire"), "per_hour": ("diesel p/h", "diesel per hour", "diesel/hour", "diesel/hr"),
	"diesel": ("total diesel", "diesel", "diesel qty", "diesel ltr"), "diesel_rate": ("d rate", "diesel rate"), "amount": ("amount",),
	"total": ("total amount", "total amt", "total")}
MATRIX_ROLE = {name: role for role, names in MATRIX.items() for name in names}
MATRIX_SUMS = ("hours", "days", "amt", "diesel", "amount", "total")  # columns a total row adds up; a rate is never added
MATRIX_UNIT = re.compile(r" ?\( ?(in )?(rs|inr|₹|ltrs?|litres?|hrs?|hours?|days?) ?\)$| ?(/|per) ?(hr|hour|day|ltr|litre)s?$| rs$")
MAX_MACHINES = 300
MAX_COLS = 130  # columns read from a sheet that holds a machine table: one runs to some 90
NARROW = reader.MAX_COLS + 1  # cells of a row that any other sheet is read to


def _norm(cell):
	text = str(cell if cell is not None else "")[:200].replace("\xa0", " ").replace(".", " ")
	return re.sub(r"\s+", " ", text).strip().lower()


def _blank(cell):
	return cell is None or not str(cell).replace("\xa0", " ").strip()


def _number(cell):
	"""A cell as a number, None when it holds none. Excel's error values ("#REF!") are not numbers."""
	if isinstance(cell, bool) or _blank(cell):
		return None
	if isinstance(cell, (int, float)):
		return float(cell) if abs(cell) < reader.MAX_AMOUNT else None
	return reader.parse_amount(cell, places=6)  # a quantity or a per-litre figure written as text keeps its decimals


def _error(cell):
	return isinstance(cell, str) and cell.strip().startswith("#")


def _nothing(cell):
	"""An empty cell, or one that says nothing was spent: "-", "NIL"."""
	return _blank(cell) or (isinstance(cell, str) and _norm(cell) in NOTHING)


def _cell(row, col):
	"""A cell as Excel names it: row 2 of the second column is B2."""
	letters, col = "", col + 1
	while col:
		col, rest = divmod(col - 1, 26)
		letters = chr(65 + rest) + letters
	return f"{letters}{row}"


def _fig(value):
	"""A quantity or a rate as it was typed: no exponent, no trailing zeros."""
	return f"{value:.4f}".rstrip("0").rstrip(".")


def _text(cell, limit=140):
	return reader._text(cell, limit)


def _runs(cells):
	"""A row cut into its stretches of filled cells: two tables side by side have a blank column between."""
	runs, start = [], None
	for i, cell in enumerate([*cells, None]):
		if _blank(cell):
			if start is not None:
				runs.append((start, i))
				start = None
		elif start is None:
			start = i
	return runs


def _titles(cells, lo, hi, wanted, last=("amount",)):
	"""Which columns of cells[lo:hi] carry the wanted titles. The first match wins, except for the fields
	in `last`: a ledger with two amount columns keeps the last."""
	found = {}
	for i in range(lo, min(hi, len(cells))):
		key = _norm(cells[i])
		for field, names in wanted.items():
			if key in names and (field not in found or field in last):
				found[field] = i
				break
	return found


def _summary_head(cells):
	# the first "Amount" is the summary's own: another table may stand to its right on the same row
	found = _titles(cells, 0, len(cells), SUMMARY_TITLES, last=())
	return found if {"head", "amount", "per_litre"} <= set(found) else None


def _table_heads(cells):
	"""The tables whose title row this is: (first column, one past the last, columns by field)."""
	heads, runs, whole = [], [], _runs(cells)
	for nth, (lo, hi) in enumerate(whole):
		# a second table set against the first with no blank column: its name title follows the first one's amount,
		# and it has an amount title of its own (further on, or alone past a merged title cell)
		after = _titles(cells, *whole[nth + 1], TITLES) if nth + 1 < len(whole) else {}
		cut, paid = lo, False
		for i in range(lo, hi):
			key = _norm(cells[i])
			if key in TITLES["amount"]:
				paid = True
			elif paid and key in TITLES["name"] and (any(_norm(cells[j]) in TITLES["amount"] for j in range(i + 1, hi))
					or ("amount" in after and "name" not in after)):
				runs.append((cut, i))
				cut, paid = i, False
		runs.append((cut, hi))
	for nth, (lo, hi) in enumerate(runs):
		cols = _titles(cells, lo, hi, TITLES)
		if "amount" in cols and "name" not in cols and nth:
			# "Item Name" merged over two columns leaves an empty title cell: the name is in the stretch before
			before = _titles(cells, *runs[nth - 1], TITLES)
			if "name" in before and "amount" not in before:
				cols, lo = {**before, **cols}, runs[nth - 1][0]
		if "amount" in cols and ("name" in cols or "qty" in cols):
			heads.append((lo, hi, cols))
	return heads


def _read_table(rows, start, title, lo, hi, cols, stop_rows, begin=0, end=None):
	"""The lines and total rows under one title row (`title`), down to the next title row over the same
	columns. `begin` and `end` are where the tables to its left and right stand: nothing of theirs is read."""
	lines, totals, skipped, after_line = [], [], 0, False
	name_col, amount_col = cols.get("name", lo), cols["amount"]
	edge = max(hi, amount_col + 1) + 2
	edge = edge if end is None else min(edge, end)
	# past the last title, only a column with no title of its own is this table's (a remark column left untitled)
	span = [i for i in range(lo, edge) if i < hi or i >= len(title) or _blank(title[i])]
	# a total's label stands left of the amount, at times one column left of the table (under an untitled serial column)
	labels = range(max(begin, lo - 1), amount_col) if amount_col > name_col else span
	for row_no, cells in rows[start:]:
		if row_no in stop_rows:
			break
		cell = lambda col: cells[col] if 0 <= col < len(cells) else None  # noqa: E731
		if all(_blank(cell(i)) for i in span):
			continue
		at = lambda field: cell(cols[field]) if field in cols else None  # noqa: E731
		beside = lambda col: cell(col) if col in span else None  # noqa: E731
		label = next((_text(cell(i), 60) for i in labels if isinstance(cell(i), str) and not _blank(cell(i))), "")
		name, amount = at("name") if "name" in cols else cell(name_col), at("amount")
		said = [_norm(label), _norm(name) if isinstance(name, str) else ""]
		worded = any(isinstance(at(field), str) and not _blank(at(field)) for field in ("uom", "cc", "place", "remark", "bill"))
		words = next((w for w in said if TOTAL_WORDS.match(w)), "") or ("" if worded else next((w for w in said if LOOSE_TOTAL.search(w)), ""))
		if words and re.search(r"lakh|lacs?( |$|\))|crore", words):
			skipped += 1  # the total written again in lakhs
			continue
		if words:
			value = _number(amount)
			if _blank(amount):  # typed under another block's amount column: the one figure of the row that is no quantity
				spare = {_number(cell(i)) for i in span if i not in (cols.get("qty"), cols.get("rate"), cols.get("uom"))} - {None}
				value = spare.pop() if len(spare) == 1 else None
			if value is None and not _error(amount) and not TOTAL_WORDS.match(words):
				skipped += 1  # "Total Qty": a total of something that is not money
				continue
			totals.append({"row": row_no, "label": _text(label or name, 40), "amount": value, "error": _error(amount),
				"grand": words.startswith(GRAND_WORDS)})
			after_line = False
			continue
		if any(SKIP_WORDS.match(w) for w in said):
			continue
		if _blank(name) and after_line and worded and (_number(amount) is not None or _error(amount)):
			name = lines[-1]["name"]  # a name merged down several rows is kept in the first only; a bare sum row has no words
		unread = not _nothing(amount) and _number(amount) is None  # "#REF!", or words where the figure should be
		if _blank(name) or (_number(amount) is None and not unread and _number(at("qty")) is None):
			skipped += 1
			continue
		qty, uom, rate, cc = at("qty"), at("uom"), at("rate"), at("cc")
		if _number(qty) is None and _number(uom) is not None:  # the two titles are swapped on some sheets
			qty, uom = uom, qty
		if "cc" not in cols and isinstance(rate, str) and _number(rate) is None and not _error(rate):
			cc, rate = rate, None  # a block with no rate keeps the cost centre in the rate's column
		after = cols.get("cc", amount_col) + 1
		place = at("place") if "place" in cols else (beside(after) if "cc" in cols else None)
		remark = at("remark") if "remark" in cols else (beside(after + 1) if "cc" in cols else None)
		why = "amount" if _error(amount) else "text" if unread else "quantity" if _error(qty) else ""
		lines.append({"row": row_no, "name": _text(name), "qty": _number(qty), "uom": _text(uom, 20), "rate": _number(rate),
			"amount": _number(amount), "written": _text(amount, 40), "error": why,
			"cc": "" if _error(cc) else _text(cc, 80), "place": _text(place, 80), "remark": _text(remark), "bill": _text(at("bill"), 40)})
		after_line = True
	return lines, totals, skipped


def _read_summary(rows, start, cols):
	"""The summary sheet's rows: heads, the row that totals them, heads listed under the total, and a last
	total row under those. A head whose amount shows an error is kept, with no amount; a head with nothing
	written, or "-", has spent nothing."""
	heads, total, closing, at_total, doubts = [], None, None, False, []
	first, last = min(cols.values()), max(cols.values())

	def settle():
		"""The row held in doubt was the total after all: the heads after it stand under it."""
		index, entry = doubts[0]
		del heads[index]
		for head in heads[index:]:
			head["outside"] = True
		return entry

	for row_no, cells in rows[start:]:
		if any(lo <= last and hi > first for lo, hi, _cols in _table_heads(cells)):
			break  # a table on the same sheet, under the summary; one beside it does not end it
		at = lambda field: cells[cols[field]] if field in cols and cols[field] < len(cells) else None  # noqa: E731
		raw, serial = at("amount"), _number(at("sr"))
		label, value = at("head"), _number(raw)
		if _blank(label) and isinstance(at("sr"), str):  # "Total Amount" is written under the serial numbers
			label = at("sr")
		words = _norm(label) if isinstance(label, str) else ""
		if not words or LITRES_WORDS.search(words) or SKIP_WORDS.match(words):
			continue
		if TOTAL_WORDS.match(words) or (LOOSE_TOTAL.search(words) and serial is None):
			entry = {"row": row_no, "amount": value, "per_litre": _number(at("per_litre"))}
			added = sum(head["amount"] or 0 for head in heads)
			if total is None and not TOTAL_WORDS.match(words) and value is not None and abs(value - added) > TOL:
				# "Total Salary" with no serial number: a head, or a total that is wrong. The next total row tells.
				doubts.append((len(heads), entry))
				heads.append({"row": row_no, "sr": serial, "head": _text(label, 80), "amount": value, "per_litre": _number(at("per_litre")),
					"budget_litre": _number(at("budget_litre")), "budget": _number(at("budget")), "surplus": _number(at("surplus")), "outside": False})
			elif total is None and doubts and (value is None or abs(value - added) > TOL):
				total, at_total, closing = settle(), True, entry
			elif total is None:
				total, at_total = entry, True
			else:
				closing = entry
			continue
		if value is None and not _error(raw) and serial is None:
			continue  # a title or a note between the heads: no serial number and no figure in the amount
		heads.append({"row": row_no, "sr": serial, "head": _text(label, 80), "amount": 0.0 if value is None and _nothing(raw) else value,
			"per_litre": _number(at("per_litre")), "budget_litre": _number(at("budget_litre")), "budget": _number(at("budget")),
			"surplus": _number(at("surplus")), "outside": at_total})
	if total is None and doubts:
		total = settle()
	return {"heads": heads, "total": total, "closing": closing}


def _sub_title(cell):
	"""A column title of a machine table without its unit: "Amount (Rs)" is "amount", "Rate/Hr" is "rate"."""
	return MATRIX_UNIT.sub("", _norm(cell)).strip()


def _read_matrix(rows, at):
	"""A machine table, when rows[at] is the row of its column titles: every machine is one row, every user of
	the machines a group of columns (hours, rate, hire amount, diesel, total) under a group title in the row
	above. Columns with a title and no group are the row's own totals. None for any other row.

	Everything kept is plain lists, texts and numbers: the book crosses to the job as JSON."""
	row_no, cells = rows[at]
	keys = [_sub_title(cell) for cell in cells]
	roles = [MATRIX_ROLE.get(key) for key in keys]
	if not at or roles.count("rate") < 2 or sum(1 for role in roles if role in ("amt", "amount", "total")) < 2:
		return None
	above_no, above = rows[at - 1]
	span = max(len(cells), len(above))
	cells, above, keys, roles = [list(part) + [None] * (span - len(part)) for part in (cells, above, keys, roles)]
	first = next(i for i, role in enumerate(roles) if role)
	if above_no != row_no - 1 or _blank(above[first]):
		return None
	titles = [f"{_norm(above[i])} {_norm(cells[i])}".strip() for i in range(first)]
	named = [i for i, title in enumerate(titles) if any(word in title for word in ("name", "machine", "vehicle", "equipment", "particular"))]
	if not named:
		return None
	# the table ends at a column with no title on either row, unless a group title follows: a table beside it is not its own
	width = next((i for i in range(first, span) if _blank(cells[i]) and _blank(above[i])
		and (i + 1 >= span or _blank(above[i + 1]) or _norm(above[i + 1]) in TITLES["name"])), span)
	# a sheet of days × rate under two shift titles is an ordinary list: a machine table says so in its name title,
	# or has diesel columns, or keeps a hire amount and a second amount in one group
	fuels = ("diesel", "per_hour", "diesel_rate")
	signed = any(word in title for title in titles for word in ("machine", "vehicle", "equipment")) or any(role in fuels for role in roles[:width])
	starts = [i for i in range(first, width) if not _blank(above[i])]
	groups, spare = [], []  # spare: titled columns that belong to no group, as (column, title, what it totals or "")
	for nth, lo in enumerate(starts):
		hi = starts[nth + 1] if nth + 1 < len(starts) else width
		cols, closed = {}, False
		for i in range(lo, hi):
			if _blank(cells[i]):
				continue
			alone = "amt" not in cols and not any(role in cols for role in fuels)  # a group of hours | rate | amount
			if closed or (not roles[i] and "total" in keys[i]) or (roles[i] == "total" and "amount" in cols and alone):
				spare.append((i, _norm(cells[i]), ""))  # past the group's own columns: a total of the whole row
				closed = True
			elif roles[i] == "amount" and "amount" in cols and "amt" not in cols:
				cols["amt"], cols["amount"] = cols["amount"], i  # "Amount" twice in a group: the hire, then the diesel
			elif roles[i]:
				cols.setdefault(roles[i], i)
				closed = roles[i] == "total"
		title = _norm(above[lo])
		if not cols:
			spare.append((lo, title, ""))
			continue
		if TOTAL_WORDS.match(title) or LOOSE_TOTAL.search(title):  # "TOTAL" over Hours | Amount: the row's totals, not a group
			spare += [(col, title, "hours" if role in ("hours", "days") else "amount") for role, col in cols.items() if role in MATRIX_SUMS]
			continue
		fuel = "amt" in cols or any(role in cols for role in fuels)
		hire = cols.get("amt", None if fuel else cols.get("amount"))
		group = {"title": _text(above[lo], 60).strip(), "cols": cols, "hire": hire, "fuel": cols.get("amount") if fuel else None, "total": cols.get("total")}
		signed = signed or (group["hire"] is not None and group["fuel"] is not None)
		if group["total"] is not None or group["hire"] is not None or group["fuel"] is not None:
			groups.append(group)
	if len(groups) < 2 or not signed:
		return None
	of_hours = lambda entry: entry[2] == "hours" or (not entry[2] and bool(re.search(r"\bhours?\b|\bhrs?\b|\(h\)", entry[1])))  # noqa: E731
	money_like = [entry for entry in spare if not of_hours(entry) and not re.search(r"diesel|ltr|litre|days?", entry[1])]
	row_hours = next((entry[0] for entry in spare if of_hours(entry)), None)
	row_total = next((entry[0] for entry in money_like if entry[2] == "amount" or re.search(r"amount|amt|\brs\b|₹", entry[1])),
		next((entry[0] for entry in reversed(money_like) if "total" in entry[1]), None))
	edge = max([row_total or 0, row_hours or 0, *[col for group in groups for col in group["cols"].values()]]) + 1
	in_groups = sorted({col for group in groups for col in group["cols"].values()})
	rates = [group["cols"]["rate"] for group in groups if "rate" in group["cols"]]
	name_titles = {_norm(above[named[0]]), _norm(cells[named[0]])} - {""}

	# the rows under the titles, down to another table's titles or a wide gap; which of them are total rows
	scan, last, seen_total = [], row_no, False
	for next_no, row in rows[at + 1:at + 1 + MAX_MACHINES + 60]:
		if next_no > last + (2 if seen_total else 6):
			break
		name = row[named[0]] if named[0] < len(row) else None
		if (isinstance(name, str) and _norm(name) in name_titles) or _table_heads(row[:edge]) or _summary_head(row[:edge]):
			break
		if all(_blank(cell) for cell in row[:edge]):
			continue  # only something right of the table stands on this row
		figures = [None] * first + [_number(cell) for cell in row[first:edge]]
		words = [_norm(cell) for cell in row[:first] if isinstance(cell, str) and not _blank(cell)]
		filled = sum(1 for figure in figures if figure is not None)
		strict = next((word for word in words if TOTAL_WORDS.match(word)), None)
		# any other label with the word total: a total row carries no rate, a machine of that name does
		loose = None if strict or any(_at(figures, col) is not None for col in rates) else next(
			(word for word in words if LOOSE_TOTAL.search(word)), None)
		total = strict or (loose if filled and (filled >= 2 or not seen_total) else None)
		scan.append({"row": next_no, "raw": row, "cells": figures, "words": words, "name": name, "filled": filled, "total": total,
			"recap": bool(loose and not total), "grouped": any(_at(figures, col) is not None for col in in_groups)})
		seen_total, last = seen_total or bool(total), next_no
	closing = max((i for i, entry in enumerate(scan) if entry["total"]), default=None)
	machines, totals, unread, loose_figures, last = [], [], [], [], row_no
	for i, entry in enumerate(scan):
		row, name, under = entry["raw"], entry["name"], closing is not None and i > closing
		if (under or closing is None) and (machines or under) and entry["row"] > last + 1:
			break  # under the last total row, or with no total row at all, a blank row ends the table
		if entry["total"]:
			totals.append({"row": entry["row"], "label": _text(entry["total"], 40), "cells": entry["cells"]})
		elif entry["filled"] and not entry["grouped"]:
			loose_figures.append(entry)  # a figure in the row's total only (GST, a deduction): no machine
		elif _blank(name) and not (entry["filled"] and machines and not under):
			if under and entry["words"]:
				break
		elif under and (entry["recap"] or not entry["filled"]):
			break  # words under the total row: something else starts here
		else:
			for col in range(first, min(edge, len(row))):
				if isinstance(row[col], str) and not _nothing(row[col]) and _number(row[col]) is None and not _error(row[col]) and len(unread) < 30:
					unread.append([entry["row"], col, _text(row[col], 20)])
			more = [_text(row[col], 40).strip() for col in named[1:] if col < len(row) and not _blank(row[col])]
			# a name merged over two rows is kept in the first only
			label = machines[-1]["name"] if _blank(name) else _text(name, 80).strip() + (f" ({', '.join(more)})" if more else "")
			machines.append({"row": entry["row"], "name": label, "cells": entry["cells"], "merged": _blank(name)})
		last = entry["row"]
	summed = sorted({col for group in groups for role, col in group["cols"].items() if role in MATRIX_SUMS} | {col for col in (row_total, row_hours) if col is not None})
	if not totals and len(machines) > 2 and machines[-1]["merged"]:
		# a last row with no name that adds up the rows above it: the total row, typed without its word
		said = {col: _at(machines[-1]["cells"], col) for col in summed if _at(machines[-1]["cells"], col) is not None}
		if said and all(abs(figure - sum(_at(machine["cells"], col) or 0 for machine in machines[:-1])) <= TOL for col, figure in said.items()):
			totals.append({"row": machines[-1]["row"], "label": "", "cells": machines.pop()["cells"]})
	if not machines:
		return None
	tail = groups[-1]
	if row_total is None and tail["total"] is not None and len(groups) > 1:
		# "Total Amount" after the last group, typed on the row of column titles: it is the row's total when every
		# machine's figure there is all its groups together
		rest = lambda cells: sum(_cost(cells, group) or 0 for group in groups[:-1])  # noqa: E731
		own = lambda cells: (_at(cells, tail["hire"]) or 0) + (_at(cells, tail["fuel"]) or 0)  # noqa: E731
		used = [machine["cells"] for machine in machines if _at(machine["cells"], tail["total"]) is not None]
		if used and any(rest(cells) for cells in used) and all(abs(_at(cells, tail["total"]) - rest(cells) - own(cells)) <= TOL for cells in used):
			row_total, tail["total"] = tail["total"], None
			if tail["hire"] is None and tail["fuel"] is None:
				groups.pop()
	# the figures a head of the summary may be taken from: the total rows, the loose figures, and what stands under the table
	under = [[round(figure, 2), entry["row"], col] for entry in [*totals, *loose_figures] for col, figure in enumerate(entry["cells"])
		if figure is not None and abs(figure) >= 1]
	under += [[round(float(cell), 2), next_no, col] for next_no, row in rows if next_no > last for col, cell in enumerate(row)
		if isinstance(cell, (int, float)) and not isinstance(cell, bool) and 1 <= abs(cell) < reader.MAX_AMOUNT]
	return {"head_row": row_no, "groups": groups, "machines": [{key: machine[key] for key in ("row", "name", "cells")} for machine in machines],
		"totals": totals, "row_total": row_total, "row_hours": row_hours, "last_row": last, "edge": edge, "under": under[:3000], "unread": unread,
		"loose": [[entry["row"], entry["cells"]] for entry in loose_figures[:60]]}


def _litres(rows):
	for _row_no, cells in rows[:8]:
		for i, cell in enumerate(cells):
			if isinstance(cell, str) and LITRES_WORDS.search(cell):
				return next((n for n in (_number(c) for c in cells[i + 1:i + 4]) if n), None)
	return None


def read_book(data, kind):
	"""Every table of every visible sheet. Raises reader.Unreadable when the file holds no MST table, or
	more than this check reads: a file is read whole or not at all, so no total is ever set against a
	part of its lines."""
	opener = {"xlsx": reader._xlsx_sheets, "xls": reader._xls_sheets, "csv": reader._csv_sheets}[kind]
	sheets, summary, left, kept, kept_totals = [], None, [reader.MAX_WALK], 0, 0
	try:
		with reader.closing(opener(data, cols=MAX_COLS)) as book:
			for index, (title, visible, raw) in enumerate(book, 1):
				if not visible:
					continue
				sheet, rows, usual = {"title": _text(title, 60).strip(), "index": index, "tables": [], "skipped": 0}, [], 0
				for row_no, cells in reader._walk(raw, left, TOO_LARGE):
					if reader._empty(cells):
						continue
					rows.append((row_no, list(cells)))
					usual += not reader._empty(cells[:NARROW])
					if usual > reader.MAX_ROWS or len(rows) > reader.MAX_ROWS + MAX_MACHINES + 500:
						raise reader.Unreadable(f"Sheet '{sheet['title']}' has more than {reader.MAX_ROWS:,} filled rows. "
							"Remove what is not part of the report and attach again.")
				found_at, matrix = next(((at, table) for at, table in ((at, _read_matrix(rows, at)) for at in range(len(rows))) if table), (None, None))
				if matrix:
					if len(matrix["machines"]) > MAX_MACHINES:
						raise reader.Unreadable(f"Sheet '{sheet['title']}' has more than {MAX_MACHINES} rows in its machine table. "
							"Remove what is not part of the report and attach again.")
					sheet["matrix"] = matrix
					sheet["wider"] = any(len(cells) > MAX_COLS and not _blank(cells[MAX_COLS]) for _row_no, cells in rows)
					kept += sum(1 for machine in matrix["machines"] for group in matrix["groups"] if _cost(machine["cells"], group))
					if kept > MAX_LINES:
						raise reader.Unreadable(f"The workbook has more than {MAX_LINES:,} lines. Send it in parts, a few sheets at a time.")
				else:  # any other sheet is read to the width it always was
					rows = [(row_no, cells[:NARROW]) for row_no, cells in rows if not reader._empty(cells[:NARROW])]
				if not rows:
					continue
				# every cell that shows "#REF!", "#VALUE!"...: said in the report wherever it stands; and how much the sheet holds
				sheet["errors"] = [(row_no, i, str(cell).strip()[:12]) for row_no, cells in rows for i, cell in enumerate(cells) if _error(cell)][:MAX_ERRORS]
				sheet["figures"] = sum(1 for _row_no, cells in rows for cell in cells if _number(cell) is not None)
				head_rows = {}
				for at, (row_no, cells) in enumerate(rows):
					if at == found_at:
						continue
					found = _summary_head(cells)
					if found and summary:
						if not sheet.get("summary") and "copy" not in sheet:
							# a second summary sheet: the first is the one that is checked; this one is set against it
							sheet["copy"] = _read_summary(rows, at + 1, found)["heads"]
						continue
					if found:
						summary = {"sheet": sheet["title"], "litres": _litres(rows), **_read_summary(rows, at + 1, found)}
						sheet["summary"], sheet["beside"] = True, max(found.values())
						right = [head for head in _table_heads(cells) if head[0] > sheet["beside"]]
						if right:
							head_rows[at] = (row_no, right)
						continue
					heads = _table_heads(cells)
					if heads:
						head_rows[at] = (row_no, heads)
				if matrix:  # no ordinary table starts on the machine table's own rows and columns
					top, bottom, edge = matrix["head_row"] - 1, matrix["last_row"], matrix["edge"]
					head_rows = {at: (row_no, [head for head in heads if not top <= row_no <= bottom or head[0] >= edge])
						for at, (row_no, heads) in head_rows.items()}
					head_rows = {at: found for at, found in head_rows.items() if found[1]}
				every = [(row_no, lo, hi) for row_no, heads in head_rows.values() for lo, hi, _cols in heads]
				if len(every) > MAX_TABLES:
					raise reader.Unreadable(f"Sheet '{sheet['title']}' has more tables than an MST sheet holds. Check the file and attach again.")
				for at, (row_no, heads) in head_rows.items():
					for lo, hi, cols in heads:
						# a table ends at the next title row over its own columns; a table beside it does not end it
						stops = {other for other, lo2, hi2 in every if other != row_no and lo2 < hi and hi2 > lo}
						if matrix and lo < matrix["edge"]:
							stops.add(matrix["head_row"] - 1)  # a table above the machine table ends there
						begin = max((hi2 for _other, _lo2, hi2 in every if hi2 <= lo), default=0)
						end = min((lo2 for _other, lo2, _hi2 in every if lo2 >= hi), default=None)
						lines, totals, skipped = _read_table(rows, at + 1, rows[at][1], lo, hi, cols, stops, begin, end)
						sheet["skipped"] += skipped
						if not lines:
							continue
						kept, kept_totals = kept + len(lines), kept_totals + len(totals)
						if kept > MAX_LINES:
							raise reader.Unreadable(f"The workbook has more than {MAX_LINES:,} lines. Send it in parts, a few sheets at a time.")
						if kept_totals > MAX_TOTALS:
							raise reader.Unreadable("The file has more total rows than an MST report holds. Check the file and attach again.")
						sheet["tables"].append({"head_row": row_no, "first_col": lo, "lines": lines, "totals": totals, "has": sorted(cols),
							"last_row": max(entry["row"] for entry in [*lines, *totals]), "last_col": max(hi, cols["amount"] + 1) + 2})
				sheets.append(sheet)
	except reader.Unreadable:
		raise
	except Exception:
		raise reader._unopenable() from None
	if not (summary and summary["heads"]) and not any(sheet["tables"] or sheet.get("matrix") for sheet in sheets):
		raise reader.Unreadable("No MST sheet was found in the file. A sheet needs its column titles: "
			"Item Name and Grand Total, or Cost Center, Amount and Per Ltr on the summary.")
	return {"sheets": sheets, "summary": summary}


# ---- the arithmetic


def money(value):
	from trustbit_ethanol.ts_gate_entry.ts_ai_check_rules import money as rupees

	return rupees(value)


def _stacks(sheet):
	"""The sheet's tables as stacks, the main one (the leftmost) first. Tables under each other that start in
	the same column, or one column apart with the same titles (a block typed without its "Sr. No."), are one
	stack: a GRAND TOTAL under the last adds them all up. A table beside another is a stack of its own."""
	stacks = []
	for table in sorted(sheet["tables"], key=lambda table: (table["head_row"], table["first_col"])):
		under = next((stack for stack in stacks if table["head_row"] > stack[-1]["last_row"] and (stack[0]["first_col"] == table["first_col"]
			or (abs(stack[0]["first_col"] - table["first_col"]) == 1 and stack[0]["has"] == table["has"]))), None)
		if under is None:
			stacks.append([table])
		else:
			under.append(table)
	return sorted(stacks, key=lambda stack: stack[0]["first_col"])


def _main_table(sheet):
	"""The tables of a head's sheet that carry its figure: the leftmost stack. A table further right (a ledger,
	a break-up) is read, but the summary does not point at it."""
	return _stacks(sheet)[0] if sheet["tables"] else []


def sheet_total(sheet):
	"""(what the sheet says its total is, what its lines add up to). Either can be None."""
	tables = _main_table(sheet)
	lines = [line for table in tables for line in table["lines"]]
	totals = [total for table in tables for total in table["totals"] if total["amount"] is not None]
	grand = [total for total in totals if total["grand"]]
	said = grand[-1]["amount"] if grand else sum(total["amount"] for total in totals) if totals else None
	added = round(sum(line["amount"] or 0 for line in lines), 2) if lines else None
	return said, added


def check_book(book):
	"""The file's own arithmetic. Returns (lines for the report, points). A point's `line_no` is the line it
	is about, or 0 for a point on a sheet or on the whole file."""
	lines, points = [], []

	def add(level, rule, title, detail, line_no=0, sheet=""):
		points.append({"line_no": line_no, "level": level, "source": "Rule", "rule": rule, "title": title,
			"detail": (f"{sheet}: " if sheet and not line_no else "") + detail, "refs": ""})

	for sheet in book["sheets"]:
		errors = {(row, col): text for row, col, text in sheet.get("errors", [])}
		on_row = lambda row, table: sorted(col for at, col in errors if at == row and table["first_col"] - 1 <= col < table["last_col"])  # noqa: E731
		for nth, stack in enumerate(_stacks(sheet)):
			# on the summary's own sheet, a table that starts right of the summary stands beside it
			main = not nth and stack[0]["first_col"] <= sheet.get("beside", stack[0]["first_col"]) and not sheet.get("matrix")
			rows = sorted([*[("line", line["row"], line, table) for table in stack for line in table["lines"]],
				*[("total", total["row"], total, table) for table in stack for total in table["totals"]]], key=lambda entry: entry[1])
			usual = {id(table): _usual_cc(table["lines"]) for table in stack}
			seen, since, blocks, mark = {}, [], [], 0
			for what, _row, entry, table in rows:
				if what == "total":
					part = None
					if not entry["grand"] and blocks and not since:
						entry["grand"] = True  # a plain "Total" under the block totals, no line between: it adds them up
						part, mark = (sum(blocks[mark:]) if mark else None), len(blocks)  # or only those since the last such total
					elif (not entry["grand"] and blocks and since and entry["amount"] is not None
							and abs(entry["amount"] - sum(blocks) - sum(since)) <= TOL < abs(entry["amount"] - sum(since))):
						entry["grand"] = True  # "Total incl. GST": the total above it and the lines since
					if entry["grand"]:
						target = sum(blocks) + sum(since)  # the closed blocks, and any lines after the last of them
						if part is not None and abs((entry["amount"] or 0) - part) <= TOL:
							target = part
						basis = "the lines" if not blocks else "the totals above it" if not since else "the totals and lines above it"
						own = sum(line["amount"] or 0 for line in table["lines"] if line["row"] < entry["row"])
						closed = any(not other["grand"] and other["row"] < entry["row"] for other in table["totals"])
						if not closed and abs((entry["amount"] or 0) - own) <= TOL:
							target = own  # one table of several, closed by its own row called "Grand Total"
					else:
						target, basis = sum(since), "the lines above it"
						blocks.append(entry["amount"] if entry["amount"] is not None else sum(since))  # no figure: what its lines come to
						since = []
					for col in on_row(entry["row"], table) if entry["error"] else ():
						errors.pop((entry["row"], col))  # said just below
					if entry["error"] or entry["amount"] is None:
						add(CHECK, "F6", "A total has no value", f"Row {entry['row']} ({entry['label']}) shows an error or was never "
							"calculated. Open the file in Excel, let it calculate, save and submit again.", sheet=sheet["title"])
					elif abs(entry["amount"] - target) > TOL:
						add(SERIOUS if main else CHECK, "F1", "A total does not match its lines",
							f"Row {entry['row']} ({entry['label']}) says {money(entry['amount'])}; {basis} add up to {money(target)}. "
							f"Difference {money(entry['amount'] - target)}.", sheet=sheet["title"])
					continue
				line = entry
				since.append(line["amount"] or 0)
				line_no = len(lines) + 1
				lines.append({"line_no": line_no, "sheet": sheet["title"], "main": main, **line})
				here = f"{sheet['title']} row {line['row']}"
				shown = [(_cell(line["row"], col), errors.pop((line["row"], col))) for col in on_row(line["row"], table)]
				if shown and not line["error"]:  # an error in the unit, the rate, the cost centre: the amount and quantity read well
					add(CHECK, "F6", "A cell of this line shows an error", f"{here}: " + ", ".join(f"{cell} shows {text}" for cell, text in shown)
						+ ". Correct the formula in the workbook and submit again.", line_no)
				if line["error"] == "quantity":
					add(CHECK, "F6", "A quantity could not be read", f"{here}: the quantity shows an error or was never calculated.", line_no)
				elif line["error"] == "text":
					add(CHECK, "F6", "A line has no amount", f"{here}: the amount '{line['written']}' could not be read as a number.", line_no)
				elif line["error"] or line["amount"] is None:
					add(CHECK, "F6", "A line has no amount", f"{here}: the amount shows an error or was never calculated.", line_no)
				elif line["qty"] is not None and line["rate"] is not None:
					worked = line["qty"] * line["rate"]
					# a figure typed to two decimals may be half its last decimal off: that much, times the other figure
					loose = lambda figure: 0 if float(figure).is_integer() else _slack(figure)  # noqa: E731 — a whole number is exact
					near = 1.0 + abs(line["qty"]) * loose(line["rate"]) + abs(line["rate"]) * loose(line["qty"])
					if abs(worked - line["amount"]) > near:
						add(CHECK, "F5", "Amount is not quantity × rate", f"{here}: {_fig(line['qty'])} × {_fig(line['rate'])} = {money(worked)}, "
							f"the sheet says {money(line['amount'])}.", line_no)
				if usual[id(table)] and line["cc"] and _norm(line["cc"]) != usual[id(table)]:
					add(CHECK, "F7", "Line under another cost centre", f"{here}: the cost centre is '{line['cc']}', "
						"not the one on the other lines of this sheet.", line_no)
				key = (_norm(line["name"]), line["qty"], line["amount"], _norm(line["cc"]), _norm(line["place"]), _norm(line["remark"]))
				# an issue line has a quantity; on a sheet of typed expenses the same particular and amount twice is usual
				if key in seen and line["amount"] and line["qty"] is not None:
					add(CHECK, "F8", "The same line twice on one sheet", f"{here} repeats row {seen[key]}: same item, quantity, "
						"amount and remark.", line_no)
				seen.setdefault(key, line["row"])
			for table in stack:
				closed = max((total["row"] for total in table["totals"]), default=None)
				tail = [line for line in table["lines"] if closed is not None and line["row"] > closed]
				if tail:
					where = f"Row {tail[0]['row']} stands" if len(tail) == 1 else f"Rows {tail[0]['row']} to {tail[-1]['row']} stand"
					add(CHECK, "F1", "Lines under the last total row", f"{where} under the total row (row {closed}) and in no total: "
						f"{money(sum(line['amount'] or 0 for line in tail))}.", sheet=sheet["title"])

		if sheet.get("matrix"):
			_check_matrix(sheet, lines, add)
		if sheet.get("wider"):
			add(CHECK, "F6", "A sheet is wider than was read", f"Only the first {MAX_COLS} columns (up to {_cell(1, MAX_COLS - 1)[:-1]}) "
				"were read. What stands further right was not checked.", sheet=sheet["title"])
		if sheet.get("summary") and book["summary"]:  # a head or the total with no value is said by its own point
			unset = {head["row"] for head in book["summary"]["heads"] if head["amount"] is None}
			unset |= {entry["row"] for entry in (book["summary"]["total"], book["summary"].get("closing")) if entry and entry["amount"] is None}
			errors = {at: text for at, text in errors.items() if at[0] not in unset}
		if errors:
			cells = sorted(errors, key=lambda at: (at[0], at[1]))
			add(CHECK, "F6", "Cells that show an error", ", ".join(f"{_cell(*at)} ({errors[at]})" for at in cells[:12])
				+ (f" and {len(cells) - 12} more" if len(cells) > 12 else "") + ". Each is a formula that no longer works. "
				"Correct it in the workbook and submit again.", sheet=sheet["title"])
	# a sheet full of figures with no table the reader knows: said as a point, not only in the small print
	unread = [f"'{sheet['title']}'" for sheet in book["sheets"] if not sheet["tables"] and not sheet.get("summary") and "copy" not in sheet
		and not sheet.get("matrix") and sheet.get("figures", 0) >= 10]
	if unread:
		add(CHECK, "F6", "A sheet was not read", "No table with the usual column titles (Item Name or Particular, with Grand Total or Amount) "
			"was found on: " + ", ".join(unread[:12]) + ". Nothing on " + ("it" if len(unread) == 1 else "them") + " was checked; a head that "
			"points there is taken from the summary as it stands.")

	summary = book["summary"]
	for sheet in book["sheets"] if summary else ():
		if "copy" in sheet:  # the same heads with other amounts: one of the two sheets is out of date
			first = {}
			for head in summary["heads"]:
				first.setdefault(_norm(head["head"]), []).append(head)
			# a head name that stands twice is set against its namesakes in their order
			other = [(head, first[_norm(head["head"])].pop(0) if first.get(_norm(head["head"])) else None) for head in sheet["copy"]]
			apart = [f"{head['head']} (row {head['row']}: {money(head['amount'] or 0)}, there {money(same['amount'] or 0)})"
				for head, same in other if same and abs((head["amount"] or 0) - (same["amount"] or 0)) > TOL]
			lone = sum(1 for _head, same in other if not same) + sum(len(heads) for heads in first.values())
			if apart:
				add(CHECK, "F2", "A second summary sheet differs from the summary", f"Against '{summary['sheet']}': " + "; ".join(apart[:8])
					+ (f"; and {len(apart) - 8} more" if len(apart) > 8 else "") + ".", sheet=sheet["title"])
			both = sum(1 for _head, same in other if same)
			sheet["agrees"] = "it differs" if apart else "no head in common" if not both else f"{both} heads, the same amounts" + (
				f"; {lone} head(s) stand on one of the two only" if lone else "")
	if summary:
		pairs, loose = _pair(book)
		untraced = _trace_matrix(summary, pairs)  # also marks the heads that share a machine table with the head before them
		alone = [head for head in summary["heads"] if head["row"] not in pairs and not head.get("shares")]
		if alone and loose:
			# which of these heads the unmatched sheets belong to is not known: counting both would count money twice
			add(CHECK, "F2", "Some heads and sheets could not be matched",
				"No sheet was found for: " + ", ".join(head["head"] for head in alone[:12]) + ". No head points at: "
				+ ", ".join(f"'{sheet['title']}'" for sheet in loose[:12]) + ". These were not compared with each other, and "
				"these heads are not among the lines of this report. A sheet is matched by the head's serial number or name "
				"at the start of its title.", sheet=summary["sheet"])
		elif loose:
			add(CHECK, "F2", "A sheet has no head on the summary", "No row of the summary points at: "
				+ ", ".join(f"'{sheet['title']}'" for sheet in loose[:12]) + ". Its lines are in this report, and in no head of "
				"the summary. A sheet is matched by the head's serial number or name at the start of its title.", sheet=summary["sheet"])
		for head in summary["heads"]:
			# no sheet of this head is in the file (a typed sheet, or the summary sent alone), or its sheet is a machine
			# table that several heads share: the summary row is the line that is counted, and the table's lines are not
			sheet = pairs.get(head["row"])
			shared = bool(sheet and sheet.get("matrix")) or bool(head.get("shares"))
			if (not shared and (sheet or loose)) or (shared and sheet and head["amount"] is None):
				continue  # a head of a machine table that shows no value is said by its own point, and the table's lines are counted
			head["line_no"] = len(lines) + 1
			lines.append({"line_no": head["line_no"], "sheet": summary["sheet"], "main": True, "row": head["row"],
				"summary": "machines" if shared else "alone",
				"name": head["head"], "qty": None, "uom": "", "rate": None, "amount": head["amount"], "written": "",
				"error": "amount" if head["amount"] is None else "", "cc": "", "place": "", "remark": "", "bill": ""})
			for line in sheet["matrix"]["lines"] if sheet and sheet.get("matrix") else ():
				line["main"], line["aside"] = False, "machine table"
		for head, detail in untraced:
			add(CHECK, "F2", "A head could not be traced to its sheet", detail, head.get("line_no", 0), summary["sheet"])
		_check_summary(summary, pairs, add)
	return lines, points


def _at(cells, col):
	return cells[col] if col is not None and col < len(cells) else None


def _cost(cells, group):
	"""What a machine cost one group: the group's own total, or its hire and diesel when it keeps no total."""
	if group["total"] is not None:
		return _at(cells, group["total"])
	hire, fuel = _at(cells, group["hire"]), _at(cells, group["fuel"])
	return None if hire is None and fuel is None else (hire or 0) + (fuel or 0)


def _near(units, rate):
	"""How far an amount may sit from units × rate: a rupee, and half the last typed decimal of each figure
	times the other. A whole number is exact."""
	loose = lambda figure: 0 if float(figure).is_integer() else _slack(figure)  # noqa: E731
	return 1.0 + abs(units) * loose(rate) + abs(rate) * loose(units)


def _check_matrix(sheet, lines, add):
	"""A machine table's own arithmetic. Its lines are a machine's cost for one group. When heads of the summary
	share the table, those heads are counted and its lines are listed only (`check_book` marks them)."""
	matrix, name = sheet["matrix"], sheet["title"]
	matrix["lines"] = []
	for machine in matrix["machines"]:
		cells, added = machine["cells"], 0.0
		for group in matrix["groups"]:
			at = lambda role: _at(cells, group["cols"].get(role))  # noqa: E731
			hours, days, rate = at("hours"), at("days"), at("rate")
			hire, fuel, total, cost = _at(cells, group["hire"]), _at(cells, group["fuel"]), _at(cells, group["total"]), _cost(cells, group)
			added += cost or 0
			units = hours if hours is not None else days
			owed = bool(group["hire"] is not None and hire is None and units and rate)  # hours and a rate, and no amount worked out
			if not cost and not hire and not fuel and not owed:
				continue
			line_no = len(lines) + 1
			# counted like any line, until a head of the summary is counted in the table's place
			lines.append({"line_no": line_no, "sheet": name, "main": True, "row": machine["row"],
				"name": f"{machine['name']} · {group['title']}"[:140], "qty": units,
				"uom": "" if units is None else "hours" if hours is not None else "days", "rate": rate,
				"amount": cost, "written": "", "error": "" if cost is not None else "amount", "cc": "", "place": "", "remark": "", "bill": ""})
			matrix["lines"].append(lines[-1])
			here = f"{name} row {machine['row']}, {group['title']}"
			if owed:
				add(CHECK, "F6", "A line has no amount", f"{here}: {_fig(units)} × {_fig(rate)} is not worked out: the amount is empty.", line_no)
			elif cost is None:
				add(CHECK, "F6", "A line has no amount", f"{here}: the total shows an error or was never calculated.", line_no)
			worked = [(figure, figure * rate) for figure in (hours, days) if figure is not None and rate is not None]
			if hire is not None and worked and all(abs(product - hire) > _near(figure, rate) for figure, product in worked):
				add(CHECK, "F5", "Amount is not quantity × rate", f"{here}: {_fig(units)} × {_fig(rate)} = "
					f"{money(worked[0][1])}, the sheet says {money(hire)}.", line_no)
			diesel, diesel_rate = at("diesel"), at("diesel_rate")
			if fuel is not None and diesel is not None and diesel_rate is not None and abs(diesel * diesel_rate - fuel) > _near(diesel, diesel_rate):
				add(CHECK, "F5", "Amount is not quantity × rate", f"{here}: diesel {_fig(diesel)} × {_fig(diesel_rate)} = "
					f"{money(diesel * diesel_rate)}, the sheet says {money(fuel)}.", line_no)
			if total is not None and (hire is not None or fuel is not None) and group["hire"] is not None and group["fuel"] is not None and abs(
					(hire or 0) + (fuel or 0) - total) > TOL:
				add(CHECK, "F1", "A total does not match its lines", f"{here}: hire {money(hire or 0)} and diesel {money(fuel or 0)} "
					f"add up to {money((hire or 0) + (fuel or 0))}, the sheet says {money(total)}.", line_no)
		said = _at(cells, matrix["row_total"])
		if said is not None and abs(said - added) > TOL:
			add(CHECK, "F1", "A total does not match its lines", f"Row {machine['row']} ({machine['name']}): its groups add up to "
				f"{money(added)}, the row's total says {money(said)}.", sheet=name)
	if matrix["unread"]:
		add(CHECK, "F6", "A figure could not be read", ", ".join(f"{_cell(row, col)} ('{text}')" for row, col, text in matrix["unread"][:12])
			+ (f" and {len(matrix['unread']) - 12} more" if len(matrix["unread"]) > 12 else "") + ": words where a figure belongs. "
			"Nothing was worked out from these cells. Type the plain number and submit again.", sheet=name)
	totals = matrix["totals"]
	if not totals:
		add(CHECK, "F1", "The machine table has no total row", f"No row with the word Total stands under the machines (rows "
			f"{matrix['machines'][0]['row']} to {matrix['last_row']}). The groups were added up from the machine rows.", sheet=name)
		return
	feeds = {group["total"] if group["total"] is not None else group["hire"] for group in matrix["groups"]} | {matrix["row_total"]}
	summed = sorted({col for group in matrix["groups"] for role, col in group["cols"].items() if role in MATRIX_SUMS}
		| {col for col in (matrix["row_total"], matrix["row_hours"]) if col is not None})
	for nth, total in enumerate(totals):
		since = totals[nth - 1]["row"] if nth else 0
		above = [machine["cells"] for machine in matrix["machines"] if machine["row"] < total["row"]]
		part = [machine["cells"] for machine in matrix["machines"] if since < machine["row"] < total["row"]]
		for col in summed:
			said = _at(total["cells"], col)
			if said is None:
				continue
			# a total row adds the machines since the total row before it, or every machine above it; a second one may
			# add to the first what stands between them (GST, a deduction)
			fits = [sum(_at(cells, col) or 0 for cells in rows) for rows in (part, above)]
			before = _at(totals[nth - 1]["cells"], col) if nth else None
			if before is not None and not part:
				fits.append(before + sum(_at(cells, col) or 0 for row, cells in matrix.get("loose") or [] if since < row < total["row"]))
			if all(abs(said - figure) > TOL for figure in fits):
				add(SERIOUS if col in feeds and total is totals[-1] else CHECK, "F1", "A total does not match its lines",
					f"{_cell(total['row'], col)} says {_fig(round(said, 2))}; the machine rows above it add up to "
					f"{_fig(round(fits[0 if nth and part else 1], 2))}.", sheet=name)
	last = totals[-1]
	tail = [machine for machine in matrix["machines"] if machine["row"] > last["row"]]
	if tail:
		where = f"Row {tail[0]['row']} stands" if len(tail) == 1 else f"Rows {tail[0]['row']} to {tail[-1]['row']} stand"
		money_under = sum(_cost(machine["cells"], group) or 0 for machine in tail for group in matrix["groups"])
		add(CHECK, "F1", "Lines under the last total row", f"{where} under the total row (row {last['row']}) and in no total: "
			f"{money(money_under)}.", sheet=name)


def _trace_matrix(summary, pairs):
	"""A head whose sheet is a machine table takes a part of it: the total of some groups (and of any table
	under them), or a figure worked out under the table. Returns the heads that are neither, as (head, what
	to say). The heads that follow such a head with no sheet of their own may share the table: when they and
	it together are a figure under the table, they are marked (`shares`) and counted as its heads."""
	heads, untraced = summary["heads"], []
	for nth, head in enumerate(heads):
		sheet = pairs.get(head["row"])
		if not sheet or not sheet.get("matrix") or head["amount"] is None:
			continue
		matrix = sheet["matrix"]
		last = matrix["totals"][-1]["cells"] if matrix["totals"] else []
		pool = []
		for group in matrix["groups"]:
			said = _at(last, group["total"]) if group["total"] is not None else None
			pool.append((group["title"], said if said is not None else sum(_cost(machine["cells"], group) or 0 for machine in matrix["machines"])))
		for stack in _stacks(sheet):  # a list of bills under the machines may be part of the head
			lines = [line["amount"] or 0 for table in stack for line in table["lines"]]
			grand = [total["amount"] for table in stack for total in table["totals"] if total["grand"] and total["amount"] is not None]
			pool.append((f"the table from row {stack[0]['head_row']}", grand[-1] if grand else sum(lines)))
		pool = [(title, figure) for title, figure in pool if figure]
		if not pool and not head["amount"]:
			continue
		# every choice of 14 groups is 16,000 sums; of more, the whole table, a few groups, or all but a few
		whole = sum(figure for _title, figure in pool)
		picks = [tuple(pool)] + [pick for size in (range(1, len(pool)) if len(pool) <= 14 else (1, 2, 3)) for pick in combinations(pool, size)]
		part = next((pick for pick in picks if abs(sum(figure for _title, figure in pick) - head["amount"]) <= TOL), None) if head["amount"] else None
		if not part and head["amount"] and len(pool) > 14:
			left = next((pick for size in (1, 2, 3) for pick in combinations(pool, size)
				if abs(whole - sum(figure for _title, figure in pick) - head["amount"]) <= TOL), None)
			part = tuple(entry for entry in pool if entry not in left) if left else None
		near = lambda figure: next((_cell(row, col) for value, row, col in matrix["under"] if abs(value - figure) <= TOL), None)  # noqa: E731
		own = near(head["amount"]) if head["amount"] else None
		family = [head]
		for other in heads[nth + 1:nth + 3]:
			if other["row"] in pairs or not other["amount"] or other["outside"] != head["outside"]:
				break
			family.append(other)
		together = next(((family[:size], near(sum(each["amount"] for each in family[:size]))) for size in range(len(family), 1, -1)
			if near(sum(each["amount"] for each in family[:size]))), None)
		said = []
		if part:
			said.append(f"head '{head['head']}' is the total of " + (" + ".join(title for title, _figure in part) if len(part) < 9 else f"{len(part)} groups"))
		elif own:
			said.append(f"head '{head['head']}' is the figure in {own}")
		if together:
			said.append("heads " + ", ".join(f"'{each['head']}'" for each in together[0]) + f" together are the figure in {together[1]}")
			for each in together[0][1:]:
				each["shares"] = sheet["title"]
		sheet["traced"] = "; ".join(said)
		if not part and not own:
			untraced.append((head, f"row {head['row']} ({head['head']}) shows {money(head['amount'])}. That is not the total of any groups of "
				f"the machine table on '{sheet['title']}', nor a figure worked out under that table."))
	return untraced


def _serial(value):
	return f"{float(value):g}"


def _pair(book):
	"""Which sheet each summary row points at ({summary row: sheet}), and the sheets with a table that no
	row points at. By serial number when one head and one sheet carry it; otherwise by the head's name
	against the sheet's title. A number used twice above the total pairs nothing. Rows under the total
	either go on counting (15, 16, ...) or start afresh at 1: a number already used above the total is
	not theirs."""
	summary, sheets = book["summary"], [sheet for sheet in book["sheets"] if sheet["tables"] or sheet.get("matrix")]
	pairs, taken, numbered = {}, set(), {}
	for sheet in sheets:
		number = SHEET_NO.match(sheet["title"])
		if number:
			numbered.setdefault(_serial(number.group(1)), []).append(sheet)
	for outside in (False, True):
		group = [head for head in summary["heads"] if head["outside"] == outside and head["sr"] is not None]
		above = {_serial(head["sr"]) for head in summary["heads"] if not head["outside"] and head["sr"] is not None} if outside else set()
		for head in group:
			key = _serial(head["sr"])
			if key not in above and len(numbered.get(key, [])) == 1 and sum(1 for other in group if _serial(other["sr"]) == key) == 1:
				pairs[head["row"]] = numbered[key][0]
				taken.add(id(numbered[key][0]))
	for head in summary["heads"]:
		if head["row"] not in pairs:
			same = [sheet for sheet in sheets if id(sheet) not in taken and _norm(SHEET_NO.sub("", sheet["title"])) == _norm(head["head"])]
			if len(same) == 1:
				pairs[head["row"]] = same[0]
				taken.add(id(same[0]))
	return pairs, [sheet for sheet in sheets if id(sheet) not in taken and not sheet.get("summary")]


def _usual_cc(lines):
	"""The cost centre most lines of a table carry, when at least three lines carry one."""
	counts = {}
	for line in lines:
		if line["cc"]:
			counts[_norm(line["cc"])] = counts.get(_norm(line["cc"]), 0) + 1
	if sum(counts.values()) < 3:
		return None
	top = max(counts, key=counts.get)
	return top if counts[top] * 2 > sum(counts.values()) else None


def _slack(figure):
	"""How far a per-litre figure may sit from the worked one: half a unit of the last decimal it is written
	to (1.46 is right for anything from 1.455 to 1.465), a little over."""
	for places in (2, 3, 4):
		if abs(round(figure, places) - figure) < 1e-9:
			return 0.51 * 10 ** -places
	return 0.00005


def _check_summary(summary, pairs, add):
	name, litres = summary["sheet"], summary["litres"]
	inside = [head for head in summary["heads"] if not head["outside"]]
	total = summary["total"]
	if total and total["amount"] is None:
		add(CHECK, "F6", "The summary total has no value", f"Row {total['row']} shows an error or was never calculated.", sheet=name)
	elif total and all(head["amount"] is not None for head in inside):
		added = sum(head["amount"] for head in inside)
		if abs(added - total["amount"]) > TOL:
			add(SERIOUS, "F3", "The summary total does not match its heads", f"Row {total['row']} says {money(total['amount'])}; "
				f"the heads above it add up to {money(added)}.", sheet=name)
	closing = summary.get("closing")
	if closing and closing["amount"] is not None and all(head["amount"] is not None for head in summary["heads"]):
		above = [head["amount"] for head in summary["heads"] if head["row"] < closing["row"]]
		under = [head["amount"] for head in summary["heads"] if head["outside"] and head["row"] < closing["row"]]
		fits = [sum(above), sum(under), *([total["amount"]] if total and total["amount"] is not None else [])]
		if all(abs(closing["amount"] - figure) > TOL for figure in fits):  # all heads, the heads under the first total, or that total again
			add(SERIOUS, "F3", "The summary total does not match its heads", f"Row {closing['row']} says {money(closing['amount'])}; "
				f"the heads above it add up to {money(sum(above))}.", sheet=name)
	if not litres:
		add(CHECK, "F4", "Production litres were not found", "The per-litre figures cannot be checked without the litres "
			"('Total Production') on the summary.", sheet=name)
	for head in summary["heads"]:
		here = f"row {head['row']} ({head['head']})"
		if head["amount"] is None:
			add(CHECK, "F6", "A head has no amount", f"{here}: the amount shows an error or was never calculated. Open the file "
				"in Excel, let it calculate, save and submit again.", head.get("line_no", 0), name)
			continue
		if litres and head["per_litre"] is not None and abs(head["per_litre"] - head["amount"] / litres) > max(
				_slack(head["per_litre"]), abs(head["per_litre"]) * 0.0005):
			add(CHECK, "F4", "A per-litre figure does not follow from its amount", f"{here}: {money(head['amount'])} ÷ {litres:,.2f} L = "
				f"{head['amount'] / litres:.4f}, the sheet says {head['per_litre']:.4f}. The litres typed into that formula may be an "
				"older figure.", head.get("line_no", 0), name)
		if head["budget"] is not None and head["surplus"] is not None and abs(head["budget"] - head["amount"] - head["surplus"]) > TOL:
			add(CHECK, "F9", "Surplus is not budget less amount", f"{here}: budget {money(head['budget'])} less {money(head['amount'])} "
				f"is {money(head['budget'] - head['amount'])}, the sheet says {money(head['surplus'])}.", head.get("line_no", 0), name)
		sheet = pairs.get(head["row"])
		if not sheet or sheet.get("matrix"):
			continue
		said, added = sheet_total(sheet)
		if len(_main_table(sheet)) > 1 and said is None:
			continue  # several tables and no stated total: which figure the summary takes is not known
		figure, basis = (said, "total") if said is not None else (added, "lines")
		# a total row that disagrees with its own lines is F1's; here the head must match neither figure
		if figure is not None and abs(figure - head["amount"]) > TOL and (added is None or abs(added - head["amount"]) > TOL):
			add(SERIOUS, "F2", "The summary does not match the head's sheet", f"{here} shows {money(head['amount'])}; the {basis} of sheet "
				f"'{sheet['title']}' come to {money(figure)}. Difference {money(head['amount'] - figure)}.", sheet=name)


def report(book, kind, erp=None, month=None):
	"""The checked workbook in the two shapes the job stores: what was read (lines in the report's own
	fields) and what was found (a verdict per line, the points, the sums). `erp` is the month's Material Issues
	as `ts_ai_check_issues.fetch` gives them: with it, every line is also set against the ERP. `month` is
	{"day": the report's date, "before": last month's figures as `ts_ai_check_months.before` gives them}: with
	it, the report keeps its own figures and is set against last month's."""
	from trustbit_ethanol.ts_gate_entry.ts_ai_check_rules import totals

	rows, points = check_book(book)
	against = None
	if erp is not None:
		from trustbit_ethanol.ts_gate_entry import ts_ai_check_issues as issues

		against = issues.compare(rows, erp, whole=bool(book["summary"]))
		# the same item, quantity and amount on two lines: with the ERP's issues of that item at hand, its quantity
		# is judged against them (E2 says a line too many), and the look-alike point is dropped
		points = [point for point in points if not (point["rule"] == "F8" and point["line_no"] in against.found)] + against.points
	kept = trend = None
	if month is not None:
		from trustbit_ethanol.ts_gate_entry import ts_ai_check_months as months

		kept = months.keep(book, rows, month["day"])
		# with no comparison with the ERP (rule E7) no line is known to be a typed one
		trend = months.compare(kept, month["before"], rows, (set(against.says) if against.ran else None) if against else ())
		points = points + trend.points
	worst = {}
	for point in points:
		if point["line_no"] and point["level"] in (SERIOUS, CHECK) and worst.get(point["line_no"]) != SERIOUS:
			worst[point["line_no"]] = point["level"]
	lines, extras = [], []
	for row in rows:
		qty = f"{_fig(row['qty'])} {row['uom']}".strip() if row["qty"] is not None else ""
		rate = f"rate {_fig(row['rate'])}" if row["rate"] is not None else ""
		details = " · ".join(part for part in (qty, rate, row["cc"], row["place"]) if part)
		lines.append({"line_no": row["line_no"], "page": 0, "source_row": row["row"], "kind": "Other", "supplier_text": row["name"],
			"bill_no_text": row["bill"], "bill_date_text": "", "po_text": "", "amount_text": row["written"],
			"amount": row["amount"] or 0.0, "remarks_text": row["remark"],
			"unclear": "amount" if row["error"] in ("amount", "text") or row["amount"] is None else "",
			"read_note": {"alone": "Taken from the summary: no sheet of this head was read.", "machines": "Taken from the summary: "
				"its sheet is a machine table shared between heads; the table's lines are listed and not counted."}.get(row.get("summary"), ""),
			"section_text": row["sheet"] + ("" if row["main"] else f" ({row.get('aside', 'side table')})"), "details_text": details[:500]})
		extras.append({"verdict": worst.get(row["line_no"], OK), "supplier": "", "supplier_title": "", "match_how": row["cc"],
			"list_says": details[:1000], "erp_says": against.says.get(row["line_no"], "") if against else "", "chain": "[]", "docs": [], "receipts_used": "", "claims": "[]"})
	# a table beside the main one repeats money the main table already holds (a break-up, a ledger): its lines
	# are listed and checked, and stay out of every sum, so the total is the one the summary sheet carries
	beside = [line["amount"] for line, row in zip(lines, rows) if not row["main"] and "aside" not in row]
	machines = [line["amount"] for line, row in zip(lines, rows) if "aside" in row]
	counted = [line if row["main"] else {**line, "amount": 0.0} for line, row in zip(lines, rows)]
	sums = totals(counted, [extra["verdict"] for extra in extras])
	total = round(sum(line["amount"] for line in counted), 2)
	read = [sheet for sheet in book["sheets"] if sheet["tables"] or sheet.get("matrix")]
	unread = [f"'{sheet['title']}'" for sheet in book["sheets"] if not sheet["tables"] and not sheet.get("summary") and "copy" not in sheet
		and not sheet.get("matrix")]
	copies = [f"'{sheet['title']}' ({sheet['agrees']})" if sheet.get("agrees") else f"'{sheet['title']}'" for sheet in book["sheets"] if "copy" in sheet]
	text = (f"{len(lines)} lines on {len(read)} sheet(s), {money(total)}. No issue found: {sums[OK][0]}. To check: {sums[CHECK][0]}. "
		f"Serious: {sums[SERIOUS][0]}. Each line sits in one group only, by its heaviest point.")
	if beside:
		text += (f" {len(beside)} of the lines stand in a table beside or under the main one ({money(round(sum(beside), 2))}): "
			"they are checked, and not counted in the total.")
	if machines:
		text += (f" {len(machines)} of the lines are from a machine table ({money(round(sum(machines), 2))}): they are checked, and "
			"not counted in the total; its heads are counted from the summary.")
	whole = [point["level"] for point in points if not point["line_no"]]
	if SERIOUS in whole or CHECK in whole:  # a point on a sheet or on the file is in no line's count
		text += f" On a sheet or on the whole file: {whole.count(SERIOUS)} Serious, {whole.count(CHECK)} To check."
	summary = book["summary"]
	coverage = [f"Sheets with a table that was read: {len(read)} of {len(book['sheets'])}."
		+ (f" No table with the usual column titles on: {', '.join(unread[:20])}." if unread else "")]
	if summary:
		coverage.append(f"Summary sheet '{summary['sheet']}': {len(summary['heads'])} heads"
			+ (f", production {summary['litres']:,.2f} litres." if summary["litres"] else ".")
			+ (f" A second summary sheet was set against it: {', '.join(copies[:5])}." if copies else ""))
	else:
		coverage.append("No summary sheet in this file: each sheet's own total was checked, and nothing was compared with a summary.")
	for sheet in book["sheets"]:
		matrix = sheet.get("matrix")
		if matrix:
			coverage.append(f"'{sheet['title']}' was read as a machine table: {len(matrix['machines'])} machines, {len(matrix['groups'])} groups; "
				"its own arithmetic was checked."
				+ (f" On the summary, {sheet['traced']}." if sheet.get("traced") else "")
				+ f" What stands under the table (from row {matrix['last_row'] + 1}) was not checked"
				+ (", apart from the table(s) read there." if any(table["head_row"] > matrix["last_row"] for table in sheet["tables"]) else "."))
	coverage += [
		"Checked: every total against its lines, the summary against each head's sheet, the per-litre figures against the "
		"litres, quantity × rate, lines under another cost centre, and lines written twice.",
		"The figures are the ones Excel last saved in the file."
		+ ("" if against else " The lines are not compared with the issues recorded in the ERP."),
	]
	coverage += against.coverage if against else []
	coverage += trend.coverage if trend else []
	# what this check cannot tell: the MD's report says it in bold, under the summary
	cannot = ["A bill, a hire charge, hours, diesel or petty cash typed into the sheet is no issue of the ERP: its arithmetic is "
		"checked, not whether its amount is right."]
	if not against or not against.says:
		cannot.append("No line of this file was compared with the issues recorded in the ERP.")
	if trend and trend.table:
		per = " a litre" if trend.table["litres"] else ""
		cannot += [f"A {trend.table['by']}'s move against {trend.table['before']} raises a point only when it is {months.MOVE:.0%} or more{per} "
			f"and at least {months.SHARE:.0%} of this report's total. A smaller move raises none"
			+ ("; neither does the same money over more or fewer litres." if per else "."),
			"A file sent under the wrong month is noticed only when its figures are the same as the other month's to the paisa."]
	elif trend:
		cannot += trend.coverage
	cannot.append("The AI's summary and the points marked AI are the AI's own reading: code checks that each figure in them is in "
		"this report, not that the conclusion is right.")
	notes = [f"'{sheet['title']}': {sheet['skipped']} row(s) were skipped (no item name, or neither an amount nor a quantity)."
		for sheet in book["sheets"] if sheet["skipped"]][:25]
	result = frappe._dict(lines=lines, printed_total=None, list_total=total, notes=notes, file_kind=reader.FILE_KINDS[kind],
		read_by="Code", pages_in_file=0, pages_seen=0, meta={})
	checked = frappe._dict(lines=extras, findings=points, summary=text, coverage="\n".join(coverage),
		n_ok=sums[OK][0], n_check=sums[CHECK][0], n_serious=sums[SERIOUS][0],
		amt_ok=sums[OK][1], amt_check=sums[CHECK][1], amt_serious=sums[SERIOUS][1],
		trend=trend.table if trend else None, figures=json.dumps({"now": kept, "trend": trend.table, "limits": cannot}, ensure_ascii=False) if kept else "")
	return result, checked
