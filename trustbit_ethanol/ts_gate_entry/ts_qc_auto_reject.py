"""
v2.8.1 Phase B — Auto-draft Purchase Return on QI Rejected.

QI submit hook:
  - On Approved  → sets linked PR.ts_qc_status = 'Approved'
  - On Rejected  → sets linked PR.ts_qc_status = 'Rejected' + auto-drafts a Purchase Return
  - Other statuses → no-op

Never cancels the original PR (preserves audit trail). Draft Return needs Stores
user to review + submit.

Refs: memory/project_flow_revamp_v2.8.md (Q11 decision)
"""

import frappe
from frappe import _
from frappe.utils import flt


def on_qi_submitted(doc, method=None):
	"""TS Quality Inspection on_submit hook — route by status."""
	status = (getattr(doc, "status", "") or "").strip()
	token = getattr(doc, "token_number", None) or getattr(doc, "token", None)

	if not token:
		return

	if status == "Approved":
		_mark_prs_approved(token, doc.name)
	elif status == "Rejected":
		_mark_prs_rejected_and_draft_return(token, doc)


def _mark_prs_approved(token, qi_name):
	"""Set ts_qc_status='Approved' on every PR linked to this token."""
	prs = frappe.get_all(
		"Purchase Receipt",
		filters={"ts_token": token, "docstatus": ["!=", 2]},
		pluck="name",
	)
	frappe.flags.in_qc_auto_update = True
	try:
		for pr_name in prs:
			frappe.db.set_value("Purchase Receipt", pr_name, "ts_qc_status", "Approved", update_modified=False)
			frappe.get_doc("Purchase Receipt", pr_name).add_comment(
				"Info", f"[QC_APPROVED] by {frappe.session.user} via {qi_name}"
			)
	finally:
		frappe.flags.in_qc_auto_update = False


def _mark_prs_rejected_and_draft_return(token, qi_doc):
	"""Set ts_qc_status='Rejected' + auto-draft a Purchase Return for review."""
	prs = frappe.get_all(
		"Purchase Receipt",
		filters={"ts_token": token, "docstatus": 1, "is_return": 0},
		pluck="name",
	)

	reason = getattr(qi_doc, "rejection_reason", None) or getattr(qi_doc, "remarks", None) or "Rejected by QI"

	frappe.flags.in_qc_auto_update = True
	drafted = []
	try:
		for pr_name in prs:
			frappe.db.set_value(
				"Purchase Receipt", pr_name, "ts_qc_status", "Rejected", update_modified=False
			)

			pr = frappe.get_doc("Purchase Receipt", pr_name)
			pr.add_comment(
				"Info",
				f"[QC_REJECTED] by {frappe.session.user} via {qi_doc.name}: "
				f"{frappe.utils.escape_html(reason)[:400]}",
			)

			# Create draft Purchase Return (is_return=1)
			try:
				ret = frappe.new_doc("Purchase Receipt")
				ret.is_return = 1
				ret.return_against = pr.name
				ret.supplier = pr.supplier
				ret.company = pr.company
				ret.posting_date = frappe.utils.today()
				ret.remarks = f"Auto-drafted on QC Rejected ({qi_doc.name}): {reason[:200]}"

				for item in pr.items:
					ret.append("items", {
						"item_code": item.item_code,
						"item_name": item.item_name,
						"qty": -1 * flt(item.qty),
						"rate": flt(item.rate),
						"uom": item.uom,
						"warehouse": item.warehouse,
						"purchase_receipt_item": item.name,
						"purchase_order": item.purchase_order,
						"purchase_order_item": item.purchase_order_item,
					})

				ret.flags.ignore_permissions = True
				ret.insert(ignore_permissions=True)
				drafted.append(ret.name)
			except Exception as e:
				frappe.log_error(
					message=f"Auto-draft Return failed for PR {pr_name} on QI {qi_doc.name}: {e}",
					title="ts_qc_auto_reject._mark_prs_rejected_and_draft_return",
				)
	finally:
		frappe.flags.in_qc_auto_update = False

	if drafted:
		# Notification to Stores users + IT Head
		try:
			stores_users = frappe.get_all(
				"Has Role",
				filters={"role": ["in", ["Stores User", "IT Head"]], "parenttype": "User"},
				pluck="parent",
			)
			for user in set(stores_users):
				frappe.get_doc({
					"doctype": "Notification Log",
					"for_user": user,
					"subject": f"QC Rejected — review Purchase Return draft(s)",
					"email_content": (
						f"Token {token}: Quality Inspection {qi_doc.name} was Rejected. "
						f"Draft Purchase Return(s) created for review: {', '.join(drafted)}. "
						f"Please review quantities and submit to reverse stock."
					),
					"type": "Alert",
					"document_type": "Purchase Receipt",
					"document_name": drafted[0],
				}).insert(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(message=f"Notification Log failed: {e}", title="ts_qc_auto_reject notification")
