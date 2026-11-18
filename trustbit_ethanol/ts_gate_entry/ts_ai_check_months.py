"""AI Checker, MST report, step 3: this month's report set against last month's.

Every checked MST report keeps its own figures (`figures` on the report): the heads of its summary, the litres,
and the items of each sheet. The next month's report reads them. Rules P1 to P3; code only, no AI.

The earlier report is read whatever the submitter's own rights on reports: what comes of it goes into the new
report, which only the MD opens, and no P rule is among the points shown to the submitter."""

import json
import re

import frappe
from frappe.utils import add_months, cstr, flt, formatdate, get_first_day, get_last_day, getdate

from trustbit_ethanol.ts_gate_entry.ts_ai_check_issues import item_key
from trustbit_ethanol.ts_gate_entry.ts_ai_check_rules import CHECK, NOTE, money

MOVE = 0.25  # a head a quarter up or down against last month ...
SHARE = 0.01  # ... by at least a hundredth of this month's total: below that a jump is small change
SAME_FROM = 1000.0  # rupees; a typed line of at least this much that repeats last month's amount to the paisa
TOP = 3  # the items named behind a head that moved
REPORT, MST = "TS AI Check Report", "MST Report"
KEPT = {"label", "litres", "total", "heads", "sheets"}  # what `keep` stores, and `before` asks of a stored value


def keep(book, rows, day):
	"""What a report keeps of its workbook for the month after it: plain texts, numbers and lists."""
	from trustbit_ethanol.ts_gate_entry import ts_ai_check_mst as mst

	summary = book.get("summary") or {}
	sheets = {}
	for row in rows:
		if not row["main"] or row.get("summary"):
			continue
		sheet = sheets.setdefault(row["sheet"], {"total": 0.0, "items": {}})
		sheet["total"] += row["amount"] or 0
		item = sheet["items"].setdefault(item_key(row["name"]), [row["name"], row["uom"], 0.0, 0.0, 0])
		item[2], item[3], item[4] = item[2] + (row["qty"] or 0), item[3] + (row["amount"] or 0), item[4] + 1
	pairs = mst._pair(book)[0] if summary.get("heads") else {}
	# a head whose amount shows an error is a head all the same: it is kept with no amount
	heads = [{"name": head["head"], "amount": round(head["amount"], 2) if head["amount"] is not None else None,
		"sheet": (pairs.get(head["row"]) or {}).get("title", ""), "outside": bool(head["outside"])} for head in summary.get("heads", ())]
	total = (summary.get("total") or {}).get("amount")
	return {"month": getdate(day).strftime("%Y-%m"), "label": formatdate(day, "MMMM yyyy"), "litres": flt(summary.get("litres")),
		"total": round(total if total is not None else sum(head["amount"] or 0 for head in heads) if heads
			else sum(sheet["total"] for sheet in sheets.values()), 2), "heads": heads,
		"sheets": {title: {"total": round(sheet["total"], 2), "items": [[name, uom, round(qty, 3), round(amount, 2), count]
			for name, uom, qty, amount, count in sheet["items"].values()]} for title, sheet in sheets.items()}}


def before(company, day):
	"""The figures of the fullest checked MST report of the month before `day`: the one with the most heads, then
	one with sheets, then the newest. {"label": that month, "report": its name or "", "now": its figures or None,
	"of": how many reports of that month have figures}."""
	first = get_first_day(add_months(getdate(day), -1))
	out = {"label": formatdate(first, "MMMM yyyy"), "report": "", "now": None, "of": 0}
	best = None
	for name in frappe.get_all(REPORT, filters={"check_type": MST, "company": company, "list_date": ["between", [first, get_last_day(first)]]},
			pluck="name", order_by="creation desc", limit=500):
		try:
			kept = json.loads(frappe.db.get_value(REPORT, name, "figures") or "{}").get("now")
		except (ValueError, AttributeError):
			kept = None
		if not isinstance(kept, dict) or not KEPT <= set(kept) or not isinstance(kept["heads"], list) or not isinstance(kept["sheets"], dict):
			continue  # a report from before the figures were kept, or a value of another shape
		out["of"] += 1
		rank = (len(kept["heads"]), bool(kept["sheets"]))
		if best is None or rank > best[0]:  # the newest comes first: an equal one that is older does not replace it
			best = (rank, name, kept)
	return {**out, "report": best[1], "now": best[2]} if best else out


def _litre(amount, litres):
	return amount / litres if litres and amount is not None else None


def _moved(was, now):
	"""How far `now` is from `was`, as a share of `was`; None when either is missing or `was` is nothing."""
	return (now - was) / abs(was) if was and now is not None else None


def _rate(value):
	return f"₹{value:,.2f}" if abs(value) >= 1 else f"₹{value:.4f}"


def _q(name):
	"""A name typed in the workbook, marked as such and cut: it must not read as a sentence of the checker's own."""
	name = re.sub(r"\s+", " ", cstr(name)).strip()
	return "'" + (name[:60] + "…" if len(name) > 60 else name) + "'"


def _keyed(heads):
	"""Heads by name, and by which of that name it is: two heads called the same are two heads."""
	seen, out = {}, {}
	for head in heads:
		base = item_key(head["name"]) or re.sub(r"\s+", " ", cstr(head["name"])).strip().lower()
		seen[base] = seen.get(base, 0) + 1
		out[(base, seen[base])] = head
	return out


def compare(now, earlier, rows, compared=()):
	"""`now` = this report's figures (`keep`), `earlier` = what `before` gave, `rows` = the workbook's lines,
	`compared` = the lines the ERP comparison has spoken of (None: it did not run, so no line is known to be
	a typed one). Returns the points, the sentences for the cover, and `table`: the heads of both months side
	by side, for the report page and for the AI."""
	out = frappe._dict(points=[], coverage=[], table=None)
	was = earlier.get("now")
	if not was:
		out.coverage.append(f"No checked MST report of {earlier['label']} for this company: nothing was compared month on month.")
		return out

	def add(level, rule, title, detail, line_no=0):
		out.points.append({"line_no": line_no, "level": level, "source": "Rule", "rule": rule, "title": title, "detail": detail,
			"refs": earlier["report"]})

	# heads when both reports have a summary; else the sheets the two have in common, by their totals
	by_head = bool(now["heads"] and was["heads"])
	renamed = {}
	if by_head:
		mine, theirs = _keyed(now["heads"]), _keyed(was["heads"])
		# a head that is new by name and stands on the sheet of a head that is gone is that head under another name
		for key in [key for key in mine if key not in theirs and mine[key]["sheet"]]:
			old = [other for other in theirs if other not in mine and item_key(theirs[other]["sheet"]) == item_key(mine[key]["sheet"])]
			if len(old) == 1:
				renamed[key] = theirs[old[0]]["name"]
				theirs[key] = theirs.pop(old[0])
	else:
		as_head = lambda sheets: {(item_key(title), 1): {"name": title, "amount": sheet["total"], "sheet": title}  # noqa: E731
			for title, sheet in sheets.items()}
		mine, theirs = as_head(now["sheets"]), as_head(was["sheets"])
		theirs = {key: head for key, head in theirs.items() if key in mine}
		if not theirs:
			out.coverage.append(f"The MST report of {was['label']} ({earlier['report']}) has none of this file's sheets: nothing was "
				"compared month on month.")
			return out
	litres = (was["litres"], now["litres"]) if by_head and was["litres"] and now["litres"] else None
	floor = SHARE * abs(now["total"] or 0)
	their_sheets = {item_key(title): sheet for title, sheet in was["sheets"].items()}

	def behind(head, old):
		"""The items of the head's sheet whose amount moved most between the two months."""
		here, there = now["sheets"].get(head["sheet"]), their_sheets.get(item_key(old["sheet"]))
		if not here or not there:
			return []
		a, b = {item_key(item[0]): item for item in there["items"]}, {item_key(item[0]): item for item in here["items"]}
		moves = [((b[key][0] if key in b else a[key][0]), a[key][3] if key in a else 0.0, b[key][3] if key in b else 0.0) for key in {*a, *b}]
		return [list(move) for move in sorted(moves, key=lambda move: -abs(move[2] - move[1]))[:TOP] if abs(move[2] - move[1]) >= 0.005]

	table = []
	for key in [*mine, *[key for key in theirs if key not in mine]]:
		head, old = mine.get(key), theirs.get(key)
		a, b = (old or {}).get("amount"), (head or {}).get("amount")
		per = (_litre(a, litres[0]), _litre(b, litres[1])) if litres else (None, None)
		change = _moved(*per) if litres else _moved(a, b)
		row = {"head": (head or old)["name"], "was": a, "now": b, "was_litre": per[0], "now_litre": per[1],
			"change": round(change, 4) if change is not None else None, "flag": "", "behind": [],
			"outside": bool((head or old).get("outside")), "said": ""}
		name = _q(row["head"])
		if head and old and a is not None and b is not None:
			# the figures in words, as the points and the AI may say them
			way = "no change" if not change else f"{'up' if change > 0 else 'down'} {abs(change):.0%}"
			row["said"] = (f"{money(a)} ({_rate(per[0])} a litre) in {was['label']}, {money(b)} ({_rate(per[1])} a litre) in this report"
				if litres else f"{money(a)} in {was['label']}, {money(b)} in this report") + (
				f": {way}{' a litre' if litres and change else ''}" if a else "")
		if head and old and change is not None and abs(change) >= MOVE and abs(b - a) >= floor:
			row["flag"], row["behind"] = "moved", behind(head, old)
			items = "; ".join(f"{_q(item)} {money(x)} → {money(y)}" for item, x, y in row["behind"])
			add(CHECK, "P1", "Cost per litre moved against last month" if litres else "Amount moved against last month",
				f"{name}: {row['said']}." + (f" The items of its sheet that moved most: {items}." if items else ""))
		elif head and old and a == 0 and b and abs(b) >= floor:  # from nothing: no percentage can say it
			row["flag"], row["behind"] = "moved", behind(head, old)
			add(CHECK, "P1", "Amount moved against last month", f"{name}: nothing in {was['label']}, {money(b)} in this report.")
		elif old and not head and by_head and a:
			row["flag"] = "gone"
			add(CHECK, "P2", "A head of last month is not in this report", f"{name}: {money(a)} in {was['label']}; this report "
				"has no head of that name.")
		elif head and not old and by_head and b:
			row["flag"] = "new"
			add(NOTE, "P2", "A head that last month's report did not have", f"{name}: {money(b)} in this report; the report of "
				f"{was['label']} has no head of that name.")
		table.append(row)
	both = [row for row in table if row["was"] is not None and row["now"] is not None]
	if by_head and len(both) == len(table) >= 2 and all(abs(row["was"] - row["now"]) < 0.005 for row in both) and (
			not (was["litres"] or now["litres"]) or abs(was["litres"] - now["litres"]) < 0.005):
		add(CHECK, "P2", "Last month's report holds the same figures as this one", f"Every head, and the litres, are the same to the paisa "
			f"in this report and in the report of {was['label']}. Was one of the two files sent with the wrong month?")

	# a typed line (a bill, a hire charge: the ERP has no issue to set it against) that repeats last month's amount
	same, lines_of = 0, {}
	for line in rows if compared is not None else ():
		if line["main"] and not line.get("summary"):
			lines_of[(line["sheet"], item_key(line["name"]))] = lines_of.get((line["sheet"], item_key(line["name"])), 0) + 1
	for line in rows if compared is not None else ():
		there = their_sheets.get(item_key(line["sheet"]))
		if not line["main"] or line.get("summary") or line["line_no"] in compared or not there or abs(line["amount"] or 0) < SAME_FROM:
			continue
		old = [item for item in there["items"] if item_key(item[0]) == item_key(line["name"])]
		if len(old) == 1 and old[0][4] == 1 and lines_of[(line["sheet"], item_key(line["name"]))] == 1 and abs(old[0][3] - line["amount"]) < 0.005:
			same += 1
			add(NOTE, "P3", "The same amount as last month", f"{line['sheet']} row {line['row']}: {_q(line['name'])}: {money(line['amount'])} here "
				f"and in the report of {was['label']}. A fixed monthly charge repeats; a bill by the meter seldom does to the paisa.", line["line_no"])

	total = [was["total"], now["total"]] if by_head else None
	total_row = None
	if total:  # the summary's own total: the heads written under it are not in it, and are listed after it
		per = (_litre(total[0], litres[0]), _litre(total[1], litres[1])) if litres else (None, None)
		change = _moved(*per) if litres else _moved(*total)
		way = "no change" if not change else f"{'up' if change > 0 else 'down'} {abs(change):.0%}"
		total_row = {"head": "Total", "was": total[0], "now": total[1], "was_litre": per[0], "now_litre": per[1],
			"change": round(change, 4) if change is not None else None, "flag": "", "behind": [], "outside": False,
			"said": (f"{money(total[0])} ({_rate(per[0])} a litre) in {was['label']}, {money(total[1])} ({_rate(per[1])} a litre) in this "
				f"report: {way}{' a litre' if change else ''}" if litres else f"{money(total[0])} in {was['label']}, {money(total[1])} in this report: {way}")}
	out.table = {"before": was["label"], "report": earlier["report"], "by": "head" if by_head else "sheet",
		"litres": list(litres) if litres else None, "total": total, "rows": table, "total_row": total_row}
	out.coverage.append(f"Set against the MST report of {was['label']} ({earlier['report']}): {len(both)} {'head' if by_head else 'sheet'}(s) "
		f"in both, by {'cost per litre' if litres else 'amount'}. A point is raised when one moved by {MOVE:.0%} or more and by at least "
		f"{money(floor)} ({SHARE:.0%} of this report's total)"
		+ ("" if litres or not by_head else "; the litres are missing in one of the two, so the amounts were compared")
		+ (f". Typed lines of {money(SAME_FROM)} or more with last month's amount to the paisa: {same}." if compared is not None
			else ". The lines were not set against the ERP, so none is known to be a typed line: repeated amounts were not looked for.")
		+ (f" {earlier['of']} checked reports of {was['label']} have figures; the one with the most heads was used." if (earlier.get("of") or 0) > 1 else "")
		+ ("".join(f" {_q(mine[key]['name'])} was taken as last month's {_q(old)}: it stands on the same sheet." for key, old in renamed.items())))
	return out
