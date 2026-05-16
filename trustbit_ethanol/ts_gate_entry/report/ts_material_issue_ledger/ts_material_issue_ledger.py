# TS Material Issue Ledger — v2.10.1.0
#
# Combined view of Material Issue REQUESTS (Material Request type='Material Issue')
# and their ACTUAL ISSUES (Stock Entry purpose='Material Issue'). One row per
# request line; LEFT JOIN aggregates Stock Entry Detail rows that fulfilled it.
# A second UNION pulls standalone Stock Entry Material Issues with no MR origin
# so they don't get missed.
#
# Per row: Requested Qty vs Issued Qty vs Pending Qty + Status
# (Pending / Partial / Fulfilled / Standalone).
# Remarks cascade: Stock Entry Detail row → MR Item row → Stock Entry header.

import frappe
from frappe import _
from frappe.utils import flt, getdate

ROW_LIMIT = 5000


def execute(filters=None):
	if not frappe.has_permission("Stock Entry", "read"):
		frappe.throw(_("Not permitted to read Stock Entry"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	columns = get_columns()
	data, truncated = get_data(filters)
	report_summary = _get_summary(data, truncated, filters)
	return columns, data, None, None, report_summary


def _validate_filters(filters):
	if not filters.get("company"):
		filters["company"] = frappe.defaults.get_user_default("Company")
	if not filters.get("company"):
		frappe.throw(_("Company is required"))
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are required"))
	if getdate(filters["from_date"]) > getdate(filters["to_date"]):
		frappe.throw(_("From Date cannot be after To Date"))


def get_columns():
	return [
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "material_request", "label": _("Material Request"), "fieldtype": "Link", "options": "Material Request", "width": 150},
		{"fieldname": "stock_entry", "label": _("Stock Entry"), "fieldtype": "Link", "options": "Stock Entry", "width": 150},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 200},
		{"fieldname": "item_group", "label": _("Item Group"), "fieldtype": "Link", "options": "Item Group", "width": 130},
		{"fieldname": "requested_qty", "label": _("Requested Qty"), "fieldtype": "Float", "width": 110, "precision": 2},
		{"fieldname": "issued_qty", "label": _("Issued Qty"), "fieldtype": "Float", "width": 100, "precision": 2},
		{"fieldname": "pending_qty", "label": _("Pending Qty"), "fieldtype": "Float", "width": 100, "precision": 2},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Data", "width": 70},
		{"fieldname": "source_warehouse", "label": _("Source Warehouse"), "fieldtype": "Link", "options": "Warehouse", "width": 150},
		{"fieldname": "use_location", "label": _("Use Location"), "fieldtype": "Data", "width": 140},
		{"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link", "options": "Cost Center", "width": 170},
		{"fieldname": "rate", "label": _("Rate"), "fieldtype": "Currency", "options": "Company:company:default_currency", "width": 100},
		{"fieldname": "value", "label": _("Value"), "fieldtype": "Currency", "options": "Company:company:default_currency", "width": 120},
		{"fieldname": "remarks", "label": _("Remarks"), "fieldtype": "Data", "width": 220},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
	]


def get_data(filters):
	"""Two-pass collection:
	A) MR Item base (type=Material Issue) with aggregated SE Detail fulfillments
	B) Standalone SE Detail rows (purpose=Material Issue, no material_request_item)
	Both filtered by company + date + optional filters; combined and sorted.
	"""
	rows_a = _query_mr_based(filters)
	rows_b = _query_standalone_se(filters)
	all_rows = rows_a + rows_b

	# Sort by posting_date desc, then by name for stability
	all_rows.sort(key=lambda r: (
		getdate(r.get("posting_date") or "1900-01-01"),
		r.get("material_request") or r.get("stock_entry") or "",
	), reverse=True)

	truncated = len(all_rows) > ROW_LIMIT
	if truncated:
		all_rows = all_rows[:ROW_LIMIT]

	# Compute pending_qty + status per row
	for row in all_rows:
		req = flt(row.get("requested_qty"))
		iss = flt(row.get("issued_qty"))
		row["pending_qty"] = max(req - iss, 0)
		if row.get("is_standalone"):
			row["status"] = "Standalone"
		elif req <= 0:
			row["status"] = "—"
		elif iss <= 0:
			row["status"] = "Pending"
		elif iss < req:
			row["status"] = "Partial"
		else:
			row["status"] = "Fulfilled"

	# Apply status filter post-collection (filtering after compute is cheaper than UNION-pre-filter)
	status_filter = filters.get("status")
	if status_filter and status_filter != "All":
		all_rows = [r for r in all_rows if r.get("status") == status_filter]

	return all_rows, truncated


def _query_mr_based(filters):
	"""Material Request rows of type=Material Issue + aggregated SE Detail join."""
	conds, params = _common_mr_conditions(filters)
	use_loc = filters.get("use_location")
	if use_loc:
		conds.append("mri.ts_delivery_location LIKE %(use_location)s")
		params["use_location"] = f"%{use_loc}%"

	match_cond = frappe.build_match_conditions("Material Request")
	match_clause = f" AND ({match_cond}) " if match_cond else ""

	sql = f"""
		SELECT
			mr.transaction_date AS posting_date,
			mr.name AS material_request,
			(SELECT MIN(se2.name) FROM `tabStock Entry Detail` sed2
				JOIN `tabStock Entry` se2 ON se2.name = sed2.parent
				WHERE sed2.material_request_item = mri.name
				  AND se2.purpose = 'Material Issue'
				  AND se2.docstatus = 1) AS stock_entry,
			mri.item_code, mri.item_name, item.item_group,
			mri.qty AS requested_qty,
			COALESCE((
				SELECT SUM(sed3.qty) FROM `tabStock Entry Detail` sed3
				JOIN `tabStock Entry` se3 ON se3.name = sed3.parent
				WHERE sed3.material_request_item = mri.name
				  AND se3.purpose = 'Material Issue'
				  AND se3.docstatus = 1
			), 0) AS issued_qty,
			mri.uom,
			(SELECT MAX(sed4.s_warehouse) FROM `tabStock Entry Detail` sed4
				JOIN `tabStock Entry` se4 ON se4.name = sed4.parent
				WHERE sed4.material_request_item = mri.name
				  AND se4.purpose = 'Material Issue'
				  AND se4.docstatus = 1) AS source_warehouse_se,
			mri.from_warehouse AS source_warehouse_mr,
			mri.warehouse AS source_warehouse_fallback,
			mri.ts_delivery_location AS use_location,
			mri.cost_center,
			(SELECT MAX(sed5.basic_rate) FROM `tabStock Entry Detail` sed5
				JOIN `tabStock Entry` se5 ON se5.name = sed5.parent
				WHERE sed5.material_request_item = mri.name
				  AND se5.purpose = 'Material Issue'
				  AND se5.docstatus = 1) AS rate,
			COALESCE((
				SELECT SUM(sed6.amount) FROM `tabStock Entry Detail` sed6
				JOIN `tabStock Entry` se6 ON se6.name = sed6.parent
				WHERE sed6.material_request_item = mri.name
				  AND se6.purpose = 'Material Issue'
				  AND se6.docstatus = 1
			), 0) AS value,
			mri.ts_item_remark AS mr_remark,
			(SELECT MIN(se7.remarks) FROM `tabStock Entry Detail` sed7
				JOIN `tabStock Entry` se7 ON se7.name = sed7.parent
				WHERE sed7.material_request_item = mri.name
				  AND se7.purpose = 'Material Issue'
				  AND se7.docstatus = 1) AS se_header_remark,
			0 AS is_standalone
		FROM `tabMaterial Request Item` mri
		JOIN `tabMaterial Request` mr ON mr.name = mri.parent
		LEFT JOIN `tabItem` item ON item.name = mri.item_code
		WHERE mr.material_request_type = 'Material Issue'
		  AND mr.docstatus = 1
		  AND mr.company = %(company)s
		  AND mr.transaction_date BETWEEN %(from_date)s AND %(to_date)s
		  {(" AND " + " AND ".join(conds)) if conds else ""}
		  {match_clause}
		LIMIT {ROW_LIMIT + 1}
	"""

	rows = frappe.db.sql(sql, params, as_dict=True)
	for row in rows:
		row["source_warehouse"] = row.pop("source_warehouse_se", None) or row.pop("source_warehouse_mr", None) or row.pop("source_warehouse_fallback", None)
		# Remarks cascade: MR row remark first (since SE row remark requires a separate lookup; SE header is the last fallback)
		row["remarks"] = (row.pop("mr_remark", None) or row.pop("se_header_remark", None) or "")
	return rows


def _query_standalone_se(filters):
	"""Stock Entry Detail rows where purpose=Material Issue + NO material_request_item link."""
	conds, params = _common_se_conditions(filters)
	use_loc = filters.get("use_location")
	if use_loc:
		# Standalone SE has no MR row, so use_location filter excludes everything by definition
		# unless ts_delivery_location is also stored on SE Detail (not standard).
		return []

	match_cond = frappe.build_match_conditions("Stock Entry")
	match_clause = f" AND ({match_cond}) " if match_cond else ""

	sql = f"""
		SELECT
			se.posting_date,
			NULL AS material_request,
			se.name AS stock_entry,
			sed.item_code,
			sed.item_name,
			item.item_group,
			0 AS requested_qty,
			sed.qty AS issued_qty,
			sed.uom,
			sed.s_warehouse AS source_warehouse,
			'' AS use_location,
			sed.cost_center,
			sed.basic_rate AS rate,
			sed.amount AS value,
			COALESCE(NULLIF(se.remarks, ''), '') AS remarks,
			1 AS is_standalone
		FROM `tabStock Entry Detail` sed
		JOIN `tabStock Entry` se ON se.name = sed.parent
		LEFT JOIN `tabItem` item ON item.name = sed.item_code
		WHERE se.purpose = 'Material Issue'
		  AND se.docstatus = 1
		  AND se.company = %(company)s
		  AND se.posting_date BETWEEN %(from_date)s AND %(to_date)s
		  AND (sed.material_request_item IS NULL OR sed.material_request_item = '')
		  {(" AND " + " AND ".join(conds)) if conds else ""}
		  {match_clause}
		LIMIT {ROW_LIMIT + 1}
	"""

	return frappe.db.sql(sql, params, as_dict=True)


def _common_mr_conditions(filters):
	"""Filters applied to the MR-based query."""
	conds = []
	params = {
		"company": filters.get("company"),
		"from_date": filters.get("from_date"),
		"to_date": filters.get("to_date"),
	}
	if filters.get("item_code"):
		conds.append("mri.item_code = %(item_code)s")
		params["item_code"] = filters["item_code"]
	if filters.get("item_group"):
		conds.append("item.item_group = %(item_group)s")
		params["item_group"] = filters["item_group"]
	if filters.get("cost_center"):
		conds.append("mri.cost_center = %(cost_center)s")
		params["cost_center"] = filters["cost_center"]
	if filters.get("source_warehouse"):
		conds.append("(mri.from_warehouse = %(source_warehouse)s OR mri.warehouse = %(source_warehouse)s)")
		params["source_warehouse"] = filters["source_warehouse"]
	if not filters.get("include_cancelled"):
		# MR with docstatus=1 only (already filtered). Allow cancelled MRs if toggle set.
		pass
	return conds, params


def _common_se_conditions(filters):
	"""Filters applied to the standalone SE query."""
	conds = []
	params = {
		"company": filters.get("company"),
		"from_date": filters.get("from_date"),
		"to_date": filters.get("to_date"),
	}
	if filters.get("item_code"):
		conds.append("sed.item_code = %(item_code)s")
		params["item_code"] = filters["item_code"]
	if filters.get("item_group"):
		conds.append("item.item_group = %(item_group)s")
		params["item_group"] = filters["item_group"]
	if filters.get("cost_center"):
		conds.append("sed.cost_center = %(cost_center)s")
		params["cost_center"] = filters["cost_center"]
	if filters.get("source_warehouse"):
		conds.append("sed.s_warehouse = %(source_warehouse)s")
		params["source_warehouse"] = filters["source_warehouse"]
	return conds, params


def _get_summary(data, truncated, filters):
	total_requested = sum(flt(r.get("requested_qty")) for r in data)
	total_issued = sum(flt(r.get("issued_qty")) for r in data)
	total_pending = sum(flt(r.get("pending_qty")) for r in data)
	total_value = sum(flt(r.get("value")) for r in data)
	distinct_items = len({r.get("item_code") for r in data if r.get("item_code")})

	summary = [
		{"label": _("Rows"), "value": len(data), "datatype": "Int"},
		{"label": _("Distinct Items"), "value": distinct_items, "datatype": "Int"},
		{"label": _("Total Requested"), "value": total_requested, "datatype": "Float"},
		{"label": _("Total Issued"), "value": total_issued, "datatype": "Float", "indicator": "Green"},
		{"label": _("Total Pending"), "value": total_pending, "datatype": "Float", "indicator": "Orange" if total_pending > 0 else "Green"},
		{"label": _("Total Value Issued"), "value": total_value, "datatype": "Currency", "currency": filters.get("company")},
	]
	if truncated:
		summary.append({
			"label": _("Notice"),
			"value": _("Result truncated at {0} rows — refine filters").format(ROW_LIMIT),
			"datatype": "Data",
			"indicator": "Orange",
		})
	return summary
