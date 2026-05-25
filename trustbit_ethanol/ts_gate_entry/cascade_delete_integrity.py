"""Cascade Delete integrity scanner (v2.11.0 Hardening G).

Two callers:
1. Post-cascade auto-run — kicked off by cascade_delete_api.initiate_cascade
   immediately after engine returns success. Writes orphan-trail report onto
   the log row.
2. Nightly scheduler — `*/0 3 * * *` via hooks.py.scheduler_events — walks
   ALL submitted (Executed-state) log rows in the prior 30 days and re-runs
   the orphan scan. Surfaces any orphan creep that may have appeared due to
   bug fixes / migrations elsewhere.

ALSO calls `ts_cascade_delete_log.verify_chain_integrity` to detect hash
chain breaks — surfaced as a separate alert.

Lesson references: 237 (log_error kwargs), 238 (best-effort), 175 (called
in-process only; not a whitelisted endpoint here — exposed via api module).
"""

from datetime import datetime, timedelta
import json
import frappe
from frappe.utils import add_days, today


def run_orphan_scan_for_log(log_name: str) -> dict:
	"""Scan orphans for a single log row's target_token. Persists result.

	Reads `log_doc.target_token` + `log_doc.cut_point`, calls
	`cascade_delete_engine.scan_orphans` with cut-point awareness so the
	intentionally-kept docs from a partial cascade are NOT false-flagged as
	orphans (v2.11.2). Writes `integrity_scan_*` fields back to the log row.
	"""
	from trustbit_ethanol.ts_gate_entry.cascade_delete_engine import scan_orphans

	if not frappe.db.exists("TS Cascade Delete Log", log_name):
		return {"error": "log row not found", "log_name": log_name}

	row = frappe.db.get_value(
		"TS Cascade Delete Log", log_name,
		["target_token", "cut_point"],
		as_dict=True,
	) or {}
	token_name = row.get("target_token")
	if not token_name:
		return {"error": "log has no target_token", "log_name": log_name}
	# v2.11.2 — translate the log's stored "Full Chain" sentinel into the
	# engine's None convention. A partial cut_point passes through unchanged.
	cut_point = row.get("cut_point") or "Full Chain"
	engine_cut = None if cut_point == "Full Chain" else cut_point

	result = scan_orphans(token_name, cut_point=engine_cut)
	clean = bool(result.get("clean"))
	orphans_json = json.dumps(result.get("orphans") or {}, indent=2, default=str)

	# Persist via db_set (bypasses append-only via flag — caller-set if needed)
	try:
		frappe.flags.cascade_delete_api_caller = True
		frappe.db.set_value(
			"TS Cascade Delete Log", log_name,
			{
				"integrity_scan_run_at": frappe.utils.now_datetime(),
				"integrity_scan_clean": 1 if clean else 0,
				"integrity_scan_orphans_json": orphans_json,
			},
			update_modified=False,
		)
		frappe.db.commit()
	finally:
		frappe.flags.cascade_delete_api_caller = False

	if not clean:
		frappe.log_error(
			title=f"Cascade integrity scan: orphans detected for log {log_name}",
			message=f"target_token={token_name} orphans={result.get('orphans')}",
		)

	return {
		"log_name": log_name,
		"token_name": token_name,
		"clean": clean,
		"orphan_count": result.get("orphan_count", 0),
		"orphans": result.get("orphans"),
	}


def nightly_orphan_scan() -> dict:
	"""Scheduler entry point (`0 3 * * *`). Walks Executed logs in last 30 days.

	Skips already-scanned-clean rows that ran in the last 7 days (efficiency).
	Returns per-log result; logs error if any scan finds new orphans.
	"""
	thirty_days_ago = add_days(today(), -30)
	rows = frappe.db.sql(
		"""SELECT name, target_token, integrity_scan_run_at, integrity_scan_clean
		   FROM `tabTS Cascade Delete Log`
		   WHERE docstatus = 1
		     AND approval_status IN ('Executed', 'Reverted')
		     AND DATE(executed_at) >= %s
		   ORDER BY executed_at DESC""",
		(thirty_days_ago,), as_dict=True,
	)

	seven_days_ago = add_days(today(), -7)
	scanned = []
	skipped = []
	for r in rows:
		if (r.integrity_scan_clean == 1
		    and r.integrity_scan_run_at
		    and frappe.utils.getdate(r.integrity_scan_run_at) >= frappe.utils.getdate(seven_days_ago)):
			skipped.append(r.name)
			continue
		try:
			res = run_orphan_scan_for_log(r.name)
			scanned.append({"log": r.name, "clean": res.get("clean"), "orphans": res.get("orphan_count", 0)})
		except Exception as e:
			frappe.log_error(
				title=f"nightly_orphan_scan failed for {r.name}",
				message=f"{type(e).__name__}: {e}",
			)

	# Also verify hash chain integrity (audit cross-check)
	try:
		from trustbit_ethanol.ts_gate_entry.doctype.ts_cascade_delete_log.ts_cascade_delete_log import (
			verify_chain_integrity,
		)
		chain = verify_chain_integrity()
		if not chain["clean"]:
			frappe.log_error(
				title="Cascade hash chain BROKEN",
				message=f"Broken links: {chain['broken_links']}",
			)
	except Exception as e:
		frappe.log_error(
			title="Cascade hash chain verify failed",
			message=f"{type(e).__name__}: {e}",
		)

	return {
		"date": today(),
		"scanned": scanned,
		"skipped": skipped,
		"scanned_count": len(scanned),
		"skipped_count": len(skipped),
	}


def expire_revert_windows() -> dict:
	"""Scheduler entry point (`*/1 * * * *`). Marks Executed logs whose
	revert_window_expires_at < now as 'frozen' (we use a flag-like state
	change but for now just rely on the timestamp comparison — the API's
	revert endpoint re-checks the window).

	This scheduler is primarily there to surface count of expiries for
	monitoring; no destructive action.
	"""
	now = frappe.utils.now_datetime()
	expired = frappe.db.sql(
		"""SELECT name FROM `tabTS Cascade Delete Log`
		   WHERE docstatus = 1
		     AND approval_status = 'Executed'
		     AND revert_window_expires_at IS NOT NULL
		     AND revert_window_expires_at < %s
		     AND reverted_at IS NULL""",
		(now,), as_dict=True,
	)
	# State remains 'Executed' (revert is no longer accessible due to timestamp).
	# This function is observability-only; no DB writes here.
	return {"count": len(expired), "expired_log_names": [r.name for r in expired]}
