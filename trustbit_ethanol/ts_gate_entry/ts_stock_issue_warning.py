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


# v2.16.5 — Material Issue mandatory fields
# (Define Use Location / Item Remark / Cost Center / Project)
_ISSUE_REQUIRED_FIELDS = (
	("ts_delivery_location", "Define Use Location"),
	("ts_item_remark", "Item Remark"),
	("cost_center", "Cost Center"),
	("project", "Project"),
)


def require_issue_fields_on_submit(doc, method=None):
	"""Stock Entry before_submit hook — for a Material Issue, every item row must
	carry Define Use Location, Item Remark, and Cost Center.

	Enforced at SUBMIT (not save): the Stores Manager Independent flow
	(ts_mr_transfer.approve_transfer) creates a DRAFT Material Issue Stock Entry
	programmatically — drafts may exist incomplete, but a finalized (submitted)
	issue must always record where the material is used, a remark, and the cost
	centre it is charged to. Mirrors the v2.16.4 PR-invoice-at-submit pattern.
	"""
	if getattr(doc, "purpose", "") != "Material Issue":
		return
	if not doc.items:
		return

	# The header "Cost Center (all items)" (ts_set_cost_center) is the authoritative
	# main cost center for a Material Issue — before_validate force-applies it to every
	# row. Require it at SUBMIT so an issue can never be finalized with a blank header
	# and silently default each row to the company-default cost center ("Main"), which
	# mis-attributes the whole issue in the cost-center reports. before_submit runs
	# AFTER before_validate (which auto-fetches the header from a source Material
	# Request), so MR-sourced issues are unaffected; only truly-blank issues are blocked.
	if not str(doc.get("ts_set_cost_center") or "").strip():
		frappe.throw(
			"Please set <b>Cost Center (all items)</b> in the header before submitting "
			"this Material Issue — it is the cost centre the entire issue is charged to.",
			title="Material Issue — Cost Center Required",
		)

	problems = []
	for row in doc.items:
		missing = [
			label for fieldname, label in _ISSUE_REQUIRED_FIELDS
			if not (str(row.get(fieldname) or "").strip())
		]
		if missing:
			problems.append((row.idx, missing))

	if not problems:
		return

	lines = "".join(
		f"<li>Row <b>{idx}</b>: {', '.join(missing)}</li>"
		for idx, missing in problems
	)
	frappe.throw(
		"Please fill the following mandatory field(s) before submitting this "
		"Material Issue:<br><ul>" + lines + "</ul>",
		title="Material Issue — Required Fields",
	)
