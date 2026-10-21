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

import contextlib
import re

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, escape_html, getdate, nowdate, formatdate, add_days

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
	# Single-flow by-product Post Distribution (23 Jul) — set at INSERT by the dialog
	# payload (no DB baseline -> guard passes), immutable afterwards: flipping it
	# post-submit would silently change whether the SM release pauses for the PM split.
	"ts_byproduct_distribution",
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
# v6 back-dated production (seeded Custom Fields, no JSON default — L227)
SETTING_CUTOVER = "ts_production_cutover_date"
SETTING_EXEMPT = "ts_production_issue_exempt_cost_centers"
_INACTIVE_RUN = ("Draft", "Rejected", "Cancelled")


def _bi(en, hi):
	"""v6 — English block, blank line, Hindi block (never mixed line by line)."""
	return "{0}<br><br>{1}".format(en, hi)


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
		# v2.41.1 display: {bom: releaser display label} so the page/form can say
		# "Pending Release — <who>" instead of "Stores Manager" (stored status
		# unchanged). Same permission-free L168 read surface as the rest.
		"bom_releasers": _bom_releaser_labels(),
		# v6 — cut-over date drives the M04 note only
		# a blank Date single reads back as 0001-01-01 (frappe cast) -> treat as not set
		"cutover_date": (str(getdate(_setting(SETTING_CUTOVER)))
		                 if _setting(SETTING_CUTOVER) and getdate(_setting(SETTING_CUTOVER)).year > 1900 else None),
	}


def _bom_releaser_labels():
	rows = frappe.get_all("TS Production BOM Config",
		filters={"parent": "TS Settings", "parentfield": "ts_production_bom_configs"},
		fields=["bom", "releaser_user", "releaser_role"], limit=0)
	out = {}
	for r in rows:
		if r.releaser_user:
			out[r.bom] = frappe.db.get_value("User", r.releaser_user, "full_name") or r.releaser_user
		elif r.releaser_role:
			out[r.bom] = r.releaser_role
	return out


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
	if doc.is_new():
		# v6 security — a run always starts as Draft (G10 counts active runs; the tamper
		# guard skips new documents, so a REST insert could otherwise preset a status)
		doc.ts_variance_status = "Draft"
	_validate_production_date(doc)
	_lock_materials_after_draft(doc)
	if flt(doc.actual_produced_qty) < 0:
		frappe.throw(_("Actual Produced Qty cannot be negative."))
	for row in (doc.materials or []):
		if flt(row.actual_qty) < 0:
			frappe.throw(_("Actual Qty cannot be negative for material {0}.").format(row.item_code))
	for row in (doc.byproducts or []):
		if flt(row.actual_qty) < 0:
			frappe.throw(_("Actual Qty cannot be negative for by-product {0}.").format(row.item_code))

	# Warehouse overrides are typed free-hand on the Production Logging page (the by-product
	# warehouse box, the material source). A group node or a warehouse of the WRONG company
	# passes the Link check but only detonates DEEP in the completion chain — after the run,
	# release SE and job cards exist and stock is already in WIP — stranding the run at
	# 'Released' with an error the Store Manager cannot fix. Catch it here at entry time
	# instead, with a clear message (22 Jul). Mirrors _assert_wh_company in the SE builder.
	def _check_wh(wh, label):
		if not wh:
			return
		info = frappe.db.get_value("Warehouse", wh, ["company", "is_group"], as_dict=True)
		if not info:
			return  # nonexistent name is rejected by the Link field itself
		if info.is_group:
			frappe.throw(_("The {0} warehouse '{1}' is a GROUP warehouse and cannot receive "
			              "stock. Pick a leaf warehouse.").format(label, frappe.utils.escape_html(wh)),
			             title=_("Group Warehouse Not Allowed"))
		if info.company and doc.company and info.company != doc.company:
			frappe.throw(_("The {0} warehouse '{1}' belongs to company '{2}', not '{3}'. "
			              "Pick a warehouse of the run's company.").format(
			              label, frappe.utils.escape_html(wh), frappe.utils.escape_html(info.company),
			              frappe.utils.escape_html(doc.company or "")),
			             title=_("Warehouse Company Mismatch"))
	for row in (doc.byproducts or []):
		if row.target_warehouse:
			_check_wh(row.target_warehouse, _("by-product target"))
	for row in (doc.materials or []):
		if row.source_warehouse:
			_check_wh(row.source_warehouse, _("material source"))


# ─────────────────────────────────────────────────────────────────────────
#  BACK-DATED PRODUCTION  (plan v6 §3.3 / §3.6, 14 Sep 2026)
# ─────────────────────────────────────────────────────────────────────────

def _lock_materials_after_draft(doc):
	"""G10 reads a Single run's material rows, so once the STORED status is not Draft their
	item + quantity are frozen (binds Administrator). No app step edits them after Draft."""
	if doc.is_new() or (doc.flow_type or "Single") == "Multiple":
		return
	if frappe.db.get_value(doc.doctype, doc.name, "ts_variance_status") in (None, "Draft"):
		return
	def rows(rr):
		return sorted((r.get("item_code") or "", round(flt(r.get("actual_qty")), 6)) for r in rr)
	stored = frappe.get_all("TS Production Material", fields=["item_code", "actual_qty"],
	                        filters={"parent": doc.name, "parenttype": doc.doctype,
	                                 "parentfield": "materials"}, limit=0)
	if rows(stored) != rows(doc.materials or []):
		frappe.throw(_bi(
			_("<b>Materials are locked.</b><br>This run is no longer Draft, so its material items and "
			  "quantities cannot change. Ask an Admin (System Manager) to Reject the release, then use "
			  "<b>Revise &amp; Reopen</b>."),
			"<b>Materials लॉक हैं।</b><br>यह run अब Draft में नहीं है, इसलिए इसके material और मात्रा नहीं "
			"बदल सकते। Admin (System Manager) से Release Reject करवाएँ, फिर <b>Revise &amp; Reopen</b> दबाएँ।"),
			title=_("Materials locked"))


def _validate_production_date(doc):
	"""G2: the date is locked once the STORED status is not Draft (binds Administrator).
	G1/G3/G7 run only on insert or while the STORED status is Draft, so a later save
	at Awaiting Distribution (days after the run) can never strand the run."""
	if not doc.posting_date:
		return
	before = None
	if not doc.is_new():
		before = doc.get_doc_before_save() or frappe.db.get_value(
			doc.doctype, doc.name, ["ts_variance_status", "posting_date"], as_dict=True)
	stored = before.get("ts_variance_status") if before else None
	if stored and stored != "Draft":
		old = before.get("posting_date")
		if (str(getdate(old)) if old else None) != str(getdate(doc.posting_date)):
			st = escape_html(stored)
			frappe.throw(_bi(
				_("<b>Production Date is locked.</b><br>This run is no longer Draft (status: {0}), so its date "
				  "cannot change.<br><br><b>What to do:</b><ul>"
				  "<li><b>Not released yet:</b> ask an Admin (System Manager) to open the run and click "
				  "<b>Release</b> &rarr; <b>Reject (admin)</b>. Then click <b>Revise &amp; Reopen</b>, fix the date "
				  "and submit again.</li>"
				  "<li><b>Already released:</b> ask the IT Head. The Stock Entries of this run must be cancelled "
				  "and a new run entered with the right date.</li></ul>").format(st),
				"<b>Production Date लॉक है।</b><br>यह run अब Draft में नहीं है (status: {0}), इसलिए इसकी तारीख "
				"नहीं बदल सकती।<br><br><b>क्या करें:</b><ul>"
				"<li><b>अभी release नहीं हुआ:</b> Admin (System Manager) run खोलकर <b>Release</b> &rarr; "
				"<b>Reject (admin)</b> दबाएँ। फिर <b>Revise &amp; Reopen</b> दबाएँ, तारीख ठीक करें और दोबारा Submit "
				"करें।</li>"
				"<li><b>Release हो चुका है:</b> IT Head से बात करें। इस run की Stock Entry cancel करके सही तारीख "
				"से नया run भरना होगा।</li></ul>".format(st)),
				title=_("Production Date locked"))
		return
	d, today = getdate(doc.posting_date), getdate(nowdate())
	if d > today:
		frappe.throw(_bi(_("Production Date cannot be in the future. Pick today or an earlier day."),
		                 "Production Date आगे की नहीं हो सकती। आज या उससे पहले की तारीख चुनें।"),
		             title=_("Future date"))
	if d == today:
		return
	if (doc.get("flow_type") or "Single") == "Multiple":
		frappe.throw(_bi(_("Old dates do not work yet for a Multiple (BOM Connector) run. Keep today's date."),
		                 "Multiple (BOM Connector) run में अभी पुरानी तारीख नहीं चलती। आज की तारीख ही रखें।"),
		             title=_("Old date not allowed"))
	# No day limit on production dates (Rahul, 27 Sep 2026). The oldest allowed day is the
	# start of an open financial year; stock-on-date and Material Issue checks still apply.
	from erpnext.accounts.utils import get_fiscal_year
	if not get_fiscal_year(d, company=doc.company, boolean=True):
		fd = formatdate(d)
		frappe.throw(_bi(
			_("{0} is not in an open financial year, so Stock cannot be posted on that day. Pick another "
			  "date, or ask the IT Head to open that year.").format(fd),
			"{0} किसी खुले financial year में नहीं है, इसलिए उस दिन Stock की एंट्री नहीं हो सकती। दूसरी "
			"तारीख चुनें, या IT Head से वह साल खुलवाएँ।".format(fd)), title=_("Financial year closed"))


def _link(route, name):
	"""Clickable desk link to a document (name escaped for HTML and URL)."""
	from urllib.parse import quote
	return '<a href="/app/{0}/{1}">{2}</a>'.format(route, quote(str(name or ""), safe=""), escape_html(name or ""))


def _q(v):
	return "{:,.3f}".format(flt(v)).rstrip("0").rstrip(".")


def _exempt_rules():
	"""'Exempt from Production Check' lines: 'Cost Center', 'Cost Center | Item Code' or
	'type: Stock Entry Type'. Blank field = nothing exempt (strictest)."""
	ccs, pairs, types = set(), set(), set()
	for line in (_setting(SETTING_EXEMPT) or "").splitlines():
		line = _norm(line)
		if line.startswith("type:"):
			t = _norm(line[5:])
			# the standard "Material Issue" type would switch both checks off — never honoured
			if t and t != "material issue":
				types.add(t)
		elif "|" in line:
			cc, item = (_norm(x) for x in line.split("|", 1))
			if cc and item:
				pairs.add((cc, item))
		elif line:
			ccs.add(line)
	return ccs, pairs, types


def _norm(s):
	"""Case- and space-insensitive (cost centre names carry double spaces on prod)."""
	return " ".join((s or "").split()).casefold()


def _is_exempt(cost_center, item_code, se_type, rules):
	ccs, pairs, types = rules
	cc = _norm(cost_center)
	return cc in ccs or (cc, _norm(item_code)) in pairs or _norm(se_type) in types


@contextlib.contextmanager
def _busy_retry():
	"""A Material Issue submit already holds its own Stock Entry row when G10 runs, so a rare
	deadlock / lock wait with a concurrent G0 is still possible. MariaDB rolls one side back
	before any write; show a plain retry message instead of the raw database error."""
	try:
		yield
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		frappe.throw(_bi(
			_("Another entry for the same material is being saved right now. Wait a few seconds and try again."),
			"इसी माल की दूसरी एंट्री अभी सेव हो रही है। कुछ सेकंड रुककर दोबारा कोशिश करें।"),
			title=_("Please try again"))


def _release_base_date(doc):
	"""The run's locked Production Date governs its release Stock Entry at approval (the draft's
	own date and set_posting_time are desk-editable). None for a draft built before this change
	(unstamped and created before the go-live marker) -> today's behaviour."""
	if not doc.posting_date or not doc.release_stock_entry:
		return None
	se = frappe.db.get_value("Stock Entry", doc.release_stock_entry, ["set_posting_time", "creation"], as_dict=True)
	if not se:
		return None
	go_live = frappe.db.get_value("Custom Field", "TS Settings-" + SETTING_CUTOVER, "creation")
	if cint(se.set_posting_time) or (go_live and se.creation and se.creation > go_live):
		return doc.posting_date
	return None


def _lock_items(item_codes):
	"""ONE sorted SELECT … FOR UPDATE on the Item rows (same order in G0 and G10). Reads after
	it that could let a double count through are locking reads (L362); see _busy_retry."""
	items = sorted({i for i in item_codes if i})
	if items:
		frappe.db.sql("SELECT name FROM `tabItem` WHERE name IN %(items)s ORDER BY name FOR UPDATE",
		              {"items": tuple(items)})
	return items


def _issue_edges(company, items, lock=True):
	"""{item: [edge…]} sorted by (date, creation). An edge = a Material Issue (purpose) row of
	the item, any warehouse, submitted — or cancelled with no amendment keeping that item —
	not exempt and not the app's own department consumption. Drafts never count.
	lock=False (G10): a missed newer submit only widens the window; a missed concurrent CANCEL
	of the issue that sets the window start would narrow it, so G10 re-checks that start issue
	with a locking read (_live_start)."""
	if not items:
		return {}
	rows = frappe.db.sql("""
		SELECT se.name, se.docstatus, se.posting_date, se.creation, se.modified, se.amended_from,
		       se.stock_entry_type, se.remarks, se.owner, sed.item_code, sed.item_name, sed.transfer_qty,
		       sed.stock_uom, sed.s_warehouse, sed.cost_center
		FROM `tabStock Entry Detail` sed STRAIGHT_JOIN `tabStock Entry` se ON se.name = sed.parent
		WHERE se.purpose = 'Material Issue' AND se.company = %(company)s AND se.docstatus IN (1, 2)
		  AND sed.item_code IN %(items)s AND IFNULL(sed.s_warehouse, '') != ''
		""" + ("LOCK IN SHARE MODE" if lock else ""), {"company": company, "items": tuple(items)}, as_dict=True)
	if not rows:
		return {}
	rules = _exempt_rules()
	kept = {(r.amended_from, r.item_code) for r in rows if r.amended_from}
	edges = {}
	for r in rows:
		# department issues are created elevated (owner Administrator) with "(PRODDEPT-…)" in
		# the remark. Both must hold: a typed remark alone, or a dept entry's stock_entry link
		# (settable on a REST insert), must not hide a Stores issue.
		if "(PRODDEPT-" in (r.remarks or "") and r.owner == "Administrator":
			continue
		if _is_exempt(r.cost_center, r.item_code, r.stock_entry_type, rules):
			continue
		if r.docstatus == 2 and (r.name, r.item_code) in kept:
			continue
		e = edges.setdefault((r.item_code, r.name), frappe._dict(
			item_code=r.item_code, item_name=r.item_name, se=r.name, docstatus=r.docstatus,
			date=getdate(r.posting_date), creation=r.creation, modified=r.modified,
			amended_from=r.amended_from, stock_uom=r.stock_uom, qty=0.0, warehouses=[]))
		e.qty += flt(r.transfer_qty)
		if r.s_warehouse not in e.warehouses:
			e.warehouses.append(r.s_warehouse)
	out = {}
	for e in sorted(edges.values(), key=lambda x: (x.date, x.creation)):
		out.setdefault(e.item_code, []).append(e)
	return out


def _assert_no_issue_covers_date(doc, stage="submit"):
	"""G0 — refuse a Single run while a submitted Material Issue of a run item is the first
	issue on or after its Production Date (the material would be counted twice). When that
	first issue was cancelled, a re-post created after the cancel is refused too. Returns the
	windows whose covering issue was cancelled (input for the M24 note)."""
	if (doc.get("flow_type") or "Single") == "Multiple":
		return []
	want = {r.item_code for r in (doc.materials or []) if r.item_code and flt(r.actual_qty) > 0}
	if not want:
		return []
	with _busy_retry():
		items = _lock_items(frappe.get_all("Item", filters={"name": ["in", list(want)], "is_stock_item": 1},
		                                   pluck="name"))
		edges = _issue_edges(doc.company, items)
	run_date = getdate(doc.posting_date or nowdate())
	blockers, covered = [], []
	for i in items:
		lst = edges.get(i) or []
		after = [e for e in lst if e.date >= run_date]
		if not after:
			continue
		end = after[0].date
		on_end = [e for e in after if e.date == end]
		live = [e for e in on_end if e.docstatus == 1]
		if live:
			blockers += live
			continue
		cancelled_at = min(e.modified for e in on_end)
		later = [e for e in lst if e.docstatus == 1 and e.date > end]
		pre = [e.date for e in later if e.creation < cancelled_at]
		cap = min(pre) if pre else None
		repost = [e for e in later if e.creation > cancelled_at and (cap is None or e.date <= cap)]
		if repost:
			blockers += repost
			continue
		prev = [e.date for e in lst if e.docstatus == 1 and e.date < end]
		covered.append(frappe._dict(item_code=i, end=end, start=max(prev) if prev else None,
		                            cancelled=on_end))
	if not blockers:
		return covered
	others = {}
	for parent, code in frappe.db.sql("SELECT DISTINCT parent, item_code FROM `tabStock Entry Detail` "
	                                  "WHERE parent IN %(n)s", {"n": tuple({e.se for e in blockers})}):
		others.setdefault(parent, set()).add(code)
	en_l, hi_l = [], []
	for e in blockers:
		kw = dict(n=escape_html(e.item_name or e.item_code), c=escape_html(e.item_code), se=_link("stock-entry", e.se),
		          d=formatdate(e.date), q=_q(e.qty), u=escape_html(e.stock_uom or ""),
		          w=escape_html(", ".join(e.warehouses)))
		en = "{n} ({c}): <b>{q} {u}</b> taken out by {se}, dated {d}, from {w}".format(**kw)
		hi = "{n} ({c}): <b>{q} {u}</b>, {se} से निकाला गया, तारीख {d}, {w} से".format(**kw)
		if e.amended_from:
			en += ", amended from {0}".format(escape_html(e.amended_from))
			hi += ", {0} का amendment".format(escape_html(e.amended_from))
		rest = sorted((others.get(e.se) or set()) - {e.item_code})
		if rest:
			en += ". Cancelling it also returns these items to Stock: " + escape_html(", ".join(rest))
			hi += "। इसे cancel करने पर ये माल भी Stock में वापस आ जाएगा: " + escape_html(", ".join(rest))
		en_l.append("<li>{0}</li>".format(en))
		hi_l.append("<li>{0}</li>".format(hi))
	x = blockers[0]
	kw = dict(run=_link("ts-production-entry", doc.name), d=formatdate(run_date),
	          en="<ul>" + "".join(en_l) + "</ul>", hi="<ul>" + "".join(hi_l) + "</ul>",
	          xse=escape_html(x.se), xd=formatdate(x.date), xq=_q(x.qty), xu=escape_html(x.stock_uom or ""),
	          xn=escape_html(x.item_name or x.item_code))
	why_en = _("Stores already took this material out of Stock with a Material Issue dated {d} or later. The ERP "
	           "counts that issue as the material used on {d}:{en}"
	           "Example: {xse}, dated {xd}, took {xq} {xu} of {xn} out of Stock. If this production is added too, "
	           "the same {xn} is counted two times.<br><br>").format(**kw)
	why_hi = ("Stores यह माल {d} या उसके बाद की तारीख के Material Issue से पहले ही Stock से निकाल चुका है। ERP उस "
	          "Issue को {d} की खपत मानता है:{hi}"
	          "उदाहरण: {xse} (तारीख {xd}) ने {xn} का {xq} {xu} Stock से निकाला। अगर यह production भी जुड़ गई, "
	          "तो वही {xn} दो बार गिना जाएगा।<br><br>").format(**kw)
	if stage == "approve":
		en = (_("<b>Release refused for run {run} (Production Date {d}).</b><br>").format(**kw) + why_en
		      + _("<b>What to do:</b><ol>"
		          "<li>Ask the IT Head to open each Material Issue above (click its number) and click <b>Cancel</b>. "
		          "Do not amend it and do not make it again.</li>"
		          "<li>Then click <b>Release</b> again.</li></ol>"
		          "If a Material Issue above must stay, ask an Admin (System Manager) to <b>Reject</b> this run "
		          "instead."))
		hi = ("<b>Run {run} (Production Date {d}) का Release रोका गया।</b><br>".format(**kw) + why_hi
		      + "<b>क्या करें:</b><ol>"
		        "<li>IT Head ऊपर लिखा हर Material Issue खोलें (उसके नंबर पर क्लिक करें) और <b>Cancel</b> दबाएँ। "
		        "उसे amend न करें और दोबारा न बनाएँ।</li>"
		        "<li>फिर दोबारा <b>Release</b> दबाएँ।</li></ol>"
		        "अगर ऊपर लिखा कोई Material Issue रखना ज़रूरी है, तो Admin (System Manager) से यह run <b>Reject</b> "
		        "करवाएँ।")
	else:
		en = (_("<b>Production for {d} cannot be submitted yet.</b><br>").format(**kw) + why_en
		      + _("<b>What to do:</b><ol>"
		          "<li>Ask the IT Head to open each Material Issue above (click its number) and click "
		          "<b>Cancel</b>.</li>"
		          "<li>Do not amend it and do not make it again.</li>"
		          "<li>Then open run {run} (or click its Production ID in <b>Recent Production Entries</b>) and "
		          "click <b>Submit for Release</b>.</li></ol>").format(**kw))
		hi = ("<b>{d} की production अभी Submit नहीं हो सकती।</b><br>".format(**kw) + why_hi
		      + "<b>क्या करें:</b><ol>"
		        "<li>IT Head ऊपर लिखा हर Material Issue खोलें (उसके नंबर पर क्लिक करें) और <b>Cancel</b> दबाएँ।</li>"
		        "<li>उसे amend न करें और दोबारा न बनाएँ।</li>"
		        "<li>फिर run {run} खोलें (या <b>Recent Production Entries</b> में उसकी Production ID पर क्लिक "
		        "करें) और <b>Submit for Release</b> दबाएँ।</li></ol>".format(**kw))
	frappe.throw(_bi(en, hi), title=_("Material Issue already covers this date"))


def _partial_period_note(doc, covered):
	"""M24 (advisory, after a successful PM submit): a covering issue cancelled AFTER go-live
	whose period is not fully entered yet. Go-live = creation of the cut-over Custom Field."""
	go_live = frappe.db.get_value("Custom Field", "TS Settings-" + SETTING_CUTOVER, "creation")
	if not covered or not go_live:
		return None
	en_all, hi_all = [], []
	for w in covered:
		gone = [e for e in w.cancelled if e.modified and e.modified > go_live]
		if not gone:
			continue
		runs = frappe.db.sql("""
			SELECT m.actual_qty, m.uom FROM `tabTS Production Material` m
			JOIN `tabTS Production Entry` pe ON pe.name = m.parent AND m.parenttype = 'TS Production Entry'
			WHERE m.item_code = %(i)s AND pe.ts_variance_status NOT IN %(x)s
			  AND IFNULL(pe.flow_type, 'Single') != 'Multiple'
			  AND pe.posting_date <= %(end)s AND pe.posting_date > %(start)s""",
			{"i": w.item_code, "x": _INACTIVE_RUN, "end": w.end, "start": w.start or "1900-01-01"}, as_dict=True)
		e0 = gone[0]
		qty = sum(flt(e.qty) for e in gone)
		kw = dict(se=escape_html(", ".join(e.se for e in gone)), sd=formatdate(e0.date), q=_q(qty),
		          u=escape_html(e0.stock_uom or ""), item=escape_html(e0.item_name or w.item_code),
		          to=formatdate(w.end), fe=formatdate(add_days(w.start, 1)) if w.start else "the start",
		          fh=formatdate(add_days(w.start, 1)) if w.start else "शुरुआत")
		if all((r.uom or "").strip().casefold() == (e0.stock_uom or "").strip().casefold() for r in runs):
			used = sum(flt(r.actual_qty) for r in runs)
			if qty - used <= 1e-6:
				continue
			kw.update(rq=_q(used), left=_q(qty - used))
			en_all.append(_("Submitted. Note: Material Issue {se} ({sd}, {q} {u} of {item}) was cancelled. It "
			                "covered {fe} to {to}. {item} used by runs entered so far for these dates: {rq} {u}. "
			                "Still not entered: {left} {u}. Enter the production for the remaining days, or "
			                "Stock will stay too high.").format(**kw))
			hi_all.append("Submit हो गया। ध्यान दें: {item} का Material Issue {se} ({sd}, {q} {u}) cancel हुआ था। "
			              "वह {fh} से {to} तक का था। इन तारीखों के run में अब तक {item} की खपत: {rq} {u}। अभी "
			              "बाकी: {left} {u}। बाकी दिनों की production भी भरें, नहीं तो Stock ज़्यादा ही "
			              "रहेगा।".format(**kw))
		else:
			en_all.append(_("Submitted. Note: Material Issue {se} ({sd}) of {item} was cancelled. It covered {fe} "
			                "to {to}. Enter the production for all remaining days, or Stock will stay too "
			                "high.").format(**kw))
			hi_all.append("Submit हो गया। ध्यान दें: {item} का Material Issue {se} ({sd}) cancel हुआ था। वह {fh} से "
			              "{to} तक का था। बाकी सभी दिनों की production भरें, नहीं तो Stock ज़्यादा ही "
			              "रहेगा।".format(**kw))
	if not en_all:
		return None
	return {"title": _("Production still to be entered"), "en": "<br><br>".join(en_all),
	        "hi": "<br><br>".join(hi_all)}


def assert_issue_not_consumed(se):
	"""G10 — a Material Issue may not take out material that active Single production runs
	already recorded as used in its window (P, E]: E = the issue date, P = the latest other
	submitted issue of that item before E. No Administrator exemption (L431)."""
	if se.get("purpose") != "Material Issue" or not se.get("items"):
		return
	if not _is_enabled():
		return  # production logging off (prod today): no lock, no check
	# the app's own department consumption issue (created elevated for a Pending entry)
	m = re.search(r"\((PRODDEPT-[^)\s]+)\)", se.get("remarks") or "")
	if se.flags.ignore_permissions and m and frappe.db.get_value(
			"TS Production Department Entry", m.group(1), "status") == "Pending":
		return
	rows = [r for r in se.items if r.item_code and r.s_warehouse]
	with _busy_retry():
		runs = _locked_active_runs([r.item_code for r in rows])  # lock FIRST, then a locking read
	if not runs:
		return  # inert until the first active run of these materials
	rules = _exempt_rules()
	check = sorted({r.item_code for r in rows
	                if not _is_exempt(r.cost_center, r.item_code, se.get("stock_entry_type"), rules)})
	if not check:
		return
	edges = _issue_edges(se.company, check, lock=False)
	return _refuse_if_consumed(se, check, runs, edges)


def _locked_active_runs(item_codes):
	items = _lock_items(item_codes)
	if not items:
		return []
	return frappe.db.sql("""
		SELECT pe.name, pe.posting_date, m.item_code, m.item_name, m.actual_qty, m.uom
		FROM `tabTS Production Material` m
		STRAIGHT_JOIN `tabTS Production Entry` pe ON pe.name = m.parent AND m.parenttype = 'TS Production Entry'
		WHERE m.item_code IN %(items)s AND m.actual_qty > 0 AND pe.ts_variance_status NOT IN %(x)s
		  AND IFNULL(pe.flow_type, 'Single') != 'Multiple'
		LOCK IN SHARE MODE""", {"items": tuple(items), "x": _INACTIVE_RUN}, as_dict=True)


def _live_start(item_edges, issue_date, skip):
	"""Latest date before the issue with a still-submitted issue of the item. The edge read was
	plain, so confirm the candidate day's issues with a locking read on their own rows (waits
	out an in-flight cancel; a cancel takes no Item/run lock, so no cycle)."""
	days = sorted({e.date for e in item_edges if e.docstatus == 1 and e.date < issue_date and e.se not in skip},
	              reverse=True)
	for d in days:
		names = tuple(e.se for e in item_edges if e.docstatus == 1 and e.date == d and e.se not in skip)
		with _busy_retry():
			if frappe.db.sql("SELECT name FROM `tabStock Entry` WHERE name IN %(n)s AND docstatus = 1 "
			                 "LOCK IN SHARE MODE", {"n": names}):
				return d
	return None


def _refuse_if_consumed(se, check, runs, edges):
	issue_date = getdate(se.posting_date or nowdate())
	skip = {se.name, se.get("amended_from")}
	hits, starts = {}, []
	for i in check:
		start = _live_start(edges.get(i, []), issue_date, skip)
		found = False
		for r in runs:
			rd = getdate(r.posting_date)
			if r.item_code == i and rd <= issue_date and (start is None or rd > start):
				h = hits.setdefault((r.name, i), frappe._dict(r, actual_qty=0.0))
				h.actual_qty += flt(r.actual_qty)
				found = True
		if found:
			starts.append(start)
	if not hits:
		return
	lines_en, lines_hi = [], []
	hs = sorted(hits.values(), key=lambda x: (getdate(x.posting_date), x.name), reverse=True)
	for h in hs[:10]:  # the latest 10 runs; the rest summarised per item below
		kw = dict(n=escape_html(h.item_name or h.item_code), c=escape_html(h.item_code), run=escape_html(h.name),
		          d=formatdate(h.posting_date), q=_q(h.actual_qty), u=escape_html(h.uom or ""))
		lines_en.append("<li>{n} ({c}): run {run}, Production Date {d}, {q} {u}</li>".format(**kw))
		lines_hi.append("<li>{n} ({c}): run {run}, Production Date {d}, {q} {u}</li>".format(**kw))
	more = {}
	for h in hs[10:]:
		agg = more.setdefault((h.item_code, h.uom or ""), [0, 0.0])
		agg[0] += 1
		agg[1] += flt(h.actual_qty)
	for (code, uom), (cnt, qty) in sorted(more.items()):
		kw = dict(c=escape_html(code), k=cnt, q=_q(qty), u=escape_html(uom))
		lines_en.append("<li>{c}: {k} more run(s), {q} {u}</li>".format(**kw))
		lines_hi.append("<li>{c}: {k} और run, {q} {u}</li>".format(**kw))
	first = None if any(s is None for s in starts) else min(starts)
	hit_items = {h.item_code for h in hits.values()}
	row = next((r for r in (se.get("items") or []) if r.item_code in hit_items), None)
	cc = (row.get("cost_center") if row else None) or se.get("ts_set_cost_center") or "Cost Center"
	mrs = sorted({r.get("material_request") for r in (se.get("items") or []) if r.get("material_request")})
	mr_links = ", ".join(_link("material-request", m) for m in mrs)
	kw = dict(fe=formatdate(add_days(first, 1)) if first else "the start",
	          fh=formatdate(add_days(first, 1)) if first else "शुरुआत", to=formatdate(issue_date),
	          en="<ul>" + "".join(lines_en) + "</ul>", hi="<ul>" + "".join(lines_hi) + "</ul>",
	          line="{0} | {1}".format(escape_html(cc), escape_html(row.item_code if row else "Item Code")),
	          mrs=mr_links)
	mr_en = _(" It came from Material Request {mrs}: ask its approver to <b>Reject</b> or <b>Close</b> it, so it "
	          "is not issued again.").format(**kw) if mrs else ""
	mr_hi = (" यह Material Request {mrs} से बना है: उसके approver से उसे <b>Reject</b> या <b>Close</b> करवाएँ, ताकि "
	         "यह दोबारा Issue न हो।").format(**kw) if mrs else ""
	frappe.throw(_bi(
		_("<b>This Material Issue cannot be submitted.</b><br>Production runs already recorded this material as used "
		  "between {fe} and {to}:{en}If this issue is submitted too, the same material is counted two times."
		  "<br><br><b>What to do:</b><ul>"
		  "<li><b>If the material was used in production:</b> you do not need this Material Issue. Do not submit "
		  "it; delete this draft.{mr}</li>"
		  "<li><b>If the material is not for production</b> (for example WTP, CPU, lab or Boiler bed material): "
		  "ask the IT Head to open <b>TS Settings</b>, find <b>Exempt from Production Check</b> and add this "
		  "line: <b>{line}</b>. Then submit this issue again.</li></ul>").format(mr=mr_en, **kw),
		("<b>यह Material Issue Submit नहीं हो सकता।</b><br>{fh} से {to} के बीच इस माल की खपत production run में "
		 "पहले ही दर्ज है:{hi}अगर यह Issue भी Submit हुआ, तो वही माल दो बार गिना जाएगा।"
		 "<br><br><b>क्या करें:</b><ul>"
		 "<li><b>अगर माल production में लगा है:</b> इस Material Issue की ज़रूरत नहीं है। इसे Submit न करें; यह "
		 "draft delete करें।{mr}</li>"
		 "<li><b>अगर माल production के लिए नहीं है</b> (जैसे WTP, CPU, lab या Boiler bed material): IT Head से "
		 "कहें कि <b>TS Settings</b> खोलें, <b>Exempt from Production Check</b> में यह लाइन जोड़ें: "
		 "<b>{line}</b>। फिर यह Issue दोबारा Submit करें।</li></ul>").format(mr=mr_hi, **kw)),
		title=_("Material already counted by production"))


def throw_stock_shortage(shortages, on_date, run=None, stage="submit"):
	"""M13 — not enough Stock on the date the release posts (PM submit and SM approve). Says, per item,
	how much is short, and how to fix it: a made item needs its own production run first, a bought item
	needs a GRN dated that day or earlier."""
	d = formatdate(on_date)
	en_r, hi_r, made, bought = [], [], [], []
	for s in shortages:
		code = s["item_code"]
		info = frappe.db.get_value("Item", code, ["item_name", "stock_uom"], as_dict=True) or {}
		bom = frappe.db.get_value("BOM", {"item": code, "is_active": 1, "docstatus": 1}, "name",
		                          order_by="is_default desc")
		n = escape_html(info.get("item_name") or code)
		req, have = flt(s["required"]), flt(s["available"])
		kw = dict(n=n, c=escape_html(code), w=escape_html(s["warehouse"]), q=_q(req), h=_q(have),
		          s=_q(max(req - have, 0)), u=escape_html(info.get("stock_uom") or ""))
		en_r.append("<li>{n} ({c}) in {w}: need <b>{q} {u}</b>, there was {h} {u}, short by <b>{s} {u}</b></li>".format(**kw))
		hi_r.append("<li>{n} ({c}), {w}: चाहिए <b>{q} {u}</b>, उस दिन {h} {u} था, <b>{s} {u}</b> कम</li>".format(**kw))
		(made if bom else bought).append((n, escape_html(bom or "")))
	uniq = lambda xs: ", ".join(dict.fromkeys(xs))
	fix_en, fix_hi = [], []
	if made:
		k = dict(names=uniq(m[0] for m in made), boms=uniq(m[1] for m in made), d=d)
		fix_en.append(_("<li><b>Made in the plant</b> ({names}): first add the production run for it on the "
		                "Production Logging page, dated {d} or earlier (BOM {boms}), and get it released.</li>").format(**k))
		fix_hi.append("<li><b>Plant में बनने वाला माल</b> ({names}): पहले Production Logging page पर उसका production "
		              "run {d} या उससे पहले की तारीख से जोड़ें (BOM {boms}) और उसे release करवाएँ।</li>".format(**k))
	if bought:
		k = dict(names=uniq(b[0] for b in bought), d=d)
		fix_en.append(_("<li><b>Bought</b> ({names}): the GRN (Purchase Receipt) must be dated {d} or earlier. When "
		                "Stores makes the GRN, they tick <b>Edit Posting Date and Time</b> and set the real day the "
		                "material arrived, then Submit.</li>").format(**k))
		fix_hi.append("<li><b>खरीदा हुआ माल</b> ({names}): उसकी GRN (Purchase Receipt) {d} या उससे पहले की तारीख की "
		              "होनी चाहिए। Stores GRN बनाते समय <b>Edit Posting Date and Time</b> पर tick करें, माल आने का "
		              "असली दिन डालें और Submit करें।</li>".format(**k))
	fix_en.append(_("<li><b>The stock is in another warehouse:</b> ask Stores to move it to the warehouse above "
	                "with a Material Transfer dated {0} or earlier.</li>").format(d))
	fix_hi.append("<li><b>माल किसी दूसरे warehouse में है:</b> Stores से कहें कि {0} या उससे पहले की तारीख का "
	              "Material Transfer करके उसे ऊपर लिखे warehouse में लाएँ।</li>".format(d))
	r = _link("ts-production-entry", run) if run else ""
	if run and stage == "submit":
		again_en = _("Then open run {0} (or click its Production ID in <b>Recent Production Entries</b>) and click "
		             "<b>Submit for Release</b>.").format(r)
		again_hi = ("फिर run {0} खोलें (या <b>Recent Production Entries</b> में उसकी Production ID पर क्लिक करें) "
		            "और <b>Submit for Release</b> दबाएँ।").format(r)
	elif run:
		again_en, again_hi = _("Then click <b>Release</b> again."), "फिर दोबारा <b>Release</b> दबाएँ।"
	else:
		again_en, again_hi = _("Then try again."), "फिर दोबारा कोशिश करें।"
	frappe.throw(_bi(
		_("<b>Not enough Stock on {0}.</b><br>The ERP checks the Stock as it was on {0}, not today. On that day "
		  "there was not enough of:{1}<b>What to do:</b><ul>{2}</ul>{3}").format(
			d, "<ul>" + "".join(en_r) + "</ul>", "".join(fix_en), again_en),
		"<b>{0} को Stock कम है।</b><br>ERP {0} का Stock देखता है, आज का नहीं। उस दिन इतना माल नहीं था:{1}"
		"<b>क्या करें:</b><ul>{2}</ul>{3}".format(d, "<ul>" + "".join(hi_r) + "</ul>", "".join(fix_hi), again_hi)),
		title=_("Not enough Stock"))


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
	# v2.41 — the per-BOM config warehouse outranks the global release source here
	# too, so valuation is checked where the release will actually pull from.
	from trustbit_ethanol.ts_gate_entry import ts_production_wo as wo_engine
	cfg_src = wo_engine.bom_config(
		getattr(doc, "bom", None),
		getattr(doc, "flow_type", None) or "Single")["source_warehouse"]
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
		s_wh = cfg_src or settings.get("release_source_warehouse") or row.source_warehouse or wip
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
	# Bell first — email alone is dead (no SMTP) and sendmail may raise.
	from trustbit_ethanol.ts_gate_entry.ts_notification_coverage import _coverage_bell
	# v6 M15 — the bell subject is plain text (strip_html, 140 chars): English subject, both
	# blocks in the bell body; email subject English only, Hindi in the email body.
	_pd = formatdate(doc.posting_date or nowdate())
	m15_en = _("Release pending: run {0}, dated {1}").format(doc.name, _pd)
	m15_hi = "Release बाकी है: run {0}, तारीख {1}".format(doc.name, _pd)
	_coverage_bell(recipients, m15_en, "TS Production Entry", doc.name, body=_bi(m15_en, m15_hi))
	frappe.sendmail(
		recipients=recipients,
		subject=m15_en,
		message=_(
			"Production Entry <b>{0}</b> ({1}) is awaiting your raw-material release "
			"(produced qty {2}). Approve the release to transfer material Stores &rarr; WIP "
			"and run the manufacture."
		).format(doc.name, escape_html(doc.production_item_name or doc.production_item or ""),
				 flt(doc.actual_produced_qty)) + "<br><br>" + m15_hi,
		reference_doctype="TS Production Entry",
		reference_name=doc.name,
	)


def _notify_creator(doc, event):
	if not doc.owner or doc.owner == "Administrator":
		return
	# Bell first — email alone is dead (no SMTP) and sendmail may raise.
	from trustbit_ethanol.ts_gate_entry.ts_notification_coverage import _coverage_bell
	_coverage_bell([doc.owner],
		_("Your production entry {0} was {1}").format(doc.name, event),
		"TS Production Entry", doc.name)
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
