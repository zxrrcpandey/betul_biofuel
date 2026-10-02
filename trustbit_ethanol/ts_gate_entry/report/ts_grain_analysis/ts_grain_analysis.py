# TS Grain Analysis — client Google Sheet "V1.8 - 3 / grain analysis report"
# (2 Oct 2026; replaces the old un-mapped "account grain report" #13).
#
# ONE ROW PER TS DEDUCTION SHEET (default category Grain; Coal/All via filter).
# The Deduction Sheet is the analysis unit — on demo 98 sheets carry EVERY
# money/weight field 100% populated, while only 62 reach a Purchase Receipt.
# Legs are LEFT-joined and render blank when absent (exit-before-GRN is legal
# since v2.30.0): PR via ds.grn_reference, falling back to pr.ts_token ==
# ds.token_number; Weighbridge Log + Token via ds.token_number; PO via
# ds.purchase_order; PI (for TDS) via pi.ts_deduction_sheet, falling back to
# pi.ts_token, then pi.ts_purchase_receipt.
#
# Client source mappings honoured verbatim:
#   Gross/Net/Tare/RST  = "Weighbridge"  -> TS Weighbridge Log (323/323 rows)
#   UL/Rebate/Brokerage = deduction table rate + Calculated Amt columns
#   QC %/Amount         = Quality Deduction line rate + calculated amt
#   LOCATION            = "use & location on po" -> PO Item ts_delivery_location
# Documented deviations/assumptions (flagged for UAT):
#   WT. DEDUCTION = DS.weight_deduction — the sheet's formula says "INVOICE
#     Quantity - Tear weight" but the DS field provably equals Invoice - NET
#     weight (6/6 sampled), which is the business-correct deduction.
#   TDS (source blank on the sheet) = SUM of the linked PI's Purchase Taxes
#     rows whose account head contains 'TDS'.
#   PAYABLE AMOUNT (source blank)   = DS.net_payable.
#   Deduction amounts use calculated_amount per the sheet's wording; an
#     overridden line's actual_amount may differ (is_overridden rows).
#   Rebate lines exist as a mechanism (Grain master row added 2 Oct, rate 0)
#     — the columns render blank until ops set the real rate.
#
# Deduction-type matching is label-TOLERANT: live data carries 'Unloading' and
# 'Unloading Charge', 'Dhalta' and 'Dhalta (Spillage)' — prefix matching, not
# equality. Chain hops use report_utils (Gotcha 17 / L409-412) — never
# hand-rolled: PR/PO/PI carry confidentiality + match conditions; DS, Token
# and Weighbridge Log are not confidentiality-governed and carry match
# conditions only, each behind the hop-readable gate.
#
# Confidentiality of the po_no COLUMN: ds.purchase_order is the DS's own field,
# but the name it carries is a governed doc — it renders only when the PO is in
# the caller-visible PO leg (harness S31, 2 Oct: 20 conf names leaked before
# this gate). The purchase_order FILTER likewise refuses to act as an existence
# oracle: an invisible PO filter returns zero rows.

import frappe
from frappe import _
from frappe.utils import flt, getdate, strip_html

from trustbit_ethanol.ts_gate_entry.report import report_utils as ru

ROW_LIMIT = 5000

CATEGORIES = ("", "Grain", "Coal", "Other", "All")


def execute(filters=None):
	if not frappe.has_permission("TS Deduction Sheet", "read"):
		frappe.throw(_("Not permitted to read TS Deduction Sheet"), frappe.PermissionError)

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
	if filters.get("item_category") and filters["item_category"] not in CATEGORIES:
		frappe.throw(_("Invalid Item Category"))


def get_columns():
	cur = "Company:company:default_currency"
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 60, "disable_total": 1},
		{"fieldname": "ds_no", "label": _("Deduction Sheet"), "fieldtype": "Link", "options": "TS Deduction Sheet", "width": 160},
		{"fieldname": "pr_no", "label": _("Purchase Receipt No"), "fieldtype": "Data", "width": 160},
		{"fieldname": "pr_date", "label": _("PR Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Data", "width": 190},
		{"fieldname": "bill_no", "label": _("Supplier Invoice No"), "fieldtype": "Data", "width": 140},
		{"fieldname": "bill_date", "label": _("Supplier Invoice Date"), "fieldtype": "Date", "width": 125},
		{"fieldname": "token_no", "label": _("Token No."), "fieldtype": "Data", "width": 130},
		{"fieldname": "vehicle_no", "label": _("Vehicle No"), "fieldtype": "Data", "width": 110},
		{"fieldname": "rst_no", "label": _("RST No"), "fieldtype": "Data", "width": 100},
		# weighbridge trio — mixed UOM-free kg figures, never totalled
		{"fieldname": "gross_weight", "label": _("Gross Weight"), "fieldtype": "Float", "width": 110, "precision": 2, "disable_total": 1},
		{"fieldname": "net_weight", "label": _("Net Weight"), "fieldtype": "Float", "width": 105, "precision": 2, "disable_total": 1},
		{"fieldname": "tare_weight", "label": _("Tare Weight"), "fieldtype": "Float", "width": 105, "precision": 2, "disable_total": 1},
		{"fieldname": "invoice_qty", "label": _("Invoice Quantity"), "fieldtype": "Float", "width": 120, "precision": 2, "disable_total": 1},
		{"fieldname": "wt_deduction", "label": _("Wt. Deduction"), "fieldtype": "Float", "width": 110, "precision": 2, "disable_total": 1},
		{"fieldname": "item_rate", "label": _("Item Rate"), "fieldtype": "Currency", "options": cur, "width": 100, "disable_total": 1},
		{"fieldname": "invoice_value", "label": _("Invoice Value"), "fieldtype": "Currency", "options": cur, "width": 125},
		{"fieldname": "pr_amount", "label": _("PR Amount"), "fieldtype": "Currency", "options": cur, "width": 120},
		{"fieldname": "ul_rate", "label": _("UL Rate"), "fieldtype": "Float", "width": 85, "precision": 2, "disable_total": 1},
		{"fieldname": "unloading_amount", "label": _("Unloading Amount"), "fieldtype": "Currency", "options": cur, "width": 130},
		{"fieldname": "rebate_rate", "label": _("Rebate Rate"), "fieldtype": "Float", "width": 95, "precision": 2, "disable_total": 1},
		{"fieldname": "rebate_amount", "label": _("Rebate Amount"), "fieldtype": "Currency", "options": cur, "width": 120},
		{"fieldname": "brok_rate", "label": _("Brokerage Rate"), "fieldtype": "Float", "width": 110, "precision": 2, "disable_total": 1},
		{"fieldname": "brokerage_amount", "label": _("Brokerage Amount"), "fieldtype": "Currency", "options": cur, "width": 130},
		{"fieldname": "qc_percentage", "label": _("QC Percentage"), "fieldtype": "Float", "width": 110, "precision": 2, "disable_total": 1},
		{"fieldname": "qc_amount", "label": _("QC Amount"), "fieldtype": "Currency", "options": cur, "width": 110},
		{"fieldname": "tds", "label": _("TDS"), "fieldtype": "Currency", "options": cur, "width": 100},
		{"fieldname": "payable_amount", "label": _("Payable Amount"), "fieldtype": "Currency", "options": cur, "width": 130},
		{"fieldname": "po_no", "label": _("P.O. No."), "fieldtype": "Data", "width": 160},
		{"fieldname": "po_date", "label": _("PO Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "location", "label": _("Location"), "fieldtype": "Data", "width": 160},
		{"fieldname": "ds_status", "label": _("Sheet Status"), "fieldtype": "Data", "width": 95},
	]


def _ded_kind(label):
	"""Label-tolerant deduction-type bucket."""
	v = (label or "").strip().lower()
	if v.startswith("unloading"):
		return "ul"
	if v.startswith(("rebate", "rebet")):
		return "rebate"
	if v.startswith("brokerage"):
		return "brok"
	if v.startswith(("quality", "qc")):
		return "qc"
	return None


def get_data(filters):
	params = {}
	conds = ["ds.docstatus = 1"] if filters.get("submitted_only") else ["ds.docstatus IN (0, 1)"]
	conds += ru.conf_match_clauses("TS Deduction Sheet", "ds")
	cat = filters.get("item_category") or "Grain"
	if cat not in ("All", ""):
		conds.append("ds.item_category = %(category)s")
		params["category"] = cat
	if filters.get("from_date"):
		conds.append("COALESCE(ds.posting_date, DATE(ds.creation)) >= %(from_date)s")
		params["from_date"] = getdate(filters["from_date"])
	if filters.get("to_date"):
		conds.append("COALESCE(ds.posting_date, DATE(ds.creation)) <= %(to_date)s")
		params["to_date"] = getdate(filters["to_date"])
	if filters.get("purchase_order"):
		# never act as an existence/linkage oracle for POs the caller cannot see:
		# an invisible (confidential or unreadable-hop) PO filter returns NOTHING,
		# indistinguishable from a PO that does not exist
		if not ru.visible_docs("Purchase Order", "po", {filters["purchase_order"]},
		                       docstatus=(0, 1, 2)):
			return [], False
		conds.append("ds.purchase_order = %(purchase_order)s")
		params["purchase_order"] = filters["purchase_order"]
	if filters.get("token"):
		conds.append("ds.token_number = %(token)s")
		params["token"] = filters["token"]

	sheets = frappe.db.sql(
		f"""
		SELECT
			ds.name AS ds_no, ds.docstatus AS ds_docstatus,
			COALESCE(ds.posting_date, DATE(ds.creation)) AS ds_date,
			ds.token_number AS token_no, ds.purchase_order AS po_id,
			ds.grn_reference, ds.supplier_name AS ds_supplier_name,
			ds.invoice_qty, ds.weight_deduction AS wt_deduction,
			ds.item_rate, ds.invoice_value, ds.net_payable AS payable_amount,
			ds.net_weight AS ds_net_weight
		FROM `tabTS Deduction Sheet` ds
		WHERE {" AND ".join(conds)}
		ORDER BY COALESCE(ds.posting_date, DATE(ds.creation)) DESC, ds.name DESC
		""",
		params,
		as_dict=True,
	)

	ds_names = [s["ds_no"] for s in sheets]
	tokens = {s["token_no"] for s in sheets if s["token_no"]}
	pos = {s["po_id"] for s in sheets if s["po_id"]}

	ded = _deduction_lines(ds_names)
	wb = _weighbridge(tokens)
	tok = _tokens(tokens)
	pr_by_ds = _receipts(sheets)
	po_meta, po_loc = _purchase_orders(pos)
	tds_by_ds = _tds(sheets, pr_by_ds)

	# supplier display names: PR supplier id -> name; fallback DS supplier_name
	sup_ids = {p["supplier"] for p in pr_by_ds.values() if p and p.get("supplier")}
	sup_names = {}
	if sup_ids:
		for chunk in ru.chunked(sup_ids):
			for x in frappe.db.sql(
				"SELECT name, supplier_name FROM `tabSupplier` WHERE name IN %(ids)s",
				{"ids": chunk}, as_dict=True,
			):
				sup_names[x["name"]] = x["supplier_name"] or x["name"]

	rows = []
	truncated = False
	for s in sheets:
		pr = pr_by_ds.get(s["ds_no"]) or {}
		w = wb.get(s["token_no"]) or {}
		t = tok.get(s["token_no"]) or {}
		po = po_meta.get(s["po_id"]) or {}
		d = ded.get(s["ds_no"]) or {}
		supplier = sup_names.get(pr.get("supplier") or "", "") or (s["ds_supplier_name"] or "")
		rows.append({
			"ds_no": s["ds_no"],
			"pr_no": pr.get("name") or "",
			"pr_date": pr.get("posting_date"),
			"supplier": " ".join(strip_html(supplier).split()) if supplier else "",
			"bill_no": " ".join(strip_html(pr["bill_no"]).split()) if pr.get("bill_no") else "",
			"bill_date": pr.get("bill_date"),
			"token_no": s["token_no"] or "",
			"vehicle_no": t.get("vehicle_number") or "",
			"rst_no": w.get("rst_number") or t.get("custom_rst_number") or "",
			"gross_weight": w.get("gross_weight"),
			"net_weight": w.get("net_weight") if w.get("net_weight") is not None else s.get("ds_net_weight"),
			"tare_weight": w.get("tare_weight"),
			"invoice_qty": s["invoice_qty"],
			"wt_deduction": s["wt_deduction"],
			"item_rate": s["item_rate"],
			"invoice_value": s["invoice_value"],
			"pr_amount": pr.get("grand_total"),
			"ul_rate": d.get("ul_rate"), "unloading_amount": d.get("ul_amt"),
			"rebate_rate": d.get("rebate_rate"), "rebate_amount": d.get("rebate_amt"),
			"brok_rate": d.get("brok_rate"), "brokerage_amount": d.get("brok_amt"),
			"qc_percentage": d.get("qc_rate"), "qc_amount": d.get("qc_amt"),
			"tds": tds_by_ds.get(s["ds_no"]),
			"payable_amount": s["payable_amount"],
			# ds.purchase_order is the DS's OWN field, but the NAME it carries is a
			# confidentiality-governed doc — render it only when the PO leg says the
			# caller may see that PO (the ts_po_report._mr_owner_map leak class, v2.38.6)
			"po_no": s["po_id"] if s["po_id"] and s["po_id"] in po_meta else "",
			"po_date": po.get("transaction_date"),
			"location": po_loc.get(s["po_id"], ""),
			"ds_status": "Submitted" if s["ds_docstatus"] == 1 else "Draft",
		})
		if len(rows) > ROW_LIMIT:
			truncated = True
			break
	if truncated:
		rows = rows[:ROW_LIMIT]
	for i, r in enumerate(rows, start=1):
		r["sr"] = i
	return rows, truncated


def _deduction_lines(ds_names):
	"""ds -> {ul_rate, ul_amt, rebate_rate, rebate_amt, brok_rate, brok_amt,
	qc_rate, qc_amt}. Rate = MAX per bucket, amount = SUM of calculated_amount
	(per the client sheet's 'Calculated Amt column')."""
	if not ds_names:
		return {}
	out = {}
	for chunk in ru.chunked(ds_names):
		for x in frappe.db.sql(
			"""SELECT parent, deduction_type, rate, calculated_amount
			FROM `tabTS Deduction Line` WHERE parent IN %(names)s""",
			{"names": chunk}, as_dict=True,
		):
			kind = _ded_kind(x["deduction_type"])
			if not kind:
				continue
			b = out.setdefault(x["parent"], {})
			rk, ak = kind + "_rate", kind + "_amt"
			b[rk] = max(flt(b.get(rk)), flt(x["rate"])) if b.get(rk) is not None else flt(x["rate"])
			b[ak] = flt(b.get(ak)) + flt(x["calculated_amount"])
	# qc bucket uses qc_rate/qc_amt keys already; normalise missing to None implicitly
	return out


def _weighbridge(tokens):
	"""token -> {gross_weight, tare_weight, net_weight, rst_number} (one log per
	token — verified). Not confidentiality-governed; match-gated + hop gate."""
	if not tokens or not ru.hop_readable("TS Weighbridge Log"):
		return {}
	conds = ru.conf_match_clauses("TS Weighbridge Log", "wb")
	where = (" AND " + " AND ".join(conds)) if conds else ""
	out = {}
	for chunk in ru.chunked(tokens):
		for x in frappe.db.sql(
			f"""SELECT wb.token_number, wb.gross_weight, wb.tare_weight,
				wb.net_weight, wb.rst_number
			FROM `tabTS Weighbridge Log` wb
			WHERE wb.token_number IN %(names)s{where}""",
			{"names": chunk}, as_dict=True,
		):
			out[x.pop("token_number")] = x
	return out


def _tokens(tokens):
	if not tokens or not ru.hop_readable("TS Token"):
		return {}
	conds = ru.conf_match_clauses("TS Token", "t")
	where = (" AND " + " AND ".join(conds)) if conds else ""
	out = {}
	for chunk in ru.chunked(tokens):
		for x in frappe.db.sql(
			f"""SELECT t.name, t.vehicle_number, t.custom_rst_number
			FROM `tabTS Token` t WHERE t.name IN %(names)s{where}""",
			{"names": chunk}, as_dict=True,
		):
			out[x.pop("name")] = x
	return out


def _receipts(sheets):
	"""ds_no -> visible PR dict. grn_reference first; token-match fallback."""
	extra = ", pr.posting_date, pr.supplier, pr.bill_no, pr.bill_date, pr.grand_total"
	by_name = {}
	named = {s["grn_reference"] for s in sheets if s.get("grn_reference")}
	for x in ru.visible_docs("Purchase Receipt", "pr", named, extra_cols=extra):
		by_name[x["name"]] = x

	tokens = {s["token_no"] for s in sheets if s["token_no"] and not s.get("grn_reference")}
	by_token = {}
	if tokens and ru.hop_readable("Purchase Receipt"):
		conds = ["pr.docstatus = 1", "IFNULL(pr.ts_token, '') != ''"] + ru.conf_match_clauses("Purchase Receipt", "pr")
		for chunk in ru.chunked(tokens):
			for x in frappe.db.sql(
				f"""SELECT pr.ts_token, pr.name{extra}
				FROM `tabPurchase Receipt` pr
				WHERE pr.ts_token IN %(names)s AND {" AND ".join(conds)}
				ORDER BY pr.posting_date DESC""",
				{"names": chunk}, as_dict=True,
			):
				by_token.setdefault(x.pop("ts_token"), x)  # newest wins

	out = {}
	for s in sheets:
		if s.get("grn_reference") and s["grn_reference"] in by_name:
			out[s["ds_no"]] = by_name[s["grn_reference"]]
		elif s["token_no"] in by_token:
			out[s["ds_no"]] = by_token[s["token_no"]]
	return out


def _purchase_orders(pos):
	"""po -> meta; po -> comma-joined distinct ts_delivery_location of its items.

	docstatus-free (0,1,2): this leg is reference metadata for a PO the Deduction
	Sheet explicitly links, AND it doubles as the name-visibility gate for the
	po_no column — docstatus must not conflate with confidentiality there."""
	meta = {x["name"]: x for x in ru.visible_docs(
		"Purchase Order", "po", pos, extra_cols=", po.transaction_date",
		docstatus=(0, 1, 2))}
	loc = {}
	if meta:
		for chunk in ru.chunked(meta.keys()):
			for x in frappe.db.sql(
				"""SELECT parent, ts_delivery_location FROM `tabPurchase Order Item`
				WHERE parent IN %(names)s AND IFNULL(ts_delivery_location, '') != ''""",
				{"names": chunk}, as_dict=True,
			):
				loc.setdefault(x["parent"], set()).add(" ".join(strip_html(x["ts_delivery_location"]).split()))
	return meta, {k: ", ".join(sorted(v)) for k, v in loc.items()}


def _tds(sheets, pr_by_ds):
	"""ds -> TDS amount from the linked PI's tax rows (head contains 'TDS').
	PI resolved by priority: pi.ts_deduction_sheet -> pi.ts_token ->
	pi.ts_purchase_receipt. PI hop is confidentiality-governed."""
	if not ru.hop_readable("Purchase Invoice"):
		return {}
	conds = ["pi.docstatus = 1"] + ru.conf_match_clauses("Purchase Invoice", "pi")
	where = " AND ".join(conds)

	pi_of_ds = {}

	def collect(field, keys, keymap):
		keys = {k for k in keys if k}
		if not keys:
			return
		for chunk in ru.chunked(keys):
			for x in frappe.db.sql(
				f"""SELECT pi.`{field}` AS k, pi.name FROM `tabPurchase Invoice` pi
				WHERE pi.`{field}` IN %(names)s AND {where}
				ORDER BY pi.posting_date DESC""",
				{"names": chunk}, as_dict=True,
			):
				for ds in keymap.get(x["k"], ()):
					pi_of_ds.setdefault(ds, x["name"])

	collect("ts_deduction_sheet", [s["ds_no"] for s in sheets],
	        {s["ds_no"]: [s["ds_no"]] for s in sheets})
	tokmap = {}
	for s in sheets:
		if s["token_no"] and s["ds_no"] not in pi_of_ds:
			tokmap.setdefault(s["token_no"], []).append(s["ds_no"])
	collect("ts_token", tokmap.keys(), tokmap)
	prmap = {}
	for s in sheets:
		pr = pr_by_ds.get(s["ds_no"])
		if pr and s["ds_no"] not in pi_of_ds:
			prmap.setdefault(pr["name"], []).append(s["ds_no"])
	collect("ts_purchase_receipt", prmap.keys(), prmap)

	pis = set(pi_of_ds.values())
	tds_of_pi = {}
	if pis:
		for chunk in ru.chunked(pis):
			for x in frappe.db.sql(
				"""SELECT parent, IFNULL(SUM(tax_amount), 0) AS t
				FROM `tabPurchase Taxes and Charges`
				WHERE parenttype = 'Purchase Invoice' AND parent IN %(names)s
				  AND account_head LIKE '%%TDS%%'
				GROUP BY parent""",
				{"names": chunk}, as_dict=True,
			):
				tds_of_pi[x["parent"]] = flt(x["t"])
	return {ds: tds_of_pi.get(pi) for ds, pi in pi_of_ds.items() if pi in tds_of_pi}


def _summary(rows, truncated):
	out = []
	if truncated:
		out.append({"label": _("Result truncated"),
		            "value": _("first {0} rows — cards reflect shown rows only").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Orange"})
	missing_pr = sum(1 for r in rows if not r["pr_no"])
	out += [
		{"label": _("Deduction Sheets"), "value": len(rows), "datatype": "Int"},
		{"label": _("Invoice Value"), "value": sum(flt(r["invoice_value"]) for r in rows), "datatype": "Currency"},
		{"label": _("Payable"), "value": sum(flt(r["payable_amount"]) for r in rows), "datatype": "Currency",
		 "indicator": "Green"},
		{"label": _("Sheets Without PR"), "value": missing_pr, "datatype": "Int",
		 "indicator": "Orange" if missing_pr else "Green"},
	]
	return out
