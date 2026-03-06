import frappe
from frappe.utils import now_datetime, time_diff_in_seconds


def execute(filters=None):
	columns = get_columns()
	data = get_data()
	return columns, data


def get_columns():
	return [
		{"fieldname": "token_number", "label": "Token", "fieldtype": "Link", "options": "BBF Token", "width": 160},
		{"fieldname": "status", "label": "Current Stage", "fieldtype": "Data", "width": 140},
		{"fieldname": "minutes_at_stage", "label": "Time at Stage (min)", "fieldtype": "Float", "width": 140},
		{"fieldname": "g1_entry_time", "label": "G1 Entry", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "g2_link_time", "label": "G2 Link", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "wb_gross_time", "label": "Gross Weight", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "unload_start_time", "label": "Unload Start", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "unload_end_time", "label": "Unload End", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "wb_tare_time", "label": "Tare Weight", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "quality_time", "label": "Quality", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "grn_time", "label": "GRN", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "indicator", "label": "Alert", "fieldtype": "Data", "width": 80},
	]


def get_data():
	tokens = frappe.get_all(
		"BBF Token",
		filters={"status": ["!=", "Exited"]},
		fields=[
			"token_number", "status", "g1_entry_time", "g2_link_time",
			"wb_gross_time", "unload_start_time", "unload_end_time",
			"wb_tare_time", "quality_time", "grn_time"
		],
		order_by="creation asc"
	)

	now = now_datetime()
	for token in tokens:
		last_ts = _get_last_timestamp(token)
		if last_ts:
			minutes = time_diff_in_seconds(now, last_ts) / 60
			token["minutes_at_stage"] = round(minutes, 1)

			if minutes > 30:
				token["indicator"] = "RED"
			elif minutes > 15:
				token["indicator"] = "YELLOW"
			else:
				token["indicator"] = "GREEN"
		else:
			token["minutes_at_stage"] = 0
			token["indicator"] = "GREEN"

	return tokens


def _get_last_timestamp(token):
	timestamp_map = {
		"Token Generated": token.get("g1_entry_time"),
		"PO Linked": token.get("g2_link_time"),
		"Gross Weighed": token.get("wb_gross_time"),
		"Unloading": token.get("unload_start_time"),
		"Tare Weighed": token.get("wb_tare_time"),
		"Quality Done": token.get("quality_time"),
		"GRN Created": token.get("grn_time"),
	}
	return timestamp_map.get(token.get("status"))
