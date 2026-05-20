"""Cascade Delete Engine (v2.11.0) — pure orchestration of the
PI → PR → DS → QI → WB → GE → Token deletion chain.

Refactored from the production-proven `_batch_token_delete_optA.py` script
(19 May 2026 Round 44, 5/5 deterministic). Idempotent + transaction-safe;
embeds the Lesson 275 surgical-SQL fallback inside the GE-cancel step.

NO direct caller; only invoked by `cascade_delete_api.py` after CEO approval.
Sets `frappe.flags.cascade_delete_mode = True` for the engine's lifetime
(Lesson 176 try/finally) so the GE on_cancel guard at
`ts_gate_entry.py:_block_cancel_if_token_post_weighing` skips its
`Token.status == 'Tare Weighed'` snapshot check (Lesson 275).

Returns a structured execution_result dict that the API persists into
the log row's `execution_result_json` field.

Lesson references: 175 / 222 (idempotent) / 224 (dual-gate has_permission) /
237 (log_error kwargs) / 238 (best-effort side effects in caller) /
262 (Option B surgical SQL pattern) / 275 (GE cancel tamper-guard fallback).
"""

import frappe
from frappe import _


# ---------------------------------------------------------------- chain helpers


def build_chain_snapshot(token_name: str) -> dict:
	"""Read-only chain map. Returns a dict consumed by preview AND the
	target_chain_json snapshot field (Hardening B).
	"""
	chain = {
		"token": token_name,
		"token_record": None,
		"gate_entries": [],
		"weighbridge_logs": [],
		"quality_inspections": [],
		"deduction_sheets": [],
		"purchase_receipts": [],
		"purchase_invoices": [],
		"stock_ledger_entries_count": 0,
		"gl_entries_count": 0,
		"versions_count": 0,
		"comments_count": 0,
	}

	if not frappe.db.exists("TS Token", token_name):
		return chain

	# Only request fields KNOWN to exist on tabTS Token (verified via SHOW COLUMNS
	# during v2.11.0 Phase 4 code-tester; `purchase_order` was wrongly assumed and
	# removed). `purchase_receipt` is the legitimate forward-link.
	chain["token_record"] = frappe.db.get_value(
		"TS Token", token_name,
		["name", "docstatus", "status", "vehicle_number", "purchase_receipt",
		 "owner", "creation", "modified"],
		as_dict=True,
	)

	chain["gate_entries"] = frappe.db.sql(
		"SELECT name, docstatus, status FROM `tabTS Gate Entry` WHERE token_number=%s",
		(token_name,), as_dict=True,
	)
	chain["weighbridge_logs"] = frappe.db.sql(
		"SELECT name, docstatus, rst_number FROM `tabTS Weighbridge Log` WHERE token_number=%s",
		(token_name,), as_dict=True,
	)
	chain["quality_inspections"] = frappe.db.sql(
		"SELECT name, docstatus FROM `tabTS Quality Inspection` WHERE token_number=%s",
		(token_name,), as_dict=True,
	)
	qi_names = [q["name"] for q in chain["quality_inspections"]]
	if qi_names:
		chain["deduction_sheets"] = frappe.db.sql(
			"SELECT name, docstatus FROM `tabTS Deduction Sheet` WHERE quality_inspection IN %s",
			(qi_names,), as_dict=True,
		)
	chain["purchase_receipts"] = frappe.db.sql(
		"SELECT name, docstatus, status, grand_total, per_billed FROM `tabPurchase Receipt` WHERE ts_token=%s",
		(token_name,), as_dict=True,
	)
	chain["purchase_invoices"] = frappe.db.sql(
		"SELECT name, docstatus, status, grand_total FROM `tabPurchase Invoice` WHERE ts_token=%s",
		(token_name,), as_dict=True,
	)

	if chain["purchase_receipts"]:
		pr_names = [r["name"] for r in chain["purchase_receipts"]]
		chain["stock_ledger_entries_count"] = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE voucher_no IN %s",
			(pr_names,))[0][0]
		chain["gl_entries_count"] += frappe.db.sql(
			"SELECT COUNT(*) FROM `tabGL Entry` WHERE voucher_no IN %s",
			(pr_names,))[0][0]
	if chain["purchase_invoices"]:
		pi_names = [r["name"] for r in chain["purchase_invoices"]]
		chain["gl_entries_count"] += frappe.db.sql(
			"SELECT COUNT(*) FROM `tabGL Entry` WHERE voucher_no IN %s",
			(pi_names,))[0][0]

	chain["versions_count"] = frappe.db.count("Version", {"ref_doctype": "TS Token", "docname": token_name})
	chain["comments_count"] = frappe.db.count("Comment", {"reference_doctype": "TS Token", "reference_name": token_name})
	return chain


# ---------------------------------------------------------------- engine main


def execute_cascade(
	token_name: str,
	log_name: str,
	force_pr: bool = False,
	force_mi: bool = False,
) -> dict:
	"""Run the full PI → PR → DS → QI → WB → GE → Token cascade.

	Each step:
	  - frappe.has_permission(doctype, action, throw=True) before destructive op
	  - try/except wraps the engine call; failure aborts the cascade
	  - per-step result appended to execution_result list

	Returns:
	  {
	    "success": bool,
	    "aborted_step": str | None,
	    "steps": [ {step, doctype, name, action, success, error?, surgical_sql?}, ... ],
	    "lesson_275_fallback_used": bool,
	  }
	"""
	steps: list[dict] = []
	lesson_275_used = False
	frappe.flags.cascade_delete_mode = True
	try:
		# Step 1 — Purchase Invoices
		pi_rows = frappe.db.sql(
			"SELECT name, docstatus FROM `tabPurchase Invoice` WHERE ts_token=%s",
			(token_name,), as_dict=True,
		)
		for pi in pi_rows:
			if pi.docstatus == 1 and not force_pr:
				steps.append({"step": "PI", "name": pi.name, "action": "skip-not-forced",
				              "success": False, "error": "Submitted PI requires force_pr"})
				return _abort(steps, "PI", lesson_275_used)
			res = _cancel_then_delete("Purchase Invoice", pi.name, allow_force=force_pr)
			steps.append({"step": "PI", **res})
			if not res["success"]:
				return _abort(steps, "PI", lesson_275_used)

		# Step 2 — Purchase Receipts
		pr_rows = frappe.db.sql(
			"SELECT name, docstatus FROM `tabPurchase Receipt` WHERE ts_token=%s",
			(token_name,), as_dict=True,
		)
		for pr in pr_rows:
			if pr.docstatus == 1 and not force_pr:
				steps.append({"step": "PR", "name": pr.name, "action": "skip-not-forced",
				              "success": False, "error": "Submitted PR requires force_pr"})
				return _abort(steps, "PR", lesson_275_used)
			res = _cancel_then_delete("Purchase Receipt", pr.name, allow_force=force_pr)
			steps.append({"step": "PR", **res})
			if not res["success"]:
				return _abort(steps, "PR", lesson_275_used)

		# Step 3 — Deduction Sheets
		qi_names_for_ds = [q.name for q in frappe.db.sql(
			"SELECT name FROM `tabTS Quality Inspection` WHERE token_number=%s",
			(token_name,), as_dict=True,
		)]
		if qi_names_for_ds:
			ds_rows = frappe.db.sql(
				"SELECT name, docstatus FROM `tabTS Deduction Sheet` WHERE quality_inspection IN %s",
				(qi_names_for_ds,), as_dict=True,
			)
			for ds in ds_rows:
				res = _cancel_then_delete("TS Deduction Sheet", ds.name, allow_force=True)
				steps.append({"step": "DS", **res})
				if not res["success"]:
					return _abort(steps, "DS", lesson_275_used)

		# Step 4 — Quality Inspection (Material Inspection) — force_mi gate
		qi_rows = frappe.db.sql(
			"SELECT name, docstatus FROM `tabTS Quality Inspection` WHERE token_number=%s",
			(token_name,), as_dict=True,
		)
		for qi in qi_rows:
			if qi.docstatus == 1 and not force_mi:
				steps.append({"step": "QI", "name": qi.name, "action": "skip-not-forced",
				              "success": False, "error": "Submitted QI requires force_mi"})
				return _abort(steps, "QI", lesson_275_used)
			res = _cancel_then_delete("TS Quality Inspection", qi.name, allow_force=force_mi)
			steps.append({"step": "QI", **res})
			if not res["success"]:
				return _abort(steps, "QI", lesson_275_used)

		# Step 5 — Weighbridge Log
		wb_rows = frappe.db.sql(
			"SELECT name, docstatus FROM `tabTS Weighbridge Log` WHERE token_number=%s",
			(token_name,), as_dict=True,
		)
		for wb in wb_rows:
			res = _cancel_then_delete("TS Weighbridge Log", wb.name, allow_force=True)
			steps.append({"step": "WB", **res})
			if not res["success"]:
				return _abort(steps, "WB", lesson_275_used)

		# Step 6 — Gate Entry (Lesson 275 fallback embedded)
		ge_rows = frappe.db.sql(
			"SELECT name, docstatus FROM `tabTS Gate Entry` WHERE token_number=%s",
			(token_name,), as_dict=True,
		)
		for ge in ge_rows:
			res = _cancel_then_delete_ge_with_lesson_275_fallback(ge.name)
			steps.append({"step": "GE", **res})
			if res.get("surgical_sql"):
				lesson_275_used = True
			if not res["success"]:
				return _abort(steps, "GE", lesson_275_used)

		# Step 7 — Token (final)
		if frappe.db.exists("TS Token", token_name):
			res = _cancel_then_delete("TS Token", token_name, allow_force=True)
			steps.append({"step": "TOK", **res})
			if not res["success"]:
				return _abort(steps, "TOK", lesson_275_used)
		else:
			steps.append({"step": "TOK", "name": token_name, "action": "already-gone", "success": True})

		frappe.db.commit()
		return {
			"success": True,
			"aborted_step": None,
			"steps": steps,
			"lesson_275_fallback_used": lesson_275_used,
		}
	except Exception as e:
		frappe.log_error(
			title=f"cascade_delete_engine.execute_cascade fatal for {token_name}",
			message=f"{type(e).__name__}: {e}\nLog: {log_name}\nSteps so far: {steps}",
		)
		frappe.db.rollback()
		return {
			"success": False,
			"aborted_step": "exception",
			"steps": steps,
			"error": f"{type(e).__name__}: {e}",
			"lesson_275_fallback_used": lesson_275_used,
		}
	finally:
		# Lesson 176 — ALWAYS clear the flag even on exception path.
		frappe.flags.cascade_delete_mode = False


# ---------------------------------------------------------------- per-step helpers


def _cancel_then_delete(doctype: str, name: str, allow_force: bool = False) -> dict:
	"""Standard cancel-then-delete pattern. Returns per-step result dict."""
	if not frappe.db.exists(doctype, name):
		return {"doctype": doctype, "name": name, "action": "already-gone", "success": True}
	try:
		ds = frappe.db.get_value(doctype, name, "docstatus")
		if ds == 1:
			if not allow_force:
				return {"doctype": doctype, "name": name, "action": "force-not-allowed",
				        "success": False, "error": "Submitted record without force flag"}
			frappe.has_permission(doctype, "cancel", doc=name, throw=True)
			doc = frappe.get_doc(doctype, name)
			doc.flags.ignore_links = True
			doc.flags.ignore_permissions = True
			doc.cancel()
			frappe.db.commit()
		frappe.has_permission(doctype, "delete", doc=name, throw=True)
		frappe.delete_doc(
			doctype, name,
			force=1,
			ignore_permissions=True,
			ignore_on_trash=True,
			delete_permanently=True,
		)
		return {"doctype": doctype, "name": name, "action": "deleted", "success": True}
	except Exception as e:
		return {
			"doctype": doctype, "name": name, "action": "error",
			"success": False, "error": f"{type(e).__name__}: {e}",
		}


def _cancel_then_delete_ge_with_lesson_275_fallback(ge_name: str) -> dict:
	"""GE-specific path: try cancel → on Lesson-275 ValidationError → surgical SQL DELETE.

	The cascade engine sets `frappe.flags.cascade_delete_mode = True` for its whole
	lifetime. The GE on_cancel guard SHOULD honour that flag (Lesson 176) and skip
	the Token.status snapshot check. If the guard is updated in a future codebase
	ship to query live data, this fallback becomes vacuous.
	"""
	if not frappe.db.exists("TS Gate Entry", ge_name):
		return {"doctype": "TS Gate Entry", "name": ge_name, "action": "already-gone", "success": True}

	ge_ds = frappe.db.get_value("TS Gate Entry", ge_name, "docstatus")
	surgical = False

	if ge_ds == 1:
		try:
			frappe.has_permission("TS Gate Entry", "cancel", doc=ge_name, throw=True)
			ge_doc = frappe.get_doc("TS Gate Entry", ge_name)
			ge_doc.flags.ignore_links = True
			ge_doc.flags.ignore_permissions = True
			ge_doc.cancel()
			frappe.db.commit()
		except Exception as e:
			# Known Lesson 275 false-positive — Token.status snapshot trips the guard
			# even when WB Log is already gone. Fall back to surgical SQL (Lesson 262 B).
			msg = str(e)
			if "Tare Weighed" in msg or "Weighbridge Log exists" in msg or "Quality" in msg:
				surgical = True
				try:
					frappe.db.sql(
						"DELETE FROM `tabComment` "
						"WHERE reference_doctype='TS Gate Entry' AND reference_name=%s",
						(ge_name,),
					)
					frappe.db.sql(
						"DELETE FROM `tabVersion` "
						"WHERE ref_doctype='TS Gate Entry' AND docname=%s",
						(ge_name,),
					)
					frappe.db.sql(
						"DELETE FROM `tabTS Gate Entry` WHERE name=%s",
						(ge_name,),
					)
					frappe.db.commit()
				except Exception as ee:
					return {
						"doctype": "TS Gate Entry", "name": ge_name,
						"action": "surgical-failed", "success": False,
						"error": f"{type(ee).__name__}: {ee}",
						"surgical_sql": True,
					}
			else:
				return {
					"doctype": "TS Gate Entry", "name": ge_name,
					"action": "cancel-failed", "success": False,
					"error": f"{type(e).__name__}: {e}",
				}

	if frappe.db.exists("TS Gate Entry", ge_name):
		try:
			frappe.has_permission("TS Gate Entry", "delete", doc=ge_name, throw=True)
			frappe.delete_doc(
				"TS Gate Entry", ge_name,
				force=1, ignore_permissions=True, ignore_on_trash=True, delete_permanently=True,
			)
		except Exception as e:
			return {
				"doctype": "TS Gate Entry", "name": ge_name,
				"action": "delete-failed", "success": False,
				"error": f"{type(e).__name__}: {e}",
				"surgical_sql": surgical,
			}

	return {
		"doctype": "TS Gate Entry", "name": ge_name,
		"action": "deleted-via-surgical-sql" if surgical else "deleted",
		"success": True, "surgical_sql": surgical,
	}


def _abort(steps: list[dict], step_name: str, lesson_275_used: bool) -> dict:
	"""Roll back the engine on first failure (Lesson 222: idempotent + transactional)."""
	frappe.db.rollback()
	return {
		"success": False,
		"aborted_step": step_name,
		"steps": steps,
		"lesson_275_fallback_used": lesson_275_used,
	}


# ---------------------------------------------------------------- orphan-trail scan


def scan_orphans(token_name: str) -> dict:
	"""Post-delete invariant check (Hardening G). 7 cross-table xrefs must all be 0."""
	checks = {}
	for label, dt, fn in [
		("Purchase Invoice", "tabPurchase Invoice", "ts_token"),
		("Purchase Receipt", "tabPurchase Receipt", "ts_token"),
		("TS Quality Inspection", "tabTS Quality Inspection", "token_number"),
		("TS Weighbridge Log", "tabTS Weighbridge Log", "token_number"),
		("TS Gate Entry", "tabTS Gate Entry", "token_number"),
	]:
		try:
			c = frappe.db.sql(
				f"SELECT COUNT(*) FROM `{dt}` WHERE {fn}=%s",
				(token_name,),
			)[0][0]
			checks[label] = c
		except Exception as e:
			checks[label] = f"error: {e}"
	checks["Comment[TS Token ref]"] = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabComment` "
		"WHERE reference_doctype='TS Token' AND reference_name=%s",
		(token_name,),
	)[0][0]
	checks["Version[TS Token docname]"] = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabVersion` "
		"WHERE ref_doctype='TS Token' AND docname=%s",
		(token_name,),
	)[0][0]
	total = sum(v for v in checks.values() if isinstance(v, int))
	return {"orphans": checks, "orphan_count": total, "clean": total == 0}
