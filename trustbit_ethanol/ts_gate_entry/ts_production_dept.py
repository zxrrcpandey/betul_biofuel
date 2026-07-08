"""Department production entries — Phase C of the Multi-BOM production plan,
upgraded by the Department Actual Production rework (4 Jul 2026 —
PLAN_dept_production_rework.md, decisions U1-U7): department participation is
now a MANDATORY GATE on the Multiple flow. At PM submit, one 'Pending' entry is
system-created per connector department line; the department "Adds Production"
(fills its actual BOM quantities) via submit_department_production; when the
last entry leaves 'Pending' (Logged or Administrator-Skipped), the run's release
Material Request is created and the Store Managers are notified
(ts_production_multi.check_all_departments_complete).

Still ACTUALS-ONLY (U2): a department user (a configured recipient of a TS
Production BOM Category) logs the raw material their department consumed.
NO stock is ever moved — there is structurally no Stock Entry code path here.

Authorization model: the recipient configuration on the category IS the auth
boundary (data-driven — Lesson 221): the session user must be a direct `user`
recipient or hold a recipient `role` of the entry's category (PM / Manufacturing
Manager / admin also pass). Inserts use ignore_permissions AFTER that explicit
gate (Lesson 224 pairing — the gate is the permission check; DocType write perms
alone can't express "recipients of this category").

Discipline (mirrors ts_production_release.py):
  - every mutation is @frappe.whitelist(methods=["POST"]) (Lesson 175)
  - kill switch ts_production_dept_entry_enabled, fail-closed (Lesson 171/172)
  - control-plane fields flipped via db_set (tamper-guarded on the doctype)
  - status set LAST (Lesson 288); comments record who/when
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, nowdate

DOCTYPE = "TS Production Department Entry"
RUN_DOCTYPE = "TS Production Entry"
SETTING_ENABLED = "ts_production_dept_entry_enabled"

_ADMIN_ROLES = {"System Manager", "IT Head"}
_PM_ROLES = {"Manufacturing Manager", "Manufacturing User", "PM", "Grain PM"}
# get_multi_run_board viewers (R3): initiator roles + oversight + the release gate.
_BOARD_ROLES = {"PM", "Grain PM", "Manufacturing Manager", "Manufacturing User",
                "CEO", "MD", "Stores Manager", "System Manager", "IT Head"}


# ---------------------------------------------------------------- gates

def _is_enabled():
	try:
		return cint(frappe.db.get_single_value("TS Settings", SETTING_ENABLED)) == 1
	except Exception:
		return False  # fail-closed (Lesson 171/172)


def _require_enabled():
	if not _is_enabled():
		frappe.throw(_(
			"Department consumption entries are currently disabled "
			"(kill switch in TS Settings)."))


def _is_admin(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(_ADMIN_ROLES & set(frappe.get_roles(user)))


def _user_categories(user=None):
	"""All ACTIVE reporting-only categories the user is a recipient of —
	direct user match OR holds a recipient role (data-driven, Lesson 221)."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	rows = frappe.db.sql("""
		SELECT DISTINCT r.parent
		FROM `tabTS Production Notify Recipient` r
		JOIN `tabTS Production BOM Category` c ON c.name = r.parent
		WHERE r.parenttype = 'TS Production BOM Category'
		  AND r.active = 1 AND c.active = 1 AND IFNULL(c.is_production, 0) = 0
		  AND (r.user = %(user)s OR (IFNULL(r.role, '') != '' AND r.role IN %(roles)s))
	""", {"user": user, "roles": tuple(roles) or ("",)})
	return [r[0] for r in rows]


def _recipient_gate(category):
	"""The session user must be a recipient of `category`, or PM/MM/admin."""
	user = frappe.session.user
	if _is_admin(user):
		return
	if _PM_ROLES & set(frappe.get_roles(user)):
		return
	if category in _user_categories(user):
		return
	frappe.throw(
		_("You are not a configured recipient of category {0}.").format(category),
		frappe.PermissionError)


# ---------------------------------------------------------------- read-only

@frappe.whitelist()
def get_department_context():
	"""For the Production Logging page: the session user's categories + their
	system-created 'Pending' department entries awaiting Add Production (v2.21 —
	Pending-entry-driven; the old run-scan predicate is gone)."""
	enabled = _is_enabled()
	cats = _user_categories() if enabled else []
	pm_like = _is_admin() or bool(_PM_ROLES & set(frappe.get_roles()))
	if enabled and not cats and pm_like:
		# PMs/admins see all reporting-only categories (oversight view).
		cats = frappe.get_all(
			"TS Production BOM Category",
			filters={"active": 1, "is_production": 0}, pluck="name", limit=0)
	pending = _pending_entries(cats) if cats else []
	return {"enabled": enabled, "categories": cats, "pending": pending}


def _pending_entries(categories):
	"""The user's Pending gate entries, joined to their (still-waiting) runs."""
	rows = frappe.db.sql("""
		SELECT d.name AS dept_entry, d.production_entry, d.bom AS dept_bom,
		       d.category, d.department, d.notified_at, d.bom_connector AS connector,
		       r.production_item_name AS item_name, r.actual_produced_qty AS produced_qty,
		       r.production_uom AS uom, r.posting_date, r.ts_variance_status AS status
		FROM `tabTS Production Department Entry` d
		JOIN `tabTS Production Entry` r ON r.name = d.production_entry
		WHERE d.status = 'Pending' AND d.category IN %(cats)s
		  AND r.ts_variance_status = 'Awaiting Department Production'
		ORDER BY d.notified_at ASC
		LIMIT 50
	""", {"cats": tuple(categories)}, as_dict=True)
	for row in rows:
		row["posting_date"] = str(row.posting_date or "")
		row["notified_at"] = str(row.notified_at or "")
		row["materials"] = _dept_bom_materials(row.dept_bom, row.production_entry)
	return rows


def _dept_bom_materials(dept_bom, production_entry):
	"""The dept BOM's material lines as dialog prefill.

	Std qty scales to the run's produced qty ONLY when the dept BOM's output item
	IS the run's production item (e.g. the WTP list is defined per RS batch).
	A dept BOM with its OWN output basis (e.g. Boiler Steam 515 MT) cannot be
	scaled by RS produced qty — its standards are shown as-is (client BOM_BBPL
	workbook, 2 Jul: Boiler BOM output = Steam, not RS)."""
	bom = frappe.db.get_value("BOM", dept_bom, ["quantity", "item"], as_dict=True)
	run = frappe.db.get_value(
		"TS Production Entry", production_entry,
		["actual_produced_qty", "production_item"], as_dict=True) or {}
	same_basis = bom and run and bom.item == run.get("production_item")
	produced = flt(run.get("actual_produced_qty"))
	scale = (produced / flt(bom.quantity)) if (same_basis and flt(bom.quantity)) else 1.0
	items = frappe.get_all(
		"BOM Item", filters={"parent": dept_bom},
		fields=["item_code", "item_name", "qty", "stock_uom"],
		order_by="idx", limit=0)
	return [{
		"item_code": i.item_code,
		"item_name": i.item_name,
		"std_qty": round(flt(i.qty) * scale, 3),
		"uom": i.stock_uom,
	} for i in items]


# ---------------------------------------------------------------- mutations

def _parse_material_rows(materials):
	if isinstance(materials, str):
		materials = json.loads(materials or "[]")
	rows = [m for m in (materials or []) if flt(m.get("qty")) > 0]
	if not rows:
		frappe.throw(_("Enter a quantity for at least one material."))
	return [{
		"item_code": m.get("item_code"),
		"std_qty": flt(m.get("std_qty")),
		"qty": flt(m.get("qty")),
		"uom": m.get("uom"),
		"remark": (m.get("remark") or "")[:140],
	} for m in rows]


@frappe.whitelist(methods=["POST"])
def submit_department_production(name, materials, posting_date=None, shift=None,
                                 remark=None):
	"""v2.21 'Add Production': the department fills its ACTUAL BOM quantities on
	its system-created 'Pending' gate entry -> Logged. When this was the last
	Pending entry of the run, the release MR is created + Store Managers notified
	(atomic trigger — runs in THIS transaction, so an MR failure rolls the Logged
	flip back too and the entry stays Pending)."""
	_require_enabled()
	doc = frappe.get_doc(DOCTYPE, name)
	_recipient_gate(doc.category)
	if doc.status != "Pending":
		frappe.throw(_("Only a Pending department entry can be logged "
		               "(current: {0}).").format(doc.status))

	doc.set("materials", _parse_material_rows(materials))
	if posting_date:
		doc.posting_date = posting_date
	if shift:
		doc.shift = shift
	if remark:
		doc.remark = remark
	doc.flags.ignore_permissions = True  # recipient gate above IS the authorization (L224 pairing)
	doc.save()

	user = frappe.session.user
	doc.db_set("submitted_by", user, update_modified=False)
	doc.db_set("logged_at", now_datetime(), update_modified=False)
	doc.db_set("status", "Logged", update_modified=False)  # status LAST (L288)
	doc.add_comment("Comment", _(
		"Department production added by {0} for category {1} (actuals-only)."
	).format(user, doc.category))

	from trustbit_ethanol.ts_gate_entry.ts_production_multi import (
		check_all_departments_complete,
	)
	trigger = check_all_departments_complete(doc.production_entry)
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "status": "Logged",
	        "mr_created": bool(trigger.get("mr_created")),
	        "material_request": trigger.get("material_request")}


@frappe.whitelist(methods=["POST"])
def admin_skip_department(name, reason):
	"""Administrator-ONLY unbrick valve (U3): skip a non-responsive department.
	Skipped satisfies the gate; the all-complete trigger re-runs immediately."""
	_require_enabled()
	if frappe.session.user != "Administrator":
		frappe.throw(_("Only Administrator may skip a department production entry."),
		             frappe.PermissionError)
	doc = frappe.get_doc(DOCTYPE, name)
	if doc.status != "Pending":
		frappe.throw(_("Only a Pending department entry can be skipped "
		               "(current: {0}).").format(doc.status))
	if not (reason or "").strip() or len(reason.strip()) < 10:
		frappe.throw(_("Please give a skip reason (at least 10 characters)."))
	doc.db_set("submitted_by", frappe.session.user, update_modified=False)
	doc.db_set("logged_at", now_datetime(), update_modified=False)
	doc.db_set("status", "Skipped", update_modified=False)  # status LAST (L288)
	doc.add_comment("Comment", _(
		"SKIPPED by Administrator. Reason: {0}").format(frappe.utils.escape_html(reason)))

	from trustbit_ethanol.ts_gate_entry.ts_production_multi import (
		check_all_departments_complete,
	)
	trigger = check_all_departments_complete(doc.production_entry)
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "status": "Skipped",
	        "mr_created": bool(trigger.get("mr_created")),
	        "material_request": trigger.get("material_request")}


@frappe.whitelist(methods=["POST"])
def submit_department_entry(production_entry, bom, category, materials,
                            posting_date=None, shift=None, remark=None):
	"""LEGACY ad-hoc create+log (standalone logs for runs WITHOUT a gate).
	Gating runs must use their system-created Pending entry — this endpoint
	redirects there so duplicates can't muddy the gate (DEC-08)."""
	_require_enabled()
	_recipient_gate(category)

	gate_entry = frappe.db.get_value(DOCTYPE, {
		"production_entry": production_entry, "category": category,
		"status": "Pending"})
	if gate_entry:
		frappe.throw(_(
			"Run {0} has a system-created Pending entry for category {1} ({2}) — "
			"add your production there instead."
		).format(production_entry, category, gate_entry))

	rows = _parse_material_rows(materials)
	doc = frappe.get_doc({
		"doctype": DOCTYPE,
		"production_entry": production_entry,
		"bom": bom,
		"category": category,
		"posting_date": posting_date or nowdate(),
		"shift": shift,
		"remark": remark,
		"materials": rows,
	})
	# Recipient gate above IS the authorization (Lesson 224 pairing documented
	# in the module docstring) — dept users hold no create perm on the doctype.
	doc.insert(ignore_permissions=True)

	user = frappe.session.user
	doc.db_set("submitted_by", user, update_modified=False)
	doc.db_set("logged_at", now_datetime(), update_modified=False)
	doc.db_set("status", "Logged", update_modified=False)  # status LAST (L288)
	doc.add_comment("Comment", _(
		"Department consumption logged by {0} for category {1} (standalone)."
	).format(user, category))
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "status": "Logged"}


@frappe.whitelist(methods=["POST"])
def cancel_department_entry(name, reason):
	"""Logged-entry correction. v2.21 REDO semantics for gate entries: while the
	run is still 'Awaiting Department Production' (no MR yet), a Logged gate
	entry reverts to Pending for re-entry (notified_at re-stamped). Once the MR
	exists the gate is spent — corrections are blocked. Non-gate (standalone /
	legacy) entries keep the old Logged -> Cancelled behavior."""
	_require_enabled()
	doc = frappe.get_doc(DOCTYPE, name)
	user = frappe.session.user
	if not (_is_admin(user) or doc.submitted_by == user or doc.owner == user):
		frappe.throw(_("Only the person who logged this entry (or an admin) "
		               "may cancel it."), frappe.PermissionError)
	if doc.status != "Logged":
		frappe.throw(_("Only a Logged entry can be cancelled (current: {0})."
		               ).format(doc.status))
	if not (reason or "").strip() or len(reason.strip()) < 10:
		frappe.throw(_("Please give a cancellation reason (at least 10 characters)."))

	run = frappe.db.get_value(RUN_DOCTYPE, doc.production_entry,
	                          ["ts_variance_status", "material_request"], as_dict=True)
	# A GATE entry (system-created at Multiple-flow submit — identified by its
	# notified_at stamp) may only be reopened while the run still waits and no
	# MR exists; once the run moved on, the gate is SPENT — never Cancelled
	# either (it is the record that justified the release MR).
	if doc.notified_at:
		if (run and run.ts_variance_status == "Awaiting Department Production"
				and not run.material_request):
			doc.db_set("status", "Pending", update_modified=False)
			doc.db_set("submitted_by", None, update_modified=False)
			doc.db_set("logged_at", None, update_modified=False)
			doc.db_set("notified_at", now_datetime(), update_modified=False)
			doc.add_comment("Comment", _(
				"Reopened (Logged -> Pending) by {0}. Reason: {1}"
			).format(user, frappe.utils.escape_html(reason)))
			frappe.db.commit()
			return {"ok": True, "name": doc.name, "status": "Pending"}
		frappe.throw(_("The release Material Request already exists (or the run "
		               "has moved on) — this gate entry can no longer be reopened "
		               "or cancelled."))

	doc.db_set("status", "Cancelled", update_modified=False)
	doc.add_comment("Comment", _(
		"Cancelled by {0}. Reason: {1}").format(user, frappe.utils.escape_html(reason)))
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "status": "Cancelled"}


# ---------------------------------------------------------------- status board

@frappe.whitelist()
def get_multi_run_board(production_entry=None):
	"""R3 visibility: per-run department tick list for PM / Grain PM / CEO / MD /
	Store Manager (+ Manufacturing roles, IT Head, System Manager). Read-only;
	bounded payload (active runs + last-7-days finished, LIMIT 50)."""
	user = frappe.session.user
	if user != "Administrator" and not (_BOARD_ROLES & set(frappe.get_roles(user))):
		frappe.throw(_("You are not permitted to view the department production "
		               "status board."), frappe.PermissionError)
	if not _is_enabled():
		return {"enabled": False, "runs": []}

	filters = {"flow_type": "Multiple"}
	if production_entry:
		filters["name"] = production_entry
	else:
		filters["ts_variance_status"] = ["in", [
			"Awaiting Department Production", "Pending Material Request",
			"Awaiting Distribution", "Completed"]]
		filters["modified"] = [">", frappe.utils.add_days(None, -7)]
	runs = frappe.get_all(
		RUN_DOCTYPE, filters=filters,
		fields=["name", "bom_connector", "production_item_name",
		        "actual_produced_qty", "production_uom", "posting_date",
		        "ts_variance_status", "material_request", "owner"],
		order_by="modified desc", limit=50)
	if not runs:
		return {"enabled": True, "runs": []}

	slots = frappe.get_all(
		DOCTYPE,
		filters={"production_entry": ["in", [r.name for r in runs]]},
		fields=["name", "production_entry", "category", "department", "bom",
		        "status", "submitted_by", "logged_at", "notified_at"],
		order_by="creation asc", limit=0)
	by_run = {}
	for s in slots:
		s["logged_at"] = str(s.logged_at or "")
		s["notified_at"] = str(s.notified_at or "")
		by_run.setdefault(s.production_entry, []).append(s)

	out = []
	for r in runs:
		dept = by_run.get(r.name, [])
		if not dept and r.ts_variance_status != "Awaiting Department Production":
			continue  # pre-rework runs without gate entries stay off the board
		r["posting_date"] = str(r.posting_date or "")
		r["departments"] = dept
		r["pending_count"] = sum(1 for s in dept if s.status == "Pending")
		out.append(r)
	return {"enabled": True, "runs": out,
	        "is_admin_user": user == "Administrator"}
