import frappe
from frappe import _
from frappe.utils import now_datetime, flt, cint, add_to_date, time_diff_in_hours, format_datetime


# ═══════════════════════════════════════════════════════════════════════
#  PUBLIC API — Whitelisted methods called from JS
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_approval_context(doctype, docname):
	"""Return approval context for the current user on a PO or MR.

	Called on form refresh to determine which buttons to show.
	"""
	doc = frappe.get_doc(doctype, docname)

	settings = frappe.get_single("BBF Settings")
	if doctype == "Purchase Order" and not settings.enable_po_approval:
		return {"approval_enabled": False}
	if doctype == "Material Request" and not settings.enable_mr_approval:
		return {"approval_enabled": False}

	user_info = _get_user_approval_role(frappe.session.user)

	if doctype == "Material Request":
		return _get_mr_approval_context(doc, user_info, settings)

	return _get_po_approval_context(doc, user_info, settings)


@frappe.whitelist()
def submit_for_approval(doctype, docname):
	"""Submit a Draft PO/MR into the approval chain."""
	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Purchase Order":
		_validate_po_submittable(doc)

		status = doc.bbf_approval_status
		if status and status not in ("", "Draft"):
			frappe.throw(_("This PO is already in the approval chain (status: {0})").format(status))

		required_level = _get_required_level(flt(doc.grand_total))
		first_level = _get_level_info(1)
		if not first_level:
			frappe.throw(_("No active approval level 1 found in BBF Approval Limit"))

		next_state = f"Pending {first_level['role_label'] or first_level['role']}"
		_log_approval_action(doc, "Submitted", "Draft", next_state,
			po_amount=flt(doc.grand_total))

		doc.db_set({
			"bbf_approval_status": next_state,
			"bbf_current_level": first_level["level"],
			"bbf_required_level": required_level,
			"bbf_submitted_by": frappe.session.user,
			"bbf_amount_at_submission": flt(doc.grand_total),
			"bbf_last_action": f"Submitted for approval on {now_datetime().strftime('%d %b %Y %H:%M')}",
		}, update_modified=True)

		_send_approval_notification(doc, "pending_approval",
			_get_role_users(first_level["role"]),
			extra={"next_role": first_level["role_label"] or first_level["role"]})

		return {"status": "ok", "next_state": next_state}

	elif doctype == "Material Request":
		_submit_mr_for_approval(doc)
		return {"status": "ok", "next_state": "Pending Department Head"}


@frappe.whitelist()
def approve_document(doctype, docname, comment=""):
	"""Approve action — final-approve or forward to next level based on amount."""
	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _approve_mr(doc, comment)

	user_info = _get_user_approval_role(frappe.session.user)
	if not user_info:
		frappe.throw(_("You don't have an approval role configured in BBF Approval Limit"))

	current_level = cint(doc.bbf_current_level)
	if user_info["level"] != current_level:
		frappe.throw(
			_("This PO is at Level {0} ({1}), but your role is Level {2} ({3})").format(
				current_level, doc.bbf_approval_status,
				user_info["level"], user_info["role"]
			)
		)

	# Server-side amount tamper detection
	_validate_amount_unchanged(doc)

	po_amount = flt(doc.grand_total)
	settings = frappe.get_single("BBF Settings")
	user_limit = flt(user_info["limit"])
	can_final = user_info["can_final_approve"]

	# Determine: final approve or forward?
	is_final = False
	if can_final:
		if settings.enable_fast_track:
			# Fast-track: approve if within limit (0 = unlimited)
			is_final = (user_limit == 0 or po_amount <= user_limit)
		else:
			# No fast-track: only final if at the required level
			is_final = (user_info["level"] >= cint(doc.bbf_required_level))

	current_state = doc.bbf_approval_status
	role_display = user_info["role_label"] or user_info["role"]

	if is_final:
		# FINAL APPROVAL — submit the PO
		_log_approval_action(doc, "Final Approved", current_state, "Approved",
			comment=comment, po_amount=po_amount)

		doc.db_set({
			"bbf_approval_status": "Approved",
			"bbf_approved_by": frappe.session.user,
			"bbf_approved_date": now_datetime(),
			"bbf_last_action": f"Final Approved by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
		}, update_modified=True)

		# Submit the PO (docstatus 0 → 1)
		doc.reload()
		doc.submit()

		# Notify all stakeholders
		all_users = _get_chain_users(doc)
		if doc.bbf_submitted_by:
			all_users.add(doc.bbf_submitted_by)
		_send_approval_notification(doc, "final_approved", list(all_users),
			extra={"approved_by": role_display})

		return {"status": "approved", "message": f"PO {doc.name} has been final approved and submitted"}

	else:
		# FORWARD to next level
		next_level = _get_next_level(user_info["level"])
		if not next_level:
			frappe.throw(_("No higher approval level configured. Cannot forward."))

		next_role_display = next_level["role_label"] or next_level["role"]
		next_state = f"Pending {next_role_display}"

		_log_approval_action(doc, "Forwarded", current_state, next_state,
			comment=comment, po_amount=po_amount)

		doc.db_set({
			"bbf_approval_status": next_state,
			"bbf_current_level": next_level["level"],
			"bbf_last_action": f"Forwarded by {role_display} to {next_role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
		}, update_modified=True)

		_send_approval_notification(doc, "pending_approval",
			_get_role_users(next_level["role"]),
			extra={"forwarded_by": role_display, "next_role": next_role_display})

		return {"status": "forwarded", "next_state": next_state}


@frappe.whitelist()
def revise_document(doctype, docname, reason, revise_to_level=None, comment=""):
	"""Send a PO/MR back for revision."""
	if not reason:
		frappe.throw(_("Revision reason is mandatory"))

	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _revise_mr(doc, reason, comment)

	user_info = _get_user_approval_role(frappe.session.user)
	if not user_info:
		frappe.throw(_("You don't have an approval role configured"))

	current_level = cint(doc.bbf_current_level)
	if user_info["level"] != current_level:
		frappe.throw(_("You cannot revise this PO — it is not at your approval level"))

	if not user_info.get("can_revise"):
		frappe.throw(_("Your approval role does not have revision permission"))

	current_state = doc.bbf_approval_status
	role_display = user_info["role_label"] or user_info["role"]

	_log_approval_action(doc, "Revised", current_state, "Revised",
		comment=comment or reason, po_amount=flt(doc.grand_total),
		revision_target=str(revise_to_level or ""))

	doc.db_set({
		"bbf_approval_status": "Revised",
		"bbf_revision_count": cint(doc.bbf_revision_count) + 1,
		"bbf_revision_reason": reason,
		"bbf_revised_by": f"{frappe.session.user} ({role_display})",
		"bbf_last_action": f"Revised by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	# Notify the original submitter
	recipients = []
	if doc.bbf_submitted_by:
		recipients.append(doc.bbf_submitted_by)
	if doc.owner and doc.owner not in recipients:
		recipients.append(doc.owner)

	_send_approval_notification(doc, "revised", recipients,
		extra={"revised_by": role_display, "reason": reason})

	return {"status": "revised", "message": f"PO {doc.name} has been sent back for revision"}


@frappe.whitelist()
def reject_document(doctype, docname, reason, comment=""):
	"""Reject a PO/MR. Terminal state."""
	if not reason:
		frappe.throw(_("Rejection reason is mandatory"))

	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _reject_mr(doc, reason, comment)

	user_info = _get_user_approval_role(frappe.session.user)
	if not user_info:
		frappe.throw(_("You don't have an approval role configured"))

	current_level = cint(doc.bbf_current_level)
	if user_info["level"] != current_level:
		frappe.throw(_("You cannot reject this PO — it is not at your approval level"))

	current_state = doc.bbf_approval_status
	role_display = user_info["role_label"] or user_info["role"]

	_log_approval_action(doc, "Rejected", current_state, "Rejected",
		comment=comment or reason, po_amount=flt(doc.grand_total))

	doc.db_set({
		"bbf_approval_status": "Rejected",
		"bbf_last_action": f"Rejected by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	# Cancel the PO (docstatus → 2)
	doc.reload()
	if doc.docstatus == 0:
		doc.flags.ignore_permissions = True
		doc.cancel()

	# Notify all stakeholders
	all_users = _get_chain_users(doc)
	if doc.bbf_submitted_by:
		all_users.add(doc.bbf_submitted_by)
	_send_approval_notification(doc, "rejected", list(all_users),
		extra={"rejected_by": role_display, "reason": reason})

	return {"status": "rejected", "message": f"PO {doc.name} has been rejected"}


@frappe.whitelist()
def resubmit_document(doctype, docname, mode="restart"):
	"""Resubmit a revised PO/MR back into the approval chain."""
	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _resubmit_mr(doc)

	if doc.bbf_approval_status != "Revised":
		frappe.throw(_("Only revised POs can be resubmitted"))

	# Recalculate required level (amount may have changed during revision)
	po_amount = flt(doc.grand_total)
	required_level = _get_required_level(po_amount)

	if mode == "restart":
		target_level_info = _get_level_info(1)
		resubmit_label = "Restart from Department Head"
	else:
		# Re-enter at the level that revised it
		# Parse revised_by to find the level
		revised_level = _find_reviser_level(doc)
		target_level_info = _get_level_info(revised_level) if revised_level else _get_level_info(1)
		resubmit_label = f"Re-enter at reviser level ({target_level_info['role_label'] or target_level_info['role']})"

	if not target_level_info:
		frappe.throw(_("Target approval level not found"))

	target_role_display = target_level_info["role_label"] or target_level_info["role"]
	next_state = f"Pending {target_role_display}"

	_log_approval_action(doc, "Resubmitted", "Revised", next_state,
		po_amount=po_amount, resubmit_mode=mode)

	doc.db_set({
		"bbf_approval_status": next_state,
		"bbf_current_level": target_level_info["level"],
		"bbf_required_level": required_level,
		"bbf_amount_at_submission": po_amount,
		"bbf_resubmit_mode": resubmit_label,
		"bbf_last_action": f"Resubmitted ({mode}) on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	_send_approval_notification(doc, "pending_approval",
		_get_role_users(target_level_info["role"]),
		extra={"next_role": target_role_display, "resubmitted": True})

	return {"status": "resubmitted", "next_state": next_state}


# ═══════════════════════════════════════════════════════════════════════
#  MR-SPECIFIC HANDLERS (single-level approval)
# ═══════════════════════════════════════════════════════════════════════

def _submit_mr_for_approval(doc):
	"""Submit MR for Department Head approval."""
	status = doc.bbf_mr_status
	if status and status not in ("", "Draft"):
		frappe.throw(_("This MR is already in the approval chain (status: {0})").format(status))

	_log_mr_action(doc, "Submitted", "Draft", "Pending Department Head")

	doc.db_set({
		"bbf_mr_status": "Pending Department Head",
	}, update_modified=True)

	_send_approval_notification(doc, "mr_pending",
		_get_role_users("Department Head"),
		extra={"next_role": "Department Head"})


def _approve_mr(doc, comment=""):
	"""Approve MR — single level, Dept Head only."""
	if doc.bbf_mr_status != "Pending Department Head":
		frappe.throw(_("This MR is not pending Department Head approval"))

	if not frappe.has_permission("Material Request", "write", doc.name):
		frappe.throw(_("You don't have permission to approve this MR"))

	user_roles = frappe.get_roles(frappe.session.user)
	if "Department Head" not in user_roles:
		frappe.throw(_("Only Department Head can approve Material Requests"))

	_log_mr_action(doc, "Final Approved", "Pending Department Head", "Approved", comment=comment)

	doc.db_set({
		"bbf_mr_status": "Approved",
		"bbf_mr_approved_by": frappe.session.user,
		"bbf_mr_approved_date": now_datetime(),
	}, update_modified=True)

	# Submit the MR (docstatus 0 → 1)
	doc.reload()
	doc.submit()

	# Notify owner
	_send_approval_notification(doc, "mr_approved", [doc.owner],
		extra={"approved_by": "Department Head"})

	return {"status": "approved", "message": f"MR {doc.name} has been approved and submitted"}


def _revise_mr(doc, reason, comment=""):
	"""Revise MR — send back to creator."""
	if doc.bbf_mr_status != "Pending Department Head":
		frappe.throw(_("This MR is not pending Department Head approval"))

	user_roles = frappe.get_roles(frappe.session.user)
	if "Department Head" not in user_roles:
		frappe.throw(_("Only Department Head can revise Material Requests"))

	_log_mr_action(doc, "Revised", "Pending Department Head", "Revised", comment=comment or reason)

	doc.db_set({
		"bbf_mr_status": "Revised",
		"bbf_mr_revision_reason": reason,
	}, update_modified=True)

	_send_approval_notification(doc, "mr_revised", [doc.owner],
		extra={"revised_by": "Department Head", "reason": reason})

	return {"status": "revised"}


def _reject_mr(doc, reason, comment=""):
	"""Reject MR."""
	if doc.bbf_mr_status != "Pending Department Head":
		frappe.throw(_("This MR is not pending Department Head approval"))

	user_roles = frappe.get_roles(frappe.session.user)
	if "Department Head" not in user_roles:
		frappe.throw(_("Only Department Head can reject Material Requests"))

	_log_mr_action(doc, "Rejected", "Pending Department Head", "Rejected", comment=comment or reason)

	doc.db_set({
		"bbf_mr_status": "Rejected",
	}, update_modified=True)

	doc.reload()
	if doc.docstatus == 0:
		doc.flags.ignore_permissions = True
		doc.cancel()

	_send_approval_notification(doc, "mr_rejected", [doc.owner],
		extra={"rejected_by": "Department Head", "reason": reason})

	return {"status": "rejected"}


def _resubmit_mr(doc):
	"""Resubmit a revised MR."""
	if doc.bbf_mr_status != "Revised":
		frappe.throw(_("Only revised MRs can be resubmitted"))

	_log_mr_action(doc, "Resubmitted", "Revised", "Pending Department Head")

	doc.db_set({
		"bbf_mr_status": "Pending Department Head",
		"bbf_mr_revision_reason": "",
	}, update_modified=True)

	_send_approval_notification(doc, "mr_pending",
		_get_role_users("Department Head"),
		extra={"next_role": "Department Head", "resubmitted": True})

	return {"status": "resubmitted"}


# ═══════════════════════════════════════════════════════════════════════
#  APPROVAL ROUTING LOGIC
# ═══════════════════════════════════════════════════════════════════════

def _get_required_level(amount):
	"""Determine the highest approval level needed for this PO amount.

	Finds the LOWEST level whose limit >= amount AND can_final_approve.
	Limit of 0 = unlimited (matches any amount).

	Example:
		₹8,000  → Level 1 (Dept Head, limit ₹10K)
		₹50,000 → Level 2 (GM, limit ₹1L)
		₹4L     → Level 3 (CEO, limit ₹6L)
		₹10L    → Level 4 (MD, unlimited)
	"""
	limits = frappe.get_all("BBF Approval Limit",
		filters={"is_active": 1, "can_final_approve": 1},
		fields=["role", "approval_level", "approval_limit"],
		order_by="approval_level asc")

	if not limits:
		frappe.throw(_("No active approval levels configured in BBF Approval Limit"))

	for ll in limits:
		limit_amount = flt(ll.approval_limit)
		# 0 = unlimited — can approve any amount
		if limit_amount == 0 or amount <= limit_amount:
			return ll.approval_level

	# Shouldn't reach here if MD has unlimited, but fallback to highest level
	return limits[-1].approval_level


def _get_user_approval_role(user):
	"""Determine which approval role (if any) this user has.

	If user has multiple approval roles, use the HIGHEST level
	(so a CEO who also has Dept Head role acts as CEO).
	"""
	user_roles = frappe.get_roles(user)

	limits = frappe.get_all("BBF Approval Limit",
		filters={"is_active": 1, "role": ["in", user_roles]},
		fields=["role", "role_label", "approval_level", "approval_limit",
				"can_final_approve", "can_revise", "revise_to_levels",
				"notify_on_pending", "notify_on_approval"],
		order_by="approval_level desc")

	if not limits:
		return None

	highest = limits[0]
	return {
		"role": highest.role,
		"role_label": highest.role_label or highest.role,
		"level": highest.approval_level,
		"limit": flt(highest.approval_limit),
		"can_final_approve": highest.can_final_approve,
		"can_revise": highest.can_revise,
		"revise_to_levels": highest.revise_to_levels,
	}


def _get_next_level(current_level):
	"""Get the next active approval level above current."""
	result = frappe.get_all("BBF Approval Limit",
		filters={"is_active": 1, "approval_level": [">", current_level]},
		fields=["role", "role_label", "approval_level as level", "approval_limit as limit",
				"can_final_approve"],
		order_by="approval_level asc",
		limit=1)
	return result[0] if result else None


def _get_level_info(level):
	"""Get approval limit info for a specific level."""
	result = frappe.get_all("BBF Approval Limit",
		filters={"is_active": 1, "approval_level": level},
		fields=["role", "role_label", "approval_level as level", "approval_limit as limit",
				"can_final_approve", "can_revise", "revise_to_levels"],
		limit=1)
	return result[0] if result else None


def _find_reviser_level(doc):
	"""Find the approval level of the person who last revised this PO."""
	logs = frappe.get_all("BBF Approval Log",
		filters={"parent": doc.name, "parenttype": doc.doctype, "action": "Revised"},
		fields=["action_by_role"],
		order_by="action_date desc",
		limit=1)

	if not logs:
		return 1

	role = logs[0].action_by_role
	level_info = frappe.get_all("BBF Approval Limit",
		filters={"role": role, "is_active": 1},
		fields=["approval_level"],
		limit=1)

	return level_info[0].approval_level if level_info else 1


# ═══════════════════════════════════════════════════════════════════════
#  VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def _validate_po_submittable(doc):
	"""Validate PO is in a state that can be submitted for approval."""
	if doc.docstatus != 0:
		frappe.throw(_("Only Draft POs can be submitted for approval"))

	if flt(doc.grand_total) <= 0:
		frappe.throw(_("PO must have a positive amount to submit for approval"))

	items = doc.get("items") or []
	if not items:
		frappe.throw(_("PO must have at least one item to submit for approval"))


def _validate_amount_unchanged(doc):
	"""Server-side check that PO amount hasn't been tampered with during approval."""
	submitted_amount = flt(doc.bbf_amount_at_submission)
	current_amount = flt(doc.grand_total)

	if submitted_amount and submitted_amount != current_amount:
		frappe.throw(
			_("PO amount has changed from {0} to {1} during approval. "
			  "This PO must be revised and resubmitted.").format(
				frappe.format_value(submitted_amount, {"fieldtype": "Currency"}),
				frappe.format_value(current_amount, {"fieldtype": "Currency"})
			)
		)


# ═══════════════════════════════════════════════════════════════════════
#  AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════

def _log_approval_action(doc, action, from_state, to_state,
						 comment="", po_amount=0, revision_target="", resubmit_mode=""):
	"""Add an immutable row to the bbf_approval_log child table.

	Uses direct insert to avoid triggering parent doc validation.
	"""
	user_info = _get_user_approval_role(frappe.session.user)

	log = frappe.get_doc({
		"doctype": "BBF Approval Log",
		"parent": doc.name,
		"parenttype": doc.doctype,
		"parentfield": "bbf_approval_log",
		"action": action,
		"from_state": from_state,
		"to_state": to_state,
		"action_by": frappe.session.user,
		"action_by_name": frappe.utils.get_fullname(frappe.session.user),
		"action_by_role": user_info["role"] if user_info else "",
		"action_date": now_datetime(),
		"comment": comment,
		"revision_target": revision_target,
		"resubmit_mode": resubmit_mode,
		"po_amount": po_amount or flt(doc.grand_total if hasattr(doc, "grand_total") else 0),
	})
	log.insert(ignore_permissions=True)


def _log_mr_action(doc, action, from_state, to_state, comment=""):
	"""Add an immutable row to the bbf_mr_log child table."""
	user_info = _get_user_approval_role(frappe.session.user)

	log = frappe.get_doc({
		"doctype": "BBF Approval Log",
		"parent": doc.name,
		"parenttype": "Material Request",
		"parentfield": "bbf_mr_log",
		"action": action,
		"from_state": from_state,
		"to_state": to_state,
		"action_by": frappe.session.user,
		"action_by_name": frappe.utils.get_fullname(frappe.session.user),
		"action_by_role": user_info["role"] if user_info else "",
		"action_date": now_datetime(),
		"comment": comment,
		"po_amount": flt(doc.grand_total) if hasattr(doc, "grand_total") else 0,
	})
	log.insert(ignore_permissions=True)


# ═══════════════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════

def _send_approval_notification(doc, action, recipients, extra=None):
	"""Send bell notification + email for approval actions."""
	if not recipients:
		return

	extra = extra or {}
	is_po = doc.doctype == "Purchase Order"
	doc_label = "PO" if is_po else "MR"
	amount_str = frappe.format_value(flt(doc.grand_total) if hasattr(doc, "grand_total") else 0,
		{"fieldtype": "Currency"})

	# Build subject and message based on action
	subjects = {
		"pending_approval": f"[Action Required] {doc_label} {doc.name} ({amount_str}) awaiting your approval",
		"final_approved": f"[Approved] {doc_label} {doc.name} ({amount_str}) — Final Approval Granted",
		"revised": f"[Revision Required] {doc_label} {doc.name} sent back for revision",
		"rejected": f"[Rejected] {doc_label} {doc.name} ({amount_str}) — Rejected",
		"mr_pending": f"[Action Required] MR {doc.name} awaiting your approval",
		"mr_approved": f"[Approved] MR {doc.name} — Approved",
		"mr_revised": f"[Revision Required] MR {doc.name} sent back for revision",
		"mr_rejected": f"[Rejected] MR {doc.name} — Rejected",
		"sla_breach": f"[SLA Alert] {doc_label} {doc.name} stuck at approval level",
	}
	subject = subjects.get(action, f"{doc_label} {doc.name} — Approval Update")

	# Build message body
	message = _build_notification_message(doc, action, extra)

	# Deduplicate and filter valid recipients
	recipients = list(set(r for r in recipients if r and r != "Administrator"))

	for user in recipients:
		# Bell notification (Notification Log)
		try:
			notification = frappe.new_doc("Notification Log")
			notification.for_user = user
			notification.from_user = frappe.session.user
			notification.document_type = doc.doctype
			notification.document_name = doc.name
			notification.subject = subject
			notification.type = "Alert"
			notification.insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(f"Failed to create notification for {user}")

	# Email to all recipients at once
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message,
			now=True,
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(f"Failed to send approval email for {doc.name}")


def _build_notification_message(doc, action, extra):
	"""Build HTML email message for approval notifications."""
	is_po = doc.doctype == "Purchase Order"
	doc_label = "Purchase Order" if is_po else "Material Request"
	amount_str = frappe.format_value(flt(doc.grand_total) if hasattr(doc, "grand_total") else 0,
		{"fieldtype": "Currency"})

	site_url = frappe.utils.get_url()
	doc_url = f"{site_url}/app/{frappe.scrub(doc.doctype)}/{doc.name}"

	# Items summary (first 3 items)
	items_html = ""
	if is_po:
		items = doc.get("items") or []
		items_summary = []
		for item in items[:3]:
			items_summary.append(f"{item.item_name or item.item_code} (Qty: {item.qty})")
		if len(items) > 3:
			items_summary.append(f"... and {len(items) - 3} more items")
		items_html = "<br>".join(items_summary)

	reason_html = ""
	if extra.get("reason"):
		reason_html = f"<p><strong>Reason:</strong> {frappe.utils.escape_html(extra['reason'])}</p>"

	forwarded_html = ""
	if extra.get("forwarded_by"):
		forwarded_html = f"<p>Forwarded by: <strong>{extra['forwarded_by']}</strong></p>"

	return f"""
	<div style="font-family: Arial, sans-serif; max-width: 600px;">
		<p>A {doc_label} requires your attention:</p>
		<table style="border-collapse: collapse; width: 100%; margin: 10px 0;">
			<tr><td style="padding: 5px; font-weight: bold;">{doc_label}:</td><td style="padding: 5px;">{doc.name}</td></tr>
			{"<tr><td style='padding: 5px; font-weight: bold;'>Supplier:</td><td style='padding: 5px;'>" + (doc.supplier_name or doc.supplier or "") + "</td></tr>" if is_po else ""}
			<tr><td style="padding: 5px; font-weight: bold;">Amount:</td><td style="padding: 5px;">{amount_str}</td></tr>
			{"<tr><td style='padding: 5px; font-weight: bold;'>Items:</td><td style='padding: 5px;'>" + items_html + "</td></tr>" if items_html else ""}
			<tr><td style="padding: 5px; font-weight: bold;">Status:</td><td style="padding: 5px;">{doc.bbf_approval_status if is_po else doc.bbf_mr_status}</td></tr>
		</table>
		{forwarded_html}
		{reason_html}
		<p><a href="{doc_url}" style="background: #2490EF; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px;">View {doc_label}</a></p>
		<hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
		<p style="color: #888; font-size: 12px;">BBF Gate Entry System — Betul Bio Fuel Pvt. Ltd.</p>
	</div>
	"""


def _get_role_users(role):
	"""Get all active users with a specific role."""
	users = frappe.get_all("Has Role",
		filters={"role": role, "parenttype": "User"},
		fields=["parent"])

	active_users = []
	for u in users:
		if u.parent == "Administrator":
			continue
		is_enabled = frappe.db.get_value("User", u.parent, "enabled")
		if is_enabled:
			active_users.append(u.parent)

	return active_users


def _get_chain_users(doc):
	"""Get all users who have acted on this document's approval chain."""
	logs = frappe.get_all("BBF Approval Log",
		filters={"parent": doc.name, "parenttype": doc.doctype},
		fields=["action_by"])
	return set(l.action_by for l in logs if l.action_by)


# ═══════════════════════════════════════════════════════════════════════
#  PO APPROVAL CONTEXT (for JS)
# ═══════════════════════════════════════════════════════════════════════

def _get_po_approval_context(doc, user_info, settings):
	"""Build full approval context for PO form JS."""
	status = doc.bbf_approval_status or ""
	current_level = cint(doc.bbf_current_level)
	required_level = cint(doc.bbf_required_level)
	po_amount = flt(doc.grand_total)
	is_pending = status.startswith("Pending")

	ctx = {
		"approval_enabled": True,
		"status": status,
		"current_level": current_level,
		"required_level": required_level or _get_required_level(po_amount),
		"po_amount": po_amount,
		"can_submit_for_approval": (doc.docstatus == 0 and status in ("", "Draft")),
		"can_approve": False,
		"can_final_approve": False,
		"can_revise": False,
		"can_reject": False,
		"can_resubmit": (status == "Revised"),
		"is_pending": is_pending,
		"user_approval_role": None,
		"user_approval_level": 0,
		"revise_to_options": [],
		"approval_chain": _build_approval_chain(doc, required_level or _get_required_level(po_amount)),
	}

	if user_info and is_pending and user_info["level"] == current_level:
		ctx["user_approval_role"] = user_info["role_label"] or user_info["role"]
		ctx["user_approval_level"] = user_info["level"]
		ctx["can_approve"] = True
		ctx["can_reject"] = True

		user_limit = flt(user_info["limit"])
		if user_info["can_final_approve"]:
			if settings.enable_fast_track:
				ctx["can_final_approve"] = (user_limit == 0 or po_amount <= user_limit)
			else:
				ctx["can_final_approve"] = (user_info["level"] >= ctx["required_level"])

		if user_info["can_revise"]:
			ctx["can_revise"] = True
			ctx["revise_to_options"] = _get_revise_options(user_info)

	return ctx


def _get_mr_approval_context(doc, user_info, settings):
	"""Build approval context for MR form JS."""
	status = doc.bbf_mr_status or ""
	is_dept_head = user_info and user_info["role"] == "Department Head"

	return {
		"approval_enabled": True,
		"status": status,
		"can_submit_for_approval": (doc.docstatus == 0 and status in ("", "Draft")),
		"can_approve": (status == "Pending Department Head" and is_dept_head),
		"can_revise": (status == "Pending Department Head" and is_dept_head),
		"can_reject": (status == "Pending Department Head" and is_dept_head),
		"can_resubmit": (status == "Revised"),
		"is_pending": (status == "Pending Department Head"),
	}


def _build_approval_chain(doc, required_level):
	"""Build the approval chain display for the stepper UI."""
	levels = frappe.get_all("BBF Approval Limit",
		filters={"is_active": 1, "approval_level": ["<=", required_level]},
		fields=["role", "role_label", "approval_level"],
		order_by="approval_level asc")

	# Get log entries for this doc
	logs = frappe.get_all("BBF Approval Log",
		filters={"parent": doc.name, "parenttype": doc.doctype,
				 "action": ["in", ["Forwarded", "Final Approved", "Approved"]]},
		fields=["action_by_name", "action_by_role", "action_date", "action"],
		order_by="action_date asc")

	# Map logs by role
	log_by_role = {}
	for log in logs:
		log_by_role[log.action_by_role] = log

	chain = []
	current_level = cint(doc.bbf_current_level)
	status = doc.bbf_approval_status or ""

	for lvl in levels:
		log = log_by_role.get(lvl.role)
		step = {
			"level": lvl.approval_level,
			"role": lvl.role_label or lvl.role,
			"status": "pending",
			"by": None,
			"date": None,
		}

		if log:
			step["status"] = "approved"
			step["by"] = log.action_by_name
			step["date"] = format_datetime(log.action_date, "dd MMM yyyy HH:mm")
		elif lvl.approval_level == current_level and status.startswith("Pending"):
			step["status"] = "current"
		elif status == "Approved":
			step["status"] = "approved"
		elif status in ("Rejected", "Revised"):
			if lvl.approval_level <= current_level:
				step["status"] = "skipped"

		chain.append(step)

	return chain


def _get_revise_options(user_info):
	"""Get the list of levels this user can revise to."""
	revise_to = user_info.get("revise_to_levels", "")
	if not revise_to:
		# Can revise to any lower level
		lower_levels = frappe.get_all("BBF Approval Limit",
			filters={"is_active": 1, "approval_level": ["<", user_info["level"]]},
			fields=["role", "role_label", "approval_level as level"],
			order_by="approval_level asc")
		return [{"level": l.level, "label": l.role_label or l.role} for l in lower_levels]

	target_levels = [int(x.strip()) for x in revise_to.split(",") if x.strip().isdigit()]
	options = []
	for tl in target_levels:
		info = _get_level_info(tl)
		if info:
			options.append({"level": tl, "label": info["role_label"] or info["role"]})
	return options


# ═══════════════════════════════════════════════════════════════════════
#  DOC EVENT HOOKS (called from hooks.py)
# ═══════════════════════════════════════════════════════════════════════

def po_on_cancel(doc, method):
	"""Reset approval fields when PO is cancelled."""
	if doc.bbf_approval_status:
		doc.db_set({
			"bbf_approval_status": "Rejected",
			"bbf_last_action": f"Cancelled on {now_datetime().strftime('%d %b %Y %H:%M')}",
		}, update_modified=False)


def po_on_amend(doc, method):
	"""Reset approval fields when PO is amended (new draft from cancelled).

	Called via before_insert — only acts if this is an amendment (amended_from is set).
	"""
	if not doc.amended_from:
		return

	doc.bbf_approval_status = "Draft"
	doc.bbf_current_level = 0
	doc.bbf_required_level = 0
	doc.bbf_approved_by = None
	doc.bbf_approved_date = None
	doc.bbf_revision_count = 0
	doc.bbf_last_action = ""
	doc.bbf_submitted_by = None
	doc.bbf_revision_reason = ""
	doc.bbf_revised_by = ""
	doc.bbf_resubmit_mode = ""
	doc.bbf_amount_at_submission = 0
	doc.bbf_last_sla_alert = None


def po_before_save(doc, method):
	"""Prevent amount changes while PO is in approval chain."""
	if not doc.bbf_approval_status:
		return

	status = doc.bbf_approval_status
	if status.startswith("Pending"):
		# Check if amount changed
		old_amount = flt(doc.bbf_amount_at_submission)
		new_amount = flt(doc.grand_total)
		if old_amount and old_amount != new_amount:
			frappe.throw(
				_("Cannot change PO amount while it is in the approval chain. "
				  "The approver must revise the PO first.")
			)


# ═══════════════════════════════════════════════════════════════════════
#  SLA CHECKER (scheduler)
# ═══════════════════════════════════════════════════════════════════════

def check_approval_sla():
	"""Find POs stuck at any approval level beyond SLA hours. Runs via scheduler."""
	settings = frappe.get_single("BBF Settings")
	if not settings.approval_sla_hours:
		return

	sla_hours = cint(settings.approval_sla_hours)
	threshold = add_to_date(now_datetime(), hours=-1 * sla_hours)

	stuck_pos = frappe.get_all("Purchase Order",
		filters={
			"bbf_approval_status": ["like", "Pending%"],
			"modified": ["<", threshold],
			"docstatus": 0,
		},
		fields=["name", "bbf_approval_status", "grand_total", "modified",
				"bbf_current_level", "bbf_last_sla_alert"])

	if not stuck_pos:
		return

	for po_data in stuck_pos:
		# Deduplication: skip if alert sent within last SLA period
		if po_data.bbf_last_sla_alert:
			last_alert_hours = time_diff_in_hours(now_datetime(), po_data.bbf_last_sla_alert)
			if last_alert_hours < sla_hours:
				continue

		hours_stuck = round(time_diff_in_hours(now_datetime(), po_data.modified), 1)

		# Mark last alert time
		frappe.db.set_value("Purchase Order", po_data.name,
			"bbf_last_sla_alert", now_datetime(), update_modified=False)

		# Send alert email
		if settings.approval_sla_email:
			amount_str = frappe.format_value(flt(po_data.grand_total), {"fieldtype": "Currency"})
			frappe.sendmail(
				recipients=[settings.approval_sla_email],
				subject=f"[SLA Alert] PO {po_data.name} stuck at {po_data.bbf_approval_status} for {hours_stuck}h",
				message=f"""
				<p>Purchase Order <strong>{po_data.name}</strong> ({amount_str}) has been stuck
				at <strong>{po_data.bbf_approval_status}</strong> for <strong>{hours_stuck} hours</strong>,
				exceeding the {sla_hours}-hour SLA.</p>
				<p><a href="{frappe.utils.get_url()}/app/purchase-order/{po_data.name}">View PO</a></p>
				""",
				now=True,
			)

		# Auto-escalate if enabled
		if settings.approval_escalation_enabled:
			_auto_escalate(po_data)

	frappe.db.commit()


def _auto_escalate(po_data):
	"""Auto-forward a stuck PO to the next approval level."""
	next_level = _get_next_level(po_data.bbf_current_level)
	if not next_level:
		return

	doc = frappe.get_doc("Purchase Order", po_data.name, for_update=True)
	next_role_display = next_level["role_label"] or next_level["role"]
	current_state = doc.bbf_approval_status
	next_state = f"Pending {next_role_display}"

	_log_approval_action(doc, "Forwarded", current_state, next_state,
		comment=f"Auto-escalated due to SLA breach ({doc.bbf_last_action})",
		po_amount=flt(doc.grand_total))

	doc.db_set({
		"bbf_approval_status": next_state,
		"bbf_current_level": next_level["level"],
		"bbf_last_action": f"Auto-escalated to {next_role_display} (SLA breach) on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	_send_approval_notification(doc, "pending_approval",
		_get_role_users(next_level["role"]),
		extra={"next_role": next_role_display, "escalated": True})
