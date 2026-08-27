# TS Tally Export — v2.43.0
# Script Report that previews ONE of the three sheets of the client's Tally
# import workbook (Pmt Receipt <- Payment Entry, JV <- Journal Entry,
# Sales <- Sales Invoice). The row-builders below are positional lists in the
# template's column order and are shared with api/ts_tally_export.py, so the
# on-screen grid and the exported .xlsx come from the same rows (make_xlsx still
# runs frappe's handle_html over string cells on the way into the workbook).
#
# Conventions agreed with the client (22 Aug 2026): voucher no. = ERPNext
# document name; dates as real date cells; ledger names = tabAccount.account_name
# (already without the company abbr) / party display names; multi-line JEs are
# exploded into Dr/Cr pairs that tie exactly (pair_legs); credit notes keep
# ERPNext's negative sign.
#
# Report JSON carries a "modified" key (Lesson 368). Columns are Data/Date/
# Currency/Float only — a Link column would gate the report on that doctype's
# read permission (Top Gotcha 18).

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate

SHEETS = {
	"Pmt Receipt": [("voucher type", "Data", 110), ("voucher date", "Date", 100),
		("voucher no.", "Data", 170), ("bank/cash", "Data", 230),
		("dr. amount", "Currency", 120), ("cr. Amount", "Currency", 120),
		("narration", "Data", 280), ("services ledger", "Data", 220)],
	"JV": [("voucher type", "Data", 110), ("voucher date", "Date", 100),
		("voucher no.", "Data", 170), ("cr. Ledger", "Data", 230),
		("dr. ledger", "Data", 230), ("amount", "Currency", 130),
		("narration", "Data", 280)],
	"Sales": [("voucher no.", "Data", 170), ("voucher type", "Data", 110),
		("voucher date", "Date", 100), ("particular", "Data", 220),
		("total invoice", "Currency", 130), ("sale ledger", "Data", 220),
		("item name", "Data", 220), ("item rate", "Currency", 110),
		("item amount", "Currency", 120), ("billed qty", "Float", 100),
		("cgst", "Currency", 100), ("sgst", "Currency", 100), ("igst", "Currency", 100)],
}
SHEET_ORDER = ["Pmt Receipt", "JV", "Sales"]
SHEET_DT = {"Pmt Receipt": "Payment Entry", "JV": "Journal Entry", "Sales": "Sales Invoice"}
ROW_LIMIT = 5000  # preview only; the export has its own cap

PMT_TYPE = {"Receive": "Receipt", "Pay": "Payment", "Internal Transfer": "Contra"}
JV_TYPE = {"Journal Entry": "Journal", "Debit Note": "Debit Note", "Credit Note": "Credit Note",
	"Contra Entry": "Contra", "Bank Entry": "Payment", "Cash Entry": "Payment"}
# Hardcoded allowlist: the table name is NEVER taken from the party_type value.
PARTY_NAME_FIELD = {"Supplier": "supplier_name", "Customer": "customer_name",
	"Employee": "employee_name", "Shareholder": "title"}
GST_COLS = ("taxable_value", "cgst_amount", "sgst_amount", "igst_amount")


def execute(filters=None):
	filters = filters or {}
	sheet = filters.get("sheet") or "JV"
	if sheet not in SHEETS:
		frappe.throw(_("Invalid sheet"))
	dt = SHEET_DT[sheet]
	# Builders use raw SQL (which bypasses permissions) -> one explicit read gate.
	if not frappe.has_permission(dt, "read"):
		frappe.throw(_("Not permitted to read {0}").format(dt), frappe.PermissionError)
	validate_filters(filters)
	rows, notes = BUILDERS[sheet](filters)
	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]
	return get_columns(sheet), rows, None, None, _summary(rows, notes, truncated)


def get_columns(sheet):
	return [{"fieldname": _slug(label), "label": _(label), "fieldtype": ftype, "width": width}
		for label, ftype, width in SHEETS[sheet]]


def headers(sheet):
	return [label for label, _t, _w in SHEETS[sheet]]


def widths(sheet):
	return [max(8, min(60, int(w / 7))) for _l, _t, w in SHEETS[sheet]]


def validate_filters(filters):
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are required"))
	# getdate() returns None for "0000-00-00"-style strings (and today for blank) -> guard both.
	start, end = getdate(filters["from_date"]), getdate(filters["to_date"])
	if not start or not end:
		frappe.throw(_("Invalid From Date or To Date"))
	if start > end:
		frappe.throw(_("From Date cannot be after To Date"))


def build_all(filters):
	"""All three sheets (used by the export endpoint). {sheet: (rows, notes)}."""
	validate_filters(filters)
	return {name: BUILDERS[name](filters) for name in SHEET_ORDER}


def _slug(label):
	return "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")


def _params(filters, alias):
	params = {"from_date": getdate(filters["from_date"]), "to_date": getdate(filters["to_date"])}
	cond = ""
	if filters.get("company"):
		cond = f" AND {alias}.company = %(company)s"
		params["company"] = filters["company"]
	return cond, params


def _acct_map(company=None):
	rows = frappe.get_all("Account", fields=["name", "account_name"],
		filters={"company": company} if company else None, limit=0)
	# Tally matches ledger names exactly -> strip stray whitespace from masters.
	return {r.name: (r.account_name or r.name).strip() for r in rows}


def _party_map(pairs):
	"""{(party_type, party): display name} — one query per allow-listed party type."""
	out = {}
	by_type = {}
	for pt, p in pairs:
		if pt in PARTY_NAME_FIELD and p:
			by_type.setdefault(pt, set()).add(p)
	for pt, names in by_type.items():
		field = PARTY_NAME_FIELD[pt]
		for r in frappe.get_all(pt, fields=["name", field], filters={"name": ("in", list(names))}, limit=0):
			out[(pt, r.name)] = (r.get(field) or r.name).strip()
	return out


def _narr(text, *fallbacks):
	for v in (text,) + fallbacks:
		if v and str(v).strip():
			return "; ".join(p.strip() for p in str(v).splitlines() if p.strip())
	return ""


# ---------------------------------------------------------------- Pmt Receipt
def _pmt_rows(filters):
	cond, params = _params(filters, "pe")
	pes = frappe.db.sql(f"""
		SELECT pe.name, pe.posting_date, pe.payment_type, pe.paid_from, pe.paid_to,
			pe.party_type, pe.party, pe.party_name, pe.base_paid_amount,
			pe.base_received_amount, pe.remarks, pe.reference_no
		FROM `tabPayment Entry` pe
		WHERE pe.docstatus = 1 AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s {cond}
		ORDER BY pe.posting_date, pe.name""", params, as_dict=True)
	acct = _acct_map(filters.get("company"))
	deds, taxes = {}, {}
	names = [p.name for p in pes]
	for i in range(0, len(names), 900):
		chunk = names[i:i + 900]
		for d in frappe.get_all("Payment Entry Deduction", fields=["parent", "account", "amount", "description"],
				filters={"parent": ("in", chunk), "amount": ("!=", 0)}, order_by="parent, idx", limit=0):
			deds.setdefault(d.parent, []).append(d)
		for t in frappe.get_all("Advance Taxes and Charges",
				fields=["parent", "account_head", "add_deduct_tax", "base_tax_amount", "included_in_paid_amount", "description"],
				filters={"parent": ("in", chunk), "parenttype": "Payment Entry", "base_tax_amount": ("!=", 0)},
				order_by="parent, idx", limit=0):
			taxes.setdefault(t.parent, []).append(t)
	rows, n_extra = [], 0
	for pe in pes:
		receive = pe.payment_type == "Receive"
		vtype = PMT_TYPE.get(pe.payment_type, "Payment")
		bank = pe.paid_to if receive else pe.paid_from
		counter = pe.paid_from if receive else pe.paid_to
		ledger = (pe.party_name or pe.party) if pe.party_type else (acct.get(counter) or counter)
		# Bank/cash leg as ERPNext posts it (payment_entry.py add_bank_gl_entries + add_tax_gl_entries):
		# a tax NOT included in the paid amount moves the bank leg — "Add" grows it, "Deduct" shrinks it.
		amount = flt(pe.base_received_amount) if receive else flt(pe.base_paid_amount)
		for t in taxes.get(pe.name, []):
			if not t.included_in_paid_amount:
				amount += flt(t.base_tax_amount) if t.add_deduct_tax == "Add" else -flt(t.base_tax_amount)
		rows.append([vtype, pe.posting_date, pe.name, acct.get(bank) or bank,
			amount if receive else 0, 0 if receive else amount, _narr(pe.remarks, pe.reference_no), ledger])
		for t in taxes.get(pe.name, []):
			# Tax ledger is debited on Pay+Add and Receive+Deduct, credited otherwise.
			amt, debit = flt(t.base_tax_amount), (t.add_deduct_tax == "Add") != receive
			rows.append([vtype, pe.posting_date, pe.name, "", amt if debit else 0, 0 if debit else amt,
				_narr(t.description, "Tax / Charge"), acct.get(t.account_head) or t.account_head])
			n_extra += 1
		for d in deds.get(pe.name, []):
			# payment_entry.py posts deductions as "debit": amount literally -> negative = credit.
			amt = flt(d.amount)
			rows.append([vtype, pe.posting_date, pe.name, "", amt if amt >= 0 else 0,
				0 if amt >= 0 else abs(amt), _narr(d.description, "Deduction"), acct.get(d.account) or d.account])
			n_extra += 1
	return rows, {"vouchers": len(pes), "extra_rows": n_extra}


# -------------------------------------------------------------------------- JV
def pair_legs(legs):
	"""Greedy sequential Dr x Cr pairing in idx order. Returns (rows, leftover) where each
	row is (cr_label, dr_label, amount); leftover is 0 for every balanced voucher."""
	drs = [[label, flt(leg.debit, 2)] for label, leg in legs if flt(leg.debit, 2) > 0]
	crs = [[label, flt(leg.credit, 2)] for label, leg in legs if flt(leg.credit, 2) > 0]
	out, i, j = [], 0, 0
	while i < len(drs) and j < len(crs):
		amt = flt(min(drs[i][1], crs[j][1]), 2)
		if amt <= 0:  # unreachable for 2-dp legs; guards the loop, remainder lands in leftover
			break
		out.append((crs[j][0], drs[i][0], amt))
		drs[i][1] = flt(drs[i][1] - amt, 2)
		crs[j][1] = flt(crs[j][1] - amt, 2)
		if drs[i][1] <= 0.004:
			i += 1
		if crs[j][1] <= 0.004:
			j += 1
	leftover = flt(sum(d[1] for d in drs[i:]) + sum(c[1] for c in crs[j:]), 2)
	return out, leftover


def _jv_rows(filters):
	cond, params = _params(filters, "je")
	legs = frappe.db.sql(f"""
		SELECT je.name, je.posting_date, je.voucher_type, je.user_remark, je.cheque_no,
			je.cheque_date, je.title, jea.idx, jea.account, jea.party_type, jea.party,
			jea.debit, jea.credit
		FROM `tabJournal Entry` je
		JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
		WHERE je.docstatus = 1 AND je.voucher_type != 'Opening Entry'
			AND je.posting_date BETWEEN %(from_date)s AND %(to_date)s {cond}
		ORDER BY je.posting_date, je.name, jea.idx""", params, as_dict=True)
	acct = _acct_map(filters.get("company"))
	party = _party_map({(leg.party_type, leg.party) for leg in legs if leg.party_type and leg.party})
	by_je, order = {}, []
	for leg in legs:
		if leg.name not in by_je:
			by_je[leg.name] = []
			order.append(leg.name)
		by_je[leg.name].append(leg)
	rows, unbalanced = [], 0
	for name in order:
		je = by_je[name][0]
		cheque = je.cheque_no and (je.cheque_no + (" dt " + formatdate(je.cheque_date) if je.cheque_date else ""))
		narration = _narr(je.user_remark, cheque, je.title)
		vtype = JV_TYPE.get(je.voucher_type, "Journal")
		labelled = [((party.get((leg.party_type, leg.party)) or leg.party) if leg.party_type and leg.party
			else (acct.get(leg.account) or leg.account or ""), leg) for leg in by_je[name]]
		pairs, leftover = pair_legs(labelled)
		if leftover:
			unbalanced += 1
		for cr, dr, amt in pairs:
			rows.append([vtype, je.posting_date, name, cr, dr, amt, narration])
	return rows, {"vouchers": len(order), "unbalanced": unbalanced}


# ----------------------------------------------------------------------- Sales
def _sales_rows(filters):
	cond, params = _params(filters, "si")
	# Column names are hardcoded literals; only their PRESENCE is checked (india_compliance
	# is optional — absent on the local bench, present on both servers).
	gst = [c for c in GST_COLS if frappe.db.has_column("Sales Invoice Item", c)]
	gst_sql = "".join(f", sii.{c}" for c in gst)
	items = frappe.db.sql(f"""
		SELECT si.name, si.posting_date, si.customer_name, si.is_return,
			CASE WHEN IFNULL(si.base_rounded_total, 0) != 0 THEN si.base_rounded_total
				ELSE si.base_grand_total END AS total_invoice,
			sii.item_name, sii.item_code, sii.qty, sii.base_net_rate, sii.base_net_amount,
			sii.income_account {gst_sql}
		FROM `tabSales Invoice` si
		JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE si.docstatus = 1 AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s {cond}
		ORDER BY si.posting_date, si.name, sii.idx""", params, as_dict=True)
	acct = _acct_map(filters.get("company"))
	rows, seen = [], set()
	for it in items:
		seen.add(it.name)
		taxable = flt(it.get("taxable_value"))
		rows.append([it.name, "Credit Note" if it.is_return else "Sales", it.posting_date,
			it.customer_name or "", flt(it.total_invoice), acct.get(it.income_account) or it.income_account or "",
			it.item_name or it.item_code or "", flt(it.base_net_rate),
			taxable if taxable != 0 else flt(it.base_net_amount), flt(it.qty),
			flt(it.get("cgst_amount")), flt(it.get("sgst_amount")), flt(it.get("igst_amount"))])
	return rows, {"vouchers": len(seen), "gst_columns": len(gst)}


BUILDERS = {"Pmt Receipt": _pmt_rows, "JV": _jv_rows, "Sales": _sales_rows}


def _summary(rows, notes, truncated):
	cards = [{"label": _("Vouchers"), "value": notes.get("vouchers", 0), "datatype": "Int"},
		{"label": _("Rows"), "value": len(rows), "datatype": "Int"}]
	if notes.get("extra_rows"):
		cards.append({"label": _("Tax / deduction rows"), "value": notes["extra_rows"], "datatype": "Int"})
	if notes.get("unbalanced"):
		cards.append({"label": _("Unbalanced vouchers"), "value": notes["unbalanced"],
			"datatype": "Int", "indicator": "Orange"})
	if truncated:
		cards.append({"label": _("Preview truncated at"), "value": ROW_LIMIT, "datatype": "Int", "indicator": "Orange"})
	return cards
