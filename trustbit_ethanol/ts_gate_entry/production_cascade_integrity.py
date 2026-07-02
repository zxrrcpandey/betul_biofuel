"""Production Cascade Delete integrity scanner — manufacturing sibling of
cascade_delete_integrity.py. Reuses that skeleton (nightly-walk + 7-day-skip +
hash-chain cross-check); the orphan SCAN BODY is production-specific and lives in
production_cascade_engine.scan_orphans (config = full physical delete -> clean ==
every chain-voucher count is 0).

Two callers:
1. Post-cascade auto-run — production_cascade_api.approve_production_cascade calls
   engine.scan_orphans directly after a successful execute.
2. Nightly scheduler — `0 3 * * *` via hooks.py — walks Executed-state log rows in
   the prior 30 days, skipping ones scanned-clean in the last 7 days, and re-runs.

ALSO cross-checks ts_production_cascade_log.verify_chain_integrity for hash chain
breaks.

Lesson references: 237 (log_error kwargs), 238 (best-effort).
"""

import frappe
from frappe.utils import add_days, today


def nightly_orphan_scan() -> dict:
	"""Scheduler entry point (`0 3 * * *`). Walks Executed/Reverted production cascade
	logs in the last 30 days; skips rows scanned-clean in the last 7 days.
	"""
	from trustbit_ethanol.ts_gate_entry.production_cascade_engine import scan_orphans

	thirty_days_ago = add_days(today(), -30)
	rows = frappe.db.sql(
		"""SELECT name, target_production, integrity_scan_run_at, integrity_scan_clean
		   FROM `tabTS Production Cascade Log`
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
			res = scan_orphans(r.name)
			scanned.append({"log": r.name, "clean": res.get("clean"), "orphans": res.get("orphan_count", 0)})
		except Exception as e:
			frappe.log_error(
				title=f"production nightly_orphan_scan failed for {r.name}",
				message=f"{type(e).__name__}: {e}",
			)

	# Hash chain cross-check.
	try:
		from trustbit_ethanol.ts_gate_entry.doctype.ts_production_cascade_log.ts_production_cascade_log import (
			verify_chain_integrity,
		)
		chain = verify_chain_integrity()
		if not chain["clean"]:
			frappe.log_error(
				title="Production cascade hash chain BROKEN",
				message=f"Broken links: {chain['broken_links']}",
			)
	except Exception as e:
		frappe.log_error(
			title="Production cascade hash chain verify failed",
			message=f"{type(e).__name__}: {e}",
		)

	return {
		"date": today(),
		"scanned": scanned,
		"skipped": skipped,
		"scanned_count": len(scanned),
		"skipped_count": len(skipped),
	}
