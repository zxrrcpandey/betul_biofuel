# TS Open RGP Register — one row per submitted TS Returnable Gate Pass.
# SINGLE-TABLE query over `tabTS Returnable Gate Pass` only (items_count via a
# correlated subquery on its own child `tabTS RGP Item`) — deliberately NO chain
# hop (no MR/PO joins), so the report_utils invariant is satisfied by not
# hopping at all. Roles are applied post-migrate via ORM by setup_rgp.py (L281).

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from trustbit_ethanol.ts_gate_entry.report import report_utils as ru  # noqa — mandatory import per house invariant L409-412; no hop in this report


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"fieldname": "rgp",
			"label": _("RGP"),
			"fieldtype": "Link",
			"options": "TS Returnable Gate Pass",
			"width": 150,
		},
		{
			# Deliberately Data, NOT Link -> Supplier: a Link column gates the
			# whole report for users without Supplier read permission.
			"fieldname": "vendor",
			"label": _("Vendor"),
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"fieldname": "challan_date",
			"label": _("Challan Date"),
			"fieldtype": "Date",
			"width": 110,
		},
		{
			"fieldname": "expected_return_date",
			"label": _("Expected Return"),
			"fieldtype": "Date",
			"width": 110,
		},
		{
			"fieldname": "items_count",
			"label": _("Items"),
			"fieldtype": "Int",
			"width": 70,
		},
		{
			"fieldname": "balance_qty",
			"label": _("Balance Qty"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 115,
		},
		{
			"fieldname": "value",
			"label": _("Value"),
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"fieldname": "age_days",
			"label": _("Age (Days)"),
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"fieldname": "status",
			"label": _("Status"),
			"fieldtype": "Data",
			"width": 150,
		},
	]


def get_data(filters):
	conditions, values = build_conditions(filters)

	rows = frappe.db.sql(
		"""
		SELECT
			rgp.name                 AS rgp,
			rgp.supplier_name        AS vendor,
			rgp.challan_date         AS challan_date,
			rgp.expected_return_date AS expected_return_date,
			(SELECT COUNT(*)
			   FROM `tabTS RGP Item` it
			  WHERE it.parent = rgp.name
			    AND it.parenttype = 'TS Returnable Gate Pass') AS items_count,
			rgp.total_balance        AS balance_qty,
			rgp.total_taxable_value  AS value,
			rgp.status               AS status
		FROM `tabTS Returnable Gate Pass` rgp
		WHERE rgp.docstatus = 1 {conditions}
		ORDER BY rgp.expected_return_date ASC, rgp.name ASC
		""".format(conditions=conditions),
		values,
		as_dict=True,
	)

	# age_days is computed in PYTHON on the server-local (IST) date — NEVER via
	# SQL NOW()/CURDATE(): prod SQL NOW() is UTC (5.5 h skew, known shipped-bug
	# class). Signed: negative = still due, positive = days overdue.
	today = getdate(nowdate())
	overdue_only = cint(filters.get("overdue_only"))

	data = []
	for row in rows:
		if row.expected_return_date:
			row.age_days = (today - getdate(row.expected_return_date)).days
		else:
			row.age_days = None

		if overdue_only and not (row.age_days is not None and row.age_days > 0):
			continue

		row.balance_qty = flt(row.balance_qty, 2)
		data.append(row)

	return data


def build_conditions(filters):
	"""Parameterized WHERE fragments only — no value is ever interpolated."""
	conditions = []
	values = {}

	status = filters.get("status")
	if isinstance(status, str):
		try:
			status = frappe.parse_json(status)
		except Exception:
			status = [status]
	if status:
		conditions.append("AND rgp.status IN %(status)s")
		values["status"] = tuple(status)

	if filters.get("supplier"):
		conditions.append("AND rgp.supplier = %(supplier)s")
		values["supplier"] = filters.get("supplier")

	if filters.get("from_date"):
		conditions.append("AND rgp.challan_date >= %(from_date)s")
		values["from_date"] = filters.get("from_date")

	if filters.get("to_date"):
		conditions.append("AND rgp.challan_date <= %(to_date)s")
		values["to_date"] = filters.get("to_date")

	if filters.get("cost_center"):
		conditions.append("AND rgp.cost_center = %(cost_center)s")
		values["cost_center"] = filters.get("cost_center")

	return " ".join(conditions), values
