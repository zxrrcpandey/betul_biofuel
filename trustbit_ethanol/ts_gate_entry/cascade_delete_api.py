"""Token Cascade Delete API endpoints (v2.11.0).

Six whitelisted entrypoints orchestrating the 2-person rule cascade:
  preview_cascade_chain    GET  (Super Admin + CEO + System Manager)
  initiate_cascade         POST (Super Admin only, rate-limited 5/48h)
  ceo_approve_cascade      POST (CEO only, must != initiator)
  cancel_pending_cascade   POST (initiator OR CEO OR System Manager)
  revert_cascade           POST (Super Admin AND within 5min window)
  run_integrity_scan       GET  (Super Admin + CEO + Auditor)
  verify_chain_integrity   GET  (audit-grade — Super Admin + CEO + Auditor + MD)

Hard rules wired here:
- Kill switch (TS Settings.ts_cascade_delete_enabled) re-checked at EVERY entry
- POST CSRF declared (Lesson 175)
- Input regex validated for every token_name + log_name arg
- frappe.has_permission(throw=True) dual-gate (Lesson 224)
- 2-person rule (Hardening C): initiated_by != approved_by enforced server-side
- Type-to-confirm gates (Q&A item 4) regex'd server-side
- Rate limit @ TS Settings.ts_cascade_rate_limit_count / _seconds (Q&A item 3)
- Each side-effect (backup + forensic + webhook + email + audit) try/except
  (Lesson 238)
- frappe.flags.cascade_delete_api_caller=True for state-mutation saves
  (try/finally per Lesson 176)
- frappe.log_error(title=..., message=...) kwargs (Lesson 237)
- All XSS-prone return strings escape_html'd
"""

import json
import re
import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import escape_html, now_datetime, add_to_date


_TOKEN_NAME_RE = re.compile(r"^BBPL-TKN-[0-9]{4}-[0-9]{5}$")
_LOG_NAME_RE = re.compile(r"^BBPL-CDL-[0-9]{4}-[0-9]{5}$")
_FORCE_PR_LITERAL = "FORCE-DELETE-PR"
_FORCE_MI_LITERAL = "FORCE-DELETE-MI"
_REVERT_GRACE_MINUTES = 5
_READ_ROLES = {"Super Admin", "CEO", "MD", "System Manager", "Administrator", "Auditor", "IT Head"}
_INITIATE_ROLES = {"Super Admin"}
_APPROVE_ROLES = {"CEO"}
_REVERT_ROLES = {"Super Admin"}


# ---------------------------------------------------------------- common gates


def _gate_kill_switch():
	"""Throws if cascade tool is disabled. Re-read every call (defense-in-depth)."""
	if not int(frappe.db.get_single_value("TS Settings", "ts_cascade_delete_enabled") or 0):
		frappe.throw(_("Cascade Delete tool is currently disabled."), frappe.PermissionError)


def _gate_role(allowed: set, action_label: str = "this action"):
	"""Throws if current user has no role in `allowed`. Lesson 224 dual-gate."""
	user_roles = set(frappe.get_roles(frappe.session.user))
	if user_roles.isdisjoint(allowed):
		frappe.throw(
			_("You do not have permission to perform {0}.").format(action_label),
			frappe.PermissionError,
		)


def _validate_token_name(token_name: str):
	if not token_name or not _TOKEN_NAME_RE.match(token_name):
		frappe.throw(_("Invalid token name format. Expected BBPL-TKN-YYYY-NNNNN."))


def _validate_log_name(log_name: str):
	if not log_name or not _LOG_NAME_RE.match(log_name):
		frappe.throw(_("Invalid cascade delete log name. Expected BBPL-CDL-YYYY-NNNNN."))


def _escape_dict_strings(d: dict) -> dict:
	"""Recursively escape_html every string in a dict (for safe browser return)."""
	if isinstance(d, dict):
		return {k: _escape_dict_strings(v) for k, v in d.items()}
	if isinstance(d, list):
		return [_escape_dict_strings(x) for x in d]
	if isinstance(d, str):
		return escape_html(d)
	return d


# ---------------------------------------------------------------- 1. PREVIEW


@frappe.whitelist()
@rate_limit(key="cascade_preview", limit=30, seconds=60)
def preview_cascade_chain(token_name: str) -> dict:
	"""Read-only chain map. Public to all 4 viewer roles."""
	_gate_kill_switch()
	_gate_role(_READ_ROLES, "preview cascade chain")
	_validate_token_name(token_name)
	# Permission to read the underlying Token
	frappe.has_permission("TS Token", "read", doc=token_name, throw=True)

	from trustbit_ethanol.ts_gate_entry.cascade_delete_engine import build_chain_snapshot
	chain = build_chain_snapshot(token_name)
	return _escape_dict_strings(chain)


# ---------------------------------------------------------------- 2. INITIATE


@frappe.whitelist(methods=["POST"])
@rate_limit(key="cascade_initiate", limit=5, seconds=172800)  # 5 per 48h fallback
def initiate_cascade(
	token_name: str,
	force_pr: int = 0,
	force_mi: int = 0,
	confirm_token_name_typed: str = "",
	force_pr_confirmation_typed: str = "",
	force_mi_confirmation_typed: str = "",
	client_screen: str = "",
	client_language: str = "",
) -> dict:
	"""Super Admin initiates a cascade. Creates Pending CEO Approval log row.

	Returns: {"success": True, "log_name": "BBPL-CDL-..."} on success.
	"""
	_gate_kill_switch()
	_gate_role(_INITIATE_ROLES, "initiate cascade delete")
	_validate_token_name(token_name)

	# Type-to-confirm gates (Q&A item 4)
	if (confirm_token_name_typed or "").strip() != token_name:
		frappe.throw(_("Confirm Token Name must exactly match {0}.").format(escape_html(token_name)))
	if int(force_pr):
		if force_pr_confirmation_typed != _FORCE_PR_LITERAL:
			frappe.throw(_("Force-PR confirmation must equal: {0}").format(_FORCE_PR_LITERAL))
	if int(force_mi):
		if force_mi_confirmation_typed != _FORCE_MI_LITERAL:
			frappe.throw(_("Force-MI confirmation must equal: {0}").format(_FORCE_MI_LITERAL))

	# Token must exist + Super Admin must have read perm
	frappe.has_permission("TS Token", "read", doc=token_name, throw=True)
	if not frappe.db.exists("TS Token", token_name):
		frappe.throw(_("Token {0} not found.").format(escape_html(token_name)))

	# Configurable rate limit (Q&A 3) — re-check inside the window via DB query.
	# (The decorator's 5/48h is a default fallback; the configurable one is here.)
	rl_count = int(frappe.db.get_single_value("TS Settings", "ts_cascade_rate_limit_count") or 5)
	rl_seconds = int(frappe.db.get_single_value("TS Settings", "ts_cascade_rate_limit_seconds") or 172800)
	from frappe.utils import add_to_date
	window_start = add_to_date(now_datetime(), seconds=-rl_seconds)
	used = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabTS Cascade Delete Log`
		   WHERE initiated_by=%s AND initiated_at >= %s""",
		(frappe.session.user, window_start),
	)[0][0]
	if used >= rl_count:
		frappe.throw(_("Rate limit reached: {0} cascade deletions in the last {1}h. Try later.").format(
			rl_count, rl_seconds // 3600,
		))
	rate_limit_position = used + 1

	# Chain snapshot (Hardening B)
	from trustbit_ethanol.ts_gate_entry.cascade_delete_engine import build_chain_snapshot
	chain = build_chain_snapshot(token_name)
	chain_json = json.dumps(chain, indent=2, default=str)

	# Forensic block (Q&A 7)
	from trustbit_ethanol.ts_gate_entry.cascade_delete_forensics import (
		capture_forensic_block, serialize_forensic_block,
	)
	forensic = capture_forensic_block({"screen": client_screen, "language": client_language})
	forensic_json = serialize_forensic_block(forensic)

	# Insert log row
	log = frappe.new_doc("TS Cascade Delete Log")
	log.target_token = token_name
	log.initiated_by = frappe.session.user
	log.initiated_at = now_datetime()
	log.approval_status = "Pending CEO Approval"
	log.confirm_token_name_typed = confirm_token_name_typed
	log.force_pr_override = int(force_pr)
	log.force_pr_confirmation_typed = force_pr_confirmation_typed if int(force_pr) else ""
	log.force_mi_override = int(force_mi)
	log.force_mi_confirmation_typed = force_mi_confirmation_typed if int(force_mi) else ""
	log.target_chain_json = chain_json
	log.forensic_block_json = forensic_json
	log.rate_limit_position = rate_limit_position
	log.flags.ignore_permissions = True
	log.insert(ignore_permissions=True)
	log.submit()
	frappe.db.commit()

	# Pre-cascade backup (Hardening B + F) — taken NOW so the snapshot is fresh
	try:
		from trustbit_ethanol.ts_gate_entry.cascade_delete_backup import create_pre_cascade_backup
		bkp = create_pre_cascade_backup(token_name, log.name)
		_persist_log_field(log.name, "backup_filename", bkp["filename"])
		_persist_log_field(log.name, "backup_sha256", bkp["sha256"])
	except Exception as e:
		frappe.log_error(
			title=f"Cascade pre-backup failed for {log.name}",
			message=f"{type(e).__name__}: {e}",
		)
		# Backup failure DOES block — without backup we can't safely cascade.
		_transition_log_state(log.name, "Failed",
		                       extra={"execution_result_json": json.dumps({"error": f"backup failed: {e}"})})
		frappe.throw(_("Pre-cascade backup failed; cascade aborted. See Error Log."))

	# Notify CEO + MD (best-effort)
	_send_pending_approval_email(log.name)

	# Webhook (best-effort, dispatches the Pending event)
	try:
		from trustbit_ethanol.ts_gate_entry.cascade_delete_webhook import dispatch_webhook
		log_doc = frappe.get_doc("TS Cascade Delete Log", log.name)
		dispatch_webhook(log_doc)
	except Exception as e:
		frappe.log_error(
			title=f"Cascade webhook dispatch failed for {log.name}",
			message=f"{type(e).__name__}: {e}",
		)

	return {"success": True, "log_name": log.name, "rate_limit_position": rate_limit_position}


# ---------------------------------------------------------------- 3. CEO APPROVE


@frappe.whitelist(methods=["POST"])
@rate_limit(key="cascade_approve", limit=30, seconds=60)
def ceo_approve_cascade(log_name: str, decision: str, reason: str = "") -> dict:
	"""CEO approves or rejects a pending cascade. `decision` ∈ {'approve','reject'}.

	On approve: state → Approved, then engine runs synchronously → state → Executed.
	On reject: state → Rejected. Reason must be ≥10 chars.
	"""
	_gate_kill_switch()
	_gate_role(_APPROVE_ROLES, "approve cascade delete")
	_validate_log_name(log_name)
	if decision not in ("approve", "reject"):
		frappe.throw(_("decision must be 'approve' or 'reject'."))

	# FOR UPDATE lock on the log row (race: two CEOs approve simultaneously)
	rows = frappe.db.sql(
		"""SELECT name, initiated_by, approval_status FROM `tabTS Cascade Delete Log`
		   WHERE name=%s FOR UPDATE""",
		(log_name,), as_dict=True,
	)
	if not rows:
		frappe.throw(_("Cascade Delete Log {0} not found.").format(escape_html(log_name)))
	log = rows[0]
	if log.approval_status != "Pending CEO Approval":
		frappe.throw(_("Log {0} is not in 'Pending CEO Approval' state (currently {1}).").format(
			escape_html(log_name), escape_html(log.approval_status),
		))

	# 2-person rule
	if log.initiated_by == frappe.session.user:
		frappe.throw(_("Two-person rule violated: you initiated this request; a different CEO must approve."))

	if decision == "reject":
		if len(reason.strip()) < 10:
			frappe.throw(_("Rejection reason must be at least 10 characters."))
		_transition_log_state(log_name, "Rejected", extra={
			"approved_by": frappe.session.user,
			"approved_at": now_datetime(),
			"rejection_reason": reason.strip()[:500],
		})
		_send_decision_email(log_name, "Rejected")
		return {"success": True, "approval_status": "Rejected"}

	# Approve → mark Approved → run engine
	_transition_log_state(log_name, "Approved", extra={
		"approved_by": frappe.session.user,
		"approved_at": now_datetime(),
	})

	log_doc = frappe.get_doc("TS Cascade Delete Log", log_name)
	from trustbit_ethanol.ts_gate_entry.cascade_delete_engine import execute_cascade
	result = execute_cascade(
		token_name=log_doc.target_token,
		log_name=log_name,
		force_pr=bool(log_doc.force_pr_override),
		force_mi=bool(log_doc.force_mi_override),
	)

	# Persist execution result + transition state
	now = now_datetime()
	revert_expires_at = add_to_date(now, minutes=_REVERT_GRACE_MINUTES)
	exec_json = json.dumps(result, indent=2, default=str)

	new_state = "Executed" if result.get("success") else "Failed"
	_transition_log_state(log_name, new_state, extra={
		"executed_at": now,
		"execution_result_json": exec_json,
		"revert_window_expires_at": revert_expires_at if result.get("success") else None,
	})

	# Post-cascade integrity scan (Hardening G)
	try:
		from trustbit_ethanol.ts_gate_entry.cascade_delete_integrity import run_orphan_scan_for_log
		run_orphan_scan_for_log(log_name)
	except Exception as e:
		frappe.log_error(
			title=f"Cascade post-exec integrity scan failed for {log_name}",
			message=f"{type(e).__name__}: {e}",
		)

	# Webhook + email
	try:
		from trustbit_ethanol.ts_gate_entry.cascade_delete_webhook import dispatch_webhook
		log_doc_after = frappe.get_doc("TS Cascade Delete Log", log_name)
		dispatch_webhook(log_doc_after)
	except Exception as e:
		frappe.log_error(title=f"Cascade webhook dispatch failed for {log_name}", message=f"{type(e).__name__}: {e}")
	try:
		_send_decision_email(log_name, new_state)
	except Exception as e:
		frappe.log_error(title=f"Cascade decision email failed for {log_name}", message=f"{type(e).__name__}: {e}")

	return {
		"success": result.get("success"),
		"approval_status": new_state,
		"steps": result.get("steps"),
		"lesson_275_fallback_used": result.get("lesson_275_fallback_used"),
		"revert_window_expires_at": str(revert_expires_at) if result.get("success") else None,
	}


# ---------------------------------------------------------------- 4. CANCEL PENDING


@frappe.whitelist(methods=["POST"])
@rate_limit(key="cascade_cancel", limit=30, seconds=60)
def cancel_pending_cascade(log_name: str) -> dict:
	"""Initiator, CEO, or System Manager can cancel a Pending request."""
	_gate_kill_switch()
	_validate_log_name(log_name)
	rows = frappe.db.sql(
		"""SELECT name, initiated_by, approval_status FROM `tabTS Cascade Delete Log`
		   WHERE name=%s FOR UPDATE""",
		(log_name,), as_dict=True,
	)
	if not rows:
		frappe.throw(_("Log {0} not found.").format(escape_html(log_name)))
	log = rows[0]
	if log.approval_status != "Pending CEO Approval":
		frappe.throw(_("Only Pending requests can be cancelled."))
	user_roles = set(frappe.get_roles(frappe.session.user))
	if frappe.session.user != log.initiated_by and user_roles.isdisjoint({"CEO", "System Manager"}):
		frappe.throw(_("Only the initiator, CEO, or System Manager may cancel."), frappe.PermissionError)
	_transition_log_state(log_name, "Cancelled", extra={
		"approved_by": frappe.session.user,
		"approved_at": now_datetime(),
	})
	try:
		_send_decision_email(log_name, "Cancelled")
	except Exception:
		pass
	return {"success": True, "approval_status": "Cancelled"}


# ---------------------------------------------------------------- 5. REVERT


@frappe.whitelist(methods=["POST"])
@rate_limit(key="cascade_revert", limit=10, seconds=60)
def revert_cascade(log_name: str) -> dict:
	"""Restore the chain from backup. Super Admin only AND within 5min."""
	_gate_kill_switch()
	_gate_role(_REVERT_ROLES, "revert cascade delete")
	_validate_log_name(log_name)
	rows = frappe.db.sql(
		"""SELECT * FROM `tabTS Cascade Delete Log` WHERE name=%s FOR UPDATE""",
		(log_name,), as_dict=True,
	)
	if not rows:
		frappe.throw(_("Log {0} not found.").format(escape_html(log_name)))
	log_row = rows[0]
	if log_row.approval_status != "Executed":
		frappe.throw(_("Only Executed cascades can be reverted (currently {0}).").format(
			escape_html(log_row.approval_status),
		))
	# 5-min window check
	now = now_datetime()
	if not log_row.revert_window_expires_at or now > log_row.revert_window_expires_at:
		frappe.throw(_("Revert window has expired. This cascade can no longer be reverted."))

	log_doc = frappe.get_doc("TS Cascade Delete Log", log_name)
	from trustbit_ethanol.ts_gate_entry.cascade_delete_backup import restore_from_backup
	result = restore_from_backup(log_doc)

	if result.get("success"):
		_transition_log_state(log_name, "Reverted", extra={
			"reverted_at": now,
			"reverted_by": frappe.session.user,
			"revert_result_json": json.dumps(result, indent=2, default=str),
		})
	else:
		# Restore failed — log row state stays Executed; revert_result_json captures error.
		_persist_log_field(log_name, "revert_result_json", json.dumps(result, indent=2, default=str))

	try:
		_send_decision_email(log_name, "Reverted" if result.get("success") else "RevertFailed")
	except Exception:
		pass

	return result


# ---------------------------------------------------------------- 6. INTEGRITY


@frappe.whitelist()
def run_integrity_scan(log_name: str = "") -> dict:
	"""On-demand integrity scan. If log_name given, scans that token; else returns
	overall hash chain integrity result."""
	_gate_kill_switch()
	_gate_role(_READ_ROLES, "run integrity scan")
	if log_name:
		_validate_log_name(log_name)
		from trustbit_ethanol.ts_gate_entry.cascade_delete_integrity import run_orphan_scan_for_log
		return run_orphan_scan_for_log(log_name)
	# No log_name — return chain integrity
	from trustbit_ethanol.ts_gate_entry.doctype.ts_cascade_delete_log.ts_cascade_delete_log import (
		verify_chain_integrity as _vci,
	)
	return _vci()


@frappe.whitelist()
def verify_chain_integrity() -> dict:
	"""Convenience alias to verify the hash chain (audit-grade, read-only)."""
	_gate_kill_switch()
	_gate_role(_READ_ROLES, "verify chain integrity")
	from trustbit_ethanol.ts_gate_entry.doctype.ts_cascade_delete_log.ts_cascade_delete_log import (
		verify_chain_integrity as _vci,
	)
	return _vci()


# ---------------------------------------------------------------- internal helpers


def _persist_log_field(log_name: str, field: str, value):
	"""Set ONE field on the log with the api_caller flag set (bypasses append-only)."""
	try:
		frappe.flags.cascade_delete_api_caller = True
		frappe.db.set_value("TS Cascade Delete Log", log_name, field, value, update_modified=False)
		frappe.db.commit()
	finally:
		frappe.flags.cascade_delete_api_caller = False


def _transition_log_state(log_name: str, new_status: str, extra: dict | None = None):
	"""Atomic state transition with append-only flag bypass.

	`extra` keys are applied alongside approval_status (so we don't fire the
	append-only guard twice).
	"""
	updates = {"approval_status": new_status}
	if extra:
		updates.update({k: v for k, v in extra.items() if v is not None})
	try:
		frappe.flags.cascade_delete_api_caller = True
		frappe.db.set_value("TS Cascade Delete Log", log_name, updates, update_modified=True)
		frappe.db.commit()
	finally:
		frappe.flags.cascade_delete_api_caller = False


def _resolve_alert_recipients() -> list[str]:
	"""Return enabled emails of users carrying CEO + MD roles."""
	emails = frappe.db.sql(
		"""SELECT DISTINCT u.email
		   FROM `tabUser` u
		   JOIN `tabHas Role` r ON r.parent = u.name
		   WHERE u.enabled = 1
		     AND u.email IS NOT NULL AND u.email != ''
		     AND r.role IN ('CEO', 'MD')""",
		as_dict=False,
	)
	return [e[0] for e in emails if e[0]]


def _send_pending_approval_email(log_name: str):
	"""MD + CEO receive bold-red alert when Pending CEO Approval row inserted."""
	recipients = _resolve_alert_recipients()
	if not recipients:
		return
	try:
		log_doc = frappe.get_doc("TS Cascade Delete Log", log_name)
		frappe.sendmail(
			recipients=recipients,
			subject=f"[BBPL CRITICAL] Cascade Delete Pending: Token {log_doc.target_token} by {log_doc.initiated_by}",
			template="Cascade Delete Warning",
			args={"doc": log_doc, "event": "PENDING APPROVAL"},
			now=False,
			delayed=True,
		)
	except Exception as e:
		frappe.log_error(
			title=f"Cascade approval email failed for {log_name}",
			message=f"{type(e).__name__}: {e}",
		)


def _send_decision_email(log_name: str, decision_label: str):
	"""MD + CEO receive notification when state transitions to a terminal label."""
	recipients = _resolve_alert_recipients()
	if not recipients:
		return
	try:
		log_doc = frappe.get_doc("TS Cascade Delete Log", log_name)
		frappe.sendmail(
			recipients=recipients,
			subject=f"[BBPL CRITICAL] Cascade Delete {decision_label}: Token {log_doc.target_token}",
			template="Cascade Delete Warning",
			args={"doc": log_doc, "event": decision_label.upper()},
			now=False,
			delayed=True,
		)
	except Exception as e:
		frappe.log_error(
			title=f"Cascade decision email failed for {log_name}",
			message=f"{type(e).__name__}: {e}",
		)


# ---------------------------------------------------------------- scheduler entry points

# Wired in hooks.py.scheduler_events
def scheduled_expire_revert_windows():
	"""`*/1 * * * *` — observability only; counts expired Executed cascades."""
	from trustbit_ethanol.ts_gate_entry.cascade_delete_integrity import expire_revert_windows
	return expire_revert_windows()


def scheduled_nightly_integrity_scan():
	"""`0 3 * * *` — nightly orphan + hash chain audit."""
	from trustbit_ethanol.ts_gate_entry.cascade_delete_integrity import nightly_orphan_scan
	return nightly_orphan_scan()
