import frappe
from frappe.utils import getdate


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"fieldname": "token_number", "label": "Token", "fieldtype": "Link", "options": "BBF Token", "width": 160},
		{"fieldname": "entry_date", "label": "Date", "fieldtype": "Date", "width": 100},
		{"fieldname": "g1_entry_time", "label": "Entry Time", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "g1_exit_time", "label": "Exit Time", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "total_turnaround_minutes", "label": "Total (min)", "fieldtype": "Float", "width": 100},
		{"fieldname": "g1_to_g2_minutes", "label": "G1→G2", "fieldtype": "Float", "width": 80},
		{"fieldname": "g2_to_wb_minutes", "label": "G2→WB", "fieldtype": "Float", "width": 80},
		{"fieldname": "wb_to_quality_minutes", "label": "WB→QC", "fieldtype": "Float", "width": 80},
		{"fieldname": "quality_to_grading_minutes", "label": "QC→Grade", "fieldtype": "Float", "width": 90},
		{"fieldname": "grading_to_unload_minutes", "label": "Grade→Unload", "fieldtype": "Float", "width": 100},
		{"fieldname": "unloading_duration_minutes", "label": "Unloading", "fieldtype": "Float", "width": 90},
		{"fieldname": "unload_to_tare_minutes", "label": "Unload→Tare", "fieldtype": "Float", "width": 100},
		{"fieldname": "tare_to_grn_minutes", "label": "Tare→GRN", "fieldtype": "Float", "width": 80},
		{"fieldname": "status", "label": "Status", "fieldtype": "Data", "width": 120},
	]


def get_data(filters):
	conditions = {}
	if filters:
		if filters.get("from_date"):
			conditions["entry_date"] = [">=", getdate(filters["from_date"])]
		if filters.get("to_date"):
			if "entry_date" in conditions:
				conditions["entry_date"] = ["between", [getdate(filters["from_date"]), getdate(filters["to_date"])]]
			else:
				conditions["entry_date"] = ["<=", getdate(filters["to_date"])]
		if filters.get("status"):
			conditions["status"] = filters["status"]

	return frappe.get_all(
		"BBF Token",
		filters=conditions,
		fields=[
			"token_number", "entry_date", "g1_entry_time", "g1_exit_time",
			"total_turnaround_minutes", "g1_to_g2_minutes", "g2_to_wb_minutes",
			"wb_to_quality_minutes", "quality_to_grading_minutes",
			"grading_to_unload_minutes", "unloading_duration_minutes",
			"unload_to_tare_minutes", "tare_to_grn_minutes", "status"
		],
		order_by="creation desc",
		limit=500
	)
