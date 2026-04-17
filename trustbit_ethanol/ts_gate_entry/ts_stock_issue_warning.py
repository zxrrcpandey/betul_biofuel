"""
v2.8.1 Phase B — Non-blocking warning on Material Issue from warehouse with Rejected-PR stock.

Shows an orange msgprint when user issues stock from a warehouse that contains
PR(s) with ts_qc_status='Rejected'. Does NOT block — relies on Stores team to
physically segregate.

Refs: memory/project_flow_revamp_v2.8.md (Q1.1 decision)
"""

import frappe


def warn_rejected_stock_in_warehouse(doc, method=None):
	"""Stock Entry before_save hook — warn on Material Issue from rejected-stock warehouse."""
	if getattr(doc, "stock_entry_type", "") != "Material Issue":
		return
	if not doc.items:
		return

	# Deduplicate source warehouses
	warehouses = set()
	for item in doc.items:
		if getattr(item, "s_warehouse", None):
			warehouses.add(item.s_warehouse)

	if not warehouses:
		return

	# For each warehouse, find submitted PRs with ts_qc_status='Rejected' posting stock there
	rejected_prs = {}
	for wh in warehouses:
		prs = frappe.db.sql("""
			SELECT DISTINCT pr.name
			FROM `tabPurchase Receipt` pr
			JOIN `tabPurchase Receipt Item` pri ON pri.parent = pr.name
			WHERE pr.docstatus = 1
			  AND pr.ts_qc_status = 'Rejected'
			  AND pri.warehouse = %s
			LIMIT 5
		""", (wh,), as_dict=True)
		if prs:
			rejected_prs[wh] = [p.name for p in prs]

	if not rejected_prs:
		return

	# Compose warning
	lines = []
	for wh, prs in rejected_prs.items():
		lines.append(f"<li><b>{frappe.utils.escape_html(wh)}</b>: {', '.join(prs)}</li>")

	frappe.msgprint(
		msg=(
			"<b>Warning:</b> This issue is drawing from warehouse(s) that contain "
			"stock from Purchase Receipts with QC Rejected status:<br><ul>"
			+ "".join(lines)
			+ "</ul><br>Verify the material being issued is NOT from a rejected batch. "
			"If unsure, consult Stores team and QI before proceeding."
		),
		title="Rejected stock warning (informational)",
		indicator="orange",
	)
