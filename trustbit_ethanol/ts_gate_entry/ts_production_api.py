# Copyright (c) 2026, Trustbit Technologies and contributors
# For license information, please see license.txt
#
# TS Production Entry — variance engine, Manufacture-SE builder, on_cancel
# reverse-sync, notifications + the legacy direct-post endpoints.
#
# Production Logging flow (the single Store-Manager release gate lives in
# ts_production_release.py; the Work-Order/Job-Card automation in ts_production_wo.py):
#   Draft -> Pending Stores Release -> Released -> Completed
#   Variance is INFORMATIONAL ONLY — there is no CEO approval gate.
#
# This module still owns the variance engine, the valuation HARD BLOCK, the
# Manufacture Stock Entry builder, the Stock Entry on_cancel reverse-sync, and the
# notification helpers — all reused by the release gate.
#
# By-products (DDGS / DWGS / LCO2 / ...) are data-driven from the BOM Scrap Items
# (no hardcoded list) and posted as is_scrap_item rows on the SAME Manufacture SE.
#
# Discipline:
#   - Mutations are @frappe.whitelist(methods=["POST"])           (Lesson 175)
#   - Role gate up front + frappe.has_permission(...,throw=True)  (Lesson 224)
#   - Valuation HARD BLOCK before any mutation                    (v2.14.1)
#   - Status is ATOMIC with SE creation: status flips ONLY after the SE submits
#     (Lesson 288) — never "Completed" without a backing Stock Entry
#   - Control-plane fields written via db_set (skip save hooks); tamper-guarded
#     in before_save                                              (Lesson 162)
#   - Notifications best-effort, individually try/except          (Lesson 238)
#     + clear leaked messages on swallowed exceptions             (Lesson 276)

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, escape_html

# Control-plane fields written ONLY by the workflow endpoints (via db_set, which
# bypasses save hooks). Guarded in before_save so a user cannot set them via the
# REST API / form. The variance fields (material_variance_pct, produced_variance_pct,
# variance_breach) are deliberately NOT guarded — the controller recomputes them on
# every validate(), so a normal save legitimately changes them and tampering is moot.
CONTROL_FIELDS = [
	"ts_variance_status",
	"linked_stock_entry",
	"submitted_by",
	"posted_by",
	"rejected_by",
	"rejection_reason",
	"actual_produced_qty_at_submission",
	# Production Logging release flow — written ONLY by the release endpoints (db_set).
	"work_order",
	"release_stock_entry",
	"released_by",
	"released_at",
	"bom_with_operations",
	"bom_transfer_material_against",
	"wip_released_qty",
	"wip_consumed_qty",
	"wip_reconcile_variance_pct",
	"wip_returned_stock_entry",
	"wip_reconcile_note",
	# Multiple flow (Phase D) — written ONLY by ts_production_multi endpoints (db_set).
	"flow_type",
	"bom_connector",
	"material_request",
	"distribution_stock_entry",
	"distribution_done",
]

# Settings keys on TS Settings (Single). ts_settings.JSON only — ts_settings.py untouched.
SETTING_ENABLED = "ts_production_entry_enabled"
SETTING_TOLERANCE = "ts_production_variance_tolerance_pct"
SETTING_WIP_WH = "ts_production_wip_warehouse"
SETTING_FG_WH = "ts_production_fg_warehouse"
SETTING_BYPRODUCT_WH = "ts_production_byproduct_warehouse"
# Production Logging (release flow) settings — new JSON fields.
SETTING_RELEASE_SOURCE_WH = "ts_production_release_source_warehouse"
SETTING_AUTO_RETURN = "ts_production_auto_return_surplus"
SETTING_WIP_RECON_TOL = "ts_production_wip_recon_tolerance_pct"


# ─────────────────────────────────────────────────────────────────────────
#  SETTINGS (fail-closed)
# ─────────────────────────────────────────────────────────────────────────

def _setting(fieldname):
	try:
		return frappe.db.get_single_value("TS Settings", fieldname)
	except Exception:
		return None


def _is_enabled():
	"""Kill switch. Fail-closed for mutations (treated as disabled if unreadable)."""
	val = _setting(SETTING_ENABLED)
	return cint(val) == 1


def _get_settings():
	auto_return = _setting(SETTING_AUTO_RETURN)
	return {
		"enabled": cint(_setting(SETTING_ENABLED)) == 1,
		"tolerance_pct": _setting(SETTING_TOLERANCE),  # may be None -> fail-closed breach
		"wip_warehouse": _setting(SETTING_WIP_WH),
		"fg_warehouse": _setting(SETTING_FG_WH),
		"byproduct_warehouse": _setting(SETTING_BYPRODUCT_WH),
		# Production Logging release flow
		"release_source_warehouse": _setting(SETTING_RELEASE_SOURCE_WH),
		# Default ON when unset (field has no JSON default per Lesson 227).
		"auto_return_surplus": (1 if auto_return is None else cint(auto_return)) == 1,
		"wip_recon_tolerance_pct": _setting(SETTING_WIP_RECON_TOL),
	}


@frappe.whitelist()
def get_production_settings():
	"""Read-only: settings for the JS form (banner + button gating). No mutation."""
	s = _get_settings()
	# Do not leak the raw tolerance as None silently — JS treats None as "fail-closed".
	return {
		"enabled": s["enabled"],
		"tolerance_pct": flt(s["tolerance_pct"]) if s["tolerance_pct"] is not None else None,
		"wip_warehouse": s["wip_warehouse"],
		"fg_warehouse": s["fg_warehouse"],
		"byproduct_warehouse": s["byproduct_warehouse"],
		"release_source_warehouse": s["release_source_warehouse"],
		"auto_return_surplus": s["auto_return_surplus"],
		"wip_recon_tolerance_pct": flt(s["wip_recon_tolerance_pct"]) if s["wip_recon_tolerance_pct"] is not None else None,
	}


# ─────────────────────────────────────────────────────────────────────────
#  FETCH STANDARD FROM BOM  (read-only — data-driven, no hardcoded by-products)
# ─────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def fetch_bom_standard(bom):
	"""Return the standard materials + by-products for a BOM so the JS can populate
	the child grids. Read-only (Lesson 175 governs MUTATIONS); gated on BOM read."""
	if not bom:
		return {}
	frappe.has_permission("BOM", "read", doc=bom, throw=True)
	b = frappe.get_doc("BOM", bom)
	settings = _get_settings()

	def _stock_uom(item_code):
		return frappe.db.get_value("Item", item_code, "stock_uom") or ""

	materials = []
	for row in (b.items or []):
		materials.append({
			"item_code": row.item_code,
			"item_name": row.item_name,
			"std_qty": flt(row.stock_qty),  # per the BOM batch (b.quantity)
			"uom": row.stock_uom or _stock_uom(row.item_code),
			"source_warehouse": row.get("source_warehouse") or settings.get("wip_warehouse"),
		})

	byproducts = []
	for row in (b.scrap_items or []):
		byproducts.append({
			"item_code": row.item_code,
			"item_name": row.item_name,
			"std_qty": flt(row.stock_qty),
			"uom": row.stock_uom or _stock_uom(row.item_code),
			"rate": flt(row.rate),
			"target_warehouse": settings.get("byproduct_warehouse"),
		})

	return {
		"company": b.company,
		"production_item": b.item,
		"production_item_name": b.item_name,
		"standard_qty": flt(b.quantity),
		"production_uom": b.uom,
		"bom_quantity": flt(b.quantity),
		"materials": materials,
		"byproducts": byproducts,
	}


# ─────────────────────────────────────────────────────────────────────────
#  VARIANCE ENGINE  (called from controller.validate on every save)
# ─────────────────────────────────────────────────────────────────────────

def apply_bom_defaults(doc):
	"""Set company / finished item / standard batch qty / uom from the BOM."""
	if not doc.bom:
		return
	batches = flt(doc.standard_batches) or 1.0
	if batches <= 0:
		batches = 1.0
	doc.standard_batches = batches
	bom = frappe.db.get_value(
		"BOM", doc.bom, ["company", "item", "item_name", "quantity", "uom"], as_dict=True
	)
	if not bom:
		return
	doc.company = doc.company or bom.company
	doc.production_item = bom.item
	doc.production_item_name = bom.item_name
	doc.standard_qty = flt(bom.quantity) * batches
	doc.production_uom = bom.uom


def _row_variance_pct(actual, std):
	"""Signed variance %, with a defined value when std is 0 (new/unexpected qty)."""
	actual = flt(actual)
	std = flt(std)
	if std:
		return (actual - std) / std * 100.0
	return 100.0 if actual else 0.0


def compute_and_set_variance(doc):
	"""Recompute std_qty (scaled to ACTUAL produced) + per-row + header variance %.

	Scaling rationale: material/by-product std is the BOM qty scaled to the actual
	output, so material variance isolates EFFICIENCY (used more/less than the recipe
	says for what was made). Produced variance is the actual output vs the BOM
	standard batch (b.quantity) — the literal 'produced quantity has variation'.
	"""
	if not doc.bom:
		return
	bom = frappe.get_doc("BOM", doc.bom)
	bom_qty = flt(bom.quantity) or 0.0
	batches = flt(doc.standard_batches) or 1.0
	if batches <= 0:
		batches = 1.0
	standard_target = bom_qty * batches      # the standard output target for this run
	doc.standard_qty = standard_target
	produced = flt(doc.actual_produced_qty) or 0.0
	scale = (produced / bom_qty) if bom_qty else 0.0  # expected-material scale (efficiency, vs actual output)

	bom_mat = {r.item_code: flt(r.stock_qty) for r in (bom.items or [])}
	bom_scrap = {r.item_code: flt(r.stock_qty) for r in (bom.scrap_items or [])}

	max_mat_abs = 0.0
	for row in (doc.materials or []):
		base = bom_mat.get(row.item_code, 0.0)  # per BOM batch
		row.std_qty = flt(base) * scale          # expected for the actual output
		row.variance_pct = _row_variance_pct(row.actual_qty, row.std_qty)
		max_mat_abs = max(max_mat_abs, abs(flt(row.variance_pct)))

	for row in (doc.byproducts or []):
		base = bom_scrap.get(row.item_code, 0.0)
		row.std_qty = flt(base) * scale
		row.variance_pct = _row_variance_pct(row.actual_qty, row.std_qty)

	doc.material_variance_pct = max_mat_abs
	# Produced variance vs the standard target (BOM qty x batches); magnitude stored positive.
	doc.produced_variance_pct = abs(_row_variance_pct(produced, standard_target)) if standard_target else 0.0

	# Flag BOM materials missing from this entry (they would NOT be consumed).
	present = {r.item_code for r in (doc.materials or []) if r.item_code}
	missing = [code for code in bom_mat.keys() if code not in present]
	if missing:
		names = []
		for code in missing[:8]:
			nm = frappe.db.get_value("Item", code, "item_name") or code
			names.append("{0} ({1})".format(nm, code))
		extra = "" if len(missing) <= 8 else " +{0} more".format(len(missing) - 8)
		doc.material_coverage_warning = _(
			"These BOM materials are not in this entry and will NOT be consumed: {0}{1}"
		).format("; ".join(names), extra)
	else:
		doc.material_coverage_warning = ""

	tol = _get_settings().get("tolerance_pct")
	if tol is None:
		# Fail-closed: no tolerance configured -> treat as a breach (needs CEO).
		doc.variance_breach = 1
	else:
		tol = flt(tol)
		breach = (flt(doc.material_variance_pct) > tol) or (flt(doc.produced_variance_pct) > tol)
		doc.variance_breach = 1 if breach else 0


def validate_entry(doc):
	"""Sanity: no negative quantities. (Posting-readiness is checked at submit time.)"""
	if flt(doc.actual_produced_qty) < 0:
		frappe.throw(_("Actual Produced Qty cannot be negative."))
	for row in (doc.materials or []):
		if flt(row.actual_qty) < 0:
			frappe.throw(_("Actual Qty cannot be negative for material {0}.").format(row.item_code))
	for row in (doc.byproducts or []):
		if flt(row.actual_qty) < 0:
			frappe.throw(_("Actual Qty cannot be negative for by-product {0}.").format(row.item_code))


def guard_control_fields(doc):
	"""Block REST/form tampering of control-plane fields (Lesson 162/176).
	Reuses the proven _block_gate_field_tampering helper (imported, not edited)."""
	from trustbit_ethanol.ts_gate_entry.ts_po_approval import _block_gate_field_tampering
	_block_gate_field_tampering(doc, CONTROL_FIELDS)


# ─────────────────────────────────────────────────────────────────────────
#  VALUATION HARD BLOCK  (v2.14.1 pattern — BEFORE any mutation)
# ─────────────────────────────────────────────────────────────────────────

def _block_if_missing_valuation(doc, settings):
	"""HARD BLOCK: raise a popup naming every line that would post at zero/missing
	valuation, BEFORE any status change or Stock Entry creation, so the entry is
	never left 'Completed' without a sound Stock Entry.

	  - consumed materials  -> need a valuation rate in their source warehouse
	    (outgoing stock is valued at source rate; missing => SE submit fails)
	  - by-products (scrap) -> need an explicit rate OR a warehouse valuation
	    (incoming scrap is otherwise posted at zero value)
	"""
	wip = settings.get("wip_warehouse")
	bywh = settings.get("byproduct_warehouse")
	problems = []
	seen = set()

	def _is_stock(item_code):
		return cint(frappe.db.get_value("Item", item_code, "is_stock_item"))

	def _val_rate(item_code, warehouse):
		if not warehouse:
			return 0.0
		return flt(frappe.db.get_value(
			"Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate"
		))

	for row in (doc.materials or []):
		if not row.item_code or flt(row.actual_qty) <= 0:
			continue
		if not _is_stock(row.item_code):
			continue
		# Consumed RM is released FROM the configured release source (Stores) and only
		# then transferred into WIP, so its valuation lives at the release source at
		# pre-flight time (WIP may be empty pre-release). Check the release source first.
		s_wh = settings.get("release_source_warehouse") or row.source_warehouse or wip
		# v2.21 dept release (13 Jul): a row the PM sourced from a department-CLAIMED
		# warehouse is released FROM that warehouse (build_release_slots re-points it),
		# so check valuation THERE — dept-produced items (steam, treated water) often
		# exist ONLY in their department warehouse, never in Stores.
		if row.source_warehouse and row.source_warehouse != s_wh:
			try:
				from trustbit_ethanol.ts_gate_entry import ts_production_dept_release as dept_rel
				if row.source_warehouse in dept_rel._warehouse_to_category():
					s_wh = row.source_warehouse
			except Exception:
				pass  # claim map unavailable -> keep the default check
		key = ("M", row.item_code, s_wh)
		if key in seen:
			continue
		seen.add(key)
		if not _val_rate(row.item_code, s_wh):
			problems.append({
				"item_code": row.item_code,
				"item_name": row.item_name or frappe.db.get_value("Item", row.item_code, "item_name") or "",
				"warehouse": s_wh or "(no source warehouse)",
				"kind": _("consumed"),
			})

	for row in (doc.byproducts or []):
		if not row.item_code or flt(row.actual_qty) <= 0:
			continue
		if not _is_stock(row.item_code):
			continue
		t_wh = row.target_warehouse or bywh
		if flt(row.rate) > 0:
			continue  # explicit rate provided
		key = ("B", row.item_code, t_wh)
		if key in seen:
			continue
		seen.add(key)
		if not _val_rate(row.item_code, t_wh):
			problems.append({
				"item_code": row.item_code,
				"item_name": row.item_name or frappe.db.get_value("Item", row.item_code, "item_name") or "",
				"warehouse": t_wh or "(no warehouse)",
				"kind": _("by-product"),
			})

	if not problems:
		return
	rows = "".join(
		"<li><b>{0}</b> — {1} <span style='color:#6b7280'>({2}, in {3})</span></li>".format(
			escape_html(p["item_code"]), escape_html(p["item_name"]),
			escape_html(p["kind"]), escape_html(p["warehouse"]),
		)
		for p in problems
	)
	frappe.throw(
		_("The following item(s) have no valuation rate, so the production cannot be posted:")
		+ "<ul style='margin-top:6px'>" + rows + "</ul>"
		+ _("Set a valuation rate first (Stock &rarr; Stock Reconciliation), or enter a rate "
		    "on the by-product row, then try again."),
		title=_("Valuation Rate Required"),
	)


# ─────────────────────────────────────────────────────────────────────────
#  MANUFACTURE STOCK ENTRY BUILDER  (hand-built — reflects ACTUALS)
#
#  DEPRECATED in the Production Logging flow: the canonical Manufacture SE is now
#  created via the Work Order in ts_production_wo.submit_manufacture_se() and consumes
#  WIP per the BOM recipe (decision 2 — strict BOM-scaled consumption), NOT the edited
#  actuals. _create_manufacture_stock_entry / _post_production below are retained ONLY
#  as reference for the warehouse-company validation; they are NOT wired to any
#  endpoint. Do NOT re-introduce a from-actuals Manufacture SE — it would diverge from
#  the strict-BOM consumption the release flow guarantees.
# ─────────────────────────────────────────────────────────────────────────

def _create_manufacture_stock_entry(doc, settings):
	"""Build + submit a native Manufacture Stock Entry from the entry's ACTUALS:
	consume raw materials (is_finished_item=0), produce the finished good
	(is_finished_item=1), and add each by-product as a scrap row (is_scrap_item=1).
	Hand-built (not via get_items) so it reflects the operator's actual quantities
	and does NOT trigger native set_scrap_items duplication."""
	wip = settings.get("wip_warehouse")
	fg = settings.get("fg_warehouse")
	bywh = settings.get("byproduct_warehouse")

	missing = [label for label, val in (
		(_("WIP / Source Warehouse"), wip),
		(_("Finished-Goods Warehouse"), fg),
		(_("By-Product Warehouse"), bywh),
	) if not val]
	if missing:
		frappe.throw(
			_("Configure these warehouses in TS Settings before posting production: {0}").format(
				", ".join(missing)
			),
			title=_("Production Warehouses Not Set"),
		)
	if flt(doc.actual_produced_qty) <= 0:
		frappe.throw(_("Actual Produced Qty must be greater than zero to post production."))

	# Warehouses must belong to the entry's company, else ERPNext fails with a cryptic
	# error on submit. Check the defaults + every per-row override up front.
	def _assert_wh_company(wh, label):
		if not wh:
			return
		wh_company = frappe.db.get_value("Warehouse", wh, "company")
		if wh_company and doc.company and wh_company != doc.company:
			frappe.throw(
				_("The {0} warehouse '{1}' belongs to company '{2}', not '{3}'. "
				  "Set warehouses for the correct company in TS Settings (or on the row).").format(
					label, wh, wh_company, doc.company),
				title=_("Warehouse Company Mismatch"),
			)
	_assert_wh_company(wip, _("WIP/Source"))
	_assert_wh_company(fg, _("Finished-Goods"))
	_assert_wh_company(bywh, _("By-Product"))
	for row in (doc.materials or []):
		if flt(row.actual_qty) > 0 and row.source_warehouse:
			_assert_wh_company(row.source_warehouse, _("material source"))
	for row in (doc.byproducts or []):
		if flt(row.actual_qty) > 0 and row.target_warehouse:
			_assert_wh_company(row.target_warehouse, _("by-product target"))

	se = frappe.new_doc("Stock Entry")
	se.purpose = "Manufacture"
	se.stock_entry_type = "Manufacture"
	se.company = doc.company
	se.posting_date = doc.posting_date
	se.set_posting_time = 1
	se.posting_time = frappe.utils.nowtime()
	se.from_bom = 1
	se.bom_no = doc.bom
	se.use_multi_level_bom = 0
	se.fg_completed_qty = flt(doc.actual_produced_qty)
	se.from_warehouse = wip
	se.to_warehouse = fg
	if doc.work_order:
		se.work_order = doc.work_order

	# Consumed raw materials (actuals)
	for row in (doc.materials or []):
		if not row.item_code or flt(row.actual_qty) <= 0:
			continue
		se.append("items", {
			"item_code": row.item_code,
			"qty": flt(row.actual_qty),
			"s_warehouse": row.source_warehouse or wip,
			"is_finished_item": 0,
			"is_scrap_item": 0,
		})

	# Finished good (Ethanol / the BOM's production item)
	se.append("items", {
		"item_code": doc.production_item,
		"qty": flt(doc.actual_produced_qty),
		"t_warehouse": fg,
		"is_finished_item": 1,
		"bom_no": doc.bom,
	})

	# By-products as scrap rows (actuals)
	for row in (doc.byproducts or []):
		if not row.item_code or flt(row.actual_qty) <= 0:
			continue
		item = {
			"item_code": row.item_code,
			"qty": flt(row.actual_qty),
			"t_warehouse": row.target_warehouse or bywh,
			"is_finished_item": 0,
			"is_scrap_item": 1,
		}
		if flt(row.rate) > 0:
			item["basic_rate"] = flt(row.rate)
		se.append("items", item)

	se.flags.ignore_permissions = True
	se.insert()
	se.submit()
	return se.name


def _post_production(doc, settings, approver=None):
	"""ATOMIC: valuation hard-block -> create+submit SE -> THEN flip status to Completed.
	If SE creation fails, status is NOT changed (entry stays recoverable)."""
	_block_if_missing_valuation(doc, settings)

	se_name = None
	se_error = None
	try:
		se_name = _create_manufacture_stock_entry(doc, settings)
	except frappe.ValidationError:
		raise  # hard-block / explicit throw — propagate the helpful message as-is
	except Exception as e:
		se_error = str(e)
		try:
			frappe.log_error(title="TS Production Entry SE failed", message=f"{doc.name}: {e}")
		except Exception:
			pass

	if not se_name:
		# Defense-in-depth for any non-validation SE failure: do NOT flip status.
		frappe.db.rollback()
		frappe.throw(
			_("Stock Entry could not be created, so the production was NOT posted:")
			+ "<br><br>" + escape_html(se_error or "unknown")
			+ "<br><br>" + _("Please resolve the issue and try again."),
			title=_("Stock Entry Creation Failed"),
		)

	doc.db_set("linked_stock_entry", se_name, update_modified=False)
	doc.db_set("ts_variance_status", "Completed", update_modified=False)
	doc.db_set("posted_by", approver or frappe.session.user, update_modified=False)
	doc.add_comment(
		"Comment",
		_("Completed by {0}. Manufacture Stock Entry {1} created + submitted.").format(
			frappe.session.user, se_name
		),
	)
	return se_name


# ─────────────────────────────────────────────────────────────────────────
#  WHITELISTED ENDPOINTS  (mutations: POST + role gate + has_permission)
# ─────────────────────────────────────────────────────────────────────────

def _require_enabled():
	if not _is_enabled():
		frappe.throw(_("The Production Entry flow is currently disabled (kill switch in TS Settings)."))


# ── Production Logging single-gate flow ──────────────────────────────────────
# The CEO variance gate has been REMOVED. The Production Manager submits a draft
# for a single Store-Manager raw-material release; variance is informational only.
# The canonical endpoints live in ts_production_release.py. The three method names
# below are kept as thin delegators so older client calls / number cards keep
# working and route through the new single gate (NO 'Pending CEO', NO direct post).


@frappe.whitelist(methods=["POST"])
def submit_production(name):
	"""Deprecated alias — routes to the single-gate release flow.
	State: Draft -> Pending Stores Release (no SE yet; no 'Pending CEO')."""
	from trustbit_ethanol.ts_gate_entry import ts_production_release as release
	return release.submit_for_release(name)


@frappe.whitelist(methods=["POST"])
def approve_production(name):
	"""Deprecated alias — routes to the Store-Manager release approval.
	State: Pending Stores Release -> Released -> Completed (SE created)."""
	from trustbit_ethanol.ts_gate_entry import ts_production_release as release
	return release.approve_release(name)


@frappe.whitelist(methods=["POST"])
def reject_production(name, reason):
	"""Deprecated alias — routes to the Store-Manager release rejection.
	State: Pending Stores Release -> Rejected (no SE)."""
	from trustbit_ethanol.ts_gate_entry import ts_production_release as release
	return release.reject_release(name, reason)


@frappe.whitelist(methods=["POST"])
def revise_production(name):
	"""Reopen a Rejected entry so the operator can edit + resubmit.
	State: Rejected -> Draft. Clears the rejection + submission snapshot."""
	_require_enabled()
	doc = frappe.get_doc("TS Production Entry", name)
	frappe.has_permission("TS Production Entry", "write", doc=doc, throw=True)

	if doc.ts_variance_status != "Rejected":
		frappe.throw(_("Only a 'Rejected' entry can be revised (current status: {0}).").format(
			doc.ts_variance_status))

	# Creator, a Manufacturing Manager, or admin may revise.
	user = frappe.session.user
	roles = frappe.get_roles(user)
	allowed = (
		user == doc.owner
		or "Manufacturing Manager" in roles
		or "System Manager" in roles
		or user == "Administrator"
	)
	if not allowed:
		frappe.throw(
			_("Only the creator or a Manufacturing Manager can revise this entry."),
			exc=frappe.PermissionError,
		)

	doc.db_set("ts_variance_status", "Draft", update_modified=False)
	doc.db_set("rejected_by", None, update_modified=False)
	doc.db_set("rejection_reason", None, update_modified=False)
	doc.db_set("submitted_by", None, update_modified=False)
	doc.db_set("actual_produced_qty_at_submission", 0, update_modified=False)
	# Clear the release-flow linkage so a re-submission builds a fresh Work Order + SE
	# (the previous WO + release SE were already torn down on rejection).
	doc.db_set("work_order", None, update_modified=False)
	doc.db_set("release_stock_entry", None, update_modified=False)
	doc.db_set("bom_with_operations", 0, update_modified=False)
	doc.db_set("bom_transfer_material_against", None, update_modified=False)
	doc.add_comment("Comment", _("Reopened for revision by {0}.").format(user))
	frappe.db.commit()
	return {"ok": True, "ts_variance_status": "Draft"}


# ─────────────────────────────────────────────────────────────────────────
#  STOCK ENTRY on_cancel  (reverse-sync — registered in hooks.py, merged list)
# ─────────────────────────────────────────────────────────────────────────

def production_on_stock_entry_cancel(doc, method=None):
	"""Reverse-sync a TS Production Entry when one of its backing Stock Entries is
	cancelled. Two cases (early-returns on every other Stock Entry):

	  - Manufacture SE (purpose 'Manufacture') == linked_stock_entry of a 'Completed'
	    entry  -> flip the entry to 'Cancelled' (the production reversal).
	  - Release SE (purpose 'Material Transfer for Manufacture') == release_stock_entry
	    of a 'Released'/'Completed' entry -> revert to 'Pending Stores Release' so the
	    Store Manager can re-release (the raw material went back to Stores).

	Mirrors ts_mr_transfer.revert_on_stock_entry_cancel."""
	purpose = getattr(doc, "purpose", None)

	if purpose == "Manufacture":
		entries = frappe.get_all(
			"TS Production Entry",
			filters={"linked_stock_entry": doc.name, "ts_variance_status": "Completed"},
			pluck="name",
		)
		for name in entries:
			frappe.db.set_value("TS Production Entry", name, "ts_variance_status", "Cancelled",
								update_modified=False)
			try:
				frappe.get_doc("TS Production Entry", name).add_comment(
					"Comment",
					_("Manufacture Stock Entry {0} cancelled — production marked Cancelled.").format(doc.name),
				)
			except Exception:
				# Best-effort comment; never break the SE cancel, and clear any leaked
				# message so a clean cancel shows no scary popup (Lesson 276).
				frappe.clear_messages()
		return

	if purpose == "Material Transfer for Manufacture":
		entries = frappe.get_all(
			"TS Production Entry",
			filters={
				"release_stock_entry": doc.name,
				"ts_variance_status": ["in", ["Released", "Completed"]],
			},
			pluck="name",
		)
		for name in entries:
			frappe.db.set_value("TS Production Entry", name, "ts_variance_status",
								"Pending Stores Release", update_modified=False)
			try:
				frappe.get_doc("TS Production Entry", name).add_comment(
					"Comment",
					_("Release Stock Entry {0} cancelled — reverted to Pending Stores Release.").format(doc.name),
				)
			except Exception:
				frappe.clear_messages()
		return

	if purpose == "Material Transfer":
		# Multiple flow (Phase D): the MR-released transfer SE was cancelled while
		# the run awaited its distribution -> revert to Pending Material Request so
		# the Store Manager can re-release via the Material Request.
		entries = frappe.get_all(
			"TS Production Entry",
			filters={
				"release_stock_entry": doc.name,
				"flow_type": "Multiple",
				"ts_variance_status": "Awaiting Distribution",
			},
			pluck="name",
		)
		for name in entries:
			frappe.db.set_value("TS Production Entry", name, "ts_variance_status",
								"Pending Material Request", update_modified=False)
			try:
				frappe.get_doc("TS Production Entry", name).add_comment(
					"Comment",
					_("Released transfer {0} cancelled — reverted to Pending Material Request.").format(doc.name),
				)
			except Exception:
				frappe.clear_messages()
		return


# ─────────────────────────────────────────────────────────────────────────
#  NOTIFICATIONS  (best-effort — never block the workflow; Lessons 238/276)
# ─────────────────────────────────────────────────────────────────────────

def _notify_stores_managers_release(doc):
	"""Email all active Stores Manager users that a production run awaits release."""
	recipients = _role_emails("Stores Manager")
	if not recipients:
		return
	frappe.sendmail(
		recipients=recipients,
		subject=_("Production raw-material release pending: {0}").format(doc.name),
		message=_(
			"Production Entry <b>{0}</b> ({1}) is awaiting your raw-material release "
			"(produced qty {2}). Approve the release to transfer material Stores &rarr; WIP "
			"and run the manufacture."
		).format(doc.name, escape_html(doc.production_item_name or doc.production_item or ""),
				 flt(doc.actual_produced_qty)),
		reference_doctype="TS Production Entry",
		reference_name=doc.name,
	)


def _notify_creator(doc, event):
	if not doc.owner or doc.owner == "Administrator":
		return
	frappe.sendmail(
		recipients=[doc.owner],
		subject=_("Your production entry {0} was {1}").format(doc.name, event),
		message=_("Production Entry <b>{0}</b> was {1}.").format(doc.name, event)
		+ (("<br><br>" + _("Reason: ") + escape_html(doc.rejection_reason or "")) if event == "rejected" else ""),
		reference_doctype="TS Production Entry",
		reference_name=doc.name,
	)


def _role_emails(role):
	users = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"
	)
	emails = []
	for u in users:
		if frappe.db.get_value("User", u, "enabled") and "@" in (u or ""):
			emails.append(u)
	return emails
