# Copyright (c) 2026, Trustbit Software
# TS Department Release Variance (v2.22 Phase 2) — planned vs ACTUAL released
# quantity per department, per run, per item, for the Single-flow department
# release. One row per Release Slot item; variance = actual - planned.

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return _columns(), _data(filters)


def _columns():
	return [
		{"label": _("Run"), "fieldname": "production_entry", "fieldtype": "Link",
		 "options": "TS Production Entry", "width": 150},
		{"label": _("Department"), "fieldname": "category", "fieldtype": "Link",
		 "options": "TS Production BOM Category", "width": 140},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link",
		 "options": "Item", "width": 120},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link",
		 "options": "Warehouse", "width": 150},
		{"label": _("Planned"), "fieldname": "planned_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Actual Released"), "fieldname": "actual_qty", "fieldtype": "Float", "width": 120},
		{"label": _("Variance"), "fieldname": "variance", "fieldtype": "Float", "width": 100},
		{"label": _("Variance %"), "fieldname": "variance_pct", "fieldtype": "Percent", "width": 100},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Data", "width": 70},
		{"label": _("Released By"), "fieldname": "released_by", "fieldtype": "Link",
		 "options": "User", "width": 160},
		{"label": _("Released At"), "fieldname": "released_at", "fieldtype": "Datetime", "width": 150},
	]


def _data(filters):
	conds = ["1=1"]
	vals = {}
	if filters.get("from_date"):
		conds.append("DATE(s.creation) >= %(from_date)s"); vals["from_date"] = filters.from_date
	if filters.get("to_date"):
		conds.append("DATE(s.creation) <= %(to_date)s"); vals["to_date"] = filters.to_date
	if filters.get("category"):
		conds.append("s.category = %(category)s"); vals["category"] = filters.category
	if filters.get("production_entry"):
		conds.append("s.production_entry = %(production_entry)s"); vals["production_entry"] = filters.production_entry
	if filters.get("status"):
		conds.append("s.status = %(status)s"); vals["status"] = filters.status

	rows = frappe.db.sql("""
		SELECT s.production_entry, s.category, s.status, s.released_by, s.released_at,
		       i.item_code, i.item_name, i.warehouse, i.uom,
		       i.planned_qty, i.actual_qty
		FROM `tabTS Production Release Slot Item` i
		JOIN `tabTS Production Release Slot` s ON s.name = i.parent
		WHERE {conds}
		ORDER BY s.production_entry DESC, s.category, i.idx
	""".format(conds=" AND ".join(conds)), vals, as_dict=True)

	out = []
	for r in rows:
		planned = flt(r.planned_qty)
		actual = flt(r.actual_qty)
		# only-Released slots have a meaningful actual; Pending slots show planned only
		released = r.status == "Released"
		var = (actual - planned) if released else 0.0
		var_pct = (var / planned * 100.0) if (released and planned) else 0.0
		out.append({
			"production_entry": r.production_entry, "category": r.category, "status": r.status,
			"item_code": r.item_code, "item_name": r.item_name, "warehouse": r.warehouse,
			"planned_qty": planned, "actual_qty": actual if released else None,
			"variance": var if released else None,
			"variance_pct": var_pct if released else None,
			"uom": r.uom, "released_by": r.released_by, "released_at": r.released_at,
		})
	return out
