"""TS Order Watch: the rules (build spec §2-§4). Pure: the report's loader hands over one data dict and gets
back {"points": [...], "ranked": [...], "limits": [...], "gaps": [...]}. No database, no session. Amounts are
rupees (base_grand_total); people appear by role only: account ids are compared here and never printed.

Limits by date (20 Nov 2026): an order is measured against the approval limit in force when it was last sent
for approval, not against today's. history() rebuilds the rule table of any day from today's rows, their change
record and the deleted rules."""

import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from frappe import _

LAST_LINE = "The ERP shows the link, not the reason."
LEVELS = ("Serious", "To check", "Note")
MERGED = ("R30", "R29", "R31", "R35", "R36", "R39", "R38/R30", "R38/R29", "R38/R35")  # one point per group
SPLIT = ("R30", "R29", "R31", "R35", "R38/R30", "R38/R29", "R38/R35")  # these print the facts against a split
KINDS = ("supplier", "item", "request")
REVIEW_FLOOR = 100000.0  # R39's 1 lakh, and R41's "more than 1 lakh"
EARLY_IS_ROUTINE = ("Grain", "Coal")  # R43: goods before the order is a Note here
CLOSED = ("Closed", "Completed", "Cancelled", "Delivered")
SUBMIT_ACTIONS = ("Submitted", "Resubmitted", "Resumed after Budget Override")  # the three that stamp an order's route
DAY, WEEK, HALF_HOUR = timedelta(days=1), timedelta(days=7), timedelta(minutes=30)
PM_ROLE = "Purchase Manager"  # role of the 1-lakh review step in TS PO Approval Step
STEP_TABLE = "approval_steps"
RULE_FIELDS = {"purchase_category": "cat", "min_amount": "low", "max_amount": "high", "is_active": "on"}
PAD, MARKS_CAP = 14 * DAY, 200  # the limit chart: days shown around a change, and the most order marks sent
FOLD = timedelta(minutes=5)  # a shorter stretch in which no order was sent for approval is not shown as a limit


def find_points(data):
	w = _Watch(data)
	found = w.r30() + w.r29() + w.r31() + w.r35() + w.r36() + w.r39()
	found += w.r30("pm", "R38/R30") + w.r29("pm", "R38/R29") + w.r35("pm", "R38/R35")
	points = _merge(found) + w.r41() + w.r42() + w.r43() + w.r44() + w.r45()
	for p in points:
		p["extra_rows"].append(_(LAST_LINE))
	points.sort(key=lambda p: (LEVELS.index(p["level"]), -(p["over_by"] or 0)))
	return {"points": points, "ranked": w.r32(), "limits": w.limits(), "gaps": w.gaps()}


# -- the limits by date --
def lines_of(table):
	"""The MD line and the review line of each category in one state of the rule table (spec 3.1): the lowest
	active band whose steps name the role. The review line counts only when a band without that step lies below."""
	lines, live = {}, [r for r in table if r.get("on")]
	for cat in {r.get("cat") for r in live}:
		act = [(_f(r.get("low")), {s.get("role") for s in r.get("steps") or []}) for r in live if r.get("cat") == cat]
		md = min((low for low, roles in act if "MD" in roles), default=None)
		pm = min((low for low, roles in act if PM_ROLE in roles), default=None)
		if pm is not None and not any(low < pm and PM_ROLE not in roles for low, roles in act):
			pm = None  # no CEO-alone band below the review step
		lines[cat] = {"md": md, "pm": pm}
	return lines


def _amount(v):
	"""A value of a change record back to a number. Frappe keeps a non-zero Currency as printed text
	("₹ 6,00,000.00") and a zero or empty one as it is. None: the text cannot be read as a number."""
	if v in (None, "", 0):
		return 0.0
	if isinstance(v, (int, float)):
		return float(v)
	text = re.sub(r"[^\d.,-]", "", str(v))
	return float(text.replace(",", "")) if re.fullmatch(r"-?\d[\d,]*(\.\d+)?", text) else None


def _state(r, steps):
	return {"cat": r.get("cat") or "", "low": _f(r.get("low")), "high": _f(r.get("high")), "on": int(_f(r.get("on"))),
		"steps": [{"name": s.get("name"), "step_order": int(_f(s.get("step_order"))), "role": s.get("role")} for s in steps or []]}


def _undo(state, diff):
	"""The rule before a change, from the rule after it: one change record read backwards. Also says what does not
	add up: "chain" (the record's new value is not the value held after it, so a later change has no record) and
	"unreadable" (a value that is not a number: the field is left as it is, never guessed)."""
	prev, issues = dict(state, steps=[dict(s) for s in state["steps"]]), []
	if not isinstance(diff, dict):
		return prev, ["unreadable"]
	for c in diff.get("changed") or []:
		if len(c) != 3 or c[0] not in RULE_FIELDS:
			continue
		k = RULE_FIELDS[c[0]]
		old, new = (c[1] or "", c[2] or "") if k == "cat" else (_amount(c[1]), _amount(c[2]))
		if old is None or new is None:
			issues.append("unreadable")
			continue
		if not (new == state[k] if k == "cat" else abs(new - state[k]) < 0.005):
			issues.append("chain")
		prev[k] = old if k in ("cat", "low", "high") else int(old)
	for c in diff.get("added") or []:  # a step row added then: it was not there before
		if len(c) == 2 and c[0] == STEP_TABLE and isinstance(c[1], dict):
			kept = [s for s in prev["steps"] if s.get("name") != c[1].get("name")]
			if len(kept) == len(prev["steps"]):
				issues.append("chain")
			prev["steps"] = kept
	for c in diff.get("removed") or []:  # a step row removed then: it was there before
		if len(c) == 2 and c[0] == STEP_TABLE and isinstance(c[1], dict):
			prev["steps"].append({"name": c[1].get("name"), "step_order": int(_f(c[1].get("step_order"))), "role": c[1].get("role")})
	for c in diff.get("row_changed") or []:  # [table, row index, row name, [[field, old, new], ...]]
		if len(c) != 4 or c[0] != STEP_TABLE:
			continue
		row = next((s for s in prev["steps"] if s.get("name") == c[2]), None)
		if row is None:
			issues.append("chain")
			continue
		for f in c[3] or []:
			if len(f) == 3 and f[0] == "role":
				if (f[2] or "") != (row.get("role") or ""):
					issues.append("chain")
				row["role"] = f[1] or None
			elif len(f) == 3 and f[0] == "step_order":
				row["step_order"] = int(_amount(f[1]) or 0)
	return prev, issues


def history(now, versions=(), deleted=()):
	"""The rule table through time.

	now:      {rule: {"cat", "low", "high", "on", "creation", "owner", "steps": [{"name", "step_order", "role"}]}}
	versions: [{"docname", "at", "by", "data": the change record}] of the rules (a new rule writes none)
	deleted:  [{"name", "at", "by", "doc": the rule as it was deleted}] (deleting a rule removes its change record)

	Each rule is walked back from today through its change records to the day it was made; a deleted rule lived
	from its making to its deletion as it was deleted. Gives {"rules": {rule: [state + "from", "to", "by"]},
	"lines": {category: {"md" | "pm": [{"from", "line", "by"}]}}, "gaps": [{"rule", "cat", "from", "to", "why"}]}.
	The day a category's first rule was made is its setup: its first entry has "from" None and holds for every
	earlier order too, with "since" = that day."""
	lives, gaps, record = [], [], defaultdict(list)
	for v in versions or ():
		if _dt(v.get("at")):
			record[v.get("docname")].append(v)
	for name, r in (now or {}).items():
		state, born, until, segs = _state(r, r.get("steps")), _dt(r.get("creation")) or datetime(1970, 1, 1), None, []
		for v in sorted(record.get(name, []), key=lambda v: _dt(v["at"]), reverse=True):
			at = _dt(v["at"])
			if at <= born:
				continue
			prev, issues = _undo(state, v.get("data"))
			segs.append({**state, "from": at, "to": until, "by": v.get("by")})
			for why in sorted(set(issues)):  # chain: the stretch after this record; unreadable: all before it
				gaps.append({"rule": name, "cat": state["cat"], "from": at if why == "chain" else None,
					"to": until if why == "chain" else at, "why": why})
			state, until = prev, at
		segs.append({**state, "from": born, "to": until, "by": r.get("owner")})
		lives.append((name, segs[::-1]))
	for d in deleted or ():
		doc = d.get("doc") if isinstance(d.get("doc"), dict) else {}
		born, died = _dt(doc.get("creation")), _dt(d.get("at"))
		state = _state({"cat": doc.get("purchase_category"), "low": doc.get("min_amount"), "high": doc.get("max_amount"),
			"on": doc.get("is_active")}, doc.get(STEP_TABLE))
		if not born or not died or born >= died:
			gaps.append({"rule": d.get("name"), "cat": state["cat"], "from": None, "to": died, "why": "deleted"})
			continue
		lives.append((d.get("name"), [{**state, "from": born, "to": died, "by": doc.get("owner"), "end_by": d.get("by")}]))
		edited = _dt(doc.get("modified"))
		if edited and edited - born > timedelta(seconds=1):  # its edits went with its change record
			gaps.append({"rule": d.get("name"), "cat": state["cat"], "from": born, "to": edited, "why": "deleted"})
	every, first = [s for name, segs in lives for s in segs], {}
	for s in every:
		if s["cat"] not in first or s["from"] < first[s["cat"]]:
			first[s["cat"]] = s["from"]
	lines = {cat: {"md": [], "pm": []} for cat in first}
	began, ended = {}, {}  # who began / ended a stretch of a category at a moment (looked up once, not scanned per moment)
	for s in every:
		began.setdefault((s["cat"], s["from"]), s.get("by"))
		if s.get("end_by"):
			ended.setdefault((s["cat"], s["to"]), s["end_by"])
	for t in sorted({x for s in every for x in (s["from"], s["to"]) if x is not None}):
		at = lines_of([s for s in every if s["from"] <= t and (s["to"] is None or t < s["to"])])
		for cat, start in first.items():
			if t < start:
				continue
			who = began.get((cat, t)) or ended.get((cat, t))
			for key in ("md", "pm"):
				v, run = (at.get(cat) or {}).get(key), lines[cat][key]
				if t.date() == start.date():
					run[:] = [{"from": None, "since": start, "line": v, "by": None}]
				elif run[-1]["line"] != v:
					run.append({"from": t, "line": v, "by": who})
	table = defaultdict(list)
	for name, segs in lives:
		table[name] += segs
	return {"rules": {name: sorted(segs, key=lambda s: s["from"]) for name, segs in table.items()}, "lines": lines, "gaps": gaps}


def state_at(rule_history, rule, at):
	"""The rule as it stood at `at`: its last state begun by then; before its record starts, its first state."""
	segs = (rule_history or {}).get(rule) or []
	hit = [s for s in segs if s["from"] <= at]
	return hit[-1] if hit else (segs[0] if segs else None)


def entry_at(line_history, category, key, at):
	run = ((line_history or {}).get(category) or {}).get(key) or []
	hit = [e for e in run if e["from"] is None or e["from"] <= at]
	return hit[-1] if hit else (run[0] if run else None)


def line_at(line_history, category, key, at):
	e = entry_at(line_history, category, key, at)
	return None if e is None or e["line"] is None else _f(e["line"])


def routed(order, logs):
	"""When the order was last sent for approval, and the amount its route was picked on: the last submission row
	of its log. No such row (outside the chain, or waiting on a budget override): its entry time, no amount."""
	subs = [l for l in logs or [] if l.get("action") in SUBMIT_ACTIONS and _dt(l.get("action_date"))]
	if not subs:
		return _dt(order.get("creation")), None
	last = max(subs, key=lambda l: _dt(l["action_date"]))
	return _dt(last["action_date"]), _f(last.get("po_amount"))


def _merge(found):
	"""A group gives one point: a finding on the same orders joins it, and the point keeps the highest level and
	names every check. A finding on some of a bigger point's orders joins it only at its level or below; a
	higher one stays its own point (spec R30: Serious only when the same members make the R29 Serious group)."""
	found.sort(key=lambda p: (-len(p["orders"]), MERGED.index(p["checks"][0]), LEVELS.index(p["level"])))
	out = []
	for p in found:
		host = next((h for h in out if set(p["orders"]) == set(h["orders"]) or (set(p["orders"]) < set(h["orders"])
			and LEVELS.index(p["level"]) >= LEVELS.index(h["level"]))), None)
		if host is None:
			out.append(p)
			continue
		if p["checks"][0] not in host["checks"]:
			host["checks"].append(p["checks"][0])
		if LEVELS.index(p["level"]) < LEVELS.index(host["level"]):
			host["level"] = p["level"]
		host["extra_rows"].append("{0}: {1}{2}".format(p["checks"][0], p["summary"], " " + p["tie"] if p["tie"] else ""))
	return out


def _f(v):
	try:
		return float(v or 0)
	except (TypeError, ValueError):
		return 0.0


def _dt(v):
	if v in (None, ""):
		return None
	if isinstance(v, datetime):
		return v
	if isinstance(v, date):
		return datetime(v.year, v.month, v.day)
	return datetime.fromisoformat(str(v))


def _d(v):
	v = _dt(v)
	return v.date() if v else None


def _inr(v):
	"""Whole rupees with Indian grouping: 600540 -> 6,00,540."""
	n = int(round(_f(v)))
	s, tail = str(abs(n)), ""
	if len(s) > 3:
		s, tail = s[:-3], "," + s[-3:]
		while len(s) > 2:
			s, tail = s[:-2], "," + s[-2:] + tail
	return ("-" if n < 0 else "") + s + tail


def _num(v):
	v = _f(v)
	return _inr(v) if abs(v - round(v)) < 1e-9 else "{0:.2f}".format(v)


def _when(v):
	return _dt(v).strftime("%d-%m-%Y %H:%M")


def _span(td):
	hours = td.total_seconds() / 3600
	if hours <= 1:
		return _("within an hour")
	if hours < 48:
		return _("within {0} hours").format(int(-(-hours // 1)))
	return _("over {0} days").format(int(-(-hours // 24)))


def _live(o):
	"""Approved, or still in approval; never cancelled or rejected."""
	st, ds = o.get("approval_status") or "", int(o.get("docstatus") or 0)
	if st == "Rejected":
		return False
	return ds == 1 or (ds == 0 and (st.startswith("Pending") or st.startswith("Awaiting Send to")))


def _key(o):
	return (o["at"], o["name"])


def _sum(run):
	return sum(o["v"] for o in run)


def _suppliers(run):
	return {o.get("supplier") for o in run}


def _windows(orders, span, when, ok):
	"""Greedy runs: anchor at the first order and take every order within `span` of it. A run that passes `ok`
	(and has 2 or more orders) is a group and the next anchor is the order after it; else the anchor moves on."""
	orders = sorted(orders, key=lambda o: (when(o), o["name"]))
	out, i = [], 0
	while i < len(orders):
		j = i
		while j + 1 < len(orders) and when(orders[j + 1]) - when(orders[i]) <= span:
			j += 1
		run = orders[i:j + 1]
		if len(run) >= 2 and ok(run):
			out.append(run)
			i = j + 1
		else:
			i += 1
	return out


def _catch_all(rows):
	"""Item codes whose rate on the loaded order lines differs 10 times or more (3 lines at least)."""
	rates = defaultdict(list)
	for r in rows:
		if _f(r.get("base_rate")) > 0:
			rates[r.get("item_code")].append(_f(r.get("base_rate")))
	return {c for c, rs in rates.items() if len(rs) >= 3 and max(rs) >= 10 * min(rs)}


class _Watch:
	def __init__(self, data):
		self.data = data
		self.lines = data.get("lines") or {}
		# no history handed over: today's lines hold for every date (one entry each)
		hist = data.get("line_history") or {c: {k: [{"from": None, "line": v, "by": None}] for k, v in (l or {}).items()}
			for c, l in self.lines.items()}
		self.frm, self.to = _d(data.get("from_date")), _d(data.get("to_date"))
		self.lo, self.hi = datetime.combine(self.frm, time.min), datetime.combine(self.to + DAY, time.min)
		self.today = _d(data.get("today")) or self.to
		self.logs, self.steps = data.get("logs") or {}, data.get("rule_steps") or {}
		self.orders = {}
		for name, src in (data.get("orders") or {}).items():
			o = dict(src, name=src.get("name") or name)
			o["v"], o["at"] = _f(o.get("base_grand_total")), _dt(o.get("creation"))
			if o["at"]:
				# sorting and the 7-day windows stay on the entry time; the route time only picks the limit
				o["routed"], o["amt"] = routed(o, self.logs.get(o["name"]))
				self.orders[o["name"]] = o
		sent = defaultdict(list)
		for o in self.orders.values():
			sent[o.get("category")].append(o["routed"])
		self.hist = {c: {k: _fold(run, sent[c]) for k, run in h.items()} for c, h in hist.items()}
		for o in self.orders.values():
			o["e"] = {k: entry_at(self.hist, o.get("category"), k, o["routed"]) for k in ("md", "pm")}
		self.sorted = sorted(self.orders.values(), key=_key)
		self.rows, self.by_mri, self.amt = defaultdict(list), defaultdict(list), {}
		for r in data.get("items") or []:
			if r.get("po") in self.orders:
				self.rows[r["po"]].append(r)
				if r.get("material_request_item"):
					self.by_mri[r["material_request_item"]].append(r)
		self.names = {r.get("item_code"): r.get("item_name") for rs in self.rows.values() for r in rs}
		self.catch_all = _catch_all([r for rs in self.rows.values() for r in rs])
		self.receipts, self.mr_items = data.get("receipts") or {}, data.get("mr_items") or {}
		lo = self.frm - WEEK  # the loader adds the 7 days before the window, so a group across its start is whole
		self.small = [o for o in self.sorted if _live(o) and not o.get("md_approved") and lo <= o["at"].date() <= self.to]

	# -- small readers --
	def line(self, o, key="md"):
		"""The limit in force when the order was last sent for approval."""
		e = o["e"][key]
		return None if e is None or e["line"] is None else _f(e["line"])

	def seg(self, o, key="md"):
		"""Which stretch of the limit's history the order falls in (its start): a group never mixes two."""
		e = o["e"][key]
		return e["from"] if e else None

	def used(self, o, key):
		"""The limit an order was measured against, said in words; only where the category's limit ever changed."""
		run, e = (self.hist.get(o.get("category")) or {}).get(key) or [], o["e"][key]
		if len(run) < 2 or e is None or e["line"] is None:
			return None
		k = next(i for i, x in enumerate(run) if x is e)
		end = run[k + 1]["from"] if k + 1 < len(run) else None
		text = _("{0} used: ₹{1}, the {2} limit in force {3}.").format(_("MD line") if key == "md" else _("Review line"),
			_inr(e["line"]), o.get("category"), _period(e, end))
		if end is not None:
			now = run[-1]["line"]
			text += " " + (_("Today's limit is ₹{0}.").format(_inr(now)) if now is not None else _("Today there is no such limit."))
		return text

	def without_md(self, o):
		"""Not approved by the MD and not on its way there: an order still in approval at or over the line will
		reach the MD; a submitted one at or over it without the MD (R36) still counts."""
		line = self.line(o)
		return not o.get("md_approved") and (line is None or o["v"] < line or o.get("docstatus") == 1)

	def in_window(self, at):
		return at is not None and self.frm <= at.date() <= self.to

	def touches(self, run):
		return any(self.in_window(o["at"]) for o in run)

	def amounts(self, o):
		"""Item code -> the order's net amount for it (worked out once per order)."""
		if o["name"] not in self.amt:
			out = defaultdict(float)
			for r in self.rows[o["name"]]:
				out[r.get("item_code")] += _f(r.get("base_net_amount"))
			self.amt[o["name"]] = dict(out)
		return self.amt[o["name"]]

	def share(self, o, code):
		net = sum(self.amounts(o).values())
		return self.amounts(o).get(code, 0) / net if net > 0 else 0

	def mrs(self, o):
		return {r.get("material_request") for r in self.rows[o["name"]] if r.get("material_request")}

	def item(self, code):
		name = self.names.get(code)
		return "{0} ({1})".format(name, code) if name and name != code else str(code)

	def stock_rows(self, name):
		return [r for r in self.receipts.get(name) or []
			if r.get("doctype") == "Purchase Receipt" or (r.get("doctype") == "Purchase Invoice" and r.get("update_stock"))]

	def skipped(self, o):
		"""Review roles the order went past because the account entering it holds them. The first submission
		row is read (spec R39): a later resubmission may be made by another account."""
		# the loader hands over the rule's steps as they stood when the order was sent for approval; else today's
		steps = o["steps"] if o.get("steps") is not None else self.steps.get(o.get("approval_rule")) or []
		subs = [l for l in self.logs.get(o["name"]) or [] if l.get("action") in SUBMIT_ACTIONS]
		if not steps or not subs or o.get("self_skip_impossible"):
			return []
		at, first = int(_f(subs[0].get("step_order"))), min(int(_f(s.get("step_order"))) for s in steps)
		if at <= first:
			return []
		return [s.get("role") for s in sorted(steps, key=lambda s: _f(s.get("step_order"))) if int(_f(s.get("step_order"))) < at]

	# -- point builders --
	def point(self, check, level, members, line, total, summary, tie="", names=None, key=None):
		"""`key` says which approval limit `line` is ("md" or "pm"); None when it is not one (R41's approved amount)."""
		members = sorted(members, key=_key)
		sups = _suppliers(members)
		p = {"checks": [check], "level": level, "summary": summary, "supplier": sups.pop() if len(sups) == 1 else None,
			"company": ", ".join(sorted({o.get("company") or "" for o in members} - {""})),
			"category": members[0].get("category") if members else None, "line": line, "total": total,
			"over_by": max(0.0, total - line) if line is not None and total is not None else 0.0,
			"orders": names or [o["name"] for o in members], "tie": tie, "facts_against": [], "extra_rows": [],
			"line_used": self.used(members[0], key) if key and members and line is not None else None, "limit": None}
		if check in SPLIT:
			p["facts_against"] = self.facts(members)
		return p

	def group(self, check, level, run, line, tie, typed=False, key="md"):
		total, sups = _sum(run), sorted(_suppliers(run), key=str)
		who = sups[0] if len(sups) == 1 else _("{0} suppliers").format(len(sups))
		if typed:
			days = (max(_typed(o) for o in run) - min(_typed(o) for o in run)).days
			when = _("dated within {0} days").format(days) if days else _("dated the same day")
			head = _("On the typed PO date only: {0} orders {1} to {2}").format(len(run), when, who)
		else:
			head = _("{0} orders {1} to {2}").format(len(run), _span(max(o["at"] for o in run) - min(o["at"] for o in run)), who)
		summary = _("{0}. Each is under the {1} of ₹{2}. Together they are ₹{3}, which is ₹{4} above the line. None came to the MD.").format(
			head, _("MD line") if key == "md" else _("review line"), _inr(line), _inr(total), _inr(total - line))
		return self.point(check, level, run, line, total, summary, tie, key=key)

	def facts(self, run):
		"""Facts against a split, over the visible orders only."""
		if not run:
			return []
		names, sups = {o["name"] for o in run}, _suppliers(run)
		first, last = run[0]["at"], run[-1]["at"]
		whom = _("this supplier") if len(sups) == 1 else _("these suppliers")
		totals = defaultdict(float)
		for o in run:
			for code, amt in self.amounts(o).items():
				totals[code] += amt
		out = []
		if totals:
			main = max(sorted(totals, key=str), key=totals.get)
			n = sum(1 for o in self.sorted if o["name"] not in names and o.get("supplier") in sups and _live(o)
				and first - 8 * WEEK <= o["at"] < first and main in self.amounts(o))
			out.append(_("Orders for {0} to {1} in the 8 weeks before: {2}").format(self.item(main), whom, n))
		docs = {r.get("doc") for o in run for r in self.stock_rows(o["name"]) if not r.get("is_return")}
		out.append(_("Received as {0} separate deliveries").format(len(docs)) if len(docs) > 1
			else _("Received as one delivery") if docs else _("Nothing received yet"))
		md = any(o.get("md_approved") and o["name"] not in names and o.get("supplier") in sups
			and last - 30 * DAY <= (_dt(o.get("final_approved_at")) or o["at"]) <= last for o in self.sorted)
		out.append(_("The MD approved another order to {0} in the last 30 days: {1}").format(whom, _("yes") if md else _("no")))
		return out

	def single(self, check, level, o, line, summary, tie="", total=None, key=None):
		return self.point(check, level, [o], line, o["v"] if total is None else total, summary, tie, key=key)

	# -- the split checks --
	def ties(self, run, line):
		out = []
		for code in sorted({c for o in run for c in self.amounts(o)} - self.catch_all, key=str):
			hit = [o for o in run if self.share(o, code) >= 0.5]
			if len(hit) >= 2 and _sum(hit) >= line:
				out.append(_("{0} is half or more of {1} of these orders, which alone come to {2}").format(self.item(code), len(hit), _inr(_sum(hit))))
				break
		at = sorted(o["at"] for o in run)
		if any(at[k + 2] - at[k] <= 2 * DAY for k in range(len(at) - 2)):
			out.append(_("3 or more of them within 48 hours"))
		count = Counter(mr for o in run for mr in self.mrs(o))
		shared = sorted(mr for mr, n in count.items() if n >= 2)
		if shared:
			out.append(_("request {0} is on 2 or more of them").format(", ".join(shared)))
		done = [o for o in run if o.get("docstatus") == 1]  # only orders still in approval are left out of (d)
		roles = {o.get("final_approver_role") for o in done}
		if len(done) >= 2 and len(roles) == 1 and None not in roles and all(_dt(o.get("final_approved_at")) for o in done):
			times = sorted(_dt(o.get("final_approved_at")) for o in done)
			if times[-1] - times[0] <= HALF_HOUR:
				out.append(_("all finally approved within 30 minutes of each other by the {0}").format(roles.pop()))
		return out

	def r30(self, key="md", check="R30"):
		out, grouped, by = [], set(), defaultdict(list)
		for o in self.small:
			line = self.line(o, key)
			if line and o["v"] < line:  # an order at or over the line is not a small order: it does not block a group
				by[(o.get("supplier"), o.get("category"), self.seg(o, key))].append(o)
		for k, orders in sorted(by.items(), key=lambda kv: str(kv[0])):
			line = self.line(orders[0], key)
			ok = lambda r: _sum(r) >= line  # noqa: E731
			for run in _windows(orders, WEEK, _at, ok):
				grouped.update(o["name"] for o in run)
				if self.touches(run):
					ties = self.ties(run, line)
					level = "To check" if ties and key == "md" else "Note"
					out.append(self.group(check, level, run, line, "; ".join(ties), key=key))
			rest = [o for o in orders if o["name"] not in grouped]  # a group that exists only on the typed PO date
			for run in _windows(rest, WEEK, _typed, ok):
				if self.touches(run):
					out.append(self.group(check, "Note", run, line, "", typed=True, key=key))
		return out

	def cut(self, run, mr):
		"""The R29 Serious test: one request line on 2 or more orders, ordered within 110% of what it asked for."""
		lines = defaultdict(lambda: [set(), 0.0])
		for o in run:
			for r in self.rows[o["name"]]:
				if r.get("material_request") == mr and r.get("material_request_item"):
					lines[r["material_request_item"]][0].add(o["name"])
					lines[r["material_request_item"]][1] += _f(r.get("stock_qty"))
		for mri, (pos, qty) in sorted(lines.items()):
			want = _f((self.mr_items.get(mri) or {}).get("stock_qty"))
			if len(pos) >= 2 and want > 0 and qty <= 1.1 * want:
				code = (self.mr_items.get(mri) or {}).get("item_code")
				return _("one request line ({0}, {1} asked for) is cut across {2} orders that together order {3}").format(
					self.item(code), _num(want), len(pos), _num(qty))
		return None

	def r29(self, key="md", check="R29"):
		out, one, many = [], defaultdict(list), defaultdict(list)
		for o in self.small:
			line = self.line(o, key)
			if line and o["v"] < line:
				for mr in self.mrs(o):
					one[(mr, o.get("supplier"), o.get("category"), self.seg(o, key))].append(o)
					many[(mr, o.get("category"), self.seg(o, key))].append(o)
		for groups, several in ((one, False), (many, True)):
			for k, orders in sorted(groups.items(), key=lambda kv: str(kv[0])):
				mr, line = k[0], self.line(orders[0], key)
				ok = (lambda r: _sum(r) >= line and len(_suppliers(r)) >= 2) if several else (lambda r: _sum(r) >= line)
				for run in _windows(orders, 30 * DAY, _at, ok):
					if not self.touches(run):
						continue
					cut = self.cut(run, mr)
					if key == "pm":
						level = "To check" if cut else "Note"
					else:
						level = "To check" if several or not cut else "Serious"
					tie = _("all draw on request {0}").format(mr) + ("; " + cut if cut else "")
					out.append(self.group(check, level, run, line, tie, key=key))
		return out

	def r31(self):
		out, by = [], defaultdict(list)
		for o in self.small:
			line = self.line(o)
			if line and line / 4 <= o["v"] < line:
				for code in self.amounts(o):
					if code not in self.catch_all and self.share(o, code) >= 0.5:
						by[(o.get("company"), code, o.get("category"), self.seg(o))].append(o)
		for k, orders in sorted(by.items(), key=lambda kv: str(kv[0])):
			code, line = k[1], self.line(orders[0])
			for run in _windows(orders, WEEK, _at, lambda r: _sum(r) >= line and len(_suppliers(r)) >= 2):
				if self.touches(run):
					tie = _("{0} is half or more of every one of these orders").format(self.item(code))
					out.append(self.group("R31", "To check", run, line, tie))
		return out

	def round_qty(self, o, line):
		"""R35(d): the main item (90% of the value or more) is ordered in a round quantity Q, and the next round
		quantity would pass the line. Round = a multiple of 10, and of 100 from 10,000 up (97,500 is round, 37 and
		one machine are not)."""
		amounts = self.amounts(o)
		net = sum(amounts.values())
		if net <= 0:
			return None
		code = max(sorted(amounts, key=str), key=amounts.get)
		q = sum(_f(r.get("qty")) for r in self.rows[o["name"]] if r.get("item_code") == code)
		if amounts[code] < 0.9 * net or q < 1 or abs(q - round(q)) > 1e-9:
			return None
		q = int(round(q))
		s = 10 ** max(1, len(str(q)) - 3)
		if q % s or o["v"] * (q + s) / q < line:
			return None
		return _("{0} of {1} is a round quantity; {2} would pass the line").format(_num(q), self.item(code), _num(q + s))

	def r35(self, key="md", check="R35"):
		out = []
		for o in self.small:
			line = self.line(o, key)
			if not line or o["v"] >= line or not self.in_window(o["at"]):
				continue
			ratio, rnd = o["v"] / line, self.round_qty(o, line)
			if key == "pm":  # R38/R35: the round quantity only, as a Note
				if ratio < 0.8 or not rnd:
					continue
				level, ties = "Note", [rnd]
			else:
				conv = _f(o.get("conversion_rate")) or 1.0
				peak = max([_f(l.get("po_amount")) * conv for l in self.logs.get(o["name"]) or []
					if l.get("action") in ("Submitted", "Resubmitted")] or [0])
				skipped = self.skipped(o) if ratio >= 0.8 and not self.mrs(o) else []
				if not (ratio >= 0.95 or peak >= line or (ratio >= 0.8 and (skipped or rnd))):
					continue
				sib = [s["name"] for s in self.small if s is not o and s.get("supplier") == o.get("supplier")
					and abs(s["at"] - o["at"]) <= WEEK and not (self.line(s) and s["v"] >= self.line(s))]
				ties = [t for t in (
					sib and _("another order to this supplier within 7 days: {0}").format(", ".join(sib)),
					peak >= line and _("first submitted at {0}, now {1}").format(_inr(peak), _inr(o["v"])),
					skipped and _("no request on any line, and the {0} review was skipped").format(" and ".join(skipped)),
					rnd) if t]
				level = "To check" if ties else "Note"
			summary = _("{0} for {1} is {2}, {3}% under the line of {4}; it did not come to the MD.").format(
				o["name"], o.get("supplier"), _inr(o["v"]), round((1 - ratio) * 100, 1), _inr(line))
			out.append(self.single(check, level, o, line, summary, "; ".join(ties), key=key))
		return out

	# -- approval gaps --
	def r36(self):
		out = []
		for o in self.sorted:
			line = self.line(o)
			if o.get("docstatus") != 1 or not _live(o) or o.get("md_approved") or line is None or o["v"] < line \
					or not self.in_window(o["at"]):
				continue
			causes, conv = [], _f(o.get("conversion_rate")) or 1.0
			if o.get("currency") != o.get("company_currency") and _f(o.get("grand_total")) < line <= o["v"]:
				causes.append(_("foreign currency: the approval compared {0} {1} with the rupee line").format(
					o.get("currency"), _num(o.get("grand_total"))))
			sub = _f(o.get("amount_at_submission"))
			if sub and o["v"] > sub * conv + 1:
				causes.append(_("raised after approval: {0} at submission, {1} now").format(_inr(sub * conv), _inr(o["v"])))
			if not any(l.get("action") == "Final Approved" for l in self.logs.get(o["name"]) or []):
				causes.append(_("submitted outside the approval chain: no final approval in its log"))
			summary = _("{0} for {1} is {2}, at or over the line of {3}, and has no MD approval.").format(
				o["name"], o.get("supplier"), _inr(o["v"]), _inr(line))
			out.append(self.single("R36", "Serious", o, line, summary, "; ".join(causes) or _("no cause shows in the order's record"), key="md"))
		return out

	def r39(self):
		out = []
		for o in self.sorted:
			if not _live(o) or not self.in_window(o["at"]):
				continue
			role = o.get("submitted_by_role") or o.get("entered_role") or _("Other")
			skipped = self.skipped(o) if o["v"] >= REVIEW_FLOOR and not self.mrs(o) else []
			fin = [l for l in self.logs.get(o["name"]) or [] if l.get("action") == "Final Approved"]
			own = bool(fin and o.get("submitted_by") and fin[-1].get("action_by") == o.get("submitted_by")
				and not o.get("self_skip_impossible"))
			if not skipped and not own:
				continue
			head = "{0} for {1} ({2})".format(o["name"], o.get("supplier"), _inr(o["v"]))
			skip = _("entered by {0}; the {1} review was skipped because the account entering it holds that role").format(
				role, " and ".join(skipped)) if skipped else ""
			if own:
				summary = _("{0}: final approval given by the account that entered it ({1}).").format(head, role)
			else:
				summary, skip = "{0}: {1}.".format(head, skip), ""
			out.append(self.single("R39", "Serious" if own else "To check", o, self.line(o), summary, skip, key="md"))
		return out

	# -- other routes past the MD --
	def receipt_orders(self):
		for name in sorted(self.receipts):
			o, rows = self.orders.get(name), self.stock_rows(name)
			if o and o.get("docstatus") == 1 and any(self.in_window(_dt(r.get("posting"))) for r in rows):
				yield o, rows

	def r41(self):
		out, allow_all = [], _f(self.data.get("stock_allowance"))
		allow = self.data.get("allowance_now") or {}
		for o, rows in self.receipt_orders():
			got_amt, got_qty = 0.0, defaultdict(float)
			for r in rows:  # a return counts against what came in, whatever sign the loader passed it with
				sign = -1 if r.get("is_return") else 1
				got_amt += sign * abs(_f(r.get("base_net_amount")))
				got_qty[r.get("item_code")] += sign * abs(_f(r.get("qty")))
			approved = _f(o.get("base_net_total"))
			extra = got_amt - approved
			if approved <= 0 or extra <= 0.005 * approved:
				continue
			ordered, allowed, from_qty = defaultdict(lambda: [0.0, 0.0]), 0.0, 0.0
			for r in self.rows[o["name"]]:
				ordered[r.get("item_code")][0] += _f(r.get("qty"))
				ordered[r.get("item_code")][1] += _f(r.get("base_net_amount"))
			for code, (qty, amt) in ordered.items():
				allowed += (_f(allow.get(code)) or allow_all) / 100 * amt
				if qty > 0:
					from_qty += max(0.0, got_qty[code] - qty) * amt / qty
			allowed = allowed if ordered else allow_all / 100 * approved  # no order lines loaded: the general allowance
			billed = sum(_f(r.get("base_net_amount")) for r in self.receipts.get(o["name"]) or [] if r.get("doctype") == "Purchase Invoice")
			level = "To check" if extra > allowed or extra > REVIEW_FLOOR else "Note"
			summary = _("{0} for {1}: received {2} against {3} approved, {4} more ({5}%).").format(
				o["name"], o.get("supplier"), _inr(got_amt), _inr(approved), _inr(extra), round(extra / approved * 100, 1))
			p = self.single("R41", level, o, approved, summary, total=got_amt)
			p["extra_rows"] += [_("Extra from quantity: {0}").format(_inr(from_qty)), _("Extra from rate: {0}").format(_inr(extra - from_qty)),
				_("Billed: {0}").format(_inr(billed)) if billed else _("Not billed yet")]
			out.append(p)
		return out

	def r42(self):
		out = []
		edits = sorted(self.data.get("allowance_edits") or [], key=lambda e: _dt(e.get("at")))
		for k, e in enumerate(edits):
			at, code = _dt(e.get("at")), e.get("item_code")
			if _f(e.get("new")) <= _f(e.get("old")) or not self.in_window(at):
				continue
			hits = sorted(((r, c) for c, rs in (self.data.get("item_receipts") or {}).items() if code is None or c == code
				for r in rs if timedelta(0) <= _dt(r.get("posting")) - at <= HALF_HOUR), key=lambda x: _dt(x[0].get("posting")))
			if not hits:
				continue
			what = self.item(code) if code else _("Stock Settings (every item)")
			mins = lambda r: int((_dt(r.get("posting")) - at).total_seconds() // 60)
			summary = _("Receipt allowance of {0} raised from {1}% to {2}% at {3}; a receipt was posted {4} minutes later.").format(
				what, _num(e.get("old")), _num(e.get("new")), _when(at), mins(hits[0][0]))
			pos = list(dict.fromkeys(r.get("po") for r, c in hits))
			p = self.point("R42", "To check", [self.orders[n] for n in pos if n in self.orders], None, None, summary, names=pos)
			p["extra_rows"] += [_("{0} for {1} ({2}), posted {3} minutes after the change").format(r.get("doc"), r.get("po"), self.item(c), mins(r))
				for r, c in hits]
			back = next((b for b in edits[k + 1:] if b.get("item_code") == code and _dt(b.get("at")) - at <= DAY
				and _f(b.get("new")) == _f(e.get("old"))), None)
			if back:
				p["extra_rows"].append(_("Set back to {0}% at {1}.").format(_num(back.get("new")), _when(back.get("at"))))
			out.append(p)
		return out

	def r43(self):
		out, gates = [], self.data.get("gate_first") or {}
		for o, rows in self.receipt_orders():
			fa = _dt(o.get("final_approved_at")) or _dt(o.get("approved_date"))
			rows = [r for r in rows if not r.get("is_return")]
			early = [r for r in rows if fa and _dt(r.get("posting")).date() < fa.date()]
			gate = _dt(gates.get(o["name"]))
			gaps = [(fa.date() - _dt(r.get("posting")).date()).days for r in early]
			parts = []
			if early:
				parts.append(_("{0} receipts worth {1} are dated before the final approval").format(
					len({r.get("doc") for r in early}), _inr(sum(_f(r.get("base_net_amount")) for r in early))))
			if gate and gate < o["at"]:
				gaps.append((o["at"] - gate).days)
				parts.append(_("the first gate entry for it was made before the order was entered"))
			if not parts:
				continue
			summary = _("{0} for {1}: {2}; largest gap {3} days.").format(o["name"], o.get("supplier"), "; ".join(parts), max(gaps))
			level = "Note" if o.get("category") in EARLY_IS_ROUTINE else "To check"
			out.append(self.single("R43", level, o, None, summary))
		return out

	def qty_rate(self, o):
		out = {}
		for code in self.amounts(o):
			rows = [r for r in self.rows[o["name"]] if r.get("item_code") == code]
			qty = sum(_f(r.get("qty")) for r in rows)
			rate = sum(_f(r.get("base_rate")) * _f(r.get("qty")) for r in rows) / qty if qty else 0
			out[code] = (sum(_f(r.get("stock_qty")) for r in rows), rate)
		return out

	def r44(self):
		out = []
		for o in self.sorted:
			line = self.line(o)
			start = _dt(o.get("approved_date")) or o["at"]
			if o.get("docstatus") != 1 or o.get("status") in CLOSED or _f(o.get("per_received")) > 0 or line is None \
					or o["v"] < line or (self.today - start.date()).days <= 60:
				continue
			mine, rep = self.qty_rate(o), ""
			for n in self.sorted:
				if rep or n is o or n.get("supplier") != o.get("supplier") or n["at"] <= o["at"] or not _live(n):
					continue
				for code, (q2, r2) in self.qty_rate(n).items():
					q, r = mine.get(code, (0, 0))
					if q > 0 and r > 0 and 0.8 <= q2 / q <= 1.2 and 0.9 <= r2 / r <= 1.1:
						rep = _("newer order {0} repeats {1}: quantity {2} against {3}, rate {4} against {5}").format(
							n["name"], self.item(code), _num(q2), _num(q), _num(r2), _num(r))
						break
			summary = _("{0} for {1} ({2}) was approved {3} days ago; nothing has been received and it is still open.").format(
				o["name"], o.get("supplier"), _inr(o["v"]), (self.today - start.date()).days)
			out.append(self.single("R44", "To check" if rep else "Note", o, line, summary, rep, key="md"))
		return out

	# -- the limits themselves --
	def limits(self):
		"""Per category: the stretches of its MD limit that touch the selected dates, oldest first."""
		out = []
		for cat in sorted(self.hist, key=str):
			run, rows, changed = self.hist[cat].get("md") or [], [], 0
			for k, e in enumerate(run):
				end = run[k + 1]["from"] if k + 1 < len(run) else None
				if (end is None or end > self.lo) and (e["from"] is None or e["from"] < self.hi):
					rows.append({"line": e["line"], "from": e["from"] or e.get("since"), "to": end, "today": end is None})
				changed += 1 if e["from"] is not None and self.lo <= e["from"] < self.hi else 0
			out.append({"category": cat, "changed": changed, "rows": rows})
		return out

	def gaps(self):
		"""Where the record of the limits is not whole AND an order of the selected dates falls in it: plain
		sentences, never a guess. Two signs: a stretch the change record does not cover, and an order whose
		amount does not fit the rebuilt band of the rule it was given."""
		out, mine = [], [o for o in self.sorted if _live(o) and self.in_window(o["at"])]
		holes = defaultdict(list)
		for g in self.data.get("history_gaps") or []:
			holes[g.get("cat") or ""].append((_dt(g.get("from")), _dt(g.get("to"))))
		for cat in sorted(holes, key=str):
			n = sum(1 for o in mine if o.get("category") == cat and any((a is None or o["routed"] >= a)
				and (b is None or o["routed"] < b) for a, b in holes[cat]))
			if n:
				out.append(_("The record of the {0} limits is not complete: {1} order(s) of these dates were sent for approval in a time it does not cover, so the limit used for them may not be the one in force then.").format(cat, n))
		table = self.data.get("rule_history")
		if table is not None:
			odd = []
			for o in mine:
				if not o.get("approval_rule") or o["amt"] is None:
					continue
				s = state_at(table, o["approval_rule"], o["routed"])
				if not (s and s["on"] and s["from"] <= o["routed"] and (s["to"] is None or o["routed"] < s["to"])
						and s["low"] <= o["amt"] and (not s["high"] or o["amt"] <= s["high"])):
					odd.append(o["name"])
			if odd:
				more = _(" and {0} more").format(len(odd) - 3) if len(odd) > 3 else ""
				out.append(_("{0} order(s) of these dates do not fit the limits rebuilt for the time they were sent for approval ({1}{2}). A change of the limits may be missing from the record.").format(
					len(odd), ", ".join(odd[:3]), more))
		return out

	def r45(self):
		"""The MD limit of a category changed. A raise (or the MD step taken out) that was set back (the first later
		limit at or under the old one) is one finding with the changes in between. A lowering put back to the same
		limit within 7 days is one finding too (a short-lived change, not a raise). Any other change is its own.
		Shown when the change, or its set-back, falls in the selected dates."""
		out = []
		inside = lambda t: t is not None and self.lo <= t < self.hi  # noqa: E731
		for cat in sorted(self.hist, key=str):
			run, k = self.hist[cat].get("md") or [], 1
			while k < len(run):
				old, new, j = run[k - 1]["line"], run[k]["line"], None
				raised = old is not None and (new is None or new > old)
				if raised:
					j = next((x for x in range(k + 1, len(run)) if run[x]["line"] is not None and run[x]["line"] <= old), None)
				elif old is not None and k + 1 < len(run) and run[k + 1]["line"] == old and run[k + 1]["from"] - run[k]["from"] < WEEK:
					j = k + 1
				if inside(run[k]["from"]) or (j is not None and inside(run[j]["from"])):
					out.append(self.limit_point(cat, run, k, j, raised))
				k = j + 1 if j is not None else k + 1
		return out

	def limit_point(self, cat, run, k, j, raised):
		old, e, back = run[k - 1]["line"], run[k], run[j] if j is not None else None
		new, at, end = e["line"], e["from"], back["from"] if back else None
		pool = [o for o in self.sorted if o.get("category") == cat and _live(o)]
		# between the two limits: at or over the old one, under the one of its own moment, and not seen by the MD
		between = [o for o in pool if raised and not o.get("md_approved") and at <= o["routed"]
			and (end is None or o["routed"] < end) and o["v"] >= old and (self.line(o) is None or o["v"] < self.line(o))]
		rs = lambda v: "₹" + _inr(v)  # noqa: E731
		if old is None:
			way = "introduced"
			text = _("An MD limit of {0} was set for {1} orders on {2}. Before that no {1} order needed the MD.").format(rs(new), cat, _when(at))
		elif not raised:
			way = "lowered"
			text = _("The MD limit for {0} orders was lowered from {1} to {2} on {3}.").format(cat, rs(old), rs(new), _when(at)) + " " + (
				_("It was put back to {0} on {1}. Orders sent for approval in between are measured against {2}.").format(rs(old), _when(end), rs(new))
				if back else _("Orders sent for approval after that are measured against the new limit."))
		else:
			way = "removed" if new is None else "raised"
			if new is None:
				text = _("The MD step was taken out of the {0} limits on {1}; the MD limit had been {2}.").format(cat, _when(at), rs(old))
			else:
				text = _("The MD limit for {0} orders was raised from {1} to {2} on {3}.").format(cat, rs(old), rs(new), _when(at))
			if back:
				text += " " + _("It was set back to {0} on {1}.").format(rs(back["line"]), _when(end))
			if between:
				text += " " + (_("In that time {0} order(s) at or over the old limit, {1} together, did not come to the MD.") if back
					else _("Since then {0} order(s) at or over the old limit, {1} together, did not come to the MD.")).format(len(between), rs(_sum(between)))
			else:
				text += " " + (_("In that time no order at or over the old limit passed without the MD.") if back
					else _("Since then no order at or over the old limit has passed without the MD."))
		level = "Note" if not between else "Serious" if back else "To check"
		p = self.point("R45", level, between, None, _sum(between), text)
		p.update(supplier=None, category=cat)
		roles = self.data.get("change_roles") or {}
		role = lambda x: (roles.get(x.get("by")) or _("Other")) if x and x.get("by") else None  # noqa: E731
		same = bool(back and e.get("by") and e.get("by") == back.get("by"))
		if role(e):
			p["extra_rows"].append(_("Changed by an account with the role {0}.").format(role(e)))
		if raised and k >= 2 and run[k - 2]["line"] is not None and new is not None and run[k - 2]["line"] >= new:
			# a raise back to where the limit stood before: said, so it is not read as a new, higher limit
			p["extra_rows"].append(_("Before {0} the limit was {1}; it stood at {2} for {3}.").format(
				_when(run[k - 1]["from"]), rs(run[k - 2]["line"]), rs(old), _length(at - run[k - 1]["from"])))
		if back:
			p["extra_rows"].append(_("Set back {0}, by the same account.").format(_after(end - at)) if same
				else _("Set back {0}, by an account with the role {1}.").format(_after(end - at), role(back)) if role(back)
				else _("Set back {0}.").format(_after(end - at)))
		# the chart: the limit over time with every order of the category as a mark
		a = max(at - PAD, self.lo - 9 * WEEK)  # the loader reads orders from 63 days before the selected dates
		b = min((end or self.hi) + PAD, self.hi)
		steps = []
		for i, x in enumerate(run):
			x0, x1 = x["from"] or datetime.min, run[i + 1]["from"] if i + 1 < len(run) else datetime.max
			if x0 < b and x1 > a:
				steps.append({"from": max(x0, a), "to": min(x1, b), "line": x["line"]})
		hit = {o["name"] for o in between}
		marks = [{"name": o["name"], "at": o["routed"], "value": o["v"], "line": self.line(o), "between": o["name"] in hit}
			for o in pool if a <= o["routed"] < b]
		if len(marks) > MARKS_CAP:  # keep the orders the finding is about, then the largest
			marks.sort(key=lambda m: (not m["between"], -m["value"]))
			marks = marks[:max(MARKS_CAP, len(hit))]
		cut = sum(1 for o in pool if a <= o["routed"] < b) - len(marks)
		marks.sort(key=lambda m: (m["at"], m["name"]))
		p["limit"] = {"category": cat, "direction": way, "old": old, "new": new, "at": at, "back_at": end,
			"back_to": back["line"] if back else None, "by_role": role(e), "back_by_role": role(back), "same_account": same,
			"range": {"from": a, "to": b}, "steps": steps, "marks": marks, "marks_cut": cut}
		return p

	# -- R32, the ranked list --
	def r32(self):
		end = self.hi - timedelta(seconds=1)  # the limits at the end of the selected dates
		mds = {c: v for c in self.hist for v in [line_at(self.hist, c, "md", end)] if v is not None}
		if not mds:
			return []
		to = self.to
		fy, d30, d90 = date(to.year if to.month >= 4 else to.year - 1, 4, 1), to - 29 * DAY, to - 89 * DAY
		pool = [o for o in self.sorted if _live(o) and self.without_md(o) and min(fy, d90) <= o["at"].date() <= to]
		entries, marks = defaultdict(list), defaultdict(list)
		for o in pool:
			entries[("supplier", o.get("supplier"))].append((o, o["v"]))
			for code, amt in self.amounts(o).items():
				entries[("item", code)].append((o, amt))
			for mr in self.mrs(o):
				entries[("request", mr)].append((o, sum(_f(r.get("base_net_amount")) for r in self.rows[o["name"]] if r.get("material_request") == mr)))
			own, others = self.line(o), [l for c, l in mds.items() if c != o.get("category")]
			if o["at"].date() >= d90 and own and others and min(others) <= o["v"] < own:  # Coal: its line is higher
				marks[("supplier", o.get("supplier"))].append(_("{0} needs the MD only from {1}: {2} ({3})").format(
					o.get("category"), _inr(own), o["name"], _inr(o["v"])))
		for (kind, mr), rows in entries.items():
			if kind == "request":
				marks[(kind, mr)] += self.request_marks(mr, rows, mds)
		out = []
		for (kind, key), rows in entries.items():
			recent = [(o, a) for o, a in rows if o["at"].date() >= d90]
			if not recent:
				continue
			cats = {o.get("category") for o, a in recent}
			line = min([mds[c] for c in cats if c in mds] or list(mds.values()))
			d90sum, m = sum(a for o, a in recent), list(marks.get((kind, key), []))
			m += [_("{0} orders never need the MD").format(c) for c in sorted(cats - set(mds), key=str)]
			if d90sum < line and not (kind == "supplier" and marks.get((kind, key))):
				continue
			per = defaultdict(float)
			for o, a in recent:
				per[o["name"]] += a
			sums = (sum(a for o, a in rows if o["at"].date() >= d30), d90sum, sum(a for o, a in rows if o["at"].date() >= fy))
			if kind == "item" and key in self.catch_all:
				sums = (None, None, None)
				m.insert(0, _("catch-all code: its rates differ 10 times or more, so it is not added up as one item"))
			out.append({"kind": kind, "key": key, "label": self.item(key) if kind == "item" else key, "d30": sums[0],
				"d90": sums[1], "fy": sums[2], "orders": len(per), "largest": max(per.values()), "marks": m})
		out.sort(key=lambda r: (KINDS.index(r["kind"]), -(r["d90"] or 0), str(r["key"])))
		return out

	def request_marks(self, mr, rows, mds):
		"""A request line whose full quantity, at the rate it was ordered at, passes the line, and that was ordered
		only through orders without the MD. Rate per stock unit, so the request's stock quantity can be priced."""
		out = []
		mris = sorted({r.get("material_request_item") for o, a in rows for r in self.rows[o["name"]]
			if r.get("material_request") == mr and r.get("material_request_item")})
		for mri in mris:
			lines = [r for r in self.by_mri[mri] if _live(self.orders[r["po"]])]
			if not all(self.without_md(self.orders[r["po"]]) for r in lines):
				continue
			qty, amt = sum(_f(r.get("stock_qty")) for r in lines), sum(_f(r.get("base_net_amount")) for r in lines)
			want = _f((self.mr_items.get(mri) or {}).get("stock_qty"))
			cats = {self.orders[r["po"]].get("category") for r in lines}
			line = min([mds[c] for c in cats if c in mds] or list(mds.values()))
			if qty > 0 and want * amt / qty >= line:
				out.append(_("request line for {0}: {1} asked for, worth about {2} at the ordered rate, over the line of {3}; ordered only in orders without the MD").format(
					self.item(lines[0].get("item_code")), _num(want), _inr(want * amt / qty), _inr(line)))
		return out


def _at(o):
	return o["at"]


def _fold(run, sent):
	"""A limit's stretches without the moments nobody was measured against: a stretch shorter than FOLD in which no
	order of the category was sent for approval (a re-save in two steps, a slip put right at once) is left out,
	and the stretch before it runs on. `sent`: when the category's orders were sent for approval."""
	out = run[:1]
	for k in range(1, len(run)):
		e, end = run[k], run[k + 1]["from"] if k + 1 < len(run) else None
		brief = end is not None and end - e["from"] <= FOLD and not any(e["from"] <= t < end for t in sent)
		if not brief and e["line"] != out[-1]["line"]:
			out.append(e)
	return out


def _period(e, end):
	"""'from 01-04-2026 to 11-11-2026 19:42', 'since 11-11-2026 19:42'; a setup day is said without its time."""
	start = _when(e["from"]) if e.get("from") else _dt(e["since"]).strftime("%d-%m-%Y") if e.get("since") else None
	if end is None:
		return _("since {0}").format(start) if start else _("at all times")
	return _("from {0} to {1}").format(start, _when(end)) if start else _("until {0}").format(_when(end))


def _length(td):
	"""'41 seconds', '3 minutes', '5 hours', '4 days'."""
	secs = int(td.total_seconds())
	if secs < 120:
		return _("{0} seconds").format(max(1, secs))
	if secs < 2 * 3600:
		return _("{0} minutes").format(secs // 60)
	return _("{0} hours").format(secs // 3600) if secs < 48 * 3600 else _("{0} days").format(secs // 86400)


def _after(td):
	return _("after {0}").format(_length(td))


def _typed(o):
	return _d(o.get("transaction_date")) or o["at"].date()
