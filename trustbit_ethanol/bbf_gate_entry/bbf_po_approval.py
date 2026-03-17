import frappe
from frappe import _
from frappe.utils import now_datetime, flt, cint, add_to_date, time_diff_in_hours, format_datetime


ALLOWED_DOCTYPES = ("Purchase Order", "Material Request")


def _validate_doctype(doctype):
	if doctype not in ALLOWED_DOCTYPES:
		frappe.throw(_("Invalid document type for approval: {0}").format(doctype))


# ═══════════════════════════════════════════════════════════════════════
#  PUBLIC API — Whitelisted methods called from JS
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_approval_context(doctype, docname):
	"""Return approval context for the current user on a PO or MR."""
	_validate_doctype(doctype)
	doc = frappe.get_doc(doctype, docname)

	settings = frappe.get_single("BBF Settings")
	if doctype == "Purchase Order" and not settings.enable_po_approval:
		return {"approval_enabled": False}
	if doctype == "Material Request" and not settings.enable_mr_approval:
		return {"approval_enabled": False}

	if doctype == "Material Request":
		return _get_mr_approval_context(doc, settings)

	return _get_po_approval_context(doc, settings)


@frappe.whitelist()
def get_submit_target(doctype, docname=None):
	"""Return the target step info for the submit confirmation dialog."""
	_validate_doctype(doctype)
	if doctype == "Material Request" and docname:
		doc = frappe.get_doc("Material Request", docname)
		cost_center = _get_mr_cost_center(doc)
		if cost_center:
			route = _find_mr_route(cost_center)
			if route:
				route_doc = frappe.get_doc("BBF MR Approval Route", route)
				steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
				if steps:
					target = _get_target_step_with_skip(steps, frappe.session.user)
					return {"target_label": target.role_label or target.role}
		return {"target_label": "Department Head"}

	if doctype == "Purchase Order" and docname:
		doc = frappe.get_doc("Purchase Order", docname)
		category = _resolve_po_category(doc)
		if category:
			rule_name = _find_po_approval_rule(category, flt(doc.grand_total))
			if rule_name:
				rule = frappe.get_doc("BBF PO Approval Rule", rule_name)
				steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
				if steps:
					target = _get_target_step_with_skip(steps, frappe.session.user)
					return {"target_label": target.role_label or target.role}
		return {"target_label": "CEO"}

	return {"target_label": "Approver"}


@frappe.whitelist()
def submit_for_approval(doctype, docname):
	"""Submit a Draft PO/MR into the approval chain."""
	_validate_doctype(doctype)
	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Purchase Order":
		return _submit_po_for_approval(doc)
	elif doctype == "Material Request":
		next_state = _submit_mr_for_approval(doc)
		return {"status": "ok", "next_state": next_state}


@frappe.whitelist()
def approve_document(doctype, docname, comment=""):
	"""Approve/Review action — handles Review, Approve, Final Approve based on step config."""
	_validate_doctype(doctype)
	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _approve_mr(doc, comment)

	return _approve_po(doc, comment)


@frappe.whitelist()
def send_to_md(docname, comment=""):
	"""CEO manually triggers MD approval step. Only available when next step is manual-trigger."""
	doc = frappe.get_doc("Purchase Order", docname, for_update=True)
	_validate_po_is_pending(doc)
	_validate_not_self_approving(doc)
	_validate_amount_unchanged(doc)

	rule = frappe.get_doc("BBF PO Approval Rule", doc.bbf_approval_rule)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.bbf_current_step)

	# Find current and next step
	current_step = next((s for s in steps if s.step_order == current_step_order), None)
	next_step = _get_next_step(steps, current_step_order)

	if not current_step or not next_step or not next_step.is_manual_trigger:
		frappe.throw(_("Cannot send to MD — no manual trigger step configured for this PO"))

	# Validate user has the current step's role
	user_roles = frappe.get_roles(frappe.session.user)
	if current_step.role not in user_roles:
		frappe.throw(_("Only the current approver ({0}) can send to MD").format(
			current_step.role_label or current_step.role))

	current_state = doc.bbf_approval_status
	next_label = next_step.role_label or next_step.role
	next_state = f"Pending {next_label}"
	role_display = current_step.role_label or current_step.role

	_log_approval_action(doc, "Sent to MD", current_state, next_state,
		comment=comment, po_amount=flt(doc.grand_total),
		step_order=next_step.step_order, purchase_category=doc.bbf_purchase_category)

	doc.db_set({
		"bbf_approval_status": next_state,
		"bbf_current_step": next_step.step_order,
		"bbf_can_send_to_md": 0,
		"bbf_last_action": f"Sent to {next_label} by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	_send_approval_notification(doc, "pending_approval",
		_get_role_users(next_step.role),
		extra={"forwarded_by": role_display, "next_role": next_label})

	return {"status": "sent_to_md", "next_state": next_state}


@frappe.whitelist()
def revise_document(doctype, docname, reason, revise_to_level=None, comment=""):
	"""Send a PO/MR back for revision."""
	_validate_doctype(doctype)
	if not reason:
		frappe.throw(_("Revision reason is mandatory"))

	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _revise_mr(doc, reason, comment)

	_validate_po_is_pending(doc)
	_validate_user_can_act_on_po(doc)

	current_state = doc.bbf_approval_status
	current_step = cint(doc.bbf_current_step)
	role_display = _get_user_step_role(doc, current_step)

	_log_approval_action(doc, "Revised", current_state, "Revised",
		comment=comment or reason, po_amount=flt(doc.grand_total),
		step_order=current_step, purchase_category=doc.bbf_purchase_category)

	doc.db_set({
		"bbf_approval_status": "Revised",
		"bbf_revision_count": cint(doc.bbf_revision_count) + 1,
		"bbf_revision_reason": reason,
		"bbf_revised_by": f"{frappe.session.user} ({role_display})",
		"bbf_can_send_to_md": 0,
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
	_validate_doctype(doctype)
	if not reason:
		frappe.throw(_("Rejection reason is mandatory"))

	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _reject_mr(doc, reason, comment)

	_validate_po_is_pending(doc)
	_validate_user_can_act_on_po(doc)

	current_state = doc.bbf_approval_status
	current_step = cint(doc.bbf_current_step)
	role_display = _get_user_step_role(doc, current_step)

	_log_approval_action(doc, "Rejected", current_state, "Rejected",
		comment=comment or reason, po_amount=flt(doc.grand_total),
		step_order=current_step, purchase_category=doc.bbf_purchase_category)

	doc.db_set({
		"bbf_approval_status": "Rejected",
		"bbf_can_send_to_md": 0,
		"bbf_last_action": f"Rejected by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	# Rejected PO stays as Draft with "Rejected" status — fields are locked in JS
	# (cancel() is invalid on Draft docs; docstatus 0 → 2 is not allowed by Frappe)

	# Notify all stakeholders
	all_users = _get_chain_users(doc)
	if doc.bbf_submitted_by:
		all_users.add(doc.bbf_submitted_by)

	_send_approval_notification(doc, "rejected", list(all_users),
		extra={"rejected_by": role_display, "reason": reason})
	_send_post_approval_notification(doc, "rejection")

	return {"status": "rejected", "message": f"PO {doc.name} has been rejected"}


@frappe.whitelist()
def resubmit_document(doctype, docname, mode="restart"):
	"""Resubmit a revised PO/MR back into the approval chain."""
	_validate_doctype(doctype)
	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _resubmit_mr(doc)

	if doc.bbf_approval_status != "Revised":
		frappe.throw(_("Only revised POs can be resubmitted"))

	_validate_resubmit_permission(doc)

	# Re-resolve category and rule (amount may have changed)
	category = _resolve_po_category(doc)
	rule_name = _find_po_approval_rule(category, flt(doc.grand_total))
	rule = frappe.get_doc("BBF PO Approval Rule", rule_name)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)

	# Always restart from step 1 (with self-skip)
	target = _get_target_step_with_skip(steps, frappe.session.user)
	next_state = f"Pending {target.role_label or target.role}"

	_log_approval_action(doc, "Resubmitted", "Revised", next_state,
		po_amount=flt(doc.grand_total), resubmit_mode=mode,
		step_order=target.step_order, purchase_category=category)

	doc.db_set({
		"bbf_approval_status": next_state,
		"bbf_purchase_category": category,
		"bbf_approval_rule": rule_name,
		"bbf_current_step": target.step_order,
		"bbf_total_steps": len(steps),
		"bbf_can_send_to_md": 0,
		"bbf_amount_at_submission": flt(doc.grand_total),
		"bbf_resubmit_mode": f"Restarted from {target.role_label or target.role}",
		"bbf_last_action": f"Resubmitted on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	_send_approval_notification(doc, "pending_approval",
		_get_role_users(target.role),
		extra={"next_role": target.role_label or target.role, "resubmitted": True})

	return {"status": "resubmitted", "next_state": next_state}


# ═══════════════════════════════════════════════════════════════════════
#  PO-SPECIFIC HANDLERS
# ═══════════════════════════════════════════════════════════════════════

def _submit_po_for_approval(doc):
	"""Submit a PO for approval using category-based routing."""
	_validate_po_submittable(doc)

	status = doc.bbf_approval_status
	if status and status not in ("", "Draft"):
		frappe.throw(_("This PO is already in the approval chain (status: {0})").format(status))

	# Resolve category from PO items → Item Group → BBF Purchase Category
	category = _resolve_po_category(doc)
	rule_name = _find_po_approval_rule(category, flt(doc.grand_total))
	rule = frappe.get_doc("BBF PO Approval Rule", rule_name)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)

	# Self-skip: if creator has step 1's role, start at step 2
	target = _get_target_step_with_skip(steps, frappe.session.user)
	next_state = f"Pending {target.role_label or target.role}"

	_log_approval_action(doc, "Submitted", "Draft", next_state,
		po_amount=flt(doc.grand_total),
		step_order=target.step_order, purchase_category=category)

	# Track if self-skip was impossible (single-step rule where submitter has the role)
	# In this case, self-approval must be allowed or the PO would be stuck forever
	self_skip_impossible = (target == steps[0] and target.role in frappe.get_roles(frappe.session.user) and len(steps) == 1)

	doc.db_set({
		"bbf_approval_status": next_state,
		"bbf_purchase_category": category,
		"bbf_approval_rule": rule_name,
		"bbf_current_step": target.step_order,
		"bbf_total_steps": len(steps),
		"bbf_can_send_to_md": 0,
		"bbf_submitted_by": frappe.session.user,
		"bbf_self_skip_impossible": 1 if self_skip_impossible else 0,
		"bbf_amount_at_submission": flt(doc.grand_total),
		"bbf_last_action": f"Submitted for approval on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	_send_approval_notification(doc, "pending_approval",
		_get_role_users(target.role),
		extra={"next_role": target.role_label or target.role})

	return {"status": "ok", "next_state": next_state}


def _approve_po(doc, comment=""):
	"""Approve/Review/Forward a PO based on the current step's action_type."""
	_validate_po_is_pending(doc)
	_validate_not_self_approving(doc)
	_validate_amount_unchanged(doc)

	if not doc.bbf_approval_rule:
		frappe.throw(_("This PO has no approval rule set. Please resubmit."))

	rule = frappe.get_doc("BBF PO Approval Rule", doc.bbf_approval_rule)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.bbf_current_step)

	current_step = next((s for s in steps if s.step_order == current_step_order), None)
	if not current_step:
		frappe.throw(_("Current approval step not found"))

	# Validate user has the current step's role (or higher step role for override)
	user_roles = frappe.get_roles(frappe.session.user)
	can_act = False
	for step in steps:
		if step.step_order >= current_step_order and step.role in user_roles:
			can_act = True
			break
	if not can_act:
		frappe.throw(_("You don't have permission to act on this PO at this step"))

	po_amount = flt(doc.grand_total)
	current_state = doc.bbf_approval_status
	# Use the acting user's actual approval role (not the step's role) for display
	role_display = _get_user_step_role(doc, current_step_order)

	if current_step.action_type == "Final Approve":
		# FINAL APPROVAL — submit the PO
		_log_approval_action(doc, "Final Approved", current_state, "Approved",
			comment=comment, po_amount=po_amount,
			step_order=current_step_order, purchase_category=doc.bbf_purchase_category)

		doc.db_set({
			"bbf_approval_status": "Approved",
			"bbf_approved_by": frappe.session.user,
			"bbf_approved_date": now_datetime(),
			"bbf_can_send_to_md": 0,
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
		_send_post_approval_notification(doc, "approval")

		return {"status": "approved", "message": f"PO {doc.name} has been final approved and submitted"}

	else:
		# REVIEW or APPROVE — forward to next step
		next_step = _get_next_step(steps, current_step_order)

		if next_step and next_step.is_manual_trigger:
			# Next step is MD (manual trigger) — mark current step as done, show "Send to MD" button
			action_label = "Reviewed" if current_step.action_type == "Review" else "Approved"
			_log_approval_action(doc, action_label, current_state,
				f"Awaiting Send to {next_step.role_label or next_step.role}",
				comment=comment, po_amount=po_amount,
				step_order=current_step_order, purchase_category=doc.bbf_purchase_category)

			doc.db_set({
				"bbf_approval_status": f"Awaiting Send to {next_step.role_label or next_step.role}",
				"bbf_can_send_to_md": 1,
				"bbf_last_action": f"{action_label} by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')} — awaiting manual send to {next_step.role_label or next_step.role}",
			}, update_modified=True)

			return {"status": "awaiting_md", "message": f"PO reviewed. CEO must now send to {next_step.role_label or next_step.role}."}

		elif next_step:
			# Auto-forward to next step
			next_label = next_step.role_label or next_step.role
			next_state = f"Pending {next_label}"
			action_label = "Reviewed" if current_step.action_type == "Review" else "Forwarded"

			_log_approval_action(doc, action_label, current_state, next_state,
				comment=comment, po_amount=po_amount,
				step_order=current_step_order, purchase_category=doc.bbf_purchase_category)

			doc.db_set({
				"bbf_approval_status": next_state,
				"bbf_current_step": next_step.step_order,
				"bbf_can_send_to_md": 0,
				"bbf_last_action": f"{action_label} by {role_display} to {next_label} on {now_datetime().strftime('%d %b %Y %H:%M')}",
			}, update_modified=True)

			_send_approval_notification(doc, "pending_approval",
				_get_role_users(next_step.role),
				extra={"forwarded_by": role_display, "next_role": next_label})

			return {"status": "forwarded", "next_state": next_state}

		else:
			frappe.throw(_("No next approval step configured. Cannot forward."))


# ═══════════════════════════════════════════════════════════════════════
#  MR-SPECIFIC HANDLERS (cost-center-based routing)
# ═══════════════════════════════════════════════════════════════════════

def _submit_mr_for_approval(doc):
	"""Submit MR for approval — routes by Cost Center."""
	if doc.docstatus != 0:
		frappe.throw(_("Only Draft Material Requests can be submitted for approval"))

	if not (doc.get("items") or []):
		frappe.throw(_("MR must have at least one item to submit for approval"))

	status = doc.bbf_mr_status
	if status and status not in ("", "Draft"):
		frappe.throw(_("This MR is already in the approval chain (status: {0})").format(status))

	cost_center = _get_mr_cost_center(doc)
	if not cost_center:
		frappe.throw(_("Cost Center is required for MR approval routing. Please set it on the MR or its items."))

	route_name = _find_mr_route(cost_center)
	if not route_name:
		frappe.throw(_("No MR approval route configured for Cost Center: {0}").format(cost_center))

	route_doc = frappe.get_doc("BBF MR Approval Route", route_name)
	steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)

	# Self-skip
	target = _get_target_step_with_skip(steps, frappe.session.user)
	next_state = f"Pending {target.role_label or target.role}"

	_log_mr_action(doc, "Submitted", "Draft", next_state,
		step_order=target.step_order, purchase_category=route_name)

	doc.db_set({
		"bbf_mr_status": next_state,
		"bbf_mr_route": route_name,
		"bbf_mr_approval_route": route_name,
		"bbf_mr_current_step": target.step_order,
		"bbf_mr_total_steps": len(steps),
		"bbf_mr_submitted_by": frappe.session.user,
	}, update_modified=True)

	_send_approval_notification(doc, "mr_pending",
		_get_role_users(target.role),
		extra={"next_role": target.role_label or target.role})

	return next_state


def _approve_mr(doc, comment=""):
	"""Approve/Review MR based on current step."""
	if not (doc.bbf_mr_status or "").startswith("Pending"):
		frappe.throw(_("This MR is not pending approval (status: {0})").format(doc.bbf_mr_status))

	# Prevent self-approval — check both creator and submitter
	mr_submitted_by = doc.bbf_mr_submitted_by if hasattr(doc, "bbf_mr_submitted_by") else None
	if doc.owner == frappe.session.user:
		frappe.throw(_("You cannot approve a Material Request that you created."))
	if mr_submitted_by and mr_submitted_by == frappe.session.user:
		frappe.throw(_("You cannot approve a Material Request that you submitted for approval."))

	route_name = doc.bbf_mr_approval_route
	if not route_name:
		frappe.throw(_("This MR has no approval route set. Please resubmit."))

	route_doc = frappe.get_doc("BBF MR Approval Route", route_name)
	steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.bbf_mr_current_step)

	current_step = next((s for s in steps if s.step_order == current_step_order), None)
	if not current_step:
		frappe.throw(_("Current MR approval step not found"))

	# Validate user has the role
	user_roles = frappe.get_roles(frappe.session.user)
	can_act = False
	for step in steps:
		if step.step_order >= current_step_order and step.role in user_roles:
			can_act = True
			break
	if not can_act:
		frappe.throw(_("You don't have permission to act on this MR at this step"))

	role_display = current_step.role_label or current_step.role
	current_state = doc.bbf_mr_status

	if current_step.action_type == "Final Approve":
		# FINAL APPROVAL
		_log_mr_action(doc, "Final Approved", current_state, "Approved",
			comment=comment, step_order=current_step_order, purchase_category=route_name)

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
			extra={"approved_by": role_display})
		_send_post_approval_notification(doc, "mr_approval")

		return {"status": "approved", "message": f"MR {doc.name} has been approved and submitted"}

	else:
		# REVIEW — forward to next step
		next_step = _get_next_step(steps, current_step_order)
		if not next_step:
			frappe.throw(_("No next MR approval step configured. Cannot forward."))

		next_label = next_step.role_label or next_step.role
		next_state = f"Pending {next_label}"

		_log_mr_action(doc, "Reviewed", current_state, next_state,
			comment=comment, step_order=current_step_order, purchase_category=route_name)

		doc.db_set({
			"bbf_mr_status": next_state,
			"bbf_mr_current_step": next_step.step_order,
		}, update_modified=True)

		_send_approval_notification(doc, "mr_pending",
			_get_role_users(next_step.role),
			extra={"forwarded_by": role_display, "next_role": next_label})

		return {"status": "forwarded", "next_state": next_state}


def _revise_mr(doc, reason, comment=""):
	"""Revise MR — send back to creator."""
	if not (doc.bbf_mr_status or "").startswith("Pending"):
		frappe.throw(_("This MR is not pending approval (status: {0})").format(doc.bbf_mr_status))

	_validate_user_can_act_on_mr(doc)

	current_step = cint(doc.bbf_mr_current_step)
	role_display = _get_user_mr_step_role(doc, current_step)
	current_state = doc.bbf_mr_status

	_log_mr_action(doc, "Revised", current_state, "Revised",
		comment=comment or reason, step_order=current_step,
		purchase_category=doc.bbf_mr_approval_route)

	doc.db_set({
		"bbf_mr_status": "Revised",
		"bbf_mr_revision_reason": reason,
	}, update_modified=True)

	_send_approval_notification(doc, "mr_revised", [doc.owner],
		extra={"revised_by": role_display, "reason": reason})

	return {"status": "revised"}


def _reject_mr(doc, reason, comment=""):
	"""Reject MR."""
	if not (doc.bbf_mr_status or "").startswith("Pending"):
		frappe.throw(_("This MR is not pending approval (status: {0})").format(doc.bbf_mr_status))

	_validate_user_can_act_on_mr(doc)

	current_step = cint(doc.bbf_mr_current_step)
	role_display = _get_user_mr_step_role(doc, current_step)
	current_state = doc.bbf_mr_status

	_log_mr_action(doc, "Rejected", current_state, "Rejected",
		comment=comment or reason, step_order=current_step,
		purchase_category=doc.bbf_mr_approval_route)

	doc.db_set({
		"bbf_mr_status": "Rejected",
	}, update_modified=True)

	# Rejected MR stays as Draft with "Rejected" status — fields are locked in JS
	# (cancel() is invalid on Draft docs; docstatus 0 → 2 is not allowed by Frappe)

	_send_approval_notification(doc, "mr_rejected", [doc.owner],
		extra={"rejected_by": role_display, "reason": reason})
	_send_post_approval_notification(doc, "mr_rejection")

	return {"status": "rejected"}


def _resubmit_mr(doc):
	"""Resubmit a revised MR."""
	if doc.bbf_mr_status != "Revised":
		frappe.throw(_("Only revised MRs can be resubmitted"))

	mr_submitted_by = doc.bbf_mr_submitted_by if hasattr(doc, "bbf_mr_submitted_by") else None
	allowed_users = {doc.owner, "Administrator"}
	if mr_submitted_by:
		allowed_users.add(mr_submitted_by)
	if frappe.session.user not in allowed_users:
		frappe.throw(_("Only the MR creator or original submitter can resubmit after revision"))

	cost_center = _get_mr_cost_center(doc)
	route_name = _find_mr_route(cost_center)
	if not route_name:
		frappe.throw(_("No MR approval route configured for Cost Center: {0}").format(cost_center))

	route_doc = frappe.get_doc("BBF MR Approval Route", route_name)
	steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)

	target = _get_target_step_with_skip(steps, frappe.session.user)
	next_state = f"Pending {target.role_label or target.role}"

	_log_mr_action(doc, "Resubmitted", "Revised", next_state,
		step_order=target.step_order, purchase_category=route_name)

	doc.db_set({
		"bbf_mr_status": next_state,
		"bbf_mr_route": route_name,
		"bbf_mr_approval_route": route_name,
		"bbf_mr_current_step": target.step_order,
		"bbf_mr_total_steps": len(steps),
		"bbf_mr_submitted_by": frappe.session.user,
		"bbf_mr_revision_reason": "",
	}, update_modified=True)

	_send_approval_notification(doc, "mr_pending",
		_get_role_users(target.role),
		extra={"next_role": target.role_label or target.role, "resubmitted": True})

	return {"status": "resubmitted"}


# ═══════════════════════════════════════════════════════════════════════
#  CATEGORY & RULE ROUTING (PO)
# ═══════════════════════════════════════════════════════════════════════

def _resolve_po_category(doc):
	"""Auto-detect BBF Purchase Category from PO items → Item Group mapping."""
	item_codes = [item.item_code for item in (doc.get("items") or []) if item.item_code]
	item_groups = set()
	if item_codes:
		results = frappe.get_all("Item",
			filters={"name": ["in", item_codes]},
			fields=["item_group"],
			pluck="item_group")
		item_groups = set(results)

	if not item_groups:
		settings = frappe.get_single("BBF Settings")
		if settings.default_po_category:
			return settings.default_po_category
		frappe.throw(_("Cannot determine purchase category — no items with Item Groups on this PO"))

	# Map each Item Group to its BBF Purchase Category
	categories = set()
	for ig in item_groups:
		result = frappe.db.sql("""
			SELECT parent FROM `tabBBF Purchase Category Item`
			WHERE item_group = %s
		""", ig, as_dict=True)
		if result:
			categories.add(result[0].parent)

	if not categories:
		settings = frappe.get_single("BBF Settings")
		if settings.default_po_category:
			return settings.default_po_category
		frappe.throw(_(
			"No purchase category mapped for Item Group(s): {0}. "
			"Please configure BBF Purchase Category or set a default in BBF Settings."
		).format(", ".join(item_groups)))

	if len(categories) == 1:
		return categories.pop()

	# Mixed categories — pick the one with the strictest (most steps) rule
	return _pick_strictest_category(categories, flt(doc.grand_total))


def _pick_strictest_category(categories, amount):
	"""Pick the category requiring the most approval steps for this amount."""
	max_steps = 0
	strictest = None

	for cat in categories:
		rule_name = _find_po_approval_rule(cat, amount)
		if rule_name:
			step_count = frappe.db.count("BBF PO Approval Step",
				filters={"parent": rule_name})
			if step_count > max_steps:
				max_steps = step_count
				strictest = cat

	return strictest or list(categories)[0]


def _find_po_approval_rule(category, amount):
	"""Find the matching BBF PO Approval Rule for this category + amount."""
	rules = frappe.get_all("BBF PO Approval Rule",
		filters={"purchase_category": category, "is_active": 1},
		fields=["name", "min_amount", "max_amount", "priority"],
		order_by="priority asc")

	for rule in rules:
		min_amt = flt(rule.min_amount)
		max_amt = flt(rule.max_amount)
		if amount >= min_amt and (max_amt == 0 or amount <= max_amt):
			return rule.name

	frappe.throw(_(
		"No approval rule configured for category '{0}' at amount {1}. "
		"Please configure BBF PO Approval Rule."
	).format(category, frappe.format_value(amount, {"fieldtype": "Currency"})))


# ═══════════════════════════════════════════════════════════════════════
#  COST CENTER ROUTING (MR)
# ═══════════════════════════════════════════════════════════════════════

def _get_mr_cost_center(doc):
	"""Get Cost Center from MR — header first, then first item."""
	if hasattr(doc, "cost_center") and doc.cost_center:
		return doc.cost_center

	for item in (doc.get("items") or []):
		if item.cost_center:
			return item.cost_center

	return None


def _find_mr_route(cost_center):
	"""Find the BBF MR Approval Route for this Cost Center."""
	result = frappe.db.sql("""
		SELECT r.name
		FROM `tabBBF MR Approval Route` r
		INNER JOIN `tabBBF MR Route Cost Center` cc ON cc.parent = r.name
		WHERE cc.cost_center = %s AND r.is_active = 1
		LIMIT 1
	""", cost_center, as_dict=True)

	return result[0].name if result else None


# ═══════════════════════════════════════════════════════════════════════
#  STEP HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _get_target_step_with_skip(steps, user):
	"""Get the first step, skipping if the user has step 1's role (self-skip)."""
	if not steps:
		frappe.throw(_("No approval steps configured"))

	first_step = steps[0]
	user_roles = frappe.get_roles(user)

	if first_step.role in user_roles and len(steps) > 1:
		return steps[1]

	return first_step


def _get_next_step(steps, current_step_order):
	"""Get the next step after current_step_order."""
	for step in sorted(steps, key=lambda s: s.step_order):
		if step.step_order > current_step_order:
			return step
	return None


def _get_user_step_role(doc, current_step_order):
	"""Get the display label for the current user's role at the given step."""
	if not doc.bbf_approval_rule:
		return frappe.utils.get_fullname(frappe.session.user)

	rule = frappe.get_doc("BBF PO Approval Rule", doc.bbf_approval_rule)
	user_roles = frappe.get_roles(frappe.session.user)
	for step in sorted(rule.approval_steps, key=lambda s: s.step_order):
		if step.step_order >= current_step_order and step.role in user_roles:
			return step.role_label or step.role
	return frappe.utils.get_fullname(frappe.session.user)


def _get_user_mr_step_role(doc, current_step_order):
	"""Get the display label for the current user's role at the MR step."""
	if not doc.bbf_mr_approval_route:
		return frappe.utils.get_fullname(frappe.session.user)

	route = frappe.get_doc("BBF MR Approval Route", doc.bbf_mr_approval_route)
	user_roles = frappe.get_roles(frappe.session.user)
	for step in sorted(route.approval_steps, key=lambda s: s.step_order):
		if step.step_order >= current_step_order and step.role in user_roles:
			return step.role_label or step.role
	return frappe.utils.get_fullname(frappe.session.user)


# ═══════════════════════════════════════════════════════════════════════
#  VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def _validate_po_is_pending(doc):
	"""Ensure PO is in a Pending or Awaiting state."""
	status = doc.bbf_approval_status or ""
	if not (status.startswith("Pending") or status.startswith("Awaiting")):
		frappe.throw(
			_("This PO is not pending approval (current status: {0}). "
			  "Only POs with 'Pending' or 'Awaiting' status can be acted upon.").format(status or "Draft")
		)


def _validate_not_self_approving(doc):
	"""Prevent the same user who submitted from approving their own PO.
	Exception: when self-skip was impossible (single-step rule where submitter has the only role).
	"""
	if doc.bbf_submitted_by and doc.bbf_submitted_by == frappe.session.user:
		# Allow self-approval if self-skip was impossible (would otherwise create stuck state)
		if cint(doc.bbf_self_skip_impossible):
			return
		frappe.throw(
			_("You cannot approve a PO that you submitted for approval. "
			  "A different approver must act on this PO.")
		)


def _validate_user_can_act_on_po(doc):
	"""Validate user has a role that can act on the PO at its current step."""
	if not doc.bbf_approval_rule:
		return

	rule = frappe.get_doc("BBF PO Approval Rule", doc.bbf_approval_rule)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.bbf_current_step)
	user_roles = frappe.get_roles(frappe.session.user)

	for step in steps:
		if step.step_order >= current_step_order and step.role in user_roles:
			return

	frappe.throw(_("You don't have permission to act on this PO at this step"))


def _validate_user_can_act_on_mr(doc):
	"""Validate user has a role that can act on the MR at its current step."""
	if not doc.bbf_mr_approval_route:
		return

	route = frappe.get_doc("BBF MR Approval Route", doc.bbf_mr_approval_route)
	steps = sorted(route.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.bbf_mr_current_step)
	user_roles = frappe.get_roles(frappe.session.user)

	for step in steps:
		if step.step_order >= current_step_order and step.role in user_roles:
			return

	frappe.throw(_("You don't have permission to act on this MR at this step"))


def _validate_resubmit_permission(doc):
	"""Only the original submitter or PO owner can resubmit."""
	allowed_users = {doc.owner}
	if doc.bbf_submitted_by:
		allowed_users.add(doc.bbf_submitted_by)
	allowed_users.add("Administrator")

	if frappe.session.user not in allowed_users:
		frappe.throw(
			_("Only the PO creator or original submitter can resubmit after revision.")
		)


def _validate_po_submittable(doc):
	"""Validate PO can be submitted for approval."""
	if doc.docstatus != 0:
		frappe.throw(_("Only Draft POs can be submitted for approval"))

	if flt(doc.grand_total) <= 0:
		frappe.throw(_("PO must have a positive amount to submit for approval"))

	items = doc.get("items") or []
	if not items:
		frappe.throw(_("PO must have at least one item to submit for approval"))


def _validate_amount_unchanged(doc):
	"""Server-side check that PO amount hasn't been tampered with."""
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
						 comment="", po_amount=0, revision_target="",
						 resubmit_mode="", step_order=0, purchase_category=""):
	"""Add an immutable row to the bbf_approval_log child table."""
	user_roles = frappe.get_roles(frappe.session.user)
	user_role = ""
	if doc.bbf_approval_rule:
		try:
			rule = frappe.get_doc("BBF PO Approval Rule", doc.bbf_approval_rule)
			# Match role at or above the current step_order to get the correct role
			effective_step = cint(step_order) or cint(doc.bbf_current_step)
			for step in sorted(rule.approval_steps, key=lambda s: s.step_order):
				if step.step_order >= effective_step and step.role in user_roles:
					user_role = step.role_label or step.role
					break
		except Exception:
			pass

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
		"action_by_role": user_role,
		"action_date": now_datetime(),
		"comment": comment,
		"revision_target": revision_target,
		"resubmit_mode": resubmit_mode,
		"po_amount": po_amount or flt(doc.grand_total if hasattr(doc, "grand_total") else 0),
		"step_order": step_order,
		"purchase_category": purchase_category,
	})
	log.insert(ignore_permissions=True)


def _log_mr_action(doc, action, from_state, to_state, comment="",
				   step_order=0, purchase_category=""):
	"""Add an immutable row to the bbf_mr_log child table."""
	user_roles = frappe.get_roles(frappe.session.user)
	user_role = ""
	if doc.bbf_mr_approval_route:
		try:
			route = frappe.get_doc("BBF MR Approval Route", doc.bbf_mr_approval_route)
			# Match role at or above the current step_order to get the correct role
			effective_step = cint(step_order) or cint(doc.bbf_mr_current_step if hasattr(doc, "bbf_mr_current_step") else 0)
			for step in sorted(route.approval_steps, key=lambda s: s.step_order):
				if step.step_order >= effective_step and step.role in user_roles:
					user_role = step.role_label or step.role
					break
		except Exception:
			pass

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
		"action_by_role": user_role,
		"action_date": now_datetime(),
		"comment": comment,
		"po_amount": flt(doc.grand_total) if hasattr(doc, "grand_total") else 0,
		"step_order": step_order,
		"purchase_category": purchase_category,
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

	subjects = {
		"pending_approval": f"[Action Required] {doc_label} {doc.name} ({amount_str}) awaiting your approval",
		"final_approved": f"[Approved] {doc_label} {doc.name} ({amount_str}) — Final Approval Granted",
		"revised": f"[Revision Required] {doc_label} {doc.name} sent back for revision",
		"rejected": f"[Rejected] {doc_label} {doc.name} ({amount_str}) — Rejected",
		"mr_pending": f"[Action Required] MR {doc.name} awaiting your approval",
		"mr_approved": f"[Approved] MR {doc.name} — Approved",
		"mr_revised": f"[Revision Required] MR {doc.name} sent back for revision",
		"mr_rejected": f"[Rejected] MR {doc.name} — Rejected",
		"sla_breach": f"[SLA Alert] {doc_label} {doc.name} stuck at approval step",
	}
	subject = subjects.get(action, f"{doc_label} {doc.name} — Approval Update")

	message = _build_notification_message(doc, action, extra)
	recipients = list(set(r for r in recipients if r and r != "Administrator"))

	for user in recipients:
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
			frappe.log_error(
				title=f"Approval Notification Error ({doc.name} → {user})",
				message=frappe.get_traceback()
			)

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
		frappe.log_error(
			title=f"Approval Email Error ({doc.name})",
			message=frappe.get_traceback()
		)


def _send_post_approval_notification(doc, action_type):
	"""Send notifications to configured post-approval recipients in BBF Settings."""
	try:
		settings = frappe.get_single("BBF Settings")
		if not settings.post_approval_notify:
			return

		is_po = doc.doctype == "Purchase Order"
		recipients = []
		for row in settings.post_approval_notify:
			if action_type == "approval" and is_po and row.notify_on_po_approval:
				recipients.append(row.user)
			elif action_type == "mr_approval" and not is_po and row.notify_on_mr_approval:
				recipients.append(row.user)
			elif action_type in ("rejection", "mr_rejection") and row.notify_on_rejection:
				recipients.append(row.user)

		if recipients:
			_send_approval_notification(doc, "final_approved" if "approval" in action_type else "rejected",
				recipients)
	except Exception:
		frappe.log_error(
			title=f"Post-Approval Notification Error ({doc.name})",
			message=frappe.get_traceback()
		)


def _build_notification_message(doc, action, extra):
	"""Build HTML email message for approval notifications."""
	is_po = doc.doctype == "Purchase Order"
	doc_label = "Purchase Order" if is_po else "Material Request"
	amount_str = frappe.format_value(flt(doc.grand_total) if hasattr(doc, "grand_total") else 0,
		{"fieldtype": "Currency"})

	site_url = frappe.utils.get_url()
	doc_url = f"{site_url}/app/{frappe.scrub(doc.doctype)}/{doc.name}"

	items_html = ""
	if is_po:
		items = doc.get("items") or []
		items_summary = []
		for item in items[:3]:
			items_summary.append(f"{item.item_name or item.item_code} (Qty: {item.qty})")
		if len(items) > 3:
			items_summary.append(f"... and {len(items) - 3} more items")
		items_html = "<br>".join(items_summary)

	# Category/route info
	category_html = ""
	if is_po and doc.bbf_purchase_category:
		category_html = f"<tr><td style='padding: 5px; font-weight: bold;'>Category:</td><td style='padding: 5px;'>{frappe.utils.escape_html(doc.bbf_purchase_category)}</td></tr>"
	elif not is_po and hasattr(doc, "bbf_mr_route") and doc.bbf_mr_route:
		category_html = f"<tr><td style='padding: 5px; font-weight: bold;'>Route:</td><td style='padding: 5px;'>{frappe.utils.escape_html(doc.bbf_mr_route)}</td></tr>"

	reason_html = ""
	if extra.get("reason"):
		reason_html = f"<p><strong>Reason:</strong> {frappe.utils.escape_html(extra['reason'])}</p>"

	forwarded_html = ""
	if extra.get("forwarded_by"):
		forwarded_html = f"<p>Forwarded by: <strong>{frappe.utils.escape_html(extra['forwarded_by'])}</strong></p>"

	status_value = doc.bbf_approval_status if is_po else (doc.bbf_mr_status if hasattr(doc, "bbf_mr_status") else "")

	return f"""
	<div style="font-family: Arial, sans-serif; max-width: 600px;">
		<p>A {doc_label} requires your attention:</p>
		<table style="border-collapse: collapse; width: 100%; margin: 10px 0;">
			<tr><td style="padding: 5px; font-weight: bold;">{doc_label}:</td><td style="padding: 5px;">{doc.name}</td></tr>
			{"<tr><td style='padding: 5px; font-weight: bold;'>Supplier:</td><td style='padding: 5px;'>" + frappe.utils.escape_html(doc.supplier_name or doc.supplier or "") + "</td></tr>" if is_po else ""}
			<tr><td style="padding: 5px; font-weight: bold;">Amount:</td><td style="padding: 5px;">{amount_str}</td></tr>
			{category_html}
			{"<tr><td style='padding: 5px; font-weight: bold;'>Items:</td><td style='padding: 5px;'>" + items_html + "</td></tr>" if items_html else ""}
			<tr><td style="padding: 5px; font-weight: bold;">Status:</td><td style="padding: 5px;">{frappe.utils.escape_html(status_value)}</td></tr>
		</table>
		{forwarded_html}
		{reason_html}
		<p><a href="{doc_url}" style="background: #2490EF; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px;">View {doc_label}</a></p>
		<hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
		<p style="color: #888; font-size: 12px;">BBF Gate Entry System — Betul Bio Fuel Pvt. Ltd.</p>
	</div>
	"""


def _get_role_users(role):
	"""Get all active users with a specific role (batch query)."""
	return frappe.db.sql_list("""
		SELECT DISTINCT hr.parent
		FROM `tabHas Role` hr
		INNER JOIN `tabUser` u ON u.name = hr.parent
		WHERE hr.role = %s
			AND hr.parenttype = 'User'
			AND u.enabled = 1
			AND hr.parent != 'Administrator'
	""", role)


def _get_chain_users(doc):
	"""Get all users who have acted on this document's approval chain."""
	logs = frappe.get_all("BBF Approval Log",
		filters={"parent": doc.name, "parenttype": doc.doctype},
		fields=["action_by"])
	return set(l.action_by for l in logs if l.action_by)


# ═══════════════════════════════════════════════════════════════════════
#  APPROVAL CONTEXT (for JS)
# ═══════════════════════════════════════════════════════════════════════

def _get_po_approval_context(doc, settings):
	"""Build full approval context for PO form JS."""
	status = doc.bbf_approval_status or ""
	current_step = cint(doc.bbf_current_step)
	total_steps = cint(doc.bbf_total_steps)
	po_amount = flt(doc.grand_total)
	is_pending = status.startswith("Pending") or status.startswith("Awaiting")
	is_self_submitted = (doc.bbf_submitted_by and doc.bbf_submitted_by == frappe.session.user)
	self_skip_impossible = cint(doc.bbf_self_skip_impossible) if hasattr(doc, "bbf_self_skip_impossible") else 0
	# Allow self-approval when self-skip was impossible (single-step rule)
	effective_self_block = is_self_submitted and not self_skip_impossible

	ctx = {
		"approval_enabled": True,
		"status": status,
		"purchase_category": doc.bbf_purchase_category or "",
		"rule_name": doc.bbf_approval_rule or "",
		"current_step": current_step,
		"total_steps": total_steps,
		"po_amount": po_amount,
		"can_submit_for_approval": (doc.docstatus == 0 and status in ("", "Draft")),
		"can_review": False,
		"can_approve": False,
		"can_final_approve": False,
		"can_send_to_md": (cint(doc.bbf_can_send_to_md) == 1 and not effective_self_block),
		"can_revise": False,
		"can_reject": False,
		"can_resubmit": (status == "Revised" and frappe.session.user in (doc.owner, doc.bbf_submitted_by or "", "Administrator")),
		"self_skip_impossible": bool(self_skip_impossible),
		"is_pending": is_pending,
		"approval_chain": [],
	}

	# Build approval chain and determine user permissions from rule (single fetch)
	rule = None
	if doc.bbf_approval_rule:
		try:
			rule = frappe.get_doc("BBF PO Approval Rule", doc.bbf_approval_rule)
			ctx["approval_chain"] = _build_po_approval_chain(doc, rule)
		except Exception:
			pass

	# Determine user's permissions at current step
	if is_pending and rule:
		try:
			steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
			user_roles = frappe.get_roles(frappe.session.user)

			current_step_obj = next((s for s in steps if s.step_order == current_step), None)

			if current_step_obj:
				can_act = False
				for step in steps:
					if step.step_order >= current_step and step.role in user_roles:
						can_act = True
						break

				if can_act and not effective_self_block:
					if current_step_obj.action_type == "Review":
						ctx["can_review"] = True
					elif current_step_obj.action_type == "Approve":
						ctx["can_approve"] = True
					elif current_step_obj.action_type == "Final Approve":
						ctx["can_final_approve"] = True

					if current_step_obj.can_revise:
						ctx["can_revise"] = True
					if current_step_obj.can_reject:
						ctx["can_reject"] = True
		except Exception:
			pass

	# If awaiting MD send, only CEO can see send_to_md + revise/reject
	if status.startswith("Awaiting") and rule:
		try:
			steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
			current_step_obj = next((s for s in steps if s.step_order == current_step), None)
			user_roles = frappe.get_roles(frappe.session.user)

			if current_step_obj and current_step_obj.role in user_roles and not effective_self_block:
				ctx["can_send_to_md"] = True
				ctx["can_revise"] = current_step_obj.can_revise
				ctx["can_reject"] = current_step_obj.can_reject
		except Exception:
			pass

	return ctx


def _get_mr_approval_context(doc, settings):
	"""Build approval context for MR form JS."""
	status = doc.bbf_mr_status or ""
	is_pending = status.startswith("Pending")
	current_step = cint(doc.bbf_mr_current_step) if hasattr(doc, "bbf_mr_current_step") else 0
	mr_submitted_by = doc.bbf_mr_submitted_by if hasattr(doc, "bbf_mr_submitted_by") else None
	is_self_submitted = (is_pending and (
		doc.owner == frappe.session.user or
		(mr_submitted_by and mr_submitted_by == frappe.session.user)
	))

	ctx = {
		"approval_enabled": True,
		"status": status,
		"mr_route": doc.bbf_mr_route if hasattr(doc, "bbf_mr_route") else "",
		"current_step": current_step,
		"total_steps": cint(doc.bbf_mr_total_steps) if hasattr(doc, "bbf_mr_total_steps") else 0,
		"can_submit_for_approval": (doc.docstatus == 0 and status in ("", "Draft")),
		"can_review": False,
		"can_approve": False,
		"can_final_approve": False,
		"can_revise": False,
		"can_reject": False,
		"can_resubmit": (status == "Revised" and frappe.session.user in (
			doc.owner, mr_submitted_by or "", "Administrator"
		)),
		"is_pending": is_pending,
		"approval_chain": [],
	}

	# Build chain
	route_name = doc.bbf_mr_approval_route if hasattr(doc, "bbf_mr_approval_route") else None
	if route_name:
		try:
			route_doc = frappe.get_doc("BBF MR Approval Route", route_name)
			ctx["approval_chain"] = _build_mr_approval_chain(doc, route_doc)

			if is_pending:
				steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
				user_roles = frappe.get_roles(frappe.session.user)
				current_step_obj = next((s for s in steps if s.step_order == current_step), None)

				if current_step_obj:
					can_act = False
					for step in steps:
						if step.step_order >= current_step and step.role in user_roles:
							can_act = True
							break

					if can_act and not is_self_submitted:
						if current_step_obj.action_type == "Review":
							ctx["can_review"] = True
						elif current_step_obj.action_type == "Final Approve":
							ctx["can_final_approve"] = True

						if current_step_obj.can_revise:
							ctx["can_revise"] = True
						if current_step_obj.can_reject:
							ctx["can_reject"] = True
		except Exception:
			pass

	return ctx


def _build_po_approval_chain(doc, rule):
	"""Build the approval chain for the PO stepper UI."""
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step = cint(doc.bbf_current_step)
	status = doc.bbf_approval_status or ""

	# Get log entries
	logs = frappe.get_all("BBF Approval Log",
		filters={"parent": doc.name, "parenttype": doc.doctype,
				 "action": ["in", ["Reviewed", "Forwarded", "Approved", "Final Approved", "Sent to MD"]]},
		fields=["action_by_name", "action_by_role", "action_date", "action", "step_order"],
		order_by="action_date asc")

	log_by_step = {}
	for log in logs:
		log_by_step[cint(log.step_order)] = log

	chain = []
	for step in steps:
		log = log_by_step.get(step.step_order)
		item = {
			"step": step.step_order,
			"role": step.role_label or step.role,
			"action_type": step.action_type,
			"is_manual": step.is_manual_trigger,
			"status": "pending",
			"by": None,
			"date": None,
		}

		if log:
			item["status"] = "done"
			item["by"] = log.action_by_name
			item["date"] = format_datetime(log.action_date, "dd MMM yyyy HH:mm")
		elif step.step_order == current_step and (status.startswith("Pending") or status.startswith("Awaiting")):
			item["status"] = "current"
		elif status == "Approved":
			item["status"] = "done"
		elif status in ("Rejected", "Revised"):
			item["status"] = "skipped"

		chain.append(item)

	return chain


def _build_mr_approval_chain(doc, route_doc):
	"""Build the approval chain for the MR stepper UI."""
	steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
	current_step = cint(doc.bbf_mr_current_step) if hasattr(doc, "bbf_mr_current_step") else 0
	status = doc.bbf_mr_status or ""

	logs = frappe.get_all("BBF Approval Log",
		filters={"parent": doc.name, "parenttype": "Material Request",
				 "action": ["in", ["Reviewed", "Final Approved"]]},
		fields=["action_by_name", "action_by_role", "action_date", "action", "step_order"],
		order_by="action_date asc")

	log_by_step = {}
	for log in logs:
		log_by_step[cint(log.step_order)] = log

	chain = []
	for step in steps:
		log = log_by_step.get(step.step_order)
		item = {
			"step": step.step_order,
			"role": step.role_label or step.role,
			"action_type": step.action_type,
			"status": "pending",
			"by": None,
			"date": None,
		}

		if log:
			item["status"] = "done"
			item["by"] = log.action_by_name
			item["date"] = format_datetime(log.action_date, "dd MMM yyyy HH:mm")
		elif step.step_order == current_step and status.startswith("Pending"):
			item["status"] = "current"
		elif status == "Approved":
			item["status"] = "done"
		elif status in ("Rejected", "Revised"):
			item["status"] = "skipped"

		chain.append(item)

	return chain


# ═══════════════════════════════════════════════════════════════════════
#  DOC EVENT HOOKS
# ═══════════════════════════════════════════════════════════════════════

def po_on_cancel(doc, method):
	"""Reset approval fields when PO is cancelled."""
	if doc.bbf_approval_status:
		doc.db_set({
			"bbf_approval_status": "Rejected",
			"bbf_can_send_to_md": 0,
			"bbf_last_action": f"Cancelled on {now_datetime().strftime('%d %b %Y %H:%M')}",
		}, update_modified=False)


def po_on_amend(doc, method):
	"""Reset approval fields when PO is amended."""
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
	# v2.0 fields
	doc.bbf_purchase_category = ""
	doc.bbf_approval_rule = ""
	doc.bbf_current_step = 0
	doc.bbf_total_steps = 0
	doc.bbf_can_send_to_md = 0
	doc.bbf_self_skip_impossible = 0


def po_before_save(doc, method):
	"""Prevent amount changes while PO is in approval chain."""
	if not doc.bbf_approval_status:
		return

	status = doc.bbf_approval_status
	if status.startswith("Pending") or status.startswith("Awaiting"):
		old_amount = flt(doc.bbf_amount_at_submission)
		new_amount = flt(doc.grand_total)
		if old_amount and old_amount != new_amount:
			frappe.throw(
				_("Cannot change PO amount while it is in the approval chain. "
				  "The approver must revise the PO first.")
			)


def mr_before_save(doc, method):
	"""Prevent changes while MR is in approval chain."""
	if not hasattr(doc, "bbf_mr_status") or not doc.bbf_mr_status:
		return

	status = doc.bbf_mr_status
	if status.startswith("Pending") or status in ("Rejected", "Revised"):
		if not doc.is_new() and doc.has_value_changed("items"):
			frappe.throw(
				_("Cannot modify MR items while it is in the approval chain (status: {0}). "
				  "The approver must revise the MR first.").format(status)
			)


# ═══════════════════════════════════════════════════════════════════════
#  SLA CHECKER
# ═══════════════════════════════════════════════════════════════════════

def check_approval_sla():
	"""Find POs stuck at any approval step beyond SLA hours."""
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
				"bbf_current_step", "bbf_approval_rule", "bbf_last_sla_alert"])

	if not stuck_pos:
		return

	for po_data in stuck_pos:
		if po_data.bbf_last_sla_alert:
			last_alert_hours = time_diff_in_hours(now_datetime(), po_data.bbf_last_sla_alert)
			if last_alert_hours < sla_hours:
				continue

		hours_stuck = round(time_diff_in_hours(now_datetime(), po_data.modified), 1)

		frappe.db.set_value("Purchase Order", po_data.name,
			"bbf_last_sla_alert", now_datetime(), update_modified=False)

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

		if settings.approval_escalation_enabled:
			_auto_escalate(po_data)

	frappe.db.commit()


def _auto_escalate(po_data):
	"""Auto-forward a stuck PO to the next approval step."""
	if not po_data.bbf_approval_rule:
		return

	doc = frappe.get_doc("Purchase Order", po_data.name, for_update=True)
	rule = frappe.get_doc("BBF PO Approval Rule", po_data.bbf_approval_rule)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(po_data.bbf_current_step)

	next_step = _get_next_step(steps, current_step_order)
	if not next_step or next_step.is_manual_trigger:
		return  # Don't auto-escalate to manual-trigger steps (MD)

	next_label = next_step.role_label or next_step.role
	current_state = doc.bbf_approval_status
	next_state = f"Pending {next_label}"

	_log_approval_action(doc, "Forwarded", current_state, next_state,
		comment=f"Auto-escalated due to SLA breach",
		po_amount=flt(doc.grand_total),
		step_order=next_step.step_order, purchase_category=doc.bbf_purchase_category)

	doc.db_set({
		"bbf_approval_status": next_state,
		"bbf_current_step": next_step.step_order,
		"bbf_last_action": f"Auto-escalated to {next_label} (SLA breach) on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	_send_approval_notification(doc, "pending_approval",
		_get_role_users(next_step.role),
		extra={"next_role": next_label, "escalated": True})
