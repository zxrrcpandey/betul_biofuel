# TS MR Approval Date Wise — BBPL Report.xlsx spec (27 Jul 2026)
#
# One row per Material Request Item line of an APPROVED Material Request,
# ordered by approval date. Spec columns (BBPL Report.xlsx -> "MR Approval
# date wise"):
#   S.No. | Approval Date | Approved by | MR Date | MR ID | Item Code |
#   Item name | Description | Qty | MR creator | Cost center
#
# Source: MR header custom fields ts_mr_approved_date / ts_mr_approved_by
# (set by the BBF MR approval flow) joined to its item lines. "Approved by"
# and "MR creator" render the person's FULL NAME per spec — the User doctype
# has show_title_field_in_link=0 on this site, so a Link column would show
# the raw login id instead.
#
# Confidentiality: confidential MRs are hidden from non-allow-listed users via
# confidential_sql_clause (Lesson 297 — raw SQL bypasses the
# permission_query_conditions hook).

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
	if not filters.get("from_date"):
		frappe.throw(_("From Date is required"))
	if not filters.get("to_date"):
		frappe.throw(_("To Date is required"))
	if getdate(filters["from_date"]) > getdate(filters["to_date"]):
		frappe.throw(_("From Date cannot be after To Date"))


def get_columns():
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 65, "disable_total": 1},
		{"fieldname": "approval_date", "label": _("Approval Date"), "fieldtype": "Date", "width": 115},
		{"fieldname": "approved_by", "label": _("Approved By"), "fieldtype": "Data", "width": 180},
		{"fieldname": "mr_date", "label": _("MR Date"), "fieldtype": "Date", "width": 105},
		{"fieldname": "mr_no", "label": _("MR ID"), "fieldtype": "Link", "options": "Material Request", "width": 190},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 220},
		{"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "width": 260},
		# MR lines mix UOMs -> a column sum would be meaningless.
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 95, "precision": 2, "disable_total": 1},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Data", "width": 75},
		{"fieldname": "mr_creator", "label": _("MR Creator"), "fieldtype": "Data", "width": 175},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 185},
	]


def get_data(filters):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause

	params = {
		"from_dt": f"{getdate(filters['from_date'])} 00:00:00",
		"to_dt": f"{getdate(filters['to_date'])} 23:59:59",
		"limit": ROW_LIMIT + 1,
	}
	clauses = [
		"mr.docstatus = 1",
		"mr.ts_mr_approved_date IS NOT NULL",
		"mr.ts_mr_approved_date BETWEEN %(from_dt)s AND %(to_dt)s",
	]

	# Confidentiality leak-plug — fragment arrives as " AND (...)"; drop the AND.
	conf = confidential_sql_clause("mr", doctype="Material Request")
	if conf:
		clauses.append(conf.strip()[4:].strip())

	match_cond = frappe.build_match_conditions("Material Request")
	if match_cond:
		clauses.append("(%s)" % match_cond.replace("`tabMaterial Request`", "mr"))

	if filters.get("cost_center"):
		clauses.append("mri.cost_center = %(cost_center)s")
		params["cost_center"] = filters["cost_center"]
	if filters.get("approved_by"):
		clauses.append("mr.ts_mr_approved_by = %(approved_by)s")
		params["approved_by"] = filters["approved_by"]
	if filters.get("material_request_type"):
		clauses.append("mr.material_request_type = %(mr_type)s")
		params["mr_type"] = filters["material_request_type"]

	where = " AND ".join(clauses)

	rows = frappe.db.sql(
		f"""
		SELECT
			DATE(mr.ts_mr_approved_date) AS approval_date,
			mr.ts_mr_approved_by         AS approver_id,
			mr.transaction_date          AS mr_date,
			mr.name                      AS mr_no,
			mri.item_code                AS item_code,
			mri.item_name                AS item_name,
			mri.description              AS description,
			mri.qty                      AS qty,
			mri.uom                      AS uom,
			mr.owner                     AS creator_id,
			mri.cost_center              AS cost_center
		FROM `tabMaterial Request` mr
		INNER JOIN `tabMaterial Request Item` mri ON mri.parent = mr.name
		WHERE {where}
		ORDER BY mr.ts_mr_approved_date DESC, mr.name, mri.idx
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)

	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]

	names = _fullname_map(
		[r.get("approver_id") for r in rows] + [r.get("creator_id") for r in rows]
	)
	for i, r in enumerate(rows, start=1):
		r["sr"] = i
		if r.get("description"):
			r["description"] = " ".join(strip_html(r["description"]).split())
		r["approved_by"] = names.get(r.pop("approver_id", None) or "", "")
		r["mr_creator"] = names.get(r.pop("creator_id", None) or "", "")

	return rows, truncated


def _fullname_map(user_ids):
	"""user_id -> full name, in one query.

	Deliberately raw SQL: operators frequently lack `User` read permission
	(Lesson 168), and an approver/creator display name is not confidential —
	it is already surfaced on the MR form itself.
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
	distinct_mrs = len({r.get("mr_no") for r in data if r.get("mr_no")})
	distinct_items = len({r.get("item_code") for r in data if r.get("item_code")})
	distinct_ccs = len({r.get("cost_center") for r in data if r.get("cost_center")})
	approvers = len({r.get("approved_by") for r in data if r.get("approved_by")})

	out = [
		{"label": _("Approved MRs"), "value": distinct_mrs, "datatype": "Int"},
		{"label": _("Item Lines"), "value": len(data), "datatype": "Int"},
		{"label": _("Distinct Items"), "value": distinct_items, "datatype": "Int"},
		{"label": _("Cost Centers"), "value": distinct_ccs, "datatype": "Int"},
		{"label": _("Approvers"), "value": approvers, "datatype": "Int"},
	]
	if truncated:
		out.append({
			"label": _("Notice"),
			"value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
			"datatype": "Data",
			"indicator": "Red",
		})
	return out
