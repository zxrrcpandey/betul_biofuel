# TS Monthly Service Summary — BBPL Report Updated 11 Aug 2026.xlsx, sheet
# "Monthly Service Summary" (Wave 3 #14; plan: PLAN_bbpl_wave3_reports.md)
#
# ONE ROW PER SERVICE-REQUEST MR ITEM x each DISTINCT submitted PO doc-linked
# via `PO Item.material_request` (LEFT-joined: a Service MR with no PO still
# renders with the whole WO leg blank — on demo only 3 of 9 submitted Service
# MRs have a PO at all, so blanks are the normal state, not an error).
#
# Client-answered mappings (11 Aug):
#   Ordered Qty  = From Service PO/WO  -> SUM(poi.stock_qty) for (PO, item_code)
#   Received Qty = From Service PR     -> SUM(pri.stock_qty) over submitted PRs
#                  of that PO matched by item_code (line links unreliable)
#   WO Balance   = PO Qty - PR Qty     -> ordered - received (blank when no PO)
#   Delivery Date= From Service PO/WO  -> po.schedule_date
#
# Submitted MRs by default; include_draft adds docstatus=0 (7 on demo);
# cancelled never. Money card sums base_rounded_total (fallback
# base_grand_total) — a USD service PO contributes its base amount exactly
# once. Every hop carries confidential_sql_clause + build_match_conditions
# with the v2.38.2 hop-readable gate; empty id-lists skip their hop.

import frappe
from frappe import _
from frappe.utils import flt, getdate, strip_html

ROW_LIMIT = 5000
IN_CHUNK = 1000


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


def get_columns():
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 60, "disable_total": 1},
		{"fieldname": "purpose", "label": _("Purpose"), "fieldtype": "Data", "width": 110},
		{"fieldname": "mr_date", "label": _("MR Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 170},
		{"fieldname": "service_description", "label": _("Service Description / Item Remark"), "fieldtype": "Data", "width": 220},
		{"fieldname": "workorder_id", "label": _("Workorder ID"), "fieldtype": "Link", "options": "Purchase Order", "width": 170},
		{"fieldname": "service_request_id", "label": _("Service Request ID"), "fieldtype": "Link", "options": "Material Request", "width": 180},
		{"fieldname": "supplier_name", "label": _("Supplier"), "fieldtype": "Data", "width": 180},
		{"fieldname": "supplier_details", "label": _("Supplier Details"), "fieldtype": "Data", "width": 250},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Data", "width": 180},
		# header amount repeats per item row -> never totalled
		{"fieldname": "wo_amount", "label": _("Work Order Amount"), "fieldtype": "Currency", "options": "Company:company:default_currency", "width": 145, "disable_total": 1},
		{"fieldname": "ordered_qty", "label": _("Ordered Qty"), "fieldtype": "Float", "width": 105, "precision": 2, "disable_total": 1},
		{"fieldname": "received_qty", "label": _("Received Qty"), "fieldtype": "Float", "width": 110, "precision": 2, "disable_total": 1},
		{"fieldname": "balance_qty", "label": _("WO Balance Qty"), "fieldtype": "Float", "width": 125, "precision": 2, "disable_total": 1},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Data", "width": 75},
		{"fieldname": "delivery_date", "label": _("Delivery Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "wo_date", "label": _("WO Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "indent_creator", "label": _("Indent Creator"), "fieldtype": "Data", "width": 160},
		{"fieldname": "mr_status", "label": _("MR Status"), "fieldtype": "Data", "width": 95},
	]


def _chunked(names):
	names = sorted(names)
	for i in range(0, len(names), IN_CHUNK):
		yield tuple(names[i:i + IN_CHUNK])


def _hop_readable(doctype):
	return frappe.has_permission(doctype, "read")


def _conf_match(doctype, alias):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause

	conds = []
	conf = confidential_sql_clause(alias, doctype=doctype)
	if conf:
		conds.append(conf.strip()[4:].strip())
	match = frappe.build_match_conditions(doctype)
	if match:
		conds.append("(%s)" % match.replace(f"`tab{doctype}`", alias))
	return conds


def _contact_blob(row):
	parts = []
	addr = row.get("address_display") or ""
	if addr:
		addr = addr.replace("<br>", ", ").replace("<br/>", ", ").replace("<br />", ", ")
		addr = " ".join(strip_html(addr).split()).strip(" ,")
		if addr:
			parts.append(addr)
	for key in ("contact_display", "contact_mobile", "contact_email"):
		val = (row.get(key) or "").strip()
		if val and val not in parts:
			parts.append(val)
	return " · ".join(parts)


def get_data(filters):
	params = {}
	ds = "mr.docstatus IN (0, 1)" if filters.get("include_draft") else "mr.docstatus = 1"
	conds = [ds, "mr.material_request_type = 'Service Request'"] + _conf_match("Material Request", "mr")
	if filters.get("from_date"):
		conds.append("mr.transaction_date >= %(from_date)s")
		params["from_date"] = getdate(filters["from_date"])
	if filters.get("to_date"):
		conds.append("mr.transaction_date <= %(to_date)s")
		params["to_date"] = getdate(filters["to_date"])
	if filters.get("material_request"):
		conds.append("mr.name = %(material_request)s")
		params["material_request"] = filters["material_request"]

	anchors = frappe.db.sql(
		f"""
		SELECT DISTINCT
			mr.name AS mr_no, mr.transaction_date AS mr_date, mr.docstatus AS mr_ds,
			mr.material_request_type AS purpose, mr.owner AS mr_owner,
			mri.item_code, mri.item_name, mri.ts_item_remark, mri.description,
			mri.stock_uom AS mri_uom, mri.idx AS item_idx
		FROM `tabMaterial Request` mr
		JOIN `tabMaterial Request Item` mri ON mri.parent = mr.name
		WHERE {" AND ".join(conds)}
		ORDER BY mr.transaction_date DESC, mr.name, mri.idx
		""",
		params,
		as_dict=True,
	)

	mr_names = {a["mr_no"] for a in anchors}
	po_of_mr, po_meta = {}, {}
	if mr_names and _hop_readable("Purchase Order"):
		po_conds = ["po.docstatus = 1", "IFNULL(poi.material_request, '') != ''"] + _conf_match("Purchase Order", "po")
		po_params = {}
		if filters.get("supplier"):
			po_conds.append("po.supplier = %(supplier)s")
			po_params["supplier"] = filters["supplier"]
		if filters.get("cost_center"):
			po_conds.append("po.cost_center = %(cost_center)s")
			po_params["cost_center"] = filters["cost_center"]
		for chunk in _chunked(mr_names):
			for x in frappe.db.sql(
				f"""SELECT DISTINCT poi.material_request AS mr, po.name,
					po.supplier_name, po.cost_center, po.transaction_date AS wo_date,
					po.schedule_date AS delivery_date, po.address_display,
					po.contact_display, po.contact_mobile, po.contact_email,
					CASE WHEN IFNULL(po.rounded_total, 0) != 0 THEN po.rounded_total ELSE po.grand_total END AS wo_amount
				FROM `tabPurchase Order Item` poi
				JOIN `tabPurchase Order` po ON po.name = poi.parent
				WHERE poi.material_request IN %(names)s AND {" AND ".join(po_conds)}""",
				dict(po_params, names=chunk),
				as_dict=True,
			):
				po_of_mr.setdefault(x["mr"], []).append(x["name"])
				po_meta[x["name"]] = x

	po_names = set(po_meta)
	# per (po, item_code): ordered from PO items, received from PR items
	ordered, po_uom = {}, {}
	if po_names:
		for chunk in _chunked(po_names):
			for x in frappe.db.sql(
				"""SELECT parent, item_code, IFNULL(SUM(stock_qty), 0) AS q, MAX(stock_uom) AS uom
				FROM `tabPurchase Order Item` WHERE parent IN %(names)s
				GROUP BY parent, item_code""",
				{"names": chunk},
				as_dict=True,
			):
				ordered[(x["parent"], x["item_code"])] = flt(x["q"])
				po_uom[(x["parent"], x["item_code"])] = x["uom"]
	received = {}
	if po_names and _hop_readable("Purchase Receipt"):
		pr_conds = ["pr.docstatus = 1", "IFNULL(pri.purchase_order, '') != ''"] + _conf_match("Purchase Receipt", "pr")
		for chunk in _chunked(po_names):
			for x in frappe.db.sql(
				f"""SELECT pri.purchase_order AS po, pri.item_code, IFNULL(SUM(pri.stock_qty), 0) AS q
				FROM `tabPurchase Receipt Item` pri
				JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
				WHERE pri.purchase_order IN %(names)s AND {" AND ".join(pr_conds)}
				GROUP BY pri.purchase_order, pri.item_code""",
				{"names": chunk},
				as_dict=True,
			):
				received[(x["po"], x["item_code"])] = flt(x["q"])

	names = _fullname_map({a["mr_owner"] for a in anchors})

	supplier_set = bool(filters.get("supplier")) or bool(filters.get("cost_center"))
	rows = []
	truncated = False
	for a in anchors:
		pos = sorted(po_of_mr.get(a["mr_no"], ()))
		legs = pos if pos else ([None] if not supplier_set else [])
		for po in legs:
			m = po_meta.get(po) or {}
			key = (po, a["item_code"])
			oq = ordered.get(key) if po else None
			rq = received.get(key, 0.0) if po else None
			desc = a.get("ts_item_remark") or a.get("description") or ""
			rows.append({
				"purpose": a["purpose"],
				"mr_date": a["mr_date"],
				"item_name": " ".join(strip_html(a["item_name"]).split()) if a.get("item_name") else (a.get("item_code") or ""),
				"service_description": " ".join(strip_html(desc).split()) if desc else "",
				"workorder_id": po,
				"service_request_id": a["mr_no"],
				"supplier_name": m.get("supplier_name") or "",
				"supplier_details": _contact_blob(m) if m else "",
				"cost_center": m.get("cost_center") or "",
				"wo_amount": flt(m.get("wo_amount")) if po else None,
				"ordered_qty": oq,
				"received_qty": rq if (po and oq is not None) else (rq if po else None),
				"balance_qty": (flt(oq) - flt(rq)) if oq is not None else None,
				"uom": po_uom.get(key) or a.get("mri_uom") or "",
				"delivery_date": m.get("delivery_date") if po else None,
				"wo_date": m.get("wo_date") if po else None,
				"indent_creator": names.get(a["mr_owner"], a["mr_owner"] or ""),
				"mr_status": "Draft" if a["mr_ds"] == 0 else "Submitted",
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


def _fullname_map(user_ids):
	"""user_id -> full name, one bulk query (Lesson 168)."""
	ids = sorted({u for u in user_ids if u})
	if not ids:
		return {}
	out = {}
	for chunk in _chunked(ids):
		for x in frappe.db.sql(
			"SELECT name, full_name FROM `tabUser` WHERE name IN %(ids)s",
			{"ids": chunk},
			as_dict=True,
		):
			out[x["name"]] = x["full_name"] or x["name"]
	return out


def _summary(rows, truncated):
	mrs = {r["service_request_id"] for r in rows}
	wos = {r["workorder_id"] for r in rows if r["workorder_id"]}
	open_rows = sum(1 for r in rows if r["balance_qty"] is not None and flt(r["balance_qty"]) > 0)
	# base-currency card: each WO counted once
	wo_amt = 0.0
	if wos:
		amts = frappe.db.sql(
			"""SELECT name, CASE WHEN IFNULL(base_rounded_total, 0) != 0
				THEN base_rounded_total ELSE base_grand_total END
			FROM `tabPurchase Order` WHERE name IN %(n)s""",
			{"n": tuple(sorted(wos))},
		)
		wo_amt = sum(flt(a[1]) for a in amts)
	out = []
	if truncated:
		out.append({"label": _("Result truncated"),
		            "value": _("first {0} rows — cards reflect shown rows only").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Orange"})
	out += [
		{"label": _("Service MRs"), "value": len(mrs), "datatype": "Int"},
		{"label": _("Work Orders"), "value": len(wos), "datatype": "Int",
		 "indicator": "Green" if wos else "Orange"},
		{"label": _("Total WO Amount (base)"), "value": wo_amt, "datatype": "Currency"},
		{"label": _("Open Balance Rows"), "value": open_rows, "datatype": "Int",
		 "indicator": "Orange" if open_rows else "Green"},
	]
	return out
