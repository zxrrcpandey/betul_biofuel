"""Live-data API for the Approval Flow Explorer page.

All endpoints are read-only. Restricted to debug/admin roles only.
Never writes, never mutates state.
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, add_to_date

ALLOWED_ROLES = {
	"System Manager", "Administrator", "IT Head",
	"CEO", "MD", "AVP", "General Manager",
	"HR Manager", "Accounts Manager",
}

HIDDEN_USERS = {"Administrator", "Guest", "admin@gmail.com"}


def _check_access():
	user_roles = set(frappe.get_roles(frappe.session.user))
	if not (ALLOWED_ROLES & user_roles):
		frappe.throw(_("You do not have access to the Approval Flow Explorer. Required role: {0}").format(
			", ".join(sorted(ALLOWED_ROLES))), frappe.PermissionError)


@frappe.whitelist()
def get_users_list():
	"""Users who appear in CC configs OR have an approval-related role."""
	_check_access()

	cc_users = frappe.db.sql("""
		SELECT DISTINCT user FROM `tabTS CC Approval User` WHERE user IS NOT NULL AND user != ''
	""", as_dict=False)
	cc_user_set = {r[0] for r in cc_users}

	role_users = frappe.db.sql("""
		SELECT DISTINCT hr.parent as email
		FROM `tabHas Role` hr
		INNER JOIN `tabUser` u ON u.name = hr.parent
		WHERE hr.parenttype = 'User'
		  AND u.enabled = 1
		  AND u.name NOT IN ('Administrator', 'Guest')
		  AND hr.role IN (
		    'CEO', 'MD', 'AVP', 'General Manager',
		    'Purchase Manager', 'Grain Purchase Manager', 'Purchase User',
		    'Department Head', 'IT Head', 'Accounts Manager', 'Accounts User',
		    'Stock Manager', 'Stock User'
		  )
	""", as_dict=True)

	all_emails = (cc_user_set | {r["email"] for r in role_users}) - HIDDEN_USERS
	if not all_emails:
		return []

	users = frappe.db.sql("""
		SELECT u.name as email, u.full_name, u.enabled
		FROM `tabUser` u
		WHERE u.name IN %(emails)s AND u.enabled = 1
		ORDER BY u.full_name
	""", {"emails": tuple(all_emails)}, as_dict=True)

	# Annotate with CC count
	for u in users:
		u["cc_count"] = frappe.db.count("TS CC Approval User", {"user": u["email"]})
		u["display"] = f"{u['full_name']} — {u['email']}"
		if u["cc_count"]:
			u["display"] += f" ({u['cc_count']} CCs)"

	return users


@frappe.whitelist()
def get_user_flow(email):
	"""Everything about one user's approval involvement."""
	_check_access()

	if not email or not frappe.db.exists("User", email):
		frappe.throw(_("User not found: {0}").format(email))

	user_doc = frappe.db.get_value("User", email, ["full_name", "enabled", "user_image"], as_dict=True)
	roles = [r for r in frappe.get_roles(email) if r not in ("All", "Guest", "Desk User")]

	# Initials for avatar
	full_name = user_doc.full_name or email.split("@")[0]
	parts = full_name.strip().split()
	initials = (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else full_name[:2].upper()

	# MR Creation authority
	creator_rows = frappe.db.sql("""
		SELECT c.cost_center, c.mr_approval_route, c.flow_type
		FROM `tabTS CC Approval User` u
		INNER JOIN `tabTS CC Approval Config` c ON c.name = u.parent
		WHERE u.user = %s AND u.action_type = 'Creator' AND c.is_active = 1
		ORDER BY c.cost_center
	""", (email,), as_dict=True)

	# MR Review/Approval authority
	approver_rows = frappe.db.sql("""
		SELECT c.cost_center, c.mr_approval_route, u.action_type, u.role, u.step_order
		FROM `tabTS CC Approval User` u
		INNER JOIN `tabTS CC Approval Config` c ON c.name = u.parent
		WHERE u.user = %s AND u.action_type IN ('Reviewer', 'Final Approver') AND c.is_active = 1
		ORDER BY c.cost_center, u.step_order
	""", (email,), as_dict=True)

	# Notify-only authority
	notify_rows = frappe.db.sql("""
		SELECT c.cost_center, c.mr_approval_route, u.role
		FROM `tabTS CC Approval User` u
		INNER JOIN `tabTS CC Approval Config` c ON c.name = u.parent
		WHERE u.user = %s AND u.action_type = 'Notify Only' AND c.is_active = 1
		ORDER BY c.cost_center
	""", (email,), as_dict=True)

	# PO Approval authority — via user's roles mapping to PO Approval Steps
	po_rows = []
	if roles:
		po_rows = frappe.db.sql("""
			SELECT r.purchase_category, r.rule_name, r.min_amount, r.max_amount,
			       s.step_order, s.role, s.action_type, s.is_manual_trigger,
			       s.can_revise, s.can_reject
			FROM `tabTS PO Approval Step` s
			INNER JOIN `tabTS PO Approval Rule` r ON r.name = s.parent
			WHERE s.role IN %(roles)s AND r.is_active = 1
			ORDER BY r.purchase_category, r.min_amount, s.step_order
		""", {"roles": tuple(roles)}, as_dict=True)

	return {
		"email": email,
		"full_name": user_doc.full_name,
		"enabled": user_doc.enabled,
		"initials": initials,
		"roles": roles,
		"stats": {
			"creator": len(creator_rows),
			"reviewer": sum(1 for r in approver_rows if r["action_type"] == "Reviewer"),
			"final_approver": sum(1 for r in approver_rows if r["action_type"] == "Final Approver"),
			"notify_only": len(notify_rows),
		},
		"create": creator_rows,
		"approve": approver_rows,
		"notify": notify_rows,
		"po": po_rows,
	}


@frappe.whitelist()
def get_user_pending(email):
	"""Live MR + PO docs where this user can act now."""
	_check_access()

	roles = [r for r in frappe.get_roles(email) if r not in ("All", "Guest", "Desk User")]
	if not roles:
		return {"po": [], "mr": []}

	# POs where user's role appears in a step at or before the current step
	po_rows = frappe.db.sql("""
		SELECT po.name, po.supplier, po.supplier_name, po.grand_total,
		       po.ts_approval_status, po.ts_submitted_by, po.ts_current_step,
		       po.ts_purchase_category, po.ts_approval_rule,
		       DATEDIFF(NOW(), po.modified) as days_pending
		FROM `tabPurchase Order` po
		WHERE po.docstatus = 0
		  AND (po.ts_approval_status LIKE 'Pending%%' OR po.ts_approval_status LIKE 'Awaiting%%')
		  AND po.ts_approval_rule IS NOT NULL
		  AND EXISTS (
		    SELECT 1 FROM `tabTS PO Approval Step` s
		    WHERE s.parent = po.ts_approval_rule
		      AND s.step_order >= IFNULL(po.ts_current_step, 1)
		      AND s.role IN %(roles)s
		  )
		  AND po.ts_submitted_by != %(email)s
		ORDER BY days_pending DESC
		LIMIT 50
	""", {"roles": tuple(roles), "email": email}, as_dict=True)

	# MRs where user's role appears in current step, filtered by CC config
	# Note: Material Request has no grand_total column — sum from child items
	mr_rows = frappe.db.sql("""
		SELECT mr.name, mr.cost_center,
		       (SELECT IFNULL(SUM(mri.amount), 0) FROM `tabMaterial Request Item` mri WHERE mri.parent = mr.name) as grand_total,
		       mr.ts_mr_status, mr.ts_mr_submitted_by, mr.ts_mr_current_step, mr.ts_mr_approval_route,
		       DATEDIFF(NOW(), mr.modified) as days_pending
		FROM `tabMaterial Request` mr
		WHERE mr.docstatus = 0
		  AND mr.ts_mr_status LIKE 'Pending%%'
		  AND mr.ts_mr_approval_route IS NOT NULL
		  AND EXISTS (
		    SELECT 1 FROM `tabTS MR Approval Step` s
		    WHERE s.parent = mr.ts_mr_approval_route
		      AND s.step_order >= IFNULL(mr.ts_mr_current_step, 1)
		      AND s.role IN %(roles)s
		  )
		  AND mr.ts_mr_submitted_by != %(email)s
		ORDER BY days_pending DESC
		LIMIT 50
	""", {"roles": tuple(roles), "email": email}, as_dict=True)

	# Further filter MRs by CC config — user must be in the CC's config as reviewer/final approver
	filtered_mrs = []
	for mr in mr_rows:
		cc_config = frappe.db.get_value("TS CC Approval Config",
			{"cost_center": mr["cost_center"], "is_active": 1}, "name")
		if not cc_config:
			filtered_mrs.append(mr)  # no CC config — role-only fallback
			continue
		allowed = frappe.db.sql("""
			SELECT 1 FROM `tabTS CC Approval User`
			WHERE parent = %s AND user = %s AND action_type IN ('Reviewer', 'Final Approver')
		""", (cc_config, email))
		if allowed:
			filtered_mrs.append(mr)

	return {"po": po_rows, "mr": filtered_mrs}


@frappe.whitelist()
def get_user_activity(email, days=14):
	"""Recent approval actions the user took (from TS Approval Log child tables)."""
	_check_access()
	days = cint(days) or 14
	cutoff = add_to_date(now_datetime(), days=-days)

	# TS Approval Log is a child table — query by parent doctype
	rows = frappe.db.sql("""
		SELECT parent, parenttype, action, from_state, to_state, action_by, action_date,
		       comment, action_by_role
		FROM `tabTS Approval Log`
		WHERE action_by = %s AND action_date >= %s
		ORDER BY action_date DESC
		LIMIT 30
	""", (email, cutoff), as_dict=True)
	return rows


@frappe.whitelist()
def get_user_notifications(email, days=14):
	"""Bell-icon notification history for approval docs."""
	_check_access()
	days = cint(days) or 14
	cutoff = add_to_date(now_datetime(), days=-days)

	rows = frappe.db.sql("""
		SELECT name, subject, document_type, document_name, creation, `read`
		FROM `tabNotification Log`
		WHERE for_user = %s
		  AND document_type IN ('Purchase Order', 'Material Request')
		  AND creation >= %s
		ORDER BY creation DESC
		LIMIT 30
	""", (email, cutoff), as_dict=True)
	return rows


@frappe.whitelist()
def get_po_rules():
	"""All active PO approval rules with their step chains."""
	_check_access()
	rules = frappe.db.sql("""
		SELECT name, purchase_category, rule_name, min_amount, max_amount, is_active
		FROM `tabTS PO Approval Rule`
		WHERE is_active = 1
		ORDER BY purchase_category, min_amount
	""", as_dict=True)

	for rule in rules:
		rule["steps"] = frappe.db.sql("""
			SELECT step_order, role, role_label, action_type, is_manual_trigger,
			       can_revise, can_reject
			FROM `tabTS PO Approval Step`
			WHERE parent = %s
			ORDER BY step_order
		""", (rule["name"],), as_dict=True)

	return rules


@frappe.whitelist()
def get_mr_routes():
	"""All MR approval routes with steps and mapped CCs."""
	_check_access()
	routes = frappe.db.sql("""
		SELECT name, route_name, is_active, description
		FROM `tabTS MR Approval Route`
		ORDER BY is_active DESC, name
	""", as_dict=True)

	for route in routes:
		route["steps"] = frappe.db.sql("""
			SELECT step_order, role, role_label, action_type,
			       can_revise, can_reject
			FROM `tabTS MR Approval Step`
			WHERE parent = %s
			ORDER BY step_order
		""", (route["name"],), as_dict=True)
		ccs = frappe.db.sql("""
			SELECT cost_center FROM `tabTS MR Route Cost Center` WHERE parent = %s
		""", (route["name"],), as_dict=True)
		route["ccs"] = [c["cost_center"] for c in ccs]

	return routes


@frappe.whitelist()
def get_traceable_docs(email=None, limit=30):
	"""List of recent PO/MR docs for the tracer dropdown."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause
	_check_access()
	limit = min(cint(limit) or 30, 100)

	# Confidentiality leak-plug: hide maize-confidential POs (name + amount) from
	# non-allowed roles (GM / HR Manager / AVP). No alias on this query → use the
	# backticked table name as the alias.
	conf_po = confidential_sql_clause("tabPurchase Order")
	po_docs = frappe.db.sql(f"""
		SELECT name, supplier_name, grand_total, ts_approval_status, ts_purchase_category
		FROM `tabPurchase Order`
		WHERE docstatus = 0
		  AND (ts_approval_status LIKE 'Pending%%' OR ts_approval_status LIKE 'Awaiting%%' OR ts_approval_status = 'Revised')
		  {conf_po}
		ORDER BY modified DESC
		LIMIT %s
	""", (limit,), as_dict=True)

	mr_docs = frappe.db.sql("""
		SELECT mr.name, mr.cost_center, mr.ts_mr_status,
		       (SELECT IFNULL(SUM(mri.amount), 0) FROM `tabMaterial Request Item` mri WHERE mri.parent = mr.name) as grand_total
		FROM `tabMaterial Request` mr
		WHERE mr.docstatus = 0
		  AND (mr.ts_mr_status LIKE 'Pending%%' OR mr.ts_mr_status LIKE 'On Hold%%' OR mr.ts_mr_status = 'Revised')
		ORDER BY mr.modified DESC
		LIMIT %s
	""", (limit,), as_dict=True)

	return {"po": po_docs, "mr": mr_docs}


@frappe.whitelist()
def trace_doc(doctype, name):
	"""Full flow analysis of one PO or MR. Returns node chain + summary."""
	_check_access()

	if doctype not in ("Purchase Order", "Material Request"):
		frappe.throw(_("Invalid doctype: {0}").format(doctype))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("Document not found: {0}").format(name))

	# Confidentiality leak-plug: _trace_po/_trace_mr expose the doc's amount +
	# supplier + cost center. Gate the per-doc read through has_permission (doc=)
	# so a maize-confidential PO/MR can't be traced by a non-allowed role.
	frappe.has_permission(doctype, "read", doc=name, throw=True)

	doc = frappe.get_doc(doctype, name)

	if doctype == "Purchase Order":
		return _trace_po(doc)
	else:
		return _trace_mr(doc)


def _trace_po(doc):
	status = doc.get("ts_approval_status") or "Not Submitted"
	rule_name = doc.get("ts_approval_rule")
	current_step = cint(doc.get("ts_current_step") or 0)
	submitted_by = doc.get("ts_submitted_by")

	nodes = [{
		"step": "CREATED",
		"role": "Purchase User",
		"user": doc.owner,
		"state": "done",
	}]

	if rule_name and frappe.db.exists("TS PO Approval Rule", rule_name):
		steps = frappe.db.sql("""
			SELECT step_order, role, role_label, action_type, is_manual_trigger
			FROM `tabTS PO Approval Step`
			WHERE parent = %s
			ORDER BY step_order
		""", (rule_name,), as_dict=True)
		for s in steps:
			if s["step_order"] < current_step:
				state = "done"
			elif s["step_order"] == current_step:
				state = "current"
			else:
				state = "pending"
			# Find current users for this role
			users_for_role = frappe.db.sql("""
				SELECT u.full_name FROM `tabHas Role` hr
				INNER JOIN `tabUser` u ON u.name = hr.parent
				WHERE hr.role = %s AND hr.parenttype = 'User' AND u.enabled = 1
				LIMIT 3
			""", (s["role"],), as_dict=True)
			user_label = ", ".join(u["full_name"] for u in users_for_role) or "(no users)"
			manual = " • manual trigger" if s.get("is_manual_trigger") else ""
			nodes.append({
				"step": f"STEP {s['step_order']}",
				"role": f"{s['role_label'] or s['role']} {s['action_type']}{manual}",
				"user": user_label,
				"state": state,
			})

	nodes.append({
		"step": "SUBMIT",
		"role": "PO Submitted" if status == "Approved" else "(awaiting final approval)",
		"user": "",
		"state": "done" if doc.docstatus == 1 else "pending",
	})

	return {
		"nodes": nodes,
		"summary": {
			"current": f"Status: {status}",
			"route": f"{doc.get('ts_purchase_category') or '-'} | Rule: {rule_name or '(not resolved)'}",
			"cost_center": doc.get("cost_center") or "-",
			"amount": frappe.utils.fmt_money(doc.get("grand_total") or 0),
			"submitted_by": submitted_by or "-",
			"days_pending": frappe.db.sql("SELECT DATEDIFF(NOW(), modified) FROM `tabPurchase Order` WHERE name = %s", (doc.name,))[0][0] or 0,
		},
	}


def _trace_mr(doc):
	status = doc.get("ts_mr_status") or "Not Submitted"
	route_name = doc.get("ts_mr_approval_route")
	current_step = cint(doc.get("ts_mr_current_step") or 0)
	submitted_by = doc.get("ts_mr_submitted_by")
	# Material Request has no grand_total column — sum from child items
	mr_total = sum(flt(item.amount) for item in (doc.get("items") or []))

	nodes = [{
		"step": "CREATED",
		"role": "Creator",
		"user": doc.owner,
		"state": "done",
	}]

	if route_name and frappe.db.exists("TS MR Approval Route", route_name):
		steps = frappe.db.sql("""
			SELECT step_order, role, role_label, action_type
			FROM `tabTS MR Approval Step`
			WHERE parent = %s
			ORDER BY step_order
		""", (route_name,), as_dict=True)

		# Get CC config users for the doc's cost center
		cc_config = frappe.db.get_value("TS CC Approval Config",
			{"cost_center": doc.get("cost_center"), "is_active": 1}, "name")

		for s in steps:
			if s["step_order"] < current_step:
				state = "done"
			elif s["step_order"] == current_step:
				state = "current"
			else:
				state = "pending"
			# Find users for this step via CC config (preferred) or role fallback
			user_label = "(no users)"
			if cc_config:
				cc_users = frappe.db.sql("""
					SELECT u.full_name FROM `tabTS CC Approval User` cu
					INNER JOIN `tabUser` u ON u.name = cu.user
					WHERE cu.parent = %s AND cu.step_order = %s AND cu.action_type IN ('Reviewer', 'Final Approver')
					LIMIT 3
				""", (cc_config, s["step_order"]), as_dict=True)
				if cc_users:
					user_label = ", ".join(u["full_name"] for u in cc_users)
			if user_label == "(no users)":
				role_users = frappe.db.sql("""
					SELECT u.full_name FROM `tabHas Role` hr
					INNER JOIN `tabUser` u ON u.name = hr.parent
					WHERE hr.role = %s AND hr.parenttype = 'User' AND u.enabled = 1
					LIMIT 3
				""", (s["role"],), as_dict=True)
				if role_users:
					user_label = ", ".join(u["full_name"] for u in role_users) + " (role fallback)"

			nodes.append({
				"step": f"STEP {s['step_order']}",
				"role": f"{s['role_label'] or s['role']} {s['action_type']}",
				"user": user_label,
				"state": state,
			})

	nodes.append({
		"step": "SUBMIT",
		"role": "MR Submitted" if status == "Approved" else "(awaiting final approval)",
		"user": "",
		"state": "done" if doc.docstatus == 1 else "pending",
	})

	return {
		"nodes": nodes,
		"summary": {
			"current": f"Status: {status}",
			"route": f"Route: {route_name or '(not resolved)'}",
			"cost_center": doc.get("cost_center") or "-",
			"amount": frappe.utils.fmt_money(mr_total),
			"submitted_by": submitted_by or "-",
			"days_pending": frappe.db.sql("SELECT DATEDIFF(NOW(), modified) FROM `tabMaterial Request` WHERE name = %s", (doc.name,))[0][0] or 0,
		},
	}


# ════════════════════════════════════════════════════════════════════════
# v2.9.12 Sprint 2 — Health Check endpoints
# ════════════════════════════════════════════════════════════════════════


@frappe.whitelist()
def get_health_check_report():
	"""Return aggregated Health Check report (all 7 validators).

	Read-only. Role-gated to ALLOWED_ROLES. Uses redis cache (60s TTL) at the
	validator layer; this endpoint is a thin wrapper.
	"""
	_check_access()
	from trustbit_ethanol.ts_gate_entry import health_check_validators
	return health_check_validators.run_all_validators()


@frappe.whitelist(methods=["POST"])
def acknowledge_issue(issue_id, suppressed_until, reason, category=None):
	"""Acknowledge a Health Check finding for a bounded period (TTL ≤ 90 days).

	POST-only per Lesson 175. Role-gated: System Manager + IT Head only
	(more restrictive than ALLOWED_ROLES — mutation requires admin).
	The TS Health Check Acknowledgment doctype's `validate()` enforces:
	- suppressed_until > today
	- suppressed_until ≤ today + 90 days
	- reason ≥ 10 chars
	- issue_id non-empty
	- issue_id unique (autoname)
	"""
	_check_access()
	user_roles = set(frappe.get_roles(frappe.session.user))
	if not ({"System Manager", "Administrator", "IT Head"} & user_roles):
		frappe.throw(
			_("Acknowledging Health Check issues requires System Manager or IT Head role."),
			frappe.PermissionError,
		)

	# Reject if already acknowledged + still active (avoid duplicate rows)
	existing = frappe.db.get_value(
		"TS Health Check Acknowledgment",
		{"issue_id": issue_id, "suppressed_until": [">=", frappe.utils.today()]},
		"name",
	)
	if existing:
		return {"ok": True, "already_acknowledged": True, "name": existing}

	doc = frappe.get_doc({
		"doctype": "TS Health Check Acknowledgment",
		"issue_id": issue_id,
		"category": category or "",
		"acknowledged_by": frappe.session.user,
		"acknowledged_on": frappe.utils.now(),
		"suppressed_until": suppressed_until,
		"reason": reason,
	})
	try:
		doc.insert(ignore_permissions=False)  # respect doctype permissions
	except frappe.DuplicateEntryError:
		# Race with concurrent ack — DB unique constraint caught it.
		# Converge with the pre-check branch above for stable client UX.
		return {"ok": True, "already_acknowledged": True, "name": issue_id}

	# Best-effort audit log per Lesson 238 (notification side-effects must
	# not block the success path).
	try:
		frappe.log_error(
			title="HC issue acknowledged",
			message=(
				f"user={frappe.session.user} issue_id={issue_id} "
				f"until={suppressed_until} reason_len={len(reason)}"
			),
		)
	except Exception:
		pass

	# Invalidate validator cache so the suppression is reflected immediately
	try:
		frappe.cache().delete_keys("health_check_v1")
	except Exception:
		pass

	return {"ok": True, "already_acknowledged": False, "name": doc.name}


@frappe.whitelist()
def get_doc_capability(doctype, name):
	"""Return per-doc capability matrix (Sprint 3)."""
	_check_access()
	from trustbit_ethanol.ts_gate_entry import health_check_simulators
	return health_check_simulators.build_doc_capability_matrix(doctype, name)


@frappe.whitelist()
def get_flow_capability(flow_type, flow_name):
	"""Return per-flow capability simulator data (Sprint 3).

	flow_type: "MR Route" | "PO Rule"
	"""
	_check_access()
	from trustbit_ethanol.ts_gate_entry import health_check_simulators
	return health_check_simulators.build_flow_capability_simulator(flow_type, flow_name)


@frappe.whitelist()
def get_health_check_targets():
	"""Return picker data for the simulators — list of stuck docs + active flows."""
	_check_access()
	stuck_docs = []
	for dt, status_field in (("Material Request", "ts_mr_status"),
	                          ("Purchase Order", "ts_approval_status")):
		rows = frappe.db.sql(
			f"""SELECT name, {status_field} AS status, docstatus,
			           DATEDIFF(NOW(), modified) AS days_stuck
			    FROM `tab{dt}`
			    WHERE
			      (docstatus = 0 AND {status_field} IN
			        ('Approved','Rejected','Pending Dept. User','Pending Dept. Head',
			         'Pending AVP','Pending CEO','Pending GM','Pending Stores Manager',
			         'Revised','On Hold'))
			      OR (docstatus = 1 AND {status_field} = 'Revised')
			      OR (docstatus = 1 AND {status_field} LIKE 'Pending %%' AND DATEDIFF(NOW(), modified) >= 7)
			      OR (docstatus = 2)
			    ORDER BY days_stuck DESC LIMIT 50""",
			as_dict=True,
		)
		for r in rows:
			r["doctype"] = dt
			stuck_docs.append(r)

	mr_routes = [r.name for r in frappe.db.sql(
		"SELECT name FROM `tabTS MR Approval Route` WHERE is_active = 1 ORDER BY name",
		as_dict=True,
	)]
	po_rules = [r.name for r in frappe.db.sql(
		"SELECT name FROM `tabTS PO Approval Rule` WHERE is_active = 1 ORDER BY name",
		as_dict=True,
	)]

	return {
		"stuck_docs": stuck_docs,
		"mr_routes": mr_routes,
		"po_rules": po_rules,
	}
