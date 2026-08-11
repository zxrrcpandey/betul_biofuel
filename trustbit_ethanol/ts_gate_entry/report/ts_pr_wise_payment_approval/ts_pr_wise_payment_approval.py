# TS PR Wise Payment Approval — BBPL Report Updated 11 Aug 2026.xlsx, sheet
# "PR Wise payment approval" (Wave 3 #10; plan: PLAN_bbpl_wave3_reports.md)
#
# ONE ROW PER SUBMITTED PO ITEM x LINKED SUBMITTED PI (doc-level DISTINCT
# pii.purchase_order -> parent PI pairs, LEFT-joined: PO items with no PI still
# render, PI columns blank). Client-answered mappings (11 Aug):
#   "total amount including GST" = PI.outstanding_amount  (col R)
#   "responcible person"         = MR creator, full name   (col S)
#
# ADVANCE LEG — per-PE pre-grouping (critic E5): Payment Entry Reference rows
# with reference_doctype='Purchase Order' are grouped PER PAYMENT ENTRY FIRST
# (name once, SUM(allocated_amount) for this PO, one posting_date, one
# reference_no) and only then concatenated — a PE carrying 2+ reference rows
# against the same PO appears once and the positional id/date/UTR lists stay
# aligned ('-' placeholder for a blank UTR). Separator is ', ' everywhere.
# Advance Paid = SUM of per-PE allocations to this PO — a DOCUMENTED DEVIATION
# from the sheet's 'PE paid_amount' wording: a PE split across documents would
# overstate, and PO.advance_paid is nonzero on only 5 POs.
#
# Confidentiality: PO anchor, PI leg and the MR-owner lookup each carry
# confidential_sql_clause + build_match_conditions (clause-list idiom); a hop
# doctype the user cannot read at all renders blank (v2.38.2 semantics).

import frappe
from frappe import _
from frappe.utils import flt, getdate, strip_html

from trustbit_ethanol.ts_gate_entry.report import report_utils as ru


ROW_LIMIT = 5000
IN_CHUNK = 1000
SEP = ", "

HAS_OPTS = ("", "Yes", "No")


def execute(filters=None):
	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("Not permitted to read Purchase Order"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	rows, truncated = get_data(filters)
	return get_columns(rows), rows, None, None, _summary(rows, truncated)


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
	for f in ("has_invoice", "has_advance"):
		if filters.get(f) and filters[f] not in HAS_OPTS:
			frappe.throw(_("Invalid {0} filter").format(f))


def get_columns(rows=None):
	currencies = {r.get("currency") for r in (rows or []) if r.get("currency")}
	mixed = len(currencies) > 1
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 60, "disable_total": 1},
		{"fieldname": "purchase_order", "label": _("PO ID"), "fieldtype": "Link", "options": "Purchase Order", "width": 170},
		{"fieldname": "purchase_invoice", "label": _("Purchase Invoice ID"), "fieldtype": "Data", "width": 165},
		{"fieldname": "pi_date", "label": _("PI Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "supplier_name", "label": _("Supplier Name"), "fieldtype": "Data", "width": 190},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "item_description", "label": _("Description"), "fieldtype": "Data", "width": 200},
		{"fieldname": "bill_no", "label": _("Supplier Tax Invoice No."), "fieldtype": "Data", "width": 150},
		{"fieldname": "bill_date", "label": _("Supplier Invoice Date"), "fieldtype": "Date", "width": 130},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 180},
		{"fieldname": "payment_condition", "label": _("PO Payment Condition"), "fieldtype": "Data", "width": 180},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Data", "width": 75},
		# header amount repeated per item row -> never totalled
		{"fieldname": "gross_total", "label": _("Gross Total Amount"), "fieldtype": "Currency", "options": "currency", "width": 145, "disable_total": 1},
		{"fieldname": "payment_ids", "label": _("Payment ID"), "fieldtype": "Data", "width": 170},
		{"fieldname": "payment_dates", "label": _("Payment Date"), "fieldtype": "Data", "width": 120},
		{"fieldname": "advance_paid", "label": _("Advance Paid Amount"), "fieldtype": "Currency", "options": "currency", "width": 150, "disable_total": 1},
		{"fieldname": "adv_utr", "label": _("Adv. UTR"), "fieldtype": "Data", "width": 140},
		{"fieldname": "pi_outstanding", "label": _("Total Amount Incl. GST (PI Outstanding)"), "fieldtype": "Currency", "options": "currency", "width": 200, "disable_total": 1 if mixed else 0},
		{"fieldname": "responsible_person", "label": _("Responsible Person"), "fieldtype": "Data", "width": 165},
	]


def get_data(filters):
	params = {}
	conds = ["po.docstatus = 1"] + ru.conf_match_clauses("Purchase Order", "po")
	if filters.get("from_date"):
		conds.append("po.transaction_date >= %(from_date)s")
		params["from_date"] = getdate(filters["from_date"])
	if filters.get("to_date"):
		conds.append("po.transaction_date <= %(to_date)s")
		params["to_date"] = getdate(filters["to_date"])
	if filters.get("purchase_order"):
		conds.append("po.name = %(purchase_order)s")
		params["purchase_order"] = filters["purchase_order"]
	if filters.get("supplier"):
		conds.append("po.supplier = %(supplier)s")
		params["supplier"] = filters["supplier"]
	if filters.get("cost_center"):
		conds.append("(po.cost_center = %(cost_center)s OR poi.cost_center = %(cost_center)s)")
		params["cost_center"] = filters["cost_center"]

	items = frappe.db.sql(
		f"""
		SELECT
			po.name AS purchase_order, po.supplier, po.supplier_name,
			po.cost_center, po.currency, po.payment_terms_template,
			CASE WHEN IFNULL(po.rounded_total, 0) != 0 THEN po.rounded_total ELSE po.grand_total END AS gross_total,
			poi.item_name, poi.item_code, poi.description AS item_description,
			poi.material_request, poi.idx AS item_idx
		FROM `tabPurchase Order Item` poi
		JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE {" AND ".join(conds)}
		ORDER BY po.transaction_date DESC, po.name, poi.idx
		""",
		params,
		as_dict=True,
	)

	po_names = {x["purchase_order"] for x in items}
	pi_of_po = _pi_legs(po_names)
	adv_of_po = _advance_legs(po_names)
	sched_of_po = _schedule_summaries(
		{x["purchase_order"] for x in items if not x.get("payment_terms_template")})
	names = _fullname_map(_mr_owner_map({x.get("material_request") for x in items}))

	has_pi = filters.get("has_invoice") or ""
	has_adv = filters.get("has_advance") or ""

	rows = []
	truncated = False
	for it in items:
		po = it["purchase_order"]
		pis = pi_of_po.get(po) or [None]
		adv = adv_of_po.get(po)
		if has_adv == "Yes" and not adv:
			continue
		if has_adv == "No" and adv:
			continue
		for pi in pis:
			if has_pi == "Yes" and not pi:
				continue
			if has_pi == "No" and pi:
				continue
			mr = it.get("material_request")
			rows.append({
				"purchase_order": po,
				"purchase_invoice": (pi or {}).get("name", "") if pi else "",
				"pi_date": (pi or {}).get("posting_date") if pi else None,
				"supplier_name": it.get("supplier_name") or it.get("supplier") or "",
				"item_name": " ".join(strip_html(it["item_name"]).split()) if it.get("item_name") else "",
				"item_code": it.get("item_code"),
				"item_description": " ".join(strip_html(it["item_description"]).split()) if it.get("item_description") else "",
				"bill_no": " ".join(strip_html(pi["bill_no"]).split()) if (pi and pi.get("bill_no")) else "",
				"bill_date": (pi or {}).get("bill_date") if pi else None,
				"cost_center": it.get("cost_center"),
				"payment_condition": it.get("payment_terms_template") or sched_of_po.get(po, ""),
				"currency": it.get("currency"),
				"gross_total": flt(it.get("gross_total")),
				"payment_ids": (adv or {}).get("ids", ""),
				"payment_dates": (adv or {}).get("dates", ""),
				"advance_paid": (adv or {}).get("amount") if adv else None,
				"adv_utr": (adv or {}).get("utrs", ""),
				"pi_outstanding": (pi or {}).get("outstanding_amount") if pi else None,
				"responsible_person": names.get(mr, "") if mr else "",
			})
			if len(rows) > ROW_LIMIT:
				truncated = True
				break
		if truncated:
			break
	if truncated:
		rows = rows[:ROW_LIMIT]
	for i, r in enumerate(rows, start=1):
		r["sr"] = i
	return rows, truncated


def _pi_legs(po_names):
	"""po -> [PI dicts], doc-level DISTINCT, submitted + visible only."""
	if not po_names or not ru.hop_readable("Purchase Invoice"):
		return {}
	conds = ["pi.docstatus = 1"] + ru.conf_match_clauses("Purchase Invoice", "pi")
	out = {}
	for chunk in ru.chunked(po_names):
		for x in frappe.db.sql(
			f"""SELECT DISTINCT pii.purchase_order AS po, pi.name, pi.posting_date,
				pi.bill_no, pi.bill_date, pi.outstanding_amount
			FROM `tabPurchase Invoice Item` pii
			JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
			WHERE pii.purchase_order IN %(names)s AND {" AND ".join(conds)}
			ORDER BY pi.posting_date, pi.name""",
			{"names": chunk},
			as_dict=True,
		):
			out.setdefault(x.pop("po"), []).append(x)
	return out


def _advance_legs(po_names):
	"""po -> {ids, dates, utrs, amount} — grouped PER PE first (one entry per
	Payment Entry even when it carries several reference rows for the PO),
	positionally aligned, '-' placeholder for a blank UTR."""
	if not po_names:
		return {}
	per_pe = {}
	for chunk in ru.chunked(po_names):
		for x in frappe.db.sql(
			"""SELECT per.reference_name AS po, pe.name, pe.posting_date,
				pe.reference_no, SUM(per.allocated_amount) AS allocated
			FROM `tabPayment Entry Reference` per
			JOIN `tabPayment Entry` pe ON pe.name = per.parent
			WHERE per.reference_doctype = 'Purchase Order'
			  AND per.reference_name IN %(names)s
			  AND pe.docstatus = 1 AND pe.payment_type = 'Pay'
			GROUP BY per.reference_name, pe.name, pe.posting_date, pe.reference_no
			ORDER BY pe.posting_date, pe.name""",
			{"names": chunk},
			as_dict=True,
		):
			per_pe.setdefault(x["po"], []).append(x)
	out = {}
	for po, pes in per_pe.items():
		out[po] = {
			"ids": SEP.join(p["name"] for p in pes),
			"dates": SEP.join(str(p["posting_date"]) for p in pes),
			"utrs": SEP.join((p["reference_no"] or "").strip() or "-" for p in pes),
			"amount": sum(flt(p["allocated"]) for p in pes),
		}
	return out


def _schedule_summaries(po_names):
	"""po -> 'Term 30%, Term 70%' summary from Payment Schedule rows, used when
	the PO has no payment_terms_template."""
	if not po_names:
		return {}
	out = {}
	for chunk in ru.chunked(po_names):
		for x in frappe.db.sql(
			"""SELECT parent, payment_term, invoice_portion FROM `tabPayment Schedule`
			WHERE parenttype = 'Purchase Order' AND parent IN %(names)s
			ORDER BY parent, idx""",
			{"names": chunk},
			as_dict=True,
		):
			label = (x["payment_term"] or "").strip()
			if not label:
				portion = flt(x["invoice_portion"])
				label = ("%g%%" % portion) if portion else ""
			if label:
				out.setdefault(x["parent"], []).append(label)
	return {k: SEP.join(v) for k, v in out.items()}


def _mr_owner_map(mr_names):
	mrs = {m for m in mr_names if m}
	if not mrs or not ru.hop_readable("Material Request"):
		return {}
	conds = ["mr.docstatus IN (0, 1)"] + ru.conf_match_clauses("Material Request", "mr")
	out = {}
	for chunk in ru.chunked(mrs):
		for x in frappe.db.sql(
			f"""SELECT mr.name, mr.owner FROM `tabMaterial Request` mr
			WHERE mr.name IN %(names)s AND {" AND ".join(conds)}""",
			{"names": chunk},
			as_dict=True,
		):
			out[x["name"]] = x["owner"]
	return out


def _fullname_map(mr_owner):
	"""mr_name -> owner's full name, one bulk User query (Lesson 168)."""
	ids = sorted({o for o in mr_owner.values() if o})
	if not ids:
		return {}
	names = {}
	for chunk in ru.chunked(ids):
		for x in frappe.db.sql(
			"SELECT name, full_name FROM `tabUser` WHERE name IN %(ids)s",
			{"ids": chunk},
			as_dict=True,
		):
			names[x["name"]] = x["full_name"] or x["name"]
	return {mr: names.get(owner, owner or "") for mr, owner in mr_owner.items()}


def _summary(rows, truncated):
	pos = {r["purchase_order"] for r in rows}
	pis = {r["purchase_invoice"] for r in rows if r["purchase_invoice"]}
	# advance: each PO counted once even across its item/PI fan-out
	adv_by_po = {}
	for r in rows:
		if r.get("advance_paid") is not None:
			adv_by_po[r["purchase_order"]] = flt(r["advance_paid"])
	out = []
	if truncated:
		out.append({"label": _("Result truncated"),
		            "value": _("first {0} rows — cards reflect shown rows only").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Orange"})
	out += [
		{"label": _("Purchase Orders"), "value": len(pos), "datatype": "Int"},
		{"label": _("Linked Invoices"), "value": len(pis), "datatype": "Int"},
		{"label": _("Advance Paid (distinct POs)"), "value": sum(adv_by_po.values()), "datatype": "Currency"},
		{"label": _("POs With Advance"), "value": len(adv_by_po), "datatype": "Int",
		 "indicator": "Blue" if adv_by_po else "Green"},
	]
	return out
