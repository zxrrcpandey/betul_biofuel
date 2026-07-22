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
	include_departments: int = 0,
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

	# Multiple-flow runs carry an auto Material Request + department consumption
	# entries that this cascade does NOT yet chain/backup/sweep (audit HIGH, 3 Jul)
	# — deleting one would leave a live re-releasable MR + orphaned dept entries.
	# Hard-block until the engine supports them.
	if frappe.db.get_value("TS Production Entry", production_name, "flow_type") == "Multiple":
		frappe.throw(_(
			"Production {0} is a MULTIPLE-flow run (auto Material Request + department "
			"entries). Cascade delete does not support Multiple-flow runs yet — cancel "
			"its documents manually or contact IT."
		).format(escape_html(production_name)))

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
	dept_cats = []
	if int(include_departments):
		cats = set(frappe.get_all("TS Production Department Entry",
			filters={"production_entry": production_name}, pluck="category", limit=0))
		# Exclude categories whose only slots are Cancelled (a prior reject_release leaves
		# the rows) — don't ask a department with no live stake to vote (22 Jul).
		cats |= set(frappe.get_all("TS Production Release Slot",
			filters={"production_entry": production_name,
			         "status": ["!=", "Cancelled"]}, pluck="category", limit=0))
		dept_cats = sorted(cats)
		log.include_departments = 1
		for c in dept_cats:
			log.append("dept_votes", {"category": c, "decision": "Pending"})
	log.flags.ignore_permissions = True
	log.insert(ignore_permissions=True)
	for c in dept_cats:
		try:
			from trustbit_ethanol.ts_gate_entry.ts_production_notify import notify_category
			notify_category(c,
				_("Delete request: confirm removal of your entries — {0}").format(escape_html(production_name)),
				_("The Production Manager requested cascade deletion of run {0} INCLUDING "
				  "department entries. Open cascade log {1} and vote Yes/No on deleting "
				  "your department's records. The CEO decides finally.").format(
				  escape_html(production_name), escape_html(log.name)),
				ref_doctype="TS Production Cascade Log", ref_name=log.name)
		except Exception:
			frappe.clear_messages()
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
	"""Build the {label: (doctype, [names])} map for the generalized backup writer.

	Includes the run's department entries, their Material-Issue Stock Entries, and its
	release slots so a within-window revert can restore whatever a dept teardown deleted
	(22 Jul — these were previously OUTSIDE the backup, making dept deletions
	unrecoverable). Captured ALWAYS, even when include_departments=0: revert's re-insert
	is idempotent and skips anything that still exists, so backing them up is harmless
	when nothing was torn down."""
	se_names = chain.get("se_names") or []
	qi_names = [q.get("name") for q in (chain.get("quality_inspections") or []) if q.get("name")]
	jc_names = [j.get("name") for j in (chain.get("job_cards") or []) if j.get("name")]
	run = chain.get("production")
	dept_entries, dept_ses, slots = [], [], []
	if run:
		for de in frappe.get_all("TS Production Department Entry",
				filters={"production_entry": run}, fields=["name", "stock_entry"], limit=0):
			dept_entries.append(de.name)
			if de.stock_entry:
				dept_ses.append(de.stock_entry)
		slots = frappe.get_all("TS Production Release Slot",
				filters={"production_entry": run}, pluck="name", limit=0)
	doc_map = {
		"production_entry": ("TS Production Entry", [run] if run else []),
		"work_order": ("Work Order", [chain.get("work_order")] if chain.get("work_order") else []),
		"job_cards": ("Job Card", jc_names),
		"stock_entries": ("Stock Entry", se_names),
		"quality_inspections": ("Quality Inspection", qi_names),
		"dept_stock_entries": ("Stock Entry", dept_ses),
		"department_entries": ("TS Production Department Entry", dept_entries),
		"release_slots": ("TS Production Release Slot", slots),
	}
	return doc_map


# ---------------------------------------------------------------- 3. APPROVE (CEO/MD)


@frappe.whitelist(methods=["POST"])
@rate_limit(key="prod_cascade_vote", limit=60, seconds=60)
def cascade_department_vote(log_name: str, category: str, decision: str) -> dict:
	"""A department recipient votes Yes/No on deleting THEIR entries for a pending
	cascade delete. One vote per category; the CEO may override later.

	Hardened 22 Jul:
	  - Recipient-gated to the caller's OWN category (unchanged), THEN a FOR UPDATE lock
	    on the log serializes every vote on it — no lost/duplicate 'all voted' CEO alert
	    and no last-writer-wins overwrite of a No by a Yes.
	  - Error strings never leak a log's approval-status enum to a caller whose category
	    is not part of the request: the membership check runs FIRST and the missing-log,
	    no-departments, and not-your-category cases all return the SAME generic message,
	    so the endpoint can't be used to enumerate cascade-log statuses.
	  - Rate-limited (was the only cascade endpoint without a limit)."""
	_gate_kill_switch()
	_validate_log_name(log_name)
	if decision not in ("Yes", "No"):
		frappe.throw(_("decision must be 'Yes' or 'No'."))
	from trustbit_ethanol.ts_gate_entry import ts_production_dept as dept
	dept._recipient_gate(category)  # recipients of THIS category (or admin) only

	# Serialize every vote on this log (kills the lost/duplicate-alert + overwrite races).
	log_rows = frappe.db.sql(
		"""SELECT approval_status, include_departments
		   FROM `tabTS Production Cascade Log` WHERE name=%s FOR UPDATE""",
		(log_name,), as_dict=True)
	# Category-membership BEFORE any status disclosure — a caller whose category is not in
	# this request gets ONE generic message regardless of the log's real state.
	_not_yours = _("Delete request {0} does not include your department, or no longer "
	               "exists.").format(escape_html(log_name))
	if not log_rows or not int(log_rows[0].include_departments or 0):
		frappe.throw(_not_yours)
	log_row = log_rows[0]
	vote_rows = frappe.db.sql(
		"""SELECT name, decision FROM `tabTS Production Cascade Dept Vote`
		   WHERE parent=%s AND category=%s""", (log_name, category), as_dict=True)
	if not vote_rows:
		frappe.throw(_not_yours)
	vote = vote_rows[0]
	# Category confirmed — only now is it safe to speak of the request's state (generically).
	if log_row.approval_status != "Pending CEO Approval":
		frappe.throw(_("Delete request {0} is no longer open for voting.").format(escape_html(log_name)))
	if vote.decision != "Pending":
		frappe.throw(_("Your department already voted: {0}.").format(escape_html(vote.decision)))

	frappe.db.set_value("TS Production Cascade Dept Vote", vote.name, {
		"decision": decision, "decided_by": frappe.session.user,
		"decided_at": now_datetime()}, update_modified=False)
	frappe.get_doc("TS Production Cascade Log", log_name).add_comment(
		"Comment", _("Department {0} voted {1} on deleting its entries (by {2}).").format(
			escape_html(category), escape_html(decision), escape_html(frappe.session.user)))
	# Recompute pending from a FRESH read under the log lock (never a stale in-memory copy).
	pending = frappe.db.sql_list(
		"""SELECT category FROM `tabTS Production Cascade Dept Vote`
		   WHERE parent=%s AND decision='Pending'""", (log_name,))
	if not pending:
		for u in frappe.get_all("Has Role", filters={"role": ["in", ["CEO", "MD"]]},
		                        pluck="parent", limit=0):
			if frappe.db.get_value("User", u, "enabled"):
				try:
					frappe.get_doc({"doctype": "Notification Log", "for_user": u, "type": "Alert",
						"document_type": "TS Production Cascade Log", "document_name": log_name,
						"subject": _("All departments voted — cascade {0} awaits your decision").format(
							escape_html(log_name))}).insert(ignore_permissions=True)
				except Exception:
					frappe.clear_messages()
	frappe.db.commit()
	return {"success": True, "pending_departments": pending}


@frappe.whitelist()
def get_my_cascade_votes() -> list:
	"""Pending cascade-delete requests the session user may vote on — one row per
	(log, category) where the user is a recipient of the category and the vote is still
	Pending. Recipient-gated (NOT log-read-perm gated) so a department operator who lacks
	TS Production Cascade Log read can still see + act on their own vote. Feeds the
	Production Logging 'Delete requests — confirm' card."""
	# Fail SOFT (return []), never throw: this read runs on EVERY Production Logging page
	# load, and the cascade tool ships disabled by default (independent kill switch). Throwing
	# here (as _gate_kill_switch does) would pop a permission-error modal on every load
	# whenever cascade is off — the sibling get_my_release_slots deliberately never gates.
	try:
		if not int(frappe.db.get_single_value("TS Settings", "ts_prod_cascade_enabled") or 0):
			return []
	except Exception:
		return []
	from trustbit_ethanol.ts_gate_entry import ts_production_dept as dept
	user_cats = None if dept._is_admin() else set(dept._user_categories())
	if user_cats is not None and not user_cats:
		return []
	rows = frappe.db.sql("""
		SELECT dv.parent AS log_name, dv.category, dv.decision,
		       log.target_production AS production, log.initiated_by, log.initiated_at
		FROM `tabTS Production Cascade Dept Vote` dv
		JOIN `tabTS Production Cascade Log` log ON log.name = dv.parent
		WHERE dv.decision = 'Pending'
		  AND log.approval_status = 'Pending CEO Approval'
		  AND log.include_departments = 1
	""", as_dict=True)
	out = []
	for r in rows:
		if user_cats is not None and r.category not in user_cats:
			continue
		r["initiated_at"] = str(r.initiated_at or "")
		out.append(r)
	# Return RAW (no _escape_dict_strings): `category` + `log_name` are IDENTITY keys the
	# vote card posts straight back to cascade_department_vote, where they must match the
	# stored names — HTML-escaping a category like "Power & Utilities" would break the
	# round-trip. The card escapes every field with esc() at display time (XSS-safe).
	return out


def _cascade_dept_teardown(log_doc):
	"""On CEO approve: per department, final = ceo_override or the dept decision
	(Pending/blank = No = KEEP, partial delete).

	Yes -> CANCEL the dept entries' Material-Issue Stock Entries (reverses their stock)
	and delete the dept entries + that category's release slots. The cancel runs ELEVATED
	to Administrator (via system_session) — a dept SE's on_cancel drives a nested Project
	cost roll-up whose permission check honours neither ignore_permissions nor a CEO/MD
	role (the exact reason the engine elevates). The CANCELLED Stock Entry doc is KEPT as
	the reversal's audit record — deleting it would orphan its ledger rows, which no scan
	covers for dept SEs. If a cancel FAILS the owning entry is KEPT (never delete an entry
	whose live stock movement still stands) and the failure is logged + surfaced, not
	swallowed. No -> keep + annotate. Returns a summary dict.
	"""
	from trustbit_ethanol.ts_gate_entry.ts_production_wo import system_session
	run = log_doc.target_production
	summary = {"deleted": [], "kept": [], "failed": []}
	for v in (log_doc.dept_votes or []):
		final = (v.ceo_override or "").strip() or ("Yes" if v.decision == "Yes" else "No")
		ents = frappe.get_all("TS Production Department Entry",
			filters={"production_entry": run, "category": v.category},
			fields=["name", "stock_entry"], limit=0)
		slots = frappe.get_all("TS Production Release Slot",
			filters={"production_entry": run, "category": v.category}, pluck="name", limit=0)
		if final != "Yes":
			for e in ents:
				try:
					frappe.get_doc("TS Production Department Entry", e.name).add_comment(
						"Comment", _("KEPT by department decision — production run {0} was "
						"deleted via cascade {1}.").format(escape_html(run),
						escape_html(log_doc.name)))
				except Exception:
					frappe.clear_messages()
			summary["kept"].append(v.category)
			continue
		cancel_failed = False
		for e in ents:
			if e.stock_entry and frappe.db.exists("Stock Entry", e.stock_entry):
				se = frappe.get_doc("Stock Entry", e.stock_entry)
				if se.docstatus == 1:
					try:
						with system_session():  # nested Project cost roll-up needs Administrator
							se.flags.ignore_permissions = True
							se.cancel()
					except Exception:
						frappe.log_error(
							title="Cascade dept teardown: Stock Entry cancel FAILED",
							message=(f"run={run} category={v.category} se={e.stock_entry}: "
							         f"{frappe.get_traceback()}"))
						frappe.clear_messages()
						cancel_failed = True
						try:
							frappe.get_doc("TS Production Department Entry", e.name).add_comment(
								"Comment", _("DELETE BLOCKED — its Stock Entry {0} could not be "
								"cancelled during cascade {1}; entry KEPT so the stock movement "
								"stays traceable. Cancel {0} manually, then re-run.").format(
								escape_html(e.stock_entry), escape_html(log_doc.name)))
						except Exception:
							frappe.clear_messages()
						continue  # do NOT delete an entry whose SE still stands
			# SE is cancelled (or never existed) — safe to delete the owning entry.
			frappe.delete_doc("TS Production Department Entry", e.name, force=1,
			                  ignore_permissions=True)
		if cancel_failed:
			# Leave the slots too — the category isn't fully torn down.
			summary["failed"].append(v.category)
			continue
		for sl in slots:
			frappe.delete_doc("TS Production Release Slot", sl, force=1,
			                  ignore_permissions=True)
		summary["deleted"].append(v.category)
	log_doc.add_comment("Comment", _("Department teardown: deleted {0}; kept {1}; FAILED {2}.").format(
		escape_html(", ".join(summary["deleted"]) or "—"),
		escape_html(", ".join(summary["kept"]) or "—"),
		escape_html(", ".join(summary["failed"]) or "—")))
	return summary


@frappe.whitelist(methods=["POST"])
@rate_limit(key="prod_cascade_approve", limit=30, seconds=60)
def approve_production_cascade(log_name: str, decision: str, reason: str = "", dept_overrides: str = "") -> dict:
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

	# Parse + validate dept_overrides BEFORE the (committed) Approved transition, so a
	# malformed payload throws while the log is still Pending + re-approvable — never
	# AFTER the commit (which stranded the log in Approved with the engine never run,
	# and no API path back). 22 Jul.
	ceo_overrides = {}
	if (dept_overrides or "").strip():
		try:
			_raw = json.loads(dept_overrides)
		except Exception:
			frappe.throw(_('dept_overrides must be valid JSON, e.g. {"Boiler": "Yes"}.'))
		if not isinstance(_raw, dict):
			frappe.throw(_("dept_overrides must be a JSON object mapping category -> 'Yes'/'No'."))
		for _cat, _val in _raw.items():
			if _val in (None, ""):
				continue
			if not isinstance(_val, str) or _val.strip() not in ("Yes", "No"):
				frappe.throw(_("dept_overrides value for {0} must be 'Yes' or 'No'.").format(
					escape_html(str(_cat))))
			ceo_overrides[str(_cat)] = _val.strip()

	# Approve -> mark Approved -> run engine.
	_transition_log_state(log_name, "Approved", extra={
		"approved_by": frappe.session.user,
		"approved_at": now_datetime(),
	})

	log_doc = frappe.get_doc("TS Production Cascade Log", log_name)
	if log_doc.get("include_departments") and ceo_overrides:
		for v in (log_doc.dept_votes or []):
			o = ceo_overrides.get(v.category)
			if o in ("Yes", "No"):
				frappe.db.set_value("TS Production Cascade Dept Vote", v.name,
				                    "ceo_override", o, update_modified=False)
				v.ceo_override = o
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
	# 20 Jul — department teardown ONLY after the engine succeeded (an engine
	# failure must never leave dept records deleted while the run survives).
	if result.get("success") and log_doc.get("include_departments"):
		try:
			_cascade_dept_teardown(log_doc)
		except Exception:
			frappe.log_error(title="Cascade dept teardown failed",
			                 message=f"{log_name}: {frappe.get_traceback()}")
			frappe.clear_messages()
	# 22 Jul — the run is physically deleted, so ANY still-Pending release slot pointing
	# at it is a zombie (a department would click Release and hit DoesNotExist). Void them
	# for EVERY successful cascade (the include_departments=0 default path leaves them all).
	if result.get("success"):
		try:
			from trustbit_ethanol.ts_gate_entry.ts_production_dept_release import (
				void_run_slots_for_deleted_run,
			)
			void_run_slots_for_deleted_run(log_doc.target_production)
		except Exception:
			frappe.log_error(title="Cascade slot cleanup failed",
			                 message=f"{log_name}: {frappe.get_traceback()}")
			frappe.clear_messages()

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
		 "force_dispatched_override", "hazard_c_blocked", "hazard_c_findings_json",
		 "include_departments"],
		as_dict=True,
	)
	if not log:
		frappe.throw(_("Production Cascade Log {0} not found.").format(escape_html(log_name)))

	dept_votes = []
	if int(log.get("include_departments") or 0):
		dept_votes = frappe.get_all("TS Production Cascade Dept Vote",
			filters={"parent": log_name},
			fields=["category", "decision", "ceo_override", "decided_by"],
			order_by="idx", limit=0)

	from trustbit_ethanol.ts_gate_entry.production_cascade_engine import build_chain_snapshot
	chain = build_chain_snapshot(log.target_production)
	pe = chain.get("production_record") or {}

	hazard_findings = []
	if log.get("hazard_c_findings_json"):
		try:
			hazard_findings = json.loads(log["hazard_c_findings_json"])
		except Exception:
			hazard_findings = []

	detail = _escape_dict_strings({
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
		"include_departments": int(log.get("include_departments") or 0),
	})
	# dept_votes attached RAW (NOT through _escape_dict_strings): each row's `category` is
	# the IDENTITY key the CEO override dialog echoes back as a dept_overrides key, and it
	# must match the stored category name (escaping "Power & Utilities" would silently drop
	# the override). The approve dialog esc()'s every field — including the data-cat
	# attribute — at display, so this stays XSS-safe.
	detail["dept_votes"] = dept_votes
	return detail


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
