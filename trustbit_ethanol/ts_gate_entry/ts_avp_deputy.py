# TS AVP Deputy — v2.9.7
# CEO acts as deputy for AVP at "Pending AVP" MR step when AVP is unavailable.
# Gated by ts_avp_deputy_enabled (default OFF). Audit comment + email to AVP role
# users + MR creator. Reuses the existing _approve_mr transition via the
# `frappe.flags.in_avp_deputy` bypass on _validate_user_can_act_on_mr.

import frappe
from frappe import _
from frappe.utils import escape_html, now_datetime


# CEO-only per user decision Q1 (Apr 28 2026)
DEPUTY_ROLE = "CEO"


@frappe.whitelist(methods=["POST"])
def approve_as_avp_deputy(mr_name, reason=None):
	"""Approve a Material Request at 'Pending AVP' status on behalf of AVP.

	Authorization chain:
	  1. Kill switch ts_avp_deputy_enabled must be ON.
	  2. Caller must hold the CEO role.
	  3. MR must be at ts_mr_status = 'Pending AVP'.
	  4. Caller must NOT be the MR owner OR submitter (self-approval still blocked).

	Once authorized, sets frappe.flags.in_avp_deputy and calls the existing
	approve_document() flow which delegates to _approve_mr(). The flag tells
	_validate_user_can_act_on_mr to skip route/CC role checks (handled here).
	Self-approval check inside _approve_mr stays active.

	Returns: dict with ok=True + the approve_document result on success.
	Raises: frappe.PermissionError or frappe.ValidationError on rejection.
	"""
	if not mr_name:
		frappe.throw(_("Material Request name is required."))

	# 1. Kill switch (Lesson 171/172: raw SQL + fail-closed)
	if not _is_deputy_mode_enabled():
		frappe.throw(_("AVP Deputy mode is currently disabled. Contact IT Head to enable."))

	# 2. Role check
	user_roles = set(frappe.get_roles(frappe.session.user))
	if DEPUTY_ROLE not in user_roles:
		raise frappe.PermissionError(
			_("Only CEO may act as AVP Deputy. Your roles: {0}").format(sorted(user_roles))
		)

	# 3. Doc + status check
	if not frappe.db.exists("Material Request", mr_name):
		frappe.throw(_("Material Request {0} not found.").format(mr_name))

	mr = frappe.get_doc("Material Request", mr_name)
	# v2.9.9 — Block Material Transfer MRs (they use ts_mr_transfer endpoints)
	from trustbit_ethanol.ts_gate_entry.ts_po_approval import _block_if_mr_transfer
	_block_if_mr_transfer(mr)
	if (mr.ts_mr_status or "") != "Pending AVP":
		frappe.throw(
			_("This MR is at status '{0}', not 'Pending AVP'. AVP Deputy mode only "
			  "applies to MRs awaiting AVP approval.").format(mr.ts_mr_status or "(blank)")
		)

	# 4. Self-approval check (defense — _approve_mr also checks this)
	mr_submitted_by = getattr(mr, "ts_mr_submitted_by", None)
	if mr.owner == frappe.session.user:
		frappe.throw(_("You cannot approve a Material Request that you created, even as Deputy."))
	if mr_submitted_by and mr_submitted_by == frappe.session.user:
		frappe.throw(_("You cannot approve a Material Request that you submitted, even as Deputy."))

	# 5. Build deputy comment + delegate to existing approve flow
	safe_reason = escape_html(reason or "(not provided)").strip() or "(not provided)"
	deputy_marker = f"[AVP DEPUTY] CEO {frappe.session.user} approving on behalf of AVP. Reason: {safe_reason}"

	original_status = mr.ts_mr_status

	# Lesson 176: try/finally on flag mutation so partial failure doesn't leak the flag.
	frappe.flags.in_avp_deputy = True
	try:
		from trustbit_ethanol.ts_gate_entry.ts_po_approval import approve_document
		result = approve_document("Material Request", mr_name, comment=deputy_marker)
	finally:
		frappe.flags.in_avp_deputy = False

	# 6. Audit comment (separate from the action comment for searchability)
	new_status = frappe.db.get_value("Material Request", mr_name, "ts_mr_status")
	_add_deputy_audit_comment(
		mr_name=mr_name,
		acting_user=frappe.session.user,
		original_status=original_status,
		new_status=new_status,
		reason=safe_reason,
	)

	# 7. Email AVP role users + MR creator
	try:
		_notify_deputy_action(
			mr_doc=mr,
			acting_user=frappe.session.user,
			reason=safe_reason,
			new_status=new_status,
		)
	except Exception as e:
		# Lesson 196: notification failure NEVER blocks the action.
		frappe.log_error(
			message=f"AVP Deputy notify failed for MR {mr_name}: {e}",
			title="ts_avp_deputy notify",
		)

	return {"ok": True, "result": result, "new_status": new_status}


# ── helpers ─────────────────────────────────────────────────────────


@frappe.whitelist(allow_guest=False)
def is_avp_deputy_enabled():
	"""Read-only helper for the JS button-visibility check.

	The kill-switch field `ts_avp_deputy_enabled` lives at permlevel=1 (IT Head
	only), so CEO cannot read it via `frappe.client.get_value`. This helper
	uses the same raw-SQL bypass as the server-side gate so the JS button can
	check enablement without exposing other permlevel=1 fields to CEO.

	Returns: bool
	"""
	return _is_deputy_mode_enabled()


def _is_deputy_mode_enabled():
	"""Read kill-switch from tabSingles via raw SQL (Lesson 171: tabSingles
	has no `modified` column; Check fields return 0 not None). Fail closed.
	"""
	try:
		row = frappe.db.sql(
			"""
			SELECT value FROM tabSingles
			WHERE doctype='TS Settings' AND field='ts_avp_deputy_enabled'
			LIMIT 1
			""",
			as_dict=False,
		)
		if not row:
			return False
		val = (row[0][0] if row[0] else "0") or "0"
		return str(val).strip() in ("1", "true", "True")
	except Exception:
		return False


def _add_deputy_audit_comment(mr_name, acting_user, original_status, new_status, reason):
	"""Audit-trail comment on the MR. Persists even if email/transition partial-fails."""
	try:
		body = (
			f"<b>[AVP DEPUTY APPROVAL]</b><br>"
			f"Acting Role: CEO<br>"
			f"Acting User: {escape_html(acting_user)}<br>"
			f"Original Status: {escape_html(original_status or '')}<br>"
			f"New Status: {escape_html(new_status or '')}<br>"
			f"Reason: {reason}<br>"
			f"Timestamp: {now_datetime()}"
		)
		mr_doc = frappe.get_doc("Material Request", mr_name)
		mr_doc.add_comment("Info", body)
	except Exception as e:
		frappe.log_error(
			message=f"AVP Deputy audit comment failed for MR {mr_name}: {e}",
			title="ts_avp_deputy audit",
		)


def _notify_deputy_action(mr_doc, acting_user, reason, new_status):
	"""Email AVP role users + MR creator. Hindi labels via translations/hi.csv."""
	# Recipients: AVP role users (enabled) + MR creator (excluding the deputy themselves)
	recipients = set()
	avp_users = frappe.get_all(
		"Has Role",
		filters={"role": "AVP", "parenttype": "User"},
		pluck="parent",
	)
	if avp_users:
		enabled = frappe.get_all(
			"User",
			filters={"name": ["in", avp_users], "enabled": 1},
			pluck="name",
		)
		for u in enabled:
			if u and u not in ("Administrator", "Guest"):
				recipients.add(u)
	if mr_doc.owner and mr_doc.owner not in ("Administrator", "Guest"):
		recipients.add(mr_doc.owner)
	# Don't email the deputy themselves
	recipients.discard(acting_user)
	if not recipients:
		return

	esc_mr = escape_html(mr_doc.name or "")
	esc_user = escape_html(acting_user or "")
	esc_status = escape_html(new_status or "")
	subject = f"[AVP Deputy] MR {esc_mr} approved by CEO {esc_user}"
	message = (
		f"<p>Material Request <b>{esc_mr}</b> was approved on behalf of AVP "
		f"by CEO <b>{esc_user}</b>.</p>"
		f"<p>Reason: {reason}</p>"
		f"<p>Status moved: <b>Pending AVP</b> → <b>{esc_status}</b></p>"
		f"<p>This is an automated notification because AVP Deputy mode is currently "
		f"enabled. If you believe this approval was incorrect, contact IT Head to "
		f"disable the flag and review the audit log on the MR.</p>"
		f"<p><a href='/app/material-request/{esc_mr}'>Open MR</a></p>"
	)
	frappe.sendmail(
		recipients=list(recipients),
		subject=subject,
		message=message,
		reference_doctype="Material Request",
		reference_name=mr_doc.name,
		delayed=False,
	)
