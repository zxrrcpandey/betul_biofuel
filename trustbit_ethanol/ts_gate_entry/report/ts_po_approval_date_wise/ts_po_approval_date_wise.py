# TS PO Approval Date Wise — BBPL Report.xlsx spec (27 Jul 2026)
#
# One row per APPROVED Purchase Order, ordered by approval date.
# Spec columns (BBPL Report.xlsx -> "PO Approval date wise"):
#   S.No. | Approval date | Approved by | PO ID | Supplire name |
#   Supplier Address & Contact details | Cost center | PO Amount |
#   PO Amount With GST | P O Status
#
# Source: Purchase Order header custom fields ts_approved_date /
# ts_approved_by (both set by the BBF PO approval flow on final approval).
#   PO Amount          = net_total          (pre-tax, per spec "PO Net Total")
#   PO Amount With GST = rounded_total      (per spec "PO Rounded Total")
#   P O Status         = ts_approval_status (BBF approval state, user-confirmed
#                        27 Jul 2026 in preference to the ERPNext `status`)
#
# "Approved by" renders the approver's FULL NAME per spec — the User doctype
# has show_title_field_in_link=0 on this site, so a Link column would show the
# raw login id instead.
#
# Confidentiality: frappe.get_all applies the permission_query_conditions hook;
# the explicit ts_confidential filter below is belt-and-braces for
# non-allow-listed users (Lesson 297).

import frappe
from frappe import _
from frappe.utils import flt, getdate, strip_html

ROW_LIMIT = 5000


def execute(filters=None):
	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("Not permitted to read Purchase Order"), frappe.PermissionError)

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
	currency = "Company:company:default_currency"
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 65, "disable_total": 1},
		{"fieldname": "approval_date", "label": _("Approval Date"), "fieldtype": "Date", "width": 115},
		{"fieldname": "approved_by", "label": _("Approved By"), "fieldtype": "Data", "width": 180},
		{"fieldname": "po_no", "label": _("PO ID"), "fieldtype": "Link", "options": "Purchase Order", "width": 180},
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Link", "options": "Supplier", "width": 190},
		{"fieldname": "supplier_name", "label": _("Supplier Name"), "fieldtype": "Data", "width": 210},
		{"fieldname": "supplier_contact", "label": _("Supplier Address & Contact"), "fieldtype": "Data", "width": 300},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 185},
		{"fieldname": "po_amount", "label": _("PO Amount"), "fieldtype": "Currency", "options": currency, "width": 140},
		{"fieldname": "po_amount_with_gst", "label": _("PO Amount With GST"), "fieldtype": "Currency", "options": currency, "width": 165},
		{"fieldname": "approval_status", "label": _("PO Status"), "fieldtype": "Data", "width": 150},
	]


def get_data(filters):
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential

	conditions = {
		"docstatus": 1,
		"ts_approved_date": ["between", [_from_dt(filters), _to_dt(filters)]],
	}
	if filters.get("approved_by"):
		conditions["ts_approved_by"] = filters["approved_by"]
	if filters.get("supplier"):
		conditions["supplier"] = filters["supplier"]
	if filters.get("cost_center"):
		conditions["cost_center"] = filters["cost_center"]

	# Confidentiality leak-plug: exclude maize-confidential POs for non-allowed
	# users. (get_all also applies the PQC hook — this is defence in depth.)
	if not user_sees_confidential("Purchase Order"):
		conditions["ts_confidential"] = 0

	rows = frappe.get_all(
		"Purchase Order",
		filters=conditions,
		fields=[
			"ts_approved_date as approval_dt",
			"ts_approved_by as approver_id",
			"name as po_no",
			"supplier",
			"supplier_name",
			"cost_center",
			"net_total",
			"rounded_total",
			"grand_total",
			"ts_approval_status",
			"address_display",
			"contact_display",
			"contact_mobile",
			"contact_email",
		],
		order_by="ts_approved_date desc, name",
		limit=ROW_LIMIT + 1,
	)

	truncated = len(rows) > ROW_LIMIT
	if truncated:
		rows = rows[:ROW_LIMIT]

	names = _fullname_map([r.get("approver_id") for r in rows])
	out = []
	for i, r in enumerate(rows, start=1):
		# rounded_total is blank on a few older POs -> fall back to grand_total
		with_gst = flt(r.get("rounded_total")) or flt(r.get("grand_total"))
		out.append({
			"sr": i,
			"approval_date": getdate(r["approval_dt"]) if r.get("approval_dt") else None,
			"approved_by": names.get(r.get("approver_id") or "", ""),
			"po_no": r.get("po_no"),
			"supplier": r.get("supplier"),
			"supplier_name": r.get("supplier_name"),
			"supplier_contact": _contact_blob(r),
			"cost_center": r.get("cost_center"),
			"po_amount": flt(r.get("net_total")),
			"po_amount_with_gst": with_gst,
			"approval_status": r.get("ts_approval_status") or "",
		})

	return out, truncated


def _contact_blob(row):
	"""Flatten the PO's address + contact block into one readable cell.

	address_display is stored as HTML with <br> separators; convert those to
	commas BEFORE stripping tags, otherwise the lines run together.
	"""
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


def _fullname_map(user_ids):
	"""user_id -> full name, in one query.

	Deliberately raw SQL: operators frequently lack `User` read permission
	(Lesson 168), and an approver display name is not confidential — it is
	already surfaced on the PO form itself.
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
	total_net = sum(flt(r.get("po_amount")) for r in data)
	total_gross = sum(flt(r.get("po_amount_with_gst")) for r in data)
	approvers = len({r.get("approved_by") for r in data if r.get("approved_by")})
	suppliers = len({r.get("supplier") for r in data if r.get("supplier")})

	out = [
		{"label": _("Approved POs"), "value": len(data), "datatype": "Int"},
		{"label": _("Suppliers"), "value": suppliers, "datatype": "Int"},
		{"label": _("Approvers"), "value": approvers, "datatype": "Int"},
		{"label": _("Total (Net)"), "value": total_net, "datatype": "Currency"},
		{"label": _("Total (With GST)"), "value": total_gross, "datatype": "Currency", "indicator": "Green"},
	]
	if truncated:
		out.append({
			"label": _("Notice"),
			"value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
			"datatype": "Data",
			"indicator": "Red",
		})
	return out


def _from_dt(filters):
	return f"{getdate(filters['from_date'])} 00:00:00"


def _to_dt(filters):
	return f"{getdate(filters['to_date'])} 23:59:59"
