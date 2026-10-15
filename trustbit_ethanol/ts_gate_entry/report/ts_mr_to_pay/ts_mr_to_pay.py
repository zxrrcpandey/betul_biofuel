# TS MR to Pay — client Google Sheet "V1.8 - 1", tab "MR to PAY" (BBPL report
# #8, 16 Oct 2026). Row 2 of the sheet carries the source mappings.
#
# ONE ROW PER SUBMITTED MR x DISTINCT DOC-LEVEL CHAIN PATH (po, pr, pi) — the
# ts_lead_time_analysis spine at MR-header grain (the sheet has no item
# columns):
#   MR -> PO  via `PO Item.material_request`
#   PO -> PR  via `PR Item.purchase_order`
#   PR -> PI  via `PI Item.purchase_receipt`
#   PO -> PI  via `PI Item.purchase_order`    — every invoice of the PO that no
#             receipt reaches renders as a (po, -, pi) row, so an invoice billed
#             straight from a received PO still shows (sheet: "PI against PO").
#             Unlike Lead Time Analysis, which keeps invoices PR-exclusive.
# An MR with no PO renders one row with blank chain columns. Return PIs (debit
# notes) are not chain legs — they already reduce the original invoice's
# outstanding_amount.
#
# Mappings (sheet row 2, plus the two unmapped columns approved 16 Oct):
#   Advance Amount         SUM(Payment Schedule.payment_amount) on PO rows whose
#                          payment_term contains 'advance' (Advance Payment Sheet rule)
#   Total Amount           PO rounded_total, else grand_total
#   Advance Payment Amount SUM of submitted 'Pay' Payment Entry allocations to the
#                          PO (same figure as TS Advance Payment Sheet / PR Wise)
#   Payment Paid Date      posting dates of those Payment Entries
#   PI Gross Amount        PI rounded_total, else grand_total — the figure
#                          outstanding_amount is measured against
#   Outstanding            PI outstanding_amount verbatim
#   Payment Request Date   submitted Payment Requests against the PO or PI;
#                          transaction_date, else the day it was raised
#                          (transaction_date is blank on most live requests)
#   Payment Done %         (PI gross - outstanding) / PI gross; on invoice-less
#                          rows, advance paid / PO total
#   UTR No                 distinct Payment Entry reference_no against the PO or PI
#   Over Due Date          PI due_date (a Payment Entry Reference row only copies
#                          it, and an unpaid invoice has no Payment Entry)
#
# Amounts repeat across the path rows of a PO / PI, so no amount column
# totals; the summary cards count each PO / PI once.
#
# Chain hops via report_utils only (Gotcha 17 / L409-412): the MR anchor and
# every leg carry their own conf + match clauses; an unreadable hop renders
# its leg blank. Only MR and PO are Link columns — every user who can open
# the report reads both, while PR / PI / Payment Entry are unreadable for some
# report roles and a Link column over them makes query_report's
# get_user_match_filters throw for that user.

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate, strip_html, today

from trustbit_ethanol.ts_gate_entry.report import report_utils as ru


ROW_LIMIT = 5000
SEP = ", "

MR_TYPES = ("Purchase & Service", "Purchase", "Service Request", "All")
PURCHASE_AND_SERVICE = ("Purchase", "Service Request")


def execute(filters=None):
	if not frappe.has_permission("Material Request", "read"):
		frappe.throw(_("Not permitted to read Material Request"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	rows, truncated = get_data(filters)
	return get_columns(), rows, None, None, _summary(rows, truncated)


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
	if filters.get("material_request_type") and filters["material_request_type"] not in MR_TYPES:
		frappe.throw(_("Invalid MR Type filter"))


def get_columns():
	def money(fieldname, label, cur, width=130):
		return {"fieldname": fieldname, "label": _(label), "fieldtype": "Currency",
		        "options": cur, "width": width, "disable_total": 1}

	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 60, "disable_total": 1},
		{"fieldname": "mr_no", "label": _("MR ID"), "fieldtype": "Link", "options": "Material Request", "width": 180},
		{"fieldname": "mr_date", "label": _("MR Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "mr_approved_date", "label": _("MR Approval Date"), "fieldtype": "Date", "width": 120},
		{"fieldname": "po_no", "label": _("PO ID"), "fieldtype": "Link", "options": "Purchase Order", "width": 170},
		{"fieldname": "po_date", "label": _("PO Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "po_approved_date", "label": _("PO Approval Date"), "fieldtype": "Date", "width": 120},
		{"fieldname": "pr_no", "label": _("PR ID"), "fieldtype": "Data", "width": 170},
		{"fieldname": "pr_date", "label": _("PR Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "pi_no", "label": _("PI ID"), "fieldtype": "Data", "width": 170},
		{"fieldname": "pi_date", "label": _("PI Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "bill_date", "label": _("Supplier Purchase Invoice Date"), "fieldtype": "Date", "width": 130},
		{"fieldname": "bill_no", "label": _("Supplier Purchase Invoice No."), "fieldtype": "Data", "width": 150},
		money("advance_amount", "Advance Amount", "currency"),
		money("total_amount", "Total Amount", "currency"),
		{"fieldname": "payment_request_date", "label": _("Payment Request Date"), "fieldtype": "Data", "width": 130},
		money("advance_paid", "Advance Payment Amount", "party_currency", 150),
		{"fieldname": "advance_paid_date", "label": _("Payment Paid Date"), "fieldtype": "Data", "width": 130},
		money("pi_gross", "PI Gross Amount", "pi_currency"),
		money("pi_outstanding", "Outstanding Amount in PI", "pi_currency", 150),
		{"fieldname": "paid_pct", "label": _("Payment Done %"), "fieldtype": "Percent", "width": 115, "disable_total": 1},
		{"fieldname": "utr_no", "label": _("UTR No"), "fieldtype": "Data", "width": 150},
		{"fieldname": "due_date", "label": _("Over Due Date"), "fieldtype": "Date", "width": 115},
	]


def _clauses(doctype, alias, extra=None):
	"""Submitted-only clause list for one hop, via the shared helper.
	Callers MUST gate on ru.hop_readable() first (report_utils rule 1)."""
	return [f"{alias}.docstatus = 1"] + ru.conf_match_clauses(doctype, alias) + (extra or [])


def get_data(filters):
	params = {}
	conds = _clauses("Material Request", "mr")
	if filters.get("from_date"):
		conds.append("mr.transaction_date >= %(from_date)s")
		params["from_date"] = getdate(filters["from_date"])
	if filters.get("to_date"):
		conds.append("mr.transaction_date <= %(to_date)s")
		params["to_date"] = getdate(filters["to_date"])
	if filters.get("material_request"):
		conds.append("mr.name = %(material_request)s")
		params["material_request"] = filters["material_request"]
	mr_type = filters.get("material_request_type") or MR_TYPES[0]
	if mr_type == MR_TYPES[0]:
		conds.append("mr.material_request_type IN %(mr_types)s")
		params["mr_types"] = PURCHASE_AND_SERVICE
	elif mr_type != "All":
		conds.append("mr.material_request_type = %(mr_type)s")
		params["mr_type"] = mr_type

	mrs = frappe.db.sql(
		f"""SELECT mr.name, mr.transaction_date, DATE(mr.ts_mr_approved_date) AS approved_date
		FROM `tabMaterial Request` mr
		WHERE {" AND ".join(conds)}
		ORDER BY mr.transaction_date DESC, mr.name""",
		params,
		as_dict=True,
	)

	po_of_mr, po_meta = _po_leg({m["name"] for m in mrs}, filters)
	pr_of_po, pr_meta = _pr_leg(set(po_meta))
	pi_of_pr, pi_of_po, pi_meta = _pi_legs(set(pr_meta), set(po_meta))
	advance_due = _advance_due(set(po_meta))
	pe_ok = ru.hop_readable("Payment Entry")
	pes = _payment_entries(set(po_meta), set(pi_meta)) if pe_ok else {}
	preqs = _payment_requests(set(po_meta), set(pi_meta))

	def paths_for(mr):
		pos = sorted(po_of_mr.get(mr, ()))
		if not pos:
			return [(None, None, None)]
		out = []
		for po in pos:
			prs = sorted(pr_of_po.get(po, ()))
			reached = set()
			for pr in prs:
				pis = sorted(pi_of_pr.get(pr, ()))
				reached.update(pis)
				out += [(po, pr, pi) for pi in pis] if pis else [(po, pr, None)]
			direct = sorted(set(pi_of_po.get(po, ())) - reached)
			out += [(po, None, pi) for pi in direct]
			if not prs and not direct:
				out.append((po, None, None))
		return out

	chained_only = bool(filters.get("supplier") or filters.get("purchase_order"))
	today_date = getdate(today())
	rows = []
	truncated = False
	for m in mrs:
		for po, pr, pi in paths_for(m["name"]):
			if chained_only and po is None:
				continue  # supplier / PO filter: only chains through those POs
			pom = po_meta.get(po) or {}
			prm = pr_meta.get(pr) or {}
			pim = pi_meta.get(pi) or {}
			adv_pes = pes.get(("Purchase Order", po), []) if po else []
			pi_pes = pes.get(("Purchase Invoice", pi), []) if pi else []
			advance_paid = sum(flt(p["allocated"]) for p in adv_pes) if adv_pes else None

			paid_pct = None
			if pi and flt(pim["gross"]):
				paid_pct = round((flt(pim["gross"]) - flt(pim["outstanding_amount"])) / flt(pim["gross"]) * 100, 2)
			elif po and not pi and pe_ok and flt(pom["total"]):
				paid_pct = round(flt(advance_paid) / flt(pom["total"]) * 100, 2)

			req_dates = sorted(preqs.get(("Purchase Order", po), set()) | preqs.get(("Purchase Invoice", pi), set()))
			utrs = []
			for p in adv_pes + pi_pes:
				utr = (p["reference_no"] or "").strip()
				if utr and utr not in utrs:
					utrs.append(utr)
			paid_dates = []
			for p in adv_pes:
				d = formatdate(p["posting_date"])
				if d not in paid_dates:
					paid_dates.append(d)

			rows.append({
				"mr_no": m["name"], "mr_date": m["transaction_date"],
				"mr_approved_date": m["approved_date"],
				"po_no": po, "po_date": pom.get("transaction_date"),
				"po_approved_date": pom.get("approved_date"),
				"pr_no": pr or "", "pr_date": prm.get("posting_date"),
				"pi_no": pi or "", "pi_date": pim.get("posting_date"),
				"bill_date": pim.get("bill_date"),
				"bill_no": " ".join(strip_html(pim["bill_no"]).split()) if pim.get("bill_no") else "",
				"currency": pom.get("currency"),
				"party_currency": pom.get("party_account_currency") or pom.get("currency"),
				"pi_currency": pim.get("currency"),
				"advance_amount": advance_due.get(po) if po else None,
				"total_amount": pom.get("total"),
				"payment_request_date": SEP.join(formatdate(d) for d in req_dates),
				"advance_paid": advance_paid,
				"advance_paid_date": SEP.join(paid_dates),
				"pi_gross": pim.get("gross"),
				"pi_outstanding": pim.get("outstanding_amount"),
				"paid_pct": paid_pct,
				"utr_no": SEP.join(utrs),
				"due_date": pim.get("due_date"),
				"is_overdue": 1 if (pi and pim.get("due_date") and getdate(pim["due_date"]) < today_date
				                    and flt(pim["outstanding_amount"]) > 0) else 0,
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


def _po_leg(mr_names, filters):
	"""mr -> [po names]; po -> meta. Submitted, visible POs only."""
	po_of_mr, meta = {}, {}
	if not mr_names or not ru.hop_readable("Purchase Order"):
		return po_of_mr, meta
	conds = _clauses("Purchase Order", "po", ["IFNULL(poi.material_request, '') != ''"])
	params = {}
	if filters.get("supplier"):
		conds.append("po.supplier = %(supplier)s")
		params["supplier"] = filters["supplier"]
	if filters.get("purchase_order"):
		conds.append("po.name = %(purchase_order)s")
		params["purchase_order"] = filters["purchase_order"]
	for chunk in ru.chunked(mr_names):
		for x in frappe.db.sql(
			f"""SELECT DISTINCT poi.material_request AS mr, po.name, po.transaction_date,
				DATE(po.ts_approved_date) AS approved_date, po.currency, po.party_account_currency,
				CASE WHEN IFNULL(po.rounded_total, 0) != 0 THEN po.rounded_total ELSE po.grand_total END AS total
			FROM `tabPurchase Order Item` poi
			JOIN `tabPurchase Order` po ON po.name = poi.parent
			WHERE poi.material_request IN %(names)s AND {" AND ".join(conds)}""",
			dict(params, names=chunk),
			as_dict=True,
		):
			po_of_mr.setdefault(x.pop("mr"), []).append(x["name"])
			meta[x["name"]] = x
	return po_of_mr, meta


def _pr_leg(po_names):
	"""po -> [pr names]; pr -> meta. Submitted, visible PRs only."""
	pr_of_po, meta = {}, {}
	if not po_names or not ru.hop_readable("Purchase Receipt"):
		return pr_of_po, meta
	conds = _clauses("Purchase Receipt", "pr", ["IFNULL(pri.purchase_order, '') != ''"])
	for chunk in ru.chunked(po_names):
		for x in frappe.db.sql(
			f"""SELECT DISTINCT pri.purchase_order AS po, pr.name, pr.posting_date
			FROM `tabPurchase Receipt Item` pri
			JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
			WHERE pri.purchase_order IN %(names)s AND {" AND ".join(conds)}""",
			{"names": chunk},
			as_dict=True,
		):
			pr_of_po.setdefault(x.pop("po"), []).append(x["name"])
			meta[x["name"]] = x
	return pr_of_po, meta


def _pi_legs(pr_names, po_names):
	"""Receipt-linked (pr -> [pi]) and PO-linked (po -> [pi]) invoice legs plus
	pi -> meta. Submitted, visible, non-return PIs only."""
	pi_of_pr, pi_of_po, meta = {}, {}, {}
	if not ru.hop_readable("Purchase Invoice"):
		return pi_of_pr, pi_of_po, meta
	cols = """pi.name, pi.posting_date, pi.bill_no, pi.bill_date, pi.due_date,
		pi.currency, pi.outstanding_amount,
		CASE WHEN IFNULL(pi.rounded_total, 0) != 0 THEN pi.rounded_total ELSE pi.grand_total END AS gross"""
	for link, names, target in (("purchase_receipt", pr_names, pi_of_pr),
	                            ("purchase_order", po_names, pi_of_po)):
		conds = _clauses("Purchase Invoice", "pi", ["pi.is_return = 0", f"IFNULL(pii.{link}, '') != ''"])
		for chunk in ru.chunked(names):
			for x in frappe.db.sql(
				f"""SELECT DISTINCT pii.{link} AS src, {cols}
				FROM `tabPurchase Invoice Item` pii
				JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
				WHERE pii.{link} IN %(names)s AND {" AND ".join(conds)}""",
				{"names": chunk},
				as_dict=True,
			):
				target.setdefault(x.pop("src"), []).append(x["name"])
				meta[x["name"]] = x
	return pi_of_pr, pi_of_po, meta


def _advance_due(po_names):
	"""po -> SUM(payment_amount) of its advance-labelled Payment Schedule rows
	(the TS Advance Payment Sheet rule). Keyed only by visible PO names."""
	out = {}
	for chunk in ru.chunked(po_names):
		for x in frappe.db.sql(
			"""SELECT parent, SUM(payment_amount) AS due FROM `tabPayment Schedule`
			WHERE parenttype = 'Purchase Order' AND parent IN %(names)s
			  AND LOWER(payment_term) LIKE '%%advance%%'
			GROUP BY parent""",
			{"names": chunk},
			as_dict=True,
		):
			out[x["parent"]] = flt(x["due"])
	return out


def _payment_entries(po_names, pi_names):
	"""(doctype, name) -> [per-PE dicts] for submitted 'Pay' Payment Entries,
	grouped PER PE first (ts_pr_wise_payment_approval pattern) so a PE with
	several reference rows to one document appears once. Callers gate on
	ru.hop_readable('Payment Entry')."""
	out = {}
	conds = ru.conf_match_clauses("Payment Entry", "pe")
	extra = (" AND " + " AND ".join(conds)) if conds else ""
	for dt, names in (("Purchase Order", po_names), ("Purchase Invoice", pi_names)):
		for chunk in ru.chunked(names):
			for x in frappe.db.sql(
				f"""SELECT per.reference_name AS ref, pe.name, pe.posting_date, pe.reference_no,
					SUM(per.allocated_amount) AS allocated
				FROM `tabPayment Entry Reference` per
				JOIN `tabPayment Entry` pe ON pe.name = per.parent
				WHERE per.reference_doctype = %(dt)s AND per.reference_name IN %(names)s
				  AND pe.docstatus = 1 AND pe.payment_type = 'Pay'{extra}
				GROUP BY per.reference_name, pe.name, pe.posting_date, pe.reference_no
				ORDER BY pe.posting_date, pe.name""",
				{"dt": dt, "names": chunk},
				as_dict=True,
			):
				out.setdefault((dt, x["ref"]), []).append(x)
	return out


def _payment_requests(po_names, pi_names):
	"""(doctype, name) -> {request dates} for submitted outward Payment
	Requests. Unreadable Payment Request hop -> {}."""
	out = {}
	if not ru.hop_readable("Payment Request"):
		return out
	conds = ["preq.docstatus = 1", "preq.payment_request_type = 'Outward'"]
	conds += ru.conf_match_clauses("Payment Request", "preq")
	for dt, names in (("Purchase Order", po_names), ("Purchase Invoice", pi_names)):
		for chunk in ru.chunked(names):
			for x in frappe.db.sql(
				f"""SELECT preq.reference_name AS ref,
					COALESCE(preq.transaction_date, DATE(preq.creation)) AS req_date
				FROM `tabPayment Request` preq
				WHERE preq.reference_doctype = %(dt)s AND preq.reference_name IN %(names)s
				  AND {" AND ".join(conds)}""",
				{"dt": dt, "names": chunk},
				as_dict=True,
			):
				out.setdefault((dt, x["ref"]), set()).add(x["req_date"])
	return out


def _summary(rows, truncated):
	mrs = {r["mr_no"] for r in rows}
	pos = {r["po_no"] for r in rows if r["po_no"]}
	pis = {}
	for r in rows:
		if r["pi_no"]:
			pis[r["pi_no"]] = r
	overdue = sum(1 for r in pis.values() if r["is_overdue"])

	out = []
	if truncated:
		out.append({"label": _("Result truncated"),
		            "value": _("first {0} rows — cards reflect shown rows only").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Orange"})
	out += [
		{"label": _("Material Requests"), "value": len(mrs), "datatype": "Int"},
		{"label": _("Purchase Orders"), "value": len(pos), "datatype": "Int"},
		{"label": _("Purchase Invoices"), "value": len(pis), "datatype": "Int"},
	]
	if len({r["pi_currency"] for r in pis.values()}) == 1:
		out.append({"label": _("PI Outstanding"), "datatype": "Currency",
		            "value": sum(flt(r["pi_outstanding"]) for r in pis.values()),
		            "currency": next(iter(pis.values()))["pi_currency"]})
	out.append({"label": _("Overdue Invoices"), "value": overdue, "datatype": "Int",
	            "indicator": "Red" if overdue else "Green"})
	return out
