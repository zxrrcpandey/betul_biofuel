"""Single-flow DEPARTMENT release (v2.21, client request 13 Jul 2026).

Business model: the RS Single run consumes items that different departments
own (Boiler / WTP / Electrical), each sourced from that department's own
warehouse. Instead of the Store Manager releasing everything, each department
releases ITS OWN items with the ACTUAL quantity used, and the Store Manager
releases only the items no department claims. The Store Manager stays the FINAL
gate: approve_release is blocked until every department slot is Released, then
the (UNCHANGED) completion chain runs.

Architecture (mirrors the proven Multiple-flow gate):
  submit_for_release (Single)  -> _build_release_slots(): partition the draft
      release SE rows by claiming category (a category "claims" a warehouse via
      its release_warehouses child table) -> one TS Production Release Slot per
      involved department (Pending) + notify each category's recipients. Rows no
      category claims stay on the Store Manager's portion.
  department user             -> release_department_slot(): enters ACTUAL qty per
      item; edits the draft release SE rows (qty := actual, 0 drops the row) and
      mirrors the run's material rows so the Manufacture consumes ACTUALS
      (decision: department actual wins); slot -> Released.
  Store Manager               -> approve_release(): _all_slots_released() gate
      blocks until every slot is Released; the existing chain then runs.

Auth: a slot's releaser must be a recipient of that category (reuses
ts_production_dept._recipient_gate — one auth boundary). Kill-switch by
construction: zero categories with release_warehouses rows => zero slots =>
today's Store-Manager-only behavior, untouched.
"""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

SLOT_DOCTYPE = "TS Production Release Slot"
RUN_DOCTYPE = "TS Production Entry"


# ------------------------------------------------------------------ config

def _warehouse_to_category():
	"""{warehouse: category} for every ACTIVE category that claims warehouses.
	Empty dict => the whole split feature is inert (Store Manager releases all)."""
	rows = frappe.db.sql("""
		SELECT w.warehouse, w.parent AS category
		FROM `tabTS Production Release Warehouse` w
		JOIN `tabTS Production BOM Category` c ON c.name = w.parent
		WHERE w.parenttype = 'TS Production BOM Category'
		  AND c.active = 1 AND IFNULL(w.warehouse, '') != ''
		  AND IFNULL(c.is_production, 0) = 0
	""", as_dict=True)
	return {r.warehouse: r.category for r in rows}


def split_release_active():
	return bool(_warehouse_to_category())


# ------------------------------------------------------------------ slot build (at submit_for_release)

def preflight_bom_membership(doc):
	"""Pre-flight (14 Jul audit): a department item is only usable if the Manufacture
	will consume it, and the Manufacture consumes strictly the BOM's items. A material
	the PM sourced from a department-CLAIMED warehouse but that is NOT in the BOM would
	be released into WIP and STRANDED. Block at submit — BEFORE the Work Order/SE exist
	— with the exact config fix. No-op when the split feature is inert."""
	wh_cat = _warehouse_to_category()
	if not wh_cat:
		return
	claimed = [r for r in (doc.materials or [])
	           if r.item_code and r.source_warehouse and flt(r.actual_qty) > 0
	           and r.source_warehouse in wh_cat]
	if not claimed:
		return
	bom_items = set(frappe.get_all("BOM Item", filters={"parent": doc.bom},
	                               pluck="item_code", limit=0))
	orphan = sorted({r.item_code for r in claimed if r.item_code not in bom_items})
	if orphan:
		names = ", ".join("{0} ({1})".format(
			frappe.db.get_value("Item", c, "item_name") or c, c) for c in orphan)
		frappe.throw(
			_("These department items are not part of the production BOM, so production "
			  "cannot consume them (they would be transferred to WIP and left unused): {0}. "
			  "Add them to the BOM ({1}), then submit again."
			  ).format(frappe.utils.escape_html(names), frappe.utils.escape_html(doc.bom)),
			title=_("Item Not in BOM"))


def build_release_slots(doc, release_se_name):
	"""Create one Pending slot per department whose warehouse a release SE row is
	sourced from. Called at the tail of submit_for_release (Single), inside its
	transaction. No-op when no category claims any warehouse."""
	wh_cat = _warehouse_to_category()
	if not wh_cat:
		return []

	# The build engine forces every release row to the Stores default
	# (build_draft_release_se) and the WO->SE mapper DROPS tiny-ratio BOM rows
	# (a small run scales an enzyme's BOM qty toward 0). So for every material the
	# PM requested from a CLAIMED department warehouse we (a) re-point an existing
	# row to that warehouse, or (b) APPEND a release row when the mapper dropped it
	# — guaranteeing every department item is releasable. Driven by the run's
	# material rows, which carry the PM's per-item source_warehouse + actual_qty.
	from erpnext.stock.get_item_details import get_conversion_factor
	from trustbit_ethanol.ts_gate_entry import ts_production_wo as wo_engine
	from trustbit_ethanol.ts_gate_entry import ts_production_api as api
	wip = api._get_settings().get("wip_warehouse")
	pm = {r.item_code: r for r in (doc.materials or [])
	      if r.item_code and r.source_warehouse and flt(r.actual_qty) > 0
	      and r.source_warehouse in wh_cat}
	se = frappe.get_doc("Stock Entry", release_se_name)
	present = {r.item_code for r in se.items}
	changed = False
	for row in se.items:
		mat = pm.get(row.item_code)
		if mat and row.s_warehouse != mat.source_warehouse:
			row.s_warehouse = mat.source_warehouse
			changed = True
	for code, mat in pm.items():
		if code in present:
			continue
		cf = 1.0
		try:
			cf = flt((get_conversion_factor(code, mat.uom) or {}).get("conversion_factor")) or 1.0
		except Exception:
			cf = 1.0
		se.append("items", {
			"item_code": code, "qty": flt(mat.actual_qty), "uom": mat.uom,
			"conversion_factor": cf, "transfer_qty": flt(mat.actual_qty) * cf,
			"s_warehouse": mat.source_warehouse, "t_warehouse": wip,
		})
		changed = True
	if changed:
		wo_engine.apply_cost_center_to_se(se, wo_engine.resolve_production_cost_center(doc))
		se.flags.ignore_permissions = True
		se.save()
		se.reload()

	# group claimed rows by category
	by_cat = {}
	for row in se.items:
		cat = wh_cat.get(row.s_warehouse)
		if not cat:
			continue  # Store Manager's portion — untouched
		by_cat.setdefault(cat, []).append(row)
	if not by_cat:
		return []

	created = []
	for cat, rows in by_cat.items():
		department = frappe.db.get_value("TS Production BOM Category", cat, "department")
		slot = frappe.new_doc(SLOT_DOCTYPE)
		slot.production_entry = doc.name
		slot.category = cat
		slot.department = department
		slot.company = doc.company
		slot.status = "Pending"
		slot.notified_at = now_datetime()
		for row in rows:
			slot.append("items", {
				"item_code": row.item_code,
				"item_name": frappe.db.get_value("Item", row.item_code, "item_name"),
				"warehouse": row.s_warehouse,
				"planned_qty": flt(row.qty),
				"actual_qty": flt(row.qty),   # prefilled = planned; the dept edits it
				"uom": row.uom,
			})
		slot.flags.ignore_permissions = True
		slot.insert(ignore_permissions=True)
		created.append(slot.name)
		_notify_slot(slot)
	return created


def _notify_slot(slot):
	"""In-app notification to the category's recipients that a release awaits them."""
	try:
		from trustbit_ethanol.ts_gate_entry import ts_production_notify as notify
		notify.notify_category(
			slot.category,
			_("Raw material to release — {0}").format(slot.production_entry),
			_("Production run {0} has {1} item(s) for your department ({2}) to "
			  "release with the actual used quantity.").format(
			  slot.production_entry, len(slot.items), slot.category),
			ref_doctype=SLOT_DOCTYPE, ref_name=slot.name)
	except Exception:
		frappe.clear_messages()  # notifications never block the flow (L238)


# ------------------------------------------------------------------ SM-last gate (in approve_release)

def pending_slots(run_name):
	return frappe.get_all(SLOT_DOCTYPE,
	                      filters={"production_entry": run_name, "status": "Pending"},
	                      pluck="name")


def assert_se_matches_slots(run_name, se_name):
	"""Final-gate integrity (17 Jul): every Released slot item must still be
	EXACTLY what the department released — same qty from the same warehouse in the
	draft SE (actual 0 = row absent). Any later edit (PM editing the draft SE, a
	crafted API call) blocks here with the offending rows named. Closes every edit
	path at the ONE submission gate instead of chasing each editor."""
	slots = frappe.get_all(SLOT_DOCTYPE,
		filters={"production_entry": run_name, "status": "Released"}, pluck="name")
	if not slots:
		return
	want = {}
	for sl in slots:
		doc = frappe.get_doc(SLOT_DOCTYPE, sl)
		for it in doc.items:
			want[(it.item_code, it.warehouse)] = (flt(it.actual_qty), doc.category)
	if not want:
		return
	se = frappe.get_doc("Stock Entry", se_name)
	have = {}
	for r in se.items:
		if (r.item_code, r.s_warehouse) in want:
			have[(r.item_code, r.s_warehouse)] = flt(r.qty)
	bad = []
	for key, (aqty, cat) in want.items():
		got = have.get(key, 0.0)
		if abs(got - aqty) > 1e-6:
			bad.append(_("{0} from {1} ({2}): department released {3}, the Stock Entry now has {4}"
				).format(frappe.utils.escape_html(key[0]), frappe.utils.escape_html(key[1] or ""),
				         frappe.utils.escape_html(cat), aqty, got))
	if bad:
		frappe.throw(
			_("The release Stock Entry no longer matches what the departments released "
			  "— it was changed after their release:")
			+ "<ul><li>" + "</li><li>".join(bad) + "</li></ul>"
			+ _("Reject the release and re-submit the run (departments will release again)."),
			title=_("Release Changed After Department Release"))


def cancel_run_slots(run_name):
	"""reject_release teardown: void the run's slots so stale Pending slots can
	never block a resubmitted run (resubmit builds fresh slots)."""
	for sl in frappe.get_all(SLOT_DOCTYPE, filters={
			"production_entry": run_name, "status": ["in", ["Pending", "Released"]]},
			pluck="name"):
		frappe.db.set_value(SLOT_DOCTYPE, sl, "status", "Cancelled", update_modified=False)


def assert_all_slots_released(run_name):
	"""Called at the top of approve_release: the Store Manager cannot release the
	Stores portion until every department has released its own."""
	pend = pending_slots(run_name)
	if pend:
		cats = frappe.get_all(SLOT_DOCTYPE, filters={"name": ["in", pend]}, pluck="category")
		frappe.throw(
			_("These departments have not released their material yet: {0}. "
			  "The Store Manager release runs after every department releases its own."
			  ).format(frappe.utils.escape_html(", ".join(cats))),
			title=_("Department Release Pending"))


@frappe.whitelist()
def get_store_release_queue():
	"""Store-Manager release zone data: every Pending-Stores-Release run, each
	annotated with its STILL-PENDING department categories. The dashboard uses this
	to show 'Waiting for: <departments>' instead of an active Release button that
	the SM-last gate would only block (14 Jul UX fix)."""
	from trustbit_ethanol.ts_gate_entry import ts_production_dept as dept
	roles = set(frappe.get_roles())
	if not (dept._is_admin() or {"Stores Manager", "Manufacturing Manager",
	        "Manufacturing User", "PM", "Grain PM", "CEO", "MD"} & roles):
		return []  # only the release-zone audience may enumerate the queue
	runs = frappe.get_all(RUN_DOCTYPE,
		filters={"ts_variance_status": "Pending Stores Release"},
		fields=["name", "bom", "production_item", "production_item_name",
		        "actual_produced_qty", "production_uom", "work_order",
		        "release_stock_entry", "submitted_by", "owner", "modified"],
		order_by="modified desc", limit=20)
	if not runs:
		return []
	names = [r.name for r in runs]
	slots = frappe.get_all(SLOT_DOCTYPE,
		filters={"production_entry": ["in", names], "status": "Pending"},
		fields=["production_entry", "category"], limit=0)
	pend = {}
	for sl in slots:
		pend.setdefault(sl.production_entry, []).append(sl.category)
	for r in runs:
		r["pending_departments"] = pend.get(r.name, [])
	return runs


@frappe.whitelist()
def pending_department_map():
	"""{run_name: [pending department categories]} for every run sitting in
	'Pending Stores Release' that still has Pending department slots. Lets the
	dashboard DISPLAY 'Pending Department Release' (departments go first) while the
	stored status stays 'Pending Stores Release' (single status by design). Read-only."""
	from trustbit_ethanol.ts_gate_entry import ts_production_dept as dept
	if not (dept._is_admin() or {"Stores Manager", "Manufacturing Manager",
	        "Manufacturing User", "PM", "Grain PM", "CEO", "MD"} & set(frappe.get_roles())):
		return {}  # release-zone audience only (mirrors get_store_release_queue)
	rows = frappe.db.sql("""
		SELECT s.production_entry, s.category
		FROM `tabTS Production Release Slot` s
		JOIN `tabTS Production Entry` r ON r.name = s.production_entry
		WHERE s.status = 'Pending' AND r.ts_variance_status = 'Pending Stores Release'
	""", as_dict=True)
	out = {}
	for r in rows:
		out.setdefault(r.production_entry, []).append(r.category)
	return out


# ------------------------------------------------------------------ department release endpoint

@frappe.whitelist(methods=["POST"])
def release_department_slot(name, items):
	"""A department recipient releases its slot with ACTUAL quantities.

	Edits the run's DRAFT release SE rows for this slot's items (qty := actual;
	0 drops the row) and mirrors the WO required_items + the run material rows so
	the Manufacture consumes the ACTUALS (decision: department actual wins).
	Does NOT submit anything — the Store Manager remains the submission gate."""
	from trustbit_ethanol.ts_gate_entry import ts_production_dept as dept
	from trustbit_ethanol.ts_gate_entry import ts_production_api as api
	import json

	api._require_enabled()
	slot = frappe.get_doc(SLOT_DOCTYPE, name)
	dept._recipient_gate(slot.category)  # recipients of THIS category (or admin) only
	if slot.status != "Pending":
		frappe.throw(_("This release is already {0}.").format(slot.status))

	run = frappe.get_doc(RUN_DOCTYPE, slot.production_entry)
	if run.ts_variance_status != "Pending Stores Release":
		frappe.throw(_("Run {0} is not awaiting release (current: {1}).").format(
			run.name, run.ts_variance_status))
	if not run.release_stock_entry:
		frappe.throw(_("Run {0} has no draft release Stock Entry.").format(run.name))

	if isinstance(items, str):
		items = json.loads(items or "[]")
	actual = {}
	for it in items:
		code = it.get("item_code")
		if code:
			actual[code] = flt(it.get("actual_qty"))
	slot_items = {r.item_code for r in slot.items}
	if set(actual) - slot_items:
		frappe.throw(_("Item(s) not in this department's release: {0}").format(
			frappe.utils.escape_html(", ".join(sorted(set(actual) - slot_items)))))

	se = frappe.get_doc("Stock Entry", run.release_stock_entry)
	if se.docstatus != 0:
		frappe.throw(_("The release Stock Entry is no longer a draft (already submitted)."))

	# apply actuals to the DRAFT SE rows belonging to this slot's (item, warehouse)
	slot_wh = {r.item_code: r.warehouse for r in slot.items}
	kept, touched = [], set()
	for row in se.items:
		if row.item_code in actual and row.s_warehouse == slot_wh.get(row.item_code):
			qty = actual[row.item_code]
			touched.add(row.item_code)
			if qty <= 0:
				continue  # dropped — nothing released for this item
			cf = flt(row.conversion_factor) or 1.0
			row.qty = qty
			row.transfer_qty = qty * cf
		kept.append(row)
	se.items = kept
	se.flags.ignore_permissions = True
	se.save()

	# mirror onto the submitted WO required_items + the run material rows (actuals win)
	_mirror_actuals(run, slot_wh, actual)

	# stamp the slot's actuals + Released (status LAST, L288)
	for r in slot.items:
		r.db_set("actual_qty", actual.get(r.item_code, flt(r.actual_qty)),
		         update_modified=False)
	user = frappe.session.user
	slot.db_set("released_by", user, update_modified=False)
	slot.db_set("released_at", now_datetime(), update_modified=False)
	slot.db_set("status", "Released", update_modified=False)
	slot.add_comment("Comment", _("Released by {0} with actual quantities.").format(user))
	deltas = _notify_pm_of_deviation(run, slot, actual)
	run.add_comment("Comment", _("Department {0} released its material (by {1}).").format(
		slot.category, user) + (("<br><b>" + _("Differs from planned") + ":</b><br>"
		+ "<br>".join(deltas)) if deltas else ""))
	frappe.db.commit()
	return {"ok": True, "status": "Released",
	        "remaining": pending_slots(run.name)}


def _notify_pm_of_deviation(run, slot, actual):
	"""PM visibility (20 Jul): when a department releases with quantities different
	from planned, tell the run owner immediately (Notification Log) with the exact
	numbers — and return the delta lines for the run's timeline comment."""
	lines = []
	for it in slot.items:
		a = actual.get(it.item_code)
		if a is None:
			continue
		planned = flt(it.planned_qty)
		if abs(flt(a) - planned) < 1e-9:
			continue
		pct = ((flt(a) - planned) / planned * 100.0) if planned else 0.0
		lines.append(_("{0}: planned {1}, released {2} ({3}{4}%)").format(
			frappe.utils.escape_html(it.item_name or it.item_code),
			planned, flt(a), "+" if pct >= 0 else "", round(pct, 1)))
	if not lines:
		return []
	owner = run.owner
	if owner and owner not in ("Administrator", "Guest"):
		try:
			frappe.get_doc({
				"doctype": "Notification Log", "for_user": owner, "type": "Alert",
				"document_type": RUN_DOCTYPE, "document_name": run.name,
				"subject": _("{0} released DIFFERENT quantities — {1}").format(
					frappe.utils.escape_html(slot.category),
					frappe.utils.escape_html(run.name)),
				"email_content": "<br>".join(lines),
			}).insert(ignore_permissions=True)
		except Exception:
			frappe.clear_messages()  # notification never blocks the release (L238)
	return lines


def _mirror_actuals(run, slot_wh, actual):
	"""Push the department actuals onto the WO required_items and the run's
	material snapshot so the Manufacture consumes actuals, not the plan."""
	wo_items = frappe.get_all("Work Order Item",
	                          filters={"parent": run.work_order},
	                          fields=["name", "item_code"], limit=0)
	for wi in wo_items:
		if wi.item_code in actual and wi.item_code in slot_wh:
			frappe.db.set_value("Work Order Item", wi.name,
			                    {"required_qty": actual[wi.item_code],
			                     "source_warehouse": slot_wh[wi.item_code]},
			                    update_modified=False)
	for r in (run.materials or []):
		if r.item_code in actual and r.item_code in slot_wh:
			r.db_set("actual_qty", actual[r.item_code], update_modified=False)


# ------------------------------------------------------------------ dashboard reads

@frappe.whitelist()
def get_my_release_slots():
	"""Pending slots the session user may release (recipient of the category), for
	the Production Logging 'Release — Your Department' card."""
	from trustbit_ethanol.ts_gate_entry import ts_production_dept as dept
	user_cats = set(dept._user_categories())
	if dept._is_admin():
		user_cats = None  # admin sees all
	slots = frappe.get_all(SLOT_DOCTYPE,
	                       filters={"status": "Pending"},
	                       fields=["name", "production_entry", "category", "department",
	                               "notified_at"], order_by="creation asc", limit=50)
	out = []
	for s in slots:
		if user_cats is not None and s.category not in user_cats:
			continue
		s["items"] = frappe.get_all("TS Production Release Slot Item",
		                            filters={"parent": s.name},
		                            fields=["item_code", "item_name", "warehouse",
		                                    "planned_qty", "uom"], order_by="idx")
		s["notified_at"] = str(s.notified_at or "")
		out.append(s)
	return out


def slots_for_run(run_name):
	"""Slot status list for the Store Manager release zone + the status board."""
	return frappe.get_all(SLOT_DOCTYPE, filters={"production_entry": run_name},
	                      fields=["name", "category", "department", "status",
	                              "released_by"], order_by="category")
