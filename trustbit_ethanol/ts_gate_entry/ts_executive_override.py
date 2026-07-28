# v2.8.12 — Executive (CEO + MD) Edit Override
# Allows CEO/MD to edit MR/PO business fields at any Draft or in-approval stage
# without triggering re-routing. Control-plane fields remain protected by
# _block_gate_field_tampering() — this module does NOT bypass that guard.
#
# Design:
#  - is_executive_override_user(): role check helper (CEO or MD)
#  - _collect_business_changes(): diff non-control-plane fields on save
#  - log_executive_edit(): append immutable TS Approval Log row (silent edit)
#  - notify_executive_edit(): enqueue email to owner + current-step approver
#
# Called from:
#  - po_before_save / mr_before_save (skip amount/items block for CEO+MD)
#  - po_on_update / mr_on_update (write audit log + email on confirmed save)

import frappe
from frappe import _
from frappe.utils import now_datetime, get_fullname, get_url_to_form, escape_html

EXECUTIVE_OVERRIDE_ROLES = ("CEO", "MD")

# Fields that are approval control-plane — never considered "business edits",
# always protected by _block_gate_field_tampering(). Listed here so override
# diff excludes them from audit log / notifications.
CONTROL_PLANE_FIELDS = {
	# PO
	"ts_approval_status", "ts_current_step", "ts_total_steps",
	"ts_current_level", "ts_required_level",
	"ts_self_skip_impossible", "ts_can_send_to_md",
	"ts_amount_at_submission", "ts_submitted_by",
	"ts_approval_rule", "ts_purchase_category", "ts_budget_overridden",
	"ts_approved_by", "ts_approved_date", "ts_revision_count",
	"ts_last_action", "ts_last_sla_alert", "ts_resubmit_mode",
	# v2.28 Executive Hold — control-plane, never a "business edit"
	"ts_po_on_hold", "ts_po_hold_reason", "ts_po_held_by",
	# MR
	"ts_mr_status", "ts_mr_route", "ts_mr_approval_route",
	"ts_mr_current_step", "ts_mr_total_steps",
	"ts_mr_self_skip_impossible", "ts_mr_submitted_by",
	"ts_mr_held_at_step", "ts_mr_hold_reason",
	"ts_mr_approved_by", "ts_mr_approved_date",
	# Framework bookkeeping
	"modified", "modified_by", "_user_tags", "_comments",
	"_assign", "_liked_by", "docstatus", "idx",
}


def is_executive_override_user(user=None):
	"""Return True when user has CEO or MD role.

	Administrator and System Manager fall through to existing admin
	bypass paths — NOT override path — so their audit signal is separate.

	Returns False during migrate/patch/install so controller-initiated
	writes (Lesson 176) don't get misclassified as CEO edits.
	"""
	flags = getattr(frappe, "flags", None)
	if flags and (flags.get("in_migrate") or flags.get("in_patch") or flags.get("in_install")):
		return False
	user = user or frappe.session.user
	if user in ("Administrator", "Guest"):
		return False
	roles = set(frappe.get_roles(user))
	return bool(roles & set(EXECUTIVE_OVERRIDE_ROLES))


def get_override_role_label(user=None):
	"""Return 'CEO', 'MD', or 'CEO + MD' string for audit logging."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	matches = [r for r in EXECUTIVE_OVERRIDE_ROLES if r in roles]
	return " + ".join(matches) if matches else ""


def collect_business_changes(doc):
	"""Diff non-control-plane fields that changed on this save.

	Returns set of field names with changed values. Child-table changes
	(items, taxes, etc.) are captured by Frappe's has_value_changed as a
	single table-level flag — we list the child-table fieldname.
	"""
	changed = set()
	if not doc.meta:
		return changed
	for df in doc.meta.fields:
		fn = df.fieldname
		if not fn or fn in CONTROL_PLANE_FIELDS:
			continue
		if df.fieldtype in ("Section Break", "Column Break", "Tab Break", "HTML", "Button"):
			continue
		try:
			if doc.has_value_changed(fn):
				changed.add(fn)
		except Exception:
			# has_value_changed may raise on fields without a DB backing;
			# treat as unchanged and keep going.
			continue
	return changed


def log_executive_edit(doc, changed_fields, user=None):
	"""Append a TS Approval Log row recording the executive override edit.

	Idempotent per-save: called once from *_after_save with the captured
	flag set. State (from_state = to_state) because override is silent.
	"""
	if not changed_fields:
		return
	user = user or frappe.session.user
	role_label = get_override_role_label(user) or "Executive"
	parentfield = "ts_approval_log" if doc.doctype == "Purchase Order" else "ts_mr_log"
	current_status = (
		doc.get("ts_approval_status")
		or doc.get("ts_mr_status")
		or "Draft"
	)

	# Truncate field list to fit 140-char Data column without silently
	# dropping information.
	sorted_fields = sorted(changed_fields)
	shown = sorted_fields[:20]
	overflow = len(sorted_fields) - len(shown)
	field_list = ", ".join(shown)
	if overflow > 0:
		field_list += f" (+{overflow} more)"
	comment = f"[EXECUTIVE OVERRIDE] Edited: {field_list}"
	if len(comment) > 140:
		comment = comment[:137] + "..."

	try:
		log = frappe.get_doc({
			"doctype": "TS Approval Log",
			"parent": doc.name,
			"parenttype": doc.doctype,
			"parentfield": parentfield,
			"action": "Edited (Executive Override)",
			"from_state": current_status,
			"to_state": current_status,
			"action_by": user,
			"action_by_name": get_fullname(user) or user,
			"action_by_role": role_label,
			"action_date": now_datetime(),
			"comment": comment,
		})
		# justified: audit log is write-once by design; CEO/MD don't hold
		# Write perm on TS Approval Log directly. Same pattern as
		# _log_approval_action() in ts_po_approval.py.
		log.insert(ignore_permissions=True)
	except Exception as e:
		# Log failure must never break the user's save.
		frappe.log_error(
			title="ts_executive_override: audit log insert failed",
			message=(
				f"{doc.doctype}/{doc.name}\n"
				f"User: {user}\n"
				f"Fields: {sorted_fields}\n"
				f"Error: {e}"
			),
		)


def notify_executive_edit(doc, changed_fields, user=None):
	"""Enqueue email to owner + original submitter + current-step approver.

	Editor themselves always excluded. Prior approvers NOT included
	(scope Q3=C: silent, no re-route — only parties with active stake
	in the doc are notified).
	"""
	if not changed_fields:
		return
	user = user or frappe.session.user
	recipients = _collect_override_recipients(doc, exclude=user)
	if not recipients:
		return
	try:
		frappe.enqueue(
			"trustbit_ethanol.ts_gate_entry.ts_executive_override._send_override_email",
			queue="short",
			timeout=60,
			doctype=doc.doctype,
			docname=doc.name,
			edited_by=user,
			edited_by_role=get_override_role_label(user) or "Executive",
			changed_fields=sorted(changed_fields),
			recipients=recipients,
		)
	except Exception as e:
		frappe.log_error(
			title="ts_executive_override: notification enqueue failed",
			message=f"{doc.doctype}/{doc.name}\nError: {e}",
		)


def _collect_override_recipients(doc, exclude=None):
	"""Owner + original submitter + current-step approvers. Deduped.

	Filters out Guest / Administrator / disabled users and the editor.
	"""
	candidates = set()

	owner = doc.get("owner")
	if owner and owner not in ("Administrator", "Guest"):
		candidates.add(owner)

	submitted_by = doc.get("ts_submitted_by") or doc.get("ts_mr_submitted_by")
	if submitted_by and submitted_by not in ("Administrator", "Guest"):
		candidates.add(submitted_by)

	# Current-step approver — derived via CC Approval Config + rule steps.
	try:
		approvers = _get_current_step_approver_users(doc)
		for a in approvers or []:
			if a and a not in ("Administrator", "Guest"):
				candidates.add(a)
	except Exception:
		# Best-effort: even if approver lookup fails, owner + submitter still notified.
		pass

	if exclude:
		candidates.discard(exclude)

	# Filter out disabled users
	if not candidates:
		return []
	enabled = frappe.get_all(
		"User",
		filters={"name": ["in", list(candidates)], "enabled": 1},
		pluck="name",
	)
	return enabled


def _get_current_step_approver_users(doc):
	"""Look up users for the step matching the doc's current approval status."""
	from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
		get_cc_config, get_cc_users_for_step,
	)

	cost_center = doc.get("cost_center")
	if not cost_center:
		# MR: try child-table cost_center if header absent
		for item in (doc.get("items") or []):
			if item.get("cost_center"):
				cost_center = item.cost_center
				break
	if not cost_center:
		return []

	cc_config = get_cc_config(cost_center)
	if not cc_config:
		return []

	current_step = doc.get("ts_current_step") or doc.get("ts_mr_current_step")
	if not current_step:
		return []

	return get_cc_users_for_step(cc_config, int(current_step)) or []


def _send_override_email(doctype, docname, edited_by, edited_by_role, changed_fields, recipients):
	"""Background-enqueued email sender. HTML-escaped, uses internal URL."""
	if not recipients:
		return

	doc_url = get_url_to_form(doctype, docname)
	editor_name = get_fullname(edited_by) or edited_by
	field_list_html = "<ul>" + "".join(
		f"<li><code>{escape_html(f)}</code></li>" for f in changed_fields
	) + "</ul>"

	# Bilingual EN + HI body (matches v2.8.10 / v2.8.11 UX pattern)
	subject = f"[Executive Override] {doctype} {docname} edited by {edited_by_role}"
	body = f"""
	<div style="font-family: Arial, sans-serif; font-size: 13px; line-height: 1.6;">
	  <p><b>{escape_html(editor_name)}</b> ({escape_html(edited_by_role)})
	     edited {escape_html(doctype)}
	     <a href="{escape_html(doc_url)}"><b>{escape_html(docname)}</b></a>.</p>
	  <p><b>Fields changed:</b></p>
	  {field_list_html}
	  <p style="padding: 10px 12px; background: #f0f9ff; border-left: 3px solid #1d4ed8; border-radius: 4px;">
	    <b>No action required</b> — the original approval chain is preserved.
	    This edit was recorded in the document's approval log for audit.
	  </p>
	  <hr style="border: none; border-top: 1px dashed #cbd5e1; margin: 16px 0;"/>
	  <div style="color: #475569; font-size: 12px;">
	    <p><b>{escape_html(editor_name)}</b> ({escape_html(edited_by_role)}) ने
	       {escape_html(doctype)}
	       <a href="{escape_html(doc_url)}"><b>{escape_html(docname)}</b></a>
	       में संशोधन किया है।</p>
	    <p>यह संशोधन दस्तावेज़ के अनुमोदन लॉग में दर्ज किया गया है।
	       <b>कोई कार्रवाई आवश्यक नहीं है</b> — मूल अनुमोदन श्रृंखला पूर्ववत है।</p>
	  </div>
	</div>
	"""

	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=body,
			reference_doctype=doctype,
			reference_name=docname,
			now=False,
		)
	except Exception as e:
		frappe.log_error(
			title="ts_executive_override: sendmail failed",
			message=(
				f"{doctype}/{docname}\n"
				f"Recipients: {recipients}\n"
				f"Error: {e}"
			),
		)
