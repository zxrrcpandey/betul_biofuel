"""Multiple (BOM Connector) production flow — Phase D of the Multi-BOM plan,
re-sequenced by the Department Actual Production rework (4 Jul 2026 —
PLAN_dept_production_rework.md, user decisions U1-U7).

Flow (v2.21 dept-gate re-sequencing):

  Draft ──submit_multi_for_release──▶ Awaiting Department Production
        (validate + valuation block; Work Order [skip_transfer=1] created;
         ONE 'Pending' TS Production Department Entry auto-created per connector
         department line; departments notified to ADD PRODUCTION [E1].
         The auto-MR is DEFERRED — R5. Zero-dept-line connectors fall through
         directly to the old immediate-MR path so a run never waits on nothing.)
  ── each department logs its actuals (submit_department_production) or an
     Administrator skips it (admin_skip_department, U3) ──▶ when the LAST
     gating entry leaves 'Pending', the ATOMIC all-complete trigger
     (check_all_departments_complete: FOR-UPDATE re-select + material_request-
     null idempotency, L288) creates + submits the auto MATERIAL REQUEST
     [Material Transfer, tagged ts_production_run] and notifies the Store
     Managers ──▶ Pending Material Request
  ── Store Manager releases via the EXISTING MR stores flow; when the MR is
     FULLY transferred (on_submit hook below — D1 fix: partial releases no
     longer advance the run) ──▶ Awaiting Distribution
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
DEPT_DOCTYPE = "TS Production Department Entry"
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


# v2.21 UAT ④ — configurable production access. These roles ALWAYS pass (they are
# also who may edit the list on TS Settings); an EMPTY list = legacy role behavior.
_PROD_AUTH_ROLES = {"Super Admin", "CEO", "MD", "System Manager"}


def _authorized_production_users():
	rows = frappe.db.sql("""
		SELECT `user` FROM `tabTS Production Authorized User`
		WHERE parenttype = 'TS Settings' AND parent = 'TS Settings'
	""")
	return {r[0] for r in rows}


def _is_production_authorized(user=None):
	"""True when the user may run production (create Multiple runs / distribute).
	Fail-open while the TS Settings list is EMPTY (unconfigured = today's behavior);
	once configured, only listed users + _PROD_AUTH_ROLES/Administrator pass."""
	user = user or frappe.session.user
	if user == "Administrator" or _PROD_AUTH_ROLES & set(frappe.get_roles(user)):
		return True
	try:
		allowed = _authorized_production_users()
	except Exception:
		return True  # table not migrated yet — behave as unconfigured
	return (user in allowed) if allowed else True


def _gate_creator(doc):
	user = frappe.session.user
	if not _is_production_authorized(user):
		frappe.throw(_("You are not authorized to run production. Ask an administrator "
		               "to add you under TS Settings → Production Authorized Users."),
		             frappe.PermissionError)
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
	return {"enabled": enabled, "connectors": connectors,
	        "authorized": _is_production_authorized()}


# ---------------------------------------------------------------- submit (auto-MR)

@frappe.whitelist(methods=["POST"])
def submit_multi_for_release(name):
	"""Multiple-flow submit (v2.21 re-sequenced): validate -> Work Order
	(skip_transfer) -> ONE 'Pending' department entry per connector dept line +
	department notifications -> status 'Awaiting Department Production'.
	The auto-MR is DEFERRED to check_all_departments_complete() (R5).
	Zero-dept-line connectors fall through to the old immediate-MR path."""
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

	dept_lines = frappe.get_all(
		"TS BOM Connector Line", filters={"parent": doc.bom_connector},
		fields=["bom", "category"], order_by="idx", limit=0)

	settings = api._get_settings()
	api.compute_and_set_variance(doc)                 # informational (decision 3)
	api._block_if_missing_valuation(doc, settings)    # fail-closed, BEFORE any mutation
	user = frappe.session.user
	wo_name = mr_name = None
	dept_entries = []
	try:
		with wo_engine.system_session():
			wo_name = wo_engine.create_and_submit_work_order(doc, settings, skip_transfer=1)
			if dept_lines:
				dept_entries = _create_pending_dept_entries(doc, dept_lines)
			else:
				# Fall-through: nothing to wait for — old immediate-MR path so a
				# run can never stall in 'Awaiting Department Production'.
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
	doc.db_set("bom_with_operations", cint(attrs.get("with_operations")), update_modified=False)
	doc.db_set("material_variance_pct", flt(doc.material_variance_pct), update_modified=False)
	doc.db_set("produced_variance_pct", flt(doc.produced_variance_pct), update_modified=False)
	doc.db_set("variance_breach", cint(doc.variance_breach), update_modified=False)

	if dept_lines:
		# status LAST (L288), then best-effort notifications
		doc.db_set("ts_variance_status", "Awaiting Department Production",
		           update_modified=False)
		doc.add_comment("Comment", _(
			"Submitted (Multiple flow) by {0}. Work Order {1} created; {2} department "
			"production entr{3} pending — the release Material Request will be created "
			"automatically when every department has added its production."
		).format(user, wo_name, len(dept_entries),
		         "y is" if len(dept_entries) == 1 else "ies are"))
		try:
			_notify_departments_add_production(doc, dept_lines)
		except Exception:
			frappe.clear_messages()
		_warn_zero_recipient_categories(dept_lines)   # DEC-15, msgprint only
		frappe.db.commit()
		return {"ok": True, "ts_variance_status": "Awaiting Department Production",
		        "work_order": wo_name, "department_entries": dept_entries}

	doc.db_set("material_request", mr_name, update_modified=False)
	doc.db_set("ts_variance_status", "Pending Material Request", update_modified=False)
	doc.add_comment("Comment", _(
		"Submitted (Multiple flow) by {0}. Work Order {1} + auto Material Request {2} "
		"created — awaiting Store Manager release (connector has no department lines)."
	).format(user, wo_name, mr_name))
	try:
		_notify_stores_managers(doc, mr_name)
	except Exception:
		frappe.clear_messages()
	frappe.db.commit()
	return {"ok": True, "ts_variance_status": "Pending Material Request",
	        "work_order": wo_name, "material_request": mr_name}


def _create_pending_dept_entries(doc, dept_lines):
	"""One 'Pending' department entry per connector dept line (runs elevated —
	the entries are system gate artifacts; departments only FILL them). Insert
	as Draft (materials may be empty pre-log), then db_set Pending + notified_at."""
	created = []
	for ln in dept_lines:
		entry = frappe.get_doc({
			"doctype": DEPT_DOCTYPE,
			"production_entry": doc.name,
			"bom": ln.bom,
			"category": ln.category,
			"posting_date": nowdate(),
		})
		entry.insert(ignore_permissions=True)
		entry.db_set("notified_at", now_datetime(), update_modified=False)
		entry.db_set("status", "Pending", update_modified=False)
		created.append(entry.name)
	return created


def _warn_zero_recipient_categories(dept_lines):
	"""DEC-15: a gating category with no active recipients means nobody is told
	to Add Production — warn the PM loudly at submit (run proceeds; the board
	shows the stall and an Administrator can Skip)."""
	for cat in sorted({ln.category for ln in dept_lines}):
		has_recipient = frappe.db.exists(
			"TS Production Notify Recipient",
			{"parenttype": "TS Production BOM Category", "parent": cat, "active": 1})
		if not has_recipient:
			frappe.msgprint(_(
				"⚠ Category {0} has NO active notification recipients — nobody will "
				"be notified to Add Production for it. Configure recipients on the "
				"category, or an Administrator will have to Skip it."
			).format(frappe.utils.escape_html(cat)), indicator="orange")


def check_all_departments_complete(run_name):
	"""ATOMIC all-departments-complete trigger (called after a dept entry leaves
	'Pending' via Logged or Skipped). Creates + submits the release MR exactly
	once and advances the run — the single most concurrency-sensitive transition
	of the rework:
	  - FOR-UPDATE re-select serializes simultaneous last-dept submissions;
	  - run-status check FIRST so legacy/grandfathered runs can never misfire;
	  - material_request-null is the idempotency key;
	  - status flipped LAST after the MR exists (L288).
	Runs inside the caller's transaction — if MR creation throws, the caller's
	whole request (including the dept entry's Logged flip) rolls back."""
	row = frappe.db.sql(
		"""SELECT name, ts_variance_status, material_request
		   FROM `tabTS Production Entry` WHERE name = %s FOR UPDATE""",
		run_name, as_dict=True)
	if not row:
		return {"mr_created": False, "reason": "run not found"}
	row = row[0]
	if row.ts_variance_status != "Awaiting Department Production":
		return {"mr_created": False, "reason": "run not awaiting departments"}
	if row.material_request:
		return {"mr_created": False, "reason": "material request already exists"}
	pending = frappe.db.count(DEPT_DOCTYPE,
	                          {"production_entry": run_name, "status": "Pending"})
	if pending:
		return {"mr_created": False, "reason": "{0} department(s) still pending".format(pending)}

	doc = frappe.get_doc(DOCTYPE, run_name)
	settings = api._get_settings()
	with wo_engine.system_session():
		mr_name = _create_and_submit_release_mr(doc, settings)
	doc.db_set("material_request", mr_name, update_modified=False)
	doc.db_set("ts_variance_status", "Pending Material Request", update_modified=False)
	try:
		doc.add_comment("Comment", _(
			"All departments completed — auto Material Request {0} created; "
			"Store Managers notified to release.").format(mr_name))
	except Exception:
		frappe.clear_messages()
	try:
		_notify_stores_managers(doc, mr_name)
	except Exception:
		frappe.clear_messages()
	return {"mr_created": True, "material_request": mr_name}


def _create_and_submit_release_mr(doc, settings):
	"""Build + submit the Material-Transfer MR (Stores -> WIP). Runs elevated.
	Material Transfer MRs bypass the custom MR approval chain by design (v2.9.9
	stores-flow independence — feasibility T3)."""
	wip = settings.get("wip_warehouse")
	src_default = settings.get("release_source_warehouse") or wip
	# v2.21 ② department-wise: prefer the run's category cost centre, else the MR
	# setting, else the company default (resolver handles the full chain).
	cc = wo_engine.resolve_production_cost_center(doc)
	if not cc:
		frappe.throw(_("No cost centre resolved — set a Cost Center on the run's "
		               "TS Production BOM Category, or 'Production MR Cost Center' in "
		               "TS Settings, or a company default. The Material Request naming needs it."))

	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Material Transfer"
	mr.company = doc.company
	mr.cost_center = cc
	mr.schedule_date = nowdate()
	mr.ts_production_run = doc.name  # tag (user decision: filterable, not hidden)

	# CONSOLIDATE by item_code — a real client BOM can list the same item on two
	# rows (e.g. Formaldehyde 10-25-0020 twice on the RS BOM). ERPNext buying's
	# validate_for_items rejects a Material Request that repeats an item_code
	# (uniqueness key is item_code ONLY, not item+warehouse), which would roll
	# back the whole department submit. Summing per item is the correct transfer
	# semantics anyway: one line per raw material.
	agg = {}
	order = []
	for row in doc.materials:
		if flt(row.actual_qty) <= 0:
			continue
		# from_warehouse = where the RM comes FROM (Stores). The PE row's
		# source_warehouse is a fetch_bom_standard artifact that DEFAULTS TO WIP —
		# honoring it blindly would ask the Store Manager to transfer WIP -> WIP
		# (audit HIGH, 3 Jul). Use it only when it's a real non-WIP override.
		row_src = row.source_warehouse if (row.source_warehouse and row.source_warehouse != wip) else None
		src = row_src or src_default
		if row.item_code not in agg:
			agg[row.item_code] = {"qty": 0.0, "from_warehouse": src}
			order.append(row.item_code)
		agg[row.item_code]["qty"] += flt(row.actual_qty)
		# same item from two different sources — fall back to the default Stores
		# source so the item still resolves to a single MR row (item-code-unique).
		if agg[row.item_code]["from_warehouse"] != src:
			agg[row.item_code]["from_warehouse"] = src_default

	for item_code in order:
		mr.append("items", {
			"item_code": item_code,
			"qty": agg[item_code]["qty"],
			"schedule_date": nowdate(),
			"warehouse": wip,
			"from_warehouse": agg[item_code]["from_warehouse"],
			"cost_center": cc,  # v2.21 ② carries to the release SE rows
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
	is submitted by the Store Manager, advance the run to 'Awaiting Distribution' —
	but ONLY once the MR is FULLY transferred (D1 fix, 4 Jul: the old code fired
	on the FIRST SE regardless of quantity, so a partial release advanced the run
	and complete_distribution could consume WIP stock that never arrived).
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
		# Recomputed from DB (docstatus=1 SEs only) so partial→partial→full and
		# cancel-then-resubmit sequences all converge to the right totals.
		released_val, se_names = _mr_release_state_value(pe.material_request)
		try:
			pe.db_set("wip_released_qty", released_val, update_modified=False)
		except Exception:
			frappe.clear_messages()
		if not _mr_fully_transferred(pe.material_request):
			# D1: partial release — stay at 'Pending Material Request'.
			try:
				pe.add_comment("Comment", _(
					"Partial raw-material release via {0} (MR {1} not yet fully "
					"transferred) — run stays pending until the remaining quantity "
					"is released.").format(doc.name, pe.material_request))
			except Exception:
				frappe.clear_messages()
			continue
		pe.db_set("release_stock_entry", doc.name, update_modified=False)
		pe.db_set("released_by", frappe.session.user, update_modified=False)
		pe.db_set("released_at", now_datetime(), update_modified=False)
		if cint(pe.bom_with_operations):
			try:
				with wo_engine.system_session():
					wo_engine.auto_complete_job_cards(pe.work_order)
			except Exception:
				frappe.clear_messages()  # retried idempotently at distribution time
		pe.db_set("ts_variance_status", "Awaiting Distribution", update_modified=False)
		try:
			pe.add_comment("Comment", _(
				"Raw material fully released via {0} (MR {1}, {2} stock entr{3}) — "
				"awaiting the PM's multi-warehouse distribution."
			).format(doc.name, pe.material_request, len(se_names),
			         "y" if len(se_names) == 1 else "ies"))
		except Exception:
			frappe.clear_messages()


def _mr_fully_transferred(mr_name):
	"""True when every MR item's transferred stock qty (across ALL submitted
	Material Transfer SEs referencing the MR) covers the requested stock qty.
	v2.21 ①-hardening: only NON-transit legs count as arrival — a native
	in-transit release (Stores -> Goods In Transit) must never be treated as
	'the material reached WIP', or the run would advance prematurely."""
	requested = dict(frappe.db.sql(
		"""SELECT item_code, SUM(stock_qty) FROM `tabMaterial Request Item`
		   WHERE parent = %s GROUP BY item_code""", mr_name))
	transferred = dict(frappe.db.sql(
		"""SELECT sed.item_code, SUM(sed.transfer_qty)
		   FROM `tabStock Entry Detail` sed
		   JOIN `tabStock Entry` se ON se.name = sed.parent
		   WHERE sed.material_request = %s AND se.docstatus = 1
		     AND se.purpose = 'Material Transfer'
		     AND IFNULL(se.add_to_transit, 0) = 0
		   GROUP BY sed.item_code""", mr_name))
	for item, req_qty in requested.items():
		if flt(transferred.get(item)) + _EPS < flt(req_qty):
			return False
	return True


def _mr_release_state_value(mr_name):
	"""(total released value across all submitted transfer SEs, [se names])."""
	se_names = frappe.db.sql_list(
		"""SELECT DISTINCT se.name
		   FROM `tabStock Entry` se
		   JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		   WHERE sed.material_request = %s AND se.docstatus = 1
		     AND se.purpose = 'Material Transfer'
		     AND IFNULL(se.add_to_transit, 0) = 0""", mr_name)
	total = 0.0
	for se in se_names:
		try:
			total += flt(wo_engine.released_value(se))
		except Exception:
			frappe.clear_messages()
	return total, se_names


# ---------------------------------------------------------------- Store-Manager release (direct, no transit)

@frappe.whitelist(methods=["POST"])
def release_multi_run(name):
	"""One-click Store-Manager release for a Multiple-flow run (v2.21 ①).

	Builds ONE DIRECT Material-Transfer Stock Entry from the run's tagged MR —
	Stores -> WIP, NO in-transit warehouse — and submits it. Replaces the SM
	having to use ERPNext's native 'Transfer (In Transit)' button, which routed
	through a Goods-In-Transit warehouse and forced a manual second leg (and
	could advance the run while material was still in transit).

	Gate: Stores Manager or admin (mirrors ts_production_release.approve_release),
	server-enforced (L142/175). Idempotent: if the run already released, returns."""
	from trustbit_ethanol.ts_gate_entry.ts_production_release import _is_admin, _is_store_manager

	api._require_enabled()  # kill-switch parity with approve_release
	doc = frappe.get_doc(DOCTYPE, name)
	frappe.has_permission(DOCTYPE, "read", doc=doc, throw=True)
	if not (_is_store_manager() or _is_admin()):
		frappe.throw(_("Only a Stores Manager can release the raw material."),
		             frappe.PermissionError)
	if doc.flow_type != "Multiple":
		frappe.throw(_("{0} is not a Multiple-flow run.").format(name))
	if doc.ts_variance_status != "Pending Material Request":
		frappe.throw(_("This run is not awaiting release (current: {0})."
		               ).format(doc.ts_variance_status))
	if not doc.material_request:
		frappe.throw(_("No release Material Request exists for this run yet."))
	mr = frappe.get_doc("Material Request", doc.material_request)
	if mr.docstatus != 1:
		frappe.throw(_("The release Material Request {0} is not submitted."
		               ).format(doc.material_request))
	# idempotency — a fully-transferred MR means the release already happened
	if _mr_fully_transferred(doc.material_request):
		return {"ok": True, "already_released": True,
		        "ts_variance_status": doc.ts_variance_status}

	settings = api._get_settings()
	wip = settings.get("wip_warehouse")
	src_default = settings.get("release_source_warehouse") or wip
	from erpnext.stock.doctype.material_request.material_request import make_stock_entry as mr_make_se

	with wo_engine.system_session():
		se = frappe.get_doc(mr_make_se(doc.material_request))
		se.purpose = "Material Transfer"
		se.stock_entry_type = "Material Transfer"
		se.add_to_transit = 0                    # DIRECT — never in-transit
		se.company = doc.company
		mr_rows = {r.item_code: r for r in mr.items}
		for row in se.items:
			src = mr_rows.get(row.item_code)
			row.s_warehouse = (src.from_warehouse if src else None) or src_default
			row.t_warehouse = (src.warehouse if src else None) or wip
		wo_engine.apply_cost_center_to_se(se, wo_engine.resolve_production_cost_center(doc))
		se.flags.ignore_permissions = True
		se.insert(ignore_permissions=True)
		se.submit()  # on_submit hook advances the run (D1 full-transfer guard)
	frappe.db.commit()
	doc.reload()
	return {"ok": True, "stock_entry": se.name,
	        "ts_variance_status": doc.ts_variance_status}


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

	# v2.21 ② department-wise cost centre on every Manufacture SE row (consume + FG)
	wo_engine.apply_cost_center_to_se(se, wo_engine.resolve_production_cost_center(doc))
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


def _notify_departments_add_production(pe, dept_lines):
	"""E1 'Add Production' notification to each connector department category at
	PM submit (v2.21 — departments now GATE the release; best-effort; escaped)."""
	from trustbit_ethanol.ts_gate_entry.ts_production_notify import notify_category

	safe_run = frappe.utils.escape_html(pe.name)
	safe_item = frappe.utils.escape_html(pe.production_item_name or pe.production_item or "")
	for cat in sorted({ln.category for ln in dept_lines}):
		notify_category(
			cat,
			_("Add Production required — {0}").format(safe_run),
			_("Production run {0} ({1}) has been submitted. Please run your "
			  "department's BOM and ADD YOUR PRODUCTION details now — the Store "
			  "Manager release waits until every department has done this."
			  ).format(safe_run, safe_item),
			ref_doctype=DOCTYPE, ref_name=pe.name,
		)
