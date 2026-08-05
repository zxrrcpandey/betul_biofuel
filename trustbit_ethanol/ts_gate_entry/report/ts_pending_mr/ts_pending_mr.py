# TS Pending MR — BBPL Report.xlsx spec (27 Jul 2026), sheet "Panding MR"
#
# One row per Material Request Item line of a Material Request that is still
# awaiting somebody's approval.
# Spec columns:
#   S.No. | MR Date | Panding by | Cost center | MR ID | Item Code |
#   Item name | Description | Qty | MR creator
#
# "Pending" = ts_mr_status LIKE 'Pending%' (the BBF MR approval flow writes the
# waiting step into that field, e.g. "Pending Department Head", "Pending AVP",
# "Pending CEO"). Cancelled MRs are excluded: demo carries 5 cancelled documents
# whose status text is still "Pending ..." — stale labels on dead documents,
# never work-in-hand. Drafts ARE included, because in this flow an MR sits at
# docstatus 0 for most of its approval journey (112 of the 126 live pending
# documents on demo are drafts).
#
# Confidentiality: confidential MRs are hidden from non-allow-listed users via
# confidential_sql_clause (Lesson 297 — raw SQL bypasses the
# permission_query_conditions hook).

import frappe
from frappe import _
from frappe.utils import getdate, strip_html

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
		{"fieldname": "mr_date", "label": _("MR Date"), "fieldtype": "Date", "width": 105},
		{"fieldname": "pending_by", "label": _("Pending By"), "fieldtype": "Data", "width": 200},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 185},
		{"fieldname": "mr_no", "label": _("MR ID"), "fieldtype": "Link", "options": "Material Request", "width": 190},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 220},
		{"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "width": 260},
		# MR lines mix UOMs (Kg / Nos / PKT / Ltr) -> a column sum is meaningless.
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 95, "precision": 2, "disable_total": 1},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Data", "width": 75},
		{"fieldname": "mr_creator", "label": _("MR Creator"), "fieldtype": "Data", "width": 175},
	]


def get_data(filters):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause

	params = {"limit": ROW_LIMIT + 1}
	clauses = [
		# docstatus 2 excluded: a cancelled MR keeps its old "Pending ..." label.
		"mr.docstatus IN (0, 1)",
		"mr.ts_mr_status LIKE 'Pending%%'",
	]

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
	if filters.get("pending_status"):
		clauses.append("mr.ts_mr_status = %(pending_status)s")
		params["pending_status"] = filters["pending_status"]
	if filters.get("material_request_type"):
		clauses.append("mr.material_request_type = %(mr_type)s")
		params["mr_type"] = filters["material_request_type"]

	where = " AND ".join(clauses)

	rows = frappe.db.sql(
		f"""
		SELECT
			mr.transaction_date AS mr_date,
			mr.ts_mr_status     AS pending_by,
			mri.cost_center     AS cost_center,
			mr.name             AS mr_no,
			mri.item_code       AS item_code,
			mri.item_name       AS item_name,
			mri.description     AS description,
			mri.qty             AS qty,
			mri.uom             AS uom,
			mr.owner            AS creator_id
		FROM `tabMaterial Request` mr
		INNER JOIN `tabMaterial Request Item` mri ON mri.parent = mr.name
		WHERE {where}
		ORDER BY mr.transaction_date ASC, mr.name, mri.idx
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
		if r.get("description"):
			r["description"] = " ".join(strip_html(r["description"]).split())
		r["mr_creator"] = names.get(r.pop("creator_id", None) or "", "")

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
	stages = {}
	for r in data:
		if r.get("mr_no"):
			stages.setdefault(r["pending_by"], set()).add(r["mr_no"])
	worst = max(stages.items(), key=lambda kv: len(kv[1])) if stages else None

	out = [
		{"label": _("Pending MRs"), "value": len(mrs), "datatype": "Int",
		 "indicator": "Orange" if mrs else "Green"},
		{"label": _("Item Lines"), "value": len(data), "datatype": "Int"},
		{"label": _("Cost Centers"), "value": len({r.get("cost_center") for r in data if r.get("cost_center")}), "datatype": "Int"},
		{"label": _("Approval Stages"), "value": len(stages), "datatype": "Int"},
	]
	if worst:
		out.append({"label": _("Biggest Queue"),
		            "value": "{0} ({1})".format(worst[0], len(worst[1])),
		            "datatype": "Data", "indicator": "Orange"})
	if truncated:
		out.append({"label": _("Notice"),
		            "value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Red"})
	return out
