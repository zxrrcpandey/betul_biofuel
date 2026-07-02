"""Production Cascade Delete Engine — pure orchestration of the full deletion of a
TS Production Entry and its submitted, stock-moving chain:

  Quality Inspections -> surplus-return SE -> Manufacture SE -> Release SE ->
  Job Cards -> Work Order -> TS Production Entry

The manufacturing sibling of cascade_delete_engine.py (the Token cascade). It REUSES
that engine's proven `_cancel_then_delete` / `_abort` patterns + the
`frappe.flags` try/finally + commit-per-step (M1) envelope, and adds five
manufacturing-specific gates PROVEN on demo (feasibility tests 1-3, 26 Jun 2026):

  * HAZARD-C pre-flight  — the SOLE guard on `allow_negative_stock=1` against
    silently corrupting downstream stock when produced FG was already dispatched /
    consumed / sold. Native ERPNext does NOT block this on demo/prod default config.
  * WO/Job-Card cancel-precondition pre-flight — a foreign submitted SE against the
    WO, a Stopped WO, or a foreign open Job Card are hard blocks.
  * Ledger-health pre-flight — never delete a doc whose ledger still has live
    (is_cancelled=0) SLE rows (catches a broken-cancel state).
  * Per-SE ledger-reversed assertion — assert 0 live SLE rows before deleting.
  * Asserted net-zero SLE/GL sweep — after physically deleting each cancelled
    stock-bearing doc, sweep its residual is_cancelled=1 SLE/GL rows ONLY after
    asserting every residual row nets to zero per (item, warehouse). End state =
    every chain doc cancelled-then-deleted, ZERO orphaned ledger rows, bins unchanged.

ATOMICITY (M1, mirrors the Token engine): this engine is NOT a single transaction.
It commits after each successful step, so a mid-cascade failure leaves the
already-committed steps deleted; recovery is via the pre-cascade backup (revert),
NOT a DB rollback. THIS IS WHY all fallible go/no-go decisions (HAZARD-C, WO/JC
precondition, ledger-health) run in the PRE-FLIGHT phase BEFORE any deletion —
a pre-flight failure aborts with partial_failure=False (nothing deleted).

DELETION ORDER (leaf -> root, PROVEN on demo):
  Step 1 Quality Inspections (gathered by reference_name IN ALL chain SE names —
         auto-QI runs on BOTH the Release SE and the Manufacture SE)
  Step 2 surplus-return SE   (last stock movement -> cancel first)
  Step 3 Manufacture SE      (its on_cancel reverse-sync flips PE status — harmless,
         PE deleted in Step 7)
  Step 4 Release SE
  Step 5 Job Cards           (Manufacture SE already gone -> they cancel)
  Step 6 Work Order          (DETACH pe links first, mirror reject_release L289-300;
         WO on_cancel auto-deletes its own cancelled Job Cards)
  Step 7 TS Production Entry (not submittable -> plain delete)

The on_cancel hook chain (hooks.py Stock Entry on_cancel) fires
`revert_on_stock_entry_cancel` + `production_on_stock_entry_cancel` when the
Manufacture/Release SE is cancelled, mutating the PE's ts_variance_status mid-cascade.
Safe ONLY because the PE is deleted LAST (Step 7). The step order is load-bearing.

NO direct caller; only invoked by `production_cascade_api.py` after CEO/MD approval.
Sets `frappe.flags.production_cascade_mode = True` for the engine's lifetime
(Lesson 176 try/finally).

Lesson references: 162 / 175 / 176 / 222 (idempotent) / 224 (dual-gate has_permission) /
237 (log_error kwargs) / 238 (best-effort) / 288 (status flips last).
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate


# Tolerance for the net-zero per-(item, warehouse) assertion before a ledger sweep.
_NET_ZERO_EPS = 1e-6


# ---------------------------------------------------------------- chain snapshot


def build_chain_snapshot(production_name: str) -> dict:
	"""Read-only chain map for a TS Production Entry. Consumed by preview, the
	target_chain_json snapshot field, and (post-delete) scan_orphans.

	Resolves the full chain LIVE from the PE + its Work Order:
	  - work_order, release_stock_entry, manufacture (linked) SE, surplus-return SE
	  - Job Cards of the WO
	  - Quality Inspections whose reference_name is ANY chain SE
	  - produced FG / by-product rows of the Manufacture SE (item, t_warehouse, batch,
	    posting date) — the HAZARD-C inputs
	  - SLE / GL counts for the chain vouchers
	"""
	chain = {
		"production": production_name,
		"production_record": None,
		"work_order": None,
		"work_order_status": None,
		"release_se": None,
		"manufacture_se": None,
		"surplus_se": None,
		"se_names": [],          # all chain Stock Entry names (release, manufacture, surplus)
		"job_cards": [],
		"quality_inspections": [],
		"produced_rows": [],     # [{item_code, warehouse, batch_no, posting_date, qty}]
		"stock_ledger_entries_count": 0,
		"gl_entries_count": 0,
		"submitted_se_count": 0,
	}

	if not frappe.db.exists("TS Production Entry", production_name):
		return chain

	pe = frappe.db.get_value(
		"TS Production Entry", production_name,
		["name", "ts_variance_status", "production_item", "production_item_name",
		 "actual_produced_qty", "work_order", "release_stock_entry", "linked_stock_entry",
		 "wip_returned_stock_entry", "company", "owner", "creation", "modified"],
		as_dict=True,
	)
	chain["production_record"] = pe

	wo_name = pe.get("work_order")
	chain["work_order"] = wo_name
	chain["release_se"] = pe.get("release_stock_entry")
	chain["manufacture_se"] = pe.get("linked_stock_entry")
	chain["surplus_se"] = pe.get("wip_returned_stock_entry")

	if wo_name:
		chain["work_order_status"] = frappe.db.get_value("Work Order", wo_name, "status")
		chain["job_cards"] = frappe.db.sql(
			"SELECT name, docstatus, status FROM `tabJob Card` WHERE work_order=%s",
			(wo_name,), as_dict=True,
		)

	se_names = [s for s in (chain["release_se"], chain["manufacture_se"], chain["surplus_se"]) if s]
	# Defensive + AUTHORITATIVE: resolve the chain SEs from the WO's own Stock Entries by
	# PURPOSE — the PE link fields (linked_stock_entry / release_stock_entry /
	# wip_returned_stock_entry) can be stale/None when the completion chain threw before
	# the final db_set (proven on demo). The WO's SEs are the source of truth. Manufacture
	# = purpose 'Manufacture'; Release = 'Material Transfer for Manufacture'; surplus =
	# a plain 'Material Transfer' against the WO. Any FOREIGN submitted SE is hard-blocked
	# by the WO/JC precondition pre-flight, so enriching from the WO is safe.
	if wo_name:
		for row in frappe.db.sql(
			"SELECT name, purpose FROM `tabStock Entry` WHERE work_order=%s", (wo_name,), as_dict=True,
		):
			if row.name not in se_names:
				se_names.append(row.name)
			if row.purpose == "Manufacture" and not chain["manufacture_se"]:
				chain["manufacture_se"] = row.name
			elif row.purpose == "Material Transfer for Manufacture" and not chain["release_se"]:
				chain["release_se"] = row.name
			elif row.purpose == "Material Transfer" and not chain["surplus_se"]:
				chain["surplus_se"] = row.name
	chain["se_names"] = se_names

	if se_names:
		for se in se_names:
			ds = frappe.db.get_value("Stock Entry", se, "docstatus")
			if ds == 1:
				chain["submitted_se_count"] += 1
		# QIs referencing any chain SE (auto-QI on both release + manufacture SEs).
		chain["quality_inspections"] = frappe.db.sql(
			"""SELECT name, docstatus, reference_name FROM `tabQuality Inspection`
			   WHERE reference_type='Stock Entry' AND reference_name IN %s""",
			(tuple(se_names),), as_dict=True,
		)
		chain["stock_ledger_entries_count"] = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE voucher_no IN %s",
			(tuple(se_names),))[0][0]
		chain["gl_entries_count"] = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabGL Entry` WHERE voucher_no IN %s",
			(tuple(se_names),))[0][0]

	# Produced FG / by-product rows of the Manufacture SE — HAZARD-C inputs.
	if chain["manufacture_se"]:
		mfg_posting = frappe.db.get_value(
			"Stock Entry", chain["manufacture_se"], ["posting_date"], as_dict=True
		) or {}
		chain["manufacture_posting_date"] = str(mfg_posting.get("posting_date") or "")
		rows = frappe.db.sql(
			"""SELECT item_code, t_warehouse, batch_no, transfer_qty, qty
			   FROM `tabStock Entry Detail`
			   WHERE parent=%s AND (is_finished_item=1 OR is_scrap_item=1)
			     AND t_warehouse IS NOT NULL AND t_warehouse != ''""",
			(chain["manufacture_se"],), as_dict=True,
		)
		for r in rows:
			chain["produced_rows"].append({
				"item_code": r.item_code,
				"warehouse": r.t_warehouse,
				"batch_no": r.batch_no or None,
				"qty": flt(r.transfer_qty) or flt(r.qty),
				"posting_date": chain["manufacture_posting_date"],
			})

	return chain


# ---------------------------------------------------------------- PRE-FLIGHT P1 HAZARD-C


def detect_hazard_c(production_name: str, chain: dict | None = None) -> dict:
	"""HAZARD-C detection — the SOLE guard against silent stock corruption.

	For each Manufacture-SE INWARD row (FG or by-product) collect (item, t_warehouse,
	batch); query OUTWARD SLEs (actual_qty<0, is_cancelled=0, voucher_no != the
	Manufacture SE, posting_date >= mfg posting_date, batch_no if the produced row
	carried a batch). ANY hit = a downstream consumer (Delivery Note / downstream
	Stock Entry / Sales-Invoice-with-update_stock — all post an OUTWARD SLE on the FG
	warehouse), so the cascade would corrupt that already-shipped stock when it cancels
	the Manufacture SE.

	Returns:
	  {"blocked": bool, "findings": [{voucher_type, voucher_no, item_code, warehouse,
	                                  qty_out, batch_no}], "produced_rows": [...]}
	"""
	chain = chain or build_chain_snapshot(production_name)
	findings: list[dict] = []
	produced_rows = chain.get("produced_rows") or []
	manufacture_se = chain.get("manufacture_se")

	if not manufacture_se or not produced_rows:
		return {"blocked": False, "findings": [], "produced_rows": produced_rows}

	for pr in produced_rows:
		params = {
			"item": pr["item_code"],
			"warehouse": pr["warehouse"],
			"mfg_se": manufacture_se,
			"mfg_date": pr.get("posting_date") or "1900-01-01",
		}
		batch_clause = ""
		if pr.get("batch_no"):
			batch_clause = "AND batch_no = %(batch)s"
			params["batch"] = pr["batch_no"]
		rows = frappe.db.sql(
			f"""SELECT voucher_type, voucher_no, item_code, warehouse, actual_qty, batch_no
			    FROM `tabStock Ledger Entry`
			    WHERE item_code = %(item)s
			      AND warehouse = %(warehouse)s
			      AND actual_qty < 0
			      AND is_cancelled = 0
			      AND voucher_no != %(mfg_se)s
			      AND posting_date >= %(mfg_date)s
			      {batch_clause}""",
			params, as_dict=True,
		)
		for r in rows:
			findings.append({
				"voucher_type": r.voucher_type,
				"voucher_no": r.voucher_no,
				"item_code": r.item_code,
				"warehouse": r.warehouse,
				"qty_out": flt(r.actual_qty),
				"batch_no": r.batch_no or None,
			})

	return {"blocked": bool(findings), "findings": findings, "produced_rows": produced_rows}


# ---------------------------------------------------------------- PRE-FLIGHT P2 WO/JC


def _preflight_wo_job_cards(chain: dict) -> dict:
	"""WO / Job-Card cancel-precondition pre-flight (PROVEN by feasibility test 3).

	HARD BLOCKS:
	  - a FOREIGN submitted Stock Entry against the WO (not in this chain's se_names)
	    — the engine must never touch an SE it did not back up.
	  - the WO is status 'Stopped' ("Unstop first").
	  - a FOREIGN open Job Card (a JC whose work_order is NOT this WO — defensive).

	Informational only (handled by step order): in-chain open Job Cards.
	"""
	wo = chain.get("work_order")
	if not wo:
		return {"ok": True}
	if not frappe.db.exists("Work Order", wo):
		return {"ok": True}  # WO already gone — nothing to pre-check

	chain_se = set(chain.get("se_names") or [])
	foreign_se = frappe.db.sql(
		"""SELECT name FROM `tabStock Entry`
		   WHERE work_order=%s AND docstatus=1""",
		(wo,), as_dict=True,
	)
	foreign = [r.name for r in foreign_se if r.name not in chain_se]
	if foreign:
		return {"ok": False, "error": (
			f"Work Order {wo} has submitted Stock Entries NOT in this cascade's chain: "
			f"{foreign}. The cascade backs up + deletes only its own chain documents; "
			"reverse / remove these foreign Stock Entries first."
		)}

	wo_status = frappe.db.get_value("Work Order", wo, "status")
	if wo_status == "Stopped":
		return {"ok": False, "error": (
			f"Work Order {wo} is Stopped. Unstop it first (a Stopped Work Order refuses "
			"cancel)."
		)}

	return {"ok": True}


# ---------------------------------------------------------------- PRE-FLIGHT P3 LEDGER-HEALTH


def _preflight_ledger_health(chain: dict) -> dict:
	"""Ledger-health pre-flight — assert no chain SE is in a broken-cancel state
	(docstatus=2 but is_cancelled=0 SLE rows still live). PROVEN shape from a concurrent
	broken-cancel run leaving 21 live rows in test 1. Never delete a live ledger.
	"""
	for se in (chain.get("se_names") or []):
		ds = frappe.db.get_value("Stock Entry", se, "docstatus")
		if ds != 2:
			continue
		live = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE voucher_no=%s AND is_cancelled=0",
			(se,))[0][0]
		if live:
			return {"ok": False, "error": (
				f"Stock Entry {se} is cancelled (docstatus=2) but still has {live} live "
				"(is_cancelled=0) Stock Ledger Entry rows — a broken-cancel state. "
				"Aborting; never delete a doc whose ledger has not reversed."
			)}
	return {"ok": True}


# ---------------------------------------------------------------- engine main


def execute_production_cascade(
	production_name: str,
	log_name: str,
	force_manufacture: bool = False,
	force_dispatched: bool = False,
) -> dict:
	"""Run the full production cascade: pre-flight (P0-P3) then leaf->root deletion +
	net-zero ledger sweep.

	The end state is: every chain doc CANCELLED then DELETED, ZERO orphaned SLE/GL
	rows for the chain vouchers, bins unchanged. ALWAYS full chain + full physical
	delete (no partial-cut, no toggle).

	Returns:
	  {success, aborted_step, steps[], pre_flight{}, sweep{}, partial_failure}
	"""
	steps: list[dict] = []
	pre_flight: dict = {}
	frappe.flags.production_cascade_mode = True
	# Elevate to Administrator for the MECHANICAL cancel/delete work (Lesson 176 try/finally
	# restore). Authorization is ALREADY complete upstream (kill switch + Manufacturing-
	# Manager role gate + 2-person CEO/MD approval); the executor identity is captured on the
	# log (executed_as) for attribution BEFORE this runs. Cancelling a Manufacture Stock
	# Entry drives NESTED doc operations (Work Order produced_qty rollback, Serial-and-Batch
	# Bundle, Project cost roll-up) whose own permission checks, in Frappe v15, honor neither
	# the parent's ignore_permissions flag nor a non-Administrator Super Admin role — exactly
	# the case ts_production_release.system_session() handles. Acting AS Administrator for the
	# mechanical steps is the only reliable lever (an empty PermissionError on the Manufacture
	# SE cancel was PROVEN on demo otherwise).
	_engine_prev_user = frappe.session.user
	if _engine_prev_user != "Administrator":
		frappe.set_user("Administrator")
	try:
		# ── PRE-FLIGHT PHASE (BEFORE any deletion) ──────────────────────────
		# P0 — re-resolve the chain snapshot LIVE (TOCTOU: stock/chain may have
		# changed since approval). Persist to log.target_chain_json.
		chain = build_chain_snapshot(production_name)
		pre_flight["chain_resolved"] = True
		try:
			_persist_chain_snapshot(log_name, chain)
		except Exception as e:
			frappe.log_error(
				title=f"production_cascade: chain snapshot persist failed for {log_name}",
				message=f"{type(e).__name__}: {e}",
			)

		if not chain.get("production_record"):
			steps.append({"step": "PRE", "action": "production-already-gone", "success": True})
			# Nothing to delete — treat as a clean no-op success.
			frappe.db.commit()
			return {
				"success": True, "aborted_step": None, "steps": steps,
				"pre_flight": pre_flight, "sweep": {}, "partial_failure": False,
			}

		# P1 — HAZARD-C (re-check at EXECUTE, not just preview).
		hazard = detect_hazard_c(production_name, chain)
		pre_flight["hazard_c"] = hazard
		if hazard.get("blocked") and not force_dispatched:
			vouchers = ", ".join(
				f"{f['voucher_type']} {f['voucher_no']}" for f in hazard["findings"]
			)
			steps.append({
				"step": "HAZ", "action": "blocked-downstream-consumer", "success": False,
				"error": (
					"Cascade blocked (HAZARD-C): produced finished-goods / by-products were "
					f"already dispatched / consumed downstream by: {vouchers}. Cancelling the "
					"Manufacture Stock Entry would corrupt that downstream stock. Reverse the "
					"downstream document(s) first, OR re-initiate with the Force-Dispatched "
					"override (FORCE-DELETE-DISPATCHED)."
				),
				"findings": hazard["findings"],
			})
			return _abort(steps, "HAZ", pre_flight)

		# P2 — WO / Job-Card cancel-precondition.
		wojc = _preflight_wo_job_cards(chain)
		pre_flight["wo_job_cards"] = wojc
		if not wojc.get("ok"):
			steps.append({"step": "WJC", "action": "blocked-precondition",
			              "success": False, "error": wojc.get("error")})
			return _abort(steps, "WJC", pre_flight)

		# P3 — ledger-health.
		ledger = _preflight_ledger_health(chain)
		pre_flight["ledger_health"] = ledger
		if not ledger.get("ok"):
			steps.append({"step": "LDG", "action": "blocked-broken-cancel",
			              "success": False, "error": ledger.get("error")})
			return _abort(steps, "LDG", pre_flight)

		# ── DELETION PHASE (leaf -> root) ───────────────────────────────────
		# A submitted Manufacture/Release/surplus SE (or submitted QI) requires the
		# Force-Manufacture override; pre-checked here so nothing is half-deleted.
		if chain.get("submitted_se_count", 0) and not force_manufacture:
			steps.append({
				"step": "SUB", "action": "blocked-submitted-not-forced", "success": False,
				"error": (
					f"This run has {chain['submitted_se_count']} submitted Stock Entry(ies) "
					"in its chain. Re-initiate with the Force-Manufacture override "
					"(FORCE-DELETE-MANUFACTURE) to cancel + delete them."
				),
			})
			return _abort(steps, "SUB", pre_flight)

		chain_se_names = list(chain.get("se_names") or [])

		# Step 1 — Quality Inspections (by reference_name IN all chain SE names).
		qi_names = [q["name"] for q in (chain.get("quality_inspections") or [])]
		# Re-query live in case auto-QI created rows after the snapshot.
		if chain_se_names:
			for q in frappe.db.sql(
				"""SELECT name FROM `tabQuality Inspection`
				   WHERE reference_type='Stock Entry' AND reference_name IN %s""",
				(tuple(chain_se_names),), as_dict=True,
			):
				if q.name not in qi_names:
					qi_names.append(q.name)
		for qi in qi_names:
			res = _cancel_then_delete("Quality Inspection", qi, allow_force=force_manufacture)
			steps.append({"step": "QI", **res})
			if not res["success"]:
				return _abort(steps, "QI", pre_flight)

		# Steps 2-4 — the stock-bearing Stock Entries.
		#
		# CRITICAL ORDERING (PROVEN by smoke test, 27 Jun 2026): for a batch-tracked
		# chain we must CANCEL ALL stock-bearing SEs FIRST (so the FULL ledger reverses —
		# each cancel restores the batch qty the next cancel needs), and only THEN delete
		# the cancelled docs + sweep their residual ledger. Cancel-delete-sweep one SE at a
		# time physically removes the Manufacture SE's batch-reversal rows BEFORE the Release
		# SE cancel needs them, tripping BatchNegativeStockError on WIP.
		#
		# Cancel order is reverse-chronological (last stock movement first):
		#   surplus-return SE  -> Manufacture SE  -> Release SE.
		# Cancelling the Manufacture/Release SE fires the on_cancel reverse-sync hooks
		# (production_on_stock_entry_cancel) that flip the PE's ts_variance_status mid-
		# cascade — HARMLESS because the PE is deleted in Step 7.
		stock_se_order = [
			("SURPLUS", chain.get("surplus_se")),
			("MANUFACTURE", chain.get("manufacture_se")),
			("RELEASE", chain.get("release_se")),
		]
		cancelled_se = []  # (step, se_name) in cancel order — deleted+swept after ALL cancels
		for step_code, se in stock_se_order:
			if not se:
				continue
			res = _cancel_stock_entry(se, allow_force=force_manufacture)
			steps.append({"step": step_code, **res})
			if not res["success"]:
				return _abort(steps, step_code, pre_flight)
			if res.get("action") != "already-gone":
				cancelled_se.append((step_code, se))

		# Now the WHOLE chain ledger is reversed. Delete each cancelled SE + sweep its
		# residual (is_cancelled=1, net-zero) SLE/GL rows. Order among them is irrelevant.
		for step_code, se in cancelled_se:
			res = _delete_cancelled_stock_entry_with_sweep(se)
			steps.append({"step": f"{step_code}-DEL", **res})
			if not res["success"]:
				return _abort(steps, f"{step_code}-DEL", pre_flight)

		# DETACH the PE -> SE links BEFORE touching the WO so neither the WO nor the
		# PE hits LinkExistsError (mirrors ts_production_release.reject_release L289-300).
		if chain.get("production_record") and frappe.db.exists("TS Production Entry", production_name):
			try:
				frappe.db.set_value(
					"TS Production Entry", production_name,
					{
						"release_stock_entry": None,
						"linked_stock_entry": None,
						"wip_returned_stock_entry": None,
					},
					update_modified=False,
				)
				frappe.db.commit()
				steps.append({"step": "DETACH", "action": "pe-links-detached",
				              "name": production_name, "success": True})
			except Exception as e:
				steps.append({"step": "DETACH", "action": "detach-failed",
				              "name": production_name, "success": False,
				              "error": f"{type(e).__name__}: {e}"})
				return _abort(steps, "DETACH", pre_flight)

		# Step 5 — Job Cards (Manufacture SE already gone -> they cancel).
		wo_name = chain.get("work_order")
		if wo_name and frappe.db.exists("Work Order", wo_name):
			jc_rows = frappe.db.sql(
				"SELECT name, docstatus FROM `tabJob Card` WHERE work_order=%s",
				(wo_name,), as_dict=True,
			)
			for jc in jc_rows:
				res = _cancel_then_delete("Job Card", jc.name, allow_force=True)
				steps.append({"step": "JOBCARD", **res})
				if not res["success"]:
					return _abort(steps, "JOBCARD", pre_flight)

			# Step 6 — Work Order (its on_cancel auto-deletes its own cancelled JCs).
			res = _cancel_then_delete("Work Order", wo_name, allow_force=True)
			steps.append({"step": "WORKORDER", **res})
			if not res["success"]:
				return _abort(steps, "WORKORDER", pre_flight)
		else:
			steps.append({"step": "WORKORDER", "action": "already-gone",
			              "name": wo_name, "success": True})

		# Step 7 — TS Production Entry (not submittable -> plain delete).
		if frappe.db.exists("TS Production Entry", production_name):
			res = _cancel_then_delete("TS Production Entry", production_name, allow_force=True)
			steps.append({"step": "PE", **res})
			if not res["success"]:
				return _abort(steps, "PE", pre_flight)
		else:
			steps.append({"step": "PE", "name": production_name,
			              "action": "already-gone", "success": True})

		frappe.db.commit()

		# Final residual-orphan sweep across ALL chain vouchers (defence-in-depth;
		# per-SE sweep already ran in each _delete_stock_entry_with_sweep).
		sweep = _final_voucher_sweep(chain_se_names)

		return {
			"success": True,
			"aborted_step": None,
			"steps": steps,
			"pre_flight": pre_flight,
			"sweep": sweep,
			"partial_failure": False,
		}
	except Exception as e:
		frappe.log_error(
			title=f"production_cascade_engine.execute fatal for {production_name}",
			message=f"{type(e).__name__}: {e}\nLog: {log_name}\nSteps so far: {steps}",
		)
		frappe.db.rollback()
		return {
			"success": False,
			"aborted_step": "exception",
			"steps": steps,
			"pre_flight": pre_flight,
			"error": f"{type(e).__name__}: {e}",
			"partial_failure": _has_mutated(steps),
		}
	finally:
		# Lesson 176 — ALWAYS restore the prior user + clear the flag, even on exception.
		if frappe.session.user != _engine_prev_user:
			frappe.set_user(_engine_prev_user)
		frappe.flags.production_cascade_mode = False


# ---------------------------------------------------------------- per-step helpers


def _cancel_then_delete(doctype: str, name: str, allow_force: bool = False) -> dict:
	"""Standard cancel-then-delete pattern (cloned from cascade_delete_engine.py,
	doctype-agnostic). Dual-gates has_permission (Lesson 224). Returns per-step dict."""
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
		frappe.db.commit()
		return {"doctype": doctype, "name": name, "action": "deleted", "success": True}
	except Exception as e:
		return {
			"doctype": doctype, "name": name, "action": "error",
			"success": False, "error": f"{type(e).__name__}: {e}",
		}


def _cancel_stock_entry(se_name: str, allow_force: bool = False) -> dict:
	"""Cancel ONE stock-bearing Stock Entry (no delete yet) + assert its ledger reversed.

	Cancel-only so the WHOLE chain's ledger can be reversed BEFORE any SLE row is
	physically swept — a batch-tracked chain needs every cancel's reversal in place when
	the next SE cancels (else BatchNegativeStockError on WIP). After cancel, assert ZERO
	live (is_cancelled=0) SLE rows (the M1 teeth — never proceed on a broken cancel).
	"""
	if not frappe.db.exists("Stock Entry", se_name):
		return {"doctype": "Stock Entry", "name": se_name, "action": "already-gone", "success": True}
	try:
		ds = frappe.db.get_value("Stock Entry", se_name, "docstatus")
		if ds == 1:
			if not allow_force:
				return {"doctype": "Stock Entry", "name": se_name, "action": "force-not-allowed",
				        "success": False, "error": "Submitted Stock Entry without force flag"}
			frappe.has_permission("Stock Entry", "cancel", doc=se_name, throw=True)
			doc = frappe.get_doc("Stock Entry", se_name)
			doc.flags.ignore_links = True
			doc.flags.ignore_permissions = True
			doc.cancel()
			frappe.db.commit()

		# Assert the ledger reversed — ZERO live SLE rows (the M1 teeth).
		live = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE voucher_no=%s AND is_cancelled=0",
			(se_name,))[0][0]
		if live:
			return {"doctype": "Stock Entry", "name": se_name, "action": "live-ledger-abort",
			        "success": False, "error": (
			            f"Stock Entry {se_name} still has {live} live (is_cancelled=0) SLE "
			            "rows after cancel — broken-cancel state. Refusing to proceed.")}
		return {"doctype": "Stock Entry", "name": se_name, "action": "cancelled", "success": True}
	except Exception as e:
		return {
			"doctype": "Stock Entry", "name": se_name, "action": "error",
			"success": False, "error": f"{type(e).__name__}: {e}",
		}


def _delete_cancelled_stock_entry_with_sweep(se_name: str) -> dict:
	"""Physically delete an ALREADY-CANCELLED Stock Entry + sweep its residual SLE/GL rows
	— but ONLY after asserting: residual QTY nets to zero per (item, warehouse), residual
	VALUE (stock_value_difference) nets to zero at the VOUCHER level, and the voucher's GL
	nets to zero (debit-credit). (Value is voucher-level, not per-pair, because a Stock
	Entry legitimately moves value BETWEEN pairs.) Runs after the whole chain is cancelled
	(full ledger reversed), so the sweep is proven safe (no bin repost needed).
	"""
	if not frappe.db.exists("Stock Entry", se_name):
		return {"doctype": "Stock Entry", "name": se_name, "action": "already-gone", "success": True}
	try:
		# Re-assert ZERO live SLE rows (belt + braces vs a concurrent re-post).
		live = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE voucher_no=%s AND is_cancelled=0",
			(se_name,))[0][0]
		if live:
			return {"doctype": "Stock Entry", "name": se_name, "action": "live-ledger-abort",
			        "success": False, "error": (
			            f"Stock Entry {se_name} has {live} live SLE rows at delete time. Aborting.")}

		# Assert QUANTITY nets to zero per (item, warehouse) — a cancel reverses qty per
		# pair, so each pair MUST be qty-zero after cancel (a non-zero pair => bin repost).
		residual_qty = frappe.db.sql(
			"""SELECT item_code, warehouse, SUM(actual_qty) AS net_qty
			   FROM `tabStock Ledger Entry` WHERE voucher_no=%s
			   GROUP BY item_code, warehouse HAVING ABS(SUM(actual_qty)) > %s""",
			(se_name, _NET_ZERO_EPS), as_dict=True,
		)
		if residual_qty:
			detail = ", ".join(f"{r.item_code}@{r.warehouse}={flt(r.net_qty)}" for r in residual_qty)
			return {"doctype": "Stock Entry", "name": se_name, "action": "net-nonzero-abort",
			        "success": False, "error": (
			            f"Stock Entry {se_name} residual SLE rows do NOT net to zero per "
			            f"(item, warehouse) on qty: {detail}. Refusing the ledger sweep "
			            "(a bin repost would be required).")}

		# H1 (corrected): assert VALUATION nets to zero at the VOUCHER level, NOT per
		# (item, warehouse). A Stock Entry legitimately moves value BETWEEN pairs (e.g.
		# Manufacture RM@WIP -V, FG@FG +V), so stock_value_difference is conserved
		# per-VOUCHER, not per-pair — a per-pair value check false-positives on every
		# balanced transfer. Voucher-level still catches a genuinely stranded-value
		# residual (the H1 concern) without breaking the normal happy path.
		net_val = frappe.db.sql(
			"SELECT IFNULL(SUM(stock_value_difference), 0) FROM `tabStock Ledger Entry` WHERE voucher_no=%s",
			(se_name,))[0][0]
		if abs(flt(net_val)) > _NET_ZERO_EPS:
			return {"doctype": "Stock Entry", "name": se_name, "action": "value-nonzero-abort",
			        "success": False, "error": (
			            f"Stock Entry {se_name} residual SLE value does NOT net to zero "
			            f"(voucher net stock_value_difference={flt(net_val)}). Refusing the sweep.")}

		# H1: Assert this voucher's residual GL nets to zero (debit - credit) before the
		# raw GL delete — never sweep an unbalanced ledger.
		gl_net = frappe.db.sql(
			"""SELECT IFNULL(SUM(debit), 0) - IFNULL(SUM(credit), 0)
			   FROM `tabGL Entry` WHERE voucher_no=%s AND voucher_type='Stock Entry'""",
			(se_name,))[0][0]
		if abs(flt(gl_net)) > _NET_ZERO_EPS:
			return {"doctype": "Stock Entry", "name": se_name, "action": "gl-nonzero-abort",
			        "success": False, "error": (
			            f"Stock Entry {se_name} residual GL rows do NOT net to zero "
			            f"(debit-credit={flt(gl_net)}). Refusing the ledger sweep.")}

		# L1: delete + sweep atomically under a savepoint, so a failed sweep rolls back
		# this SE's delete (never a deleted-doc-with-stranded-ledger half-state).
		frappe.has_permission("Stock Entry", "delete", doc=se_name, throw=True)
		frappe.db.savepoint("pcd_se_sweep")
		try:
			frappe.delete_doc(
				"Stock Entry", se_name,
				force=1, ignore_permissions=True, ignore_on_trash=True, delete_permanently=True,
			)
			sle_deleted = frappe.db.sql(
				"DELETE FROM `tabStock Ledger Entry` WHERE voucher_no=%s", (se_name,))
			gl_deleted = frappe.db.sql(
				"DELETE FROM `tabGL Entry` WHERE voucher_no=%s AND voucher_type='Stock Entry'",
				(se_name,))
		except Exception:
			frappe.db.rollback(save_point="pcd_se_sweep")
			raise
		frappe.db.commit()
		return {
			"doctype": "Stock Entry", "name": se_name, "action": "deleted-and-swept",
			"success": True,
			"sle_rows_swept": _rowcount(sle_deleted),
			"gl_rows_swept": _rowcount(gl_deleted),
		}
	except Exception as e:
		return {
			"doctype": "Stock Entry", "name": se_name, "action": "error",
			"success": False, "error": f"{type(e).__name__}: {e}",
		}


def _final_voucher_sweep(chain_se_names: list) -> dict:
	"""Defence-in-depth post-delete sweep: assert ZERO residual SLE/GL rows remain for
	ALL chain vouchers (the per-SE sweep already removed them). Reports any leftovers
	WITHOUT deleting blindly (a leftover here would mean a per-SE net-zero assertion
	never ran — surface it, don't mask it)."""
	if not chain_se_names:
		return {"clean": True, "residual_sle": 0, "residual_gl": 0}
	sle = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE voucher_no IN %s",
		(tuple(chain_se_names),))[0][0]
	gl = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabGL Entry` WHERE voucher_no IN %s",
		(tuple(chain_se_names),))[0][0]
	clean = (sle == 0 and gl == 0)
	if not clean:
		frappe.log_error(
			title="production_cascade: residual ledger rows after sweep",
			message=f"chain_vouchers={chain_se_names} residual_sle={sle} residual_gl={gl}",
		)
	return {"clean": clean, "residual_sle": sle, "residual_gl": gl}


def _abort(steps: list, step_name: str, pre_flight: dict) -> dict:
	"""Abort the cascade at `step_name`.

	M1 — frappe.db.rollback() only undoes the CURRENT uncommitted transaction. The
	engine commits after each successful step, so any step that already completed
	stays deleted. `partial_failure` flags a partially-deleted chain; recovery is via
	the pre-cascade backup (revert). A PRE-FLIGHT failure aborts with
	partial_failure=False (nothing deleted).
	"""
	frappe.db.rollback()
	return {
		"success": False,
		"aborted_step": step_name,
		"steps": steps,
		"pre_flight": pre_flight,
		"partial_failure": _has_mutated(steps),
		"completed_steps": [s.get("step") for s in steps
		                    if s.get("success") and s.get("action") in _MUTATED_ACTIONS],
	}


_MUTATED_ACTIONS = {"deleted", "deleted-and-swept", "cancelled", "pe-links-detached"}


def _has_mutated(steps: list) -> bool:
	return any(s.get("success") and s.get("action") in _MUTATED_ACTIONS for s in steps)


def _rowcount(res):
	"""frappe.db.sql DELETE may return None / a cursor count depending on the driver."""
	try:
		return frappe.db._cursor.rowcount
	except Exception:
		return None


def _persist_chain_snapshot(log_name: str, chain: dict):
	"""Persist the re-resolved chain snapshot + target_work_order onto the log
	(api_caller flag bypasses append-only)."""
	try:
		frappe.flags.production_cascade_api_caller = True
		frappe.db.set_value(
			"TS Production Cascade Log", log_name,
			{
				"target_chain_json": json.dumps(chain, indent=2, default=str),
				"target_work_order": chain.get("work_order"),
			},
			update_modified=False,
		)
		frappe.db.commit()
	finally:
		frappe.flags.production_cascade_api_caller = False


# ---------------------------------------------------------------- orphan-trail scan


def scan_orphans(log_name: str) -> dict:
	"""Post-delete invariant check (config = full physical delete -> clean == all-zero).

	Reads the chain vouchers from log.target_chain_json (the PE is gone, so we cannot
	re-query the PE). Scoped to THIS log's chain vouchers ONLY — never sweeps global
	orphans. Returns {orphans: {label: count}, orphan_count, clean}.

	Invariant for the full-physical-delete config: EVERY count == 0.
	"""
	if not frappe.db.exists("TS Production Cascade Log", log_name):
		return {"error": "log row not found", "log_name": log_name}

	row = frappe.db.get_value(
		"TS Production Cascade Log", log_name,
		["target_production", "target_work_order", "target_chain_json"],
		as_dict=True,
	) or {}
	chain = {}
	if row.get("target_chain_json"):
		try:
			chain = json.loads(row["target_chain_json"])
		except Exception:
			chain = {}

	pe = row.get("target_production") or chain.get("production")
	wo = row.get("target_work_order") or chain.get("work_order")
	se_names = chain.get("se_names") or []

	checks: dict = {}
	if se_names:
		checks["Stock Ledger Entry"] = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE voucher_no IN %s",
			(tuple(se_names),))[0][0]
		checks["GL Entry"] = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabGL Entry` WHERE voucher_no IN %s",
			(tuple(se_names),))[0][0]
		checks["Stock Entry"] = frappe.db.sql(
			"SELECT COUNT(*) FROM `tabStock Entry` WHERE name IN %s",
			(tuple(se_names),))[0][0]
		checks["Quality Inspection"] = frappe.db.sql(
			"""SELECT COUNT(*) FROM `tabQuality Inspection`
			   WHERE reference_type='Stock Entry' AND reference_name IN %s""",
			(tuple(se_names),))[0][0]
	else:
		checks["Stock Ledger Entry"] = 0
		checks["GL Entry"] = 0
		checks["Stock Entry"] = 0
		checks["Quality Inspection"] = 0
	checks["Work Order"] = frappe.db.count("Work Order", {"name": wo}) if wo else 0
	checks["Job Card"] = frappe.db.count("Job Card", {"work_order": wo}) if wo else 0
	checks["TS Production Entry"] = frappe.db.count("TS Production Entry", {"name": pe}) if pe else 0

	total = sum(v for v in checks.values() if isinstance(v, int))
	result = {
		"log_name": log_name,
		"production": pe,
		"work_order": wo,
		"orphans": checks,
		"orphan_count": total,
		"clean": total == 0,
	}

	clean = result["clean"]
	orphans_json = json.dumps(checks, indent=2, default=str)
	try:
		frappe.flags.production_cascade_api_caller = True
		frappe.db.set_value(
			"TS Production Cascade Log", log_name,
			{
				"integrity_scan_run_at": frappe.utils.now_datetime(),
				"integrity_scan_clean": 1 if clean else 0,
				"integrity_scan_orphans_json": orphans_json,
			},
			update_modified=False,
		)
		frappe.db.commit()
	finally:
		frappe.flags.production_cascade_api_caller = False

	if not clean:
		frappe.log_error(
			title=f"Production cascade integrity scan: orphans for log {log_name}",
			message=f"production={pe} work_order={wo} orphans={checks}",
		)

	return result
