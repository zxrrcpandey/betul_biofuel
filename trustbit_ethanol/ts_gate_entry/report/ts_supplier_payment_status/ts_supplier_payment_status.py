# TS Supplier Payment Status — BBPL Report Updated 11 Aug 2026.xlsx, sheet
# "Supplier Payment Status Report" (Wave 3 #11; plan: PLAN_bbpl_wave3_reports.md)
#
# ONE ROW PER SUBMITTED PURCHASE INVOICE. Chain walked BACKWARD:
#   PI -> PR  via `PI Item.purchase_receipt`
#   PI -> PO  via `PI Item.purchase_order`
#   PO -> MR  via `PO Item.material_request`   (doc-level; line-level
#             material_request_item is unreliable and is never read)
# Backlinks are aggregated INTO the row as comma-joined sorted-distinct sets,
# so the row count always equals the caller-visible submitted-PI count — never
# inflated by fan-out, never deflated by missing legs. 34 of 95 PIs have no PO
# link at all; they render with blank chain cells by design.
#
# Client-answered mappings (11 Aug): Outstanding Amount = PI.outstanding_amount
# verbatim; Invoice Amount = PI.grand_total (document currency); Advance Paid =
# ERPNext-maintained PO.advance_paid summed over the row's DISTINCT visible POs
# (dict first, then sum — never SQL SUM over the item fan-out). advance_paid is
# denominated in the PO's PARTY ACCOUNT currency (ERPNext schema), so the value
# is blanked (None) when any linked PO's party_account_currency differs from
# the PI currency rather than summing mixed denominations.
#
# Confidentiality: every raw-SQL hop over PI/PO/PR/MR carries BOTH
# confidential_sql_clause (Lesson 297; the helper returns '' for allow-listed
# users, else hides rows unless owner = user) AND build_match_conditions
# alias-repointed. A chain leg the caller may not see renders blank — it is
# indistinguishable from an absent leg (no existence oracle).

import frappe
from frappe import _
from frappe.utils import flt, getdate, strip_html

from trustbit_ethanol.ts_gate_entry.report import report_utils as ru


ROW_LIMIT = 5000
IN_CHUNK = 1000

# ERPNext-maintained PI statuses possible at docstatus=1 (incl. Submitted and
# Internal Transfer — the enum is wider than the common six).
PI_STATUSES = (
	"Paid", "Partly Paid", "Unpaid", "Overdue", "Return",
	"Debit Note Issued", "Submitted", "Internal Transfer",
)


def execute(filters=None):
	if not frappe.has_permission("Purchase Invoice", "read"):
		frappe.throw(_("Not permitted to read Purchase Invoice"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	rows, truncated = get_data(filters)
	columns = get_columns(rows)
	summary, chart = _summary_and_chart(rows, truncated)
	return columns, rows, None, chart, summary


def _validate_filters(filters):
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are required"))
	if getdate(filters["from_date"]) > getdate(filters["to_date"]):
		frappe.throw(_("From Date cannot be after To Date"))
	if filters.get("company") and not frappe.db.exists("Company", filters["company"]):
		frappe.throw(_("Invalid Company"))
	if filters.get("payment_status") and filters["payment_status"] not in PI_STATUSES:
		frappe.throw(_("Invalid Payment Status"))


def get_columns(rows=None):
	# Currency totals are honest only within one currency: enable them when the
	# result carries exactly one distinct currency, disable when mixed.
	currencies = {r.get("currency") for r in (rows or []) if r.get("currency")}
	mixed = len(currencies) > 1
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 60, "disable_total": 1},
		{"fieldname": "mr_no", "label": _("MR No"), "fieldtype": "Data", "width": 170},
		{"fieldname": "po_no", "label": _("PO No"), "fieldtype": "Data", "width": 170},
		{"fieldname": "pr_no", "label": _("PR No"), "fieldtype": "Data", "width": 170},
		{"fieldname": "pi_no", "label": _("PI No"), "fieldtype": "Link", "options": "Purchase Invoice", "width": 180},
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Data", "width": 200},
		{"fieldname": "bill_no", "label": _("Supp Invoice No"), "fieldtype": "Data", "width": 140},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Data", "width": 80},
		{"fieldname": "invoice_amount", "label": _("Invoice Amount"), "fieldtype": "Currency", "options": "currency", "width": 135, "disable_total": 1 if mixed else 0},
		# PO-header amount repeated when 2+ PIs share a PO -> never totalled.
		{"fieldname": "advance_paid", "label": _("Advance Paid Amount"), "fieldtype": "Currency", "options": "currency", "width": 150, "disable_total": 1},
		{"fieldname": "outstanding_amount", "label": _("Outstanding Amount"), "fieldtype": "Currency", "options": "currency", "width": 150, "disable_total": 1 if mixed else 0},
		{"fieldname": "pi_status", "label": _("Payment Status"), "fieldtype": "Data", "width": 110},
	]


def get_data(filters):
	params = {
		"from_date": getdate(filters["from_date"]),
		"to_date": getdate(filters["to_date"]),
		"limit": ROW_LIMIT + 1,
	}
	clauses = [
		"pi.docstatus = 1",
		"pi.posting_date BETWEEN %(from_date)s AND %(to_date)s",
	] + ru.conf_match_clauses("Purchase Invoice", "pi")

	if filters.get("company"):
		clauses.append("pi.company = %(company)s")
		params["company"] = filters["company"]
	if filters.get("supplier"):
		clauses.append("pi.supplier = %(supplier)s")
		params["supplier"] = filters["supplier"]
	if filters.get("payment_status"):
		clauses.append("pi.status = %(payment_status)s")
		params["payment_status"] = filters["payment_status"]
	if filters.get("outstanding_only"):
		clauses.append("ABS(pi.outstanding_amount) > 0.005")
	if filters.get("purchase_order"):
		clauses.append(
			"EXISTS (SELECT 1 FROM `tabPurchase Invoice Item` x"
			" WHERE x.parent = pi.name AND x.purchase_order = %(purchase_order)s)"
		)
		params["purchase_order"] = filters["purchase_order"]

	rows = frappe.db.sql(
		f"""
		SELECT
			pi.name               AS pi_no,
			pi.supplier           AS supplier_id,
			pi.bill_no            AS bill_no,
			pi.currency           AS currency,
			pi.grand_total        AS invoice_amount,
			pi.outstanding_amount AS outstanding_amount,
			pi.status             AS pi_status
		FROM `tabPurchase Invoice` pi
		WHERE {" AND ".join(clauses)}
		ORDER BY pi.supplier, pi.posting_date DESC, pi.name DESC
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)
	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]

	_attach_chain(rows)
	_attach_supplier_names(rows)

	for i, r in enumerate(rows, start=1):
		r["sr"] = i
		if r.get("bill_no"):
			r["bill_no"] = " ".join(strip_html(r["bill_no"]).split())
	return rows, truncated


def _attach_chain(rows):
	"""Backward chain: PI items -> PO/PR sets -> visible legs -> MR set."""
	if not rows:
		return
	pi_names = [r["pi_no"] for r in rows]

	po_of_pi, pr_of_pi = {}, {}
	for chunk in ru.chunked(pi_names):
		for x in frappe.db.sql(
			"""SELECT parent, purchase_order, purchase_receipt
			FROM `tabPurchase Invoice Item`
			WHERE parent IN %(names)s
			  AND (IFNULL(purchase_order, '') != '' OR IFNULL(purchase_receipt, '') != '')""",
			{"names": chunk},
			as_dict=True,
		):
			if x.purchase_order:
				po_of_pi.setdefault(x.parent, set()).add(x.purchase_order)
			if x.purchase_receipt:
				pr_of_pi.setdefault(x.parent, set()).add(x.purchase_receipt)

	all_pos = set().union(*po_of_pi.values()) if po_of_pi else set()
	all_prs = set().union(*pr_of_pi.values()) if pr_of_pi else set()

	# advance_paid is denominated in the PO's PARTY ACCOUNT currency.
	po_rows = ru.visible_docs("Purchase Order", "po", all_pos,
	                        extra_cols=", po.advance_paid, po.party_account_currency")
	visible_po = {x["name"]: x for x in po_rows}
	visible_pr = {x["name"] for x in ru.visible_docs("Purchase Receipt", "pr", all_prs)}

	# PO -> MR, doc-level, over the VISIBLE POs only.
	mr_of_po = {}
	if visible_po:
		for chunk in ru.chunked(visible_po.keys()):
			for x in frappe.db.sql(
				"""SELECT poi.parent AS po, poi.material_request AS mr
				FROM `tabPurchase Order Item` poi
				WHERE poi.parent IN %(names)s AND IFNULL(poi.material_request, '') != ''
				GROUP BY poi.parent, poi.material_request""",
				{"names": chunk},
				as_dict=True,
			):
				mr_of_po.setdefault(x.po, set()).add(x.mr)
	all_mrs = set().union(*mr_of_po.values()) if mr_of_po else set()
	visible_mr = {x["name"] for x in ru.visible_docs("Material Request", "mr", all_mrs)}

	for r in rows:
		pos = sorted(p for p in po_of_pi.get(r["pi_no"], ()) if p in visible_po)
		prs = sorted(p for p in pr_of_pi.get(r["pi_no"], ()) if p in visible_pr)
		mrs = sorted({m for p in pos for m in mr_of_po.get(p, ()) if m in visible_mr})
		r["po_no"] = ", ".join(pos)
		r["pr_no"] = ", ".join(prs)
		r["mr_no"] = ", ".join(mrs)
		# Advance: dedup by PO name (dict) BEFORE summing; blank on party-account
		# currency mismatch rather than summing mixed denominations.
		if not pos:
			r["advance_paid"] = None
		else:
			legs = [visible_po[p] for p in pos]
			if any((l.get("party_account_currency") or r.get("currency")) != r.get("currency") for l in legs):
				r["advance_paid"] = None
			else:
				r["advance_paid"] = sum(flt(l.get("advance_paid")) for l in legs)
		r["_po_set"] = pos  # for the distinct-PO advance card


def _attach_supplier_names(rows):
	"""Supplier display names, one bulk query. Data column, not Link — a
	Link -> Supplier column would gate the whole report on Supplier read
	(Lesson 168 class / get_linked_doctypes)."""
	ids = sorted({r.get("supplier_id") for r in rows if r.get("supplier_id")})
	if not ids:
		for r in rows:
			r["supplier"] = ""
		return
	names = {}
	for chunk in ru.chunked(ids):
		for x in frappe.db.sql(
			"SELECT name, supplier_name FROM `tabSupplier` WHERE name IN %(ids)s",
			{"ids": chunk},
			as_dict=True,
		):
			names[x["name"]] = x["supplier_name"] or x["name"]
	for r in rows:
		r["supplier"] = names.get(r.pop("supplier_id", None) or "", "")


def _summary_and_chart(rows, truncated):
	"""Cards + chart computed from the RETURNED (post-truncation) rows; the
	truncation notice discloses that totals are partial."""
	currencies = {r.get("currency") for r in rows if r.get("currency")}
	mixed = len(currencies) > 1

	invoice_total = sum(flt(r.get("invoice_amount")) for r in rows)
	outstanding_total = sum(flt(r.get("outstanding_amount")) for r in rows)
	# each PO counted once even when shared by several PIs
	po_advance = {}
	for r in rows:
		if r.get("advance_paid") is None:
			continue
		for p in r.get("_po_set") or ():
			po_advance.setdefault(p, 0.0)
	if po_advance:
		for chunk in ru.chunked(po_advance.keys()):
			for x in frappe.db.sql(
				"SELECT name, advance_paid FROM `tabPurchase Order` WHERE name IN %(n)s",
				{"n": chunk}, as_dict=True,
			):
				po_advance[x["name"]] = flt(x["advance_paid"])
	advance_total = sum(po_advance.values())
	for r in rows:
		r.pop("_po_set", None)

	summary = []
	if truncated:
		summary.append({"label": _("Result truncated"),
		                "value": _("first {0} rows — totals reflect shown rows only").format(ROW_LIMIT),
		                "datatype": "Data", "indicator": "Orange"})
	summary.append({"label": _("Invoices"), "value": len(rows), "datatype": "Int"})
	if mixed:
		summary.append({"label": _("Invoice Total (mixed currency)"), "value": "—", "datatype": "Data"})
		summary.append({"label": _("Outstanding (mixed currency)"), "value": "—", "datatype": "Data"})
	else:
		summary.append({"label": _("Invoice Total"), "value": invoice_total, "datatype": "Currency"})
		summary.append({"label": _("Outstanding"), "value": outstanding_total, "datatype": "Currency",
		                "indicator": "Red" if outstanding_total > 0 else "Green"})
	summary.append({"label": _("Advance (distinct POs)"), "value": advance_total, "datatype": "Currency"})

	# chart: top 10 suppliers by summed outstanding, display names as labels
	by_supplier = {}
	for r in rows:
		s = r.get("supplier") or ""
		by_supplier[s] = by_supplier.get(s, 0.0) + flt(r.get("outstanding_amount"))
	top = sorted(by_supplier.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
	chart = None
	if top and any(v for _lbl, v in top):
		chart = {
			"data": {"labels": [k or _("(blank)") for k, _v in top],
			         "datasets": [{"name": _("Outstanding"), "values": [round(v, 2) for _k, v in top]}]},
			"type": "bar",
		}
	return summary, chart
