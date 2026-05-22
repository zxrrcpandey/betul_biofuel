"""Cascade Delete Engine (v2.11.0) — pure orchestration of the
PI → PR → DS → QI → WB → GE → Token deletion chain.

Refactored from the production-proven `_batch_token_delete_optA.py` script
(19 May 2026 Round 44, 5/5 deterministic). Each deletion step is idempotent.

ATOMICITY (v2.11.1 audit M1 — corrected): this engine is NOT a single
transaction. It commits after each successful step, so a mid-cascade failure
leaves the already-committed steps deleted (a partially-deleted chain) and
`_abort()` returns `partial_failure=True` listing the completed steps.
Recovery from a partial failure is via the pre-cascade backup (Hardening B/M
revert), NOT a DB rollback. The B4 financial-link gate runs BEFORE any commit.

NO direct caller; only invoked by `cascade_delete_api.py` after CEO approval.
Sets `frappe.flags.cascade_delete_mode = True` for the engine's lifetime
(Lesson 176 try/finally). NOTE (v2.11.1 audit B2): `ts_gate_entry.py`'s GE
`on_cancel` guard does NOT actually honour this flag — its POST_WEIGHING /
POST_GRN branches throw unconditionally. The surgical-SQL DELETE inside
`_cancel_then_delete_ge_with_lesson_275_fallback` is therefore the formally-
accepted PRIMARY GE-deletion path for post-weighbridge tokens, not a rare
fallback (Lesson 275). The flag is kept set for any future guard that does
honour it; making the guard honour it is backlogged (locked file).

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
		# B4 (v2.11.1) — financial linkage. Deleting a PI/PR that has a
		# downstream Payment Entry / Journal Entry / Landed Cost Voucher (or a
		# PR already billed) leaves dangling GL references.
		"payment_entries": [],
		"journal_entries": [],
		"landed_cost_vouchers": [],
		"billed_prs": [],
		"has_financial_links": False,
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

	# B4 (v2.11.1) — detect downstream financial documents that would be left
	# with dangling GL references if the in-chain PI/PR were deleted.
	chain["billed_prs"] = [r["name"] for r in chain["purchase_receipts"]
	                       if (r.get("per_billed") or 0) > 0]
	fin_ref_names = ([r["name"] for r in chain["purchase_invoices"]]
	                 + [r["name"] for r in chain["purchase_receipts"]])
	if fin_ref_names:
		chain["payment_entries"] = frappe.db.sql(
			"""SELECT DISTINCT pe.name, pe.docstatus, pe.paid_amount
			   FROM `tabPayment Entry Reference` per
			   JOIN `tabPayment Entry` pe ON pe.name = per.parent
			   WHERE per.reference_doctype IN ('Purchase Invoice', 'Purchase Receipt')
			     AND per.reference_name IN %s""",
			(fin_ref_names,), as_dict=True,
		)
		chain["journal_entries"] = frappe.db.sql(
			"""SELECT DISTINCT je.name, je.docstatus
			   FROM `tabJournal Entry Account` jea
			   JOIN `tabJournal Entry` je ON je.name = jea.parent
			   WHERE jea.reference_type IN ('Purchase Invoice', 'Purchase Receipt')
			     AND jea.reference_name IN %s""",
			(fin_ref_names,), as_dict=True,
		)
	if chain["purchase_receipts"]:
		pr_names_lcv = [r["name"] for r in chain["purchase_receipts"]]
		chain["landed_cost_vouchers"] = frappe.db.sql(
			"""SELECT DISTINCT lcpr.parent AS name
			   FROM `tabLanded Cost Purchase Receipt` lcpr
			   WHERE lcpr.receipt_document_type = 'Purchase Receipt'
			     AND lcpr.receipt_document IN %s""",
			(pr_names_lcv,), as_dict=True,
		)
	chain["has_financial_links"] = bool(
		chain["payment_entries"] or chain["journal_entries"]
		or chain["landed_cost_vouchers"] or chain["billed_prs"])

	chain["versions_count"] = frappe.db.count("Version", {"ref_doctype": "TS Token", "docname": token_name})
	chain["comments_count"] = frappe.db.count("Comment", {"reference_doctype": "TS Token", "reference_name": token_name})
	return chain


# ---------------------------------------------------------------- engine main


# Deletion order (leaf -> root). The cut-point names the EARLIEST-created
# document to delete; the engine runs every step at index <= cut-index and
# SKIPS steps below (created earlier). cut_point=None / "TS Gate Entry" also
# deletes the Token. Partial cuts keep the Token and reset its status.
_DELETION_STEPS = [
	("PI", "Purchase Invoice"),
	("PR", "Purchase Receipt"),
	("DS", "TS Deduction Sheet"),
	("QI", "TS Quality Inspection"),
	("WB", "TS Weighbridge Log"),
	("GE", "TS Gate Entry"),
]
CUT_POINT_DOCTYPES = [d for _, d in _DELETION_STEPS]  # whitelist for API validation


def _cut_index(cut_point: str | None) -> int:
	"""Index in _DELETION_STEPS up to which deletion runs. None -> full (5)."""
	if not cut_point:
		return len(_DELETION_STEPS) - 1
	for i, (_code, dt) in enumerate(_DELETION_STEPS):
		if dt == cut_point:
			return i
	# M4 (v2.11.1) — unknown cut_point: a destructive engine must NEVER silently
	# widen scope to a full cascade. Fail closed — refuse outright.
	frappe.throw(_("Invalid cascade cut-point: {0}").format(str(cut_point)))


def execute_cascade(
	token_name: str,
	log_name: str,
	force_pr: bool = False,
	force_mi: bool = False,
	cut_point: str | None = None,
	force_payment_links: bool = False,
) -> dict:
	"""Run the PI → PR → DS → QI → WB → GE → Token cascade.

	`cut_point` (None = full cascade incl. Token): names the earliest document
	to delete. Steps at deletion-order index <= cut-index run; steps below are
	SKIPPED (kept). The Token is deleted only when cut_point is None or
	"TS Gate Entry"; for a partial cut the Token is kept and its status is
	auto-reset to the deepest surviving document's real phase.

	Returns:
	  {success, aborted_step, steps[], lesson_275_fallback_used, partial(bool)}
	"""
	steps: list[dict] = []
	lesson_275_used = False
	cut_idx = _cut_index(cut_point)
	is_partial = cut_point not in (None, "TS Gate Entry")
	frappe.flags.cascade_delete_mode = True
	try:
		# B4 (v2.11.1) — financial-linkage gate. Checked BEFORE any deletion so
		# nothing is committed if it blocks. Deleting a PI/PR with a downstream
		# Payment Entry / Journal Entry / Landed Cost Voucher (or a billed PR)
		# would orphan GL entries — refuse unless the Super Admin explicitly set
		# force_payment_links (its own type-to-confirm gate at initiate time).
		_fin = build_chain_snapshot(token_name)
		if _fin.get("has_financial_links") and not force_payment_links:
			steps.append({
				"step": "FIN", "action": "blocked-financial-links", "success": False,
				"error": (
					"Cascade blocked — chain has downstream financial documents: "
					f"Payment Entries={[r['name'] for r in _fin.get('payment_entries', [])]} "
					f"Journal Entries={[r['name'] for r in _fin.get('journal_entries', [])]} "
					f"Landed Cost Vouchers={[r['name'] for r in _fin.get('landed_cost_vouchers', [])]} "
					f"billed Purchase Receipts={_fin.get('billed_prs', [])}. "
					"Deleting them would leave dangling GL references. Re-initiate with "
					"the Force-Payment-Links override if this is intended."
				),
			})
			return _abort(steps, "FIN", lesson_275_used)

		# Step 1 — Purchase Invoices  (deletion index 0)
		if 0 <= cut_idx:
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
		else:
			steps.append({"step": "PI", "action": "skipped-below-cutpoint", "success": True})

		# Step 2 — Purchase Receipts  (deletion index 1)
		if 1 <= cut_idx:
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
		else:
			steps.append({"step": "PR", "action": "skipped-below-cutpoint", "success": True})

		# Step 3 — Deduction Sheets  (deletion index 2)
		if 2 <= cut_idx:
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
		else:
			steps.append({"step": "DS", "action": "skipped-below-cutpoint", "success": True})

		# Step 4 — Quality Inspection — force_mi gate  (deletion index 3)
		if 3 <= cut_idx:
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
		else:
			steps.append({"step": "QI", "action": "skipped-below-cutpoint", "success": True})

		# Step 5 — Weighbridge Log  (deletion index 4)
		if 4 <= cut_idx:
			wb_rows = frappe.db.sql(
				"SELECT name, docstatus FROM `tabTS Weighbridge Log` WHERE token_number=%s",
				(token_name,), as_dict=True,
			)
			for wb in wb_rows:
				res = _cancel_then_delete("TS Weighbridge Log", wb.name, allow_force=True)
				steps.append({"step": "WB", **res})
				if not res["success"]:
					return _abort(steps, "WB", lesson_275_used)
		else:
			steps.append({"step": "WB", "action": "skipped-below-cutpoint", "success": True})

		# Step 6 — Gate Entry (Lesson 275 fallback embedded)  (deletion index 5)
		if 5 <= cut_idx:
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
		else:
			steps.append({"step": "GE", "action": "skipped-below-cutpoint", "success": True})

		# Step 7 — Token: DELETE for full cascade; RESET status for a partial cut.
		if is_partial:
			res = _reset_token_status(token_name, cut_point, log_name)
			steps.append({"step": "TOK", **res})
			if not res["success"]:
				return _abort(steps, "TOK", lesson_275_used)
		elif frappe.db.exists("TS Token", token_name):
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
			"partial": is_partial,
			"cut_point": cut_point,
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
	"""GE-specific path: try cancel → on the Lesson-275 ValidationError → surgical SQL DELETE.

	IMPORTANT (v2.11.1 audit B2) — the surgical-SQL DELETE is the FORMALLY-ACCEPTED
	PRIMARY GE-deletion path for any post-weighbridge token, NOT a rare fallback.
	`ts_gate_entry.py`'s `on_cancel` guard does NOT honour `frappe.flags.cascade_delete_mode`
	— its POST_WEIGHING / POST_GRN branches throw unconditionally — so `ge_doc.cancel()`
	below predictably raises for any token that has a Weighbridge Log or Purchase
	Receipt, and this function then deletes the GE directly via SQL. (Making the
	guard honour the flag is backlogged — it touches the locked ts_gate_entry.py.)
	The trigger below matches the guard's EXACT message (not a loose substring) so an
	unrelated ValidationError can never be misclassified into a force-delete.
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
			# B3 (v2.11.1) — match the GE on_cancel guard's EXACT message, not a
			# loose substring. The old `"Quality" in msg` test mis-classified ANY
			# unrelated ValidationError mentioning "Quality" into a force-delete.
			msg = str(e)
			is_lesson_275 = (
				isinstance(e, frappe.exceptions.ValidationError)
				and "Cannot cancel Gate Entry" in msg
				and ("A Weighbridge Log exists" in msg
				     or "A Purchase Receipt has been created" in msg)
			)
			if is_lesson_275:
				surgical = True
				try:
					# B3 — the surgical DELETE bypasses frappe.delete_doc, so it must
					# remove everything delete_doc would have. Must-succeed: child-table
					# rows + the GE row. Best-effort: comments/versions/shares/todos.
					for _tf in frappe.get_meta("TS Gate Entry").get_table_fields():
						frappe.db.sql(
							f"DELETE FROM `tab{_tf.options}` "
							"WHERE parent=%s AND parenttype='TS Gate Entry'",
							(ge_name,),
						)
					for _tbl, _dt_col, _nm_col in (
						("tabComment", "reference_doctype", "reference_name"),
						("tabVersion", "ref_doctype", "docname"),
						("tabDocShare", "share_doctype", "share_name"),
						("tabToDo", "reference_type", "reference_name"),
					):
						try:
							frappe.db.sql(
								f"DELETE FROM `{_tbl}` "
								f"WHERE `{_dt_col}`='TS Gate Entry' AND `{_nm_col}`=%s",
								(ge_name,),
							)
						except Exception:
							pass  # auxiliary cleanup is best-effort — never abort the cascade
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
	"""Abort the cascade at `step_name`.

	M1 (v2.11.1) — `frappe.db.rollback()` here only undoes the CURRENT uncommitted
	transaction. The engine commits after each successful step, so any step that
	already completed stays deleted. `partial_failure` flags that the chain is now
	partially deleted; recovery is via the pre-cascade backup (revert), not a DB
	rollback. (A pre-deletion gate failure — e.g. the B4 financial block or a
	not-forced skip — aborts with partial_failure=False, nothing deleted.)
	"""
	frappe.db.rollback()
	_mutated = {"deleted", "deleted-via-surgical-sql", "status-reset"}
	completed = [s for s in steps if s.get("success") and s.get("action") in _mutated]
	return {
		"success": False,
		"aborted_step": step_name,
		"steps": steps,
		"lesson_275_fallback_used": lesson_275_used,
		"partial_failure": bool(completed),
		"completed_steps": [s.get("step") for s in completed],
	}


def _reset_token_status(token_name: str, cut_point: str, log_name: str) -> dict:
	"""Partial cascade — the Token is KEPT; reset its status to the real phase
	of the deepest surviving document.

	Mapping (planner-validated against the BBF codebase — only 5 token statuses
	are ever written by lifecycle hooks; 'Quality Done'/'Graded'/'Unloading' are
	dead, so a token with a QI but no GRN genuinely sits at the weighbridge phase):

	  cut_point          deepest surviving   ->  Token.status
	  Purchase Invoice   Purchase Receipt        GRN Created
	  Purchase Receipt   DS / QI / WB            Tare Weighed | Gross Weighed
	  TS Deduction Sheet QI / WB                 Tare Weighed | Gross Weighed
	  TS Quality Inspect WB                      Tare Weighed | Gross Weighed
	  TS Weighbridge Log Gate Entry              PO Linked

	Mirrors the production-proven stores_receiving_api.pr_on_cancel_clear_token
	reset logic. A reset failure ABORTS the cascade (status integrity is NOT
	best-effort — a token left at a stale forward status is the Lesson 275 bug).
	"""
	try:
		if not frappe.db.exists("TS Token", token_name):
			return {"step": "TOK", "action": "reset-skipped-token-gone",
			        "success": True, "name": token_name}

		clear: dict = {}
		if cut_point == "Purchase Invoice":
			new_status = "GRN Created"
		elif cut_point in ("Purchase Receipt", "TS Deduction Sheet", "TS Quality Inspection"):
			# Deepest surviving is the Weighbridge Log (DS/QI carry no own status).
			# tare_weight is `decimal NOT NULL DEFAULT 0.00` — a "no tare yet" WB
			# stores 0.00, never NULL. Rank by tare MAGNITUDE so any WB that
			# actually carries a tare weight wins the tie-break.
			wb = frappe.db.sql(
				"""SELECT tare_weight FROM `tabTS Weighbridge Log`
				   WHERE token_number=%s ORDER BY tare_weight DESC LIMIT 1""",
				(token_name,), as_dict=True,
			)
			has_tare = bool(wb and wb[0].get("tare_weight"))
			new_status = "Tare Weighed" if has_tare else "Gross Weighed"
			clear = {"purchase_receipt": "", "grn_time": None}
			if cut_point == "TS Quality Inspection":
				clear["quality_time"] = None
		elif cut_point == "TS Weighbridge Log":
			# Deepest surviving is the Gate Entry.
			new_status = "PO Linked"
			clear = {"purchase_receipt": "", "grn_time": None, "quality_time": None,
			         "wb_gross_time": None, "wb_tare_time": None, "custom_rst_number": ""}
		else:
			# Defensive fallback — never leave a stale forward status.
			new_status = "PO Linked"

		updates = {"status": new_status}
		# Only clear fields that actually exist on TS Token.
		valid_cols = set(frappe.db.get_table_columns("TS Token"))
		for k, v in clear.items():
			if k in valid_cols:
				updates[k] = v
		frappe.db.set_value("TS Token", token_name, updates, update_modified=False)

		# Audit comment on the token.
		try:
			frappe.get_doc("TS Token", token_name).add_comment(
				"Comment",
				text=(f"[PARTIAL CASCADE] Status reset to '{new_status}' after cut-point "
				      f"'{cut_point}' deletion (cascade log {log_name})."),
			)
		except Exception:
			pass  # comment is best-effort; the status reset is what matters

		frappe.db.commit()
		return {
			"step": "TOK", "action": "status-reset", "name": token_name,
			"new_status": new_status, "cleared_fields": list(clear.keys()),
			"success": True,
		}
	except Exception as e:
		return {
			"step": "TOK", "action": "status-reset-failed", "name": token_name,
			"success": False, "error": f"{type(e).__name__}: {e}",
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
