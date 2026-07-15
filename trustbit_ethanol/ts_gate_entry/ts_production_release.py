# Copyright (c) 2026, Trustbit Technologies and contributors
# For license information, please see license.txt
#
# TS Production — Store-Manager raw-material RELEASE gate (Production Logging).
#
# The SINGLE approval gate in the flow (decision 3 — no CEO gate). Copies the
# proven ts_mr_transfer.py "draft SE + role gate + creator != approver + revert-on-
# cancel" pattern (Pattern C). Orchestrates the Work-Order/Job-Card automation engine
# in ts_production_wo.py.
#
# State machine (ts_variance_status; is_submittable=0):
#   Draft -> Pending Stores Release   submit_for_release()  (PM; creates WO + draft SE)
#   Pending Stores Release -> Released  approve_release()    (Store Mgr; submits SE)
#   Released -> Completed                approve_release()    (same call: Job Cards +
#                                                              Manufacture SE + close WO)
#   Released -> Completed                complete_released_production()  (idempotent re-run)
#   Pending Stores Release -> Rejected   reject_release()     (Store Mgr; cancels WO + SE)
#
# 'Released' is recoverable: the chain spans multiple submits; on any failure AFTER the
# release SE submits we leave the entry at 'Released' (RM is in WIP, WO In Process) and
# the operator re-runs complete_released_production() — never half-completing a WO.
#
# Discipline:
#   - All mutations @frappe.whitelist(methods=["POST"])             (Lesson 175)
#   - Role gate up front + frappe.has_permission(..., throw=True)   (Lesson 224)
#   - ignore_permissions SE inserts paired with explicit has_permission (Lesson 175/224)
#   - Pre-flight ALL hard checks BEFORE any submit; status flips LAST (Lesson 288)
#   - Control-plane fields via db_set (skip save hooks); tamper-guarded (Lesson 162)
#   - Notifications best-effort, each in its own try/except         (Lesson 238/276)

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, escape_html

from trustbit_ethanol.ts_gate_entry import ts_production_api as api
from trustbit_ethanol.ts_gate_entry import ts_production_wo as wo_engine

DOCTYPE = "TS Production Entry"


# ─────────────────────────────────────────────────────────────────────────
#  ROLE HELPERS
# ─────────────────────────────────────────────────────────────────────────

def _is_admin(user=None):
	user = user or frappe.session.user
	return (user == "Administrator") or ("System Manager" in frappe.get_roles(user))


def _is_store_manager(user=None):
	user = user or frappe.session.user
	return "Stores Manager" in frappe.get_roles(user)


def _is_production_manager(user=None):
	"""Production Manager creator role. ERPNext-native Manufacturing roles are reused."""
	user = user or frappe.session.user
	roles = frappe.get_roles(user)
	return ("Manufacturing Manager" in roles) or ("Manufacturing User" in roles)


# ─────────────────────────────────────────────────────────────────────────
#  1) SUBMIT FOR RELEASE  (Production Manager)
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def submit_for_release(name):
	"""Production Manager submits a Draft for the Store-Manager release gate.

	Creates + submits a Work Order (Job Cards auto-create for ops BOMs), builds the
	DRAFT Material-Transfer-for-Manufacture Stock Entry, and flips the entry to
	'Pending Stores Release'. Variance is computed for information only (NO CEO gate).

	State: Draft -> Pending Stores Release.
	"""
	api._require_enabled()
	doc = frappe.get_doc(DOCTYPE, name)
	frappe.has_permission(DOCTYPE, "write", doc=doc, throw=True)

	user = frappe.session.user
	if not (user == doc.owner or _is_production_manager(user) or _is_admin(user)):
		frappe.throw(
			_("Only the creator or a Production Manager can submit this run for release."),
			exc=frappe.PermissionError,
		)

	if doc.ts_variance_status != "Draft":
		frappe.throw(_("Only a Draft entry can be submitted for release (current status: {0}).").format(
			doc.ts_variance_status))

	# Fresh variance + sanity (variance is informational in this flow)
	api.compute_and_set_variance(doc)
	api.validate_entry(doc)
	if flt(doc.actual_produced_qty) <= 0:
		frappe.throw(_("Enter the Actual Produced Qty before submitting for release."))
	if not doc.materials:
		frappe.throw(_("Pull the standard from the BOM and enter the raw materials before submitting."))

	settings = api._get_settings()

	# Pre-flight valuation hard-block BEFORE creating any backing doc (Lesson 288).
	api._block_if_missing_valuation(doc, settings)
	# Pre-flight (14 Jul audit): a department-claimed material MUST be in the BOM,
	# else it would be released into WIP and stranded (Manufacture consumes BOM only).
	from trustbit_ethanol.ts_gate_entry import ts_production_dept_release as dept_rel
	dept_rel.preflight_bom_membership(doc)

	# Capture BOM branch attributes for the release/automation steps.
	attrs = wo_engine.read_bom_attrs(doc.bom)

	# Create + submit the Work Order (ops BOM -> Job Cards auto-create) + build the
	# DRAFT release Stock Entry. Elevated: WO submit triggers NESTED Job Card creation
	# and the SE insert needs perms the creator/PM may not hold (the run is authorized
	# at this gate; the permission branch above admits a non-Manufacturing owner). The
	# control/audit db_set writes below run OUTSIDE this block, as the real user.
	release_se = None
	se_error = None
	with wo_engine.system_session():
		wo_name = wo_engine.create_and_submit_work_order(doc, settings)
		# DRAFT release Stock Entry (docstatus 0 — Store Manager is the gate).
		try:
			release_se = wo_engine.build_draft_release_se(wo_name, settings, doc)
		except frappe.ValidationError:
			raise
		except Exception as e:
			se_error = str(e)
			try:
				frappe.log_error(title="TS Production release SE draft failed", message=f"{name}: {e}")
			except Exception:
				pass

	if not release_se:
		# Roll back the WO so we never leave an orphan submitted WO without a release SE.
		try:
			from erpnext.manufacturing.doctype.work_order.work_order import close_work_order
			with wo_engine.system_session():
				close_work_order(wo_name, "Closed")
		except Exception:
			frappe.clear_messages()
		frappe.db.rollback()
		frappe.throw(
			_("The raw-material release Stock Entry could not be prepared, so the run was NOT "
			  "submitted:") + "<br><br>" + escape_html(se_error or "unknown"),
			title=_("Release Could Not Be Prepared"),
		)

	# Snapshot + control-field writes (db_set — skip save hooks; tamper-guarded).
	doc.db_set("actual_produced_qty_at_submission", flt(doc.actual_produced_qty), update_modified=False)
	doc.db_set("submitted_by", user, update_modified=False)
	doc.db_set("work_order", wo_name, update_modified=False)
	doc.db_set("release_stock_entry", release_se, update_modified=False)
	doc.db_set("bom_with_operations", cint(attrs["with_operations"]), update_modified=False)
	doc.db_set("bom_transfer_material_against", attrs["transfer_material_against"], update_modified=False)
	doc.db_set("material_variance_pct", flt(doc.material_variance_pct), update_modified=False)
	doc.db_set("produced_variance_pct", flt(doc.produced_variance_pct), update_modified=False)
	doc.db_set("variance_breach", cint(doc.variance_breach), update_modified=False)
	doc.db_set("ts_variance_status", "Pending Stores Release", update_modified=False)
	doc.add_comment(
		"Comment",
		_("Submitted for release by {0}. Work Order {1} + draft Release SE {2} created.").format(
			user, wo_name, release_se),
	)
	# v2.21 (client request 13 Jul) — split the release: any run item sourced from a
	# department-claimed warehouse becomes that department's release slot (they enter
	# the ACTUAL qty). No-op when no category claims a warehouse (today's behavior).
	dept_categories = []
	try:
		from trustbit_ethanol.ts_gate_entry import ts_production_dept_release as dept_rel
		created = dept_rel.build_release_slots(doc, release_se) or []
		dept_categories = [frappe.db.get_value("TS Production Release Slot", c, "category")
		                   for c in created]
	except Exception:
		frappe.log_error(title="TS dept release slot build failed",
		                 message=f"{name}: {frappe.get_traceback()}")
		frappe.clear_messages()
	try:
		api._notify_stores_managers_release(doc)
	except Exception:
		frappe.clear_messages()
	frappe.db.commit()
	return {
		"ok": True,
		"ts_variance_status": "Pending Stores Release",
		"work_order": wo_name,
		"release_stock_entry": release_se,
		"department_categories": [c for c in dept_categories if c],
		"message": _("Submitted to the Stores Manager for raw-material release."),
	}


# ─────────────────────────────────────────────────────────────────────────
#  2) APPROVE RELEASE  (Store Manager)  — fires the whole automation chain
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def approve_release(name):
	"""Store Manager approves the raw-material release and runs the automation chain.

	Pre-flights ALL hard checks BEFORE any submit; then submits the release SE,
	auto-completes Job Cards (ops BOMs), submits the Manufacture SE, closes the WO,
	auto-returns surplus WIP, and flips the entry to 'Completed' LAST (Lesson 288).

	State: Pending Stores Release -> Released -> Completed (single call).
	Separation of duties: the submitter may not approve their own release.
	"""
	api._require_enabled()
	doc = frappe.get_doc(DOCTYPE, name)

	if not (_is_store_manager() or _is_admin()):
		frappe.throw(_("Only a Stores Manager can approve the raw-material release."),
					 exc=frappe.PermissionError)
	frappe.has_permission(DOCTYPE, "write", doc=doc, throw=True)

	if doc.ts_variance_status != "Pending Stores Release":
		frappe.throw(_("Only a 'Pending Stores Release' entry can be approved (current status: {0}).").format(
			doc.ts_variance_status))

	# Separation of duties (creator != approver; admin bypass for recovery).
	if doc.submitted_by == frappe.session.user and not _is_admin():
		frappe.throw(
			_("You submitted this run, so you cannot approve its release (separation of duties)."),
			exc=frappe.PermissionError,
		)

	# v2.21 (client request 13 Jul) — the Store Manager is the FINAL gate: every
	# department must release its own material first. No-op when no slots exist.
	from trustbit_ethanol.ts_gate_entry import ts_production_dept_release as dept_rel
	dept_rel.assert_all_slots_released(doc.name)

	if not doc.release_stock_entry or not doc.work_order:
		frappe.throw(_("This entry has no Work Order / release Stock Entry — re-submit it for release."))

	settings = api._get_settings()

	# ── PRE-FLIGHT (BEFORE any submit) ───────────────────────────────────
	# 1) Valuation hard-block (re-check; the form may have changed).
	api._block_if_missing_valuation(doc, settings)
	# 2) Overproduction guard: produced <= WO planned qty.
	wo_qty = flt(frappe.db.get_value("Work Order", doc.work_order, "qty"))
	if flt(doc.actual_produced_qty) > wo_qty + 1e-9:
		frappe.throw(
			_("Produced qty ({0}) exceeds the Work Order planned qty ({1}). "
			  "Reject and re-submit with the correct qty.").format(
				flt(doc.actual_produced_qty), wo_qty),
			title=_("Over-Production"),
		)
	# 3) Stock availability for the release SE rows (avoid NegativeStockError mid-chain).
	shortages = wo_engine.check_stock_available(doc.release_stock_entry)
	if shortages:
		rows = "".join(
			"<li><b>{0}</b> in {1}: need {2}, have {3}</li>".format(
				escape_html(s["item_code"]), escape_html(s["warehouse"]),
				flt(s["required"]), flt(s["available"]),
			) for s in shortages
		)
		frappe.throw(
			_("Not enough stock to release the following raw material(s):")
			+ "<ul style='margin-top:6px'>" + rows + "</ul>"
			+ _("Receive / reconcile the stock first, then approve again."),
			title=_("Insufficient Stock for Release"),
		)

	# ── SUBMIT release SE -> entry becomes recoverable 'Released' ─────────
	# Elevated: authorized at this gate, but the SE submit drives nested docs
	# (Project cost roll-up) whose own perms the Stores Manager doesn't hold.
	with wo_engine.system_session():
		wo_engine.submit_release_se(doc.release_stock_entry)
	released_val = wo_engine.released_value(doc.release_stock_entry)
	doc.db_set("released_by", frappe.session.user, update_modified=False)
	doc.db_set("released_at", now_datetime(), update_modified=False)
	doc.db_set("wip_released_qty", flt(released_val), update_modified=False)
	doc.db_set("ts_variance_status", "Released", update_modified=False)
	doc.add_comment(
		"Comment",
		_("Released by {0}. Release SE {1} submitted (material moved Stores → WIP).").format(
			frappe.session.user, doc.release_stock_entry),
	)
	frappe.db.commit()  # checkpoint — RM is in WIP; the rest is recoverable.

	# ── Run the rest of the chain (idempotent) ───────────────────────────
	actor = frappe.session.user
	with wo_engine.system_session():
		return _run_completion_chain(doc.name, settings, actor=actor)


# ─────────────────────────────────────────────────────────────────────────
#  3) REJECT RELEASE  (Store Manager)
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def reject_release(name, reason):
	"""Reject the release: cancels the draft release SE + the WO (and its
	auto-created Job Cards). State: Pending Stores Release -> Rejected.

	Business rule (client, 3 Jul): a Store Manager may ONLY Release — never reject.
	Reject is retained as an ADMIN-ONLY recovery escape-hatch (Lesson 142: enforce
	the role restriction server-side, not just by hiding the button)."""
	api._require_enabled()
	reason = (reason or "").strip()
	if len(reason) < 10:
		frappe.throw(_("Rejection reason must be at least 10 characters."))

	doc = frappe.get_doc(DOCTYPE, name)
	if not _is_admin():
		frappe.throw(_("The raw-material release cannot be rejected by a Store Manager. "
					   "Store Managers release only."),
					 exc=frappe.PermissionError)
	frappe.has_permission(DOCTYPE, "write", doc=doc, throw=True)

	if doc.ts_variance_status != "Pending Stores Release":
		frappe.throw(_("Only a 'Pending Stores Release' entry can be rejected (current status: {0}).").format(
			doc.ts_variance_status))

	# Tear down the draft release SE (never submitted yet) + the WO + its Job Cards.
	# Detach the link FIRST so se.delete() doesn't hit LinkExistsError (the PE still
	# pointed at the SE, so the delete was silently failing and orphaning the draft).
	rel_se = doc.release_stock_entry
	doc.db_set("release_stock_entry", None, update_modified=False)
	# Elevated: cancelling the WO + its Job Cards (and any nested on_cancel side-effects)
	# needs perms the Stores Manager doesn't hold; authorized at this gate. Audit writes
	# (rejected_by / comment) happen OUTSIDE the block, as the real user.
	with wo_engine.system_session():
		if rel_se:
			try:
				se = frappe.get_doc("Stock Entry", rel_se)
				if se.docstatus == 0:
					se.flags.ignore_permissions = True
					se.delete()
			except Exception:
				frappe.clear_messages()
		if doc.work_order:
			_cancel_work_order_and_job_cards(doc.work_order)

	doc.db_set("ts_variance_status", "Rejected", update_modified=False)
	doc.db_set("rejected_by", frappe.session.user, update_modified=False)
	doc.db_set("rejection_reason", reason, update_modified=False)
	doc.add_comment(
		"Comment",
		_("Release rejected by {0}. Reason: {1}").format(frappe.session.user, escape_html(reason)),
	)
	try:
		api._notify_creator(doc, "rejected")
	except Exception:
		frappe.clear_messages()
	frappe.db.commit()
	return {"ok": True, "ts_variance_status": "Rejected"}


# ─────────────────────────────────────────────────────────────────────────
#  4) COMPLETE RELEASED PRODUCTION  (idempotent recovery from 'Released')
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def complete_released_production(name):
	"""Re-run the post-release chain (Job Cards -> Manufacture SE -> close WO ->
	surplus return) from a recoverable 'Released' state. Idempotent: skips already-
	Completed Job Cards and an already-submitted Manufacture SE. State: Released ->
	Completed."""
	api._require_enabled()
	doc = frappe.get_doc(DOCTYPE, name)
	if not (_is_store_manager() or _is_admin()):
		frappe.throw(_("Only a Stores Manager can complete a released production run."),
					 exc=frappe.PermissionError)
	frappe.has_permission(DOCTYPE, "write", doc=doc, throw=True)

	if doc.ts_variance_status != "Released":
		frappe.throw(_("Only a 'Released' entry can be completed (current status: {0}).").format(
			doc.ts_variance_status))

	settings = api._get_settings()
	actor = frappe.session.user
	with wo_engine.system_session():
		return _run_completion_chain(doc.name, settings, actor=actor)


# ─────────────────────────────────────────────────────────────────────────
#  COMPLETION CHAIN  (steps 7-10) — idempotent, never half-completes a WO
# ─────────────────────────────────────────────────────────────────────────

def _run_completion_chain(name, settings, actor=None):
	"""Steps 7-10: auto-complete Job Cards -> Manufacture SE -> close WO ->
	auto-return surplus -> WIP reconciliation -> flip to Completed.

	On ANY exception the entry stays 'Released' (RM in WIP, WO In Process) and the
	operator retries via complete_released_production() — RM is never re-transferred,
	Job Cards are never double-completed, the Manufacture SE is never duplicated."""
	doc = frappe.get_doc(DOCTYPE, name)
	wo_name = doc.work_order
	company = frappe.db.get_value("Work Order", wo_name, "company")

	try:
		# 7a — auto-complete Job Cards (no-op for no-ops BOMs).
		if cint(doc.bom_with_operations):
			wo_engine.auto_complete_job_cards(wo_name)

		# 8 — Manufacture SE (consumes WIP per BOM-scaled qty). Idempotent.
		manufacture_se = wo_engine.submit_manufacture_se(wo_name, company=company)
		if not manufacture_se:
			manufacture_se = frappe.db.get_value(
				"Stock Entry",
				{"work_order": wo_name, "purpose": "Manufacture", "docstatus": 1},
				"name",
			)

		# 9 — close the Work Order if it didn't auto-Complete.
		wo_engine.close_work_order_if_needed(wo_name)

		# 10a — auto-return surplus WIP -> Stores (decision 4), if enabled.
		return_se = None
		if settings.get("auto_return_surplus"):
			return_se, _surplus = wo_engine.auto_return_surplus(wo_name, settings)

	except Exception as e:
		# Stay recoverable at 'Released'; surface a clear, retryable error.
		try:
			frappe.log_error(title="TS Production completion chain failed", message=f"{name}: {e}")
		except Exception:
			pass
		frappe.db.commit()  # persist the 'Released' checkpoint + any partial Job-Card progress
		frappe.throw(
			_("The production could not be completed. The raw material is already in WIP, so the "
			  "run is kept at 'Released' — fix the issue and click 'Complete Production' to retry. "
			  "Details:") + "<br><br>" + escape_html(str(e)),
			title=_("Completion Failed — Retry from Released"),
		)

	# WIP reconciliation (released vs BOM-consumed).
	consumed_val = wo_engine.manufacture_consumed_value(manufacture_se)
	released_val = flt(doc.wip_released_qty)
	recon_pct = ((released_val - consumed_val) / consumed_val * 100.0) if consumed_val else 0.0
	note = _build_recon_note(released_val, consumed_val, recon_pct, return_se, settings)

	# Control-field writes — status flips LAST (Lesson 288).
	doc.db_set("linked_stock_entry", manufacture_se, update_modified=False)
	doc.db_set("wip_consumed_qty", flt(consumed_val), update_modified=False)
	doc.db_set("wip_reconcile_variance_pct", flt(recon_pct), update_modified=False)
	if return_se:
		doc.db_set("wip_returned_stock_entry", return_se, update_modified=False)
	doc.db_set("wip_reconcile_note", note, update_modified=False)
	doc.db_set("posted_by", actor or frappe.session.user, update_modified=False)
	doc.db_set("ts_variance_status", "Completed", update_modified=False)
	doc.add_comment(
		"Comment",
		_("Completed by {0}. Manufacture SE {1}. {2}").format(
			actor or frappe.session.user, manufacture_se, note),
	)
	frappe.db.commit()
	return {
		"ok": True,
		"ts_variance_status": "Completed",
		"stock_entry": manufacture_se,
		"release_stock_entry": doc.release_stock_entry,
		"wip_returned_stock_entry": return_se,
		"message": _("Production completed. Manufacture Stock Entry {0} created.").format(manufacture_se),
	}


def _build_recon_note(released_val, consumed_val, recon_pct, return_se, settings):
	delta = flt(released_val) - flt(consumed_val)
	tol = settings.get("wip_recon_tolerance_pct")
	flag = ""
	if tol is not None and abs(flt(recon_pct)) > flt(tol):
		flag = " (exceeds the {0}% reconciliation tolerance)".format(flt(tol))
	if abs(delta) <= 1e-6:
		return _("Released material equals BOM-consumed — no WIP leftover.")
	if delta > 0:
		base = _("Over-released by {0} (released {1} / consumed {2}){3}.").format(
			flt(delta), flt(released_val), flt(consumed_val), flag)
		if return_se:
			base += " " + _("Surplus auto-returned WIP → Stores via {0}.").format(return_se)
		else:
			base += " " + _("Surplus left in WIP (auto-return off or no distinct Stores warehouse).")
		return base
	return _("Under-released by {0} (released {1} / consumed {2}){3}.").format(
		abs(flt(delta)), flt(released_val), flt(consumed_val), flag)


def _cancel_work_order_and_job_cards(wo_name):
	"""Cancel a Work Order + its auto-created Job Cards (used on reject). The WO has
	no submitted SE at this point, so it is cancellable; Job Cards cancel first."""
	try:
		for jc in frappe.get_all("Job Card", filters={"work_order": wo_name}, pluck="name"):
			try:
				jc_doc = frappe.get_doc("Job Card", jc)
				if jc_doc.docstatus == 1:
					jc_doc.flags.ignore_permissions = True
					jc_doc.cancel()
				if jc_doc.docstatus in (0, 2):
					jc_doc.flags.ignore_permissions = True
					jc_doc.delete()
			except Exception:
				frappe.clear_messages()
		wo = frappe.get_doc("Work Order", wo_name)
		if wo.docstatus == 1:
			wo.flags.ignore_permissions = True
			wo.cancel()
	except Exception:
		# Best-effort teardown — never block the rejection.
		frappe.clear_messages()
