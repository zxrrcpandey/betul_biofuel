# TS Advance Payment Sheet — client Google Sheet "V1.8 - 2 / Advance Paymment
# sheet approval" (2 Oct 2026). Client layout: rows grouped under cost-centre
# headers with S No. | VENDOR | STATUS (e.g. "AD 100%") | AMOUNT | MATERIAL |
# LOCATION | RESPONSIBLE PERSON.
#
# ONE ROW PER QUALIFYING SUBMITTED PO, in two approved populations:
#   TERM ADVANCE    — the PO owns >=1 Payment Schedule row whose payment_term
#                     label contains 'advance' (case-insensitive; the only
#                     clean signal — invoice_portion<100 also catches release
#                     halves like 'Relies 50%', and due_date<=po date is true
#                     on 422/422 rows). STATUS = "AD <portion>%" per row
#                     (portion 0 falls back to payment_amount/grand_total),
#                     AMOUNT = SUM of those rows' payment_amount.
#   GENERAL ADVANCE — the PO has submitted Payment Entry allocations but NO
#                     advance-labelled row (the client's term-less case).
#                     STATUS = "General Advance"; AMOUNT = the paid amount
#                     (the only known quantum — flagged for UAT).
# PAID comes from Payment Entry Reference -> PO allocations grouped PER PE
# (the ts_pr_wise_payment_approval._advance_legs pattern) because
# Payment Schedule.paid_amount is 0 on every live row. Settled term advances
# (paid >= due) are SKIPPED by default per the client's note; the
# show_settled filter includes them.
#
# Grouping: cost centre -> supplier -> PO date, with an injected per-CC
# subtotal row (Material Issue Ledger consolidated-mode precedent); subtotal
# rows carry is_subtotal=1, no S No., and are styled bold by the JS.
#
# Chain hops via report_utils only (Gotcha 17 / L409-412): the PO anchor
# carries conf+match clauses; general-advance candidates resolve through
# ru.visible_docs; Payment Entry / PO Item / Material Request legs are
# hop-gated, the MR-creator hop conf-governed (the v2.38.6 _mr_owner_map
# class). RESPONSIBLE PERSON = the MR creator per the client's 11-Aug answer
# for PR-Wise — blank when the PO has no MR link (3 of 50 POs on demo).

import frappe
from frappe import _
from frappe.utils import flt, getdate, strip_html

from trustbit_ethanol.ts_gate_entry.report import report_utils as ru

ROW_LIMIT = 5000

ADVANCE_TYPES = ("", "All", "Term Advance", "General Advance")


def execute(filters=None):
	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("Not permitted to read Purchase Order"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	rows, truncated, totals = get_data(filters)
	return get_columns(), rows, None, None, _summary(rows, truncated, totals)


def _parse_date(val, label):
	try:
		return getdate(val)
	except Exception:
		frappe.throw(_("Invalid {0}").format(label))


def _validate_filters(filters):
	frm = _parse_date(filters["from_date"], "From Date") if filters.get("from_date") else None
	to = _parse_date(filters["to_date"], "To Date") if filters.get("to_date") else None
	if frm and to and frm > to:
		frappe.throw(_("From Date cannot be after To Date"))
	if filters.get("advance_type") and filters["advance_type"] not in ADVANCE_TYPES:
		frappe.throw(_("Invalid Advance Type"))


def get_columns():
	cur = "Company:company:default_currency"
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 60, "disable_total": 1},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Data", "width": 210},
		{"fieldname": "po_no", "label": _("P.O. No."), "fieldtype": "Link", "options": "Purchase Order", "width": 160},
		{"fieldname": "po_date", "label": _("PO Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "vendor", "label": _("Vendor"), "fieldtype": "Data", "width": 190},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 120},
		{"fieldname": "amount", "label": _("Advance Amount"), "fieldtype": "Currency", "options": cur, "width": 130},
		{"fieldname": "paid_amount", "label": _("Paid Amount"), "fieldtype": "Currency", "options": cur, "width": 125},
		{"fieldname": "balance", "label": _("Balance"), "fieldtype": "Currency", "options": cur, "width": 120},
		{"fieldname": "material", "label": _("Material"), "fieldtype": "Data", "width": 260},
		{"fieldname": "location", "label": _("Location"), "fieldtype": "Data", "width": 180},
		{"fieldname": "responsible_person", "label": _("Responsible Person"), "fieldtype": "Data", "width": 150},
		{"fieldname": "po_total", "label": _("PO Amount"), "fieldtype": "Currency", "options": cur, "width": 125, "disable_total": 1},
	]


def get_data(filters):
	term = _term_advances(filters)
	general = _general_advances(filters, exclude=set(term))

	all_pos = list(term) + list(general)
	paid = _paid_map(all_pos)

	# the client's note: already-paid (settled) term advances are skipped
	if not filters.get("show_settled"):
		term = {
			po: d for po, d in term.items()
			if not (flt(d["due"]) and flt(paid.get(po, 0)) >= flt(d["due"]) - 0.005)
		}

	atype = filters.get("advance_type") or "All"
	if atype == "Term Advance":
		general = {}
	elif atype == "General Advance":
		term = {}

	pos = list(term) + list(general)
	mat, loc = _items(pos)
	resp = _responsible(pos)

	raw = []
	for po, d in term.items():
		raw.append(_row(po, d, d["due"], paid.get(po), mat, loc, resp))
	for po, d in general.items():
		# AMOUNT = the paid amount: a general advance has no term to owe against
		raw.append(_row(po, d, paid.get(po), paid.get(po), mat, loc, resp, general=True))

	raw.sort(key=lambda r: (r["cost_center"] or "~", r["vendor"] or "~",
	                        str(r["po_date"] or ""), r["po_no"]))

	truncated = len(raw) > ROW_LIMIT
	raw = raw[:ROW_LIMIT]

	rows, sr = [], 0
	totals = {"due": 0.0, "paid": 0.0, "general": 0}
	group, gsum = None, None
	for r in raw:
		if r["cost_center"] != group:
			if gsum:
				rows.append(gsum)
			group = r["cost_center"]
			gsum = {
				"is_subtotal": 1, "sr": None, "po_no": "", "po_date": None,
				"cost_center": _("Total — {0}").format(group or _("No Cost Center")),
				"vendor": "", "status": "", "material": "", "location": "",
				"responsible_person": "", "po_total": None,
				"amount": 0.0, "paid_amount": 0.0, "balance": 0.0,
			}
		sr += 1
		r["sr"] = sr
		rows.append(r)
		for k in ("amount", "paid_amount", "balance"):
			gsum[k] = flt(gsum[k]) + flt(r[k])
		totals["due"] += flt(r["amount"])
		totals["paid"] += flt(r["paid_amount"])
		if r["is_general"]:
			totals["general"] += 1
	if gsum:
		rows.append(gsum)
	return rows, truncated, totals


def _row(po, d, amount, paid_amt, mat, loc, resp, general=False):
	amount = flt(amount) if amount is not None else None
	paid_amt = flt(paid_amt) if paid_amt is not None else None
	balance = None
	if not general and amount is not None:
		balance = amount - flt(paid_amt)
	return {
		"is_subtotal": 0,
		"is_general": 1 if general else 0,
		"cost_center": d["cost_center"] or "",
		"po_no": po,
		"po_date": d["transaction_date"],
		"vendor": " ".join(strip_html(d["supplier_name"] or "").split()),
		"status": _("General Advance") if general else d["status"],
		"amount": amount,
		"paid_amount": paid_amt,
		"balance": balance,
		"material": mat.get(po, ""),
		"location": loc.get(po, ""),
		"responsible_person": resp.get(po, ""),
		"po_total": d["grand_total"],
	}


def _term_advances(filters):
	"""po -> {status, due, supplier_name, cost_center, transaction_date,
	grand_total}. Anchor query: the PO is the governed base — conf + match
	clauses on its alias."""
	params = {}
	conds = ["po.docstatus = 1", "LOWER(ps.payment_term) LIKE '%%advance%%'"]
	conds += ru.conf_match_clauses("Purchase Order", "po")
	if filters.get("from_date"):
		conds.append("po.transaction_date >= %(from_date)s")
		params["from_date"] = getdate(filters["from_date"])
	if filters.get("to_date"):
		conds.append("po.transaction_date <= %(to_date)s")
		params["to_date"] = getdate(filters["to_date"])
	if filters.get("cost_center"):
		conds.append("po.cost_center = %(cost_center)s")
		params["cost_center"] = filters["cost_center"]
	if filters.get("supplier"):
		conds.append("po.supplier = %(supplier)s")
		params["supplier"] = filters["supplier"]

	out = {}
	for x in frappe.db.sql(
		f"""SELECT po.name, po.supplier_name, po.cost_center, po.transaction_date,
			po.grand_total, ps.payment_term, ps.invoice_portion, ps.payment_amount
		FROM `tabPayment Schedule` ps
		JOIN `tabPurchase Order` po ON po.name = ps.parent AND ps.parenttype = 'Purchase Order'
		WHERE {" AND ".join(conds)}
		ORDER BY po.name, ps.idx""",
		params, as_dict=True,
	):
		d = out.setdefault(x["name"], {
			"supplier_name": x["supplier_name"], "cost_center": x["cost_center"],
			"transaction_date": x["transaction_date"], "grand_total": x["grand_total"],
			"due": 0.0, "_statuses": [],
		})
		d["due"] += flt(x["payment_amount"])
		portion = flt(x["invoice_portion"])
		if not portion and flt(x["grand_total"]):
			portion = round(flt(x["payment_amount"]) / flt(x["grand_total"]) * 100, 1)
		d["_statuses"].append("AD %g%%" % portion)
	for d in out.values():
		d["status"] = ", ".join(d.pop("_statuses"))
	return out


def _general_advances(filters, exclude):
	"""po -> meta for POs with submitted PE allocations but no advance-labelled
	schedule row. Candidates come from the PE leg (hop-gated), then resolve
	through ru.visible_docs so confidentiality + match govern the PO itself;
	the date/CC/supplier filters apply to the resolved metadata."""
	if not ru.hop_readable("Payment Entry"):
		return {}
	cands = {x[0] for x in frappe.db.sql(
		"""SELECT DISTINCT per.reference_name
		FROM `tabPayment Entry Reference` per
		JOIN `tabPayment Entry` pe ON pe.name = per.parent
		WHERE per.reference_doctype = 'Purchase Order'
		  AND pe.docstatus = 1 AND pe.payment_type = 'Pay'"""
	)} - set(exclude)
	if not cands:
		return {}

	out = {}
	frm = getdate(filters["from_date"]) if filters.get("from_date") else None
	to = getdate(filters["to_date"]) if filters.get("to_date") else None
	for x in ru.visible_docs(
		"Purchase Order", "po", cands,
		extra_cols=", po.supplier, po.supplier_name, po.cost_center, po.transaction_date, po.grand_total",
	):
		if frm and x["transaction_date"] and getdate(x["transaction_date"]) < frm:
			continue
		if to and x["transaction_date"] and getdate(x["transaction_date"]) > to:
			continue
		if filters.get("cost_center") and x["cost_center"] != filters["cost_center"]:
			continue
		if filters.get("supplier") and x["supplier"] != filters["supplier"]:
			continue
		out[x["name"]] = {
			"supplier_name": x["supplier_name"], "cost_center": x["cost_center"],
			"transaction_date": x["transaction_date"], "grand_total": x["grand_total"],
			"due": None, "status": "",
		}
	return out


def _paid_map(po_names):
	"""po -> SUM of per-PE allocated amounts (grouped per PE first, the
	ts_pr_wise_payment_approval pattern). None-safe: unreadable PE hop or no
	allocations -> the PO simply has no paid figure."""
	if not po_names or not ru.hop_readable("Payment Entry"):
		return {}
	out = {}
	for chunk in ru.chunked(po_names):
		for x in frappe.db.sql(
			"""SELECT per.reference_name AS po, pe.name, SUM(per.allocated_amount) AS allocated
			FROM `tabPayment Entry Reference` per
			JOIN `tabPayment Entry` pe ON pe.name = per.parent
			WHERE per.reference_doctype = 'Purchase Order'
			  AND per.reference_name IN %(names)s
			  AND pe.docstatus = 1 AND pe.payment_type = 'Pay'
			GROUP BY per.reference_name, pe.name""",
			{"names": chunk}, as_dict=True,
		):
			out[x["po"]] = flt(out.get(x["po"])) + flt(x["allocated"])
	return out


def _items(po_names):
	"""po -> comma-joined distinct item names; po -> distinct delivery
	locations. Keyed only by already-visible PO names."""
	if not po_names:
		return {}, {}
	mat, loc = {}, {}
	for chunk in ru.chunked(po_names):
		for x in frappe.db.sql(
			"""SELECT parent, item_name, ts_delivery_location
			FROM `tabPurchase Order Item` WHERE parent IN %(names)s
			ORDER BY parent, idx""",
			{"names": chunk}, as_dict=True,
		):
			if x["item_name"]:
				mat.setdefault(x["parent"], [])
				v = " ".join(strip_html(x["item_name"]).split())
				if v and v not in mat[x["parent"]]:
					mat[x["parent"]].append(v)
			if x["ts_delivery_location"]:
				loc.setdefault(x["parent"], set()).add(
					" ".join(strip_html(x["ts_delivery_location"]).split()))
	return ({k: ", ".join(v) for k, v in mat.items()},
	        {k: ", ".join(sorted(v)) for k, v in loc.items()})


def _responsible(po_names):
	"""po -> MR creator full name (client's 11-Aug answer for PR-Wise:
	responsible person = MR creator). MR hop is confidentiality-governed —
	a hidden MR contributes nothing (the v2.38.6 _mr_owner_map class)."""
	if not po_names:
		return {}
	mr_of_po = {}
	for chunk in ru.chunked(po_names):
		for x in frappe.db.sql(
			"""SELECT DISTINCT parent, material_request
			FROM `tabPurchase Order Item`
			WHERE parent IN %(names)s AND IFNULL(material_request, '') != ''""",
			{"names": chunk}, as_dict=True,
		):
			mr_of_po.setdefault(x["parent"], []).append(x["material_request"])

	mr_names = {m for v in mr_of_po.values() for m in v}
	owner_of_mr = {x["name"]: x["owner"] for x in ru.visible_docs(
		"Material Request", "mr", mr_names, extra_cols=", mr.owner", docstatus=(0, 1))}
	fullnames = ru.fullname_map(set(owner_of_mr.values()))

	out = {}
	for po, mrs in mr_of_po.items():
		names = []
		for m in mrs:
			owner = owner_of_mr.get(m)
			fn = fullnames.get(owner, "") if owner else ""
			if fn and fn not in names:
				names.append(fn)
		if names:
			out[po] = ", ".join(names)
	return out


def _summary(rows, truncated, totals):
	out = []
	if truncated:
		out.append({"label": _("Result truncated"),
		            "value": _("first {0} rows — cards reflect shown rows only").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Orange"})
	data_rows = [r for r in rows if not r.get("is_subtotal")]
	outstanding = sum(flt(r["balance"]) for r in data_rows if r.get("balance") is not None)
	out += [
		{"label": _("Advance POs"), "value": len(data_rows), "datatype": "Int"},
		{"label": _("Advance Due"), "value": totals["due"], "datatype": "Currency"},
		{"label": _("Paid"), "value": totals["paid"], "datatype": "Currency"},
		{"label": _("Outstanding"), "value": outstanding, "datatype": "Currency",
		 "indicator": "Orange" if outstanding else "Green"},
		{"label": _("General Advances"), "value": totals["general"], "datatype": "Int",
		 "indicator": "Blue"},
	]
	return out
