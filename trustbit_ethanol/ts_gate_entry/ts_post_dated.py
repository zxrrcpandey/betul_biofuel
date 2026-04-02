"""Post-Dated Entry API — check access, submit/approve/reject requests, scheduler expiry."""

import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime, getdate, add_to_date


# ═══════════════════════════════════════════════════════════════
#  CHECK ACCESS — called by JS on every form refresh
# ═══════════════════════════════════════════════════════════════

@frappe.whitelist()
def check_post_dated_access(doctype, token_number=None):
	"""Check if post-dated entry is currently enabled for a given DocType/token.
	Returns: {enabled, from_date, to_date, valid_until, request_name, source}
	"""
	now = now_datetime()
	today = getdate()

	# 1. Check Pre-Enable mode in TS Settings
	settings = frappe.get_cached_doc("TS Settings")
	if settings.get("enable_pre_post_dated"):
		pre_from = settings.get("pre_post_dated_from")
		pre_to = settings.get("pre_post_dated_to")
		if pre_from and pre_to:
			return {
				"enabled": True,
				"from_date": str(pre_from),
				"to_date": str(pre_to),
				"valid_until": str(pre_to) + " 23:59:59",
				"request_name": None,
				"source": "pre_enabled",
			}

	# 2. Check active requests
	filters = {
		"status": "Active",
		"valid_until": [">=", now],
	}

	requests = frappe.get_all(
		"TS Post Dated Entry Request",
		filters=filters,
		fields=["name", "request_type", "from_date", "to_date", "valid_until",
		        "valid_from", "token_number"],
		order_by="valid_until desc",
	)

	for req in requests:
		# Check valid_from
		if req.valid_from and get_datetime(req.valid_from) > now:
			continue

		# Check scope
		if req.request_type == "Day-wise" or req.request_type == "Pre-Enable All":
			return _build_access_result(req)

		elif req.request_type == "Transaction-wise":
			if token_number and req.token_number == token_number:
				return _build_access_result(req)

		elif req.request_type == "DocType-wise":
			allowed = frappe.get_all(
				"TS Post Dated DocType",
				filters={"parent": req.name, "enabled": 1},
				pluck="doctype_name",
			)
			if doctype in allowed:
				return _build_access_result(req)

	return {"enabled": False}


def _build_access_result(req):
	return {
		"enabled": True,
		"from_date": str(req.from_date),
		"to_date": str(req.to_date),
		"valid_until": str(req.valid_until),
		"request_name": req.name,
		"source": "request",
	}


# ═══════════════════════════════════════════════════════════════
#  SUBMIT / APPROVE / REJECT
# ═══════════════════════════════════════════════════════════════

@frappe.whitelist()
def submit_request(request_name):
	"""IT Head submits request for CEO approval."""
	doc = frappe.get_doc("TS Post Dated Entry Request", request_name)

	if doc.status != "Draft":
		frappe.throw(_("Only Draft requests can be submitted"))

	if not frappe.has_permission("TS Post Dated Entry Request", "write"):
		frappe.throw(_("You don't have permission to submit this request"))

	doc.db_set("status", "Pending Approval")

	# Notify CEO
	_send_notification(
		doc,
		action="Pending Approval",
		recipients=_get_ceo_users(),
		subject=_("Post-Dated Entry Request — Pending Your Approval"),
		message=_("{0} ({1}) has requested {2} post-dated entry access for {3} to {4}. Valid until {5}.\n\nReason: {6}").format(
			frappe.get_value("User", doc.requested_by, "full_name"),
			"IT Head",
			doc.request_type,
			doc.from_date,
			doc.to_date,
			doc.valid_until,
			doc.reason,
		),
	)

	frappe.msgprint(_("Request submitted for CEO approval"), indicator="blue")


@frappe.whitelist()
def approve_request(request_name):
	"""CEO approves the request."""
	doc = frappe.get_doc("TS Post Dated Entry Request", request_name)

	if doc.status != "Pending Approval":
		frappe.throw(_("Only Pending Approval requests can be approved"))

	# Validate CEO role (IT Head cannot approve even if they have other roles)
	user_roles = frappe.get_roles()
	is_ceo = "CEO" in user_roles or "MD" in user_roles
	is_sm = "System Manager" in user_roles and frappe.session.user == "Administrator"
	if not is_ceo and not is_sm:
		frappe.throw(_("Only CEO can approve post-dated entry requests"))

	# Self-approval prevention
	if doc.requested_by == frappe.session.user:
		frappe.throw(_("You cannot approve your own request"))

	now = now_datetime()
	doc.db_set("approved_by", frappe.session.user)
	doc.db_set("approved_date", now)
	doc.db_set("status", "Active")

	# If valid_from is not set, activate immediately
	if not doc.valid_from:
		doc.db_set("valid_from", now)

	# Notify IT Head
	_send_notification(
		doc,
		action="Approved",
		recipients=[doc.requested_by],
		subject=_("Post-Dated Entry Request Approved — {0}").format(doc.name),
		message=_("Your {0} post-dated entry request has been approved by {1}.\n\nBackdate range: {2} to {3}\nValid until: {4}").format(
			doc.request_type,
			frappe.get_value("User", frappe.session.user, "full_name"),
			doc.from_date,
			doc.to_date,
			doc.valid_until,
		),
	)

	frappe.msgprint(_("Request approved. Post-dated entry is now active."), indicator="green")


@frappe.whitelist()
def reject_request(request_name, reason=""):
	"""CEO rejects the request."""
	doc = frappe.get_doc("TS Post Dated Entry Request", request_name)

	if doc.status != "Pending Approval":
		frappe.throw(_("Only Pending Approval requests can be rejected"))

	user_roles = frappe.get_roles()
	is_ceo = "CEO" in user_roles or "MD" in user_roles
	is_sm = "System Manager" in user_roles and frappe.session.user == "Administrator"
	if not is_ceo and not is_sm:
		frappe.throw(_("Only CEO can reject post-dated entry requests"))

	if not reason:
		frappe.throw(_("Rejection reason is required"))

	doc.db_set("status", "Rejected")
	doc.db_set("rejection_reason", reason)

	# Notify IT Head
	_send_notification(
		doc,
		action="Rejected",
		recipients=[doc.requested_by],
		subject=_("Post-Dated Entry Request Rejected — {0}").format(doc.name),
		message=_("Your {0} post-dated entry request has been rejected by {1}.\n\nReason: {2}").format(
			doc.request_type,
			frappe.get_value("User", frappe.session.user, "full_name"),
			reason,
		),
	)

	frappe.msgprint(_("Request rejected."), indicator="red")


# ═══════════════════════════════════════════════════════════════
#  SERVER-SIDE VALIDATION — called from flow DocType controllers
# ═══════════════════════════════════════════════════════════════

def validate_post_dated_date(doctype, date_value, token_number=None):
	"""Validate that a backdated date is allowed. Called from flow DocType validate/before_insert.
	Returns the request name if valid, raises error if not.
	"""
	if not date_value:
		return None

	entry_date = getdate(date_value)
	today = getdate()

	# Current or future date — no post-dated check needed
	if entry_date >= today:
		if entry_date > today:
			frappe.throw(_("Future dates are not allowed. Cannot set date to {0}.").format(entry_date))
		return None

	# Date is in the past — check ALL active requests/pre-enable for one that covers this date
	access = check_post_dated_access(doctype, token_number)

	if not access.get("enabled"):
		frappe.throw(
			_("Post-dated entry is not enabled. Cannot set date to {0} (past date). "
			  "Contact IT Head to request post-dated entry access.").format(entry_date)
		)

	# Check if this specific date falls within the returned range
	allowed_from = getdate(access["from_date"])
	allowed_to = getdate(access["to_date"])

	if allowed_from <= entry_date <= allowed_to:
		return access.get("request_name")

	# The first match didn't cover the date — check ALL active requests
	now = now_datetime()
	all_requests = frappe.get_all(
		"TS Post Dated Entry Request",
		filters={"status": "Active", "valid_until": [">=", now]},
		fields=["name", "request_type", "from_date", "to_date", "valid_until", "valid_from", "token_number"],
		order_by="valid_until desc",
	)

	for req in all_requests:
		if req.valid_from and get_datetime(req.valid_from) > now:
			continue

		req_from = getdate(req.from_date)
		req_to = getdate(req.to_date)

		if not (req_from <= entry_date <= req_to):
			continue

		# Check scope
		if req.request_type in ("Day-wise", "Pre-Enable All"):
			return req.name
		elif req.request_type == "Transaction-wise":
			if token_number and req.token_number == token_number:
				return req.name
		elif req.request_type == "DocType-wise":
			allowed = frappe.get_all("TS Post Dated DocType", filters={"parent": req.name, "enabled": 1}, pluck="doctype_name")
			if doctype in allowed:
				return req.name

	frappe.throw(
		_("Backdating to {0} is not allowed. No active request covers this date for {1}.").format(
			entry_date, doctype
		)
	)


def add_post_dated_comment(doc, request_name, original_date):
	"""Add audit comment on a document that was backdated."""
	if not request_name:
		return

	comment_text = (
		"Post-Dated Entry: Date was set to {0} (backdated from {1}). "
		"Authorized by request {2}."
	).format(doc.get("entry_date") or doc.get("inspection_date") or "N/A", original_date, request_name)

	doc.add_comment("Info", comment_text)


# ═══════════════════════════════════════════════════════════════
#  SCHEDULER — expire requests + send warnings
# ═══════════════════════════════════════════════════════════════

def expire_post_dated_requests():
	"""Called every 5 minutes by scheduler. Expires active requests and sends warnings."""
	now = now_datetime()

	# 1. Expire active requests past their valid_until
	expired = frappe.get_all(
		"TS Post Dated Entry Request",
		filters={"status": "Active", "valid_until": ["<", now]},
		pluck="name",
	)
	for name in expired:
		frappe.db.set_value("TS Post Dated Entry Request", name, "status", "Expired")
		doc = frappe.get_doc("TS Post Dated Entry Request", name)
		_send_notification(
			doc,
			action="Expired",
			recipients=[doc.requested_by],
			subject=_("Post-Dated Entry Expired — {0}").format(name),
			message=_("Your {0} post-dated entry request has expired. Date fields are now locked to current date.").format(
				doc.request_type,
			),
		)

	if expired:
		frappe.db.commit()

	# 2. Send expiry warnings
	settings = frappe.get_cached_doc("TS Settings")
	warning_minutes = settings.get("post_dated_expiry_warning_minutes") or 30
	warning_threshold = add_to_date(now, minutes=warning_minutes)

	about_to_expire = frappe.get_all(
		"TS Post Dated Entry Request",
		filters={
			"status": "Active",
			"valid_until": ["<=", warning_threshold],
			"valid_until": [">=", now],
			"expiry_warning_sent": 0,
		},
		pluck="name",
	)

	for name in about_to_expire:
		doc = frappe.get_doc("TS Post Dated Entry Request", name)
		frappe.db.set_value("TS Post Dated Entry Request", name, "expiry_warning_sent", 1)
		_send_notification(
			doc,
			action="Expiring Soon",
			recipients=[doc.requested_by],
			subject=_("Post-Dated Entry Expiring in {0} Minutes — {1}").format(warning_minutes, name),
			message=_("Your {0} post-dated entry request will expire at {1}. "
			          "Notify operators to complete all pending backdated entries.").format(
				doc.request_type,
				doc.valid_until,
			),
		)

	if about_to_expire:
		frappe.db.commit()


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def _get_ceo_users():
	"""Get all users with CEO role."""
	return frappe.get_all(
		"Has Role",
		filters={"role": "CEO", "parenttype": "User"},
		pluck="parent",
	)


def _send_notification(doc, action, recipients, subject, message):
	"""Send Notification Log to recipients."""
	for user in recipients:
		if not frappe.db.exists("User", user):
			continue
		try:
			notification = frappe.get_doc({
				"doctype": "Notification Log",
				"for_user": user,
				"from_user": frappe.session.user,
				"type": "Alert",
				"document_type": "TS Post Dated Entry Request",
				"document_name": doc.name,
				"subject": subject,
				"email_content": message,
			})
			notification.insert(ignore_permissions=True)
		except Exception:
			pass  # Non-critical — don't block the action
