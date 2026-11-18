"""AI Checker, step 2: the ERP facts behind each line of a payment list, and the checked list.

Runs inside the background job, as the person who submitted the list. The reads that LIST orders and
receipts (`_receipts`, `_orders`, `_received`) carry the confidentiality and user-permission clauses of
`report_utils`, the house rule for chain reads. Reads keyed on a receipt that was already read that way
(its items, gate entry, weighbridge log, deduction sheet, returns, shared-token count) carry none, and the
"hidden" probes and the first-order count return only a yes/no.
Accounts roles and the MD role are both full readers of confidential purchases, so what the job may read
is what the report's reader may see. The report shows these facts as text: nobody gains a right to open
the documents themselves.

Nothing here writes. The judgement itself is in `ts_ai_check_rules`, which never touches the database."""

import json
import re
from difflib import SequenceMatcher

import frappe
from frappe.utils import add_days, cint, cstr, flt, getdate, nowdate

from trustbit_ethanol.ts_gate_entry import ts_ai_check_rules as rules
from trustbit_ethanol.ts_gate_entry.report import report_utils as ru

RECEIPT_DAYS = 120  # receipts this old can still stand behind a payment
ORDER_DAYS = 365
MAX_DOCS = 300  # receipts, and orders, read for one supplier; the report says so when a supplier has more
USE_DAYS = 90
BACK_DAYS = 7  # a list dated this long before the day of its check gets a point on the whole list
NAME_NOISE = {"MS", "PVT", "PRIVATE", "LTD", "LIMITED", "LLP", "CO", "COMPANY", "AND", "THE"}
CLOSE, NEAR = 0.84, 0.04  # how alike two names must be, and how near a second candidate counts as a rival
USED = ("Material Issue", "Manufacture", "Material Consumption for Manufacture")
REPORT, LINE = "TS AI Check Report", "TS AI Check Line"


def name_key(text):
	"""A supplier name without the parts people write differently every time."""
	text = re.sub(r"\bM\s*/\s*S\b\.?", " ", cstr(text).upper().replace("&", " AND "))
	return " ".join(w for w in re.sub(r"[^A-Z0-9 ]+", " ", text).split() if w not in NAME_NOISE)


def supplier_index():
	rows = frappe.get_all("Supplier", fields=["name", "supplier_name", "disabled", "on_hold"], limit_page_length=0)
	index = []
	for row in rows:
		keys = {name_key(row.supplier_name), name_key(row.name)} - {""}
		index.append(frappe._dict(name=row.name, title=row.supplier_name or row.name, name_keys=keys,
			disabled=row.disabled, on_hold=row.on_hold))
	return index


def _words(key, other):
	"""One name is the other with words added, as in "Sample Engg" and "Sample Engg Works". One shared word
	is never enough: a wrong supplier is worse than no supplier."""
	a, b = set(key.split()), set(other.split())
	return (a <= b or b <= a) and len(a & b) >= 2


def _alike(key, other):
	if _words(key, other):
		return 0.9
	matcher = SequenceMatcher(None, key, other)
	return matcher.ratio() if matcher.quick_ratio() >= CLOSE else 0.0


def resolve(text, index):
	"""The supplier a typed name means, or None. `others` names the rivals when the name fits several."""
	key = name_key(text)
	if not key:
		return None
	exact = [s for s in index if key in s.name_keys]
	if exact:
		scored = [(1.0, s) for s in exact]
	else:
		scored = [(max(_alike(key, k) for k in s.name_keys), s) for s in index if s.name_keys]
		scored = [pair for pair in scored if pair[0] >= CLOSE]
	if not scored:
		return None
	scored.sort(key=lambda pair: (-pair[0], pair[1].disabled, pair[1].name))
	score, best = scored[0]
	return {"name": best.name, "title": best.title, "disabled": best.disabled, "on_hold": best.on_hold,
		# "close" is a likeness of letters only ("Jai" and "Jain"): the rules ask a person to confirm it
		"how": "exact" if exact else "words" if any(_words(key, k) for k in best.name_keys) else "close",
		"others": [s.title for other, s in scored[1:] if score - other <= NEAR]}


# ---- reads, each for ONE supplier


def _sql(query, values):
	return frappe.db.sql(query, values, as_dict=True)


def _by(rows, key):
	out = {}
	for row in rows:
		out.setdefault(row[key], []).append(row)
	return out


def _where(doctype, alias, *conds):
	# Frappe hands the user-permission clause over with every "%" already doubled, as a query that is run
	# with values needs it: it is used as it comes
	return " AND ".join([*conds, *ru.conf_match_clauses(doctype, alias)])


def _receipts(supplier, company, lo, hi):
	if not ru.hop_readable("Purchase Receipt"):
		return []  # the submitter may not read receipts at all: `supplier_facts` then says so
	where = _where("Purchase Receipt", "pr", "pr.docstatus = 1", "pr.is_return = 0", "pr.base_grand_total > 0", "pr.supplier = %(s)s",
		"pr.company = %(c)s", "pr.posting_date BETWEEN %(lo)s AND %(hi)s")
	rows = _sql(f"""SELECT pr.name, pr.posting_date AS date, pr.posting_time, pr.base_grand_total AS total,
			pr.base_net_total AS net, pr.ts_token AS token, pr.ts_gate_entry AS gate_entry,
			pr.custom_rst_number AS rst
		FROM `tabPurchase Receipt` pr WHERE {where}
		ORDER BY pr.posting_date DESC, pr.posting_time DESC, pr.name DESC LIMIT {MAX_DOCS}""",
		{"s": supplier, "c": company, "lo": lo, "hi": hi})
	if not rows:
		return []
	names = tuple(r.name for r in rows)
	items = _by(_sql("""SELECT parent, purchase_order, item_code, item_name, qty, base_rate AS rate, uom,
		base_amount AS amount FROM `tabPurchase Receipt Item` WHERE parent IN %(n)s ORDER BY idx""", {"n": names}), "parent")
	tokens = tuple({r.token for r in rows if r.token})
	gates = tuple({r.gate_entry for r in rows if r.gate_entry})
	flows = dict(frappe.db.sql("SELECT name, material_flow FROM `tabTS Gate Entry` WHERE name IN %(n)s", {"n": gates})) if gates else {}
	logs, shared = {}, {}
	if tokens:
		for log in _sql("""SELECT token_number, status, net_weight, net_weight_in_po_uom AS qty, po_uom, rst_number
				FROM `tabTS Weighbridge Log` WHERE token_number IN %(t)s AND docstatus < 2 ORDER BY creation""", {"t": tokens}):
			logs[log.token_number] = log
		shared = dict(frappe.db.sql("""SELECT ts_token, COUNT(*) - 1 FROM `tabPurchase Receipt`
			WHERE docstatus = 1 AND is_return = 0 AND ts_token IN %(t)s GROUP BY ts_token HAVING COUNT(*) > 1""", {"t": tokens}))
	# a deduction sheet names its receipt in `grn_reference`; an older sheet names only the gate token
	cut_by_receipt, cut_by_token = {}, {}
	for c in _sql("""SELECT name, grn_reference AS pr, token_number AS token, net_payable, total_deduction AS cut
			FROM `tabTS Deduction Sheet` WHERE docstatus = 1 AND (grn_reference IN %(n)s OR token_number IN %(t)s)
			ORDER BY creation""", {"n": names, "t": tokens or ("",)}):
		if c.pr:
			cut_by_receipt[c.pr] = c
		elif c.token:
			cut_by_token[c.token] = c
	sent_back = dict(frappe.db.sql("""SELECT return_against, SUM(ABS(base_grand_total)) FROM `tabPurchase Receipt`
		WHERE docstatus = 1 AND is_return = 1 AND return_against IN %(n)s GROUP BY return_against""", {"n": names}))
	raw_items = _raw_material({i.item_code for rows_ in items.values() for i in rows_})
	bills = _bills(names)
	out = []
	for r in rows:
		mine = items.get(r.name) or []
		main = max(mine, key=lambda i: flt(i.amount), default=None)
		log = logs.get(r.token) or frappe._dict()
		one_item = len({(i.item_code, flt(i.rate), i.uom) for i in mine}) == 1
		same_unit = one_item and cstr(log.po_uom).strip().lower() == cstr(mine[0].uom).strip().lower()
		cut = cut_by_receipt.get(r.name) or cut_by_token.get(r.token)
		out.append({
			"name": r.name, "date": cstr(r.date), "time": cstr(r.posting_time), "total": flt(r.total), "net": flt(r.net),
			"token": r.token, "rst": cstr(r.rst or log.rst_number).strip(),
			"rm": flows.get(r.gate_entry) == "Raw Material" if r.gate_entry in flows else bool(main and main.item_code in raw_items),
			"wb_done": log.status == "Completed" and flt(log.net_weight) > 0,
			"wb_qty": flt(log.qty) if same_unit and flt(log.qty) > 0 else None,
			"qty": sum(flt(i.qty) for i in mine) if one_item else None,
			"rate": flt(mine[0].rate) if one_item else None, "uom": mine[0].uom if one_item else None,
			"orders": list(dict.fromkeys(i.purchase_order for i in mine if i.purchase_order)),
			"deduction": {"name": cut.name, "net_payable": flt(cut.net_payable), "cut": flt(cut.cut)} if cut else None,
			"token_shared": int(shared.get(r.token) or 0), "returned": flt(sent_back.get(r.name)),
			"order_share": _shares(mine), "invoice": bills.get(r.name),
			"item": main.item_code if main else None, "item_name": main.item_name if main else None,
		})
	return out


def _bills(names):
	"""The purchase invoices made from these receipts, and what the ERP shows as still unpaid on them, in the
	company's currency: receipt -> {names, total, open}. An invoice the submitter may not read is left out.
	The ERP keeps the unpaid amount in the currency of the supplier's account, which is the invoice's own
	currency or the company's."""
	if not ru.hop_readable("Purchase Invoice"):
		return {}
	where = _where("Purchase Invoice", "pi", "pi.docstatus = 1", "pi.is_return = 0", "pi.grand_total > 0",
		"i.purchase_receipt IN %(n)s")
	out = {}
	for row in _sql(f"""SELECT DISTINCT i.purchase_receipt AS pr, pi.name, pi.base_grand_total AS total,
			CASE WHEN pi.party_account_currency = pi.currency THEN pi.base_grand_total * pi.outstanding_amount / pi.grand_total
				ELSE pi.outstanding_amount END AS unpaid
		FROM `tabPurchase Invoice` pi JOIN `tabPurchase Invoice Item` i ON i.parent = pi.name
		WHERE {where} ORDER BY pi.name""", {"n": names}):
		bill = out.setdefault(row.pr, {"names": [], "total": 0.0, "open": 0.0})
		bill["names"].append(row.name)
		bill["total"] += flt(row.total)
		bill["open"] += max(flt(row.unpaid), 0.0)
	return out


def _renamed(receipts, orders, supplier):
	"""Earlier names of the documents this check read: old name -> today's name. A receipt or an order that
	was cancelled and amended carries a new name, and a receipt made again for the same gate token stands
	in the place of the cancelled one. A record that was deleted leaves nothing to follow."""
	out = {}
	for doctype, names in (("Purchase Receipt", [r["name"] for r in receipts]), ("Purchase Order", [o["name"] for o in orders])):
		today, look = {name: name for name in names}, list(names)
		for _step in range(5):  # an amended copy of an amended copy, and so on
			if not look:
				break
			rows = frappe.db.sql(f"""SELECT name, amended_from FROM `tab{doctype}`
				WHERE name IN %(n)s AND IFNULL(amended_from, '') != ''""", {"n": look})
			look = []
			for name, older in rows:
				if older not in today:
					today[older] = out[older] = today[name]
					look.append(older)
	by_token = {}
	for r in receipts:
		if r["token"]:
			by_token.setdefault(r["token"], []).append(r["name"])
	single = {token: names[0] for token, names in by_token.items() if len(names) == 1}
	if single:
		for name, token in frappe.db.sql("""SELECT name, ts_token FROM `tabPurchase Receipt`
				WHERE docstatus = 2 AND is_return = 0 AND supplier = %(s)s AND ts_token IN %(t)s""", {"s": supplier, "t": list(single)}):
			out.setdefault(name, single[token])
	return out


def _follow(rows, renamed):
	"""Earlier lines as they stand today: a document that was cancelled and made again is called by its new
	name, so a second request on it is seen. `was` keeps the old name for the sentence."""
	for row in rows if renamed else []:
		was = {renamed[name]: name for name in row["receipts"] if name in renamed}
		if was:
			row["was"] = was
		row["receipts"] = list(dict.fromkeys(renamed.get(name, name) for name in row["receipts"]))
		row["claims"] = [[kind, renamed.get(doc, doc), renamed.get(order, order), value] for kind, doc, order, value in row["claims"]]
	return rows


def _shares(items):
	"""How a receipt's value falls on the orders it names ("" = no order), as parts of one."""
	by_order = {}
	for i in items:
		by_order[i.purchase_order or ""] = by_order.get(i.purchase_order or "", 0) + flt(i.amount)
	whole = sum(by_order.values())
	return {name: value / whole for name, value in by_order.items()} if whole > 0 else {}


def _raw_material(items):
	"""Which of these items sit under the item group 'Raw Material', for receipts with no gate entry."""
	if not items:
		return set()
	return set(frappe.db.sql_list("""SELECT i.name FROM `tabItem` i JOIN `tabItem Group` g ON g.name = i.item_group
		JOIN `tabItem Group` top ON top.name = 'Raw Material' AND g.lft >= top.lft AND g.rgt <= top.rgt
		WHERE i.name IN %(i)s""", {"i": tuple(items)}))


def _received(supplier, company, lo, hi):
	"""Value received in a period, less what was sent back (a return is a receipt with a minus value).
	Its own sum, so the cap on receipts read never shortens it."""
	if not ru.hop_readable("Purchase Receipt"):
		return 0.0
	where = _where("Purchase Receipt", "pr", "pr.docstatus = 1", "pr.supplier = %(s)s", "pr.company = %(c)s",
		"pr.posting_date BETWEEN %(lo)s AND %(hi)s")
	return flt(frappe.db.sql(f"SELECT SUM(pr.base_grand_total) FROM `tabPurchase Receipt` pr WHERE {where}",
		{"s": supplier, "c": company, "lo": lo, "hi": hi})[0][0])


def _orders(supplier, company, lo, hi, wanted):
	"""Submitted orders and orders in the approval system (waiting, sent back or refused: drafts here),
	newest first, plus every order a receipt names."""
	if not ru.hop_readable("Purchase Order"):
		return [], []
	hold = "po.ts_po_on_hold" if frappe.db.has_column("Purchase Order", "ts_po_on_hold") else "0"
	pick = "(po.transaction_date BETWEEN %(lo)s AND %(hi)s" + (" OR po.name IN %(w)s)" if wanted else ")")
	where = _where("Purchase Order", "po", "po.docstatus < 2", "po.supplier = %(s)s", "po.company = %(c)s", pick)
	rows = _sql(f"""SELECT po.name, po.transaction_date AS date, po.base_grand_total AS total, po.base_net_total AS net,
			po.docstatus, po.ts_approval_status AS approval, po.per_received, po.status, {hold} AS on_hold,
			CASE WHEN po.party_account_currency = po.currency THEN po.advance_paid * po.conversion_rate
				ELSE po.advance_paid END AS advance_paid
		FROM `tabPurchase Order` po WHERE {where}
		ORDER BY {"po.name IN %(w)s DESC, " if wanted else ""}po.transaction_date DESC, po.name DESC LIMIT {MAX_DOCS}""",
		{"s": supplier, "c": company, "lo": lo, "hi": hi, "w": tuple(wanted)})
	if not rows:
		return [], []
	names = tuple(r.name for r in rows)
	items = _by(_sql("""SELECT poi.parent, poi.item_code, poi.item_name, poi.qty, poi.received_qty, poi.uom, poi.base_amount AS amount,
			poi.material_request, i.is_stock_item
		FROM `tabPurchase Order Item` poi LEFT JOIN `tabItem` i ON i.name = poi.item_code
		WHERE poi.parent IN %(n)s ORDER BY poi.idx""", {"n": names}), "parent")
	terms = _by(_sql("""SELECT parent, payment_term, description, invoice_portion, base_payment_amount, payment_amount
		FROM `tabPayment Schedule` WHERE parenttype = 'Purchase Order' AND parent IN %(n)s ORDER BY idx""", {"n": names}), "parent")
	orders, drafts = [], []
	for r in rows:
		if r.docstatus == 0 and cstr(r.approval) in ("", "Draft", "Not Submitted"):  # never sent for approval
			drafts.append({"name": r.name, "total": flt(r.total)})
			continue
		mine = items.get(r.name) or []
		main = max((i for i in mine if i.is_stock_item), key=lambda i: flt(i.amount), default=None)
		advance = [{"term": cstr(t.payment_term or t.description).strip()[:60], "portion": flt(t.invoice_portion),
				"amount": flt(t.base_payment_amount) or flt(t.payment_amount)}
			for t in terms.get(r.name) or []
			if "adv" in cstr(t.payment_term).lower() or "advance" in cstr(t.description).lower()]
		orders.append({
			"name": r.name, "date": cstr(r.date), "total": flt(r.total), "net": flt(r.net), "docstatus": r.docstatus,
			"approval": r.approval, "per_received": flt(r.per_received), "advance": advance,
			"on_hold": bool(cint(r.on_hold) or r.status == "On Hold"), "closed": r.status == "Closed",
			"advance_paid": flt(r.advance_paid),  # in the company's currency, see the query
			"service": not any(i.is_stock_item for i in mine),
			"requests": list(dict.fromkeys(i.material_request for i in mine if i.material_request)),
			"items": [{"name": i.item_name or i.item_code, "qty": flt(i.qty), "received_qty": flt(i.received_qty),
				"uom": i.uom} for i in mine],
			"item": main.item_code if main else None, "item_name": main.item_name if main else None,
		})
	return orders, drafts


def _stock(item, name, ref, before=None):
	"""What was in store (all warehouses) just before a moment, or now, and the monthly use before it.
	Stock on a date is the latest running balance of each warehouse, never a sum of movements."""
	if not item or not frappe.db.get_value("Item", item, "is_stock_item"):
		return None
	if before:
		qty = frappe.db.sql("""SELECT SUM((SELECT s.qty_after_transaction FROM `tabStock Ledger Entry` s
				WHERE s.item_code = %(i)s AND s.warehouse = w.warehouse AND s.is_cancelled = 0 AND s.posting_datetime < %(t)s
				ORDER BY s.posting_datetime DESC, s.creation DESC LIMIT 1))
			FROM (SELECT DISTINCT warehouse FROM `tabStock Ledger Entry` WHERE item_code = %(i)s AND is_cancelled = 0) w""",
			{"i": item, "t": before})[0][0]
		day = before[:10]
	else:
		qty = frappe.db.sql("SELECT SUM(actual_qty) FROM `tabBin` WHERE item_code = %(i)s", {"i": item})[0][0]
		day = nowdate()
	used = frappe.db.sql("""SELECT -SUM(s.actual_qty) FROM `tabStock Ledger Entry` s
		JOIN `tabStock Entry` e ON e.name = s.voucher_no
		WHERE s.voucher_type = 'Stock Entry' AND s.item_code = %(i)s AND s.is_cancelled = 0 AND s.actual_qty < 0
			AND e.purpose IN %(p)s AND s.posting_date BETWEEN %(lo)s AND %(hi)s""",
		{"i": item, "p": USED, "lo": add_days(day, -USE_DAYS), "hi": day})[0][0]
	return {"name": name or item, "qty": flt(qty), "monthly_use": flt(used) / (USE_DAYS / 30), "ref": ref,
		"uom": frappe.db.get_value("Item", item, "stock_uom")}


def _asked_before(supplier, company, this_report):
	"""This supplier's lines on the lists checked here before this one, whatever date was typed on them:
	a list is earlier because it was checked earlier. Every line is returned, with the receipts and the
	order it stood on. `recent` marks the rows that count for the on-account position."""
	today = nowdate()
	rows = _sql(f"""SELECT r.name AS report, r.list_date, DATE(r.creation) AS checked, l.line_no, l.amount, l.receipts_used, l.claims, l.match_how
		FROM `tab{LINE}` l JOIN `tab{REPORT}` r ON r.name = l.parent
		WHERE r.company = %(c)s AND l.supplier = %(s)s AND r.creation >= %(lo)s AND r.name != %(me)s
		ORDER BY r.creation DESC, l.idx""", {"c": company, "s": supplier, "lo": add_days(today, -ORDER_DAYS), "me": this_report})
	return ([{"report": row.report, "list_date": cstr(row.list_date), "line_no": row.line_no, "amount": flt(row.amount),
		"receipts": [name for name in cstr(row.receipts_used).split(", ") if name], "claims": _claims(row.claims),
		"loose": cstr(row.match_how) == rules.ACCOUNT_HOW,
		"recent": cstr(row.checked) >= cstr(add_days(today, -rules.POSITION_DAYS))} for row in rows])


def _lay_over(rows, again=None):
	"""A list sent again replaces the lines it repeats. The older copy of such a line is marked
	`superseded`, so money asked once counts once, whatever date was typed on the second copy. Which list
	sent which again is the T2 point saved with the later one. A line is the same line when the amount
	agrees, or when it stands on the same receipt or order (a corrected amount)."""
	reports = sorted({row["report"] for row in rows})
	if again is None:
		again = frappe.db.sql("""SELECT parent, refs FROM `tabTS AI Check Finding`
			WHERE rule = 'T2' AND line_no = 0 AND parent IN %(r)s""", {"r": reports}) if len(reports) > 1 else ()
	stands = lambda row: {(kind, doc or order) for kind, doc, order, _value in row["claims"]}  # noqa: E731
	for newer, refs in again:
		for older in cstr(refs).split(", "):
			old = [row for row in rows if row["report"] == older]
			left = []
			for row in (r for r in rows if r["report"] == newer and old):
				hit = next((o for o in old if not o.get("superseded") and abs(o["amount"] - row["amount"]) <= rules.TOL), None)
				if hit:
					hit["superseded"] = True
				else:
					left.append(row)
			for row in left:
				hit = next((o for o in old if not o.get("superseded") and stands(o) & stands(row)), None)
				if hit:
					hit["superseded"] = True
	return rows


def _claims(text):
	"""The claims stored with an earlier line; a line from before they were stored has none. Only rows
	of the stored shape are kept: [kind, document, order, money]."""
	try:
		rows = json.loads(text or "[]")
	except ValueError:
		return []
	number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
	return [row for row in rows if isinstance(row, list) and len(row) == 4 and row[0] in ("o", "r")
		and isinstance(row[1], str) and isinstance(row[2], str) and number(row[3])] if isinstance(rows, list) else []


def asked_sum(rows, skip=(), twice=False):
	"""Money asked on earlier lists, for the on-account position. A list sent twice is counted once: lines
	that a later copy replaced are left out, lists of one date are laid over each other, and an amount
	counts as often as the list that has it most. `skip` is the lines that this list itself carries again.
	With `twice`, the answer is the part that this laying-over left out: two lists of one date with the same
	amount may also be two lists, so the on-account rule shows that part and grades it To check."""
	per = {}
	for row in rows:
		if row["recent"] and not row.get("superseded") and (row["report"], row.get("line_no")) not in skip:
			counts = per.setdefault((row["list_date"], round(row["amount"], 2)), {})
			counts[row["report"]] = counts.get(row["report"], 0) + 1
	return sum(key[1] * (sum(counts.values()) - max(counts.values()) if twice else max(counts.values())) for key, counts in per.items())


def supplier_facts(supplier, company, list_date, this_report):
	"""Everything the rules need about one supplier, as plain values."""
	hi = cstr(list_date)
	receipts = _receipts(supplier, company, add_days(hi, -RECEIPT_DAYS), hi)
	orders, drafts = _orders(supplier, company, add_days(hi, -ORDER_DAYS), hi,
		{name for r in receipts for name in r["orders"]})
	# today's names first: a corrected copy of a list is laid over its first copy by the documents it stands on
	asked = _lay_over(_follow(_asked_before(supplier, company, this_report), _renamed(receipts, orders, supplier)))
	recent = add_days(hi, -rules.POSITION_DAYS)
	month = add_days(hi, -30)
	in_month = [o for o in orders if o["docstatus"] == 1 and month <= o["date"] <= hi]
	facts = {
		"receipts": receipts, "orders": orders, "drafts": drafts, "asked_rows": asked,
		"asked_before": asked_sum(asked), "asked_twice": asked_sum(asked, twice=True), "until": hi if hi < nowdate() else None,
		"received": _received(supplier, company, recent, hi), "capped": len(receipts) >= MAX_DOCS,
		"orders_30d": len(in_month), "orders_30d_total": sum(o["total"] for o in in_month),
		"first_order": frappe.db.count("Purchase Order", {"supplier": supplier, "docstatus": 1}) <= 1,
	}
	if not receipts and not orders:
		# the submitter may be kept from this supplier's documents: "not found" would then be untrue. The
		# two probes look in the same periods as the reads above, without the reader's limits
		v = {"s": supplier, "c": company, "hi": hi, "lo_o": add_days(hi, -ORDER_DAYS), "lo_r": add_days(hi, -RECEIPT_DAYS)}
		facts["hidden"] = bool(frappe.db.sql("""SELECT 1 FROM `tabPurchase Order` WHERE supplier = %(s)s AND company = %(c)s
			AND docstatus = 1 AND transaction_date BETWEEN %(lo_o)s AND %(hi)s LIMIT 1""", v) or frappe.db.sql("""SELECT 1
			FROM `tabPurchase Receipt` WHERE supplier = %(s)s AND company = %(c)s AND docstatus = 1 AND is_return = 0
			AND base_grand_total > 0 AND posting_date BETWEEN %(lo_r)s AND %(hi)s LIMIT 1""", v))
	return facts


def _add_stock(found):
	"""Stock is read only for the documents a line was matched to: at most three per line."""
	if found["receipts"]:
		for r in found["receipts"][:3]:
			if "stock" not in r:
				r["stock"] = _stock(r["item"], r["item_name"], r["name"], before=f"{r['date']} {r['time'] or '00:00:00'}")
	elif found.get("order") and "stock" not in found["order"]:
		o = found["order"]
		o["stock"] = _stock(o["item"], o["item_name"], o["name"])


def _safe(text, limit=None):
	"""Text for the report. Names from the ERP go into these sentences, and a form's grid shows a stored
	value as HTML, so the two signs that make HTML are replaced, as the reader does for text from the file."""
	text = cstr(text).replace("<", "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}").replace(">", "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}")
	return text[:limit] if limit else text


def check(doc, result):
	"""The checked list: per line the supplier, verdict, trail and sentences; the points; the counts."""
	company = doc.company
	list_date = getdate(doc.list_date) if doc.list_date else getdate(nowdate())
	list_date = min(list_date, getdate(nowdate()))
	index, cache, resolved = supplier_index(), {}, []
	for line in result.lines:
		supplier = resolve(line.get("supplier_text"), index)
		resolved.append(supplier)
		if supplier and supplier["name"] not in cache:
			cache[supplier["name"]] = supplier_facts(supplier["name"], company, list_date, doc.name)

	# what only the whole list knows. An amount that was not read (0) is never "the same amount"
	n = len(result.lines)
	asked_here, first_seen, repeats = {}, {}, {}
	for line, supplier in zip(result.lines, resolved):
		if supplier:
			asked_here[supplier["name"]] = asked_here.get(supplier["name"], 0) + flt(line.get("amount"))
			key = (supplier["name"], round(flt(line.get("amount")), 2))
			if key[1] > 0 and key in first_seen:
				repeats[line["line_no"]] = first_seen[key]
			first_seen.setdefault(key, line["line_no"])
	# only a line with a supplier and an amount can be compared with an earlier list
	comparable = sum(1 for line, supplier in zip(result.lines, resolved) if supplier and flt(line.get("amount")) > 0)
	on_earlier, carried = {}, set()
	for line, supplier in zip(result.lines, resolved):
		hit = set()  # every earlier list that has this line counts it once, and one earlier line serves one line
		for row in (cache[supplier["name"]]["asked_rows"] if supplier and flt(line.get("amount")) > 0 else []):
			key = (row["report"], row["line_no"])
			if abs(row["amount"] - flt(line.get("amount"))) <= rules.TOL and row["report"] not in hit and key not in carried:
				hit.add(row["report"])
				carried.add(key)
				on_earlier[row["report"]] = on_earlier.get(row["report"], 0) + 1
	sizes = dict(frappe.db.sql(f"""SELECT parent, COUNT(*) FROM `tab{LINE}`
		WHERE parent IN %(r)s AND IFNULL(supplier, '') != '' AND amount > 0 GROUP BY parent""", {"r": list(on_earlier)})) if on_earlier else {}
	# "sent again" needs most of this list AND most of the earlier one: one repeated line cut out as a
	# list of its own is not a list sent again
	resubmitted = {report for report, hits in on_earlier.items() if comparable >= 3 and hits * 2 > comparable
		and hits * 2 > cint(sizes.get(report))}
	carried = {key for key in carried if key[0] in resubmitted}  # the earlier lines that this list repeats

	lines, findings, verdicts = [], [], []
	taken, stood, standing = {}, {}, []  # receipt -> line; line -> its receipts; (line, what it stands on)
	for line, supplier in zip(result.lines, resolved):
		facts = {**cache[supplier["name"]], "supplier": supplier} if supplier else {}
		used, found = [], {}
		ctx = {"list_asked": asked_here.get(supplier["name"]) if supplier else None, "resent": resubmitted, "carried": carried,
			"repeat_of": repeats.get(line["line_no"]), "repeat_on": stood.get(repeats.get(line["line_no"])) or [],
			"taken": dict(taken), "claims": list(standing)}
		if supplier:
			# a list sent again is not added on top of its own earlier checks
			facts["asked_before"] = asked_sum(facts["asked_rows"], carried)
			facts["asked_twice"] = asked_sum(facts["asked_rows"], carried, twice=True)
			found = rules.match_amount(line, facts, ctx)
			_add_stock(found)
			used = [r["name"] for r in found["receipts"]]
		judged = rules.judge(line, facts, ctx)
		# one receipt stands behind one line, and an order is asked for once: the next line is told so
		stood[line["line_no"]] = used
		for name in used:
			taken.setdefault(name, line["line_no"])
		mine = rules.claims(found, flt(line.get("amount"))) if found else []
		standing.append((line["line_no"], mine))
		verdicts.append(judged["verdict"])
		lines.append({"verdict": judged["verdict"], "supplier": supplier["name"] if supplier else "",
			"supplier_title": _safe(supplier["title"], 140) if supplier else "", "match_how": _safe(judged["how"], 140),
			"list_says": _safe(judged["list_says"], 1000), "erp_says": _safe(judged["erp_says"], 1000),
			"chain": _safe(json.dumps(judged["chain"], ensure_ascii=False)), "docs": judged["docs"][:20],
			"receipts_used": _safe(", ".join(used[:40])),
			"claims": _safe(json.dumps(mine, ensure_ascii=False))})
		findings += [{"line_no": line["line_no"], "source": "Rule", **point, "title": _safe(point["title"], 140),
			"detail": _safe(point["detail"], 1000), "refs": _safe(point["refs"], 500)} for point in judged["points"]]

	sums = rules.totals(result.lines, verdicts)
	printed, total = result.printed_total, flt(result.list_total)
	if printed is not None and abs(total - flt(printed)) > 0.50:
		level = rules.CHECK if cstr(result.read_by).startswith("AI") else rules.SERIOUS
		findings.insert(0, {"line_no": 0, "source": "Rule", "level": level, "rule": "T1", "refs": "",
			"title": "The list total does not add up",
			"detail": f"The lines add up to {rules.money(total)}. The total printed on the list is {rules.money(printed)}."})
	if resubmitted:
		findings.insert(0, {"line_no": 0, "source": "Rule", "level": rules.NOTE, "rule": "T2", "refs": ", ".join(sorted(resubmitted)),
			"title": "Most of this list was on an earlier checked list",
			"detail": "It looks like a list sent again. Repeated lines are graded To check, not Serious."})
	late = (getdate(nowdate()) - list_date).days
	if late > BACK_DAYS:  # the typed date moves every period this check reads
		findings.insert(0, {"line_no": 0, "source": "Rule", "level": rules.CHECK, "rule": "T3", "refs": "",
			"title": f"The list is dated {late} days before it was checked",
			"detail": f"Receipts and orders after {rules.day(list_date)} were not read, and every period in this report "
				"counts back from that date."})
	whole = [f for f in findings if f["line_no"] == 0 and f["level"] in (rules.SERIOUS, rules.CHECK)]
	found = sum(1 for s in resolved if s)
	read = [r for facts in cache.values() for r in facts["receipts"]]
	raw = [r for r in read if r["rm"]]
	earlier = frappe.db.count(REPORT, {"company": company, "check_type": "Payment List", "name": ("!=", doc.name),
		"creation": (">=", add_days(nowdate(), -rules.POSITION_DAYS))})
	summary = (f"{n} lines, {rules.money(total)}. No issue found: {sums[rules.OK][0]}. To check: {sums[rules.CHECK][0]}. "
		f"Serious: {sums[rules.SERIOUS][0]}. Each line sits in one group only, by its heaviest point.")
	if whole:  # a point on the whole list is in no line's count: the summary and the bell name it
		summary += " On the whole list, " + "; ".join(f"{f['level']}: {f['title']}" for f in whole) + "."
	capped = sum(1 for facts in cache.values() if facts.get("capped"))
	coverage = [
		f"Supplier found in the ERP: {found} of {n} lines.",
		f"Raw material receipts of these suppliers in the last {RECEIPT_DAYS} days with a weighbridge slip: "
		f"{sum(1 for r in raw if r['wb_done'])} of {len(raw)}.",
		f"Earlier lists checked here for this company in the last {rules.POSITION_DAYS} days: {earlier}.",
		f"Receipts read for these suppliers that have a purchase invoice in the ERP: {len([r for r in read if r.get('invoice')])} of {len(read)}.",
		"A line is called 'paid' only where the ERP shows it: an invoice of its receipt with nothing outstanding, or "
		"an advance recorded on its order. A line is called 'asked before' only when it was on an earlier list "
		"checked here. Payments made through the bank or Tally and not entered in the ERP are not visible to this check.",
	] + ([f"For {capped} supplier(s) only the newest {MAX_DOCS} receipts were read."] if capped else [])
	return frappe._dict(lines=lines, findings=findings, summary=summary, coverage="\n".join(coverage),
		n_ok=sums[rules.OK][0], n_check=sums[rules.CHECK][0], n_serious=sums[rules.SERIOUS][0],
		amt_ok=sums[rules.OK][1], amt_check=sums[rules.CHECK][1], amt_serious=sums[rules.SERIOUS][1])
