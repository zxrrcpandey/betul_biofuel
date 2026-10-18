import frappe
from frappe.utils import add_days, now_datetime


@frappe.whitelist()
def count_awaiting_gross(**kwargs):
	"""Custom Number Card "Awaiting Gross Weight": trucks from the last 7 days
	that were sent to the weighbridge (submitted Gate Entry) but have no gross
	weight yet. Token status alone can't tell — Non-RM no-weighing trucks also
	sit at "PO Linked" — and older rows are abandoned, not waiting."""
	if not frappe.has_permission("TS Token", "read"):
		return 0
	return frappe.db.sql("""
		SELECT COUNT(*) FROM `tabTS Token` t
		WHERE t.status = 'PO Linked' AND t.creation >= %s
		  AND EXISTS (SELECT 1 FROM `tabTS Gate Entry` g
			WHERE g.token_number = t.name AND g.docstatus = 1
			  AND g.status = 'Sent to Weighbridge')
	""", add_days(now_datetime(), -7))[0][0]
