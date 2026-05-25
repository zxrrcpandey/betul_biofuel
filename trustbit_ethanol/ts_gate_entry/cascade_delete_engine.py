"""Cascade Delete Engine (v2.11.2) — pure orchestration of the
PI → PR → DS → QI → WB → GE → Token deletion chain.

Refactored from the production-proven `_batch_token_delete_optA.py` script
(19 May 2026 Round 44, 5/5 deterministic). Each deletion step is idempotent.

ATOMICITY (v2.11.1 audit M1 — corrected): this engine is NOT a single
transaction. It commits after each successful step, so a mid-cascade failure
leaves the already-committed steps deleted (a partially-deleted chain) and
`_abort()` returns `partial_failure=True` listing the completed steps.
Recovery from a partial failure is via the pre-cascade backup (Hardening B/M
revert), NOT a DB rollback. The B4 financial-link gate runs BEFORE any commit.

v2.11.2 — partial cascade RE-ENABLED. The v2.11.0 `_reset_token_status` static
map was demolished by the 22-May audit B1 (real demo data has tokens at
statuses the planner called "dead"). Replaced with a derive-from-deepest-
surviving-doc approach + a pre-cascade `_validate_partial_cut_feasibility`
that runs BEFORE Step 1 and fails closed on any unmapped (current_status,
cut_point) pair. The decision matrix `_CD_PARTIAL_MATRIX` is the explicit
allowlist. New lessons 278 (state-machine inverse must derive, not map) +
279 (pre-cascade validation belongs with destructive engines).

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
262 (Option B surgical SQL pattern) / 275 (GE cancel tamper-guard fallback) /
278 (derive-from-survivor) / 279 (pre-cascade validation).
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

# v2.11.2 — cut-points that delete the TS Token outright (Token is NOT kept).
# `None` and `TS Gate Entry` both fully delete the chain incl. Token. The user
# decision (23-May-2026) was "engine wins — GE cut = Full Chain" so the JS
# preview text now matches this behaviour.
_FULL_CHAIN_CUT_POINTS = (None, "TS Gate Entry")

# v2.11.2 PARTIAL CASCADE DECISION MATRIX — the allowlist of safe
# (current_token_status, cut_point) → new_status transitions.
#
# A partial cut keeps the Token alive. The reset is DERIVED from the deepest
# surviving doc after the cut (NOT from a static cut_point → status map; see
# Lesson 278). This matrix is the cross-check: for every (current_status,
# cut_point) cell, EITHER a safe target status is enumerated OR the cell
# explicitly says "ABORT" with a reason (fail-closed, Lesson 279). Any pair
# missing from the matrix is also ABORT (defensive default).
#
# Justification of every non-ABORT cell:
#   - GRN Created × PI cut  → GRN Created (only PI deleted; PR survives)
#   - GRN Created × PR/DS/QI/WB cuts → mirrors stores_receiving_api.
#     pr_on_cancel_clear_token (1172-1199) which is the production-proven
#     reverse hook BBPL has used since v2.5.
#   - Tare Weighed × PR/DS/QI cuts → keep Tare Weighed (WB-with-tare survives).
#   - Gross Weighed × PR/DS/QI cuts → keep Gross Weighed (WB-gross survives).
#   - Tare/Gross × WB cut → PO Linked (only GE survives, matches
#     ts_gate_entry.update_token_status:227).
#   - Plant Exited / Campus Exited / Exited → ABORT (token has physically left
#     the plant; reverting to an earlier phase would leave the physical-state
#     pointer dangling — full chain delete is still allowed because it's a
#     "this never happened" rewrite, but partial reset is unsafe).
#   - Unloading / Quality Done / Graded → ABORT (legacy values; current code
#     never writes them, so the desired end-state semantics are undefined).
#   - Token Generated / PO Linked → ABORT (n/a; the cut-point doc doesn't
#     exist for this chain shape — no PI/PR/DS/QI/WB at this phase).
#
# Stock-OUT statuses (SI Linked, Gross Recorded, Tare Recorded, Loading Done,
# Dispatch Ready) ABORT for any cut — cascade tool is RM-only.
_CD_PARTIAL_MATRIX = {
	# (current_status, cut_point) → "new_status" or ("ABORT", "reason")
	("GRN Created",   "Purchase Invoice"):    "GRN Created",
	("GRN Created",   "Purchase Receipt"):    "Tare Weighed",
	("GRN Created",   "TS Deduction Sheet"):  "Tare Weighed",
	("GRN Created",   "TS Quality Inspection"): "Tare Weighed",
	("GRN Created",   "TS Weighbridge Log"):  "PO Linked",
	("Tare Weighed",  "Purchase Receipt"):    "Tare Weighed",
	("Tare Weighed",  "TS Deduction Sheet"):  "Tare Weighed",
	("Tare Weighed",  "TS Quality Inspection"): "Tare Weighed",
	("Tare Weighed",  "TS Weighbridge Log"):  "PO Linked",
	("Gross Weighed", "Purchase Receipt"):    "Gross Weighed",
	("Gross Weighed", "TS Deduction Sheet"):  "Gross Weighed",
	("Gross Weighed", "TS Quality Inspection"): "Gross Weighed",
	("Gross Weighed", "TS Weighbridge Log"):  "PO Linked",
	# Quality Done — legacy status, current code never writes it (audit B1).
	# A partial cut from here has undefined semantics → fail-closed.
	# Full Chain delete still works because the Token is removed entirely.
}

# Fields cleared on TS Token when the new_status is each value (mirrors
# stores_receiving_api.pr_on_cancel_clear_token).
_CD_CLEAR_FIELDS_BY_STATUS = {
	"GRN Created":  {},
	"Tare Weighed": {"purchase_receipt": "", "grn_time": None, "quality_time": None},
	"Gross Weighed": {"purchase_receipt": "", "grn_time": None, "quality_time": None,
	                  "wb_tare_time": None},
	"PO Linked":    {"purchase_receipt": "", "grn_time": None, "quality_time": None,
	                 "wb_gross_time": None, "wb_tare_time": None, "custom_rst_number": ""},
}

# Reasons used by `_validate_partial_cut_feasibility` ABORT branches.
_LEGACY_STATUSES = ("Unloading", "Quality Done", "Graded")
_POST_EXIT_STATUSES = ("Plant Exited", "Campus Exited", "Exited")
_STOCK_OUT_STATUSES = ("SI Linked", "Gross Recorded", "Tare Recorded",
                       "Loading Done", "Dispatch Ready")
_PRE_CHAIN_STATUSES = ("Token Generated", "PO Linked", "G1 Entered", "G2 Entered")


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


def _validate_partial_cut_feasibility(token_name: str, cut_point: str) -> dict:
	"""v2.11.2 — pre-cascade fail-closed feasibility check (Lesson 279).

	Runs BEFORE any deletion. Reads the Token's CURRENT status, looks it up in
	`_CD_PARTIAL_MATRIX`, returns either:
	  {"ok": True, "current_status": ..., "projected_new_status": ...}
	  {"ok": False, "error": "<reason>"}

	Any (current_status, cut_point) pair NOT in the matrix → ABORT. The matrix
	is the explicit allowlist — defensive default is ABORT. This means a
	partial cut on a token at a legacy / post-exit / stock-OUT status fails
	before anything is committed, with a clear operator-facing reason.
	"""
	current = frappe.db.get_value("TS Token", token_name, "status")
	if not current:
		return {"ok": False, "error": (
			f"Token {token_name} has no status — partial cut requires a known "
			"current state. Use Full Chain or fix the Token record."
		)}

	# Pre-chain — no document to cut at this phase.
	if current in _PRE_CHAIN_STATUSES:
		return {"ok": False, "error": (
			f"Token {token_name} is at '{current}'. The chain has not yet "
			f"reached '{cut_point}'. Use Full Chain to delete the Token, or "
			"wait until the chain progresses."
		)}

	# Post-exit — semantically unsound to revert.
	if current in _POST_EXIT_STATUSES:
		return {"ok": False, "error": (
			f"Token {token_name} is at '{current}' (vehicle has left the plant). "
			"Partial cut cannot revert a token that has physically exited — the "
			"forward-state pointer would dangle. Use Full Chain (delete the "
			"entire chain) if a re-entry is needed."
		)}

	# Legacy statuses — current code never writes them.
	if current in _LEGACY_STATUSES:
		return {"ok": False, "error": (
			f"Token {token_name} is at the legacy status '{current}' which the "
			"current codebase never writes. Partial cut semantics are undefined "
			"here. Either amend the Token status to a current-code value first, "
			"or use Full Chain."
		)}

	# Stock-OUT path — cascade is RM-only.
	if current in _STOCK_OUT_STATUSES:
		return {"ok": False, "error": (
			f"Token {token_name} is at the Stock-OUT status '{current}'. The "
			"cascade tool only supports RM (Stock-IN) flows."
		)}

	# Matrix lookup — defensive default is ABORT.
	target = _CD_PARTIAL_MATRIX.get((current, cut_point))
	if target is None:
		return {"ok": False, "error": (
			f"Partial cut from current status '{current}' at cut-point "
			f"'{cut_point}' has no enumerated safe target. The (status × cut-"
			"point) cell is not in the allowlist."
		)}

	return {"ok": True, "current_status": current, "projected_new_status": target}


def _detect_deepest_surviving_doc(token_name: str) -> str:
	"""Return the deepest survivor's doctype after the cascade steps have run.

	Walks PI → PR → DS → QI → WB → GE in that order; the FIRST doctype that
	still has a row for this token is the survivor. Used by `_reset_token_status`
	to derive the new Token status independently from the cut_point.

	The 'WB with tare' vs 'WB gross-only' distinction is encoded via the
	returned label — caller looks at the label to pick between Tare Weighed
	and Gross Weighed.

	Returns one of: "Purchase Invoice", "Purchase Receipt", "TS Deduction Sheet",
	"TS Quality Inspection", "TS Weighbridge Log (tare)",
	"TS Weighbridge Log (gross)", "TS Gate Entry", or "none".
	"""
	if frappe.db.exists("Purchase Invoice", {"ts_token": token_name}):
		return "Purchase Invoice"
	if frappe.db.exists("Purchase Receipt", {"ts_token": token_name}):
		return "Purchase Receipt"
	# DS is linked via QI; if a QI exists, check if any DS still links to it.
	qi_names = frappe.db.sql_list(
		"SELECT name FROM `tabTS Quality Inspection` WHERE token_number=%s",
		(token_name,),
	)
	if qi_names:
		if frappe.db.sql(
			"SELECT 1 FROM `tabTS Deduction Sheet` WHERE quality_inspection IN %s LIMIT 1",
			(tuple(qi_names),),
		):
			return "TS Deduction Sheet"
		return "TS Quality Inspection"
	wb = frappe.db.sql(
		"""SELECT tare_weight FROM `tabTS Weighbridge Log`
		   WHERE token_number=%s ORDER BY tare_weight DESC LIMIT 1""",
		(token_name,), as_dict=True,
	)
	if wb:
		# tare_weight is `decimal NOT NULL DEFAULT 0.00` — magnitude-based
		# tie-break (Lesson 275 v2.11.0.2 fix).
		return ("TS Weighbridge Log (tare)" if wb[0].get("tare_weight")
		        else "TS Weighbridge Log (gross)")
	if frappe.db.exists("TS Gate Entry", {"token_number": token_name}):
		return "TS Gate Entry"
	return "none"


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
	is_partial = cut_point not in _FULL_CHAIN_CUT_POINTS
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

		# v2.11.2 (Lesson 279) — pre-cascade feasibility check for PARTIAL cuts.
		# Runs BEFORE any deletion. If the (current_status, cut_point) pair is
		# not in `_CD_PARTIAL_MATRIX`, abort cleanly — nothing committed.
		# Full Chain skips this gate (Token is deleted; no inverse-mapping needed).
		if is_partial:
			feas = _validate_partial_cut_feasibility(token_name, cut_point)
			if not feas.get("ok"):
				steps.append({
					"step": "VAL", "action": "partial-cut-unsafe",
					"success": False, "error": feas.get("error"),
				})
				return _abort(steps, "VAL", lesson_275_used)
			steps.append({
				"step": "VAL", "action": "partial-cut-validated",
				"current_status": feas["current_status"],
				"projected_new_status": feas["projected_new_status"],
				"success": True,
			})

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

		# Step 7 — Token: DELETE for full cascade (cut_point=None or "TS Gate
		# Entry"); RESET status for a partial cut. `is_partial` was set at the
		# top using `_FULL_CHAIN_CUT_POINTS` (v2.11.2 — engine wins on GE cut).
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
	"""Partial cascade — the Token is KEPT; reset its status using the
	v2.11.2 derive-from-deepest-surviving-doc algorithm (Lesson 278).

	Two-stage decision:
	  1. Read CURRENT status, cross-check against `_CD_PARTIAL_MATRIX` for the
	     EXPECTED post-cut status. (The pre-cascade `_validate_partial_cut_
	     feasibility` already proved this combination is in the allowlist;
	     we re-check here as TOCTOU defence — status may have changed between
	     CEO approval and execute.)
	  2. CALL `_detect_deepest_surviving_doc` to find what actually survived.
	     If the survivor's implied status disagrees with the matrix's expected
	     status, ABORT (status integrity > best-effort). This catches the case
	     where a doc that the chain snapshot showed as present silently failed
	     to delete or the chain shape changed unexpectedly.
	  3. Apply the new status + clear fields per `_CD_CLEAR_FIELDS_BY_STATUS`.

	Mirrors stores_receiving_api.pr_on_cancel_clear_token (lines 1172-1199),
	the production-proven reverse hook. A reset failure ABORTS the cascade.

	Returns {step, action, success, name, new_status?, cleared_fields?,
	         deepest_survivor?, error?}.
	"""
	try:
		if not frappe.db.exists("TS Token", token_name):
			return {"step": "TOK", "action": "reset-skipped-token-gone",
			        "success": True, "name": token_name}

		current = frappe.db.get_value("TS Token", token_name, "status")
		expected = _CD_PARTIAL_MATRIX.get((current, cut_point))
		if expected is None:
			# Should not happen — _validate_partial_cut_feasibility already
			# rejected this combination. TOCTOU safety net.
			return {
				"step": "TOK", "action": "status-reset-failed", "name": token_name,
				"success": False,
				"error": (f"TOCTOU: current status '{current}' at cut '{cut_point}' "
				          "is not in the partial-cut allowlist. Token status may "
				          "have changed between approval and execute."),
			}

		# Cross-check via the derive-from-survivor path. If the survivor's
		# implied status differs from the matrix's expected, fail closed.
		survivor = _detect_deepest_surviving_doc(token_name)
		survivor_implied = {
			"Purchase Receipt": "GRN Created",
			"TS Deduction Sheet": ("Tare Weighed" if current == "Tare Weighed"
			                       else expected),
			"TS Quality Inspection": ("Tare Weighed" if current == "Tare Weighed"
			                          else expected),
			"TS Weighbridge Log (tare)": "Tare Weighed",
			"TS Weighbridge Log (gross)": "Gross Weighed",
			"TS Gate Entry": "PO Linked",
		}.get(survivor)
		if survivor_implied is None or survivor_implied != expected:
			return {
				"step": "TOK", "action": "status-reset-failed", "name": token_name,
				"success": False,
				"error": (
					f"Survivor cross-check failed: deepest surviving doc is "
					f"'{survivor}' (implies status '{survivor_implied}') but matrix "
					f"expected '{expected}' for ({current!r}, {cut_point!r}). "
					"Chain shape diverged from the pre-cascade snapshot — partial "
					"reset aborted."
				),
			}

		new_status = expected
		clear = _CD_CLEAR_FIELDS_BY_STATUS.get(new_status, {})
		updates = {"status": new_status}
		# Only clear fields that actually exist on TS Token.
		valid_cols = set(frappe.db.get_table_columns("TS Token"))
		for k, v in clear.items():
			if k in valid_cols:
				updates[k] = v
		frappe.db.set_value("TS Token", token_name, updates, update_modified=False)

		# Audit comment on the token (best-effort — status reset is authoritative).
		try:
			frappe.get_doc("TS Token", token_name).add_comment(
				"Comment",
				text=(f"[PARTIAL CASCADE] Status reset {current!r} → {new_status!r} "
				      f"after cut-point {cut_point!r} deletion (cascade log "
				      f"{log_name}; deepest survivor: {survivor})."),
			)
		except Exception:
			pass

		frappe.db.commit()
		return {
			"step": "TOK", "action": "status-reset", "name": token_name,
			"prev_status": current, "new_status": new_status,
			"deepest_survivor": survivor,
			"cleared_fields": list(clear.keys()),
			"success": True,
		}
	except Exception as e:
		return {
			"step": "TOK", "action": "status-reset-failed", "name": token_name,
			"success": False, "error": f"{type(e).__name__}: {e}",
		}


# ---------------------------------------------------------------- orphan-trail scan


def scan_orphans(token_name: str, cut_point: str | None = None) -> dict:
	"""Post-delete invariant check (Hardening G).

	v2.11.2 — cut_point-aware. For a Full Chain cascade, every chain doctype
	must be at 0 rows for the token. For a partial cut, doctypes ABOVE the
	cut (i.e. that were KEPT by the engine's `skipped-below-cutpoint` steps)
	are NOT orphans — they are intentional survivors. Pass `cut_point=None`
	or "TS Gate Entry" (the Full Chain cut-points) to get the strict check.
	Pass the actual partial cut_point to exempt the kept doctypes.

	Returns {orphans: {label: count}, orphan_count, clean, kept: [labels]}.
	The `kept` array lists doctype labels intentionally skipped from orphan
	accounting; their row counts are still reported under `kept_rows` for
	forensic visibility but do NOT contribute to `orphan_count`.
	"""
	checks: dict = {}
	kept_rows: dict = {}
	# Decide which doctypes should be 0 (orphan-checked) vs left alone.
	# A partial cut at cut_point X deletes index <= cut_idx; survivors are
	# steps below the cut (higher index in _DELETION_STEPS).
	if cut_point in _FULL_CHAIN_CUT_POINTS:
		check_codes = {"PI", "PR", "DS", "QI", "WB", "GE"}
	else:
		cut_idx = _cut_index(cut_point)
		check_codes = {code for i, (code, _dt) in enumerate(_DELETION_STEPS)
		               if i <= cut_idx}
	# Map deletion-step code → (label, table, fk-column)
	_CHAIN_TABLES = [
		("PI", "Purchase Invoice", "tabPurchase Invoice", "ts_token"),
		("PR", "Purchase Receipt", "tabPurchase Receipt", "ts_token"),
		# DS has no direct token_number FK on tabTS Deduction Sheet; it's
		# only reachable via QI. Skip orphan-counting DS rows directly
		# (a stranded DS implies its parent QI is also stranded — caught
		# by the QI check).
		("QI", "TS Quality Inspection", "tabTS Quality Inspection", "token_number"),
		("WB", "TS Weighbridge Log", "tabTS Weighbridge Log", "token_number"),
		("GE", "TS Gate Entry", "tabTS Gate Entry", "token_number"),
	]
	for code, label, dt, fn in _CHAIN_TABLES:
		try:
			c = frappe.db.sql(
				f"SELECT COUNT(*) FROM `{dt}` WHERE {fn}=%s",
				(token_name,),
			)[0][0]
			if code in check_codes:
				checks[label] = c
			else:
				kept_rows[label] = c
		except Exception as e:
			checks[label] = f"error: {e}"
	# Comments / Versions — for a FULL cascade the Token itself is gone, so
	# any leftover reference IS an orphan. For a partial cut the Token is
	# kept, so these are EXPECTED (they're the Token's audit trail). Move
	# them to kept_rows in the partial case.
	cmt = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabComment` "
		"WHERE reference_doctype='TS Token' AND reference_name=%s",
		(token_name,),
	)[0][0]
	ver = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabVersion` "
		"WHERE ref_doctype='TS Token' AND docname=%s",
		(token_name,),
	)[0][0]
	if cut_point in _FULL_CHAIN_CUT_POINTS:
		checks["Comment[TS Token ref]"] = cmt
		checks["Version[TS Token docname]"] = ver
	else:
		kept_rows["Comment[TS Token ref]"] = cmt
		kept_rows["Version[TS Token docname]"] = ver
	total = sum(v for v in checks.values() if isinstance(v, int))
	return {
		"orphans": checks,
		"orphan_count": total,
		"clean": total == 0,
		"kept": sorted(kept_rows.keys()),
		"kept_rows": kept_rows,
		"cut_point": cut_point or "Full Chain",
	}
