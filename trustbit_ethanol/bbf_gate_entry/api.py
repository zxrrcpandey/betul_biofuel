import frappe
from frappe.utils import now_datetime, time_diff_in_seconds, getdate


@frappe.whitelist()
def get_purchase_orders(po_id=None, po_date=None, tentative_qty=None):
	filters = {"docstatus": 1, "status": ["not in", ["Closed", "Cancelled"]]}

	if po_id:
		filters["name"] = ["like", f"%{po_id}%"]
	if po_date:
		filters["transaction_date"] = po_date
	if tentative_qty:
		tentative_qty = float(tentative_qty)
		if tentative_qty > 0:
			filters["total_qty"] = ["between", [tentative_qty * 0.8, tentative_qty * 1.2]]

	return frappe.get_all(
		"Purchase Order",
		filters=filters,
		fields=["name", "supplier", "supplier_name", "transaction_date", "total_qty", "grand_total"],
		order_by="transaction_date desc",
		limit=20
	)


@frappe.whitelist()
def check_sla_breaches():
	settings = frappe.get_single("BBF Settings")
	threshold = settings.sla_threshold_minutes or 30
	escalation_email = settings.escalation_email

	active_tokens = frappe.get_all(
		"BBF Token",
		filters={"status": ["not in", ["Exited", "Token Generated"]]},
		fields=["name", "token_number", "status", "g1_entry_time", "g2_link_time",
				"wb_gross_time", "quality_time", "grading_time",
				"unload_start_time", "unload_end_time",
				"wb_tare_time", "grn_time"]
	)

	breaches = []
	now = now_datetime()

	for token in active_tokens:
		last_timestamp = _get_last_timestamp(token)
		if last_timestamp:
			minutes_at_stage = time_diff_in_seconds(now, last_timestamp) / 60
			if minutes_at_stage > threshold:
				breaches.append({
					"token": token.token_number,
					"status": token.status,
					"minutes": round(minutes_at_stage, 1)
				})

	if breaches and escalation_email:
		_send_sla_alert(breaches, escalation_email, threshold)

	return breaches


def _get_last_timestamp(token):
	timestamp_map = {
		"PO Linked": token.g2_link_time,
		"Gross Weighed": token.wb_gross_time,
		"Quality Done": token.quality_time,
		"Graded": token.grading_time,
		"Unloading": token.unload_start_time,
		"Tare Weighed": token.wb_tare_time,
		"GRN Created": token.grn_time,
	}
	return timestamp_map.get(token.status)


def _send_sla_alert(breaches, email, threshold):
	rows = ""
	for b in breaches:
		rows += f"<tr><td>{b['token']}</td><td>{b['status']}</td><td>{b['minutes']} min</td></tr>"

	message = f"""
	<h3>SLA Breach Alert - Tokens exceeding {threshold} minutes</h3>
	<table border="1" cellpadding="5" cellspacing="0">
		<thead><tr><th>Token</th><th>Stage</th><th>Time at Stage</th></tr></thead>
		<tbody>{rows}</tbody>
	</table>
	"""

	frappe.sendmail(
		recipients=[email],
		subject=f"BBF Gate Entry - SLA Breach Alert ({len(breaches)} tokens)",
		message=message
	)
