"""Post-approval Revision Request for Material Request — SOFT REVISE (Path 3).

Allows Purchase User / Purchase Manager / IT Head (plus existing senior roles)
to request a revision on an already-APPROVED (docstatus=1) MR.

Path 3 = notification-only:
  - MR state is NOT changed. docstatus stays 1. (Since v2.30.2 the cosmetic
    ts_mr_revision_requested Check is stamped for list pill + filter visibility.)
  - An audit log row is inserted in the TS Approval Log child table.
  - A visible timeline Comment is added.
  - A Notification Log (bell) + email is sent to the MR creator.
  - Creator then manually cancels + amends the MR in the standard Frappe flow.

Guards:
  - Role must be one of: Purchase User, Purchase Manager, IT Head, AVP, CEO, MD, System Manager, Administrator.
  - MR must have docstatus = 1 (submitted).
  - Reason is mandatory (non-empty, max 2000 chars).
  - PO linkage check: if any item has per_ordered > 0 or any PO Item references
    this MR, the request is BLOCKED with a clear error listing the linked POs.
    (The creator can't cancel the MR while POs exist, so the notification
    would be useless.)

This module is additive — it does NOT modify any locked MR approval code.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, flt, get_fullname

ALLOWED_ROLES = {
	"Purchase User", "Purchase Manager", "IT Head",
	"AVP", "CEO", "MD",
	"System Manager", "Administrator",
}

MAX_REASON_LEN = 2000


def _require_post():
	"""v2.31.0 — L366: methods=["POST"] is enforced on the TOP-LEVEL cmd only;
	via core's bare-whitelist map_docs trampoline this 2-arg mutation stays
	GET-reachable with CSRF skipped (and it commits explicitly, so GET
	auto-rollback does not save it). Re-assert in-body (request is None for
	console/jobs/tests, which stay allowed). Precedent: ts_sr_to_po."""
	_req = getattr(frappe.local, "request", None)
	if _req is not None and _req.method != "POST":
		raise frappe.PermissionError


@frappe.whitelist(methods=["POST"])
def request_mr_revision(mr_name, reason):
	"""Soft-revise a post-approval Material Request.

	Does NOT change docstatus or ts_mr_status. Creates audit trail +
	notifies the original creator to cancel + amend manually. Only the
	cosmetic ts_mr_revision_requested Check is stamped (v2.30.2) so the
	list pill + standard filter can surface the open request.
	"""
	_require_post()  # L366 — trampoline-reachable
	# 1. Role guard
	user = frappe.session.user
	user_roles = set(frappe.get_roles(user))
	if not (ALLOWED_ROLES & user_roles):
		frappe.throw(
			_("Only Purchase User, Purchase Manager, or IT Head can request post-approval revision."),
			frappe.PermissionError,
		)

	# 2. Input validation
	if not mr_name:
		frappe.throw(_("MR name is required."))
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Reason is mandatory."))
	if len(reason) > MAX_REASON_LEN:
		frappe.throw(_("Reason is too long (max {0} characters).").format(MAX_REASON_LEN))

	# 3. Load MR and validate state
	if not frappe.db.exists("Material Request", mr_name):
		frappe.throw(_("Material Request not found: {0}").format(mr_name))

	mr = frappe.get_doc("Material Request", mr_name)
	if mr.docstatus != 1:
		frappe.throw(
			_("Revision request only allowed on submitted (approved) Material Requests. "
			  "Current docstatus: {0}").format(mr.docstatus)
		)

	# 4. PO linkage check — block if any POs already reference this MR
	linked_pos = _get_linked_pos(mr_name)
	if linked_pos:
		po_list = ", ".join(linked_pos[:5])
		more = f" (+ {len(linked_pos) - 5} more)" if len(linked_pos) > 5 else ""
		frappe.throw(
			_("Cannot request revision — {0} Purchase Order(s) already created from this MR: {1}{2}. "
			  "Cancel or amend those POs first, then request revision.").format(
				len(linked_pos), po_list, more
			)
		)

	# 5. Also block if per_ordered is non-zero on any item (defensive)
	for item in mr.items or []:
		if flt(item.get("ordered_qty")) > 0:
			frappe.throw(
				_("Cannot request revision — MR item '{0}' already has ordered_qty = {1}. "
				  "A PO was likely created from this MR.").format(
					item.get("item_code") or item.get("item_name") or "?",
					item.get("ordered_qty")
				)
			)

	# 6. Insert audit log row (TS Approval Log child table)
	_insert_approval_log_entry(mr, user, user_roles, reason)

	# 6b. Stamp the display flag — drives the list pill + standard filter only;
	# approval state (docstatus / ts_mr_status) stays untouched. db_set skips
	# save hooks, so no tamper guard fires (same pattern as hold_mr/resume_mr).
	mr.db_set("ts_mr_revision_requested", 1, update_modified=True)

	# 7. Add visible timeline comment on the MR
	user_name = get_fullname(user) or user
	user_role_str = _primary_role_label(user_roles)
	comment_html = _(
		"📝 <b>Revision Request</b> by {0} ({1})<br>"
		"<b>Reason:</b> {2}<br>"
		"<br>"
		"<i>Action required: the original creator must cancel this MR and amend it with the requested changes. "
		"This is a notification-only request — the MR state is unchanged.</i>"
	).format(
		frappe.utils.escape_html(user_name),
		frappe.utils.escape_html(user_role_str),
		frappe.utils.escape_html(reason),
	)
	mr.add_comment("Comment", text=comment_html)

	# 8. Notify the MR creator via Notification Log (bell) + email
	recipients = _get_notification_recipients(mr)
	_send_revision_notification(mr, user, user_name, user_role_str, reason, recipients)

	frappe.db.commit()

	return {
		"status": "ok",
		"message": _("Revision request sent to {0}. Audit log + timeline comment added.").format(
			", ".join(recipients) if recipients else "creator"
		),
		"recipients": recipients,
		"reason": reason,
	}


def _get_linked_pos(mr_name):
	"""Return list of PO names that reference any item of this MR."""
	rows = frappe.db.sql("""
		SELECT DISTINCT poi.parent
		FROM `tabPurchase Order Item` poi
		INNER JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE poi.material_request = %s AND po.docstatus != 2
		ORDER BY poi.parent
	""", (mr_name,), as_dict=False)
	return [r[0] for r in rows]


def _insert_approval_log_entry(mr, user, user_roles, reason):
	"""Append a row to TS Approval Log child table on the MR.

	Note: the `action` field is a Select with fixed options. We use "Revised"
	(the closest semantic match) and prefix the comment with [POST-APPROVAL]
	so the timeline and reports can distinguish this from an in-chain revise.
	"""
	try:
		log_row = frappe.get_doc({
			"doctype": "TS Approval Log",
			"parent": mr.name,
			"parenttype": "Material Request",
			"parentfield": "ts_mr_log",
			"action": "Revised",
			"from_state": mr.get("ts_mr_status") or "Approved",
			"to_state": "Revision Requested (post-approval)",
			"action_by": user,
			"action_by_name": get_fullname(user) or user,
			"action_by_role": _primary_role_label(user_roles),
			"action_date": now_datetime(),
			"comment": f"[POST-APPROVAL] {reason}"[:140],
		})
		log_row.insert(ignore_permissions=True)
	except Exception as e:
		# Log failure but don't block the whole request
		frappe.log_error(
			title="ts_mr_post_approval_revision: audit log insert failed",
			message=f"MR: {mr.name}\nUser: {user}\nError: {str(e)}",
		)


def _get_notification_recipients(mr):
	"""Return the creator (owner) + original submitter (deduped, filtered to enabled users)."""
	candidates = set()
	if mr.owner and mr.owner not in ("Administrator", "Guest"):
		candidates.add(mr.owner)
	submitted_by = mr.get("ts_mr_submitted_by")
	if submitted_by and submitted_by not in ("Administrator", "Guest"):
		candidates.add(submitted_by)
	if not candidates:
		return []
	# Filter to enabled users
	enabled = frappe.db.sql("""
		SELECT name FROM `tabUser`
		WHERE name IN %(emails)s AND enabled = 1
	""", {"emails": tuple(candidates)}, as_dict=False)
	return [r[0] for r in enabled]


def _send_revision_notification(mr, requested_by, requested_by_name, requested_by_role, reason, recipients):
	"""Create Notification Log entries + send email to recipients."""
	if not recipients:
		return

	esc = frappe.utils.escape_html
	subject = _("[Revision Requested] MR {0} — Please cancel and amend").format(mr.name)

	doc_url = frappe.utils.get_url(f"/app/material-request/{mr.name}")
	cost_center = mr.get("cost_center") or "—"

	message_html = f"""
		<div style="font-family: Arial, sans-serif; font-size: 14px; color: #1a202c;">
			<h3 style="color: #b45309; margin: 0 0 12px 0;">📝 Revision Requested on Approved MR</h3>
			<p style="margin: 8px 0;"><b>MR:</b> <a href="{doc_url}">{esc(mr.name)}</a></p>
			<p style="margin: 8px 0;"><b>Cost Center:</b> {esc(cost_center)}</p>
			<p style="margin: 8px 0;"><b>Requested by:</b> {esc(requested_by_name)} ({esc(requested_by_role)})</p>
			<p style="margin: 8px 0;"><b>Reason:</b></p>
			<blockquote style="border-left: 3px solid #f59e0b; background: #fef3c7; padding: 10px 14px; margin: 8px 0; color: #78350f;">
				{esc(reason)}
			</blockquote>
			<p style="margin: 16px 0 8px 0;"><b>Action required from you:</b></p>
			<ol>
				<li>Open the MR</li>
				<li>Click <b>Cancel</b> from the menu</li>
				<li>Then click <b>Amend</b> to create a new draft with the requested changes</li>
				<li>Save + Submit for Approval again</li>
			</ol>
			<p style="color: #64748b; font-size: 12px; margin-top: 18px;">
				<i>This is a notification-only request. The original MR is still submitted (docstatus = 1) and
				has not been modified. Any Purchase Orders previously linked to it would have been blocked.</i>
			</p>
			<p style="color: #64748b; font-size: 11px; margin-top: 12px;">
				TS Gate Entry System — Betul Bio Fuel Pvt. Ltd.
			</p>
		</div>
	"""

	# Notification Log (bell icon) — one per recipient
	for r in recipients:
		try:
			nl = frappe.get_doc({
				"doctype": "Notification Log",
				"for_user": r,
				"type": "Alert",
				"document_type": "Material Request",
				"document_name": mr.name,
				"subject": subject,
				"email_content": message_html,
				"from_user": requested_by,
			})
			nl.insert(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(
				title="ts_mr_post_approval_revision: notification log insert failed",
				message=f"User: {r}\nMR: {mr.name}\nError: {str(e)}",
			)

	# Email (single send, multiple recipients)
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message_html,
			reference_doctype="Material Request",
			reference_name=mr.name,
			now=True,
		)
	except Exception as e:
		frappe.log_error(
			title="ts_mr_post_approval_revision: email send failed",
			message=f"Recipients: {recipients}\nMR: {mr.name}\nError: {str(e)}",
		)


def _primary_role_label(user_roles):
	"""Pick the most-senior / most-descriptive role to show in the audit log."""
	priority = [
		"MD", "CEO", "AVP", "IT Head",
		"Purchase Manager", "Purchase User",
		"System Manager", "Administrator",
	]
	for role in priority:
		if role in user_roles:
			return role
	return "User"


@frappe.whitelist()
def can_request_revision(mr_name):
	"""Check if the current user can request revision on this MR.
	Called by the JS to decide whether to show the button.
	Returns: {"allowed": bool, "reason": str | None, "linked_pos": [...]}
	"""
	user_roles = set(frappe.get_roles(frappe.session.user))
	if not (ALLOWED_ROLES & user_roles):
		return {"allowed": False, "reason": "role_missing"}
	if not mr_name or not frappe.db.exists("Material Request", mr_name):
		return {"allowed": False, "reason": "mr_not_found"}
	mr = frappe.db.get_value("Material Request", mr_name,
		["docstatus", "ts_mr_status", "owner"], as_dict=True)
	if mr.docstatus != 1:
		return {"allowed": False, "reason": "not_submitted"}
	# Check PO linkage — button still shown but will warn
	linked_pos = _get_linked_pos(mr_name)
	return {
		"allowed": True,
		"linked_pos": linked_pos,
		"has_linked_pos": bool(linked_pos),
	}
