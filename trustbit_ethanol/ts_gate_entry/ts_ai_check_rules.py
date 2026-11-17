"""AI Checker, step 2: the judgement of one payment-list line, as plain functions.

Nothing here reads the database or the clock. `ts_ai_check_match` gathers the ERP facts and hands them in,
so every rule can be tested with invented facts. Money is in the company's currency. No figure comes from
the AI: its review (`ts_ai_check_review`) is added afterwards and can never change a verdict made here.

A real list carries a supplier name, an amount and a word of remark, so a line is matched by its amount:
one receipt, receipts added, weighed weight x rate, a receipt less its deduction, or an order's total or
advance terms. "Asked before" rests on the earlier lists checked here. "Paid" is said only where the ERP
itself shows it: an invoice of the receipt with nothing outstanding, or an advance recorded on the order."""

import re

OK, CHECK, SERIOUS, NOTE = "No issue found", "To check", "Serious", "Note"
RANK = {SERIOUS: 3, CHECK: 2, NOTE: 1, OK: 0}
TOL = 1.0  # rupees: a list rounds to the rupee
WEIGHT_TOL = 0.005  # a receipt may differ from the weighed weight by half a percent
OVER_RECEIPT = 0.01  # received this far over the ordered quantity before it is a point
EXCESS_MONTHS = 3  # stock that covers more months of use than this is excess
POSITION_DAYS = 90  # the window for "received" against "asked" on an on-account payment
MANY_ORDERS = 8  # this many orders to one supplier in 30 days points to Order Watch
MAX_WINDOW = 12  # receipts added together, at most
ROUND_STEP = 50000
ON_ACCOUNT = re.compile(r"on\s*a\s*/?\s*c\b|on\s+account|ad\s*-?\s*hoc", re.I)
PARTS = (25, 50, 75)


def money(value):
	"""An amount the way the lists write it: Indian digit groups, paise only when there are any."""
	value = round(float(value or 0), 2)
	whole, paise = divmod(round(abs(value) * 100), 100)
	digits = str(whole)
	head, tail = digits[:-3], digits[-3:]
	if head:
		tail = re.sub(r"(?<=\d)(?=(\d\d)+$)", ",", head) + "," + tail
	return ("-" if value < 0 else "") + "₹" + tail + (f".{paise:02d}" if paise else "")


def number(value):
	return f"{float(value or 0):,.3f}".rstrip("0").rstrip(".")


def day(value):
	"""2026-11-12 as 12-11-2026."""
	text = str(value or "")[:10]
	return "-".join(reversed(text.split("-"))) if len(text) == 10 else text


def _same(a, b):
	return abs(float(a or 0) - float(b or 0)) <= TOL


def on_account(line):
	amount = float(line.get("amount") or 0)
	return bool(ON_ACCOUNT.search(line.get("remarks_text") or "")) or (amount >= 100000 and amount % ROUND_STEP == 0)


def _found(code, how, receipts=(), order=None, **more):
	return {"code": code, "how": how, "receipts": list(receipts), "order": order, **more}


def _payables(receipt):
	"""What a receipt is worth after its deduction sheet, both ways such sheets are written."""
	cut = receipt.get("deduction")
	return [cut["net_payable"], receipt["total"] - cut["cut"]] if cut else []


def _weighed(receipt):
	"""Weighed weight x rate, without and with the receipt's own tax."""
	if not (receipt.get("wb_qty") and receipt.get("rate")):
		return []
	base = receipt["wb_qty"] * receipt["rate"]
	qty_value = (receipt.get("qty") or 0) * receipt["rate"]
	return [base, base * receipt["total"] / qty_value] if qty_value > 0 else [base]


ACCOUNT_HOW = "Round amount. No receipt or group of receipts adds up to it"  # stored with the line; read back


def _settled(r):
	"""The ERP shows the invoice of this receipt with nothing outstanding."""
	bill = r.get("invoice")
	return bool(bill) and bill["total"] > 0 and bill["open"] <= TOL


def _by_receipt(amount, receipts, orders, used=(), claimed=()):
	# a receipt no other line stands on comes first; of those, one the ERP does not show as paid
	singles = sorted(receipts, key=lambda r: (r["name"] in used, _settled(r)))
	for r in singles:
		if _same(r["total"], amount):
			return _found("receipt", "= one receipt", [r])
	for r in singles:
		back = r.get("returned") or 0
		if back and _same(r["total"] - back, amount):
			return _found("returned", "= receipt less the goods sent back", [r], less_return=True)
		if back and any(_same(v - back, amount) for v in _payables(r)):
			return _found("deduction", "= receipt less the goods sent back and the deduction", [r], less_return=True)
	for r in singles:
		if any(_same(v, amount) for v in _payables(r)):
			return _found("deduction", "= receipt less the deduction on its sheet", [r])
	for r in singles:
		if any(_same(v, amount) for v in _weighed(r)):
			how = f"= weighed {number(r['wb_qty'])} {r.get('uom') or ''} × {money(r['rate'])}".replace("  ", " ")
			return _found("weighed", how, [r])
	# receipts next to each other in time, added: the newest run first, and a run that no other line
	# stands on before one that shares a receipt
	first = None
	for net in (False, True):
		values = [(_payables(r) or [r["total"]])[0] if net else r["total"] for r in receipts]
		for start in range(len(values)):
			total = values[start]
			for end in range(start + 1, min(start + MAX_WINDOW, len(values))):
				total += values[end]
				if _same(total, amount):
					how = f"= {end - start + 1} receipts added" + (", less deductions" if net else "")
					hit = _found("receipts", how, receipts[start:end + 1], net=net)
					if not any(r["name"] in used for r in hit["receipts"]):
						return hit
					first = first or hit
				if total > amount + TOL:
					break
	return first


def _by_order(amount, receipts, orders, used=(), claimed=()):
	# an approved order before a waiting, refused or held copy of it; of two orders of one value, the one
	# that nothing was asked on yet, and then the one with no advance paid in the ERP
	orders = sorted(orders, key=lambda o: (not approved(o) or bool(o.get("on_hold")), o["name"] in claimed,
		float(o.get("advance_paid") or 0) > TOL))
	for o in orders:
		for term in o.get("advance") or []:
			if _same(term["amount"], amount):
				return _found("advance", f"= order's advance of {number(term['portion'])}%", order=o, term=term)
			if _same(o["net"] * term["portion"] / 100, amount):
				how = f"= order's advance of {number(term['portion'])}%, before GST"
				return _found("advance", how, order=o, term=term, before_gst=True)
	for o in orders:
		if _same(o["total"], amount):
			return _found("order", "= order total", order=o)
		if _same(o["net"], amount):
			return _found("order", "= order value before GST", order=o, before_gst=True)
	for o in orders:
		for part in PARTS:
			if _same(o["total"] * part / 100, amount) or _same(o["net"] * part / 100, amount):
				return _found("part", f"= {part}% of the order", order=o, part=part)
	return None


def claims(found, amount):
	"""What a matched line stands on, as rows [kind, document, order, money]. "o" is money asked on an order
	itself (its total, a part, an advance). "r" is money asked on a receipt, split over the orders that the
	receipt names. The rows are stored with the line, so a later list reads them as they were."""
	if found.get("order"):
		return [["o", "", found["order"]["name"], round(amount, 2)]]
	receipts, out = found.get("receipts") or [], []
	for r in receipts:
		value = amount if len(receipts) == 1 else r["total"]
		names = r.get("orders") or []
		shares = r.get("order_share") or {name: 1 / len(names) for name in names} or {"": 1}
		out += [["r", r["name"], order, round(value * share, 2)] for order, share in shares.items()]
	return out


def _on_orders(rows):
	"""The orders that money was asked on directly, in stored claims."""
	return {order for kind, _doc, order, _value in rows or [] if kind == "o"}


def match_amount(line, facts, ctx=None):
	"""Which ERP document stands behind the line's amount. An advance looks at orders first. Of two
	receipts (or two orders) of one value, the one that no earlier line of this list (`taken`, `claims`)
	and no earlier list stands on is taken. A list that is being sent again (`resent`) does not use up
	its own receipts and orders."""
	ctx = ctx or {}
	amount = float(line.get("amount") or 0)
	receipts, orders = facts.get("receipts") or [], facts.get("orders") or []
	rows = [row for row in facts.get("asked_rows") or [] if row["report"] not in (ctx.get("resent") or ())]
	used = set(ctx.get("taken") or ()) | {name for row in rows for name in row.get("receipts") or []}
	claimed = {name for row in rows for name in _on_orders(row.get("claims"))}
	claimed |= {name for _line_no, mine in ctx.get("claims") or [] for name in _on_orders(mine)}
	if amount > 0:
		tries = (_by_order, _by_receipt) if line.get("kind") == "Advance" else (_by_receipt, _by_order)
		for attempt in tries:
			found = attempt(amount, receipts, orders, used, claimed)
			if found:
				return found
	if on_account(line):
		return _found("account", ACCOUNT_HOW)
	return _found("none", "No receipt or order of this value")


def approved(order):
	"""A submitted order whose approval is not waiting and was not refused. Orders older than the approval
	system are submitted with the status 'Not Submitted'; they count as approved."""
	status = order.get("approval") or ""
	return order.get("docstatus") == 1 and not status.startswith("Pending") and status not in ("Rejected", "Revised")


def _node(key, state, value):
	return {"k": key, "s": state, "v": value}


def _names(rows, one, many):
	rows = list(dict.fromkeys(rows))
	return rows[0] if len(rows) == 1 else f"{len(rows)} {many}" if rows else one


class _Line:
	"""The points and the trail of one line while it is being judged."""

	def __init__(self, line, facts, found, ctx):
		self.line, self.facts, self.found, self.ctx = line, facts, found, ctx
		self.points, self.amount = [], float(line.get("amount") or 0)
		self.receipts, self.order = found.get("receipts") or [], found.get("order")
		self.claims = claims(found, self.amount)
		self.orders = [self.order] if self.order else self._orders_of(self.receipts)
		self.advance = line.get("kind") == "Advance" or found["code"] == "advance"

	def _orders_of(self, receipts):
		wanted = list(dict.fromkeys(name for r in receipts for name in r.get("orders") or []))
		by_name = {o["name"]: o for o in self.facts.get("orders") or []}
		return [by_name[name] for name in wanted if name in by_name]

	def add(self, level, rule, title, detail, refs=()):
		self.points.append({"level": level, "rule": rule, "title": title, "detail": detail,
			"refs": ", ".join(dict.fromkeys(str(r) for r in refs if r))})

	# ---- the rules

	def supplier(self):
		s = self.facts.get("supplier")
		if not s:
			self.add(CHECK, "S1", "No supplier of this name in the ERP",
				"The check stops here. Nothing behind this payment can be verified.")
			return False
		if s.get("how") == "close":
			self.add(CHECK, "S4", "The name on the list is spelled differently in the ERP",
				"Checked against " + s["title"] + ". Confirm it is the same party.", [s["name"]])
		if s.get("others"):
			self.add(CHECK, "S2", "The name fits more than one supplier record",
				"Checked against " + s["title"] + ". Also close: " + ", ".join(s["others"][:3]) + ".", [s["name"]])
		if s.get("disabled") or s.get("on_hold"):
			what = "disabled" if s.get("disabled") else "on hold"
			self.add(SERIOUS, "S3", "The supplier is " + what + " in the ERP",
				"Payments to this supplier are stopped in the ERP.", [s["name"]])
		return True

	def repeats(self):
		mine = [r["name"] for r in self.receipts]
		taken = self.ctx.get("taken") or {}
		shared = {name: taken[name] for name in mine if name in taken}
		if self.ctx.get("repeat_of"):
			# two bills of one value: only when BOTH lines stand on a receipt, and not on the same one
			own = bool(mine) and bool(self.ctx.get("repeat_on")) and not shared
			# or two orders of one value, each line on an order of its own
			first = next((rows for no, rows in self.ctx.get("claims") or [] if no == self.ctx["repeat_of"]), [])
			here, there = _on_orders(self.claims), _on_orders(first)
			apart = bool(here) and bool(there) and not here & there
			self.add(CHECK if own or apart else SERIOUS, "R1", "Same supplier and amount twice in this list",
				f"Line {self.ctx['repeat_of']} asks the same {money(self.amount)} for this supplier."
				+ (f" Each line has a receipt of its own: this one {', '.join(mine[:3])}." if own else "")
				+ (f" Each line has an order of its own: this one {', '.join(sorted(here))}." if apart and not own else ""))
		elif shared:
			name, other = next(iter(shared.items()))
			self.add(SERIOUS, "R3", "The same receipt is behind two lines of this list",
				f"{name} also stands behind line {other}. A receipt is paid once.", list(shared))
		resent = self.ctx.get("resent") or ()  # earlier lists that this list sends again
		# a line of another, older list is looked at before a line of the list that is being sent again
		rows = sorted(self.facts.get("asked_rows") or [], key=lambda row: row["report"] in resent)
		same = next((row for row in rows if self.amount > 0 and _same(row["amount"], self.amount)), None)
		reused = next((row for row in rows if set(mine) & set(row.get("receipts") or [])), None)
		if same:
			theirs = same.get("receipts") or []
			# no line of another list stood on this line's receipt (the list being sent again does not count)
			own = bool(mine) and bool(theirs) and not (reused and reused["report"] not in resent)
			here, there = _on_orders(self.claims), _on_orders(same.get("claims"))
			tail = "If the first was not paid, this is fine. The ERP cannot tell."
			if own:
				tail = f"That line stood on {', '.join(theirs[:3])}; this one has a receipt of its own."
			elif here and there and not here & there:  # two orders of one value
				own = True
				tail = f"That line stood on {', '.join(sorted(there))}; this one has an order of its own, {', '.join(sorted(here))}."
			else:
				# an advance and, after delivery, the balance of the same order (asked by order or by receipt)
				orders = {o["name"]: o for o in self.facts.get("orders") or []}
				for name in dict.fromkeys(order for _kind, _doc, order, _value in self.claims):
					if name in there and name in orders and float(orders[name].get("per_received") or 0) >= 99.5:
						asked, limit, _direct, _who, soft = self._ledger(name)
						if asked - soft <= limit + TOL:
							own = True
							tail = (f"Both stand on {name}, which is fully received. With this line "
								f"{money(asked - soft)} of its {money(orders[name]['total'])} is asked."
								+ (f" {money(soft)} more is on a second copy of a list and is left out here." if soft > TOL else ""))
							break
			self.add(CHECK if own or same["report"] in resent else SERIOUS, "R2", "Asked before, on the list of " + day(same["list_date"]),
				f"Line {same['line_no']} of that list asked the same {money(self.amount)} for this supplier. " + tail, [same["report"]])
		elif reused:
			both = sorted(set(mine) & set(reused["receipts"]))
			then = (reused.get("was") or {}).get(both[0])  # the receipt was cancelled and made again since
			self.add(CHECK if reused["report"] in resent else SERIOUS, "R4", "A receipt behind this line was asked for before",
				f"{both[0]}{f' (then named {then})' if then else ''} stood behind line {reused['line_no']} of the list of {day(reused['list_date'])}, which asked "
				f"{money(reused['amount'])}. If that was paid, part of this line is paid twice.", [reused["report"], *both])
		if "amount" in (self.line.get("unclear") or ""):
			self.add(CHECK, "U1", "The amount was not clear in the file",
				"The reader gave its best reading. Compare it with the sheet.")

	def _ledger(self, name):
		"""Everything asked on one order, from the claims stored with each line: (asked, limit, direct, who,
		soft). A receipt counts once, however many lines name it (a second use of a receipt is R1 to R4's
		point). Money asked on the order itself counts per line. `soft` is the part that may be one request
		counted twice: a line of a list that this list sends again but does not repeat, and the second of two
		lists of one date that ask the same amount on the order. A point that needs that part is To check.
		`limit` is what the order can honestly owe: its value, or the value of its receipts when that is
		more (an order received over its quantity is Q1's point, not this one's)."""
		resent, carried = self.ctx.get("resent") or (), self.ctx.get("carried") or ()
		by_receipt, per, who, soft = {}, {}, {}, 0.0

		def take(rows, label, report=""):
			own = 0.0
			for kind, doc, order, value in rows:
				if order != name:
					continue
				if label:
					who.setdefault(label, set()).add(report)
				if kind == "r":
					by_receipt[doc] = max(by_receipt.get(doc, 0.0), float(value))
				else:
					own += float(value)
			return own

		direct = take(self.claims, "")
		for line_no, rows in self.ctx.get("claims") or []:
			direct += take(rows, f"line {line_no}")
		mine = name in _on_orders(self.claims)
		for row in self.facts.get("asked_rows") or []:
			again = row["report"] in resent
			# a line that a later copy of its list replaced, a line this list carries again, and (on the
			# list being sent again) the earlier copy of this very request are not a second request
			if row.get("superseded") or (again and ((row["report"], row["line_no"]) in carried
					or (mine and name in _on_orders(row.get("claims"))))):
				continue
			own = take(row.get("claims") or [], f"line {row['line_no']} of the list of {day(row['list_date'])}", row["report"])
			if own and again:
				direct, soft = direct + own, soft + own
			elif own:
				counts = per.setdefault((row["list_date"], round(own, 2)), {})
				counts[row["report"]] = counts.get(row["report"], 0) + 1
		direct += sum(key[1] * sum(counts.values()) for key, counts in per.items())
		soft += sum(key[1] * (sum(counts.values()) - max(counts.values())) for key, counts in per.items())
		received = sum(by_receipt.values())
		total = next(float(o["total"]) for o in self.facts.get("orders") or [] if o["name"] == name)
		names = [label if len(reports) < 2 else label.replace("of the list of", f"of {len(reports)} lists of")
			for label, reports in who.items()]
		return received + direct, max(total * (1 + OVER_RECEIPT), received), direct, names, soft

	def over_order(self):
		"""An order asked for by two routes: its own value (total, part or advance) on one line and its
		receipts on another, in this list or on earlier lists."""
		orders = {o["name"]: o for o in self.facts.get("orders") or []}
		for name in dict.fromkeys(order for _kind, _doc, order, _value in self.claims):
			if name not in orders:
				continue
			asked, limit, direct, who, soft = self._ledger(name)
			if who and direct > 0 and asked > limit + TOL:
				# over the value only with a part that may be one request counted twice: To check
				level = CHECK if asked - soft <= limit + TOL else SERIOUS
				self.add(level, "R5", "More is asked on this order than its value",
					f"{name} is {money(orders[name]['total'])}. This line and {', '.join(who[:3])}"
					f"{' and others' if len(who) > 3 else ''} ask {money(asked)} on it and on its receipts. "
					+ (f"{money(soft)} of this may be one request counted twice: a list sent again, or two lists of one "
						"date with the same amount. " if soft > TOL else "")
					+ "If the earlier ones were paid, this pays the same goods again.", [name])

	def orders_approved(self):
		what = "Advance asked" if self.advance else "Payment asked"
		for o in self.orders:
			got = float(o.get("per_received") or 0)
			if not approved(o):
				status = o.get("approval") or "not submitted"
				self.add(SERIOUS, "A1", what + " on an order that is not approved",
					f"{o['name']} has the status {status}.", [o["name"]])
			elif o.get("on_hold"):
				self.add(SERIOUS, "A2", what + " on an order that is on hold",
					f"{o['name']} was put on hold in the ERP after its approval.", [o["name"]])
			elif o.get("closed") and got < 99.5 and not self.receipts:
				self.add(CHECK, "A3", "The order is closed",
					f"{o['name']} was closed with {got:.0f}% received. Nothing more is to come on it.", [o["name"]])
			for item in o.get("items") or []:
				if item["qty"] > 0 and item["received_qty"] > item["qty"] * (1 + OVER_RECEIPT):
					over = (item["received_qty"] / item["qty"] - 1) * 100
					self.add(CHECK, "Q1", f"Order received {over:.1f}% over its quantity",
						f"{o['name']} was for {number(item['qty'])} {item.get('uom') or ''} of {item['name']}. "
						f"{number(item['received_qty'])} came in against it.", [o["name"]])
					break

	def _settled_on(self, name):
		"""What the ERP shows as paid on the invoices of the receipts made against one order only."""
		bills = {tuple(r["invoice"]["names"]): r["invoice"] for r in self.facts.get("receipts") or []
			if r.get("invoice") and r.get("orders") == [name]}
		return sum(max(bill["total"] - bill["open"], 0.0) for bill in bills.values())

	def paid(self):
		"""What the ERP itself shows as paid: an invoice of the receipts with nothing (or less than this line)
		outstanding, and an advance recorded on the order. One invoice over several receipts is judged once,
		against everything this line asks on it."""
		groups = {}
		for r in self.receipts:
			bill = r.get("invoice")
			if bill and bill["total"] > 0:
				groups.setdefault(tuple(bill["names"]), (bill, []))[1].append(r)
		billed = set()  # orders whose payment already shows on an invoice of this line
		for names, (bill, mine) in groups.items():
			asked = self.amount if len(mine) == len(self.receipts) else sum(r["total"] for r in mine)
			docs, refs = ", ".join(r["name"] for r in mine[:3]), [*(r["name"] for r in mine[:3]), *names[:3]]
			if bill["open"] <= TOL:
				self.add(SERIOUS, "Y1", "The ERP shows this bill as already paid",
					f"{', '.join(names[:3])} for {docs} is {money(bill['total'])}, and nothing is outstanding on it in the ERP "
					"(paid or adjusted).", refs)
			elif bill["open"] < bill["total"] - TOL and asked > bill["open"] + TOL:
				self.add(CHECK, "Y2", "The ERP shows part of this bill as paid",
					f"{', '.join(names[:3])} for {docs} is {money(bill['total'])}; {money(bill['open'])} is outstanding in the ERP. "
					f"This line asks {money(asked)} on it.", refs)
			else:
				continue
			billed |= {name for r in mine for name in r.get("orders") or []}
		o, title = self.order, "An advance is already recorded on this order"
		if o:  # the line stands on the order itself
			given, settled = float(o.get("advance_paid") or 0), self._settled_on(o["name"])
			over = given + settled + self.amount > o["total"] * (1 + OVER_RECEIPT) + TOL
			also = f" Invoices of its receipts show {money(settled)} paid." if settled > TOL else ""
			if given > TOL:
				# after delivery a line of the advance's value is the balance, not the advance asked again
				term = self.found.get("term") if float(o.get("per_received") or 0) < 99.5 else None
				again = bool(term) and given >= term["amount"] - TOL  # the advance of the terms is already paid
				self.add(SERIOUS if over or again else CHECK if term else NOTE, "Y3", title,
					f"{o['name']} is {money(o['total'])}. The ERP shows {money(given)} paid in advance on it.{also} "
					f"This line asks {money(self.amount)}.", [o["name"]])
			elif over and settled > TOL:
				self.add(SERIOUS, "Y4", "Part of this order is already paid in the ERP",
					f"{o['name']} is {money(o['total'])}.{also} This line asks {money(self.amount)}.", [o["name"]])
			return
		by_name = {x["name"]: x for x in self.facts.get("orders") or []}
		for name in dict.fromkeys(order for _kind, _doc, order, _value in self.claims):
			# the line stands on receipts: the advance paid on their order comes off what the receipts are worth
			given = float(by_name[name].get("advance_paid") or 0) if name in by_name else 0
			if given <= TOL or name in billed:
				continue
			asked, limit, direct, _who, _soft = self._ledger(name)
			over = asked - direct + max(direct, given) > limit + TOL
			self.add(SERIOUS if over else NOTE, "Y3", title,
				f"{name} is {money(by_name[name]['total'])}. The ERP shows {money(given)} paid in advance on it. "
				f"With this line {money(asked - direct)} is asked on its receipts.", [name])

	def gate(self):
		"""Gate token, weighbridge slip and weighed weight behind each receipt that is paid."""
		paid_on_weight = self.found["code"] == "weighed"
		bare = [r["name"] for r in self.receipts if not r.get("orders")]
		if bare:
			self.add(CHECK, "B1", "Receipt with no purchase order behind it",
				f"{', '.join(bare[:3])}{' and others' if len(bare) > 3 else ''}: received without an order, so no "
				"order approval stands behind the payment.", bare)
		known = {o["name"] for o in self.facts.get("orders") or []}
		unread = list(dict.fromkeys(n for r in self.receipts for n in r.get("orders") or [] if n not in known))
		if unread:
			self.add(CHECK, "X2", "The order behind this receipt was not read",
				f"{', '.join(unread[:3])}{' and others' if len(unread) > 3 else ''}: this check could not read it, so "
				"its approval was not checked.", unread)
		gross = self.found["code"] in ("receipt", "receipts", "weighed", "returned") and not self.found.get("net")
		for r in self.receipts:
			if r.get("returned") and not self.found.get("less_return"):
				self.add(SERIOUS, "V1", "Goods on this receipt were sent back",
					f"{r['name']} is {money(r['total'])}. Goods worth {money(r['returned'])} were returned to the supplier"
					+ (f", and deduction sheet {r['deduction']['name']} takes off {money(r['deduction']['cut'])}" if r.get("deduction") else "")
					+ (f", so {money(r['total'] - r['returned'] - ((r.get('deduction') or {}).get('cut') or 0))} is payable."
						if r["returned"] + ((r.get("deduction") or {}).get("cut") or 0) < r["total"] - TOL else ", so nothing is payable on it."),
					[r["name"]])
			cut = r.get("deduction")
			if gross and cut and cut["cut"] > 0:
				self.add(SERIOUS, "V2", "Paid in full on a receipt with a deduction",
					f"{r['name']} is {money(r['total'])}. Deduction sheet {cut['name']} takes off {money(cut['cut'])}.",
					[r["name"], cut["name"]])
			raw = r.get("rm")
			if not r.get("token"):
				if raw:
					self.add(SERIOUS, "G1", "Raw material receipt with no gate token",
						f"{r['name']} ({money(r['total'])}) has no gate token behind it.", [r["name"]])
				else:
					self.add(CHECK, "G2", "Receipt with no gate token",
						f"{r['name']} ({money(r['total'])}) has no gate token behind it.", [r["name"]])
			if raw and not r.get("wb_done"):
				self.add(SERIOUS, "G3", "Raw material load with no weighbridge slip",
					f"{r['name']} ({money(r['total'])}) has no completed weighbridge entry, so its quantity "
					"rests on the store's word.", [r["name"]])
			elif raw and not r.get("rst"):
				self.add(CHECK, "G4", "Weighbridge entry has no slip (RST) number",
					f"{r['name']} was weighed, but no slip number is recorded.", [r["name"]])
			if r.get("wb_qty") and r.get("qty") and r["qty"] > r["wb_qty"] * (1 + WEIGHT_TOL):
				unit = r.get("uom") or ""
				gap = f"{r['name']} is for {number(r['qty'])} {unit}. The weighbridge net is {number(r['wb_qty'])} {unit}."
				if paid_on_weight:
					self.add(CHECK, "G5", "Receipt quantity is above the weighed weight",
						gap + " The payment follows the weighbridge, which is the lower figure.", [r["name"]])
				else:
					self.add(SERIOUS, "G5", "Paid on a receipt quantity above the weighed weight",
						gap + " The payment follows the receipt, which is the higher figure.", [r["name"]])
			if raw and r.get("token_shared"):
				self.add(SERIOUS, "G6", "The same gate token is behind more than one receipt",
					f"{r['name']} shares its gate token with {r['token_shared']} other receipt(s).", [r["name"]])

	def stock(self):
		"""Excess stock. On an advance the money has not gone yet, so the point is graded higher."""
		if self.receipts:
			rows = [r.get("stock") for r in self.receipts]
		else:  # an order whose goods are in already: today's stock includes them, so it proves nothing
			rows = [o.get("stock") for o in self.orders if float(o.get("per_received") or 0) < 99.5]
		for s in rows:
			if not s or s["qty"] <= 0:
				continue
			early = not self.receipts
			level = SERIOUS if early else CHECK
			when = "in store now" if early else "in store before this receipt"
			if s["monthly_use"] > 0:
				months = s["qty"] / s["monthly_use"]
				if months <= EXCESS_MONTHS:
					continue
				detail = (f"{number(s['qty'])} {s.get('uom') or ''} of {s['name']} were {when}. Use over the last "
					f"3 months: {number(s['monthly_use'])} a month. That covers "
					+ ("more than 3 years." if months > 36 else f"{months:.1f} months."))
			else:
				detail = (f"{number(s['qty'])} {s.get('uom') or ''} of {s['name']} were {when}, and none was "
					"used in the last 3 months.")
			if early:
				detail += " The money has not gone yet, so this can still be stopped."
			self.add(level, "K1", "Excess stock", detail, [s.get("ref")])
			break

	def order_payment(self):
		"""A payment that equals an order, or a part of it, with no receipt of its value."""
		o, code = self.order, self.found["code"]
		got = float(o.get("per_received") or 0)
		if self.found.get("before_gst"):
			self.add(NOTE, "N1", "Amount is before GST", f"{o['name']} is {money(o['net'])} + GST = {money(o['total'])}.",
				[o["name"]])
		if code == "advance":
			if got >= 99.5:
				self.add(NOTE, "N3", "The order is already fully received",
					f"{o['name']} shows {got:.0f}% received, so this is a payment after delivery, not an advance.", [o["name"]])
			return
		has_terms = bool(o.get("advance"))
		share = self.found.get("part") or 100
		if got >= share - 0.5:
			return
		if got <= 0:
			if o.get("service"):
				self.add(CHECK, "W1", "Payment for work with nothing recorded as done",
					f"{o['name']} is a service or work order of {money(o['total'])}. No receipt or completion is "
					"recorded." + ("" if has_terms else " The order has no advance terms."), [o["name"]])
			else:
				level = CHECK if has_terms else SERIOUS
				self.add(level, "W2", "Payment for an order with nothing received",
					f"{o['name']} ({money(o['total'])}) shows nothing received."
					+ ("" if has_terms else " The order has no advance terms."), [o["name"]])
		else:
			self.add(CHECK, "W3", f"Paying {share}% of the order with {got:.0f}% received",
				f"{o['name']} is {money(o['total'])}. {got:.0f}% of it is recorded as received.", [o["name"]])

	def _position(self):
		"""The supplier's running position: (received, asked, sentence, orders with advance terms, live ones)."""
		f = self.facts
		# `asked_twice`: the second of two lists of one date with the same amount, which may be one list
		received, twice = float(f.get("received") or 0), float(f.get("asked_twice") or 0)
		before = float(f.get("asked_before") or 0) + twice
		asked = before + float(self.ctx.get("list_asked") or self.amount)
		# a list dated before the day of its check counts back from its own date: the sentence says so
		period = f"in the {POSITION_DAYS} days to {day(f['until'])}" if f.get("until") else f"in the last {POSITION_DAYS} days"
		figures = (f"Received {period} {money(received)}. Asked on earlier checked lists "
			f"{money(before)}. With this list {money(asked)}.")
		if twice > TOL:
			figures += (f" {money(twice)} of the earlier money is on two lists of one date with the same amount, "
				"which may be one list.")
		with_terms = [o for o in f.get("orders") or [] if o.get("advance") and approved(o)]
		return received, asked, figures, with_terms, any(not o.get("on_hold") and not o.get("closed") for o in with_terms), twice

	def paid_ahead(self):
		"""A bill asked in full after on-account money was asked on an earlier list: the on-account money may
		already cover part of it. Only when the supplier is asked more than was received."""
		loose = [row for row in self.facts.get("asked_rows") or [] if row.get("loose") and row.get("recent")
			and not row.get("superseded") and (row["report"], row["line_no"]) not in (self.ctx.get("carried") or ())]
		received, asked, figures, _with_terms, has_terms, _twice = self._position()
		if loose and asked > received + TOL and not has_terms:
			row = loose[0]
			self.add(CHECK, "P3", "On-account money was asked before this bill",
				f"Line {row['line_no']} of the list of {day(row['list_date'])} asked {money(row['amount'])} on account, tied "
				f"to no bill. This line asks its bill in full. {figures}", [row["report"]])

	def position(self):
		"""A round on-account payment cannot be tied to a bill: the supplier's running position instead."""
		f = self.facts
		received, asked, figures, with_terms, has_terms, twice = self._position()
		if asked - twice > received + TOL and not has_terms:
			self.add(SERIOUS, "P1", f"Asked {money(asked - twice - received)} more than was received",
				figures + (" The only approved order with advance terms is on hold or closed." if with_terms
					else " No approved order of this supplier carries advance terms."))
		elif asked > received + TOL and not has_terms:
			self.add(CHECK, "P2", "On-account payment", "It cannot be tied to one bill. " + figures
				+ " If they are one list, the total is inside what was received.")
		else:
			self.add(CHECK, "P2", "On-account payment", "It cannot be tied to one bill. " + figures)
		# the trail of an on-account line is the supplier's recent deliveries as a whole, with one point
		# for loads that were never weighed (not one point per load)
		recent = f.get("receipts") or []
		raw = [r for r in recent if r.get("rm")]
		unweighed = [r for r in raw if not r.get("wb_done")]
		if unweighed:
			self.add(CHECK, "G7", f"{len(unweighed)} of {len(raw)} recent raw material loads have no weighbridge slip",
				"Receipts of this supplier with no completed weighbridge entry: "
				+ ", ".join(r["name"] for r in unweighed[:6]) + ("…" if len(unweighed) > 6 else "."),
				[r["name"] for r in unweighed[:6]])
		self.receipts, self.orders = recent, self._orders_of(recent)
		return figures

	def notes(self):
		f = self.facts
		if (f.get("orders_30d") or 0) >= MANY_ORDERS:
			self.add(CHECK, "O1", "Many orders to one supplier",
				f"{f['orders_30d']} orders in 30 days, {money(f.get('orders_30d_total'))} in total. "
				"Open Order Watch for the full group.")
		for o in self.orders:
			for draft in f.get("drafts") or []:
				if draft["name"] != o["name"] and _same(draft["total"], o["total"]):
					self.add(NOTE, "D1", "Draft copy of an order still open",
						f"{draft['name']} is an unsubmitted order of the same value as {o['name']}.", [draft["name"]])
					break
		if self.order and f.get("first_order"):
			self.add(NOTE, "N2", "New supplier", "This is the first order to this supplier.")

	# ---- the trail and the two sentences

	def chain(self):
		if not self.facts.get("supplier"):
			keys = ("Request", "Order", "Terms", "Asked before", "Stock") if self.advance else (
				"Request", "Order", "Gate token", "Weighbridge", "Receipt", "Stock")
			return [_node(k, "na", "Not known") for k in keys]
		rules = {p["rule"]: p["level"] for p in self.points}
		orders, receipts = self.orders, self.receipts
		requests = [m for o in orders for m in o.get("requests") or []]
		out = [_node("Request", "ok" if requests else "na", _names(requests, "Order made directly" if orders else "None", "requests"))]
		if not orders:
			named = [name for r in receipts for name in r.get("orders") or []]
			if "B1" in rules:
				out.append(_node("Order", "warn", "No order behind the receipt"))
			elif named:  # the receipt names an order that this check could not read
				out.append(_node("Order", "warn", _names(named, "", "orders") + ", not read"))
			else:
				out.append(_node("Order", "bad" if self.found["code"] == "none" else "na", "None matched"))
		else:
			state, tail = (("bad", ", not approved") if "A1" in rules else ("bad", ", on hold") if "A2" in rules
				else ("bad", ", asked over its value") if "R5" in rules else ("warn", ", closed") if "A3" in rules else ("warn", ", received over") if "Q1" in rules else ("ok", ", approved"))
			out.append(_node("Order", state, _names([o["name"] for o in orders], "", "orders") + tail))
		if self.advance:
			term = self.found.get("term")
			out.append(_node("Terms", "ok" if term else "warn", term["term"] if term else "No advance terms"))
			out.append(_node("Asked before", "bad" if "R2" in rules or "R1" in rules or rules.get("Y3") == SERIOUS else "ok",
				"Paid in the ERP" if rules.get("Y3") == SERIOUS else "On an earlier list" if "R2" in rules else "Not on an earlier list"))
		else:
			service = bool(orders) and all(o.get("service") for o in orders) and not receipts
			raw = [r for r in receipts if r.get("rm")]
			with_token = [r for r in receipts if r.get("token")]
			if service:
				out += [_node("Gate token", "na", "Service"), _node("Weighbridge", "na", "Service")]
			elif not receipts:
				out += [_node("Gate token", "na", "No receipt"), _node("Weighbridge", "na", "No receipt")]
			else:
				state = "ok" if len(with_token) == len(receipts) else "bad" if "G1" in rules else "warn"
				text = with_token[0]["token"] if len(receipts) == 1 and with_token else f"{len(with_token)} of {len(receipts)} loads"
				out.append(_node("Gate token", state, text))
				if not raw:
					out.append(_node("Weighbridge", "na", "Not raw material"))
				else:
					done = [r for r in raw if r.get("wb_done")]
					short = "warn" if self.found["code"] == "account" else "bad"
					state = short if len(done) < len(raw) else "warn" if "G4" in rules or "G5" in rules else "ok"
					text = ("RST " + str(done[0]["rst"]) if done[0].get("rst") else "Weighed, no slip number") if len(raw) == 1 and done else f"{len(done)} of {len(raw)} slips"
					out.append(_node("Weighbridge", state, text))
			if receipts:
				state = "warn" if "G5" in rules else "ok"
				state = "bad" if rules.keys() & {"V1", "V2", "R3", "R4", "Y1"} else state
				out.append(_node("Receipt", state, _names([r["name"] for r in receipts], "", "receipts")
					+ (", paid in the ERP" if "Y1" in rules else "")))
			elif orders:
				got = max(float(o.get("per_received") or 0) for o in orders)
				weak = rules.keys() & {"W1", "W2", "W3"}
				out.append(_node("Receipt", "warn" if weak else "na" if self.found["code"] == "advance" else "ok",
					f"{got:.0f}% received" if got else "Nothing recorded"))
			else:
				out.append(_node("Receipt", "bad" if self.found["code"] == "none" else "na", "None matched"))
		stocks = [x.get("stock") for x in (receipts or orders) if x.get("stock")]
		if "K1" in rules:
			out.append(_node("Stock", "bad", "Excess stock"))
		elif stocks and stocks[0]["monthly_use"] > 0:
			out.append(_node("Stock", "ok", f"{stocks[0]['qty'] / stocks[0]['monthly_use']:.1f} months of use"))
		elif self.found["code"] in ("account", "none"):
			out.append(_node("Stock", "na", "Not checked"))
		else:
			out.append(_node("Stock", "na", "Not a stock item" if not stocks else "No use recorded"))
		return out

	def erp_says(self, figures=""):
		code, receipts, o = self.found["code"], self.receipts, self.order
		if not self.facts.get("supplier"):
			return "No supplier record matches this name."
		if self.facts.get("hidden"):
			return "Records exist for this supplier, but they are outside the submitter's access."
		if code == "account":
			return figures
		if code == "none":
			last = [money(r["total"]) for r in (self.facts.get("receipts") or [])[:3]]
			if not last:
				return "No receipt and no order of this value in the last 120 days."
			read = f"The newest {len(self.facts['receipts'])} receipts were read. " if self.facts.get("capped") else ""
			return read + "The last receipts are " + ", ".join(last) + f". None equals {money(self.amount)}, alone or added."
		if receipts:
			if len(receipts) == 1:
				r = receipts[0]
				text = f"{r['name']} of {day(r['date'])}, {money(r['total'])}."
				if r.get("wb_qty"):
					text += f" Weighbridge net {number(r['wb_qty'])} {r.get('uom') or ''}" + (f", slip RST {r['rst']}." if r.get("rst") else ".")
				cut = r.get("deduction")
				text += f" Sent back to the supplier: {money(r['returned'])}." if r.get("returned") else ""
				return text + (f" Deduction sheet {cut['name']}: net payable {money(cut['net_payable'])}." if cut else "")
			dates = sorted(str(r["date"]) for r in receipts)
			return (f"{len(receipts)} receipts between {day(dates[0])} and {day(dates[-1])} add up to "
				f"{money(self.amount)}: " + ", ".join(r["name"] for r in receipts[:6]) + ("…" if len(receipts) > 6 else "."))
		text = f"{o['name']} of {day(o['date'])}, {money(o['net'])}" + (f" + GST = {money(o['total'])}" if not _same(o["net"], o["total"]) else "")
		text += ", " + (o.get("approval") or "") + "."
		terms = [t["term"] for t in o.get("advance") or []]
		return text + (" Payment terms: " + ", ".join(terms) + "." if terms else "") + f" Received {float(o.get('per_received') or 0):.0f}%."


def judge(line, facts, ctx=None):
	"""One line's verdict, trail, sentences and points. `ctx` carries what only the whole list knows:
	`list_asked` (this supplier's lines in this list, added), `repeat_of` (an earlier line with the same
	supplier and amount) with `repeat_on` (the receipts that line stands on), `resent` (the earlier lists that
	this list sends again) with `carried` (the lines of those lists that this list repeats, as (report, line)),
	`taken` (the receipts that earlier lines of this list stand on, with their line numbers) and `claims`
	(per earlier line of this list: its line number and what it stands on)."""
	ctx = ctx or {}
	found = match_amount(line, facts, ctx) if facts.get("supplier") else _found("none", "No supplier of this name in the ERP")
	job = _Line(line, facts, found, ctx)
	figures = ""
	if job.supplier():
		job.repeats()
		if facts.get("hidden"):
			job.add(CHECK, "X1", "Outside the submitter's access",
				"Records exist for this supplier, but the person who submitted the list cannot read them.")
		else:
			job.orders_approved()
			job.over_order()
			job.gate()
			job.paid()
			job.stock()
			if found["code"] == "account":
				figures = job.position()
			elif found["code"] == "none":
				job.add(CHECK, "M1", "No receipt or order of this value",
					"It may be a part payment or a payment after a deduction. Nothing in the ERP equals it.")
			elif job.order:
				job.order_payment()
			if found["code"] not in ("account", "none"):
				job.paid_ahead()
			job.notes()
	level = max((p["level"] for p in job.points if p["level"] != NOTE), key=RANK.get, default=OK)
	remark = (line.get("remarks_text") or "").strip()
	says = money(job.amount) + (f', remark "{remark}".' if remark else ".")
	if (line.get("details_text") or "").strip():
		says += " " + line["details_text"].strip()
	return {"verdict": level, "how": found["how"], "list_says": says, "erp_says": job.erp_says(figures),
		"chain": job.chain(), "points": job.points, "code": found["code"],
		"docs": list(dict.fromkeys([r["name"] for r in job.receipts] + [o["name"] for o in job.orders]
			+ [name for r in job.receipts for name in (r.get("invoice") or {}).get("names") or []]))}


def totals(lines, verdicts):
	"""Counts and money by verdict. Each line sits in one group only, so the three sums add up to the list."""
	out = {OK: [0, 0.0], CHECK: [0, 0.0], SERIOUS: [0, 0.0]}
	for line, verdict in zip(lines, verdicts):
		out[verdict][0] += 1
		out[verdict][1] += float(line.get("amount") or 0)
	return out
