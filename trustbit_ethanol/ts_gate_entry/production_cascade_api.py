"""Production Cascade Delete API endpoints — the manufacturing sibling of
cascade_delete_api.py (the Token cascade). Cloned from that hardened core; the
2-person/rate-limit/executor-resolution/TOCTOU machinery is identical. The new
surface is: Manufacturing-Manager-only INITIATE, CEO-or-MD APPROVE, the prod
type-to-confirm phrases (PE name, FORCE-DELETE-MANUFACTURE, FORCE-DELETE-DISPATCHED),
and the HAZARD-C blast radius in the preview.

Endpoints (all mutations @frappe.whitelist(methods=["POST"]) — Lesson 175):
  preview_production_cascade           GET  (read roles) + HAZARD-C blast radius
  initiate_production_cascade          POST (Manufacturing Manager) — Pending log + backup
  approve_production_cascade           POST (CEO or MD) — 2-person + FOR UPDATE + run engine
  cancel_pending_production_cascade    POST (initiator / CEO / MD / System Manager)
  revert_production_cascade            POST (Super Admin / CEO / MD + window) — LOUDER warning
  get_pending_production_review_detail GET  (read roles) — decision support
  run_production_integrity_scan        GET  (read roles)
  verify_production_chain_integrity    GET  (read roles)
  scheduled_*                          scheduler

Hard rules wired here (mirrors the Token API):
- Kill switch (TS Settings.ts_prod_cascade_enabled) re-checked at EVERY entry, fail-closed
- POST CSRF declared (Lesson 175)
- Input regex validated for every production_name + log_name arg
- frappe.has_permission(throw=True) dual-gate (Lesson 224)
- 2-person rule: initiated_by != approved_by enforced server-side
- Type-to-confirm gates regex'd server-side
- Rate limit @ TS Settings.ts_prod_cascade_rate_limit_count / _seconds
- Each side-effect (backup + forensic + webhook + email) try/except (Lesson 238)
- frappe.flags.production_cascade_api_caller=True for state-mutation saves (try/finally, L176)
- frappe.log_error(title=..., message=...) kwargs (Lesson 237)
- All XSS-prone return strings escape_html'd
"""

import json
import re
import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import escape_html, now_datetime, add_to_date, flt


# PE naming is PROD-.YYYY.- -> e.g. PROD-2026-00001 (digits, may be more).
_PRODUCTION_NAME_RE = re.compile(r"^PROD-[0-9]{4}-[0-9]{5,}$")
_LOG_NAME_RE = re.compile(r"^BBPL-PCD-[0-9]{4}-[0-9]{5}$")
_FORCE_MANUFACTURE_LITERAL = "FORCE-DELETE-MANUFACTURE"
_FORCE_DISPATCHED_LITERAL = "FORCE-DELETE-DISPATCHED"
_REVERT_GRACE_MINUTES = 5

_READ_ROLES = {"Super Admin", "CEO", "MD", "System Manager", "Administrator",
               "Auditor", "IT Head", "Manufacturing Manager"}
# LOCKED DESIGN DECISION — initiate is Manufacturing Manager ONLY.
_INITIATE_ROLES = {"Manufacturing Manager"}
# LOCKED DESIGN DECISION — approve is CEO OR MD (either may approve; != initiator).
_APPROVE_ROLES = {"CEO", "MD"}
_REVERT_ROLES = {"Super Admin", "CEO", "MD"}
# The resolved executor must hold Super Admin (the role that carries the WO/JC/SE/QI
# cancel+delete Custom DocPerm grants — see setup_super_admin_role._SUPER_ADMIN_PERMS).
_EXECUTOR_REQUIRED_ROLES = {"Super Admin"}


# ---------------------------------------------------------------- common gates


def _gate_kill_switch():
	"""Throws if the production cascade tool is disabled. Re-read every call, fail-closed."""
	try:
		enabled = int(frappe.db.get_single_value("TS Settings", "ts_prod_cascade_enabled") or 0)
	except Exception:
		enabled = 0
	if not enabled:
		frappe.throw(_("Production Cascade Delete tool is currently disabled."), frappe.PermissionError)


def _gate_role(allowed: set, action_label: str = "this action"):
	"""Throws if the current user has no role in `allowed`. Lesson 224 dual-gate."""
	user_roles = set(frappe.get_roles(frappe.session.user))
	if user_roles.isdisjoint(allowed):
		frappe.throw(
			_("You do not have permission to perform {0}.").format(action_label),
			frappe.PermissionError,
		)


def _resolve_executor_user(initiator: str) -> str:
	"""Return the user the engine should run AS — a Super-Admin-roled enabled user.

	The Manufacturing Manager initiator does NOT hold the WO/JC/SE/QI cancel+delete
	Custom DocPerm grants; running the engine as them would throw mid-cascade with a
	partially-deleted chain. Resolution order:
	  1. TS Setting ts_prod_cascade_executor_user (if set + enabled + Super Admin).
	  2. First enabled user with the Super Admin role (deterministic by creation).
	  3. Fail-closed — throw with a clear remediation.
	"""
	initiator_roles = set(frappe.get_roles(initiator))
	if not initiator_roles.isdisjoint(_EXECUTOR_REQUIRED_ROLES):
		return initiator

	configured = frappe.db.get_single_value("TS Settings", "ts_prod_cascade_executor_user")
	if configured:
		ok_enabled = frappe.db.get_value("User", configured, "enabled")
		configured_roles = set(frappe.get_roles(configured)) if ok_enabled else set()
		if ok_enabled and not configured_roles.isdisjoint(_EXECUTOR_REQUIRED_ROLES):
			return configured

	fallback = frappe.db.sql(
		"""SELECT u.name FROM `tabUser` u
		   JOIN `tabHas Role` hr ON hr.parent = u.name
		   WHERE u.enabled = 1
		     AND hr.role IN %(roles)s
		   ORDER BY u.creation ASC
		   LIMIT 1""",
		{"roles": tuple(_EXECUTOR_REQUIRED_ROLES)},
	)
	if fallback:
		return fallback[0][0]

	frappe.throw(_(
		"Production Cascade cannot execute: no enabled user with the Super Admin role is "
		"available to run the deletion. An admin must either (a) assign the Super Admin "
		"role to an enabled user, OR (b) set `ts_prod_cascade_executor_user` in TS Settings "
		"to a Super-Admin-roled user. This protects the cascade from running with "
		"insufficient permissions on Work Order / Job Card / Stock Entry / Quality "
		"Inspection / TS Production Entry."
	), frappe.PermissionError)


def _validate_production_name(production_name: str):
	if not production_name or not _PRODUCTION_NAME_RE.match(production_name):
		frappe.throw(_("Invalid Production Entry name format. Expected PROD-YYYY-NNNNN."))


def _validate_log_name(log_name: str):
	if not log_name or not _LOG_NAME_RE.match(log_name):
		frappe.throw(_("Invalid production cascade log name. Expected BBPL-PCD-YYYY-NNNNN."))


def _escape_dict_strings(d):
	"""Recursively escape_html every string in a structure (safe browser return)."""
	if isinstance(d, dict):
		return {k: _escape_dict_strings(v) for k, v in d.items()}
	if isinstance(d, list):
		return [_escape_dict_strings(x) for x in d]
	if isinstance(d, str):
		return escape_html(d)
	return d


# ---------------------------------------------------------------- 1. PREVIEW


@frappe.whitelist()
@rate_limit(key="prod_cascade_preview", limit=30, seconds=60)
def preview_production_cascade(production_name: str) -> dict:
	"""Read-only chain map + HAZARD-C blast radius. Public to all viewer roles."""
	_gate_kill_switch()
	_gate_role(_READ_ROLES, "preview production cascade chain")
	_validate_production_name(production_name)
	frappe.has_permission("TS Production Entry", "read", doc=production_name, throw=True)

	from trustbit_ethanol.ts_gate_entry.production_cascade_engine import (
		build_chain_snapshot, detect_hazard_c,
	)
	chain = build_chain_snapshot(production_name)
	hazard = detect_hazard_c(production_name, chain)
	chain["hazard_c"] = hazard
	return _escape_dict_strings(chain)


# ---------------------------------------------------------------- 2. INITIATE


@frappe.whitelist(methods=["POST"])
def initiate_production_cascade(
	production_name: str,
	force_manufacture: int = 0,
	force_dispatched: int = 0,
	confirm_production_name_typed: str = "",
	force_manufacture_confirmation_typed: str = "",
	force_dispatched_confirmation_typed: str = "",
	client_screen: str = "",
	client_language: str = "",
) -> dict:
	"""Manufacturing Manager initiates a production cascade. Creates a Pending log row.

	ALWAYS full chain + full physical delete (no partial-cut). Returns:
	  {"success": True, "log_name": "BBPL-PCD-..."} on success.
	"""
	_gate_kill_switch()
	_gate_role(_INITIATE_ROLES, "initiate production cascade delete")
	_validate_production_name(production_name)

	# Type-to-confirm gates.
	if (confirm_production_name_typed or "").strip() != production_name:
		frappe.throw(_("Confirm Production Name must exactly match {0}.").format(escape_html(production_name)))
	if int(force_manufacture):
		if force_manufacture_confirmation_typed != _FORCE_MANUFACTURE_LITERAL:
			frappe.throw(_("Force-Manufacture confirmation must equal: {0}").format(_FORCE_MANUFACTURE_LITERAL))
	if int(force_dispatched):
		if force_dispatched_confirmation_typed != _FORCE_DISPATCHED_LITERAL:
			frappe.throw(_("Force-Dispatched confirmation must equal: {0}").format(_FORCE_DISPATCHED_LITERAL))

	frappe.has_permission("TS Production Entry", "read", doc=production_name, throw=True)
	if not frappe.db.exists("TS Production Entry", production_name):
		frappe.throw(_("Production Entry {0} not found.").format(escape_html(production_name)))

	# In-flight duplicate guard.
	in_flight = frappe.db.get_value(
		"TS Production Cascade Log",
		{"target_production": production_name,
		 "approval_status": ["in", ["Pending CEO Approval", "Approved"]]},
		"name",
	)
	if in_flight:
		frappe.throw(_("Production {0} already has an in-flight cascade ({1}). Wait for it to be "
		               "approved, rejected, or cancelled before starting another.").format(
			escape_html(production_name), escape_html(in_flight)))

	# Configurable rate limit (per-user, window-based, counts persisted log rows).
	rl_count = int(frappe.db.get_single_value("TS Settings", "ts_prod_cascade_rate_limit_count") or 5)
	rl_seconds = int(frappe.db.get_single_value("TS Settings", "ts_prod_cascade_rate_limit_seconds") or 172800)
	window_start = add_to_date(now_datetime(), seconds=-rl_seconds)
	used = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabTS Production Cascade Log`
		   WHERE initiated_by=%s AND initiated_at >= %s""",
		(frappe.session.user, window_start),
	)[0][0]
	if used >= rl_count:
		frappe.throw(_("Rate limit reached: {0} production cascades in the last {1}h. Try later.").format(
			rl_count, rl_seconds // 3600,
		))
	rate_limit_position = used + 1

	# Chain snapshot + HAZARD-C (so the CEO/MD sees the blast radius on the log row).
	from trustbit_ethanol.ts_gate_entry.production_cascade_engine import (
		build_chain_snapshot, detect_hazard_c,
	)
	chain = build_chain_snapshot(production_name)
	hazard = detect_hazard_c(production_name, chain)
	chain_json = json.dumps(chain, indent=2, default=str)

	# Forensic block.
	from trustbit_ethanol.ts_gate_entry.cascade_delete_forensics import (
		capture_forensic_block, serialize_forensic_block,
	)
	forensic = capture_forensic_block({"screen": client_screen, "language": client_language})
	forensic_json = serialize_forensic_block(forensic)

	# Insert log row.
	log = frappe.new_doc("TS Production Cascade Log")
	log.target_production = production_name
	log.target_work_order = chain.get("work_order")
	log.initiated_by = frappe.session.user
	log.initiated_at = now_datetime()
	log.approval_status = "Pending CEO Approval"
	log.confirm_production_name_typed = confirm_production_name_typed
	log.force_manufacture_override = int(force_manufacture)
	log.force_manufacture_confirmation_typed = (
		force_manufacture_confirmation_typed if int(force_manufacture) else "")
	log.force_dispatched_override = int(force_dispatched)
	log.force_dispatched_confirmation_typed = (
		force_dispatched_confirmation_typed if int(force_dispatched) else "")
	log.hazard_c_blocked = 1 if hazard.get("blocked") else 0
	log.hazard_c_findings_json = json.dumps(hazard.get("findings") or [], indent=2, default=str)
	log.target_chain_json = chain_json
	log.forensic_block_json = forensic_json
	log.rate_limit_position = rate_limit_position
	log.flags.ignore_permissions = True
	log.insert(ignore_permissions=True)
	log.submit()
	frappe.db.commit()

	# Pre-cascade backup (generalized writer + the production doc_map).
	try:
		from trustbit_ethanol.ts_gate_entry.cascade_delete_backup import create_pre_cascade_backup
		bkp = create_pre_cascade_backup(production_name, log.name, doc_map=_production_backup_doc_map(chain))
		_persist_log_field(log.name, "backup_filename", bkp["filename"])
		_persist_log_field(log.name, "backup_sha256", bkp["sha256"])
	except Exception as e:
		frappe.log_error(
			title=f"Production cascade pre-backup failed for {log.name}",
			message=f"{type(e).__name__}: {e}",
		)
		_transition_log_state(log.name, "Failed",
		                      extra={"execution_result_json": json.dumps({"error": f"backup failed: {e}"})})
		frappe.throw(_("Pre-cascade backup failed; cascade aborted. See Error Log."))

	# Notify CEO + MD (best-effort).
	_send_pending_approval_email(log.name)

	# Webhook (best-effort, dispatches the Pending event).
	try:
		from trustbit_ethanol.ts_gate_entry.cascade_delete_webhook import dispatch_webhook
		log_doc = frappe.get_doc("TS Production Cascade Log", log.name)
		dispatch_webhook(log_doc)
	except Exception as e:
		frappe.log_error(
			title=f"Production cascade webhook dispatch failed for {log.name}",
			message=f"{type(e).__name__}: {e}",
		)

	frappe.clear_messages()
	return {"success": True, "log_name": log.name, "rate_limit_position": rate_limit_position}


def _production_backup_doc_map(chain: dict) -> dict:
	"""Build the {label: (doctype, [names])} map for the generalized backup writer."""
	se_names = chain.get("se_names") or []
	qi_names = [q.get("name") for q in (chain.get("quality_inspections") or []) if q.get("name")]
	jc_names = [j.get("name") for j in (chain.get("job_cards") or []) if j.get("name")]
	doc_map = {
		"production_entry": ("TS Production Entry",
		                     [chain.get("production")] if chain.get("production") else []),
		"work_order": ("Work Order", [chain.get("work_order")] if chain.get("work_order") else []),
		"job_cards": ("Job Card", jc_names),
		"stock_entries": ("Stock Entry", se_names),
		"quality_inspections": ("Quality Inspection", qi_names),
	}
	return doc_map


# ---------------------------------------------------------------- 3. APPROVE (CEO/MD)


@frappe.whitelist(methods=["POST"])
@rate_limit(key="prod_cascade_approve", limit=30, seconds=60)
def approve_production_cascade(log_name: str, decision: str, reason: str = "") -> dict:
	"""CEO or MD approves or rejects a pending production cascade.
	`decision` in {'approve','reject'}. On approve: state -> Approved, engine runs -> Executed.
	On reject: state -> Rejected. Reason must be >=10 chars.
	"""
	_gate_kill_switch()
	_gate_role(_APPROVE_ROLES, "approve production cascade delete")
	_validate_log_name(log_name)
	if decision not in ("approve", "reject"):
		frappe.throw(_("decision must be 'approve' or 'reject'."))

	rows = frappe.db.sql(
		"""SELECT name, initiated_by, approval_status FROM `tabTS Production Cascade Log`
		   WHERE name=%s FOR UPDATE""",
		(log_name,), as_dict=True,
	)
	if not rows:
		frappe.throw(_("Production Cascade Log {0} not found.").format(escape_html(log_name)))
	log = rows[0]
	if log.approval_status != "Pending CEO Approval":
		frappe.throw(_("Log {0} is not in 'Pending CEO Approval' state (currently {1}).").format(
			escape_html(log_name), escape_html(log.approval_status),
		))

	# 2-person rule.
	if log.initiated_by == frappe.session.user:
		frappe.throw(_("Two-person rule violated: you initiated this request; a different CEO or MD must approve."))

	if decision == "reject":
		if len(reason.strip()) < 10:
			frappe.throw(_("Rejection reason must be at least 10 characters."))
		_transition_log_state(log_name, "Rejected", extra={
			"approved_by": frappe.session.user,
			"approved_at": now_datetime(),
			"rejection_reason": reason.strip()[:500],
		})
		_send_decision_email(log_name, "Rejected")
		frappe.clear_messages()
		return {"success": True, "approval_status": "Rejected"}

	# Approve -> mark Approved -> run engine.
	_transition_log_state(log_name, "Approved", extra={
		"approved_by": frappe.session.user,
		"approved_at": now_datetime(),
	})

	log_doc = frappe.get_doc("TS Production Cascade Log", log_name)
	from trustbit_ethanol.ts_gate_entry.production_cascade_engine import execute_production_cascade

	_approver_user = frappe.session.user
	_initiator_user = log_doc.initiated_by
	if not _initiator_user or not frappe.db.get_value("User", _initiator_user, "enabled"):
		_transition_log_state(log_name, "Failed", extra={
			"executed_at": now_datetime(),
			"execution_result_json": json.dumps({
				"error": (f"Initiator account '{_initiator_user}' is disabled or missing — "
				          "cascade aborted. Re-initiate from an enabled account."),
			}),
		})
		frappe.clear_messages()
		return {
			"success": False, "approval_status": "Failed",
			"error": (f"Initiator account '{_initiator_user}' is disabled or missing — "
			          "cascade aborted. Re-initiate from an enabled account."),
		}
	try:
		_engine_user = _resolve_executor_user(_initiator_user)
	except frappe.PermissionError as e:
		_transition_log_state(log_name, "Failed", extra={
			"executed_at": now_datetime(),
			"execution_result_json": json.dumps({
				"error": (f"Executor resolution failed for initiator '{_initiator_user}': {str(e)}"),
			}),
		})
		frappe.clear_messages()
		return {"success": False, "approval_status": "Failed", "error": str(e)}

	_persist_log_field(log_name, "executed_as", _engine_user)
	try:
		frappe.set_user(_engine_user)
		result = execute_production_cascade(
			production_name=log_doc.target_production,
			log_name=log_name,
			force_manufacture=bool(log_doc.force_manufacture_override),
			force_dispatched=bool(log_doc.force_dispatched_override),
		)
		if isinstance(result, dict):
			result["initiator"] = _initiator_user
			result["executor"] = _engine_user
			result["approver"] = _approver_user
	finally:
		# ALWAYS restore the approving user's session before post-engine steps (Lesson 176).
		frappe.set_user(_approver_user)

	now = now_datetime()
	try:
		_rw = int(frappe.db.get_single_value("TS Settings", "ts_prod_cascade_revert_window_minutes") or 0)
	except (TypeError, ValueError):
		_rw = 0
	revert_minutes = _rw if 5 <= _rw <= 60 else _REVERT_GRACE_MINUTES
	revert_expires_at = add_to_date(now, minutes=revert_minutes)
	exec_json = json.dumps(result, indent=2, default=str)

	new_state = "Executed" if result.get("success") else "Failed"
	_transition_log_state(log_name, new_state, extra={
		"executed_at": now,
		"execution_result_json": exec_json,
		"revert_window_expires_at": revert_expires_at if result.get("success") else None,
	})

	# Post-cascade orphan scan — only meaningful on success.
	if result.get("success"):
		try:
			from trustbit_ethanol.ts_gate_entry.production_cascade_engine import scan_orphans
			scan_orphans(log_name)
		except Exception as e:
			frappe.log_error(
				title=f"Production cascade post-exec scan failed for {log_name}",
				message=f"{type(e).__name__}: {e}",
			)

	# Webhook + email.
	try:
		from trustbit_ethanol.ts_gate_entry.cascade_delete_webhook import dispatch_webhook
		log_doc_after = frappe.get_doc("TS Production Cascade Log", log_name)
		dispatch_webhook(log_doc_after)
	except Exception as e:
		frappe.log_error(title=f"Production cascade webhook dispatch failed for {log_name}",
		                 message=f"{type(e).__name__}: {e}")
	try:
		_send_decision_email(log_name, new_state)
	except Exception as e:
		frappe.log_error(title=f"Production cascade decision email failed for {log_name}",
		                 message=f"{type(e).__name__}: {e}")

	frappe.clear_messages()
	return {
		"success": result.get("success"),
		"approval_status": new_state,
		"steps": result.get("steps"),
		"pre_flight": result.get("pre_flight"),
		"sweep": result.get("sweep"),
		"aborted_step": result.get("aborted_step"),
		"revert_window_expires_at": str(revert_expires_at) if result.get("success") else None,
	}


# ---------------------------------------------------------------- 4. CANCEL PENDING


@frappe.whitelist(methods=["POST"])
@rate_limit(key="prod_cascade_cancel", limit=30, seconds=60)
def cancel_pending_production_cascade(log_name: str) -> dict:
	"""Initiator, CEO, MD, or System Manager can cancel a Pending request."""
	_gate_kill_switch()
	_validate_log_name(log_name)
	rows = frappe.db.sql(
		"""SELECT name, initiated_by, approval_status FROM `tabTS Production Cascade Log`
		   WHERE name=%s FOR UPDATE""",
		(log_name,), as_dict=True,
	)
	if not rows:
		frappe.throw(_("Log {0} not found.").format(escape_html(log_name)))
	log = rows[0]
	if log.approval_status != "Pending CEO Approval":
		frappe.throw(_("Only Pending requests can be cancelled."))
	user_roles = set(frappe.get_roles(frappe.session.user))
	if frappe.session.user != log.initiated_by and user_roles.isdisjoint({"CEO", "MD", "System Manager"}):
		frappe.throw(_("Only the initiator, CEO, MD, or System Manager may cancel."), frappe.PermissionError)
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
@rate_limit(key="prod_cascade_revert", limit=10, seconds=60)
def revert_production_cascade(log_name: str) -> dict:
	"""Restore the chain DOCUMENTS from backup. Super Admin / CEO / MD AND within window.

	LOUD warning (UI): revert restores the Work Order / Job Card / Stock Entry / PE
	DOCUMENTS only. It does NOT recreate Stock Ledger / GL entries, does NOT restore
	WIP/FG bin balances, does NOT re-link the WO's produced qty. The restored Manufacture
	SE shows submitted but moved ZERO stock — a stock-valuation hole requiring a manual
	Stock Reconciliation. Prefer re-running production fresh.
	"""
	_gate_kill_switch()
	_gate_role(_REVERT_ROLES, "revert production cascade delete")
	_validate_log_name(log_name)
	rows = frappe.db.sql(
		"""SELECT * FROM `tabTS Production Cascade Log` WHERE name=%s FOR UPDATE""",
		(log_name,), as_dict=True,
	)
	if not rows:
		frappe.throw(_("Log {0} not found.").format(escape_html(log_name)))
	log_row = rows[0]
	if log_row.approval_status != "Executed":
		frappe.throw(_("Only Executed cascades can be reverted (currently {0}).").format(
			escape_html(log_row.approval_status),
		))
	now = now_datetime()
	if not log_row.revert_window_expires_at or now > log_row.revert_window_expires_at:
		frappe.throw(_("Revert window has expired. This cascade can no longer be reverted."))

	log_doc = frappe.get_doc("TS Production Cascade Log", log_name)
	from trustbit_ethanol.ts_gate_entry.cascade_delete_backup import restore_from_backup
	result = restore_from_backup(log_doc)

	if result.get("success"):
		_transition_log_state(log_name, "Reverted", extra={
			"reverted_at": now,
			"reverted_by": frappe.session.user,
			"revert_result_json": json.dumps(result, indent=2, default=str),
		})
	else:
		_persist_log_field(log_name, "revert_result_json", json.dumps(result, indent=2, default=str))

	try:
		_send_decision_email(log_name, "Reverted" if result.get("success") else "RevertFailed")
	except Exception:
		pass

	return result


# ---------------------------------------------------------------- 6. DECISION SUPPORT


@frappe.whitelist()
@rate_limit(key="prod_cascade_review_detail", limit=60, seconds=60)
def get_pending_production_review_detail(log_name: str) -> dict:
	"""Rich decision-support detail for a pending production cascade. Read-only."""
	_gate_kill_switch()
	_gate_role(_READ_ROLES, "view production cascade review detail")
	_validate_log_name(log_name)

	log = frappe.db.get_value(
		"TS Production Cascade Log", log_name,
		["target_production", "approval_status", "force_manufacture_override",
		 "force_dispatched_override", "hazard_c_blocked", "hazard_c_findings_json"],
		as_dict=True,
	)
	if not log:
		frappe.throw(_("Production Cascade Log {0} not found.").format(escape_html(log_name)))

	from trustbit_ethanol.ts_gate_entry.production_cascade_engine import build_chain_snapshot
	chain = build_chain_snapshot(log.target_production)
	pe = chain.get("production_record") or {}

	hazard_findings = []
	if log.get("hazard_c_findings_json"):
		try:
			hazard_findings = json.loads(log["hazard_c_findings_json"])
		except Exception:
			hazard_findings = []

	return _escape_dict_strings({
		"production": log.target_production,
		"production_status": pe.get("ts_variance_status"),
		"production_item": pe.get("production_item_name") or pe.get("production_item"),
		"produced_qty": flt(pe.get("actual_produced_qty")),
		"work_order": chain.get("work_order"),
		"work_order_status": chain.get("work_order_status"),
		"counts": {
			"job_cards": len(chain.get("job_cards") or []),
			"stock_entries": len(chain.get("se_names") or []),
			"submitted_stock_entries": chain.get("submitted_se_count") or 0,
			"quality_inspections": len(chain.get("quality_inspections") or []),
			"stock_ledger_entries": chain.get("stock_ledger_entries_count") or 0,
			"gl_entries": chain.get("gl_entries_count") or 0,
		},
		"release_se": chain.get("release_se"),
		"manufacture_se": chain.get("manufacture_se"),
		"surplus_se": chain.get("surplus_se"),
		"produced_rows": chain.get("produced_rows") or [],
		"hazard_c": {
			"blocked": bool(log.get("hazard_c_blocked")),
			"findings": hazard_findings,
		},
		"force_manufacture_override": int(log.force_manufacture_override or 0),
		"force_dispatched_override": int(log.force_dispatched_override or 0),
	})


@frappe.whitelist()
def run_production_integrity_scan(log_name: str = "") -> dict:
	"""On-demand integrity scan. If log_name given, scans that chain; else hash chain."""
	_gate_kill_switch()
	_gate_role(_READ_ROLES, "run production integrity scan")
	if log_name:
		_validate_log_name(log_name)
		from trustbit_ethanol.ts_gate_entry.production_cascade_engine import scan_orphans
		return scan_orphans(log_name)
	from trustbit_ethanol.ts_gate_entry.doctype.ts_production_cascade_log.ts_production_cascade_log import (
		verify_chain_integrity as _vci,
	)
	return _vci()


@frappe.whitelist()
def verify_production_chain_integrity() -> dict:
	"""Convenience alias to verify the hash chain (audit-grade, read-only)."""
	_gate_kill_switch()
	_gate_role(_READ_ROLES, "verify production chain integrity")
	from trustbit_ethanol.ts_gate_entry.doctype.ts_production_cascade_log.ts_production_cascade_log import (
		verify_chain_integrity as _vci,
	)
	return _vci()


# ---------------------------------------------------------------- internal helpers


def _persist_log_field(log_name: str, field: str, value):
	"""Set ONE field on the log with the api_caller flag set (bypasses append-only)."""
	try:
		frappe.flags.production_cascade_api_caller = True
		frappe.db.set_value("TS Production Cascade Log", log_name, field, value, update_modified=False)
		frappe.db.commit()
	finally:
		frappe.flags.production_cascade_api_caller = False


def _transition_log_state(log_name: str, new_status: str, extra: dict | None = None):
	"""Atomic state transition with append-only flag bypass."""
	updates = {"approval_status": new_status}
	if extra:
		updates.update({k: v for k, v in extra.items() if v is not None})
	try:
		frappe.flags.production_cascade_api_caller = True
		frappe.db.set_value("TS Production Cascade Log", log_name, updates, update_modified=True)
		frappe.db.commit()
	finally:
		frappe.flags.production_cascade_api_caller = False


def _resolve_alert_recipients() -> list:
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


def _render_cascade_email(log_doc, event: str) -> str:
	"""Render the Production Cascade Delete Warning email body. Falls back to a minimal
	inline body if the Email Template is missing (Lesson 238 — alert still flows)."""
	context = {"doc": log_doc, "event": event, "frappe": frappe}
	body = frappe.db.get_value("Email Template", "Production Cascade Delete Warning", "response")
	if body:
		try:
			return frappe.render_template(body, context)
		except Exception as e:
			frappe.log_error(
				title="Production cascade email template render failed",
				message=f"{type(e).__name__}: {e}",
			)
	return (
		f"<h3 style='color:#dc2626;'>BBPL Production Cascade Delete &mdash; {escape_html(event)}</h3>"
		f"<p><b>Log:</b> {escape_html(log_doc.name)}<br>"
		f"<b>Production Entry:</b> {escape_html(log_doc.target_production or '')}<br>"
		f"<b>Work Order:</b> {escape_html(log_doc.target_work_order or '')}<br>"
		f"<b>Initiated by:</b> {escape_html(log_doc.initiated_by or '')}<br>"
		f"<b>Status:</b> {escape_html(log_doc.approval_status or '')}</p>"
	)


def _notify_cascade_whatsapp(log_name: str, action: str, recipients):
	"""Fire the WhatsApp approval shim alongside the CEO/MD email. Fail-soft: never
	raises into the cascade flow; inert when the WhatsApp kill-switch is off."""
	try:
		if not recipients:
			return
		log_doc = frappe.get_doc("TS Production Cascade Log", log_name)
		from trustbit_ethanol.ts_gate_entry.ts_whatsapp_notify import whatsapp_notify_for_approval
		whatsapp_notify_for_approval(
			log_doc, action, recipients, extra={"reason": log_doc.get("rejection_reason")}
		)
	except Exception:
		pass


def _send_pending_approval_email(log_name: str):
	"""CEO + MD receive a bold-red alert when a Pending row is inserted."""
	recipients = _resolve_alert_recipients()
	if not recipients:
		return
	try:
		log_doc = frappe.get_doc("TS Production Cascade Log", log_name)
		frappe.sendmail(
			recipients=recipients,
			subject=f"[BBPL CRITICAL] Production Cascade Delete Pending: {log_doc.target_production} by {log_doc.initiated_by}",
			message=_render_cascade_email(log_doc, "PENDING APPROVAL"),
			now=False,
			delayed=True,
		)
	except Exception as e:
		frappe.log_error(
			title=f"Production cascade approval email failed for {log_name}",
			message=f"{type(e).__name__}: {e}",
		)
	_notify_cascade_whatsapp(log_name, "prod_cascade_pending", recipients)


def _send_decision_email(log_name: str, decision_label: str):
	"""CEO + MD receive notification when state transitions to a terminal label."""
	recipients = _resolve_alert_recipients()
	if not recipients:
		return
	try:
		log_doc = frappe.get_doc("TS Production Cascade Log", log_name)
		frappe.sendmail(
			recipients=recipients,
			subject=f"[BBPL CRITICAL] Production Cascade Delete {decision_label}: {log_doc.target_production}",
			message=_render_cascade_email(log_doc, decision_label.upper()),
			now=False,
			delayed=True,
		)
	except Exception as e:
		frappe.log_error(
			title=f"Production cascade decision email failed for {log_name}",
			message=f"{type(e).__name__}: {e}",
		)
	_cd_action = {"Executed": "prod_cascade_executed", "Rejected": "prod_cascade_rejected"}.get(decision_label)
	if _cd_action:
		_notify_cascade_whatsapp(log_name, _cd_action, recipients)


# ---------------------------------------------------------------- scheduler entry points

# Wired in hooks.py.scheduler_events
def scheduled_expire_production_revert_windows():
	"""`*/5 * * * *` — observability only; counts expired Executed production cascades."""
	now = frappe.utils.now_datetime()
	expired = frappe.db.sql(
		"""SELECT name FROM `tabTS Production Cascade Log`
		   WHERE docstatus = 1
		     AND approval_status = 'Executed'
		     AND revert_window_expires_at IS NOT NULL
		     AND revert_window_expires_at < %s
		     AND reverted_at IS NULL""",
		(now,), as_dict=True,
	)
	return {"count": len(expired), "expired_log_names": [r.name for r in expired]}


def scheduled_nightly_production_integrity_scan():
	"""`0 3 * * *` — nightly orphan + hash chain audit for production cascades."""
	from trustbit_ethanol.ts_gate_entry.production_cascade_integrity import nightly_orphan_scan
	return nightly_orphan_scan()
