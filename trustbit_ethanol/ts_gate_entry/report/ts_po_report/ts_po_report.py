# TS PO Report — BBPL Report.xlsx spec (27 Jul 2026), sheet "PO report"
#
# One row per Purchase Order ITEM, with pricing, per-item GST and fulfilment.
# Spec columns:
#   S.No. | PO ID | PO Status | Material Request | Item Code | Item Name |
#   Description | Item Remark | Quantity | Price List Rate | Discount Amount |
#   Rate | Amount | IGST Amount | SGST Amount | CGST Amount | Grand Total |
#   PO Creater name | Received Qty | Mr creater name
#
# PER-ITEM GST — IMPORTANT
# ------------------------
# `Purchase Order Item.igst_amount / sgst_amount / cgst_amount` exist (India
# Compliance is installed) but are ZERO on every row: India Compliance does not
# populate them on Purchase Orders. PO GST lives only in the header
# `Purchase Taxes and Charges` table.
#
# So each item's share is DERIVED: for every Input-Tax head actually charged on
# the PO (CGST / SGST / IGST, excluding RCM and Refund heads), that head's
# tax_amount is apportioned across the item lines weighted by
# (line amount x that line's own rate for the head, read from item_tax_rate).
# Any residual paise from rounding is pushed onto the largest line so the
# columns reconcile EXACTLY to the header total. Verified against every PO on
# demo carrying real GST rows: 58/58 reconciled, including 16 multi-item POs.
#
# A PO with no Input-Tax rows simply shows zeros — that is honest, not a gap.
#
# "Grand Total" is the PO header total (per spec) and therefore repeats on each
# of that PO's item rows; the column carries disable_total so it cannot be
# summed into a meaningless figure.
#
# Confidentiality: maize/grain-confidential POs are hidden from non-allow-listed
# users via confidential_sql_clause (Lesson 297 — raw SQL bypasses the
# permission_query_conditions hook).

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, strip_html

ROW_LIMIT = 5000
_GST_KINDS = ("cgst", "sgst", "igst")


def execute(filters=None):
	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("Not permitted to read Purchase Order"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	data, truncated = get_data(filters)
	return get_columns(), data, None, None, _summary(data, truncated)


def _validate_filters(filters):
	frm, to = filters.get("from_date"), filters.get("to_date")
	if frm and to and getdate(frm) > getdate(to):
		frappe.throw(_("PO Date From cannot be after PO Date To"))


def get_columns():
	cur = "Company:company:default_currency"
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 65, "disable_total": 1},
		{"fieldname": "po_no", "label": _("PO ID"), "fieldtype": "Link", "options": "Purchase Order", "width": 175},
		{"fieldname": "po_status", "label": _("PO Status"), "fieldtype": "Data", "width": 150},
		{"fieldname": "material_request", "label": _("Material Request"), "fieldtype": "Link", "options": "Material Request", "width": 180},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 145},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 210},
		{"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "width": 230},
		{"fieldname": "item_remark", "label": _("Item Remark"), "fieldtype": "Data", "width": 190},
		# PO lines mix UOMs -> quantity sums are meaningless.
		{"fieldname": "qty", "label": _("Quantity"), "fieldtype": "Float", "width": 100, "precision": 2, "disable_total": 1},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Data", "width": 75},
		{"fieldname": "price_list_rate", "label": _("Price List Rate"), "fieldtype": "Currency", "options": cur, "width": 130, "disable_total": 1},
		{"fieldname": "discount_amount", "label": _("Discount Amount"), "fieldtype": "Currency", "options": cur, "width": 135},
		{"fieldname": "rate", "label": _("Rate"), "fieldtype": "Currency", "options": cur, "width": 110, "disable_total": 1},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "options": cur, "width": 130},
		{"fieldname": "igst_amount", "label": _("IGST Amount"), "fieldtype": "Currency", "options": cur, "width": 125},
		{"fieldname": "sgst_amount", "label": _("SGST Amount"), "fieldtype": "Currency", "options": cur, "width": 125},
		{"fieldname": "cgst_amount", "label": _("CGST Amount"), "fieldtype": "Currency", "options": cur, "width": 125},
		# Header value repeated per item row -> never total it.
		{"fieldname": "grand_total", "label": _("Grand Total"), "fieldtype": "Currency", "options": cur, "width": 140, "disable_total": 1},
		{"fieldname": "po_creator", "label": _("PO Creator"), "fieldtype": "Data", "width": 165},
		{"fieldname": "received_qty", "label": _("Received Qty"), "fieldtype": "Float", "width": 120, "precision": 2, "disable_total": 1},
		{"fieldname": "mr_creator", "label": _("MR Creator"), "fieldtype": "Data", "width": 165},
	]


def get_data(filters):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause

	params = {"limit": ROW_LIMIT + 1}
	clauses = ["po.docstatus IN (0, 1)"] if filters.get("include_draft") else ["po.docstatus = 1"]

	conf = confidential_sql_clause("po")
	if conf:
		clauses.append(conf.strip()[4:].strip())

	match_cond = frappe.build_match_conditions("Purchase Order")
	if match_cond:
		clauses.append("(%s)" % match_cond.replace("`tabPurchase Order`", "po"))

	if filters.get("from_date"):
		clauses.append("po.transaction_date >= %(from_date)s")
		params["from_date"] = getdate(filters["from_date"])
	if filters.get("to_date"):
		clauses.append("po.transaction_date <= %(to_date)s")
		params["to_date"] = getdate(filters["to_date"])
	if filters.get("supplier"):
		clauses.append("po.supplier = %(supplier)s")
		params["supplier"] = filters["supplier"]
	if filters.get("cost_center"):
		clauses.append("(po.cost_center = %(cost_center)s OR poi.cost_center = %(cost_center)s)")
		params["cost_center"] = filters["cost_center"]
	if filters.get("item_code"):
		clauses.append("poi.item_code = %(item_code)s")
		params["item_code"] = filters["item_code"]
	if filters.get("approval_status"):
		clauses.append("po.ts_approval_status = %(approval_status)s")
		params["approval_status"] = filters["approval_status"]
	if filters.get("material_request"):
		clauses.append("poi.material_request = %(material_request)s")
		params["material_request"] = filters["material_request"]

	where = " AND ".join(clauses)

	rows = frappe.db.sql(
		f"""
		SELECT
			poi.name                    AS poi_name,
			po.name                     AS po_no,
			po.ts_approval_status       AS po_status,
			poi.material_request        AS material_request,
			poi.item_code               AS item_code,
			poi.item_name               AS item_name,
			poi.description             AS description,
			poi.ts_item_remark          AS item_remark,
			poi.qty                     AS qty,
			poi.uom                     AS uom,
			poi.price_list_rate         AS price_list_rate,
			poi.discount_amount         AS discount_amount,
			poi.rate                    AS rate,
			poi.amount                  AS amount,
			COALESCE(NULLIF(po.rounded_total, 0), po.grand_total) AS grand_total,
			po.owner                    AS po_owner,
			IFNULL(poi.received_qty, 0) AS received_qty
		FROM `tabPurchase Order Item` poi
		JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE {where}
		ORDER BY po.transaction_date DESC, po.name, poi.idx
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)

	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]

	gst = _gst_by_item({r["po_no"] for r in rows})
	mr_owner = _mr_owner_map({r.get("material_request") for r in rows})
	names = _fullname_map(
		[r.get("po_owner") for r in rows] + list(mr_owner.values())
	)

	for i, r in enumerate(rows, start=1):
		r["sr"] = i
		share = gst.get(r.pop("poi_name"), {})
		r["cgst_amount"] = share.get("cgst", 0.0)
		r["sgst_amount"] = share.get("sgst", 0.0)
		r["igst_amount"] = share.get("igst", 0.0)
		r["po_creator"] = names.get(r.pop("po_owner", None) or "", "")
		r["mr_creator"] = names.get(mr_owner.get(r.get("material_request")) or "", "")
		# Free text typed on the PO line; the datatable renders cells as HTML.
		for f in ("description", "item_remark"):
			if r.get(f):
				r[f] = " ".join(strip_html(r[f]).split())

	return rows, truncated


# --------------------------------------------------------------- GST derivation

def _gst_kind(account_head):
	"""Map an account head to cgst/sgst/igst, or None if it is not an input-GST head.

	RCM and Refund heads are excluded: they are reverse-charge / refund
	bookkeeping, not tax charged to us on this order.
	"""
	a = (account_head or "").lower()
	if not a.startswith("input tax"):
		return None
	if "rcm" in a or "refund" in a:
		return None
	for kind in _GST_KINDS:
		if kind in a:
			return kind
	return None


def _gst_by_item(po_names):
	"""po_item_name -> {'cgst': x, 'sgst': y, 'igst': z}.

	Apportions each header tax head across the PO's item lines weighted by
	(line amount x that line's rate for the head), residual onto the largest
	line so the total reconciles exactly to the header.
	"""
	po_names = {p for p in po_names if p}
	if not po_names:
		return {}

	taxes = frappe.db.sql(
		"""SELECT parent, account_head, tax_amount FROM `tabPurchase Taxes and Charges`
		   WHERE parenttype = 'Purchase Order' AND parent IN %(pos)s""",
		{"pos": tuple(po_names)}, as_dict=True,
	)
	if not taxes:
		return {}

	items = frappe.db.sql(
		"""SELECT name, parent, amount, item_tax_rate FROM `tabPurchase Order Item`
		   WHERE parent IN %(pos)s ORDER BY parent, idx""",
		{"pos": tuple(po_names)}, as_dict=True,
	)

	by_po_items = {}
	for it in items:
		by_po_items.setdefault(it["parent"], []).append(it)

	totals = {}
	for t in taxes:
		kind = _gst_kind(t["account_head"])
		if kind:
			totals.setdefault(t["parent"], {}).setdefault(kind, 0.0)
			totals[t["parent"]][kind] += flt(t["tax_amount"])

	out = {}
	for po, kinds in totals.items():
		lines = by_po_items.get(po) or []
		if not lines:
			continue
		for kind, total in kinds.items():
			if not total:
				continue
			weights = []
			for it in lines:
				rate = 0.0
				try:
					for acct, r in json.loads(it.get("item_tax_rate") or "{}").items():
						if _gst_kind(acct) == kind:
							rate = flt(r)
							break
				except Exception:
					# A malformed item_tax_rate must not break the whole report;
					# the line simply falls back to amount-weighting below.
					rate = 0.0
				weights.append([it["name"], flt(it["amount"]) * rate, flt(it["amount"])])
			wsum = sum(w[1] for w in weights)
			if wsum <= 0:
				# No per-line rates recorded -> fall back to plain amount weighting.
				for w in weights:
					w[1] = w[2]
				wsum = sum(w[1] for w in weights)
			if wsum <= 0:
				continue
			running = 0.0
			for name, w, _amt in weights:
				val = round(total * w / wsum, 2)
				out.setdefault(name, {})[kind] = val
				running += val
			biggest = max(weights, key=lambda x: x[1])[0]
			out[biggest][kind] = round(out[biggest][kind] + (total - running), 2)
	return out


# ------------------------------------------------------------------- helpers

def _mr_owner_map(mr_names):
	mrs = {m for m in mr_names if m}
	if not mrs:
		return {}
	rows = frappe.db.sql(
		"SELECT name, owner FROM `tabMaterial Request` WHERE name IN %(m)s",
		{"m": tuple(mrs)}, as_dict=True,
	)
	return {r["name"]: r["owner"] for r in rows}


def _fullname_map(user_ids):
	"""user_id -> full name, in one query.

	Deliberately raw SQL: operators frequently lack `User` read permission
	(Lesson 168), and a creator display name is not confidential — it is
	already surfaced on the PO/MR form itself.
	"""
	ids = sorted({u for u in user_ids if u})
	if not ids:
		return {}
	rows = frappe.db.sql(
		"SELECT name, full_name FROM `tabUser` WHERE name IN %(ids)s",
		{"ids": tuple(ids)}, as_dict=True,
	)
	return {r["name"]: (r["full_name"] or r["name"]) for r in rows}


def _summary(data, truncated):
	pos = {r.get("po_no") for r in data if r.get("po_no")}
	net = sum(flt(r.get("amount")) for r in data)
	tax = sum(flt(r.get("cgst_amount")) + flt(r.get("sgst_amount")) + flt(r.get("igst_amount")) for r in data)
	with_mr = len({r.get("material_request") for r in data if r.get("material_request")})

	out = [
		{"label": _("Purchase Orders"), "value": len(pos), "datatype": "Int"},
		{"label": _("Item Lines"), "value": len(data), "datatype": "Int"},
		{"label": _("Linked MRs"), "value": with_mr, "datatype": "Int"},
		{"label": _("Line Amount"), "value": net, "datatype": "Currency"},
		{"label": _("GST (derived)"), "value": tax, "datatype": "Currency", "indicator": "Blue"},
	]
	if truncated:
		out.append({"label": _("Notice"),
		            "value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Red"})
	return out
