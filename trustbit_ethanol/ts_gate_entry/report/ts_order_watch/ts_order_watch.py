# Copyright (c) 2026, Trustbit Software and contributors
"""TS Order Watch: orders that together pass the MD's approval line without
reaching the MD, and other approval facts (plan 7D, build spec 14 Nov 2026).

- Access: MD only, checked in code as the first statement of execute(); the
  JSON role list is menu visibility only.
- Visible orders: every anchor query on tabPurchase Order carries
  ru.conf_match_clauses. Child tables are read only for visible order names
  (ru.chunked), so every total and count covers visible orders only.
- User's decision, 14 Nov 2026 ("Show facts in report"): the MD role cannot
  read Purchase Receipt, Purchase Invoice, TS Token, TS Gate Entry or Version
  and gets no new permission. Those tables are read here with raw SQL, ONLY
  for visible orders (allowance edits: only items received on visible orders),
  and shown as plain text. A deliberate departure from ru.hop_readable; no
  Link to those doctypes is returned.
- All SQL lives here; the checks live in ts_order_watch_rules (no DB access).
"""

import json
from datetime import date

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, fmt_money, get_datetime, getdate, today

from trustbit_ethanol.ts_gate_entry import ts_order_watch_rules as rules
from trustbit_ethanol.ts_gate_entry.report import report_utils as ru

ALLOWED_ROLES = ("MD",)  # user's decision 14 Nov 2026: MD only, no System Manager
# Role label of an account: the first of these it holds, else "Other" (spec 3.6).
ROLE_ORDER = ("MD", "CEO", "AVP", "Purchase Manager", "Grain Purchase Manager", "Purchase User")
PM_ROLE = "Purchase Manager"  # role of the 1-lakh review step in TS PO Approval Step
NUMS = ("conversion_rate", "grand_total", "base_grand_total", "base_net_total", "per_received", "amount_at_submission")
PO_COLS = """po.name, po.company, po.supplier, IFNULL(po.ts_purchase_category, '') AS category, po.currency,
	po.conversion_rate, po.grand_total, po.base_grand_total, po.base_net_total, po.creation, po.transaction_date,
	po.docstatus, po.status, po.ts_approval_status AS approval_status, po.per_received,
	po.ts_approval_rule AS approval_rule, IF(IFNULL(po.ts_submitted_by, '') = '', po.owner, po.ts_submitted_by) AS submitted_by,
	po.ts_self_skip_impossible AS self_skip_impossible, po.ts_amount_at_submission AS amount_at_submission,
	po.ts_approved_date AS approved_date"""
# submitted, or still in approval (a group can be seen before its last order is approved)
LIVE = """(po.docstatus = 1 OR (po.docstatus = 0 AND (po.ts_approval_status LIKE %(pending)s
	OR po.ts_approval_status LIKE %(awaiting)s))) AND IFNULL(po.ts_approval_status, '') != 'Rejected'"""
RECEIVED_IN_WINDOW = """(EXISTS (SELECT 1 FROM `tabPurchase Receipt Item` ri JOIN `tabPurchase Receipt` pr ON pr.name = ri.parent
		WHERE ri.purchase_order = po.name AND pr.docstatus = 1 AND pr.posting_date BETWEEN %(from_date)s AND %(to_date)s)
	OR EXISTS (SELECT 1 FROM `tabPurchase Invoice Item` pii JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
		WHERE pii.purchase_order = po.name AND pi.docstatus = 1 AND pi.update_stock = 1
		AND pi.posting_date BETWEEN %(from_date)s AND %(to_date)s))"""
RECEIPTS = """SELECT ri.purchase_order AS po, 'Purchase Receipt' AS doctype, pr.name AS doc,
		TIMESTAMP(pr.posting_date, pr.posting_time) AS posting, pr.is_return, 1 AS update_stock,
		ri.item_code, ri.qty, ri.base_net_amount, pr.ts_token AS token
	FROM `tabPurchase Receipt Item` ri JOIN `tabPurchase Receipt` pr ON pr.name = ri.parent
	WHERE pr.docstatus = 1 AND ri.purchase_order IN %(n)s
	UNION ALL
	SELECT pii.purchase_order, 'Purchase Invoice', pi.name, TIMESTAMP(pi.posting_date, pi.posting_time), pi.is_return,
		pi.update_stock, pii.item_code, pii.qty, pii.base_net_amount, NULL
	FROM `tabPurchase Invoice Item` pii JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
	WHERE pi.docstatus = 1 AND pii.purchase_order IN %(n)s
	ORDER BY posting"""
GATE = """SELECT po, MIN(t) AS t FROM (
		SELECT ri.purchase_order AS po, tk.creation AS t FROM `tabPurchase Receipt Item` ri
			JOIN `tabPurchase Receipt` pr ON pr.name = ri.parent JOIN `tabTS Token` tk ON tk.name = pr.ts_token
			WHERE pr.docstatus < 2 AND ri.purchase_order IN %(n)s
		UNION ALL
		SELECT ge.purchase_order, LEAST(ge.creation, IFNULL(tk.creation, ge.creation)) FROM `tabTS Gate Entry` ge
			LEFT JOIN `tabTS Token` tk ON tk.name = ge.token_number
			WHERE ge.docstatus < 2 AND ge.purchase_order IN %(n)s
		UNION ALL
		SELECT gp.purchase_order, LEAST(ge.creation, IFNULL(tk.creation, ge.creation)) FROM `tabTS Gate Entry PO` gp
			JOIN `tabTS Gate Entry` ge ON ge.name = gp.parent LEFT JOIN `tabTS Token` tk ON tk.name = ge.token_number
			WHERE gp.parenttype = 'TS Gate Entry' AND ge.docstatus < 2 AND gp.purchase_order IN %(n)s
	) x GROUP BY po"""
VERSIONS = """SELECT ref_doctype, docname, data, creation FROM `tabVersion`
	WHERE ref_doctype = %(dt)s AND {0} AND creation >= %(a)s AND creation < %(b)s AND data LIKE %(like)s"""


def _check_role():
	# never in a background job: a scheduled Auto Email Report runs as Administrator, and "Export in Background"
	# leaves a file that other desk users can open
	if getattr(frappe.local, "job", None):
		frappe.throw(_("Order watch is for the MD."), frappe.PermissionError)
	if frappe.session.user == "Administrator":
		return
	if not set(ALLOWED_ROLES) & set(frappe.get_roles(frappe.session.user)):
		frappe.throw(_("Order watch is for the MD."), frappe.PermissionError)


def execute(filters=None):
	_check_role()  # FIRST STATEMENT, before any filter parsing
	data = load(filters)
	return _columns(), _rows(rules.find_points(data), data)


def _in(sql, names, **params):
	"""Run `sql` once per chunk of `names` (bound as %(n)s); no names, no query."""
	out = []
	for chunk in ru.chunked({x for x in names if x}):
		out += frappe.db.sql(sql, dict(params, n=chunk), as_dict=True)
	return out


def _pos(conds, p):
	"""Visible orders matching `conds`: the anchor query, with confidentiality + match conditions."""
	conds = list(conds) + ru.conf_match_clauses("Purchase Order", "po")
	if p.company:
		conds.append("po.company = %(company)s")
	return frappe.db.sql(f"SELECT {PO_COLS} FROM `tabPurchase Order` po WHERE {' AND '.join(conds)}", p, as_dict=True)


def _lines():
	"""The line per category from the active rule table (spec 3.1), and every rule's steps."""
	rules = {}
	for r in frappe.db.sql("""SELECT r.name, r.purchase_category, r.min_amount, r.is_active, s.step_order, s.role
		FROM `tabTS PO Approval Rule` r LEFT JOIN `tabTS PO Approval Step` s
			ON s.parent = r.name AND s.parenttype = 'TS PO Approval Rule'
		ORDER BY r.name, s.step_order""", as_dict=True):
		x = rules.setdefault(r.name, frappe._dict(cat=r.purchase_category, low=flt(r.min_amount), on=cint(r.is_active), steps=[]))
		if r.step_order is not None:
			x.steps.append({"step_order": cint(r.step_order), "role": r.role})
	lines = {}
	for cat in {x.cat for x in rules.values() if x.on}:
		act = [(x.low, {s["role"] for s in x.steps}) for x in rules.values() if x.on and x.cat == cat]
		md = min((low for low, roles in act if "MD" in roles), default=None)
		pm = min((low for low, roles in act if PM_ROLE in roles), default=None)
		if pm is not None and not any(low < pm and PM_ROLE not in roles for low, roles in act):
			pm = None  # no CEO-alone band below the review step
		lines[cat] = {"md": md, "pm": pm}
	return lines, {name: x.steps for name, x in rules.items()}


def load(filters=None):
	"""The data dict of spec section 2 (the rules' only input)."""
	f = frappe._dict(filters or {})
	to_date = getdate(f.to_date or today())
	from_date = getdate(f.from_date or add_days(to_date, -90))
	if from_date > to_date:
		frappe.throw(_("From Date must be on or before To Date."))
	if f.company and not frappe.db.exists("Company", {"name": f.company}):  # dict form: L449
		frappe.throw(_("Company {0} not found.").format(frappe.utils.escape_html(f.company)))  # shown as HTML
	now = getdate(today())
	fy = date(to_date.year if to_date.month >= 4 else to_date.year - 1, 4, 1)
	p = frappe._dict(company=f.company or None, pending="Pending%", awaiting="Awaiting Send to%",
		from_date=from_date, to_date=to_date, end=add_days(to_date, 1),
		# a group can start 7 days before from_date and its facts look 8 weeks further back; R32: 90 days and the FY
		start=min(add_days(from_date, -63), add_days(to_date, -90), fy))
	lines, rule_steps = _lines()
	found = _pos([LIVE, "po.creation >= %(start)s", "po.creation < %(end)s"], p)
	found += _pos(["po.docstatus = 1", RECEIVED_IN_WINDOW], p)
	md_lines = [v["md"] for v in lines.values() if v["md"] is not None]
	if md_lines:  # R44: open, nothing received, at or over the lowest line, approved over 60 days ago
		p.update(closed=("Closed", "Completed", "Cancelled", "Delivered"), min_md=min(md_lines), aged=add_days(now, -60))
		old = _pos(["po.docstatus = 1", "po.status NOT IN %(closed)s", "IFNULL(po.per_received, 0) = 0",
			"po.base_grand_total >= %(min_md)s", "IFNULL(po.ts_approved_date, po.creation) < %(aged)s"], p)
		if old:  # newer orders to the same suppliers, for "a newer order repeats it"
			p.update(sups=tuple({o.supplier for o in old}), first=min(o.creation for o in old))
			found += old + _pos([LIVE, "po.supplier IN %(sups)s", "po.creation > %(first)s"], p)
	orders = {o.name: o for o in found}

	logs, held = {}, {}
	for r in _in("""SELECT parent, action, action_by, action_by_role, action_date, po_amount, step_order
		FROM `tabTS Approval Log` WHERE parenttype = 'Purchase Order' AND parent IN %(n)s
		ORDER BY action_date, creation, idx""", orders):
		logs.setdefault(r.pop("parent"), []).append(r)
	for r in _in("""SELECT parent, role FROM `tabHas Role` WHERE parenttype = 'User' AND role IN %(roles)s
		AND parent IN %(n)s""", {o.submitted_by for o in orders.values()}, roles=ROLE_ORDER):
		held.setdefault(r.parent, set()).add(r.role)
	default_cat = frappe.db.get_single_value("TS Settings", "default_po_category") or ""
	currency = dict(frappe.db.sql("SELECT name, default_currency FROM `tabCompany`"))
	for o in orders.values():
		steps, lg = rule_steps.get(o.approval_rule), logs.get(o.name, [])
		step_role = {s["step_order"]: s["role"] for s in steps or []}
		ok = [x for x in lg if x.action in ("Approved", "Final Approved")]
		# the step decides, not the person (some logins hold both CEO and MD); no rule: the logged role
		o.md_approved = any((step_role.get(x.step_order) if steps is not None else x.action_by_role) == "MD" for x in ok)
		fa = [x for x in lg if x.action == "Final Approved"]
		if fa:
			o.final_approved_at = get_datetime(fa[-1].action_date)
			o.final_approver_role = step_role.get(fa[-1].step_order) or fa[-1].action_by_role or None
		else:
			o.final_approved_at = get_datetime(o.approved_date) if o.approved_date else None
			o.final_approver_role = steps[-1]["role"] if steps and o.approved_date else None
		o.approved_date = get_datetime(o.approved_date) if o.approved_date else None
		o.category = o.category or default_cat
		o.company_currency = currency.get(o.company)
		o.creation, o.transaction_date = get_datetime(o.creation), getdate(o.transaction_date)
		o.self_skip_impossible = cint(o.self_skip_impossible)
		o.submitted_by_role = o.entered_role = next((r for r in ROLE_ORDER if r in held.get(o.submitted_by, ())), "Other")
		for k in NUMS:
			o[k] = flt(o[k])

	items = _in("""SELECT parent AS po, item_code, item_name, stock_qty, qty, base_rate, base_net_amount,
		material_request, material_request_item FROM `tabPurchase Order Item`
		WHERE parenttype = 'Purchase Order' AND parent IN %(n)s ORDER BY parent, idx""", orders)
	mrs = {r.name for r in ru.visible_docs("Material Request", "mr", {i.material_request for i in items if i.material_request}, docstatus=None)}
	mr_items = {r.pop("name"): r for r in _in("""SELECT name, parent AS material_request, item_code, stock_qty
		FROM `tabMaterial Request Item` WHERE parenttype = 'Material Request' AND name IN %(n)s""",
		{i.material_request_item for i in items if i.material_request in mrs}) if r.material_request in mrs}
	# (the line's own request must be visible too: a PO line can name a visible request and another one's line)

	receipts, item_receipts, seen = {}, {}, set()
	for r in _in(RECEIPTS, [n for n, o in orders.items() if o.docstatus == 1]):
		po = r.pop("po")
		r.posting = get_datetime(r.posting)
		receipts.setdefault(po, []).append(r)
		if not r.is_return and (r.doctype == "Purchase Receipt" or r.update_stock) and (r.item_code, r.doc, po) not in seen:
			seen.add((r.item_code, r.doc, po))
			item_receipts.setdefault(r.item_code, []).append({"posting": r.posting, "doc": r.doc, "po": po})
	gate_first = {r.po: get_datetime(r.t) for r in _in(GATE, receipts)}

	edits, codes = [], set(item_receipts)
	if codes:  # R42: allowance edits of items received on visible orders, plus Stock Settings
		va = {"a": from_date, "b": add_days(to_date, 2), "like": "%over_delivery_receipt_allowance%"}
		vs = _in(VERSIONS.format("docname IN %(n)s"), codes, dt="Item", **va)
		vs += frappe.db.sql(VERSIONS.format("1 = 1"), dict(va, dt="Stock Settings"), as_dict=True)
		for v in sorted(vs, key=lambda v: v.creation):
			try:
				changed = json.loads(v.data or "{}").get("changed") or []
			except (ValueError, AttributeError):
				continue
			for c in changed:
				if len(c) == 3 and c[0] == "over_delivery_receipt_allowance":
					edits.append({"item_code": v.docname if v.ref_doctype == "Item" else None,
						"at": get_datetime(v.creation), "old": flt(c[1]), "new": flt(c[2])})
	allowance_now = {r.name: flt(r.over_delivery_receipt_allowance) for r in _in(
		"SELECT name, over_delivery_receipt_allowance FROM `tabItem` WHERE name IN %(n)s", codes)}

	return {"today": now, "from_date": from_date, "to_date": to_date, "lines": lines, "orders": orders,
		"items": items, "mr_items": mr_items, "logs": logs, "rule_steps": rule_steps, "receipts": receipts,
		"gate_first": gate_first, "allowance_edits": edits, "allowance_now": allowance_now,
		"stock_allowance": flt(frappe.db.get_single_value("Stock Settings", "over_delivery_receipt_allowance")),
		"item_receipts": item_receipts}


def _money(v):
	return fmt_money(flt(v), 0, "INR")


def _qty(q):
	q = flt(q, 3)
	return str(int(q)) if q == int(q) else str(q)


def _columns():
	def c(fieldname, label, fieldtype="Data", width=120, options=None):
		return {"fieldname": fieldname, "label": _(label), "fieldtype": fieldtype, "options": options, "width": width}

	# Supplier is plain text: an MD-only login cannot read Supplier, and query_report.run refuses a report whose
	# Link column points at a doctype the user cannot read (spec 1: no Link the MD cannot open).
	return [c("level", "Level", width=90), c("checks", "Check", width=110), c("found", "What was found", width=420),
		c("supplier", "Supplier", width=160), c("company", "Company", width=150),
		c("total", "Total", "Currency"), c("over_by", "Over the line by", "Currency"),
		c("po", "Order", "Link", 170, "Purchase Order"), c("entry_time", "Entry time", "Datetime", 150),
		c("po_date", "PO date", width=170), c("value", "Value", "Currency"), c("items", "Items", width=240),
		c("request", "Request", width=160), c("entered_by", "Entered by (role)", width=140),
		c("approved", "Approved (role + time)", width=190), c("received", "Received", width=180)]


def _order_row(o, its, recs):
	got = {r.doc for r in recs if not r.is_return and (r.doctype == "Purchase Receipt" or r.update_stock)}
	value = sum(flt(r.base_net_amount) for r in recs if r.doctype == "Purchase Receipt" or r.update_stock)
	typed = "" if o.transaction_date == o.creation.date() else " " + _("(typed date differs)")
	names = ["{0} × {1}".format(i.item_name or i.item_code, _qty(i.qty)) for i in its[:3]]
	if len(its) > 3:
		names.append(_("+{0} more").format(len(its) - 3))
	when = o.final_approved_at and "{0} {1}".format(o.final_approver_role or "", o.final_approved_at.strftime("%d-%m-%Y %H:%M"))
	return {"indent": 1, "po": o.name, "supplier": o.supplier, "company": o.company, "entry_time": o.creation,
		"po_date": o.transaction_date.strftime("%d-%m-%Y") + typed, "value": o.base_grand_total,
		"items": "; ".join(names), "request": ", ".join(sorted({i.material_request for i in its if i.material_request})),
		"entered_by": o.entered_role, "approved": (when or o.approval_status or "").strip(),
		"received": _("{0} receipt(s), {1}").format(len(got), _money(value)) if got else _("Nothing received yet")}


def _rows(out, data):
	orders, items, rows = data["orders"], {}, []
	for i in data["items"]:
		items.setdefault(i.po, []).append(i)
	for p in out.get("points") or []:
		rows.append({"indent": 0, "level": p.get("level"), "checks": " + ".join(p.get("checks") or []),
			"found": p.get("summary"), "supplier": p.get("supplier"), "company": p.get("company"),
			"total": p.get("total"), "over_by": p.get("over_by") if p.get("line") is not None else None})
		rows += [_order_row(orders[n], items.get(n, []), data["receipts"].get(n, []))
			for n in p.get("orders") or [] if n in orders]
		texts = [p.get("tie")] + list(p.get("facts_against") or []) + list(p.get("extra_rows") or [])
		rows += [{"indent": 1, "found": t} for t in texts if t and t != rules.LAST_LINE]
		rows.append({"indent": 1, "found": rules.LAST_LINE})
	ranked = out.get("ranked") or []
	if ranked:
		rows.append({"indent": 0, "level": "Note", "checks": "R32", "found": _("Standing small orders (ranked)")})
	for r in ranked:  # orders is a count; d30/d90/fy are None for a catch-all code (never added up)
		sums = "" if r["d90"] is None else _("30 days {0} · 90 days {1} · year {2} · ").format(
			_money(r["d30"]), _money(r["d90"]), _money(r["fy"]))
		rows.append({"indent": 1, "checks": "R32", "total": r["d90"],
			"supplier": r["key"] if r["kind"] == "supplier" else None,
			"found": _("{0}: {1}{2} orders · largest {3}").format(r["label"], sums, cint(r["orders"]), _money(r["largest"]))
				+ "".join(" · " + m for m in r.get("marks") or [])})
	blank = dict.fromkeys(("total", "over_by", "value"))  # the desk shows a MISSING money key as "₹ 0.00", None as blank
	return [{k: _inert(v) for k, v in {**blank, **r}.items()} for r in rows] or [{"indent": 0, "found": _("Nothing found for these dates.")}]


def _inert(v):
	"""Excel/CSV export: a text cell starting with =, +, - or @ (also once Frappe strips HTML from it, which turns
	&#61; into =) would be a live formula; an apostrophe keeps it text, as in the Tally export (item and
	supplier names are typed by people)."""
	from frappe.utils.xlsxutils import ILLEGAL_CHARACTERS_RE, handle_html

	# the Excel writer also drops control characters, so a name starting "\x01=" would start with "=" there
	if isinstance(v, str) and any(ILLEGAL_CHARACTERS_RE.sub("", s).lstrip()[:1] in ("=", "+", "-", "@")
		for s in (v, handle_html(v))):
		return "'" + v
	return v
