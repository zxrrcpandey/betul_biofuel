"""Multiple (BOM Connector) production flow — Phase D of the Multi-BOM plan
(memory/project_production_multi_flow.md; mockup_production_multi_phaseD.html).

Flow (feasibility-proven T1/T2/T3, 2 Jul 2026):

  Draft ──submit_multi_for_release──▶ Pending Material Request
        (validate + valuation block; Work Order [skip_transfer=1] created;
         auto MATERIAL REQUEST [Material Transfer, tagged ts_production_run]
         created + submitted — rides the v2.9.9 stores-flow bypass)
  ── Store Manager releases via the EXISTING MR stores flow; when the MR's
     Material Transfer SE is SUBMITTED (on_submit hook below) ──▶ Awaiting
     Distribution (release fields stamped; Job Cards auto-complete best-effort;
     department categories notified)
  ──complete_distribution──▶ Completed
        (PM's manual multi-warehouse split; ONE Manufacture SE with multiple
         finished/by-product rows [T1/T2]; sum-must-equal-produced enforced
         server-side; WO closed; surplus auto-returned; WIP reconciled)

Discipline (mirrors ts_production_release.py):
  - mutations @frappe.whitelist(methods=["POST"]) + has_permission(throw=True)
  - kill switches fail-closed: master ts_production_entry_enabled AND
    ts_production_multi_flow_enabled (Lesson 227 — no JSON defaults)
  - control-plane fields via db_set only; status flips LAST (Lesson 288)
  - elevated steps inside wo_engine.system_session() try/finally (Lesson 176)
  - notifications best-effort (Lessons 238/276); user-derived values escaped
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, nowdate

from trustbit_ethanol.ts_gate_entry import ts_production_api as api
from trustbit_ethanol.ts_gate_entry import ts_production_wo as wo_engine
from trustbit_ethanol.ts_gate_entry.ts_production_release import (
	_cancel_work_order_and_job_cards,
	_build_recon_note,
	_is_admin,
	_is_production_manager,
)

DOCTYPE = "TS Production Entry"
SETTING_MULTI_ENABLED = "ts_production_multi_flow_enabled"
SETTING_MR_COST_CENTER = "ts_production_mr_cost_center"
_EPS = 1e-6


# ---------------------------------------------------------------- gates

def _is_multi_enabled():
	try:
		return cint(frappe.db.get_single_value("TS Settings", SETTING_MULTI_ENABLED)) == 1
	except Exception:
		return False  # fail-closed


def _require_enabled():
	api._require_enabled()  # master production kill switch
	if not _is_multi_enabled():
		frappe.throw(_("The Multiple (BOM Connector) production flow is currently "
		               "disabled (kill switch in TS Settings)."))


def _gate_creator(doc):
	user = frappe.session.user
	if user == doc.owner or _is_production_manager(user) or _is_admin(user):
		return
	frappe.throw(_("Only the entry's creator or a Production Manager may do this."),
	             frappe.PermissionError)


# ---------------------------------------------------------------- read-only

@frappe.whitelist()
def get_multi_context():
	"""Chooser data for the page: is the Multiple flow on + active connectors."""
	enabled = False
	try:
		enabled = api._is_enabled() and _is_multi_enabled()
	except Exception:
		enabled = False
	connectors = []
	if enabled:
		for c in frappe.get_all("TS BOM Connector", filters={"active": 1},
		                        fields=["name", "main_bom", "main_bom_item"], limit=0):
			lines = frappe.get_all("TS BOM Connector Line", filters={"parent": c.name},
			                       fields=["bom", "category", "department"], limit=0)
			connectors.append({**c, "department_boms": lines})
	return {"enabled": enabled, "connectors": connectors}


# ---------------------------------------------------------------- submit (auto-MR)

@frappe.whitelist(methods=["POST"])
def submit_multi_for_release(name):
	"""Multiple-flow submit: validate -> Work Order (skip_transfer) -> auto
	Material Request (tagged) -> status 'Pending Material Request'."""
	_require_enabled()
	doc = frappe.get_doc(DOCTYPE, name)
	frappe.has_permission(DOCTYPE, "write", doc=doc, throw=True)
	_gate_creator(doc)

	if doc.flow_type != "Multiple":
		frappe.throw(_("{0} is not a Multiple-flow entry.").format(name))
	if doc.ts_variance_status != "Draft":
		frappe.throw(_("Only a Draft entry can be submitted (current: {0})."
		               ).format(doc.ts_variance_status))
	if flt(doc.actual_produced_qty) <= 0:
		frappe.throw(_("Actual Produced Qty must be greater than zero."))
	if not (doc.materials or []):
		frappe.throw(_("Add the raw materials consumed."))
	conn = frappe.db.get_value("TS BOM Connector", doc.bom_connector,
	                           ["main_bom", "active"], as_dict=True)
	if not conn or not conn.active:
		frappe.throw(_("BOM Connector {0} not found or inactive.").format(doc.bom_connector))
	if doc.bom != conn.main_bom:
		frappe.throw(_("The entry's BOM ({0}) must be the connector's MAIN BOM ({1})."
		               ).format(doc.bom, conn.main_bom))

	settings = api._get_settings()
	api.compute_and_set_variance(doc)                 # informational (decision 3)
	api._block_if_missing_valuation(doc, settings)    # fail-closed, BEFORE any mutation
	user = frappe.session.user
	wo_name = mr_name = None
	try:
		with wo_engine.system_session():
			wo_name = wo_engine.create_and_submit_work_order(doc, settings, skip_transfer=1)
			mr_name = _create_and_submit_release_mr(doc, settings)
	except Exception:
		if wo_name:
			try:
				with wo_engine.system_session():
					_cancel_work_order_and_job_cards(wo_name)
			except Exception:
				frappe.clear_messages()
		raise

	attrs = wo_engine.read_bom_attrs(doc.bom)
	doc.db_set("actual_produced_qty_at_submission", flt(doc.actual_produced_qty),
	           update_modified=False)
	doc.db_set("submitted_by", user, update_modified=False)
	doc.db_set("work_order", wo_name, update_modified=False)
	doc.db_set("material_request", mr_name, update_modified=False)
	doc.db_set("bom_with_operations", cint(attrs.get("with_operations")), update_modified=False)
	doc.db_set("material_variance_pct", flt(doc.material_variance_pct), update_modified=False)
	doc.db_set("produced_variance_pct", flt(doc.produced_variance_pct), update_modified=False)
	doc.db_set("variance_breach", cint(doc.variance_breach), update_modified=False)
	doc.db_set("ts_variance_status", "Pending Material Request", update_modified=False)
	doc.add_comment("Comment", _(
		"Submitted (Multiple flow) by {0}. Work Order {1} + auto Material Request {2} "
		"created — awaiting Store Manager release."
	).format(user, wo_name, mr_name))

	try:
		_notify_stores_managers(doc, mr_name)
	except Exception:
		frappe.clear_messages()
	frappe.db.commit()
	return {"ok": True, "ts_variance_status": "Pending Material Request",
	        "work_order": wo_name, "material_request": mr_name}


def _create_and_submit_release_mr(doc, settings):
	"""Build + submit the Material-Transfer MR (Stores -> WIP). Runs elevated.
	Material Transfer MRs bypass the custom MR approval chain by design (v2.9.9
	stores-flow independence — feasibility T3)."""
	wip = settings.get("wip_warehouse")
	src_default = settings.get("release_source_warehouse") or wip
	cc = (frappe.db.get_single_value("TS Settings", SETTING_MR_COST_CENTER)
	      or frappe.get_cached_value("Company", doc.company, "cost_center"))
	if not cc:
		frappe.throw(_("Set 'Production MR Cost Center' in TS Settings (or a default "
		               "cost centre on the company) — the Material Request naming needs it."))

	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Material Transfer"
	mr.company = doc.company
	mr.cost_center = cc
	mr.schedule_date = nowdate()
	mr.ts_production_run = doc.name  # tag (user decision: filterable, not hidden)
	for row in doc.materials:
		if flt(row.actual_qty) <= 0:
			continue
		# from_warehouse = where the RM comes FROM (Stores). The PE row's
		# source_warehouse is a fetch_bom_standard artifact that DEFAULTS TO WIP —
		# honoring it blindly would ask the Store Manager to transfer WIP -> WIP
		# (audit HIGH, 3 Jul). Use it only when it's a real non-WIP override.
		row_src = row.source_warehouse if (row.source_warehouse and row.source_warehouse != wip) else None
		mr.append("items", {
			"item_code": row.item_code,
			"qty": flt(row.actual_qty),
			"schedule_date": nowdate(),
			"warehouse": wip,
			"from_warehouse": row_src or src_default,
			"ts_delivery_location": _("Production run {0}").format(doc.name),
			"ts_item_remark": _("Multiple-flow raw material release"),
		})
	if not (mr.items or []):
		frappe.throw(_("No material row has a quantity greater than zero."))
	mr.flags.ignore_permissions = True
	mr.insert(ignore_permissions=True)
	mr.submit()
	return mr.name


# ---------------------------------------------------------------- SE on_submit hook

def production_multi_on_stock_entry_submit(doc, method=None):
	"""Stock Entry on_submit (hooks.py): when the tagged MR's Material Transfer SE
	is submitted by the Store Manager, advance the run to 'Awaiting Distribution'.
	Best-effort beyond the status flip — a Job-Card automation failure must never
	block the Store Manager's SE submit (complete_distribution re-runs it)."""
	if getattr(doc, "purpose", "") != "Material Transfer":
		return
	mr_names = {r.material_request for r in (doc.items or []) if r.get("material_request")}
	if not mr_names:
		return
	entries = frappe.get_all(
		DOCTYPE,
		filters={"material_request": ["in", list(mr_names)],
		         "flow_type": "Multiple",
		         "ts_variance_status": "Pending Material Request"},
		pluck="name")
	for name in entries:
		pe = frappe.get_doc(DOCTYPE, name)
		pe.db_set("release_stock_entry", doc.name, update_modified=False)
		pe.db_set("released_by", frappe.session.user, update_modified=False)
		pe.db_set("released_at", now_datetime(), update_modified=False)
		try:
			pe.db_set("wip_released_qty", wo_engine.released_value(doc.name),
			          update_modified=False)
		except Exception:
			frappe.clear_messages()
		if cint(pe.bom_with_operations):
			try:
				with wo_engine.system_session():
					wo_engine.auto_complete_job_cards(pe.work_order)
			except Exception:
				frappe.clear_messages()  # retried idempotently at distribution time
		pe.db_set("ts_variance_status", "Awaiting Distribution", update_modified=False)
		try:
			pe.add_comment("Comment", _(
				"Raw material released via {0} (from MR {1}) — awaiting the PM's "
				"multi-warehouse distribution.").format(doc.name, pe.material_request))
		except Exception:
			frappe.clear_messages()
		try:
			_notify_departments(pe)
		except Exception:
			frappe.clear_messages()


# ---------------------------------------------------------------- MR on_cancel hook

def production_multi_on_mr_cancel(doc, method=None):
	"""Material Request on_cancel (hooks.py): a cancelled tagged MR rejects the
	pending Multiple-flow run (its WO is torn down)."""
	run = getattr(doc, "ts_production_run", None)
	if not run:
		return
	status = frappe.db.get_value(DOCTYPE, run, "ts_variance_status")
	if status != "Pending Material Request":
		return
	pe = frappe.get_doc(DOCTYPE, run)
	try:
		with wo_engine.system_session():
			_cancel_work_order_and_job_cards(pe.work_order)
	except Exception:
		frappe.clear_messages()
	pe.db_set("ts_variance_status", "Rejected", update_modified=False)
	pe.db_set("rejected_by", frappe.session.user, update_modified=False)
	pe.db_set("rejection_reason", _("Material Request {0} was cancelled.").format(doc.name),
	          update_modified=False)
	try:
		pe.add_comment("Comment", _(
			"Auto Material Request {0} cancelled — run rejected.").format(doc.name))
	except Exception:
		frappe.clear_messages()


# ---------------------------------------------------------------- distribution

@frappe.whitelist(methods=["POST"])
def complete_distribution(name, distribution):
	"""PM posts the manual multi-warehouse split: ONE Manufacture SE with multiple
	finished/by-product rows (T1/T2). Sum-per-item MUST equal the actual quantity."""
	_require_enabled()
	doc = frappe.get_doc(DOCTYPE, name)
	frappe.has_permission(DOCTYPE, "write", doc=doc, throw=True)
	_gate_creator(doc)
	if doc.flow_type != "Multiple":
		frappe.throw(_("{0} is not a Multiple-flow entry.").format(name))
	if doc.ts_variance_status != "Awaiting Distribution":
		frappe.throw(_("Distribution is only possible from 'Awaiting Distribution' "
		               "(current: {0}).").format(doc.ts_variance_status))
	if isinstance(distribution, str):
		distribution = json.loads(distribution or "[]")
	rows = _validate_distribution(doc, distribution)

	settings = api._get_settings()
	user = frappe.session.user
	with wo_engine.system_session():
		# persist the split on the entry (audit) — children are not tamper-guarded
		doc.set("fg_distribution", [])
		for r in rows:
			doc.append("fg_distribution", r)
		doc.flags.ignore_permissions = True
		doc.save()
		doc.reload()

		if cint(doc.bom_with_operations):
			wo_engine.auto_complete_job_cards(doc.work_order)  # idempotent retry
		se = _submit_distribution_manufacture_se(doc, rows, settings)
		wo_engine.close_work_order_if_needed(doc.work_order)
		return_se = None
		if settings.get("auto_return_surplus"):
			try:
				return_se, _surplus = wo_engine.auto_return_surplus(doc.work_order, settings)
			except Exception:
				frappe.clear_messages()

	released_val = flt(doc.wip_released_qty)
	consumed_val = wo_engine.manufacture_consumed_value(se)
	recon_pct = ((released_val - consumed_val) / consumed_val * 100.0) if consumed_val else 0.0
	note = _build_recon_note(released_val, consumed_val, recon_pct, return_se, settings)

	doc.db_set("linked_stock_entry", se, update_modified=False)
	doc.db_set("distribution_stock_entry", se, update_modified=False)
	doc.db_set("distribution_done", 1, update_modified=False)
	doc.db_set("wip_consumed_qty", consumed_val, update_modified=False)
	doc.db_set("wip_reconcile_variance_pct", flt(recon_pct), update_modified=False)
	if return_se:
		doc.db_set("wip_returned_stock_entry", return_se, update_modified=False)
	doc.db_set("wip_reconcile_note", note, update_modified=False)
	doc.db_set("posted_by", user, update_modified=False)
	doc.db_set("ts_variance_status", "Completed", update_modified=False)  # LAST (L288)
	doc.add_comment("Comment", _(
		"Multi-warehouse distribution posted by {0} via {1} ({2} rows)."
	).format(user, se, len(rows)))
	frappe.db.commit()
	return {"ok": True, "ts_variance_status": "Completed", "stock_entry": se,
	        "wip_returned_stock_entry": return_se}


def _validate_distribution(doc, distribution):
	"""Server-side sum-must-equal-actual per item + warehouse sanity. Returns
	normalized child rows. NEVER trusts the client."""
	if not distribution:
		frappe.throw(_("No distribution rows given."))
	fg_item = doc.production_item
	targets = {fg_item: ("Finished", flt(doc.actual_produced_qty))}
	for bp in (doc.byproducts or []):
		if flt(bp.actual_qty) > 0:
			targets[bp.item_code] = ("By-Product", flt(bp.actual_qty))

	sums, rows = {}, []
	for d in distribution:
		item = (d.get("item_code") or "").strip()
		wh = (d.get("warehouse") or "").strip()
		qty = flt(d.get("qty"))
		if item not in targets:
			frappe.throw(_("Row item {0} is not the finished item or a by-product "
			               "of this run.").format(item))
		if qty <= 0:
			frappe.throw(_("Distribution quantities must be greater than zero ({0})."
			               ).format(item))
		wh_company = frappe.db.get_value("Warehouse", wh, "company")
		if not wh_company:
			frappe.throw(_("Warehouse {0} not found.").format(wh))
		if wh_company != doc.company:
			frappe.throw(_("Warehouse {0} belongs to {1}, not {2}."
			               ).format(wh, wh_company, doc.company))
		sums[item] = flt(sums.get(item)) + qty
		rows.append({
			"item_code": item,
			"line_type": targets[item][0],
			"warehouse": wh,
			"qty": qty,
			"uom": d.get("uom"),
			"rate": flt(d.get("rate")),
		})

	for item, (_kind, target) in targets.items():
		got = flt(sums.get(item))
		if abs(got - target) > max(_EPS, abs(target) * 1e-9):
			frappe.throw(_(
				"Distribution for {0} must total exactly {1} (you gave {2})."
			).format(item, target, got))
	return rows


def _submit_distribution_manufacture_se(doc, rows, settings):
	"""ONE Manufacture SE: BOM-scaled consumption FROM WIP + the PM's per-warehouse
	finished/by-product rows (feasibility T1/T2). Runs inside system_session."""
	from erpnext.manufacturing.doctype.work_order.work_order import (
		make_stock_entry as wo_make_se,
	)

	# Idempotency: an already-submitted Manufacture SE for this WO wins.
	existing = frappe.get_all("Stock Entry", filters={
		"work_order": doc.work_order, "purpose": "Manufacture", "docstatus": 1,
	}, pluck="name", limit=1)
	if existing:
		return existing[0]

	wip = settings.get("wip_warehouse")
	se = frappe.get_doc(wo_make_se(doc.work_order, "Manufacture",
	                               flt(doc.actual_produced_qty)))

	# skip_transfer WOs backflush on "material transferred" (= 0 in the MR path),
	# so ERPNext yields NO consumption rows — add them explicitly from the WO's
	# required items (the PM's release quantities), consumed FROM WIP.
	prec = cint(frappe.db.get_default("float_precision")) or 3
	# Consumption = the PE's ACTUAL materials — exactly what the auto-MR released
	# into WIP (client requirement: "PM adds actual material used"). NOT the WO/BOM
	# requirement basis: skip_transfer templates either backflush nothing or pull
	# the full BOM list, both wrong here (would go negative on never-released items).
	for r in [x for x in se.items if x.s_warehouse]:
		se.remove(r)
	for m in (doc.materials or []):
		qty = flt(flt(m.actual_qty), prec)
		if qty <= 0:
			continue
		stock_uom = frappe.db.get_value("Item", m.item_code, "stock_uom")
		rate = flt(frappe.db.get_value(
			"Bin", {"item_code": m.item_code, "warehouse": wip}, "valuation_rate"))
		se.append("items", {
			"item_code": m.item_code,
			"qty": qty, "transfer_qty": qty,
			"uom": stock_uom, "stock_uom": stock_uom,
			"conversion_factor": 1,
			"s_warehouse": wip,
			"basic_rate": rate,
			"basic_amount": qty * rate,
			"allow_zero_valuation_rate": 1 if not rate else 0,
		})
	# Defensive: drop ANY row whose stock qty rounds to zero at ERPNext's float
	# precision (template or appended) — validation rejects zero-stock-qty rows.
	for r in [x for x in se.items
	          if flt(flt(x.qty) * (flt(x.conversion_factor) or 1.0), prec) <= 0]:
		se.remove(r)

	fg_rows = [r for r in se.items if cint(r.is_finished_item)]
	template_inward = [r for r in se.items
	                   if (not r.s_warehouse) and not cint(r.is_finished_item)]
	# consumption always FROM WIP (the MR transfer physically put the RM there)
	for r in se.items:
		if r.s_warehouse:
			r.s_warehouse = wip

	# drop template inward rows; rebuild finished + by-product rows from the split
	for r in list(fg_rows[1:]) + template_inward:
		se.remove(r)
	fg_template = fg_rows[0]
	dist_fg = [r for r in rows if r["line_type"] == "Finished"]
	dist_bp = [r for r in rows if r["line_type"] == "By-Product"]

	first = dist_fg[0]
	fg_template.qty = first["qty"]
	fg_template.transfer_qty = first["qty"]
	fg_template.t_warehouse = first["warehouse"]
	for extra in dist_fg[1:]:
		se.append("items", {
			"item_code": extra["item_code"], "qty": extra["qty"],
			"uom": fg_template.uom, "stock_uom": fg_template.stock_uom,
			"conversion_factor": 1, "t_warehouse": extra["warehouse"],
			"is_finished_item": 1,
		})
	bp_rates = {bp.item_code: flt(bp.rate) for bp in (doc.byproducts or [])}
	for bp in dist_bp:
		se.append("items", {
			"item_code": bp["item_code"], "qty": bp["qty"],
			"conversion_factor": 1, "t_warehouse": bp["warehouse"],
			"is_scrap_item": 1,
			"basic_rate": bp["rate"] or bp_rates.get(bp["item_code"]) or 0,
		})

	# By-product cap first (may rescale scrap rates), THEN explicit FG costing:
	# ERPNext cannot auto-derive a rate for a virgin FG item split across multiple
	# finished rows, so we apply the standard formula ourselves:
	#   fg_rate = max(consumed_value - by_product_value, 0) / total_fg_qty
	wo_engine._cap_byproduct_rates(se)
	out_val = sum(flt(r.basic_amount) for r in se.items if r.s_warehouse)
	scrap_val = sum(flt(r.basic_amount) for r in se.items
	                if (not r.s_warehouse) and not cint(r.is_finished_item))
	fg_qty = sum(flt(r.qty) for r in se.items if cint(r.is_finished_item))
	fg_rate = (max(out_val - scrap_val, 0.0) / fg_qty) if fg_qty else 0.0
	for r in se.items:
		if cint(r.is_finished_item):
			r.basic_rate = fg_rate
			r.basic_amount = flt(r.qty) * fg_rate
			r.set_basic_rate_manually = 1
			r.allow_zero_valuation_rate = 1
		elif not r.s_warehouse:
			# by-product rows: explicit rate already set; allow zero on rate-less rows
			if not flt(r.basic_rate):
				r.allow_zero_valuation_rate = 1

	se.flags.ignore_permissions = True
	se.insert(ignore_permissions=True)
	wo_engine._auto_qi_for_se(se)           # BOM inspection_required auto-QI (reloads)
	se.submit()
	return se.name


# ---------------------------------------------------------------- notify

def _notify_stores_managers(doc, mr_name):
	"""Bell the Stores Managers about the pending MR release (best-effort)."""
	holders = frappe.db.sql("""
		SELECT DISTINCT u.name FROM `tabHas Role` hr
		JOIN `tabUser` u ON u.name = hr.parent AND u.enabled = 1
		WHERE hr.role = 'Stores Manager' AND u.name NOT IN ('Administrator', 'Guest')
	""", as_dict=True)
	for h in holders:
		try:
			frappe.get_doc({
				"doctype": "Notification Log",
				"for_user": h.name,
				"type": "Alert",
				"subject": _("Production release pending — {0}").format(mr_name),
				"email_content": _(
					"Multiple-flow production run {0}: release the raw material via "
					"Material Request {1}."
				).format(frappe.utils.escape_html(doc.name),
				         frappe.utils.escape_html(mr_name)),
				"document_type": "Material Request",
				"document_name": mr_name,
			}).insert(ignore_permissions=True)
		except Exception:
			frappe.clear_messages()


def _notify_departments(pe):
	"""Notify each connector department category (best-effort; escaped values)."""
	from trustbit_ethanol.ts_gate_entry.ts_production_notify import notify_category

	cats = frappe.get_all("TS BOM Connector Line",
	                      filters={"parent": pe.bom_connector}, pluck="category", limit=0)
	safe_run = frappe.utils.escape_html(pe.name)
	for cat in set(cats):
		notify_category(
			cat,
			_("Log department consumption — {0}").format(safe_run),
			_("Production run {0} has been released. Please log your department's "
			  "material consumption (reporting only).").format(safe_run),
			ref_doctype=DOCTYPE, ref_name=pe.name,
		)
