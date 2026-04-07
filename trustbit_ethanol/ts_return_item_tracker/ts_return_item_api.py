"""TS Return Item Tracker API — Issue, Return, Transfer, Damage, Discard + Ledger + Live Tracking."""

import frappe
from frappe import _
from frappe.utils import now_datetime, getdate, nowtime, flt, time_diff_in_seconds, get_datetime


# ═══════════════════════════════════════════════════════════════
#  ISSUE MATERIAL
# ═══════════════════════════════════════════════════════════════

@frappe.whitelist()
def complete_transaction(transaction_name):
	"""Complete a transaction — creates ledger entries and updates assignments."""
	doc = frappe.get_doc("TS Return Item Transaction", transaction_name)

	if doc.status not in ("Draft", "Approved"):
		frappe.throw(_("Only Draft or Approved transactions can be completed"))

	handler = {
		"Issue": _process_issue,
		"Return": _process_return,
		"Re-issue": _process_issue,  # Same as issue with reference
		"Transfer": _process_transfer,
		"Damage Report": _process_damage,
		"Discard": _process_discard,
		"Check Out": _process_issue,
		"Check In": _process_return,
	}.get(doc.transaction_type)

	if not handler:
		frappe.throw(_("Unknown transaction type: {0}").format(doc.transaction_type))

	handler(doc)
	doc.db_set("status", "Completed")
	frappe.db.commit()
	frappe.msgprint(_("Transaction completed successfully"), indicator="green")


def _process_issue(doc):
	"""Issue items — deduct from asset stock, create assignment, create ledger entries."""
	for item in doc.items:
		asset = frappe.get_doc("TS Return Item", item.asset_item)

		# Deduct stock
		new_stock = flt(asset.current_stock) - flt(item.qty)
		if new_stock < 0 and asset.category != "Consumable":
			frappe.throw(_("Insufficient stock for {0}. Available: {1}, Requested: {2}").format(
				asset.item_name, asset.current_stock, item.qty
			))
		asset.db_set("current_stock", max(new_stock, 0))

		# Create assignment (who has what)
		if asset.category != "Consumable":
			frappe.get_doc({
				"doctype": "TS Return Item Assignment",
				"asset_item": item.asset_item,
				"item_name": asset.item_name,
				"qty": item.qty,
				"person": doc.issued_to_person,
				"person_name": doc.issued_to_name,
				"department": doc.department,
				"cost_center": doc.cost_center,
				"location": doc.location,
				"issued_at": get_datetime(str(doc.transaction_date) + " " + str(doc.transaction_time)),
				"expected_return_at": doc.expected_return_at,
				"issue_transaction": doc.name,
				"status": "Active",
				"category": asset.category,
			}).insert(ignore_permissions=True)

		# Create ledger entry
		_create_ledger_entry(
			asset_item=item.asset_item,
			item_name=asset.item_name,
			transaction_type=doc.transaction_type,
			qty_change=-flt(item.qty),
			balance_qty=max(new_stock, 0),
			voucher_type="TS Return Item Transaction",
			voucher_no=doc.name,
			person=doc.issued_to_person,
			department=doc.department,
			location=doc.location,
			value_change=-flt(item.unit_value) * flt(item.qty),
			remarks=doc.remarks,
			posting_date=doc.transaction_date,
			posting_time=doc.transaction_time,
		)


def _process_return(doc):
	"""Return items — add back to asset stock, close assignment, calculate usage duration."""
	for item in doc.items:
		asset = frappe.get_doc("TS Return Item", item.asset_item)

		# Add back to stock
		new_stock = flt(asset.current_stock) + flt(item.qty)
		asset.db_set("current_stock", new_stock)

		# Close assignment
		if doc.reference_transaction:
			assignments = frappe.get_all("TS Return Item Assignment",
				filters={"issue_transaction": doc.reference_transaction, "asset_item": item.asset_item, "status": "Active"},
				pluck="name")
			for a in assignments:
				frappe.db.set_value("TS Return Item Assignment", a, {
					"status": "Returned",
					"is_overdue": 0,
				})

		# Calculate usage duration
		if doc.reference_transaction and doc.actual_return_time:
			issue_time = frappe.db.get_value("TS Return Item Transaction", doc.reference_transaction,
				["transaction_date", "transaction_time"], as_dict=True)
			if issue_time:
				issue_dt = get_datetime(str(issue_time.transaction_date) + " " + str(issue_time.transaction_time))
				return_dt = get_datetime(doc.actual_return_time)
				diff_seconds = time_diff_in_seconds(return_dt, issue_dt)
				doc.db_set("usage_hours", round(diff_seconds / 3600, 2))
				doc.db_set("usage_days", round(diff_seconds / 86400, 2))

		# Create ledger entry
		_create_ledger_entry(
			asset_item=item.asset_item,
			item_name=asset.item_name,
			transaction_type=doc.transaction_type,
			qty_change=flt(item.qty),
			balance_qty=new_stock,
			voucher_type="TS Return Item Transaction",
			voucher_no=doc.name,
			person=doc.returned_by_person,
			department=doc.return_department,
			value_change=flt(item.unit_value) * flt(item.qty),
			remarks=doc.remarks,
			posting_date=doc.transaction_date,
			posting_time=doc.transaction_time,
		)


def _process_transfer(doc):
	"""Transfer items between departments — close old assignment, create new one."""
	for item in doc.items:
		# Close old assignment
		if doc.from_person:
			assignments = frappe.get_all("TS Return Item Assignment",
				filters={"person": doc.from_person, "asset_item": item.asset_item, "status": "Active"},
				pluck="name")
			for a in assignments:
				frappe.db.set_value("TS Return Item Assignment", a, "status", "Transferred")

		# Create new assignment
		asset = frappe.get_doc("TS Return Item", item.asset_item)
		frappe.get_doc({
			"doctype": "TS Return Item Assignment",
			"asset_item": item.asset_item,
			"item_name": asset.item_name,
			"qty": item.qty,
			"person": doc.to_person,
			"person_name": frappe.db.get_value("TS Return Item Person", doc.to_person, "person_name") if doc.to_person else "",
			"department": doc.to_department,
			"location": doc.location,
			"issued_at": now_datetime(),
			"issue_transaction": doc.name,
			"status": "Active",
			"category": asset.category,
		}).insert(ignore_permissions=True)

		# Two ledger entries: Transfer Out + Transfer In
		balance = flt(asset.current_stock)
		_create_ledger_entry(
			asset_item=item.asset_item, item_name=asset.item_name,
			transaction_type="Transfer Out", qty_change=0, balance_qty=balance,
			voucher_type="TS Return Item Transaction", voucher_no=doc.name,
			person=doc.from_person, department=doc.from_department,
			posting_date=doc.transaction_date, posting_time=doc.transaction_time,
		)
		_create_ledger_entry(
			asset_item=item.asset_item, item_name=asset.item_name,
			transaction_type="Transfer In", qty_change=0, balance_qty=balance,
			voucher_type="TS Return Item Transaction", voucher_no=doc.name,
			person=doc.to_person, department=doc.to_department,
			posting_date=doc.transaction_date, posting_time=doc.transaction_time,
		)


def _process_damage(doc):
	"""Log damage — no stock change, just record."""
	for item in doc.items:
		asset = frappe.get_doc("TS Return Item", item.asset_item)
		_create_ledger_entry(
			asset_item=item.asset_item, item_name=asset.item_name,
			transaction_type="Damage", qty_change=0, balance_qty=flt(asset.current_stock),
			voucher_type="TS Return Item Transaction", voucher_no=doc.name,
			person=doc.issued_to_person or doc.returned_by_person,
			department=doc.department or doc.return_department,
			remarks=doc.damage_reason,
			posting_date=doc.transaction_date, posting_time=doc.transaction_time,
		)


def _process_discard(doc):
	"""Discard items — remove from stock permanently.
	Items must be returned first before discarding (no discarding assigned items)."""
	for item in doc.items:
		asset = frappe.get_doc("TS Return Item", item.asset_item)

		# Block discard if items are currently assigned (must Return first)
		assigned_qty = flt(frappe.db.sql("""
			SELECT COALESCE(SUM(qty), 0) FROM `tabTS Return Item Assignment`
			WHERE asset_item = %s AND status IN ('Active', 'Overdue')
		""", item.asset_item)[0][0])

		if assigned_qty > 0:
			frappe.throw(
				_("{0} has {1} qty currently issued/assigned. "
				  "Please create a Return transaction first, then Discard.").format(
					asset.item_name, assigned_qty
				)
			)

		# Check sufficient stock
		discard_qty = flt(item.qty)
		if discard_qty > flt(asset.current_stock):
			frappe.throw(
				_("Cannot discard {0} of {1}. Only {2} in stock.").format(
					discard_qty, asset.item_name, asset.current_stock
				)
			)

		# Deduct from stock
		new_stock = flt(asset.current_stock) - discard_qty
		asset.db_set("current_stock", max(new_stock, 0))
		if new_stock <= 0:
			asset.db_set("status", "Discarded")

		_create_ledger_entry(
			asset_item=item.asset_item, item_name=asset.item_name,
			transaction_type="Discard", qty_change=-discard_qty, balance_qty=max(new_stock, 0),
			voucher_type="TS Return Item Transaction", voucher_no=doc.name,
			value_change=-flt(doc.discard_value or 0),
			remarks=doc.discard_reason,
			posting_date=doc.transaction_date, posting_time=doc.transaction_time,
		)


# ═══════════════════════════════════════════════════════════════
#  DISCARD APPROVAL
# ═══════════════════════════════════════════════════════════════

@frappe.whitelist()
def submit_for_discard_approval(transaction_name):
	"""Return Item Controller submits discard for CEO/MD approval."""
	doc = frappe.get_doc("TS Return Item Transaction", transaction_name)
	if doc.transaction_type != "Discard":
		frappe.throw(_("Only Discard transactions need approval"))
	if doc.status != "Draft":
		frappe.throw(_("Only Draft transactions can be submitted for approval"))

	doc.db_set("status", "Pending Approval")

	# Notify CEO
	settings = frappe.get_cached_doc("TS Return Item Settings")
	approver_role = settings.get("discard_approver_role") or "CEO"
	approvers = frappe.get_all("Has Role", filters={"role": approver_role, "parenttype": "User"}, pluck="parent")
	for user in approvers:
		frappe.get_doc({
			"doctype": "Notification Log",
			"for_user": user,
			"from_user": frappe.session.user,
			"type": "Alert",
			"document_type": "TS Return Item Transaction",
			"document_name": doc.name,
			"subject": _("Return Item Discard Approval Required — {0}").format(doc.name),
			"email_content": _("Discard request for {0} items. Value: {1}. Reason: {2}").format(
				len(doc.items), doc.discard_value, doc.discard_reason
			),
		}).insert(ignore_permissions=True)

	frappe.msgprint(_("Submitted for {0} approval").format(approver_role), indicator="blue")


@frappe.whitelist()
def approve_discard(transaction_name):
	"""CEO/MD approves discard."""
	doc = frappe.get_doc("TS Return Item Transaction", transaction_name)
	if doc.status != "Pending Approval":
		frappe.throw(_("Only Pending Approval transactions can be approved"))

	settings = frappe.get_cached_doc("TS Return Item Settings")
	approver_role = settings.get("discard_approver_role") or "CEO"
	if approver_role not in frappe.get_roles():
		frappe.throw(_("Only {0} can approve discards").format(approver_role))

	doc.db_set("approved_by", frappe.session.user)
	doc.db_set("approved_at", now_datetime())
	doc.db_set("status", "Approved")

	# Auto-complete the discard
	_process_discard(doc)
	doc.db_set("status", "Completed")
	frappe.db.commit()
	frappe.msgprint(_("Discard approved and completed"), indicator="green")


@frappe.whitelist()
def reject_discard(transaction_name, reason=""):
	"""CEO/MD rejects discard."""
	doc = frappe.get_doc("TS Return Item Transaction", transaction_name)
	if doc.status != "Pending Approval":
		frappe.throw(_("Only Pending Approval transactions can be rejected"))

	doc.db_set("status", "Rejected")
	doc.db_set("approval_remarks", reason)
	frappe.msgprint(_("Discard rejected"), indicator="red")


# ═══════════════════════════════════════════════════════════════
#  OVERDUE CHECK (Scheduler)
# ═══════════════════════════════════════════════════════════════

def check_overdue_assignments():
	"""Called by scheduler every 30 min. Marks overdue assignments and sends notifications."""
	now = now_datetime()

	overdue = frappe.get_all("TS Return Item Assignment",
		filters={
			"status": "Active",
			"expected_return_at": ["<", now],
			"expected_return_at": ["is", "set"],
			"is_overdue": 0,
		},
		fields=["name", "asset_item", "item_name", "person", "person_name", "department", "expected_return_at"],
	)

	for a in overdue:
		frappe.db.set_value("TS Return Item Assignment", a.name, "is_overdue", 1)
		frappe.db.set_value("TS Return Item Assignment", a.name, "status", "Overdue")

	if overdue:
		frappe.db.commit()


# ═══════════════════════════════════════════════════════════════
#  DASHBOARD API
# ═══════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_asset_dashboard():
	"""Return dashboard data — KPIs, live assignments, recent transactions."""
	data = {}

	data["total_assets"] = frappe.db.count("TS Return Item", {"status": "Active"})
	data["available"] = frappe.db.sql("SELECT COALESCE(SUM(current_stock), 0) FROM `tabTS Return Item` WHERE status='Active'")[0][0]
	data["currently_issued"] = frappe.db.count("TS Return Item Assignment", {"status": ["in", ["Active", "Overdue"]]})
	data["overdue"] = frappe.db.count("TS Return Item Assignment", {"status": "Overdue"})
	data["damaged"] = frappe.db.count("TS Return Item Transaction", {"transaction_type": "Damage Report", "status": "Completed"})
	data["pending_discard"] = frappe.db.count("TS Return Item Transaction", {"transaction_type": "Discard", "status": "Pending Approval"})
	data["issues_today"] = frappe.db.count("TS Return Item Transaction", {"transaction_type": "Issue", "transaction_date": getdate(), "status": "Completed"})
	data["returns_today"] = frappe.db.count("TS Return Item Transaction", {"transaction_type": "Return", "transaction_date": getdate(), "status": "Completed"})

	# Live assignments
	data["live_assignments"] = frappe.get_all("TS Return Item Assignment",
		filters={"status": ["in", ["Active", "Overdue"]]},
		fields=["name", "asset_item", "item_name", "category", "person_name", "department",
		        "location", "issued_at", "expected_return_at", "is_overdue", "status"],
		order_by="is_overdue desc, issued_at asc",
		limit=50,
	)

	return data


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def _create_ledger_entry(**kwargs):
	"""Create a TS Return Item Ledger entry."""
	doc = frappe.get_doc({
		"doctype": "TS Return Item Ledger",
		"posting_date": kwargs.get("posting_date") or getdate(),
		"posting_time": kwargs.get("posting_time") or nowtime(),
		"asset_item": kwargs.get("asset_item"),
		"item_name": kwargs.get("item_name"),
		"transaction_type": kwargs.get("transaction_type"),
		"qty_change": kwargs.get("qty_change", 0),
		"balance_qty": kwargs.get("balance_qty", 0),
		"voucher_type": kwargs.get("voucher_type"),
		"voucher_no": kwargs.get("voucher_no"),
		"person": kwargs.get("person"),
		"department": kwargs.get("department"),
		"location": kwargs.get("location"),
		"value_change": kwargs.get("value_change", 0),
		"balance_value": kwargs.get("balance_value", 0),
		"remarks": kwargs.get("remarks"),
	})
	doc.insert(ignore_permissions=True)
	return doc.name


# ═══════════════════════════════════════════════════════════════
#  BULK IMPORT
# ═══════════════════════════════════════════════════════════════

@frappe.whitelist()
def bulk_import_assets(rows):
	"""Create multiple TS Return Items from a list of row dicts."""
	import json
	if isinstance(rows, str):
		rows = json.loads(rows)

	results = []
	for row in rows:
		try:
			# Lookup ERPNext Item by item_code
			item_code = row.get("item_code", "").strip()
			if not item_code:
				frappe.throw(_("Item Code is required"))

			if not frappe.db.exists("Item", item_code):
				frappe.throw(_("ERPNext Item '{0}' not found").format(item_code))

			# Block duplicates — check if already linked
			existing = frappe.db.get_value("TS Return Item", {"erp_item": item_code}, "name")
			if existing:
				frappe.throw(_("Item '{0}' already linked to {1}").format(item_code, existing))

			# Fetch item details from ERPNext
			erp_item = frappe.db.get_value("Item", item_code,
				["item_name", "item_code", "stock_uom", "item_group"], as_dict=True)

			# Parse ALL date fields
			warranty = _parse_date(row.get("warranty_expiry", "")) or None
			purchase_date = _parse_date(row.get("purchase_date", "")) or None
			amc_expiry = _parse_date(row.get("amc_expiry", "")) or None

			qty = flt(row.get("qty", 0))

			doc = frappe.get_doc({
				"doctype": "TS Return Item",
				"erp_item": item_code,
				"item_name": erp_item.item_name,
				"item_code": erp_item.item_code,
				"item_group": erp_item.item_group,
				"uom": erp_item.stock_uom or "Nos",
				"category": row.get("category", "Returnable"),
				"opening_stock": qty,
				"purchase_value": flt(row.get("purchase_value", 0)),
				"current_value": flt(row.get("purchase_value", 0)),
				"purchase_date": purchase_date,
				"warranty_expiry": warranty,
				"amc_expiry": amc_expiry,
				"amc_vendor": row.get("amc_vendor", ""),
				"description": row.get("description", ""),
				"min_stock_level": flt(row.get("min_stock_level", 0)),
				"expected_return_hours": flt(row.get("expected_return_hours", 0)),
				"status": "Active",
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()

			results.append({
				"success": True,
				"name": doc.name,
				"item_code": item_code,
				"item_name": erp_item.item_name,
				"category": doc.category,
				"current_stock": flt(doc.current_stock) or qty,
			})
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Return Item Bulk Import Error ({row.get('item_code', '?')})",
				message=frappe.get_traceback()
			)
			frappe.clear_messages()
			results.append({
				"success": False,
				"item_code": row.get("item_code", "?"),
				"error": str(e)[:200],
			})

	return results


@frappe.whitelist()
def get_asset_reference():
	"""Return reference data for bulk import — categories, UOMs, locations."""
	data = {}
	data["uoms"] = frappe.get_all("UOM", fields=["name"], order_by="name", limit_page_length=0)
	data["locations"] = frappe.get_all("TS Return Item Location",
		fields=["name", "location_type"], filters={"is_active": 1},
		order_by="name", limit_page_length=0)
	return data


def _parse_date(date_str):
	"""Parse date from various formats (DD-MM-YYYY, DD/MM/YYYY, YYYY-MM-DD) to YYYY-MM-DD."""
	if not date_str or not str(date_str).strip():
		return ""
	date_str = str(date_str).strip()
	if len(date_str) == 10 and date_str[4] == "-" and date_str[7] == "-":
		return date_str
	from datetime import datetime
	for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%m-%d-%Y", "%m/%d/%Y", "%Y/%m/%d"):
		try:
			return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
		except ValueError:
			continue
	try:
		return str(getdate(date_str))
	except Exception:
		return ""
