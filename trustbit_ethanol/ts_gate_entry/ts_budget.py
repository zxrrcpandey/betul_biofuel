import frappe
from frappe import _
from frappe.utils import flt, cint, nowdate, now_datetime, getdate, add_months, get_first_day, get_last_day, format_datetime


# ═══════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════

BUDGET_ROLES = ("CEO", "MD", "IT Head", "Accounts Manager", "System Manager")


def _check_budget_role():
	"""Check if user has a role that can view financial data."""
	user_roles = frappe.get_roles(frappe.session.user)
	if not any(r in user_roles for r in BUDGET_ROLES + ("Department Head", "Purchase Manager", "Grain Purchase Manager", "AVP")):
		frappe.throw(_("You don't have permission to access budget data"))


def _get_fiscal_year_dates(fiscal_year):
	"""Return (start_date, end_date) for a fiscal year."""
	fy = frappe.get_doc("Fiscal Year", fiscal_year)
	return getdate(fy.year_start_date), getdate(fy.year_end_date)


def _resolve_fiscal_year(company=None):
	"""Get current active fiscal year — v2.9.17.9 date-based lookup.

	Old behavior preferred frappe.defaults.get_global_default('fiscal_year') which
	stays stale until an admin manually flips it (e.g. demo still pointed at
	'2025-2026' on 15 May 2026 when Indian FY had already rolled to '2026-2027').
	That caused budget banners to read the wrong year's committed/spent numbers.

	New behavior: find the Fiscal Year row whose [year_start_date, year_end_date]
	window contains today. Falls back to the legacy default if no date match.
	Auto-rolls every April without sysadmin action.
	"""
	today = getdate(nowdate())
	fy = frappe.db.get_value(
		"Fiscal Year",
		{
			"disabled": 0,
			"year_start_date": ["<=", today],
			"year_end_date": [">=", today],
		},
		"name",
	)
	if fy:
		return fy
	# Fallback — stale global default, then most-recent non-disabled FY.
	return (
		frappe.defaults.get_global_default("fiscal_year")
		or frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name", order_by="year_start_date desc")
	)


# ═══════════════════════════════════════════════════════════════════════
#  BUDGET PROPOSAL WORKFLOW
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def submit_budget_proposal(proposal_name):
	"""Department Head submits proposal for CEO approval."""
	doc = frappe.get_doc("TS Budget Proposal", proposal_name, for_update=True)

	# Authorization: only proposer/owner can submit
	if frappe.session.user not in (doc.proposed_by, doc.owner, "Administrator"):
		frappe.throw(_("Only the proposal creator can submit for approval"))

	if doc.status != "Draft":
		frappe.throw(_("Only Draft proposals can be submitted. Current status: {0}").format(doc.status))

	if not doc.budget_items:
		frappe.throw(_("Please add at least one budget line item"))

	# Validate all items have proposed amounts
	for row in doc.budget_items:
		if flt(row.proposed_amount) <= 0:
			frappe.throw(_("Proposed amount for {0} must be greater than zero").format(
				row.account_name or row.account))

	# Calculate totals
	doc.total_proposed = sum(flt(row.proposed_amount) for row in doc.budget_items)
	doc.total_approved = 0

	# Pre-fill CEO approved amounts with proposed amounts (CEO can adjust)
	for row in doc.budget_items:
		if not flt(row.ceo_approved_amount):
			row.ceo_approved_amount = row.proposed_amount

	doc.status = "Pending CEO"
	doc.save(ignore_permissions=True)

	# Notify CEO
	_send_budget_notification(doc, "pending",
		_get_role_users("CEO"),
		extra={"proposed_by": frappe.utils.get_fullname(frappe.session.user)})

	return {"status": "submitted", "message": _("Budget proposal submitted to CEO for approval")}


@frappe.whitelist()
def approve_budget_proposal(proposal_name, comment=""):
	"""CEO approves a budget proposal → creates ERPNext Budget record."""
	doc = frappe.get_doc("TS Budget Proposal", proposal_name, for_update=True)

	if doc.status != "Pending CEO":
		frappe.throw(_("Only proposals pending CEO approval can be approved. Current status: {0}").format(doc.status))

	user_roles = frappe.get_roles(frappe.session.user)
	if "CEO" not in user_roles and "System Manager" not in user_roles:
		frappe.throw(_("Only CEO or System Manager can approve budget proposals"))

	# Validate CEO approved amounts — must have at least one > 0
	has_nonzero = False
	for row in doc.budget_items:
		if flt(row.ceo_approved_amount) < 0:
			frappe.throw(_("Approved amount for {0} cannot be negative").format(
				row.account_name or row.account))
		if flt(row.ceo_approved_amount) > 0:
			has_nonzero = True

	if not has_nonzero:
		frappe.throw(_("At least one account must have a non-zero approved amount"))

	# Check for duplicate — another approved proposal for same CC+FY
	existing = frappe.db.exists("TS Budget Proposal", {
		"cost_center": doc.cost_center,
		"fiscal_year": doc.fiscal_year,
		"status": "Approved",
		"name": ["!=", doc.name]
	})
	if existing:
		frappe.throw(_(
			"An approved budget already exists for {0} in {1}: {2}. "
			"Please revise the existing one instead."
		).format(doc.cost_center, doc.fiscal_year, existing))

	# Calculate totals
	doc.total_approved = sum(flt(row.ceo_approved_amount) for row in doc.budget_items)
	doc.status = "Approved"
	doc.approved_by = frappe.session.user
	doc.approved_date = nowdate()
	doc.ceo_comment = comment
	doc.save(ignore_permissions=True)

	# Create the ERPNext Budget record
	budget_name = _create_erpnext_budget(doc)

	# Notify proposer
	if doc.proposed_by:
		_send_budget_notification(doc, "approved",
			[doc.proposed_by],
			extra={"approved_by": frappe.utils.get_fullname(frappe.session.user),
				   "budget_name": budget_name})

	return {"status": "approved", "budget_name": budget_name,
			"message": _("Budget approved and activated for {0}").format(doc.cost_center)}


@frappe.whitelist()
def revise_budget_proposal(proposal_name, reason):
	"""CEO sends proposal back for revision."""
	if not reason:
		frappe.throw(_("Revision reason is mandatory"))

	doc = frappe.get_doc("TS Budget Proposal", proposal_name, for_update=True)

	if doc.status != "Pending CEO":
		frappe.throw(_("Only proposals pending CEO approval can be revised"))

	user_roles = frappe.get_roles(frappe.session.user)
	if "CEO" not in user_roles and "System Manager" not in user_roles:
		frappe.throw(_("Only CEO or System Manager can revise budget proposals"))

	doc.status = "Revised"
	doc.rejection_reason = reason
	doc.save(ignore_permissions=True)

	if doc.proposed_by:
		_send_budget_notification(doc, "revised",
			[doc.proposed_by],
			extra={"revised_by": frappe.utils.get_fullname(frappe.session.user),
				   "reason": reason})

	return {"status": "revised"}


@frappe.whitelist()
def reject_budget_proposal(proposal_name, reason):
	"""CEO rejects a budget proposal."""
	if not reason:
		frappe.throw(_("Rejection reason is mandatory"))

	doc = frappe.get_doc("TS Budget Proposal", proposal_name, for_update=True)

	if doc.status != "Pending CEO":
		frappe.throw(_("Only proposals pending CEO approval can be rejected"))

	user_roles = frappe.get_roles(frappe.session.user)
	if "CEO" not in user_roles and "System Manager" not in user_roles:
		frappe.throw(_("Only CEO or System Manager can reject budget proposals"))

	doc.status = "Rejected"
	doc.rejection_reason = reason
	doc.save(ignore_permissions=True)

	if doc.proposed_by:
		_send_budget_notification(doc, "rejected",
			[doc.proposed_by],
			extra={"rejected_by": frappe.utils.get_fullname(frappe.session.user),
				   "reason": reason})

	return {"status": "rejected"}


@frappe.whitelist()
def resubmit_budget_proposal(proposal_name):
	"""DH resubmits a revised proposal."""
	doc = frappe.get_doc("TS Budget Proposal", proposal_name, for_update=True)

	if doc.status != "Revised":
		frappe.throw(_("Only revised proposals can be resubmitted"))

	if frappe.session.user not in (doc.proposed_by, doc.owner, "Administrator"):
		frappe.throw(_("Only the original proposer can resubmit"))

	doc.total_proposed = sum(flt(row.proposed_amount) for row in doc.budget_items)
	for row in doc.budget_items:
		row.ceo_approved_amount = row.proposed_amount

	doc.status = "Pending CEO"
	doc.rejection_reason = ""
	doc.save(ignore_permissions=True)

	_send_budget_notification(doc, "pending",
		_get_role_users("CEO"),
		extra={"proposed_by": frappe.utils.get_fullname(frappe.session.user),
			   "resubmitted": True})

	return {"status": "resubmitted"}


@frappe.whitelist()
def fetch_last_year_data(cost_center, fiscal_year):
	"""Fetch last year's budget and actual data for reference."""
	_check_budget_role()

	fy = frappe.get_doc("Fiscal Year", fiscal_year)
	fy_start = getdate(fy.year_start_date)

	# Get previous fiscal year
	prev_fy_start = add_months(fy_start, -12)

	prev_fy = frappe.db.get_value("Fiscal Year", {
		"year_start_date": get_first_day(prev_fy_start),
		"disabled": 0
	})

	if not prev_fy:
		return {"last_year_fiscal": None, "accounts": []}

	prev_fy_start_date, prev_fy_end_date = _get_fiscal_year_dates(prev_fy)

	# Get last year's budget
	budget_data = {}
	budgets = frappe.get_all("Budget",
		filters={"cost_center": cost_center, "fiscal_year": prev_fy, "docstatus": 1},
		fields=["name"])

	for budget in budgets:
		items = frappe.get_all("Budget Account",
			filters={"parent": budget.name},
			fields=["account", "budget_amount"])
		for item in items:
			budget_data[item.account] = budget_data.get(item.account, 0) + flt(item.budget_amount)

	# Get last year's actual spend (from GL Entry)
	actual_data = {}
	gl_entries = frappe.db.sql("""
		SELECT account, SUM(debit) - SUM(credit) as actual
		FROM `tabGL Entry`
		WHERE cost_center = %s
			AND posting_date BETWEEN %s AND %s
			AND is_cancelled = 0
		GROUP BY account
	""", (cost_center, prev_fy_start_date, prev_fy_end_date), as_dict=True)

	for gl in gl_entries:
		actual_data[gl.account] = flt(gl.actual)

	# Get all expense accounts for this company
	company = frappe.db.get_value("Cost Center", cost_center, "company")
	expense_accounts = frappe.get_all("Account",
		filters={"company": company, "root_type": "Expense", "is_group": 0, "disabled": 0},
		fields=["name", "account_name"],
		order_by="name")

	accounts = []
	total_budget = 0
	total_actual = 0

	for acc in expense_accounts:
		budget_amt = budget_data.get(acc.name, 0)
		actual_amt = actual_data.get(acc.name, 0)
		if budget_amt or actual_amt:
			accounts.append({
				"account": acc.name,
				"account_name": acc.account_name,
				"last_year_budget": budget_amt,
				"last_year_actual": actual_amt,
			})
			total_budget += budget_amt
			total_actual += actual_amt

	utilization_pct = (total_actual / total_budget * 100) if total_budget else 0

	return {
		"last_year_fiscal": prev_fy,
		"last_year_total_budget": total_budget,
		"last_year_total_actual": total_actual,
		"last_year_utilization_pct": round(utilization_pct, 1),
		"accounts": accounts,
	}


# ═══════════════════════════════════════════════════════════════════════
#  BUDGET CHECK FOR PO APPROVAL
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def check_budget_for_po(docname):
	"""Check if a PO has sufficient budget. Returns budget status for UI indicator."""
	# Any user who can read the PO can see budget status (Frappe permission check on get_doc)
	doc = frappe.get_doc("Purchase Order", docname)

	cost_center = doc.cost_center if hasattr(doc, "cost_center") and doc.cost_center else None
	if not cost_center:
		for item in (doc.get("items") or []):
			if item.cost_center:
				cost_center = item.cost_center
				break

	if not cost_center:
		return {
			"status": "no_cc",
			"message": _("No Cost Center set on this PO. Budget cannot be checked."),
			"color": "gray"
		}

	company = doc.company
	fiscal_year = _resolve_fiscal_year(company)

	if not fiscal_year:
		return {"status": "no_fy", "message": _("No active fiscal year found"), "color": "gray"}

	po_amount = flt(doc.grand_total)
	fy_start, fy_end = _get_fiscal_year_dates(fiscal_year)

	# Get budget for this CC + FY
	budget_info = _get_budget_info(cost_center, fiscal_year, company, fy_start, fy_end)

	if not budget_info["has_budget"]:
		return {
			"status": "no_budget",
			"message": _("No budget configured for {0} in {1}").format(cost_center, fiscal_year),
			"color": "gray",
			"cost_center": cost_center,
		}

	annual_budget = budget_info["annual_budget"]
	committed = budget_info["committed"]
	actual_spent = budget_info["actual_spent"]
	available = annual_budget - committed - actual_spent

	result = {
		"status": "ok",
		"cost_center": cost_center,
		"fiscal_year": fiscal_year,
		"annual_budget": annual_budget,
		"committed": committed,
		"actual_spent": actual_spent,
		"available": available,
		"po_amount": po_amount,
		"utilization_pct": round((committed + actual_spent) / annual_budget * 100, 1) if annual_budget else 0,
		"color": "green",
		"message": "",
	}

	if po_amount > available:
		result["status"] = "exceeded"
		result["color"] = "red"
		result["shortfall"] = po_amount - available
		result["message"] = _(
			"Budget exceeded for {0}. "
			"Annual Budget: {1}, Committed: {2}, Spent: {3}, Available: {4}, "
			"This PO: {5}, Shortfall: {6}"
		).format(
			cost_center,
			frappe.format_value(annual_budget, {"fieldtype": "Currency"}),
			frappe.format_value(committed, {"fieldtype": "Currency"}),
			frappe.format_value(actual_spent, {"fieldtype": "Currency"}),
			frappe.format_value(available, {"fieldtype": "Currency"}),
			frappe.format_value(po_amount, {"fieldtype": "Currency"}),
			frappe.format_value(po_amount - available, {"fieldtype": "Currency"}),
		)
	elif result["utilization_pct"] >= 80:
		result["status"] = "warning"
		result["color"] = "yellow"
		result["message"] = _("{0}% of annual budget utilized for {1}").format(
			result["utilization_pct"], cost_center)
	else:
		result["message"] = _("Budget available: {0}").format(
			frappe.format_value(available, {"fieldtype": "Currency"}))

	return result


def validate_budget_on_po_submit(doc):
	"""Legacy entry point preserved for callers expecting throw-on-breach.

	v2.10.0 routing:
	- Kill switch ON  (default): returns breach dict; caller (_submit_po_for_approval)
	  routes the PO into 'Pending Budget Override' status + auto-creates a
	  TS Budget Override Approval. NO throw.
	- Kill switch OFF (emergency): throws like the pre-v2.10.0 hard block.
	"""
	cost_center = _resolve_doc_cost_center(doc)
	if not cost_center:
		frappe.throw(_(
			"Cost Center is mandatory on Purchase Orders for budget control. "
			"Please set the Cost Center before submitting for approval."
		))

	# Legacy escape: explicit per-doc override flag wins (kept for migration parity).
	if cint(getattr(doc, "ts_budget_overridden", 0)):
		return {"status": "overridden", "has_breach": False, "budget_exceeded": False}

	breach = detect_budget_breach_for_po(doc)

	if not breach.get("has_breach"):
		return {"status": "ok", "has_breach": False, "budget_exceeded": False, **breach}

	if _is_override_flow_enabled():
		return {"status": "breach", "has_breach": True, "budget_exceeded": True, **breach}

	# Kill switch OFF — legacy hard-throw behavior.
	frappe.throw(_(
		"Insufficient budget for {0}.\n\n"
		"Annual Budget: {1}\n"
		"Already Committed (POs): {2}\n"
		"Already Spent (Invoices): {3}\n"
		"Available: {4}\n"
		"This PO: {5}\n"
		"Shortfall: {6}\n\n"
		"Please revise the PO amount or request a budget increase."
	).format(
		breach.get("cost_center", cost_center),
		frappe.format_value(breach.get("annual_budget_amount", 0), {"fieldtype": "Currency"}),
		frappe.format_value(breach.get("annual_committed_amount", 0), {"fieldtype": "Currency"}),
		frappe.format_value(breach.get("annual_spent_amount", 0), {"fieldtype": "Currency"}),
		frappe.format_value(breach.get("annual_available", 0), {"fieldtype": "Currency"}),
		frappe.format_value(breach.get("source_amount", 0), {"fieldtype": "Currency"}),
		frappe.format_value(
			max(flt(breach.get("annual_breach_delta", 0)), flt(breach.get("monthly_breach_delta", 0))),
			{"fieldtype": "Currency"},
		),
	))


@frappe.whitelist(methods=["POST"])
def ceo_budget_override(docname, reason):
	"""CEO overrides the budget block on a PO. Logs the override.

	DEPRECATED in v2.10.0 when ts_budget_override_flow_enabled is ON (default).
	Throws when override flow is enabled — callers must use the
	TS Budget Override Approval workflow instead. Re-enabled as an emergency
	escape valve when the kill switch is flipped OFF (per plan Q7).
	"""
	if _is_override_flow_enabled():
		frappe.throw(_(
			"ceo_budget_override is deprecated in v2.10.0. Use the TS Budget "
			"Override Approval workflow instead. To re-enable this legacy endpoint "
			"as an emergency escape valve, a System Manager must flip "
			"TS Settings → 'Budget Override Flow Enabled' to OFF."
		))
	if not reason:
		frappe.throw(_("Override reason is mandatory"))

	user_roles = frappe.get_roles(frappe.session.user)
	if not ({"CEO", "MD", "System Manager"} & set(user_roles)):
		frappe.throw(_("Only CEO, MD, or System Manager can override budget blocks"), frappe.PermissionError)

	doc = frappe.get_doc("Purchase Order", docname, for_update=True)

	# Prevent duplicate overrides
	if cint(doc.ts_budget_overridden):
		return {"status": "already_overridden", "message": _("Budget override already applied to this PO")}

	cost_center = doc.cost_center if hasattr(doc, "cost_center") and doc.cost_center else None
	if not cost_center:
		for item in (doc.get("items") or []):
			if item.cost_center:
				cost_center = item.cost_center
				break

	company = doc.company
	fiscal_year = _resolve_fiscal_year(company)
	fy_start, fy_end = _get_fiscal_year_dates(fiscal_year) if fiscal_year else (None, None)

	available = 0
	if fiscal_year and fy_start:
		budget_info = _get_budget_info(cost_center, fiscal_year, company, fy_start, fy_end)
		available = budget_info["annual_budget"] - budget_info["committed"] - budget_info["actual_spent"] if budget_info["has_budget"] else 0

	# Log the override
	log = frappe.get_doc({
		"doctype": "TS Budget Override Log",
		"parent": doc.name,
		"parenttype": "Purchase Order",
		"parentfield": "ts_budget_override_log",
		"override_date": now_datetime(),
		"override_by": frappe.session.user,
		"override_by_name": frappe.utils.get_fullname(frappe.session.user),
		"cost_center": cost_center,
		"budget_available": available,
		"po_amount": flt(doc.grand_total),
		"shortfall": flt(doc.grand_total) - available if available < flt(doc.grand_total) else 0,
		"reason": reason,
	})
	log.insert(ignore_permissions=True)

	# Set override flag on PO
	doc.db_set("ts_budget_overridden", 1, update_modified=True)

	# Visible audit trail in PO timeline (override is also captured in TS Budget Override Log)
	doc.add_comment(
		"Comment",
		text=_("Budget override applied by {0}. Reason: {1}").format(
			frappe.utils.get_fullname(frappe.session.user),
			frappe.utils.escape_html(reason),
		),
	)

	return {"status": "overridden", "message": _("Budget override applied. PO can now proceed through approval.")}


# ═══════════════════════════════════════════════════════════════════════
#  BUDGET HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _get_budget_info(cost_center, fiscal_year, company, fy_start, fy_end):
	"""Get budget summary for a cost center in a fiscal year."""
	result = {
		"has_budget": False,
		"annual_budget": 0,
		"committed": 0,
		"actual_spent": 0,
	}

	# Get total budget amount from all Budget records for this CC + FY
	budgets = frappe.get_all("Budget",
		filters={"cost_center": cost_center, "fiscal_year": fiscal_year, "docstatus": 1},
		fields=["name"])

	if not budgets:
		return result

	result["has_budget"] = True

	for budget in budgets:
		items = frappe.get_all("Budget Account",
			filters={"parent": budget.name},
			fields=["budget_amount"])
		result["annual_budget"] += sum(flt(i.budget_amount) for i in items)

	# Get committed amount (unbilled portion of submitted POs in this FY)
	# Uses grand_total * (1 - per_billed/100) to avoid double-counting with actual spent
	committed = frappe.db.sql("""
		SELECT COALESCE(SUM(grand_total * (1 - IFNULL(per_billed, 0) / 100)), 0) as total
		FROM `tabPurchase Order`
		WHERE cost_center = %s
			AND transaction_date BETWEEN %s AND %s
			AND company = %s
			AND docstatus = 1
			AND status NOT IN ('Closed', 'Cancelled')
	""", (cost_center, fy_start, fy_end, company))
	result["committed"] = flt(committed[0][0]) if committed else 0

	# Get actual spent (from GL Entry in FY date range)
	actual = frappe.db.sql("""
		SELECT COALESCE(SUM(debit) - SUM(credit), 0) as total
		FROM `tabGL Entry`
		WHERE cost_center = %s
			AND posting_date BETWEEN %s AND %s
			AND company = %s
			AND is_cancelled = 0
			AND voucher_type IN ('Purchase Invoice', 'Journal Entry')
	""", (cost_center, fy_start, fy_end, company))
	result["actual_spent"] = flt(actual[0][0]) if actual else 0

	return result


def _get_monthly_budget_info(cost_center, fiscal_year, company):
	"""Get accumulated monthly budget info if monthly distribution is set."""
	budgets = frappe.get_all("Budget",
		filters={"cost_center": cost_center, "fiscal_year": fiscal_year, "docstatus": 1},
		fields=["name", "monthly_distribution"])

	if not budgets or not budgets[0].monthly_distribution:
		return None

	distribution = budgets[0].monthly_distribution
	fy_start, fy_end = _get_fiscal_year_dates(fiscal_year)
	today = getdate(nowdate())

	# Build a list of months from FY start to today
	# For Indian FY (Apr 2026 - Mar 2027), months in order are: Apr, May, ..., Dec, Jan, Feb, Mar
	elapsed_months = set()
	current = fy_start
	while current <= today and current <= fy_end:
		elapsed_months.add(current.month)
		# Move to next month
		if current.month == 12:
			current = current.replace(year=current.year + 1, month=1, day=1)
		else:
			current = current.replace(month=current.month + 1, day=1)

	# Get accumulated percentage for elapsed months
	month_name_to_num = {
		"January": 1, "February": 2, "March": 3, "April": 4,
		"May": 5, "June": 6, "July": 7, "August": 8,
		"September": 9, "October": 10, "November": 11, "December": 12
	}

	accumulated_pct = 0
	dist_doc = frappe.get_doc("Monthly Distribution", distribution)
	for row in dist_doc.percentages:
		month_num = month_name_to_num.get(row.month, 0)
		if month_num in elapsed_months:
			accumulated_pct += flt(row.percentage_allocation)

	# Get total annual budget
	total_annual = 0
	for budget in budgets:
		items = frappe.get_all("Budget Account",
			filters={"parent": budget.name},
			fields=["budget_amount"])
		total_annual += sum(flt(i.budget_amount) for i in items)

	monthly_budget = total_annual * accumulated_pct / 100

	# Get committed + actual for comparison
	budget_info = _get_budget_info(cost_center, fiscal_year, company, fy_start, fy_end)

	return {
		"monthly_budget": monthly_budget,
		"monthly_committed": budget_info["committed"],
		"monthly_actual": budget_info["actual_spent"],
		"monthly_available": monthly_budget - budget_info["committed"] - budget_info["actual_spent"],
		"accumulated_pct": accumulated_pct,
	}


def _create_erpnext_budget(proposal):
	"""Create an ERPNext Budget record from an approved TS Budget Proposal."""
	# Check if budget already exists for this CC + FY — cancel it first
	existing = frappe.get_all("Budget",
		filters={
			"cost_center": proposal.cost_center,
			"fiscal_year": proposal.fiscal_year,
			"docstatus": 1
		},
		for_update=True)

	if existing:
		budget = frappe.get_doc("Budget", existing[0].name, for_update=True)
		budget.flags.ignore_permissions = True
		budget.cancel()

	budget = frappe.new_doc("Budget")
	budget.budget_against = "Cost Center"
	budget.cost_center = proposal.cost_center
	budget.company = proposal.company
	budget.fiscal_year = proposal.fiscal_year
	budget.monthly_distribution = proposal.monthly_distribution or ""

	# Budget actions
	budget.applicable_on_material_request = 1
	budget.action_if_annual_budget_exceeded_on_mr = "Warn"
	budget.action_if_accumulated_monthly_budget_exceeded_on_mr = "Warn"
	budget.applicable_on_purchase_order = 1
	budget.action_if_annual_budget_exceeded_on_po = "Stop"
	budget.action_if_accumulated_monthly_budget_exceeded_on_po = "Stop"
	budget.applicable_on_booking_actual_expenses = 1
	budget.action_if_annual_budget_exceeded = "Warn"
	budget.action_if_accumulated_monthly_budget_exceeded = "Warn"

	for item in proposal.budget_items:
		amount = flt(item.ceo_approved_amount) or flt(item.proposed_amount)
		if amount > 0:
			budget.append("accounts", {
				"account": item.account,
				"budget_amount": amount,
			})

	budget.flags.ignore_permissions = True
	budget.insert()
	budget.submit()

	return budget.name


# ═══════════════════════════════════════════════════════════════════════
#  BUDGET DASHBOARD DATA
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_budget_dashboard(fiscal_year=None, company=None):
	"""Return budget vs actual vs committed data for all cost centers."""
	_check_budget_role()

	if not fiscal_year:
		fiscal_year = _resolve_fiscal_year()
	if not company:
		company = frappe.defaults.get_global_default("company")

	if not fiscal_year:
		return {"data": [], "summary": {}}

	fy_start, fy_end = _get_fiscal_year_dates(fiscal_year)

	# Get all budgets for this FY
	budgets = frappe.db.sql("""
		SELECT
			b.cost_center,
			b.monthly_distribution,
			SUM(ba.budget_amount) as annual_budget
		FROM `tabBudget` b
		INNER JOIN `tabBudget Account` ba ON ba.parent = b.name
		WHERE b.fiscal_year = %s
			AND b.company = %s
			AND b.docstatus = 1
		GROUP BY b.cost_center, b.monthly_distribution
	""", (fiscal_year, company), as_dict=True)

	if not budgets:
		return {"data": [], "summary": {}}

	# Get committed (unbilled portion of submitted POs in FY) per CC
	committed_data = {}
	committed_rows = frappe.db.sql("""
		SELECT cost_center, COALESCE(SUM(grand_total * (1 - IFNULL(per_billed, 0) / 100)), 0) as total
		FROM `tabPurchase Order`
		WHERE transaction_date BETWEEN %s AND %s
			AND company = %s AND docstatus = 1
			AND status NOT IN ('Closed', 'Cancelled')
		GROUP BY cost_center
	""", (fy_start, fy_end, company), as_dict=True)
	for row in committed_rows:
		committed_data[row.cost_center] = flt(row.total)

	# Get actual spent per CC (from GL Entry in FY date range).
	# v2.29.x — expense-root accounts only + Stock Entry included; see the
	# MIRROR comment in ts_budget_dashboard.py (keep both queries in sync).
	actual_data = {}
	actual_rows = frappe.db.sql("""
		SELECT gle.cost_center, COALESCE(SUM(gle.debit) - SUM(gle.credit), 0) as total
		FROM `tabGL Entry` gle
		INNER JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE gle.posting_date BETWEEN %s AND %s
			AND gle.company = %s AND gle.is_cancelled = 0
			AND gle.voucher_type IN ('Purchase Invoice', 'Journal Entry', 'Stock Entry')
			AND acc.root_type = 'Expense'
		GROUP BY gle.cost_center
	""", (fy_start, fy_end, company), as_dict=True)
	for row in actual_rows:
		actual_data[row.cost_center] = flt(row.total)

	data = []
	total_budget = 0
	total_committed = 0
	total_actual = 0

	for b in budgets:
		cc = b.cost_center
		annual = flt(b.annual_budget)
		committed = committed_data.get(cc, 0)
		actual = actual_data.get(cc, 0)
		available = annual - committed - actual
		utilization = round((committed + actual) / annual * 100, 1) if annual else 0

		data.append({
			"cost_center": cc,
			"annual_budget": annual,
			"committed": committed,
			"actual_spent": actual,
			"available": available,
			"utilization_pct": utilization,
			"monthly_distribution": b.monthly_distribution or "None",
			"status": "Exceeded" if available < 0 else ("Warning" if utilization >= 80 else "OK"),
		})

		total_budget += annual
		total_committed += committed
		total_actual += actual

	data.sort(key=lambda x: x["utilization_pct"], reverse=True)

	return {
		"data": data,
		"summary": {
			"total_budget": total_budget,
			"total_committed": total_committed,
			"total_actual": total_actual,
			"total_available": total_budget - total_committed - total_actual,
			"overall_utilization": round((total_committed + total_actual) / total_budget * 100, 1) if total_budget else 0,
		}
	}


# ═══════════════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════

def _send_budget_notification(doc, action, recipients, extra=None):
	"""Send notification for budget proposal actions."""
	if not recipients:
		return

	extra = extra or {}
	esc = frappe.utils.escape_html
	subjects = {
		"pending": _("[Action Required] Budget Proposal for {0} ({1}) awaiting approval").format(
			esc(doc.cost_center), esc(doc.fiscal_year)),
		"approved": _("[Approved] Budget for {0} ({1}) — Approved & Activated").format(
			esc(doc.cost_center), esc(doc.fiscal_year)),
		"revised": _("[Revision Required] Budget Proposal for {0} sent back").format(
			esc(doc.cost_center)),
		"rejected": _("[Rejected] Budget Proposal for {0} ({1})").format(
			esc(doc.cost_center), esc(doc.fiscal_year)),
	}
	subject = subjects.get(action, _("Budget Proposal {0} — Update").format(doc.name))

	recipients = list(set(r for r in recipients if r and r != "Administrator"))

	for user in recipients:
		try:
			notification = frappe.new_doc("Notification Log")
			notification.for_user = user
			notification.from_user = frappe.session.user
			notification.document_type = "TS Budget Proposal"
			notification.document_name = doc.name
			notification.subject = subject
			notification.type = "Alert"
			notification.insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(
				title="Budget Notification Error ({0} → {1})".format(doc.name, user),
				message=frappe.get_traceback())

	try:
		amount_str = frappe.format_value(flt(doc.total_proposed), {"fieldtype": "Currency"})
		site_url = frappe.utils.get_url()
		doc_url = "{0}/app/bbf-budget-proposal/{1}".format(site_url, doc.name)

		reason_html = ""
		if extra.get("reason"):
			reason_html = "<p><strong>Reason:</strong> {0}</p>".format(esc(extra["reason"]))

		message = """
		<div style="font-family: Arial, sans-serif; max-width: 600px;">
			<p>Budget Proposal requires your attention:</p>
			<table style="border-collapse: collapse; width: 100%%; margin: 10px 0;">
				<tr><td style="padding: 5px; font-weight: bold;">Cost Center:</td><td style="padding: 5px;">{cc}</td></tr>
				<tr><td style="padding: 5px; font-weight: bold;">Fiscal Year:</td><td style="padding: 5px;">{fy}</td></tr>
				<tr><td style="padding: 5px; font-weight: bold;">Proposed Total:</td><td style="padding: 5px;">{amt}</td></tr>
				<tr><td style="padding: 5px; font-weight: bold;">Status:</td><td style="padding: 5px;">{status}</td></tr>
			</table>
			{reason}
			<p><a href="{url}" style="background: #2490EF; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px;">View Proposal</a></p>
		</div>
		""".format(
			cc=esc(doc.cost_center), fy=esc(doc.fiscal_year),
			amt=esc(amount_str), status=esc(doc.status),
			reason=reason_html, url=doc_url
		)

		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message,
			now=True,
			reference_doctype="TS Budget Proposal",
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(
			title="Budget Email Error ({0})".format(doc.name),
			message=frappe.get_traceback())


def _get_role_users(role):
	"""Get all active users with a specific role."""
	return frappe.db.sql_list("""
		SELECT DISTINCT hr.parent
		FROM `tabHas Role` hr
		INNER JOIN `tabUser` u ON u.name = hr.parent
		WHERE hr.role = %s
			AND hr.parenttype = 'User'
			AND u.enabled = 1
			AND hr.parent != 'Administrator'
	""", role)


# ═══════════════════════════════════════════════════════════════════════
#  CC BUDGET STATUS — for MR form warning banner
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_cc_budget_status(cost_center):
	"""Return monthly budget status for a Cost Center — used in MR form warning."""
	from frappe.utils import getdate, get_first_day, get_last_day, nowdate, flt

	if not cost_center:
		return {"has_budget": False}

	today = getdate(nowdate())
	month_start = get_first_day(today)
	month_end = get_last_day(today)
	month_name = today.strftime("%B %Y")

	# v2.9.17.9 — use date-based _resolve_fiscal_year instead of the stale global default
	# so the banner always reflects the CURRENT fiscal year (auto-rolls each April).
	fiscal_year = _resolve_fiscal_year()
	if not fiscal_year:
		return {"has_budget": False, "fiscal_year": None}

	budget = frappe.get_all("Budget",
		filters={
			"cost_center": cost_center,
			"fiscal_year": fiscal_year,
			"docstatus": 1,
		},
		limit=1)

	if not budget:
		return {"has_budget": False, "fiscal_year": fiscal_year}

	budget_doc = frappe.get_doc("Budget", budget[0].name)
	annual_amount = sum(flt(a.budget_amount) for a in budget_doc.accounts)

	# Calculate monthly amount (equal distribution = annual / 12)
	monthly_amount = annual_amount / 12

	# Calculate PO committed amount for this CC this month.
	# v2.29.x — ALIGNED to the CEO gate's _committed_for_period formula (header
	# cost centre, tax-inclusive grand_total minus billed portion, Closed/
	# Cancelled excluded, company-scoped). The old item-amount sum was net of
	# GST and disagreed with the gate/dashboard by exactly the tax component —
	# the banner must warn with the number the gate will actually enforce.
	company = frappe.db.get_value("Cost Center", cost_center, "company")
	row = frappe.db.sql("""
		SELECT
			COALESCE(SUM(grand_total * (1 - IFNULL(per_billed, 0) / 100)), 0),
			COALESCE(SUM(total * (1 - IFNULL(per_billed, 0) / 100)), 0)
		FROM `tabPurchase Order`
		WHERE cost_center = %s
			AND transaction_date BETWEEN %s AND %s
			AND company = %s
			AND docstatus = 1
			AND status NOT IN ('Closed', 'Cancelled')
	""", (cost_center, month_start, month_end, company))[0]
	used = flt(row[0])          # incl. GST — what the gate enforces
	used_excl_gst = flt(row[1])  # net of taxes — client's preferred reading

	remaining = monthly_amount - used

	return {
		"has_budget": True,
		"cost_center": cost_center,
		"fiscal_year": fiscal_year,
		"month_name": month_name,
		"budget_monthly": monthly_amount,
		"budget_annual": annual_amount,
		"used": used,
		"used_excl_gst": used_excl_gst,
		"remaining": remaining,
		"exceeded": used > monthly_amount,
	}


# ═══════════════════════════════════════════════════════════════════════
#  v2.10.0 — BUDGET OVERRIDE APPROVAL FLOW: breach detector
# ═══════════════════════════════════════════════════════════════════════


def _is_override_flow_enabled():
	"""Read kill switch from TS Settings via raw SQL (Lesson 171/172 — fail-closed).

	Returns True if flow is ON (default). Returns False ONLY when an explicit
	'0' is stored. Any other exception path defaults to True so a misconfigured
	settings row does not silently revert behavior to the legacy hard-block.
	"""
	try:
		row = frappe.db.sql(
			"SELECT value FROM tabSingles WHERE doctype=%s AND field=%s",
			("TS Settings", "ts_budget_override_flow_enabled"),
		)
		if not row:
			return True
		return cint(row[0][0]) == 1
	except Exception:
		return True


def _resolve_doc_cost_center(doc):
	"""Return the primary CC for a PO/MR — header first, then first item with CC."""
	primary = getattr(doc, "cost_center", None)
	if primary:
		return primary
	for item in (doc.get("items") or []):
		if getattr(item, "cost_center", None):
			return item.cost_center
	return None


def _resolve_doc_cc_list(doc):
	"""Return ordered, deduped list of CCs touched by a doc (header + items)."""
	seen = set()
	out = []
	primary = getattr(doc, "cost_center", None)
	if primary:
		seen.add(primary)
		out.append(primary)
	for item in (doc.get("items") or []):
		cc = getattr(item, "cost_center", None)
		if cc and cc not in seen:
			seen.add(cc)
			out.append(cc)
	return out


def _resolve_doc_amount(doc, ref_doctype):
	"""Authoritative amount for budget comparison.

	PO: grand_total.
	MR: sum(stock_qty * rate) when EVERY item has a rate. If any item lacks rate,
	    returns None — caller treats this as 'skip breach check' (plan Q3).
	"""
	if ref_doctype == "Purchase Order":
		return flt(getattr(doc, "grand_total", 0))
	if ref_doctype == "Material Request":
		items = doc.get("items") or []
		if not items:
			return 0.0
		total = 0.0
		for item in items:
			rate = flt(getattr(item, "rate", 0))
			qty = flt(getattr(item, "stock_qty", 0) or getattr(item, "qty", 0))
			if rate <= 0:
				return None  # Q3 default — skip
			total += qty * rate
		return total
	return None


def mr_amount_is_resolvable(mr_name):
	"""True when every item on this MR carries a rate — i.e. _resolve_doc_amount
	returns a number rather than the mr_no_rate skip.

	NOTE: this covers only the rate-skip. The no_cost_center / no_fiscal_year
	skips are not detected here — both are either impossible at MR submit
	(CC is mandatory there) or would skip the PO check identically, so the
	bypass outcome is the same. Used by the PO-submit bypass: "the MR was the
	gating event" only holds if the MR's own breach check was able to run.
	Fail-closed: any error means False, so the PO gets checked.
	"""
	try:
		mr = frappe.get_doc("Material Request", mr_name)
		return _resolve_doc_amount(mr, "Material Request") is not None
	except Exception:
		return False


def _share_amount_for_cc(doc, cc, ref_doctype, total_amount):
	"""Compute this doc's share against a single CC.

	Single-CC doc: full amount. Multi-CC doc: SUM of per-item allocations
	whose `cost_center` matches `cc` (PO: item.amount; MR: stock_qty * rate).
	"""
	cc_list = _resolve_doc_cc_list(doc)
	if len(cc_list) <= 1:
		return flt(total_amount)
	share = 0.0
	for item in (doc.get("items") or []):
		if getattr(item, "cost_center", None) != cc:
			continue
		if ref_doctype == "Purchase Order":
			share += flt(getattr(item, "amount", 0))
		elif ref_doctype == "Material Request":
			share += flt(getattr(item, "stock_qty", 0) or getattr(item, "qty", 0)) * flt(
				getattr(item, "rate", 0)
			)
	return share


def _committed_for_period(cost_center, period_start, period_end, company):
	"""Submitted POs falling in [period_start, period_end] — unbilled portion only."""
	row = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(grand_total * (1 - IFNULL(per_billed, 0) / 100)), 0)
		FROM `tabPurchase Order`
		WHERE cost_center = %s
		  AND transaction_date BETWEEN %s AND %s
		  AND company = %s
		  AND docstatus = 1
		  AND status NOT IN ('Closed', 'Cancelled')
		""",
		(cost_center, period_start, period_end, company),
	)
	return flt(row[0][0]) if row else 0


def _spent_for_period(cost_center, period_start, period_end, company):
	"""Actual GL spend in [period_start, period_end] for the given CC + company."""
	row = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(debit) - SUM(credit), 0)
		FROM `tabGL Entry`
		WHERE cost_center = %s
		  AND posting_date BETWEEN %s AND %s
		  AND company = %s
		  AND is_cancelled = 0
		  AND voucher_type IN ('Purchase Invoice', 'Journal Entry')
		""",
		(cost_center, period_start, period_end, company),
	)
	return flt(row[0][0]) if row else 0


def _compute_breach_snapshot(doc, ref_doctype):
	"""Core breach math used by detect_budget_breach_for_po / for_mr.

	Returns a dict matching the snapshot fields of TS Budget Override Approval.
	Never throws — always sets `has_breach` in {True, False}.
	"""
	cc_list = _resolve_doc_cc_list(doc)
	if not cc_list:
		return {"has_breach": False, "reason": "no_cost_center"}

	company = doc.company
	fiscal_year = _resolve_fiscal_year(company)
	if not fiscal_year:
		return {"has_breach": False, "reason": "no_fiscal_year"}

	total_amount = _resolve_doc_amount(doc, ref_doctype)
	if total_amount is None:
		return {"has_breach": False, "reason": "mr_no_rate"}

	fy_start, fy_end = _get_fiscal_year_dates(fiscal_year)

	# Q10 default — backdated docs respect their own month (doc.transaction_date),
	# not today. Falls back to today when transaction_date is unset.
	txn_date = getdate(doc.get("transaction_date") or nowdate())
	breach_month_start = get_first_day(txn_date)
	breach_month_end = get_last_day(txn_date)
	breach_month_name = txn_date.strftime("%B %Y")

	primary_breach = None
	breach_lines = []
	any_breach = False

	for cc in cc_list:
		budget_info = _get_budget_info(cc, fiscal_year, company, fy_start, fy_end)
		share = _share_amount_for_cc(doc, cc, ref_doctype, total_amount)

		if not budget_info["has_budget"]:
			breach_lines.append({
				"cost_center": cc,
				"share_amount": share,
				"breach_type": "None",
				"period_month": breach_month_name,
				"annual_budget": 0,
				"annual_available": 0,
				"annual_breach": 0,
				"monthly_budget": 0,
				"monthly_available": 0,
				"monthly_breach": 0,
			})
			continue

		annual_budget = flt(budget_info["annual_budget"])
		annual_committed = flt(budget_info["committed"])
		annual_spent = flt(budget_info["actual_spent"])
		annual_available = annual_budget - annual_committed - annual_spent

		# v1 — equal distribution. Monthly Distribution support deferred per plan §15.
		monthly_budget = annual_budget / 12.0 if annual_budget else 0
		monthly_committed = _committed_for_period(cc, breach_month_start, breach_month_end, company)
		monthly_spent = _spent_for_period(cc, breach_month_start, breach_month_end, company)
		monthly_available = monthly_budget - monthly_committed - monthly_spent

		annual_breach = max(0.0, share - annual_available)
		monthly_breach = max(0.0, share - monthly_available)

		if annual_breach > 0 and monthly_breach > 0:
			line_type = "Both"
		elif annual_breach > 0:
			line_type = "Annual"
		elif monthly_breach > 0:
			line_type = "Monthly"
		else:
			line_type = "None"

		if line_type != "None":
			any_breach = True

		breach_lines.append({
			"cost_center": cc,
			"share_amount": share,
			"breach_type": line_type,
			"period_month": breach_month_name,
			"annual_budget": annual_budget,
			"annual_available": annual_available,
			"annual_breach": annual_breach,
			"monthly_budget": monthly_budget,
			"monthly_available": monthly_available,
			"monthly_breach": monthly_breach,
		})

		combined = annual_breach + monthly_breach
		if line_type != "None" and (primary_breach is None or combined > primary_breach["combined"]):
			primary_breach = {
				"cc": cc,
				"annual_budget": annual_budget,
				"annual_committed": annual_committed,
				"annual_spent": annual_spent,
				"annual_available": annual_available,
				"annual_breach": annual_breach,
				"monthly_budget": monthly_budget,
				"monthly_committed": monthly_committed,
				"monthly_spent": monthly_spent,
				"monthly_available": monthly_available,
				"monthly_breach": monthly_breach,
				"type": line_type,
				"combined": combined,
			}

	if not any_breach:
		return {
			"has_breach": False,
			"reason": "within_budget",
			"breach_lines": breach_lines,
			"source_amount": total_amount,
		}

	p = primary_breach
	return {
		"has_breach": True,
		"breach_type": p["type"],
		"cost_center": p["cc"],
		"company": company,
		"fiscal_year": fiscal_year,
		"period_month": breach_month_name,
		"source_amount": total_amount,
		"annual_budget_amount": p["annual_budget"],
		"annual_committed_amount": p["annual_committed"],
		"annual_spent_amount": p["annual_spent"],
		"annual_available": p["annual_available"],
		"annual_breach_delta": p["annual_breach"],
		"annual_utilization_pct": (
			round((p["annual_committed"] + p["annual_spent"]) / p["annual_budget"] * 100, 1)
			if p["annual_budget"]
			else 0
		),
		"monthly_budget_amount": p["monthly_budget"],
		"monthly_committed_amount": p["monthly_committed"],
		"monthly_spent_amount": p["monthly_spent"],
		"monthly_available": p["monthly_available"],
		"monthly_breach_delta": p["monthly_breach"],
		"monthly_utilization_pct": (
			round((p["monthly_committed"] + p["monthly_spent"]) / p["monthly_budget"] * 100, 1)
			if p["monthly_budget"]
			else 0
		),
		"breach_lines": breach_lines,
	}


def detect_budget_breach_for_po(doc):
	"""Non-throwing breach detector for a Purchase Order."""
	return _compute_breach_snapshot(doc, "Purchase Order")


def detect_budget_breach_for_mr(doc):
	"""Non-throwing breach detector for a Material Request.

	Per plan Q3 default: MR with any item missing rate returns has_breach=False (skip).
	"""
	return _compute_breach_snapshot(doc, "Material Request")
