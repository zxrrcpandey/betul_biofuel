# Copyright (c) 2026, Trustbit Technologies and contributors
# For license information, please see license.txt
#
# TS Production — Work-Order + Job-Card automation engine (Production Logging).
#
# Pure helper module (NO whitelisted endpoints — it is called only from the
# Store-Manager release gate in ts_production_release.py). Every method here was
# proven LIVE on demo on the no-ops BOM (BOM-10-02-0001-015) and the ops BOM
# (BOM-10-02-0001-001, 4 operations / 4 Job Cards).
#
# Verified facts baked in:
#   - Work Order MUST carry company = BOM.company (NOT the global default company),
#     wip + fg (+ scrap) warehouses, planned_start_date, AND a source_warehouse on
#     EVERY required item (BOM rows have a blank source).
#   - On submit, an operations BOM auto-creates one Job Card per operation.
#   - Raw-material RELEASE = "Material Transfer for Manufacture" Stock Entry; built
#     DRAFT (docstatus 0) so the Store Manager is the gate, submitted on approve.
#       * transfer_material_against == "Work Order" -> ONE WO-level transfer.
#       * transfer_material_against == "Job Card"   -> one transfer per Job Card.
#   - Job-Card auto-complete = append ONE time_logs row with
#     completed_qty (+ process_loss_qty) == for_quantity EXACTLY, from_time < to_time,
#     non-overlapping windows, submitted in sequence_id order.
#   - Manufacture SE = make_stock_entry(wo, "Manufacture", qty); WO auto-Completed
#     when produced_qty == qty; close_work_order only if not already Completed.
#
# Idempotency (Lesson 288): callers pre-flight ALL hard checks; every step here is
# re-runnable from a recoverable 'Released' state (skip already-Completed Job Cards,
# skip an already-submitted Manufacture SE) so a Work Order is never half-completed.

import contextlib

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, add_to_date, get_datetime

RELEASE_PURPOSE = "Material Transfer for Manufacture"


# ─────────────────────────────────────────────────────────────────────────
#  COST CENTRE RESOLUTION  (v2.21 UAT fix ② — department-wise via category)
# ─────────────────────────────────────────────────────────────────────────

def resolve_production_cost_center(pe):
	"""The authoritative cost centre for a production run's Stock Entries.

	Department-wise (user decision): the run's main TS Production BOM Category
	carries a `cost_center`. Resolution order:
	  1. the connector's main (is_production=1) category cost_center;
	  2. any active category whose BOM output item == the run's production item;
	  3. TS Settings.ts_production_mr_cost_center (the existing MR fallback);
	  4. the company's default cost centre.
	Returns a cost-centre name (may be the company default) or None if even that
	is unset — callers leave the SE row's own default in that case."""
	cc = None
	# 1) connector main category
	conn = getattr(pe, "bom_connector", None)
	if conn:
		main_cat = frappe.db.get_value("TS BOM Connector", conn, "main_category")
		if main_cat:
			cc = frappe.db.get_value("TS Production BOM Category", main_cat, "cost_center")
	# 2) category whose is_production BOM matches the run's item
	if not cc and getattr(pe, "production_item", None):
		cats = frappe.get_all("TS Production BOM Category",
		                      filters={"active": 1, "is_production": 1},
		                      fields=["name", "cost_center"], limit=0)
		for c in cats:
			if c.cost_center:
				cc = c.cost_center
				break
	# 3) the existing MR cost-centre setting
	if not cc:
		cc = frappe.db.get_single_value("TS Settings", "ts_production_mr_cost_center")
	# 4) company default
	if not cc and getattr(pe, "company", None):
		cc = frappe.get_cached_value("Company", pe.company, "cost_center")
	return cc or None


def apply_cost_center_to_se(se, cost_center):
	"""Force the resolved cost centre onto every Stock Entry row (authoritative
	header->rows, same discipline as the Material Issue fix, L301-304)."""
	if not cost_center:
		return
	for row in (se.items or []):
		row.cost_center = cost_center


# ─────────────────────────────────────────────────────────────────────────
#  BOM ATTRIBUTES (read up-front — drive the branching)
# ─────────────────────────────────────────────────────────────────────────

def read_bom_attrs(bom_name):
	"""Return {company, with_operations, transfer_material_against} for a BOM.
	Used to capture bom_with_operations + bom_transfer_material_against at draft."""
	row = frappe.db.get_value(
		"BOM", bom_name,
		["company", "with_operations", "transfer_material_against"],
		as_dict=True,
	) or {}
	return {
		"company": row.get("company"),
		"with_operations": cint(row.get("with_operations")),
		"transfer_material_against": row.get("transfer_material_against") or "Work Order",
	}


# ─────────────────────────────────────────────────────────────────────────
#  STEP 3-4 — CREATE + SUBMIT WORK ORDER
# ─────────────────────────────────────────────────────────────────────────

def create_and_submit_work_order(pe, settings, skip_transfer=0):
	"""Build + submit a Work Order for a TS Production Entry. Returns the WO name.

	pe       : the TS Production Entry document
	settings : dict from ts_production_api._get_settings()
	skip_transfer: 1 for the Multiple flow (Phase D) — its raw material reaches WIP
	    via the auto-Material-Request's plain Material Transfer SE, which does NOT
	    count toward WO.material_transferred_for_manufacture; skip_transfer lets the
	    Manufacture SE post without transfer records (consumption is forced from WIP
	    by the distribution builder). Single flow stays 0 (unchanged).

	CRITICAL: company = BOM.company (verified blocker #1); per-item source_warehouse
	is assigned from the PE material row, else the release-source default, else WIP
	(verified blocker #2). required_items are overwritten from the PM's edited PE
	materials (drops PM-removed lines) — this honors the RELEASE only; the BOM still
	governs Manufacture-SE consumption (decision 2).
	"""
	bom = frappe.get_doc("BOM", pe.bom)
	company = bom.company  # NEVER the global default (verified)
	wip = settings.get("wip_warehouse")
	fg = settings.get("fg_warehouse")
	scrap = settings.get("byproduct_warehouse")
	src_default = settings.get("release_source_warehouse") or wip

	qty = flt(pe.actual_produced_qty)
	if qty <= 0:
		frappe.throw(_("Actual Produced Qty must be greater than zero to create a Work Order."))
	if not wip or not fg:
		frappe.throw(_("Configure the WIP and Finished-Goods warehouses in TS Settings first."),
					 title=_("Production Warehouses Not Set"))

	wo = frappe.new_doc("Work Order")
	wo.production_item = bom.item
	wo.bom_no = bom.name
	wo.company = company
	wo.qty = qty
	wo.fg_warehouse = fg
	wo.wip_warehouse = wip
	if scrap:
		wo.scrap_warehouse = scrap
	wo.use_multi_level_bom = 0
	wo.skip_transfer = cint(skip_transfer)
	wo.planned_start_date = now_datetime()  # verified prerequisite

	# Pull the operation/required-item structure from the BOM.
	if hasattr(wo, "set_work_order_operations"):
		wo.set_work_order_operations()
	wo.set_required_items()

	# Assign a source warehouse on EVERY required item (BOM rows are blank) and, if
	# the PM edited/removed material lines, mirror those edits onto the WO so the
	# RELEASE reflects the PM's request. PM-removed items are dropped from the WO.
	pe_mat = {}
	for row in (pe.materials or []):
		if row.item_code and flt(row.actual_qty) > 0:
			pe_mat[row.item_code] = row
	keep = []
	for ri in (wo.required_items or []):
		pe_row = pe_mat.get(ri.item_code)
		if pe_mat and pe_row is None:
			# PM removed this line from the release request — drop it from the WO.
			continue
		if pe_row is not None:
			ri.required_qty = flt(pe_row.actual_qty)
			# Release always sources from the configured release warehouse (Stores);
			# the PM edits qty / removes lines, NOT the source warehouse. (pe_row.source
			# is a fetch_bom_standard artifact = wip, which must NOT drive the release.)
			ri.source_warehouse = src_default or pe_row.source_warehouse
		else:
			ri.source_warehouse = src_default or ri.source_warehouse
		keep.append(ri)
	if pe_mat:
		wo.required_items = keep

	wo.flags.ignore_permissions = True
	wo.insert()
	wo.submit()  # ops BOM -> Job Cards auto-create here
	# 20 Jul (team finding): WO.validate() re-normalizes required_items back to
	# BOM-scaled on insert, so the PM edits above were LOST for CONSUMPTION — the
	# Manufacture consumed BOM std (10) even when the PM entered 8/12. Mirror the
	# PM's actuals back via db_set (the proven dept-release _mirror_actuals
	# pattern; make_stock_entry reads DB values) so consumption = PM ACTUALS.
	if pe_mat:
		for ri in frappe.get_all("Work Order Item", filters={"parent": wo.name},
		                         fields=["name", "item_code"], limit=0):
			row = pe_mat.get(ri.item_code)
			if row is not None and flt(row.actual_qty) > 0:
				frappe.db.set_value("Work Order Item", ri.name,
				                    "required_qty", flt(row.actual_qty),
				                    update_modified=False)
	return wo.name


# ─────────────────────────────────────────────────────────────────────────
#  STEP 5 — BUILD DRAFT RELEASE SE (Material Transfer for Manufacture)
# ─────────────────────────────────────────────────────────────────────────

def build_draft_release_se(wo_name, settings, pe=None):
	"""Build the DRAFT raw-material release Stock Entry(ies) for a submitted WO.
	Returns the Stock Entry name (docstatus 0). Branches on transfer_material_against.

	Built DRAFT so the Store Manager is the submission gate (Pattern C).

	DECISION 2: the PM's EDITED material lines drive the RELEASE quantity. The WO
	required_items get re-normalized back to BOM-1x by Work Order.validate() on
	insert(), so the in-memory override there is lost — we instead apply the PM's
	actual_qty to the release Stock-Entry rows DIRECTLY here, and drop PM-removed
	lines. This is what makes over-release (-> surplus -> auto-return) actually work."""
	from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry

	wo = frappe.get_doc("Work Order", wo_name)
	src_default = settings.get("release_source_warehouse") or settings.get("wip_warehouse")

	pm_qty = {}
	if pe is not None:
		for r in (pe.materials or []):
			if r.item_code and flt(r.actual_qty) > 0:
				pm_qty[r.item_code] = flt(r.actual_qty)

	def _assign_sources(se):
		kept = []
		for row in se.items:
			if pm_qty:
				if row.item_code not in pm_qty:
					continue  # PM removed this material from the release request
				cf = flt(row.conversion_factor) or 1.0
				row.qty = pm_qty[row.item_code]
				row.transfer_qty = flt(row.qty) * cf
			if not row.s_warehouse:
				row.s_warehouse = src_default
			kept.append(row)
		if pm_qty:
			se.items = kept
		# v2.21 ② department-wise cost centre on every release row
		if pe is not None:
			apply_cost_center_to_se(se, resolve_production_cost_center(pe))
		se.flags.ignore_permissions = True
		se.insert()  # DRAFT — docstatus 0
		return se.name

	# Both transfer modes are covered by ONE WO-level "Material Transfer for Manufacture"
	# Stock Entry that includes every required item — the Store Manager submits it once.
	# For tma == "Work Order" the Job Cards carry no items, so a WO-level transfer is the
	# only path. For tma == "Job Card", ERPNext's make_stock_entry still maps every
	# required item (the per-card for_quantity is satisfied once the WO-level transfer is
	# submitted), and the reference ops BOMs all use tma == "Work Order".
	se = frappe.get_doc(make_stock_entry(wo_name, RELEASE_PURPOSE, flt(wo.qty)))
	return _assign_sources(se)


@contextlib.contextmanager
def system_session():
	"""Run the automated completion chain's privileged ERPNext-doc operations as
	Administrator, restoring the real user in finally (Lesson 176-style).

	The chain is AUTHORIZED upstream at the Store-Manager approval gate
	(approve_release / complete_released_production enforce role + has_permission +
	separation-of-duties). The docs it then drives — Work Order, Job Card, Stock
	Entry and, via Stock Entry.on_submit, the NESTED Project cost roll-up
	(update_cost_in_project -> Project.save) — each enforce their OWN doctype
	permissions, which a Stores Manager does not hold. In Frappe v15 a nested doc's
	permission check honors neither the parent's doc-level nor the global
	ignore_permissions flag (only the doc's own flag / the Administrator bypass), so
	the only reliable lever is to act AS Administrator for the mechanical steps. The
	caller writes its audit fields (released_by / posted_by = the real actor) OUTSIDE
	this block, so attribution is preserved."""
	if frappe.session.user == "Administrator":
		# Already elevated (e.g. the _is_admin recovery path) — true no-op so nesting
		# can never restore the wrong user.
		yield
		return
	prev_user = frappe.session.user
	# frappe.set_user (v15) ALSO overwrites session.sid with the USERNAME and wipes
	# session.data — in a web request that corrupts the live session record if the
	# worker dies mid-block (seen 9 Jul: a supervisor restart mid-request left a
	# session that 417'd every visit, even incognito, until the hourly sweep).
	# Snapshot and restore the trampled fields so the session survives intact.
	prev_sid = frappe.session.get("sid")
	prev_data = frappe.session.get("data")
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(prev_user)
		if prev_sid is not None:
			frappe.session["sid"] = prev_sid
		if prev_data is not None:
			frappe.session["data"] = prev_data


def _auto_qi_for_se(se):
	"""Clear the BOM-level QC gate on an automated Stock Entry.

	When a BOM carries inspection_required=1, ERPNext copies it onto the WO's Stock
	Entries and then BLOCKS the submit until EVERY inward row (t_warehouse, non-scrap)
	has a submitted, Accepted Quality Inspection (controllers/stock_controller.py).
	This automated flow has no manual QC step, so we auto-create + auto-accept one QI
	per such row — from the item's QI template, else the BOM's — force-accepting every
	reading (manual_inspection skips the min/max auto-evaluation) and linking it back.
	Uses native `Quality Inspection` only (NOT the app's hooked `TS Quality Inspection`).
	Idempotent: rows that already carry a QI are skipped. Returns the QI names made."""
	if not se.get("inspection_required"):
		return []
	bom_no = se.get("bom_no")
	if not bom_no and se.get("work_order"):
		bom_no = frappe.db.get_value("Work Order", se.work_order, "bom_no")
	bom_tmpl = frappe.db.get_value("BOM", bom_no, "quality_inspection_template") if bom_no else None

	made = []
	for row in se.items:
		# Mirror ERPNext's gate: only INWARD, non-scrap rows missing a QI need one.
		if not row.t_warehouse or row.get("is_scrap_item") or row.quality_inspection:
			continue
		tmpl = frappe.db.get_value("Item", row.item_code, "quality_inspection_template") or bom_tmpl
		qi = frappe.new_doc("Quality Inspection")
		qi.update({
			"inspection_type": "Incoming",
			"reference_type": "Stock Entry",
			"reference_name": se.name,
			"item_code": row.item_code,
			"sample_size": flt(row.qty) or 1.0,
			"report_date": frappe.utils.nowdate(),
			"inspected_by": frappe.session.user,
		})
		if tmpl:
			qi.quality_inspection_template = tmpl
			qi.get_item_specification_details()
			for rd in qi.readings:
				rd.manual_inspection = 1  # skip auto min/max evaluation -> keep Accepted
				rd.status = "Accepted"
		qi.status = "Accepted"
		qi.flags.ignore_permissions = True
		qi.insert(ignore_permissions=True)
		qi.submit()
		# Link row -> QI in the DB (what the submit gate checks). QI.on_submit also
		# back-references the SE row, bumping its timestamp, so we reload below.
		frappe.db.set_value("Stock Entry Detail", row.name, "quality_inspection",
							qi.name, update_modified=False)
		made.append(qi.name)
	if made:
		se.reload()  # refresh: QI submits bumped the SE timestamp + linked the rows
	return made


def submit_release_se(se_name):
	"""Submit the DRAFT release SE (Store-Manager approve step). WO -> In Process."""
	se = frappe.get_doc("Stock Entry", se_name)
	if se.docstatus == 0:
		_auto_qi_for_se(se)  # auto-accept QI per inward row when the BOM requires it
		se.flags.ignore_permissions = True
		se.submit()          # caller runs this inside system_session() (Administrator)
	return se.name


def released_value(se_name):
	"""Total value of raw material released to WIP (for WIP reconciliation)."""
	se = frappe.get_doc("Stock Entry", se_name)
	return flt(sum(flt(r.amount) for r in (se.items or [])))


# ─────────────────────────────────────────────────────────────────────────
#  STEP 7a — AUTO-COMPLETE JOB CARDS (operations BOMs)
# ─────────────────────────────────────────────────────────────────────────

def auto_complete_job_cards(wo_name):
	"""Auto-complete every Open Job Card of a Work Order, in sequence_id order.

	VERIFIED approach: append ONE time_logs row per card with
	completed_qty (+ process_loss_qty) == for_quantity EXACTLY, from_time < to_time,
	non-overlapping windows across cards (staggered) -> save -> submit.

	Idempotent: a Job Card already Completed is skipped; only appends a time log when
	total_completed_qty < for_quantity. Capacity OverlapError fallback ladder per
	Risk #4 (stagger windows; LAST resort = temporary disable_capacity_planning,
	reverted in try/finally)."""
	job_cards = frappe.get_all(
		"Job Card",
		filters={"work_order": wo_name},
		fields=["name", "sequence_id", "creation"],
		order_by="sequence_id asc, creation asc",
	)
	if not job_cards:
		return 0  # no-ops BOM — nothing to do

	completed = 0
	# Staggered, non-overlapping 30-minute windows; start an hour ago so to_time < now.
	cursor = add_to_date(now_datetime(), hours=-1)
	for jc_row in job_cards:
		jc = frappe.get_doc("Job Card", jc_row["name"])
		if jc.docstatus == 1 or jc.status == "Completed":
			# Advance the cursor so subsequent windows stay non-overlapping.
			cursor = add_to_date(cursor, minutes=35)
			continue

		for_qty = flt(jc.for_quantity)
		already = flt(jc.total_completed_qty)
		loss = flt(jc.process_loss_qty)
		remaining = for_qty - already - loss
		if remaining > 0:
			from_time = cursor
			to_time = add_to_date(from_time, minutes=30)
			jc.append("time_logs", {
				"from_time": from_time,
				"to_time": to_time,
				"completed_qty": remaining,  # completed + loss == for_quantity exactly
			})
			cursor = add_to_date(to_time, minutes=5)  # gap so the next card never overlaps
			jc.flags.ignore_permissions = True
			jc.save()

		_submit_job_card_with_capacity_fallback(jc.name)
		completed += 1

	return completed


def _submit_job_card_with_capacity_fallback(jc_name):
	"""Submit a Job Card; on a capacity/overlap error, retry with staggered windows,
	then as a LAST resort temporarily disable capacity planning (reverted)."""
	jc = frappe.get_doc("Job Card", jc_name)
	if jc.docstatus == 1:
		return
	jc.flags.ignore_permissions = True
	try:
		jc.submit()
		return
	except Exception as e:
		msg = str(e).lower()
		is_capacity = ("overlap" in msg) or ("capacity" in msg) or ("workstation" in msg)
		if not is_capacity:
			raise

	# Retry 1 — push the time window further back so it cannot overlap a neighbour.
	try:
		jc.reload()
		if jc.time_logs:
			tl = jc.time_logs[-1]
			new_from = add_to_date(get_datetime(tl.from_time), hours=-6)
			tl.from_time = new_from
			tl.to_time = add_to_date(new_from, minutes=30)
			jc.flags.ignore_permissions = True
			jc.save()
		jc.submit()
		return
	except Exception as e2:
		msg2 = str(e2).lower()
		if not (("overlap" in msg2) or ("capacity" in msg2) or ("workstation" in msg2)):
			raise

	# LAST resort — temporarily disable capacity planning, ALWAYS reverted.
	prev = frappe.db.get_single_value("Manufacturing Settings", "disable_capacity_planning")
	try:
		frappe.db.set_single_value("Manufacturing Settings", "disable_capacity_planning", 1)
		frappe.clear_cache(doctype="Manufacturing Settings")
		jc.reload()
		jc.flags.ignore_permissions = True
		jc.submit()
	finally:
		frappe.db.set_single_value("Manufacturing Settings", "disable_capacity_planning", cint(prev))
		frappe.clear_cache(doctype="Manufacturing Settings")


# ─────────────────────────────────────────────────────────────────────────
#  STEP 8-9 — MANUFACTURE SE + CLOSE WORK ORDER
# ─────────────────────────────────────────────────────────────────────────

def _cap_byproduct_rates(se):
	"""Keep the finished-good cost from going negative when by-products out-value inputs.

	ERPNext derives the Manufacture FG basic_rate as (consumed_raw_value − scrap_value)
	/ fg_qty and REFUSES a negative basic_rate (NonNegativeError). When a BOM's
	by-products (scrap items, e.g. DDGS/DWGS) are worth more in total than the raw
	material consumed, the FG cost goes negative and the submit is blocked.

	Per the chosen policy: value by-products at their BOM Scrap Item rates, but if their
	total would exceed ~99% of the consumed input value, scale them DOWN so the FG cost
	floors at ~1% of input (strictly positive). set_basic_rate_manually=1 makes ERPNext
	keep our rates instead of re-valuing the scrap rows from the item master at submit."""
	scrap_rows = [d for d in se.items if d.get("is_scrap_item")]
	fg_qty = sum(flt(d.transfer_qty) for d in se.items if d.is_finished_item)
	if not scrap_rows or fg_qty <= 0:
		return
	outgoing = sum(flt(d.transfer_qty) * flt(d.basic_rate) for d in se.items if d.s_warehouse)
	if outgoing <= 0:
		return
	bom_no = se.get("bom_no") or (se.get("work_order") and frappe.db.get_value("Work Order", se.work_order, "bom_no"))
	bom_rates = {}
	if bom_no:
		for s in frappe.get_all("BOM Scrap Item", filters={"parent": bom_no}, fields=["item_code", "rate"]):
			bom_rates[s.item_code] = flt(s.rate)
	# 1) seed each by-product at its BOM scrap rate (fallback: its current build rate)
	for d in scrap_rows:
		d.set_basic_rate_manually = 1
		d.basic_rate = bom_rates.get(d.item_code, flt(d.basic_rate))
		d.basic_amount = flt(d.transfer_qty) * flt(d.basic_rate)
	# 2) cap so by-products never exceed ~99% of the input value (FG cost stays > 0)
	scrap_cost = sum(flt(d.basic_amount) for d in scrap_rows)
	max_scrap = outgoing * 0.99
	if scrap_cost > max_scrap:
		scale = max_scrap / scrap_cost
		for d in scrap_rows:
			d.basic_rate = flt(d.basic_rate) * scale
			d.basic_amount = flt(d.transfer_qty) * flt(d.basic_rate)
		# The cap is a DATA-ANOMALY signal (by-products worth more than the inputs),
		# not a routine event — make it visible (non-blocking toast) AND leave a
		# permanent note on the Production Entry so the team reviews that BOM.
		msg = _(
			"By-product valuation was auto-capped on this run: the BOM by-products "
			"totalled {0} but the raw material consumed was only {1}, so they were "
			"scaled down (×{2}) to {3} to keep the finished-good cost positive. Please "
			"review this BOM's scrap rates / raw-material valuations — by-products "
			"should not normally cost more than the inputs."
		).format(
			round(scrap_cost, 2), round(outgoing, 2), round(scale, 3), round(max_scrap, 2)
		)
		frappe.msgprint(msg, title=_("By-product valuation capped"), indicator="orange")
		pe = se.get("work_order") and frappe.db.get_value(
			"TS Production Entry", {"work_order": se.work_order}, "name"
		)
		if pe:
			try:
				frappe.get_doc("TS Production Entry", pe).add_comment("Comment", msg)
			except Exception:
				frappe.clear_messages()


def submit_manufacture_se(wo_name, company=None):
	"""Create + submit the Manufacture Stock Entry (consumes WIP per BOM-scaled qty,
	produces FG + by-products). WO auto-Completes when produced_qty == qty.
	Idempotent: returns an existing submitted Manufacture SE if one already exists."""
	from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry

	existing = frappe.get_all(
		"Stock Entry",
		filters={"work_order": wo_name, "purpose": "Manufacture", "docstatus": 1},
		pluck="name",
	)
	if existing:
		return existing[0]

	wo = frappe.get_doc("Work Order", wo_name)
	remaining = flt(wo.qty) - flt(wo.produced_qty)
	if remaining <= 0:
		# Already fully produced — nothing to manufacture (recovery re-run).
		return None

	se = frappe.get_doc(make_stock_entry(wo_name, "Manufacture", remaining))
	se.company = company or wo.company
	# 20 Jul — by-product ACTUALS: the native mapper builds scrap rows BOM-scaled;
	# the PM's edited by-product qty/warehouse (run.byproducts) must win — same
	# actuals-first rule as materials. qty 0 drops the row.
	_pe_bp = frappe.get_all("TS Production Entry", filters={"work_order": wo_name},
	                        pluck="name", limit=1)
	if _pe_bp:
		bp = {r.item_code: r for r in
		      (frappe.get_doc("TS Production Entry", _pe_bp[0]).byproducts or [])}
		if bp:
			kept = []
			for row in se.items:
				if getattr(row, "is_scrap_item", 0) and row.item_code in bp:
					r = bp[row.item_code]
					if flt(r.actual_qty) <= 0:
						continue
					cf = flt(row.conversion_factor) or 1.0
					row.qty = flt(r.actual_qty)
					row.transfer_qty = flt(r.actual_qty) * cf
					if r.get("target_warehouse"):
						row.t_warehouse = r.target_warehouse
				kept.append(row)
			se.items = kept
	_cap_byproduct_rates(se)  # keep FG cost >= 0 when by-products out-value the inputs
	# v2.21 ② department-wise cost centre — resolve from the run linked to this WO
	_pe = frappe.get_all("TS Production Entry", filters={"work_order": wo_name},
	                     fields=["name", "bom_connector", "production_item", "company"], limit=1)
	if _pe:
		apply_cost_center_to_se(se, resolve_production_cost_center(frappe._dict(_pe[0])))
	se.flags.ignore_permissions = True
	se.insert()
	_auto_qi_for_se(se)  # auto-accept QI per inward row when the BOM requires it
	se.submit()          # caller runs this inside system_session() (Administrator)
	return se.name


def manufacture_consumed_value(se_name):
	"""Total value of raw material consumed by the Manufacture SE (incoming-side
	finished/scrap rows excluded; only the consumed source rows)."""
	if not se_name:
		return 0.0
	se = frappe.get_doc("Stock Entry", se_name)
	total = 0.0
	for r in (se.items or []):
		if cint(r.is_finished_item) or cint(r.is_scrap_item):
			continue
		if r.s_warehouse and not r.t_warehouse:
			total += flt(r.amount)
	return flt(total)


def close_work_order_if_needed(wo_name):
	"""Close the WO if it is not already Completed/Closed. Best-effort — a WO that
	auto-reached Completed needs no explicit close (verified)."""
	from erpnext.manufacturing.doctype.work_order.work_order import close_work_order

	status = frappe.db.get_value("Work Order", wo_name, "status")
	if status in ("Completed", "Closed", "Cancelled"):
		return
	try:
		close_work_order(wo_name, "Closed")
	except Exception:
		# A WO with a Job Card still In Process can't close — the caller stays at
		# 'Released' and the operator retries via complete_released_production.
		frappe.clear_messages()
		raise


# ─────────────────────────────────────────────────────────────────────────
#  AUTO-RETURN SURPLUS WIP -> STORES  (decision 4)
# ─────────────────────────────────────────────────────────────────────────

def auto_return_surplus(wo_name, settings):
	"""After completion, if leftover released raw material sits in the WIP warehouse
	(released − BOM-consumed > 0), create + submit a Material Transfer (WIP -> Stores
	source) to zero it. Returns (return_se_name | None, returned_items list).

	Driven by the WO's required_items: transferred_qty is what was released; the
	Manufacture SE consumed the BOM-scaled qty. The per-item surplus is the leftover
	WIP balance attributable to this run."""
	wip = settings.get("wip_warehouse")
	dest = settings.get("release_source_warehouse") or settings.get("wip_warehouse")
	if not wip or not dest or wip == dest:
		# No distinct WIP/Stores pair to move between — nothing to return.
		return None, []

	wo = frappe.get_doc("Work Order", wo_name)
	surplus = []
	for ri in (wo.required_items or []):
		leftover = flt(ri.transferred_qty) - flt(ri.consumed_qty)
		if leftover > 1e-9:
			surplus.append({
				"item_code": ri.item_code,
				"qty": leftover,
				"s_warehouse": ri.source_warehouse or wip,  # leftover is held in WIP
			})
	if not surplus:
		return None, []

	se = frappe.new_doc("Stock Entry")
	se.purpose = "Material Transfer"
	se.stock_entry_type = "Material Transfer"
	se.company = wo.company
	se.set_posting_time = 1
	for row in surplus:
		se.append("items", {
			"item_code": row["item_code"],
			"qty": row["qty"],
			"s_warehouse": wip,          # surplus sits in WIP
			"t_warehouse": dest,         # return to the Stores source warehouse
		})
	# v2.21 ② department-wise cost centre on the surplus-return rows too
	_pe = frappe.get_all("TS Production Entry", filters={"work_order": wo_name},
	                     fields=["name", "bom_connector", "production_item", "company"], limit=1)
	if _pe:
		apply_cost_center_to_se(se, resolve_production_cost_center(frappe._dict(_pe[0])))
	se.flags.ignore_permissions = True
	se.insert()
	se.submit()          # caller runs this inside system_session() (Administrator)
	return se.name, surplus


# ─────────────────────────────────────────────────────────────────────────
#  PRE-FLIGHT — STOCK AVAILABILITY (BEFORE any submit; Lesson 288)
# ─────────────────────────────────────────────────────────────────────────

def check_stock_available(se_name):
	"""Return a list of {item_code, warehouse, required, available} for release-SE
	rows that lack physical stock in their source warehouse, so the caller can BLOCK
	before submitting (avoids NegativeStockError mid-chain). Empty list = all OK."""
	se = frappe.get_doc("Stock Entry", se_name)
	shortages = []
	seen = {}
	for row in (se.items or []):
		if not row.item_code or not row.s_warehouse:
			continue
		key = (row.item_code, row.s_warehouse)
		seen[key] = seen.get(key, 0.0) + flt(row.qty)
	for (item_code, wh), req in seen.items():
		actual = flt(frappe.db.get_value(
			"Bin", {"item_code": item_code, "warehouse": wh}, "actual_qty"
		))
		if actual < req - 1e-9:
			shortages.append({
				"item_code": item_code,
				"warehouse": wh,
				"required": req,
				"available": actual,
			})
	return shortages
