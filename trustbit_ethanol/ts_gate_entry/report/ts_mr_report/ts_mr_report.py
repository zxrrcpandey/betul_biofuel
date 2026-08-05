# TS MR Report — BBPL Report.xlsx spec (27 Jul 2026), sheet "MR report"
#
# One row per Material Request Item line, with its downstream fulfilment.
# Spec columns:
#   S.No. | MR ID | doc.status | Item Code | Item Name | Description |
#   Item Remark | Quantity | Completed Qty | Received Qty | Cost Center |
#   Define Use Location | Responcible person | panding quantity
#
# Completed Qty / Received Qty — IMPORTANT
# ----------------------------------------
# The spec sources these as "Ordered Qty. From PO against MR" and "Received Qty.
# From PR against Link MR". The obvious implementation — summing PO Item /
# PR Item rows joined on `material_request_item` — is WRONG on this data: only
# 273 of 763 PO Item rows and 66 PR Item rows actually carry that line link, so
# a derived sum returns 0 for roughly three quarters of lines. Measured against
# ERPNext's own maintained rollups on 1,581 submitted MR lines, the derived
# figure disagreed on 1,185 of them — and in every sampled case ERPNext held the
# correct value while the derived one was 0.
#
# So this report reads `Material Request Item.ordered_qty` / `.received_qty`,
# which ARE the "from PO / from PR" rollups the spec is describing — ERPNext
# maintains them on PO and PR submit. Same reasoning as the Open PO Report,
# which reads poi.received_qty rather than re-deriving from Purchase Receipts.
#
# Confidentiality: confidential MRs are hidden from non-allow-listed users via
# confidential_sql_clause (Lesson 297).

import frappe
from frappe import _
from frappe.utils import flt, getdate, strip_html

ROW_LIMIT = 5000


def execute(filters=None):
	if not frappe.has_permission("Material Request", "read"):
		frappe.throw(_("Not permitted to read Material Request"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	data, truncated = get_data(filters)
	return get_columns(), data, None, None, _summary(data, truncated)


def _validate_filters(filters):
	frm, to = filters.get("from_date"), filters.get("to_date")
	if frm and to and getdate(frm) > getdate(to):
		frappe.throw(_("From Date cannot be after To Date"))


def get_columns():
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 65, "disable_total": 1},
		{"fieldname": "mr_no", "label": _("MR ID"), "fieldtype": "Link", "options": "Material Request", "width": 190},
		{"fieldname": "doc_status", "label": _("Doc Status"), "fieldtype": "Data", "width": 130},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 210},
		{"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "width": 240},
		{"fieldname": "item_remark", "label": _("Item Remark"), "fieldtype": "Data", "width": 200},
		# MR lines mix UOMs -> column sums would be meaningless.
		{"fieldname": "qty", "label": _("Quantity"), "fieldtype": "Float", "width": 100, "precision": 2, "disable_total": 1},
		{"fieldname": "completed_qty", "label": _("Completed Qty"), "fieldtype": "Float", "width": 120, "precision": 2, "disable_total": 1},
		{"fieldname": "received_qty", "label": _("Received Qty"), "fieldtype": "Float", "width": 115, "precision": 2, "disable_total": 1},
		{"fieldname": "pending_qty", "label": _("Pending Quantity"), "fieldtype": "Float", "width": 130, "precision": 2, "disable_total": 1},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Data", "width": 75},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 185},
		{"fieldname": "use_location", "label": _("Define Use Location"), "fieldtype": "Data", "width": 180},
		{"fieldname": "responsible_person", "label": _("Responsible Person"), "fieldtype": "Data", "width": 175},
	]


def get_data(filters):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause

	params = {"limit": ROW_LIMIT + 1}
	# Cancelled MRs excluded by default; the toggle brings them back for audits.
	clauses = ["mr.docstatus IN (0, 1, 2)"] if filters.get("include_cancelled") else ["mr.docstatus IN (0, 1)"]

	conf = confidential_sql_clause("mr", doctype="Material Request")
	if conf:
		clauses.append(conf.strip()[4:].strip())

	match_cond = frappe.build_match_conditions("Material Request")
	if match_cond:
		clauses.append("(%s)" % match_cond.replace("`tabMaterial Request`", "mr"))

	if filters.get("from_date"):
		clauses.append("mr.transaction_date >= %(from_date)s")
		params["from_date"] = getdate(filters["from_date"])
	if filters.get("to_date"):
		clauses.append("mr.transaction_date <= %(to_date)s")
		params["to_date"] = getdate(filters["to_date"])
	if filters.get("cost_center"):
		clauses.append("mri.cost_center = %(cost_center)s")
		params["cost_center"] = filters["cost_center"]
	if filters.get("item_code"):
		clauses.append("mri.item_code = %(item_code)s")
		params["item_code"] = filters["item_code"]
	if filters.get("status"):
		clauses.append("mr.status = %(status)s")
		params["status"] = filters["status"]
	if filters.get("material_request_type"):
		clauses.append("mr.material_request_type = %(mr_type)s")
		params["mr_type"] = filters["material_request_type"]
	if filters.get("only_pending"):
		clauses.append("mri.qty > IFNULL(mri.received_qty, 0)")

	where = " AND ".join(clauses)

	rows = frappe.db.sql(
		f"""
		SELECT
			mr.name                        AS mr_no,
			mr.status                      AS doc_status,
			mri.item_code                  AS item_code,
			mri.item_name                  AS item_name,
			mri.description                AS description,
			mri.ts_item_remark             AS item_remark,
			mri.qty                        AS qty,
			IFNULL(mri.ordered_qty, 0)     AS completed_qty,
			IFNULL(mri.received_qty, 0)    AS received_qty,
			mri.uom                        AS uom,
			mri.cost_center                AS cost_center,
			mri.ts_delivery_location       AS use_location,
			mr.owner                       AS creator_id
		FROM `tabMaterial Request` mr
		INNER JOIN `tabMaterial Request Item` mri ON mri.parent = mr.name
		WHERE {where}
		ORDER BY mr.transaction_date DESC, mr.name, mri.idx
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)

	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]

	names = _fullname_map([r.get("creator_id") for r in rows])
	for i, r in enumerate(rows, start=1):
		r["sr"] = i
		# Never negative: an over-receipt would otherwise read as "-5 pending".
		r["pending_qty"] = max(flt(r.get("qty")) - flt(r.get("received_qty")), 0.0)
		for f in ("description", "item_remark"):
			if r.get(f):
				r[f] = " ".join(strip_html(r[f]).split())
		r["responsible_person"] = names.get(r.pop("creator_id", None) or "", "")

	return rows, truncated


def _fullname_map(user_ids):
	"""user_id -> full name, in one query.

	Deliberately raw SQL: operators frequently lack `User` read permission
	(Lesson 168), and a creator display name is not confidential — it is
	already surfaced on the MR form itself.
	"""
	ids = sorted({u for u in user_ids if u})
	if not ids:
		return {}
	rows = frappe.db.sql(
		"SELECT name, full_name FROM `tabUser` WHERE name IN %(ids)s",
		{"ids": tuple(ids)},
		as_dict=True,
	)
	return {r["name"]: (r["full_name"] or r["name"]) for r in rows}


def _summary(data, truncated):
	mrs = {r.get("mr_no") for r in data if r.get("mr_no")}
	pending_lines = sum(1 for r in data if flt(r.get("pending_qty")) > 0)
	unordered = sum(1 for r in data if flt(r.get("completed_qty")) <= 0)

	out = [
		{"label": _("Material Requests"), "value": len(mrs), "datatype": "Int"},
		{"label": _("Item Lines"), "value": len(data), "datatype": "Int"},
		{"label": _("Lines Pending Receipt"), "value": pending_lines, "datatype": "Int",
		 "indicator": "Orange" if pending_lines else "Green"},
		{"label": _("Lines Not Yet Ordered"), "value": unordered, "datatype": "Int",
		 "indicator": "Red" if unordered else "Green"},
		{"label": _("Distinct Items"), "value": len({r.get("item_code") for r in data if r.get("item_code")}), "datatype": "Int"},
	]
	if truncated:
		out.append({"label": _("Notice"),
		            "value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Red"})
	return out
