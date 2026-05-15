import frappe
from frappe import _
from frappe.utils import now_datetime, flt, cint, add_to_date, time_diff_in_hours, format_datetime
from trustbit_ethanol.ts_gate_entry.ts_budget import validate_budget_on_po_submit
from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
	get_cc_config, get_cc_users_for_step, get_cc_approvers_for_step,
	get_cc_creators, get_cc_notify_only_users,
)
from trustbit_ethanol.ts_gate_entry.ts_executive_override import (
	is_executive_override_user,
	collect_business_changes,
	log_executive_edit,
	notify_executive_edit,
)


ALLOWED_DOCTYPES = ("Purchase Order", "Material Request")


def _validate_doctype(doctype):
	if doctype not in ALLOWED_DOCTYPES:
		frappe.throw(_("Invalid document type for approval: {0}").format(doctype))


def _block_if_mr_transfer(doc):
	"""v2.9.9 — Reject Purchase-chain endpoints for Material Transfer / Material Issue MRs.

	Called at top of every internal MR action handler (_submit_mr_for_approval,
	_approve_mr, _revise_mr, _reject_mr, hold_mr, resume_mr, resubmit_document
	when type=MR, approve_as_avp_deputy). Transfer + Issue flow uses
	ts_mr_transfer endpoints exclusively.

	Gated by kill-switch — when ts_material_transfer_flow_enabled=0, this
	block lifts and these MRs route through legacy Purchase chain.
	"""
	if not isinstance(doc, str) and not hasattr(doc, "material_request_type"):
		return  # not an MR doc
	if isinstance(doc, str):
		mtype = frappe.db.get_value("Material Request", doc, "material_request_type")
	else:
		mtype = doc.material_request_type
	# Stores flow types: Material Transfer + Material Issue
	if mtype not in ("Material Transfer", "Material Issue"):
		return
	# Kill switch — if disabled, allow legacy Purchase chain to handle them
	if not cint(frappe.db.get_single_value("TS Settings", "ts_material_transfer_flow_enabled")):
		return
	frappe.throw(
		_("This action is not available for {0} MRs. "
		  "Use the Stores Manager workflow buttons (Submit for Stores Approval / "
		  "Approve & Create Stock Entry / Reject) instead.").format(mtype),
		exc=frappe.ValidationError,
	)


# ═══════════════════════════════════════════════════════════════════════
#  PUBLIC API — Whitelisted methods called from JS
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_approval_context(doctype, docname):
	"""Return approval context for the current user on a PO or MR."""
	_validate_doctype(doctype)
	doc = frappe.get_doc(doctype, docname)

	settings = frappe.get_single("TS Settings")
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
				route_doc = frappe.get_doc("TS MR Approval Route", route)
				steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
				if steps:
					target = _get_target_step_for_mr(steps, frappe.session.user, cost_center)
					return {"target_label": target.role_label or target.role}
		return {"target_label": "Department Head"}

	if doctype == "Purchase Order" and docname:
		doc = frappe.get_doc("Purchase Order", docname)
		category = _resolve_po_category(doc)
		if category:
			rule_name = _find_po_approval_rule(category, flt(doc.grand_total))
			if rule_name:
				rule = frappe.get_doc("TS PO Approval Rule", rule_name)
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
	comment = (comment or "")[:2000]
	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _approve_mr(doc, comment)

	return _approve_po(doc, comment)


@frappe.whitelist()
def send_to_md(docname, comment=""):
	"""CEO manually triggers MD approval step. Only available when next step is manual-trigger."""
	comment = (comment or "")[:2000]
	doc = frappe.get_doc("Purchase Order", docname, for_update=True)
	_validate_po_is_pending(doc)
	_validate_not_self_approving(doc)
	_validate_amount_unchanged(doc)

	rule = frappe.get_doc("TS PO Approval Rule", doc.ts_approval_rule)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.ts_current_step)

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

	current_state = doc.ts_approval_status
	next_label = next_step.role_label or next_step.role
	next_state = f"Pending {next_label}"
	role_display = current_step.role_label or current_step.role

	_log_approval_action(doc, "Sent to MD", current_state, next_state,
		comment=comment, po_amount=flt(doc.grand_total),
		step_order=next_step.step_order, purchase_category=doc.ts_purchase_category)

	doc.db_set({
		"ts_approval_status": next_state,
		"ts_current_step": next_step.step_order,
		"ts_can_send_to_md": 0,
		"ts_last_action": f"Sent to {next_label} by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	_send_approval_notification(doc, "pending_approval",
		_get_role_users(next_step.role),
		extra={"forwarded_by": role_display, "next_role": next_label})

	return {"status": "sent_to_md", "next_state": next_state}


@frappe.whitelist()
def revise_document(doctype, docname, reason, revise_to_level=None, comment=""):
	"""Send a PO/MR back for revision."""
	_validate_doctype(doctype)
	comment = (comment or "")[:2000]
	reason = (reason or "")[:2000]
	if not reason:
		frappe.throw(_("Revision reason is mandatory"))

	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _revise_mr(doc, reason, comment)

	_validate_po_is_pending(doc)
	_validate_user_can_act_on_po(doc)

	current_state = doc.ts_approval_status
	current_step = cint(doc.ts_current_step)
	role_display = _get_user_step_role(doc, current_step)

	_log_approval_action(doc, "Revised", current_state, "Revised",
		comment=comment or reason, po_amount=flt(doc.grand_total),
		step_order=current_step, purchase_category=doc.ts_purchase_category)

	doc.db_set({
		"ts_approval_status": "Revised",
		"ts_revision_count": cint(doc.ts_revision_count) + 1,
		"ts_revision_reason": reason,
		"ts_revised_by": f"{frappe.session.user} ({role_display})",
		"ts_can_send_to_md": 0,
		"ts_last_action": f"Revised by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	# Notify the original submitter
	recipients = []
	if doc.ts_submitted_by:
		recipients.append(doc.ts_submitted_by)
	if doc.owner and doc.owner not in recipients:
		recipients.append(doc.owner)

	_send_approval_notification(doc, "revised", recipients,
		extra={"revised_by": role_display, "reason": reason})

	return {"status": "revised", "message": f"PO {doc.name} has been sent back for revision"}


@frappe.whitelist()
def reject_document(doctype, docname, reason, comment=""):
	"""Reject a PO/MR. Terminal state."""
	_validate_doctype(doctype)
	comment = (comment or "")[:2000]
	reason = (reason or "")[:2000]
	if not reason:
		frappe.throw(_("Rejection reason is mandatory"))

	doc = frappe.get_doc(doctype, docname, for_update=True)

	if doctype == "Material Request":
		return _reject_mr(doc, reason, comment)

	_validate_po_is_pending(doc)
	_validate_user_can_act_on_po(doc)

	current_state = doc.ts_approval_status
	current_step = cint(doc.ts_current_step)
	role_display = _get_user_step_role(doc, current_step)

	_log_approval_action(doc, "Rejected", current_state, "Rejected",
		comment=comment or reason, po_amount=flt(doc.grand_total),
		step_order=current_step, purchase_category=doc.ts_purchase_category)

	doc.db_set({
		"ts_approval_status": "Rejected",
		"ts_can_send_to_md": 0,
		"ts_last_action": f"Rejected by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	# Rejected PO stays as Draft with "Rejected" status — fields are locked in JS
	# (cancel() is invalid on Draft docs; docstatus 0 → 2 is not allowed by Frappe)

	# Notify all stakeholders
	all_users = _get_chain_users(doc)
	if doc.ts_submitted_by:
		all_users.add(doc.ts_submitted_by)

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
		_block_if_mr_transfer(doc)  # v2.9.9
		return _resubmit_mr(doc)

	if doc.ts_approval_status != "Revised":
		frappe.throw(_("Only revised POs can be resubmitted"))

	_validate_resubmit_permission(doc)

	# Re-resolve category and rule (amount may have changed)
	category = _resolve_po_category(doc)
	rule_name = _find_po_approval_rule(category, flt(doc.grand_total))
	rule = frappe.get_doc("TS PO Approval Rule", rule_name)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)

	# Always restart from step 1 (with self-skip)
	target = _get_target_step_with_skip(steps, frappe.session.user)
	next_state = f"Pending {target.role_label or target.role}"

	_log_approval_action(doc, "Resubmitted", "Revised", next_state,
		po_amount=flt(doc.grand_total), resubmit_mode=mode,
		step_order=target.step_order, purchase_category=category)

	doc.db_set({
		"ts_approval_status": next_state,
		"ts_purchase_category": category,
		"ts_approval_rule": rule_name,
		"ts_current_step": target.step_order,
		"ts_total_steps": len(steps),
		"ts_can_send_to_md": 0,
		"ts_amount_at_submission": flt(doc.grand_total),
		"ts_resubmit_mode": f"Restarted from {target.role_label or target.role}",
		"ts_last_action": f"Resubmitted on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	_send_approval_notification(doc, "pending_approval",
		_get_role_users(target.role),
		extra={"next_role": target.role_label or target.role, "resubmitted": True})

	return {"status": "resubmitted", "next_state": next_state}


@frappe.whitelist()
def hold_mr(docname, reason):
	"""Put an MR on hold. Only Reviewers (HOD) can hold."""
	reason = (reason or "")[:2000]
	if not reason:
		frappe.throw(_("Hold reason is mandatory"))

	doc = frappe.get_doc("Material Request", docname, for_update=True)
	_block_if_mr_transfer(doc)  # v2.9.9
	status = doc.ts_mr_status or ""

	if not status.startswith("Pending"):
		frappe.throw(_("Only pending MRs can be put on hold (status: {0})").format(status))

	_validate_user_can_act_on_mr(doc)

	route_name = doc.ts_mr_approval_route
	if not route_name:
		frappe.throw(_("This MR has no approval route set."))

	route_doc = frappe.get_doc("TS MR Approval Route", route_name)
	steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.ts_mr_current_step)
	current_step = next((s for s in steps if s.step_order == current_step_order), None)

	if not current_step:
		frappe.throw(_("Current MR approval step not found"))

	# Only Review steps (HOD) can hold — Final Approvers should approve or reject
	if current_step.action_type != "Review":
		frappe.throw(_("Hold is only available for Review steps (not {0})").format(current_step.action_type))

	role_display = current_step.role_label or current_step.role
	current_state = doc.ts_mr_status
	hold_state = f"On Hold - {role_display}"

	_log_mr_action(doc, "Held", current_state, hold_state,
		comment=reason, step_order=current_step_order,
		purchase_category=route_name)

	doc.db_set({
		"ts_mr_status": hold_state,
		"ts_mr_hold_reason": reason,
		"ts_mr_held_by": f"{frappe.session.user} ({role_display})",
		"ts_mr_held_at_step": current_step_order,
	}, update_modified=True)

	# Notify creator
	recipients = [doc.owner]
	mr_submitted_by = doc.ts_mr_submitted_by if hasattr(doc, "ts_mr_submitted_by") else None
	if mr_submitted_by and mr_submitted_by not in recipients:
		recipients.append(mr_submitted_by)

	_send_approval_notification(doc, "mr_held", recipients,
		extra={"held_by": role_display, "reason": reason})

	return {"status": "held", "hold_state": hold_state}


@frappe.whitelist()
def resume_mr(docname, comment=""):
	"""Resume a held MR. Returns it to Pending at the same step."""
	comment = (comment or "")[:2000]
	doc = frappe.get_doc("Material Request", docname, for_update=True)
	_block_if_mr_transfer(doc)  # v2.9.9
	status = doc.ts_mr_status or ""

	if not status.startswith("On Hold"):
		frappe.throw(_("Only held MRs can be resumed (status: {0})").format(status))

	_validate_user_can_act_on_mr(doc)

	route_name = doc.ts_mr_approval_route
	if not route_name:
		frappe.throw(_("This MR has no approval route set."))

	route_doc = frappe.get_doc("TS MR Approval Route", route_name)
	steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)

	# Restore to the step where it was held
	held_step_order = cint(doc.ts_mr_held_at_step) if hasattr(doc, "ts_mr_held_at_step") else cint(doc.ts_mr_current_step)
	held_step = next((s for s in steps if s.step_order == held_step_order), None)

	if not held_step:
		frappe.throw(_("Cannot find the approval step to resume at"))

	role_display = held_step.role_label or held_step.role
	pending_state = f"Pending {role_display}"

	_log_mr_action(doc, "Resumed", status, pending_state,
		comment=comment, step_order=held_step_order,
		purchase_category=route_name)

	doc.db_set({
		"ts_mr_status": pending_state,
		"ts_mr_current_step": held_step_order,
		"ts_mr_hold_reason": "",
		"ts_mr_held_by": "",
		"ts_mr_held_at_step": 0,
	}, update_modified=True)

	# Notify the step's approvers so they know it's back
	cost_center = _get_mr_cost_center(doc)
	cc_config = get_cc_config(cost_center)
	recipients = []
	if cc_config:
		recipients = get_cc_users_for_step(cc_config, held_step_order)
	if not recipients:
		recipients = _get_role_users(held_step.role)

	_send_approval_notification(doc, "mr_pending", recipients,
		extra={"next_role": role_display, "resumed": True})

	return {"status": "resumed", "pending_state": pending_state}


# ═══════════════════════════════════════════════════════════════════════
#  PO-SPECIFIC HANDLERS
# ═══════════════════════════════════════════════════════════════════════

def _submit_po_for_approval(doc):
	"""Submit a PO for approval using category-based routing."""
	_validate_po_submittable(doc)

	# v2.10.0 — Budget breach detection.
	# Kill switch ON (default): breach diverts PO to 'Pending Budget Override' and
	#   auto-creates a TS Budget Override Approval doc. NO throw.
	# Kill switch OFF: legacy hard-throw behavior preserved via validate_budget_on_po_submit.
	#
	# v2.10.0.3 — PO inherited from an approved Material Request bypasses the
	# breach detector. The MR was the gating event in the procurement lifecycle
	# (it either fit budget at submit, or was approved through the override flow
	# itself). Re-checking at PO submit produced false positives on every MR→PO
	# conversion. Trade-off: PO line rates inflated beyond MR estimate aren't
	# re-vetted here; the actual GL spend at PI/payment posting catches real
	# overruns. Standalone POs (no item.material_request) still hit the check.
	# Fail-closed: forged `material_request` values that don't reference an
	# Approved + submitted MR are ignored — every claimed MR must verify.
	claimed_mr_names = {
		item.get("material_request")
		for item in (doc.get("items") or [])
		if item.get("material_request")
	}
	po_has_mr_source = False
	if claimed_mr_names:
		verified = frappe.db.count(
			"Material Request",
			filters={
				"name": ["in", list(claimed_mr_names)],
				"docstatus": 1,
				"ts_mr_status": "Approved",
				"company": doc.company,  # defense-in-depth: same-company MR only
			},
		)
		po_has_mr_source = (verified == len(claimed_mr_names))
	settings = frappe.get_single("TS Settings")
	if not po_has_mr_source and cint(settings.enable_budget_check) and not frappe.flags.get("in_budget_override_approval"):
		from trustbit_ethanol.ts_gate_entry.ts_budget import (
			_is_override_flow_enabled as _bo_enabled,
			detect_budget_breach_for_po,
		)

		if _bo_enabled():
			breach = detect_budget_breach_for_po(doc)
			if breach.get("has_breach"):
				from trustbit_ethanol.ts_gate_entry.ts_budget_override import (
					create_budget_override_for_doc,
				)

				override = create_budget_override_for_doc("Purchase Order", doc.name)
				frappe.flags.in_budget_override_approval = True
				try:
					doc.db_set({
						"ts_approval_status": "Pending Budget Override",
						"ts_budget_override_ref": override["name"],
						"ts_budget_breach_flag": 1,
						"ts_amount_at_submission": flt(doc.grand_total),
						"ts_submitted_by": frappe.session.user,
						"ts_last_action": f"Held pending budget approval on {now_datetime().strftime('%d %b %Y %H:%M')}",
					}, update_modified=True)
				finally:
					frappe.flags.in_budget_override_approval = False
				# v2.10.0.1 — Lesson 238 best-effort audit log.
				# TS Approval Log action Select doesn't include "Held for Budget Override" yet
				# (will be added via PropertySetter in a follow-up). Wrap so the docstatus
				# transition + override creation NEVER block on the audit row.
				try:
					_log_approval_action(
						doc,
						"Held for Budget Override",
						"Draft",
						"Pending Budget Override",
						comment=f"Linked override: {override['name']} ({breach.get('breach_type')})",
						po_amount=flt(doc.grand_total),
						step_order=0,
						purchase_category=doc.get("ts_purchase_category") or "",
					)
				except Exception:
					try:
						frappe.log_error(
							title="budget override log skipped (PO)",
							message=f"doc={doc.name} override={override['name']} err={frappe.get_traceback()[:500]}",
						)
					except Exception:
						pass
				return {
					"status": "ok",
					"next_state": "Pending Budget Override",
					"override": override["name"],
				}
		else:
			# Kill switch OFF — legacy hard-throw.
			validate_budget_on_po_submit(doc)

	status = doc.ts_approval_status
	if status and status not in ("", "Draft", "Not Submitted"):
		frappe.throw(_("This PO is already in the approval chain (status: {0})").format(status))

	# Resolve category from PO items → Item Group → TS Purchase Category
	category = _resolve_po_category(doc)
	rule_name = _find_po_approval_rule(category, flt(doc.grand_total))
	rule = frappe.get_doc("TS PO Approval Rule", rule_name)
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
		"ts_approval_status": next_state,
		"ts_purchase_category": category,
		"ts_approval_rule": rule_name,
		"ts_current_step": target.step_order,
		"ts_total_steps": len(steps),
		"ts_can_send_to_md": 0,
		"ts_submitted_by": frappe.session.user,
		"ts_self_skip_impossible": 1 if self_skip_impossible else 0,
		"ts_amount_at_submission": flt(doc.grand_total),
		"ts_last_action": f"Submitted for approval on {now_datetime().strftime('%d %b %Y %H:%M')}",
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

	# v2.10.0 — Block normal-route approval while a budget override is still pending.
	# (The override approval endpoint itself sets frappe.flags.in_budget_override_approval
	# to bypass this check during the resume transition.)
	if doc.get("ts_approval_status") == "Pending Budget Override" and not frappe.flags.get(
		"in_budget_override_approval"
	):
		ref = doc.get("ts_budget_override_ref")
		frappe.throw(
			_("This PO is awaiting CEO budget-override approval ({0}). Approve the override first.").format(
				ref or "(unlinked)"
			)
		)

	if not doc.ts_approval_rule:
		frappe.throw(_("This PO has no approval rule set. Please resubmit."))

	rule = frappe.get_doc("TS PO Approval Rule", doc.ts_approval_rule)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.ts_current_step)

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
	current_state = doc.ts_approval_status
	# Use the acting user's actual approval role (not the step's role) for display
	role_display = _get_user_step_role(doc, current_step_order)

	if current_step.action_type == "Final Approve":
		# FINAL APPROVAL — submit the PO
		_log_approval_action(doc, "Final Approved", current_state, "Approved",
			comment=comment, po_amount=po_amount,
			step_order=current_step_order, purchase_category=doc.ts_purchase_category)

		doc.db_set({
			"ts_approval_status": "Approved",
			"ts_approved_by": frappe.session.user,
			"ts_approved_date": now_datetime(),
			"ts_can_send_to_md": 0,
			"ts_last_action": f"Final Approved by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')}",
		}, update_modified=True)

		# Submit the PO (docstatus 0 → 1)
		doc.reload()
		doc.submit()

		# Notify all stakeholders
		all_users = _get_chain_users(doc)
		if doc.ts_submitted_by:
			all_users.add(doc.ts_submitted_by)
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
				step_order=current_step_order, purchase_category=doc.ts_purchase_category)

			doc.db_set({
				"ts_approval_status": f"Awaiting Send to {next_step.role_label or next_step.role}",
				"ts_can_send_to_md": 1,
				"ts_last_action": f"{action_label} by {role_display} on {now_datetime().strftime('%d %b %Y %H:%M')} — awaiting manual send to {next_step.role_label or next_step.role}",
			}, update_modified=True)

			return {"status": "awaiting_md", "message": f"PO reviewed. CEO must now send to {next_step.role_label or next_step.role}."}

		elif next_step:
			# Auto-forward to next step
			next_label = next_step.role_label or next_step.role
			next_state = f"Pending {next_label}"
			action_label = "Reviewed" if current_step.action_type == "Review" else "Forwarded"

			_log_approval_action(doc, action_label, current_state, next_state,
				comment=comment, po_amount=po_amount,
				step_order=current_step_order, purchase_category=doc.ts_purchase_category)

			doc.db_set({
				"ts_approval_status": next_state,
				"ts_current_step": next_step.step_order,
				"ts_can_send_to_md": 0,
				"ts_last_action": f"{action_label} by {role_display} to {next_label} on {now_datetime().strftime('%d %b %Y %H:%M')}",
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
	_block_if_mr_transfer(doc)  # v2.9.9 — Transfer uses ts_mr_transfer endpoints
	if doc.docstatus != 0:
		frappe.throw(_("Only Draft Material Requests can be submitted for approval"))

	if not (doc.get("items") or []):
		frappe.throw(_("MR must have at least one item to submit for approval"))

	status = doc.ts_mr_status
	if status and status not in ("", "Draft", "Not Submitted"):
		frappe.throw(_("This MR is already in the approval chain (status: {0})").format(status))

	cost_center = _get_mr_cost_center(doc)
	if not cost_center:
		frappe.throw(_("Cost Center is required for MR approval routing. Please set it on the MR or its items."))

	# v2.10.0 — Budget breach detection (mirrors PO branch).
	# Inserted BEFORE route selection so a breach diverts straight to override approval.
	settings = frappe.get_single("TS Settings")
	if cint(settings.enable_budget_check) and not frappe.flags.get("in_budget_override_approval"):
		from trustbit_ethanol.ts_gate_entry.ts_budget import (
			_is_override_flow_enabled as _bo_enabled,
			detect_budget_breach_for_mr,
		)

		if _bo_enabled():
			breach = detect_budget_breach_for_mr(doc)
			if breach.get("has_breach"):
				from trustbit_ethanol.ts_gate_entry.ts_budget_override import (
					create_budget_override_for_doc,
				)

				override = create_budget_override_for_doc("Material Request", doc.name)
				frappe.flags.in_budget_override_approval = True
				try:
					doc.db_set({
						"ts_mr_status": "Pending Budget Override",
						"ts_budget_override_ref": override["name"],
						"ts_budget_breach_flag": 1,
						"ts_mr_submitted_by": frappe.session.user,
					}, update_modified=True)
				finally:
					frappe.flags.in_budget_override_approval = False
				# v2.10.0.1 — Lesson 238 best-effort audit log (see PO branch above).
				try:
					_log_mr_action(
						doc,
						"Held for Budget Override",
						"Draft",
						"Pending Budget Override",
						step_order=0,
						purchase_category=breach.get("breach_type") or "",
					)
				except Exception:
					try:
						frappe.log_error(
							title="budget override log skipped (MR)",
							message=f"doc={doc.name} override={override['name']} err={frappe.get_traceback()[:500]}",
						)
					except Exception:
						pass
				return "Pending Budget Override"

	route_name = _find_mr_route(cost_center)
	if not route_name:
		frappe.throw(_("No MR approval route configured for Cost Center: {0}").format(cost_center))

	route_doc = frappe.get_doc("TS MR Approval Route", route_name)
	steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)

	# Self-skip (role-based) + CC config skip (no Reviewer for step)
	target = _get_target_step_for_mr(steps, frappe.session.user, cost_center)
	next_state = f"Pending {target.role_label or target.role}"

	# Track if self-skip was impossible (single-step route where submitter has the role)
	self_skip_impossible = (target == steps[0] and target.role in frappe.get_roles(frappe.session.user) and len(steps) == 1)

	_log_mr_action(doc, "Submitted", "Draft", next_state,
		step_order=target.step_order, purchase_category=route_name)

	doc.db_set({
		"ts_mr_status": next_state,
		"ts_mr_route": route_name,
		"ts_mr_approval_route": route_name,
		"ts_mr_current_step": target.step_order,
		"ts_mr_total_steps": len(steps),
		"ts_mr_submitted_by": frappe.session.user,
		"ts_mr_self_skip_impossible": 1 if self_skip_impossible else 0,
	}, update_modified=True)

	# CC-aware notification: notify only mapped users if CC config exists
	cost_center = _get_mr_cost_center(doc)
	cc_config = get_cc_config(cost_center)
	recipients = []
	if cc_config:
		recipients = get_cc_users_for_step(cc_config, target.step_order)
	if not recipients:
		recipients = _get_role_users(target.role)

	_send_approval_notification(doc, "mr_pending", recipients,
		extra={"next_role": target.role_label or target.role})

	return next_state


def _approve_mr(doc, comment=""):
	"""Approve/Review MR based on current step."""
	_block_if_mr_transfer(doc)  # v2.9.9

	# v2.10.0 — Block normal-route approval while a budget override is still pending.
	if doc.get("ts_mr_status") == "Pending Budget Override" and not frappe.flags.get(
		"in_budget_override_approval"
	):
		ref = doc.get("ts_budget_override_ref")
		frappe.throw(
			_("This MR is awaiting CEO budget-override approval ({0}). Approve the override first.").format(
				ref or "(unlinked)"
			)
		)

	if not (doc.ts_mr_status or "").startswith("Pending"):
		frappe.throw(_("This MR is not pending approval (status: {0})").format(doc.ts_mr_status))

	# Prevent self-approval — check both creator and submitter
	# Exception: when self-skip was impossible (single-step route where submitter has the only role)
	mr_self_skip_impossible = cint(doc.ts_mr_self_skip_impossible) if hasattr(doc, "ts_mr_self_skip_impossible") else 0
	mr_submitted_by = doc.ts_mr_submitted_by if hasattr(doc, "ts_mr_submitted_by") else None
	if not mr_self_skip_impossible:
		if doc.owner == frappe.session.user:
			frappe.throw(_("You cannot approve a Material Request that you created."))
		if mr_submitted_by and mr_submitted_by == frappe.session.user:
			frappe.throw(_("You cannot approve a Material Request that you submitted for approval."))

	route_name = doc.ts_mr_approval_route
	if not route_name:
		frappe.throw(_("This MR has no approval route set. Please resubmit."))

	route_doc = frappe.get_doc("TS MR Approval Route", route_name)
	steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.ts_mr_current_step)

	current_step = next((s for s in steps if s.step_order == current_step_order), None)
	if not current_step:
		frappe.throw(_("Current MR approval step not found"))

	# Validate user can act — CC config check + role-based higher-level override
	_validate_user_can_act_on_mr(doc)

	role_display = _get_user_mr_step_role(doc, current_step_order) or current_step.role_label or current_step.role
	current_state = doc.ts_mr_status

	if current_step.action_type == "Final Approve":
		# FINAL APPROVAL
		_log_mr_action(doc, "Final Approved", current_state, "Approved",
			comment=comment, step_order=current_step_order, purchase_category=route_name)

		doc.db_set({
			"ts_mr_status": "Approved",
			"ts_mr_approved_by": frappe.session.user,
			"ts_mr_approved_date": now_datetime(),
		}, update_modified=True)

		# Submit the MR (docstatus 0 → 1)
		doc.reload()
		doc.submit()

		# Notify owner + CC Notify Only users (Purchase team for MR→PO conversion)
		mr_approved_recipients = [doc.owner]
		cost_center = _get_mr_cost_center(doc)
		cc_config = get_cc_config(cost_center)
		if cc_config:
			notify_only = get_cc_notify_only_users(cc_config)
			mr_approved_recipients.extend(notify_only)
		# Also notify all Purchase Users + Purchase Head for MR→PO conversion
		purchase_users = _get_role_users("Purchase Manager") + _get_role_users("Purchase User")
		mr_approved_recipients.extend(purchase_users)
		_send_approval_notification(doc, "mr_approved", mr_approved_recipients,
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
			"ts_mr_status": next_state,
			"ts_mr_current_step": next_step.step_order,
		}, update_modified=True)

		# CC-aware notification (with fallback to role-based)
		cost_center = _get_mr_cost_center(doc)
		cc_config = get_cc_config(cost_center)
		recipients = []
		if cc_config:
			recipients = get_cc_users_for_step(cc_config, next_step.step_order)
		if not recipients:
			recipients = _get_role_users(next_step.role)

		_send_approval_notification(doc, "mr_pending", recipients,
			extra={"forwarded_by": role_display, "next_role": next_label})

		return {"status": "forwarded", "next_state": next_state}


def _revise_mr(doc, reason, comment=""):
	"""Revise MR — send back to creator."""
	_block_if_mr_transfer(doc)  # v2.9.9
	if not (doc.ts_mr_status or "").startswith("Pending"):
		frappe.throw(_("This MR is not pending approval (status: {0})").format(doc.ts_mr_status))

	_validate_user_can_act_on_mr(doc)

	current_step = cint(doc.ts_mr_current_step)
	role_display = _get_user_mr_step_role(doc, current_step)
	current_state = doc.ts_mr_status

	_log_mr_action(doc, "Revised", current_state, "Revised",
		comment=comment or reason, step_order=current_step,
		purchase_category=doc.ts_mr_approval_route)

	doc.db_set({
		"ts_mr_status": "Revised",
		"ts_mr_revision_reason": reason,
	}, update_modified=True)

	notify_users = list(set(filter(None, [doc.owner, doc.ts_mr_submitted_by])))
	_send_approval_notification(doc, "mr_revised", notify_users,
		extra={"revised_by": role_display, "reason": reason})

	return {"status": "revised"}


def _reject_mr(doc, reason, comment=""):
	"""Reject MR."""
	_block_if_mr_transfer(doc)  # v2.9.9
	if not (doc.ts_mr_status or "").startswith("Pending"):
		frappe.throw(_("This MR is not pending approval (status: {0})").format(doc.ts_mr_status))

	_validate_user_can_act_on_mr(doc)

	current_step = cint(doc.ts_mr_current_step)
	role_display = _get_user_mr_step_role(doc, current_step)
	current_state = doc.ts_mr_status

	_log_mr_action(doc, "Rejected", current_state, "Rejected",
		comment=comment or reason, step_order=current_step,
		purchase_category=doc.ts_mr_approval_route)

	doc.db_set({
		"ts_mr_status": "Rejected",
	}, update_modified=True)

	# Rejected MR stays as Draft with "Rejected" status — fields are locked in JS
	# (cancel() is invalid on Draft docs; docstatus 0 → 2 is not allowed by Frappe)

	notify_users = list(set(filter(None, [doc.owner, doc.ts_mr_submitted_by])))
	_send_approval_notification(doc, "mr_rejected", notify_users,
		extra={"rejected_by": role_display, "reason": reason})
	_send_post_approval_notification(doc, "mr_rejection")

	return {"status": "rejected"}


def _resubmit_mr(doc):
	"""Resubmit a revised MR."""
	if doc.ts_mr_status != "Revised":
		frappe.throw(_("Only revised MRs can be resubmitted"))

	mr_submitted_by = doc.ts_mr_submitted_by if hasattr(doc, "ts_mr_submitted_by") else None
	allowed_users = {doc.owner, "Administrator"}
	if mr_submitted_by:
		allowed_users.add(mr_submitted_by)
	if frappe.session.user not in allowed_users:
		frappe.throw(_("Only the MR creator or original submitter can resubmit after revision"))

	cost_center = _get_mr_cost_center(doc)
	route_name = _find_mr_route(cost_center)
	if not route_name:
		frappe.throw(_("No MR approval route configured for Cost Center: {0}").format(cost_center))

	route_doc = frappe.get_doc("TS MR Approval Route", route_name)
	steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)

	target = _get_target_step_for_mr(steps, frappe.session.user, cost_center)
	next_state = f"Pending {target.role_label or target.role}"

	_log_mr_action(doc, "Resubmitted", "Revised", next_state,
		step_order=target.step_order, purchase_category=route_name)

	doc.db_set({
		"ts_mr_status": next_state,
		"ts_mr_route": route_name,
		"ts_mr_approval_route": route_name,
		"ts_mr_current_step": target.step_order,
		"ts_mr_total_steps": len(steps),
		"ts_mr_submitted_by": frappe.session.user,
		"ts_mr_revision_reason": "",
	}, update_modified=True)

	# CC-aware notification (with fallback to role-based)
	cc_config = get_cc_config(cost_center)
	recipients = []
	if cc_config:
		recipients = get_cc_users_for_step(cc_config, target.step_order)
	if not recipients:
		recipients = _get_role_users(target.role)
	_send_approval_notification(doc, "mr_pending", recipients,
		extra={"next_role": target.role_label or target.role, "resubmitted": True})

	return {"status": "resubmitted"}


# ═══════════════════════════════════════════════════════════════════════
#  CATEGORY & RULE ROUTING (PO)
# ═══════════════════════════════════════════════════════════════════════

def _resolve_po_category(doc):
	"""Auto-detect TS Purchase Category from PO items → Item Group mapping."""
	item_codes = [item.item_code for item in (doc.get("items") or []) if item.item_code]
	item_groups = set()
	if item_codes:
		results = frappe.get_all("Item",
			filters={"name": ["in", item_codes]},
			fields=["item_group"],
			pluck="item_group")
		item_groups = set(results)

	if not item_groups:
		settings = frappe.get_single("TS Settings")
		if settings.default_po_category:
			return settings.default_po_category
		frappe.throw(_("Cannot determine purchase category — no items with Item Groups on this PO"))

	# Map each Item Group to its TS Purchase Category (batch query)
	categories = set()
	if item_groups:
		cat_results = frappe.db.sql("""
			SELECT item_group, parent FROM `tabTS Purchase Category Item`
			WHERE item_group IN %s
		""", [list(item_groups)], as_dict=True)
		for row in cat_results:
			categories.add(row.parent)

	if not categories:
		settings = frappe.get_single("TS Settings")
		if settings.default_po_category:
			return settings.default_po_category
		frappe.throw(_(
			"No purchase category mapped for Item Group(s): {0}. "
			"Please configure TS Purchase Category or set a default in TS Settings."
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
			step_count = frappe.db.count("TS PO Approval Step",
				filters={"parent": rule_name})
			if step_count > max_steps:
				max_steps = step_count
				strictest = cat

	return strictest or list(categories)[0]


def _find_po_approval_rule(category, amount):
	"""Find the matching TS PO Approval Rule for this category + amount."""
	rules = frappe.get_all("TS PO Approval Rule",
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
		"Please configure TS PO Approval Rule."
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
	"""Find the TS MR Approval Route for this Cost Center."""
	result = frappe.db.sql("""
		SELECT r.name
		FROM `tabTS MR Approval Route` r
		INNER JOIN `tabTS MR Route Cost Center` cc ON cc.parent = r.name
		WHERE cc.cost_center = %s AND r.is_active = 1
		LIMIT 1
	""", cost_center, as_dict=True)

	return result[0].name if result else None


# ═══════════════════════════════════════════════════════════════════════
#  STEP HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _get_target_step_with_skip(steps, user):
	"""Get the first step where user does NOT have the role (self-skip).
	Skips ALL consecutive steps where the submitter holds the role.
	Used for PO approval (no CC config awareness).
	"""
	if not steps:
		frappe.throw(_("No approval steps configured"))

	user_roles = frappe.get_roles(user)

	for step in steps:
		if step.role not in user_roles:
			return step

	# User has roles for ALL steps — return last step with self_skip_impossible
	return steps[-1]


def _get_target_step_for_mr(steps, user, cost_center):
	"""Get target step for MR with both role-based self-skip AND CC config skip.
	Skips steps where:
	  1. User has the step's role (self-skip), OR
	  2. CC config has no Reviewer/approver for that step (no HOD = skip)
	"""
	if not steps:
		frappe.throw(_("No approval steps configured"))

	user_roles = frappe.get_roles(user)
	cc_config = get_cc_config(cost_center) if cost_center else None

	for step in steps:
		# Check 1: Role-based self-skip (user has this step's role)
		if step.role in user_roles:
			continue

		# Check 2: CC config skip — if CC config exists and has NO users for this step,
		# skip it (e.g., Civil CC has no Reviewer/HOD → skip Dept Head step)
		if cc_config and step.action_type == "Review":
			step_users = get_cc_approvers_for_step(cc_config, step.step_order)
			if not step_users:
				continue

		# This step has a valid approver and user doesn't hold the role — stop here
		return step

	# User has roles for ALL steps (or all skipped) — return last step
	return steps[-1]


def _get_next_step(steps, current_step_order):
	"""Get the next step after current_step_order."""
	for step in sorted(steps, key=lambda s: s.step_order):
		if step.step_order > current_step_order:
			return step
	return None


def _get_user_step_role(doc, current_step_order):
	"""Get the display label for the current user's role at the given step."""
	if not doc.ts_approval_rule:
		return frappe.utils.get_fullname(frappe.session.user)

	rule = frappe.get_doc("TS PO Approval Rule", doc.ts_approval_rule)
	user_roles = frappe.get_roles(frappe.session.user)
	for step in sorted(rule.approval_steps, key=lambda s: s.step_order):
		if step.step_order >= current_step_order and step.role in user_roles:
			return step.role_label or step.role
	return frappe.utils.get_fullname(frappe.session.user)


def _get_user_mr_step_role(doc, current_step_order):
	"""Get the display label for the current user's role at the MR step."""
	if not doc.ts_mr_approval_route:
		return frappe.utils.get_fullname(frappe.session.user)

	route = frappe.get_doc("TS MR Approval Route", doc.ts_mr_approval_route)
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
	status = doc.ts_approval_status or ""
	if not (status.startswith("Pending") or status.startswith("Awaiting")):
		frappe.throw(
			_("This PO is not pending approval (current status: {0}). "
			  "Only POs with 'Pending' or 'Awaiting' status can be acted upon.").format(status or "Draft")
		)


def _validate_not_self_approving(doc):
	"""Prevent the same user who submitted or created from approving their own PO.
	Exception: when self-skip was impossible (single-step rule where submitter has the only role).
	"""
	is_submitter = doc.ts_submitted_by and doc.ts_submitted_by == frappe.session.user
	is_creator = doc.owner and doc.owner == frappe.session.user
	if is_submitter or is_creator:
		if cint(doc.ts_self_skip_impossible):
			return
		frappe.throw(
			_("You cannot approve a PO that you created or submitted for approval. "
			  "A different approver must act on this PO.")
		)


def _validate_user_can_act_on_po(doc):
	"""Validate user has a role that can act on the PO at its current step."""
	if not doc.ts_approval_rule:
		return

	rule = frappe.get_doc("TS PO Approval Rule", doc.ts_approval_rule)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(doc.ts_current_step)
	user_roles = frappe.get_roles(frappe.session.user)

	for step in steps:
		if step.step_order >= current_step_order and step.role in user_roles:
			return

	frappe.throw(_("You don't have permission to act on this PO at this step"))


def _validate_user_can_act_on_mr(doc):
	"""Validate user has a role that can act on the MR at its current step.
	If CC config exists, also validate user is in the config for this step.

	v2.9.7 — AVP Deputy mode bypass: when frappe.flags.in_avp_deputy is set, the
	caller (ts_avp_deputy.approve_as_avp_deputy) has already validated kill switch
	+ CEO role + Pending AVP status + self-approval. Skip route/CC checks here so
	CEO can act at the AVP step on routes where AVP is the final approver
	(no higher CEO step in the route to enable the standard higher-level override).
	"""
	if getattr(frappe.flags, "in_avp_deputy", False):
		return
	if not doc.ts_mr_approval_route:
		return

	route = frappe.get_doc("TS MR Approval Route", doc.ts_mr_approval_route)
	steps = sorted(route.approval_steps, key=lambda s: s.step_order)
	# For On Hold MRs, use the held step (stored in ts_mr_held_at_step)
	status = doc.ts_mr_status or ""
	if status.startswith("On Hold"):
		current_step_order = cint(doc.ts_mr_held_at_step) if hasattr(doc, "ts_mr_held_at_step") else cint(doc.ts_mr_current_step)
	else:
		current_step_order = cint(doc.ts_mr_current_step)
	user_roles = frappe.get_roles(frappe.session.user)

	# Standard role check (>= for higher-level override)
	role_ok = False
	for step in steps:
		if step.step_order >= current_step_order and step.role in user_roles:
			role_ok = True
			break
	if not role_ok:
		frappe.throw(_("You don't have permission to act on this MR at this step"))

	# CC config check — if config exists, verify user is mapped for this step
	cost_center = _get_mr_cost_center(doc)
	cc_config = get_cc_config(cost_center)
	if cc_config:
		cc_approvers = get_cc_approvers_for_step(cc_config, current_step_order)
		if cc_approvers and frappe.session.user not in cc_approvers:
			# Allow higher-level override: check if user is approver at ANY higher step
			all_higher_approvers = []
			for step in steps:
				if step.step_order > current_step_order:
					all_higher_approvers.extend(get_cc_approvers_for_step(cc_config, step.step_order))
			if frappe.session.user not in all_higher_approvers:
				frappe.throw(_("You are not configured as an approver for {0} at this step").format(cost_center))


def _validate_resubmit_permission(doc):
	"""Only the original submitter or PO owner can resubmit."""
	allowed_users = {doc.owner}
	if doc.ts_submitted_by:
		allowed_users.add(doc.ts_submitted_by)
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
	submitted_amount = flt(doc.ts_amount_at_submission)
	current_amount = flt(doc.grand_total)

	# Use explicit > 0 check (not falsy) to prevent bypass when amount is 0
	if submitted_amount > 0 and submitted_amount != current_amount:
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
	"""Add an immutable row to the ts_approval_log child table."""
	user_roles = frappe.get_roles(frappe.session.user)
	user_role = ""
	if doc.ts_approval_rule:
		try:
			rule = frappe.get_doc("TS PO Approval Rule", doc.ts_approval_rule)
			# Match role at or above the current step_order to get the correct role
			effective_step = cint(step_order) or cint(doc.ts_current_step)
			for step in sorted(rule.approval_steps, key=lambda s: s.step_order):
				if step.step_order >= effective_step and step.role in user_roles:
					user_role = step.role_label or step.role
					break
		except Exception:
			frappe.log_error(title="Approval Context Error", message=frappe.get_traceback())

	log = frappe.get_doc({
		"doctype": "TS Approval Log",
		"parent": doc.name,
		"parenttype": doc.doctype,
		"parentfield": "ts_approval_log",
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
	"""Add an immutable row to the ts_mr_log child table."""
	user_roles = frappe.get_roles(frappe.session.user)
	user_role = ""
	if doc.ts_mr_approval_route:
		try:
			route = frappe.get_doc("TS MR Approval Route", doc.ts_mr_approval_route)
			# Match role at or above the current step_order to get the correct role
			effective_step = cint(step_order) or cint(doc.ts_mr_current_step if hasattr(doc, "ts_mr_current_step") else 0)
			for step in sorted(route.approval_steps, key=lambda s: s.step_order):
				if step.step_order >= effective_step and step.role in user_roles:
					user_role = step.role_label or step.role
					break
		except Exception:
			frappe.log_error(title="Approval Context Error", message=frappe.get_traceback())

	log = frappe.get_doc({
		"doctype": "TS Approval Log",
		"parent": doc.name,
		"parenttype": "Material Request",
		"parentfield": "ts_mr_log",
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
		"mr_held": f"[On Hold] MR {doc.name} — Put on Hold",
		"mr_resumed": f"[Resumed] MR {doc.name} — Resumed from Hold",
		"sla_breach": f"[CTL Alert] {doc_label} {doc.name} stuck at approval step",
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
	"""Send notifications to configured post-approval recipients in TS Settings."""
	try:
		settings = frappe.get_single("TS Settings")
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
			items_summary.append("{0} (Qty: {1})".format(
				frappe.utils.escape_html(item.item_name or item.item_code), item.qty))
		if len(items) > 3:
			items_summary.append(f"... and {len(items) - 3} more items")
		items_html = "<br>".join(items_summary)

	# Category/route info
	category_html = ""
	if is_po and doc.ts_purchase_category:
		category_html = f"<tr><td style='padding: 5px; font-weight: bold;'>Category:</td><td style='padding: 5px;'>{frappe.utils.escape_html(doc.ts_purchase_category)}</td></tr>"
	elif not is_po and hasattr(doc, "ts_mr_route") and doc.ts_mr_route:
		category_html = f"<tr><td style='padding: 5px; font-weight: bold;'>Route:</td><td style='padding: 5px;'>{frappe.utils.escape_html(doc.ts_mr_route)}</td></tr>"

	reason_html = ""
	if extra.get("reason"):
		reason_html = f"<p><strong>Reason:</strong> {frappe.utils.escape_html(extra['reason'])}</p>"

	forwarded_html = ""
	if extra.get("forwarded_by"):
		forwarded_html = f"<p>Forwarded by: <strong>{frappe.utils.escape_html(extra['forwarded_by'])}</strong></p>"

	status_value = doc.ts_approval_status if is_po else (doc.ts_mr_status if hasattr(doc, "ts_mr_status") else "")

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
		<p style="color: #888; font-size: 12px;">TS Gate Entry System — Betul Bio Fuel Pvt. Ltd.</p>
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
	logs = frappe.get_all("TS Approval Log",
		filters={"parent": doc.name, "parenttype": doc.doctype},
		fields=["action_by"])
	return set(l.action_by for l in logs if l.action_by)


# ═══════════════════════════════════════════════════════════════════════
#  APPROVAL CONTEXT (for JS)
# ═══════════════════════════════════════════════════════════════════════

def _get_po_approval_context(doc, settings):
	"""Build full approval context for PO form JS."""
	status = doc.ts_approval_status or ""
	current_step = cint(doc.ts_current_step)
	total_steps = cint(doc.ts_total_steps)
	po_amount = flt(doc.grand_total)
	is_pending = status.startswith("Pending") or status.startswith("Awaiting")
	is_self_submitted = (doc.ts_submitted_by and doc.ts_submitted_by == frappe.session.user)
	self_skip_impossible = cint(doc.ts_self_skip_impossible) if hasattr(doc, "ts_self_skip_impossible") else 0
	# Allow self-approval when self-skip was impossible (single-step rule)
	effective_self_block = is_self_submitted and not self_skip_impossible

	ctx = {
		"approval_enabled": True,
		"status": status,
		"purchase_category": doc.ts_purchase_category or "",
		"rule_name": doc.ts_approval_rule or "",
		"current_step": current_step,
		"total_steps": total_steps,
		"po_amount": po_amount,
		"can_submit_for_approval": (doc.docstatus == 0 and status in ("", "Draft", "Not Submitted")),
		"can_review": False,
		"can_approve": False,
		"can_final_approve": False,
		"can_send_to_md": (cint(doc.ts_can_send_to_md) == 1 and not effective_self_block),
		"can_revise": False,
		"can_reject": False,
		"can_resubmit": (status == "Revised" and frappe.session.user in (doc.owner, doc.ts_submitted_by or "", "Administrator")),
		"self_skip_impossible": bool(self_skip_impossible),
		"is_pending": is_pending,
		"approval_chain": [],
	}

	# Build approval chain and determine user permissions from rule (single fetch)
	rule = None
	if doc.ts_approval_rule:
		try:
			rule = frappe.get_doc("TS PO Approval Rule", doc.ts_approval_rule)
			ctx["approval_chain"] = _build_po_approval_chain(doc, rule)
		except Exception:
			frappe.log_error(title="Approval Context Error", message=frappe.get_traceback())

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
			frappe.log_error(title="Approval Context Error", message=frappe.get_traceback())

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
			frappe.log_error(title="Approval Context Error", message=frappe.get_traceback())

	return ctx


def _get_mr_approval_context(doc, settings):
	"""Build approval context for MR form JS."""
	# v2.9.9 — Stores flow branch (Material Transfer + Material Issue):
	# return Transfer/Issue context (different actions, no AVP/CEO/MD chain).
	# Kill-switch gated.
	if doc.material_request_type in ("Material Transfer", "Material Issue") and cint(
		frappe.db.get_single_value("TS Settings", "ts_material_transfer_flow_enabled")
	):
		from trustbit_ethanol.ts_gate_entry.ts_mr_transfer import get_transfer_context
		return get_transfer_context(doc)

	status = doc.ts_mr_status or ""
	is_pending = status.startswith("Pending")
	is_on_hold = status.startswith("On Hold")
	current_step = cint(doc.ts_mr_current_step) if hasattr(doc, "ts_mr_current_step") else 0
	mr_submitted_by = doc.ts_mr_submitted_by if hasattr(doc, "ts_mr_submitted_by") else None
	mr_self_skip_impossible = cint(doc.ts_mr_self_skip_impossible) if hasattr(doc, "ts_mr_self_skip_impossible") else 0
	is_self_submitted = ((is_pending or is_on_hold) and (
		doc.owner == frappe.session.user or
		(mr_submitted_by and mr_submitted_by == frappe.session.user)
	))
	# Allow self-approval when self-skip was impossible (single-step route)
	effective_mr_self_block = is_self_submitted and not mr_self_skip_impossible

	ctx = {
		"approval_enabled": True,
		"status": status,
		"mr_route": doc.ts_mr_route if hasattr(doc, "ts_mr_route") else "",
		"current_step": current_step,
		"total_steps": cint(doc.ts_mr_total_steps) if hasattr(doc, "ts_mr_total_steps") else 0,
		"can_submit_for_approval": (doc.docstatus == 0 and status in ("", "Draft", "Not Submitted")),
		"can_review": False,
		"can_approve": False,
		"can_final_approve": False,
		"can_hold": False,
		"can_resume": False,
		"can_revise": False,
		"can_reject": False,
		"can_resubmit": (status == "Revised" and frappe.session.user in (
			doc.owner, mr_submitted_by or "", "Administrator"
		)),
		"is_pending": is_pending,
		"is_on_hold": is_on_hold,
		"hold_reason": (doc.ts_mr_hold_reason if hasattr(doc, "ts_mr_hold_reason") else "") or "",
		"approval_chain": [],
	}

	# Build chain
	route_name = doc.ts_mr_approval_route if hasattr(doc, "ts_mr_approval_route") else None
	if route_name:
		try:
			route_doc = frappe.get_doc("TS MR Approval Route", route_name)
			ctx["approval_chain"] = _build_mr_approval_chain(doc, route_doc)

			# CC config for user-level validation
			cost_center = _get_mr_cost_center(doc)
			cc_config = get_cc_config(cost_center)

			if is_pending:
				steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
				user_roles = frappe.get_roles(frappe.session.user)
				current_step_obj = next((s for s in steps if s.step_order == current_step), None)

				if current_step_obj:
					can_act = _check_user_can_act(steps, current_step, user_roles, cc_config, effective_mr_self_block)

					if can_act:
						if current_step_obj.action_type == "Review":
							ctx["can_review"] = True
							ctx["can_hold"] = True  # Reviewers can hold
						elif current_step_obj.action_type == "Final Approve":
							ctx["can_final_approve"] = True

						if current_step_obj.can_revise:
							ctx["can_revise"] = True
						if current_step_obj.can_reject:
							ctx["can_reject"] = True

			elif is_on_hold:
				# Only the HOD who held (or higher) can resume
				held_step_order = cint(doc.ts_mr_held_at_step) if hasattr(doc, "ts_mr_held_at_step") else current_step
				steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
				user_roles = frappe.get_roles(frappe.session.user)
				can_act = _check_user_can_act(steps, held_step_order, user_roles, cc_config, effective_mr_self_block)
				if can_act:
					ctx["can_resume"] = True

		except Exception:
			frappe.log_error(title="Approval Context Error", message=frappe.get_traceback())

	return ctx


def _check_user_can_act(steps, current_step_order, user_roles, cc_config, effective_self_block):
	"""Check if current user can act at the given step, considering role + CC config + self-block."""
	if effective_self_block:
		return False

	# Standard role check (>= for higher-level override)
	role_ok = False
	for step in steps:
		if step.step_order >= current_step_order and step.role in user_roles:
			role_ok = True
			break
	if not role_ok:
		return False

	# CC config check
	if cc_config:
		cc_approvers = get_cc_approvers_for_step(cc_config, current_step_order)
		if cc_approvers and frappe.session.user not in cc_approvers:
			# Check higher steps
			for step in steps:
				if step.step_order > current_step_order:
					higher_approvers = get_cc_approvers_for_step(cc_config, step.step_order)
					if frappe.session.user in higher_approvers:
						return True
			return False

	return True


def _build_po_approval_chain(doc, rule):
	"""Build the approval chain for the PO stepper UI."""
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step = cint(doc.ts_current_step)
	status = doc.ts_approval_status or ""

	# Get log entries
	logs = frappe.get_all("TS Approval Log",
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
	current_step = cint(doc.ts_mr_current_step) if hasattr(doc, "ts_mr_current_step") else 0
	status = doc.ts_mr_status or ""

	logs = frappe.get_all("TS Approval Log",
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
		elif step.step_order == current_step and (status.startswith("Pending") or status.startswith("On Hold")):
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
	if doc.ts_approval_status:
		doc.db_set({
			"ts_approval_status": "Rejected",
			"ts_can_send_to_md": 0,
			"ts_last_action": f"Cancelled on {now_datetime().strftime('%d %b %Y %H:%M')}",
		}, update_modified=False)


def mr_on_amend(doc, method=None):
	"""v2.9.12 — Reset approval-tracking fields when MR is amended.

	Mirrors po_on_amend below. Without this hook, amended MR drafts retain
	the parent's ts_mr_status (e.g. "Approved", "Pending CEO") which makes
	`can_submit_for_approval` predicate fail (status not in eligible list)
	and the Submit-for-Approval button stays hidden — user can't progress.

	Resets ts_mr_status to "Not Submitted" + clears tracking fields. Writes
	go through `doc.set()` (in-memory before save), so the reset is part of
	the new amended doc's pre-save state and does not trigger
	_block_gate_field_tampering at validate-time.
	"""
	if doc.doctype != "Material Request":
		return
	if not doc.amended_from:
		return

	doc.ts_mr_status = "Not Submitted"
	for fname in (
		"ts_mr_route",
		"ts_mr_current_step",
		"ts_mr_total_steps",
		"ts_mr_submitted_by",
		"ts_amount_at_submission",
		"ts_last_action",
		"hold_reason",
		"held_by",
		"held_at_step",
	):
		if hasattr(doc, fname):
			doc.set(fname, None)


def po_on_amend(doc, method):
	"""Reset approval fields when PO is amended."""
	if not doc.amended_from:
		return

	doc.ts_approval_status = "Draft"
	doc.ts_approved_by = None
	doc.ts_approved_date = None
	doc.ts_revision_count = 0
	doc.ts_last_action = ""
	doc.ts_submitted_by = None
	doc.ts_revision_reason = ""
	doc.ts_revised_by = ""
	doc.ts_resubmit_mode = ""
	doc.ts_amount_at_submission = 0
	doc.ts_last_sla_alert = None
	# v2.0 fields
	doc.ts_purchase_category = ""
	doc.ts_approval_rule = ""
	doc.ts_current_step = 0
	doc.ts_total_steps = 0
	doc.ts_can_send_to_md = 0
	doc.ts_self_skip_impossible = 0


_PO_GATE_FIELDS = (
	"ts_approval_status",
	"ts_current_step",
	"ts_total_steps",
	"ts_current_level",
	"ts_required_level",
	"ts_self_skip_impossible",
	"ts_can_send_to_md",
	"ts_amount_at_submission",
	"ts_submitted_by",
	"ts_approval_rule",
	"ts_purchase_category",
	"ts_budget_overridden",
)

_MR_GATE_FIELDS = (
	"ts_mr_status",
	"ts_mr_route",
	"ts_mr_approval_route",
	"ts_mr_current_step",
	"ts_mr_total_steps",
	"ts_mr_self_skip_impossible",
	"ts_mr_submitted_by",
	"ts_mr_held_at_step",
)


def _block_gate_field_tampering(doc, gate_fields):
	"""Block REST API mutation of approval control-plane fields.

	Why: Custom Field defaults are permlevel 0, so any user with PO/MR write
	permission could call frappe.client.set_value to flip ts_self_skip_impossible,
	ts_amount_at_submission, ts_approval_status, etc. and bypass the entire
	approval chain. Legitimate controller writes use db_set() which skips save
	hooks entirely — so this guard only fires for unauthorised user mutations.

	v2.9.14.6 — Stores Workflow endpoints (submit_for_stores_approval +
	approve_transfer) need doc.save() to also docstatus-submit (0→1). They set
	doc.flags.ts_approval_workflow_call BEFORE save so this guard allows their
	legitimate ts_mr_status mutation through. doc.flags is per-instance, set
	only inside whitelisted endpoints — not reachable from REST API tamper.
	"""
	if doc.is_new():
		return
	if frappe.session.user == "Administrator":
		return
	if "System Manager" in frappe.get_roles(frappe.session.user):
		return
	if doc.flags.get("ts_approval_workflow_call"):
		return
	# v2.10.0 — Allow legitimate status mutations from the budget-override approval flow.
	# This flag is set ONLY by ts_budget_override.py helpers (server-side, not REST-reachable).
	if frappe.flags.get("in_budget_override_approval"):
		return
	for field in gate_fields:
		if doc.has_value_changed(field):
			frappe.throw(
				_("Field '{0}' is locked by the approval system. "
				  "Use the approval workflow buttons instead.").format(field),
				title=_("Approval Field Locked"),
			)


# ── v2.9.0 Day 3: PO Deduction Terms ──────────────────────────────────────
# Tolerance threshold for "override differs from default" — beyond this, a
# reason MUST be supplied. 5% per Plan v4 Day 3 Step E.
_PO_DEDUCTION_OVERRIDE_TOLERANCE_PCT = 5.0


def _validate_po_deduction_terms(doc):
	"""Validate v2.9.0 PO deduction terms section (Plan v4 Gaps 1, 2 + inactive guard).

	Rules enforced:
	1. If `ts_deduction_template` is empty AND PO has Grain-category items →
	   silent fallback to category default master (Plan v4 Gap 1, Approach B).
	   Sets the field via db_set so it shows on the form after save.
	2. If template is inactive → throw clear error.
	3. For every override row, round default_rate + override_rate to 4 decimals
	   (Plan v4 Gap 2). Round in-place on the row docfield.
	4. If override_rate is set AND differs from default_rate by more than
	   _PO_DEDUCTION_OVERRIDE_TOLERANCE_PCT → require override_reason. (Frappe's
	   `mandatory_depends_on` on the child JSON catches the "any override → reason"
	   case at form level; this server-side check enforces the >5% delta rule
	   that JSON cannot express, and acts as a defense-in-depth guard against
	   REST mutation.)

	Skips entirely if the Custom Fields are not yet seeded (defensive — covers
	the migrate-not-yet-run window).
	"""
	# Defensive: if seed hasn't run yet, the fields don't exist on the doc.
	if not hasattr(doc, "ts_deduction_template"):
		return

	# Rule 1 — Silent fallback to category default for Grain.
	# Only fire on draft (docstatus=0). On submitted/cancelled docs we don't
	# silently mutate — those edits go through the executive-override path.
	if doc.docstatus == 0 and not (doc.ts_deduction_template or "").strip():
		if _po_has_grain_items(doc):
			default_master = frappe.db.get_value(
				"TS Deduction Master",
				{"category": "Grain", "is_default": 1, "is_active": 1},
				"name",
			)
			if default_master:
				doc.ts_deduction_template = default_master
				# Banner / note message — flagged so JS can display "fallback applied".
				doc.flags.ts_deduction_template_fallback = default_master

	# Rule 2 — Inactive template guard.
	if doc.ts_deduction_template:
		template_active = frappe.db.get_value(
			"TS Deduction Master", doc.ts_deduction_template, "is_active"
		)
		if template_active is None:
			frappe.throw(
				_("Deduction Template '{0}' does not exist.").format(
					doc.ts_deduction_template
				)
			)
		if not template_active:
			frappe.throw(
				_("Deduction Template '{0}' is inactive. Pick an active template "
				  "or clear the field.").format(doc.ts_deduction_template)
			)

	# Rules 3 & 4 — round + check override-vs-default delta + enforce reason.
	for row in (doc.get("ts_deduction_overrides") or []):
		# Round to 4 decimals in place (Plan v4 Gap 2).
		row.default_rate = _round4(row.default_rate)
		row.override_rate = _round4(row.override_rate)

		default_rate = flt(row.default_rate)
		override_rate = flt(row.override_rate)

		# Only enforce the >5% reason rule when an override is actually present
		# AND a default exists to compare against.
		if not override_rate:
			continue
		if not default_rate:
			# No master rate to compare — JSON `mandatory_depends_on` already
			# made reason required; skip the delta check.
			continue

		delta_pct = abs(override_rate - default_rate) / default_rate * 100.0
		if delta_pct > _PO_DEDUCTION_OVERRIDE_TOLERANCE_PCT:
			if not (row.override_reason or "").strip():
				frappe.throw(
					_("Deduction row '{0}': override rate ({1}) differs from "
					  "master rate ({2}) by {3:.1f}% (>{4:.0f}%). Please provide "
					  "an override reason.").format(
						row.deduction_code or row.deduction_label or f"#{row.idx}",
						override_rate, default_rate, delta_pct,
						_PO_DEDUCTION_OVERRIDE_TOLERANCE_PCT,
					),
					title=_("Override Reason Required"),
				)


def _po_has_grain_items(doc) -> bool:
	"""True if at least one item on the PO maps to category=Grain.

	Mapping: Item → Item Group → ts_purchase_category (custom link). Falls back
	to checking item_group name contains 'grain' (case-insensitive) only if the
	custom category link is missing on Item Group. Defense-in-depth — never
	throws on missing meta.
	"""
	for item in (doc.get("items") or []):
		if not item.item_code:
			continue
		try:
			ig = frappe.db.get_value("Item", item.item_code, "item_group")
			if not ig:
				continue
			# Try the custom purchase category link first.
			category = frappe.db.get_value("Item Group", ig, "ts_purchase_category")
			if category and frappe.db.get_value(
				"TS Purchase Category", category, "category_name"
			) == "Grain":
				return True
			# Fallback heuristic — item group name contains 'grain'.
			if "grain" in (ig or "").lower():
				return True
		except Exception:
			continue
	return False


def _round4(val) -> float:
	"""Round to 4 decimals (Plan v4 Gap 2). Returns 0.0 if val is None / non-numeric."""
	try:
		return round(float(val or 0), 4)
	except (TypeError, ValueError):
		return 0.0
# ───────────────────────────────────────────────────────────────────────────


def po_before_save(doc, method):
	"""Prevent amount changes while PO is in approval chain + block duplicate PO from MR + copy project from MR."""
	# ── Duplicate PO from MR block ──
	if doc.is_new():
		_check_duplicate_po_from_mr(doc)

	# ── Copy project from MR to PO ──
	if not doc.project:
		_copy_project_from_mr(doc)

	# ── v2.9.8.37: Mirror ts_project → native project for ERPNext mapper compat ──
	# Native project field is hidden in form (v2.9.8.37); BBPL downstream reads
	# ts_project, but ERPNext PR/PI mappers + project-based reports still need
	# the native `project` column populated. Sync silently on every save.
	if doc.get("ts_project") and doc.project != doc.ts_project:
		doc.project = doc.ts_project

	# ── v2.9.0 Day 3: PO Deduction Terms validation (Plan v4 Gaps 1, 2, inactive guard) ──
	# Runs BEFORE tamper guard so even CEO/MD edits to template/overrides get
	# the rounding + reason-required + inactive-template checks. The new fields
	# are NOT control-plane (no permlevel=1 on parent Custom Field), so the
	# tamper guard does not need them in _PO_GATE_FIELDS.
	_validate_po_deduction_terms(doc)

	# ── Gate-field tamper guard (Security #14) ──
	# CEO + MD do NOT bypass this — control-plane fields stay protected.
	_block_gate_field_tampering(doc, _PO_GATE_FIELDS)

	# ── Executive (CEO + MD) override (v2.8.12) ──
	# Allow full business-field edit on Draft + in-approval docs. Capture
	# the changed-field set now; audit log + email are written in
	# po_on_update after the save has committed.
	if not doc.is_new() and doc.docstatus == 0 and is_executive_override_user():
		changes = collect_business_changes(doc)
		if changes:
			doc.flags.executive_override_changes = changes
		return  # skip amount guard for CEO/MD

	if not doc.ts_approval_status:
		return

	status = doc.ts_approval_status
	if status.startswith("Pending") or status.startswith("Awaiting"):
		old_amount = flt(doc.ts_amount_at_submission)
		new_amount = flt(doc.grand_total)
		if old_amount and old_amount != new_amount:
			frappe.throw(
				_("Cannot change PO amount while it is in the approval chain. "
				  "The approver must revise the PO first.")
			)


def po_on_update(doc, method):
	"""v2.8.12 — write executive override audit log + email after successful save.

	Wired as Frappe `on_update` doc_event (fires after both Insert and Update
	saves commit). If amount changed during override + PO is mid-chain, sync
	ts_amount_at_submission so subsequent approvers don't get blocked by
	_validate_amount_unchanged. db_set writes bypass the tamper guard
	(same pattern as legitimate controller writes).
	"""
	changes = getattr(doc.flags, "executive_override_changes", None)
	if not changes:
		return

	# Keep the approval chain silent: if amount changed, update the
	# baseline so the next approver's _validate_amount_unchanged passes.
	if "grand_total" in changes or "items" in changes or "taxes" in changes:
		if doc.ts_approval_status and doc.ts_approval_status.startswith("Pending"):
			new_amount = flt(doc.grand_total)
			if new_amount and flt(doc.ts_amount_at_submission) != new_amount:
				doc.db_set("ts_amount_at_submission", new_amount, update_modified=False)

	log_executive_edit(doc, changes)
	notify_executive_edit(doc, changes)


def _check_duplicate_po_from_mr(doc):
	"""Previously blocked duplicate POs from same MR. Removed because ERPNext
	natively supports multiple POs from one MR (partial orders, split suppliers).
	ERPNext tracks per_ordered on MR items to prevent over-ordering."""
	pass


def _copy_project_from_mr(doc):
	"""Auto-copy project + is_new_project from linked MR to PO when PO is created from MR."""
	for item in (doc.get("items") or []):
		if item.material_request:
			mr_data = frappe.db.get_value("Material Request", item.material_request,
				["ts_project", "ts_is_new_project"], as_dict=True)
			if mr_data:
				if mr_data.ts_project:
					doc.project = mr_data.ts_project
				if mr_data.ts_is_new_project:
					doc.ts_is_new_project = mr_data.ts_is_new_project
				return


def mr_before_submit_block_direct(doc, method=None):
	"""v2.9.17.9 — Server-side guard against bypassing the MR approval chain.

	Why: Stock User role has submit=1 on Material Request. Without this guard
	any user (or REST/API call) can submit a draft MR directly via native
	Submit / Ctrl+Shift+Enter / frappe.client.submit — bypassing the
	"Submit for Approval" workflow entirely. Three prod MRs (PR-CF-PRD-26-00008,
	00008-1, 00011) were submitted this way: docstatus=1 with ts_mr_status=
	'Not Submitted' and ts_mr_route=NULL, stranded outside any approval flow.

	State-based guard:
	- Material Transfer + Material Issue: bypass (they have their own Stores
	  Workflow at ts_mr_transfer.handle_before_save and explicit submit endpoints).
	- ts_mr_route set AND ts_mr_status='Approved': legitimate _approve_mr Final
	  Approve path — sets the route + flips status to 'Approved' via db_set
	  BEFORE doc.submit(), so this guard sees the legit state and passes.
	- Else: throw with bilingual guidance pointing the user at the
	  'Submit for Approval' button.
	"""
	if not doc:
		return
	# Frappe `before_submit` fires AFTER docstatus is set to 1 in-memory but BEFORE the DB write.
	# So at this point doc.docstatus == 1 (not 0). We allow this hook to act on every submit
	# attempt; the state-based gate below decides legit vs. bypass.
	if (doc.material_request_type or "") in ("Material Transfer", "Material Issue"):
		return  # Stores Workflow has its own submit chain
	route = (doc.get("ts_mr_route") or doc.get("ts_mr_approval_route") or "").strip()
	status = (doc.get("ts_mr_status") or "").strip()
	if route and status == "Approved":
		return  # _approve_mr Final Approve path — set state first then submit
	frappe.throw(
		_(
			"Direct submission of Material Requests is not allowed. "
			"Please use the <b>Submit for Approval</b> button so this MR "
			"enters the proper approval chain. (Status: {0}, Route: {1})"
		).format(status or "—", route or "—"),
		title=_("Use Submit for Approval"),
	)


def mr_before_save(doc, method):
	"""Prevent changes while MR is in approval chain."""
	# ── Gate-field tamper guard (Security #14) ──
	# CEO + MD do NOT bypass this — control-plane fields stay protected.
	_block_gate_field_tampering(doc, _MR_GATE_FIELDS)

	# ── v2.9.9: Stores flow type-branch (Material Transfer + Material Issue) ──
	# Skip ALL Purchase chain logic (cost_center auto-fill, exec override,
	# items-lock-by-status, ts_mr_log writes) for these types.
	# Tamper guard above STILL applies. Kill switch in TS Settings flips
	# this back to legacy behavior if needed (rollback path).
	if doc.material_request_type in ("Material Transfer", "Material Issue"):
		from trustbit_ethanol.ts_gate_entry.ts_mr_transfer import (
			_is_flow_enabled, handle_before_save,
		)
		if _is_flow_enabled():
			handle_before_save(doc)
			return  # SKIP all Purchase logic below

	# ── v2.9.0.6 (Bug 11.F): defensive CC fill ──
	# Frappe auto-fills item rows' cost_center from Item.default_cost_center
	# (often "Main - BBPL"), causing User Permission errors for users
	# restricted to specific CCs. JS handles the proactive fix; this is the
	# server-side safety net for API-driven creates / imports / programmatic
	# saves that bypass the JS layer.
	if doc.cost_center:
		for row in (doc.items or []):
			if not row.cost_center or row.cost_center != doc.cost_center:
				row.cost_center = doc.cost_center

	# ── Executive (CEO + MD) override (v2.8.12) ──
	if not doc.is_new() and doc.docstatus == 0 and is_executive_override_user():
		changes = collect_business_changes(doc)
		if changes:
			doc.flags.executive_override_changes = changes
		return  # skip items-in-chain guard for CEO/MD

	if not hasattr(doc, "ts_mr_status") or not doc.ts_mr_status:
		return

	status = doc.ts_mr_status
	if status.startswith("Pending") or status.startswith("On Hold") or status in ("Rejected",):
		if not doc.is_new() and doc.has_value_changed("items"):
			frappe.throw(
				_("Cannot modify MR items while it is in the approval chain (status: {0}). "
				  "The approver must revise the MR first.").format(status)
			)


def mr_on_update(doc, method):
	"""v2.8.12 — write executive override audit log + email after successful save.

	Wired as Frappe `on_update` doc_event (fires after both Insert and Update).
	"""
	changes = getattr(doc.flags, "executive_override_changes", None)
	if changes:
		log_executive_edit(doc, changes)
		notify_executive_edit(doc, changes)


# ═══════════════════════════════════════════════════════════════════════
#  SLA CHECKER
# ═══════════════════════════════════════════════════════════════════════

def check_approval_sla():
	"""Find POs stuck at any approval step beyond SLA hours."""
	settings = frappe.get_single("TS Settings")
	if not settings.approval_sla_hours:
		return

	sla_hours = cint(settings.approval_sla_hours)
	threshold = add_to_date(now_datetime(), hours=-1 * sla_hours)

	stuck_pos = frappe.get_all("Purchase Order",
		filters={
			"ts_approval_status": ["like", "Pending%"],
			"modified": ["<", threshold],
			"docstatus": 0,
		},
		fields=["name", "ts_approval_status", "grand_total", "modified",
				"ts_current_step", "ts_approval_rule", "ts_last_sla_alert"])

	if not stuck_pos:
		return

	for po_data in stuck_pos:
		# Lock the row to prevent duplicate SLA alerts from concurrent scheduler workers
		frappe.db.sql("SELECT name FROM `tabPurchase Order` WHERE name = %s FOR UPDATE", po_data.name)

		if po_data.ts_last_sla_alert:
			last_alert_hours = time_diff_in_hours(now_datetime(), po_data.ts_last_sla_alert)
			if last_alert_hours < sla_hours:
				continue

		hours_stuck = round(time_diff_in_hours(now_datetime(), po_data.modified), 1)

		frappe.db.set_value("Purchase Order", po_data.name,
			"ts_last_sla_alert", now_datetime(), update_modified=False)

		if settings.approval_sla_email:
			amount_str = frappe.format_value(flt(po_data.grand_total), {"fieldtype": "Currency"})
			esc = frappe.utils.escape_html
			frappe.sendmail(
				recipients=[settings.approval_sla_email],
				subject=f"[CTL Alert] PO {po_data.name} stuck at {po_data.ts_approval_status} for {hours_stuck}h",
				message=f"""
				<p>Purchase Order <strong>{esc(po_data.name)}</strong> ({esc(amount_str)}) has been stuck
				at <strong>{esc(po_data.ts_approval_status)}</strong> for <strong>{hours_stuck} hours</strong>,
				exceeding the {sla_hours}-hour CTL (Committed Time Limit).</p>
				<p><a href="{frappe.utils.get_url()}/app/purchase-order/{esc(po_data.name)}">View PO</a></p>
				""",
				now=True,
			)

		if settings.approval_escalation_enabled:
			_auto_escalate(po_data)

	# Also check stuck Material Requests
	stuck_mrs = frappe.get_all("Material Request",
		filters={
			"ts_mr_status": ["like", "Pending%"],
			"modified": ["<", threshold],
			"docstatus": 0,
		},
		fields=["name", "ts_mr_status", "modified", "ts_mr_last_sla_alert"])

	for mr_data in stuck_mrs:
		frappe.db.sql("SELECT name FROM `tabMaterial Request` WHERE name = %s FOR UPDATE", mr_data.name)

		if mr_data.ts_mr_last_sla_alert:
			last_alert_hours = time_diff_in_hours(now_datetime(), mr_data.ts_mr_last_sla_alert)
			if last_alert_hours < sla_hours:
				continue

		hours_stuck = round(time_diff_in_hours(now_datetime(), mr_data.modified), 1)

		frappe.db.set_value("Material Request", mr_data.name,
			"ts_mr_last_sla_alert", now_datetime(), update_modified=False)

		if settings.approval_sla_email:
			esc = frappe.utils.escape_html
			frappe.sendmail(
				recipients=[settings.approval_sla_email],
				subject=f"[CTL Alert] MR {mr_data.name} stuck at {mr_data.ts_mr_status} for {hours_stuck}h",
				message=f"""
				<p>Material Request <strong>{esc(mr_data.name)}</strong> has been stuck
				at <strong>{esc(mr_data.ts_mr_status)}</strong> for <strong>{hours_stuck} hours</strong>,
				exceeding the {sla_hours}-hour CTL (Committed Time Limit).</p>
				<p><a href="{frappe.utils.get_url()}/app/material-request/{esc(mr_data.name)}">View MR</a></p>
				""",
				now=True,
			)

	frappe.db.commit()


def _auto_escalate(po_data):
	"""Auto-forward a stuck PO to the next approval step."""
	if not po_data.ts_approval_rule:
		return

	doc = frappe.get_doc("Purchase Order", po_data.name, for_update=True)
	rule = frappe.get_doc("TS PO Approval Rule", po_data.ts_approval_rule)
	steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
	current_step_order = cint(po_data.ts_current_step)

	next_step = _get_next_step(steps, current_step_order)
	if not next_step or next_step.is_manual_trigger:
		return  # Don't auto-escalate to manual-trigger steps (MD)

	next_label = next_step.role_label or next_step.role
	current_state = doc.ts_approval_status
	next_state = f"Pending {next_label}"

	_log_approval_action(doc, "Forwarded", current_state, next_state,
		comment=f"Auto-escalated due to CTL breach",
		po_amount=flt(doc.grand_total),
		step_order=next_step.step_order, purchase_category=doc.ts_purchase_category)

	doc.db_set({
		"ts_approval_status": next_state,
		"ts_current_step": next_step.step_order,
		"ts_last_action": f"Auto-escalated to {next_label} (CTL breach) on {now_datetime().strftime('%d %b %Y %H:%M')}",
	}, update_modified=True)

	_send_approval_notification(doc, "pending_approval",
		_get_role_users(next_step.role),
		extra={"next_role": next_label, "escalated": True})


# ═══════════════════════════════════════════════════════════════════════
#  v2.10.0 — Budget override resume helper
# ═══════════════════════════════════════════════════════════════════════


def _assign_route_and_first_step(doc):
	"""Route selection + step-1 status assignment WITHOUT breach detection.

	Called by ts_budget_override._resume_normal_route after CEO approves the override.
	Caller MUST set frappe.flags.in_budget_override_approval = True so the gate-field
	tamper guard allows the status mutation and the breach detector inside
	_submit_*_for_approval is bypassed (defensive — we don't call _submit_* here).
	"""
	originator = (
		(doc.get("ts_mr_submitted_by") if doc.doctype == "Material Request" else doc.get("ts_submitted_by"))
		or doc.owner
	)

	if doc.doctype == "Purchase Order":
		category = _resolve_po_category(doc)
		rule_name = _find_po_approval_rule(category, flt(doc.grand_total))
		rule = frappe.get_doc("TS PO Approval Rule", rule_name)
		steps = sorted(rule.approval_steps, key=lambda s: s.step_order)
		target = _get_target_step_with_skip(steps, originator)
		next_state = f"Pending {target.role_label or target.role}"
		self_skip_impossible = (
			target == steps[0]
			and target.role in frappe.get_roles(originator)
			and len(steps) == 1
		)
		doc.db_set({
			"ts_approval_status": next_state,
			"ts_purchase_category": category,
			"ts_approval_rule": rule_name,
			"ts_current_step": target.step_order,
			"ts_total_steps": len(steps),
			"ts_can_send_to_md": 0,
			"ts_self_skip_impossible": 1 if self_skip_impossible else 0,
			"ts_amount_at_submission": flt(doc.grand_total),
			"ts_last_action": f"Resumed after budget override on {now_datetime().strftime('%d %b %Y %H:%M')}",
		}, update_modified=True)
		_log_approval_action(
			doc,
			"Resumed after Budget Override",  # v2.10.0.2 — TS Approval Log.action Select now includes this value
			"Pending Budget Override",
			next_state,
			po_amount=flt(doc.grand_total),
			step_order=target.step_order,
			purchase_category=category,
		)
		_send_approval_notification(
			doc,
			"pending_approval",
			_get_role_users(target.role),
			extra={"next_role": target.role_label or target.role, "resumed_from_budget_override": True},
		)
		return next_state

	if doc.doctype == "Material Request":
		cost_center = _get_mr_cost_center(doc)
		route_name = _find_mr_route(cost_center)
		if not route_name:
			frappe.throw(
				_("No MR approval route configured for Cost Center: {0}").format(cost_center)
			)
		route_doc = frappe.get_doc("TS MR Approval Route", route_name)
		steps = sorted(route_doc.approval_steps, key=lambda s: s.step_order)
		target = _get_target_step_for_mr(steps, originator, cost_center)
		next_state = f"Pending {target.role_label or target.role}"
		self_skip_impossible = (
			target == steps[0]
			and target.role in frappe.get_roles(originator)
			and len(steps) == 1
		)
		doc.db_set({
			"ts_mr_status": next_state,
			"ts_mr_route": route_name,
			"ts_mr_approval_route": route_name,
			"ts_mr_current_step": target.step_order,
			"ts_mr_total_steps": len(steps),
			"ts_mr_self_skip_impossible": 1 if self_skip_impossible else 0,
		}, update_modified=True)
		_log_mr_action(
			doc,
			"Resumed after Budget Override",  # v2.10.0.2 — TS Approval Log.action Select now includes this value
			"Pending Budget Override",
			next_state,
			step_order=target.step_order,
			purchase_category=route_name,
		)
		cc_config = get_cc_config(cost_center)
		recipients = []
		if cc_config:
			recipients = get_cc_users_for_step(cc_config, target.step_order)
		if not recipients:
			recipients = _get_role_users(target.role)
		_send_approval_notification(
			doc,
			"mr_pending",
			recipients,
			extra={"next_role": target.role_label or target.role, "resumed_from_budget_override": True},
		)
		return next_state

	frappe.throw(
		_("Cannot resume route for {0} — only Material Request and Purchase Order are supported.").format(
			doc.doctype
		)
	)
