"""Department consumption entries — Phase C of the Multi-BOM production plan
(see memory/project_production_multi_flow.md).

Reporting-only: a department user (a configured recipient of a TS Production BOM
Category) logs the raw material their department consumed for a production run.
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
SETTING_ENABLED = "ts_production_dept_entry_enabled"

_ADMIN_ROLES = {"System Manager", "IT Head"}
_PM_ROLES = {"Manufacturing Manager", "Manufacturing User", "PM", "Grain PM"}


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
	"""For the Production Logging page: the session user's categories + the
	production runs still awaiting THEIR department's consumption log."""
	enabled = _is_enabled()
	cats = _user_categories() if enabled else []
	pm_like = _is_admin() or bool(_PM_ROLES & set(frappe.get_roles()))
	if enabled and not cats and pm_like:
		# PMs/admins see all reporting-only categories (oversight view).
		cats = frappe.get_all(
			"TS Production BOM Category",
			filters={"active": 1, "is_production": 0}, pluck="name", limit=0)
	pending = _pending_runs(cats) if cats else []
	return {"enabled": enabled, "categories": cats, "pending": pending}


def _pending_runs(categories):
	"""Runs whose BOM is the main_bom of an active connector carrying a dept line
	for one of `categories`, not yet logged (non-cancelled) for that category."""
	lines = frappe.db.sql("""
		SELECT l.parent AS connector, l.bom AS dept_bom, l.category,
		       c.main_bom, cat.department
		FROM `tabTS BOM Connector Line` l
		JOIN `tabTS BOM Connector` c ON c.name = l.parent AND c.active = 1
		JOIN `tabTS Production BOM Category` cat ON cat.name = l.category
		WHERE l.category IN %(cats)s
	""", {"cats": tuple(categories)}, as_dict=True)
	if not lines:
		return []
	by_main = {}
	for ln in lines:
		by_main.setdefault(ln.main_bom, []).append(ln)

	runs = frappe.get_all(
		"TS Production Entry",
		filters={"bom": ["in", list(by_main)],
		         "ts_variance_status": ["in", ["Released", "Completed"]]},
		fields=["name", "bom", "production_item_name", "actual_produced_qty",
		        "production_uom", "posting_date", "ts_variance_status"],
		order_by="modified desc", limit=30)

	out = []
	for run in runs:
		for ln in by_main.get(run.bom, []):
			logged = frappe.db.exists(DOCTYPE, {
				"production_entry": run.name, "category": ln.category,
				"status": ["!=", "Cancelled"]})
			if logged:
				continue
			out.append({
				"production_entry": run.name,
				"item_name": run.production_item_name,
				"produced_qty": run.actual_produced_qty,
				"uom": run.production_uom,
				"posting_date": str(run.posting_date or ""),
				"status": run.ts_variance_status,
				"connector": ln.connector,
				"category": ln.category,
				"department": ln.department,
				"dept_bom": ln.dept_bom,
				"materials": _dept_bom_materials(ln.dept_bom, run.name),
			})
	return out


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

@frappe.whitelist(methods=["POST"])
def submit_department_entry(production_entry, bom, category, materials,
                            posting_date=None, shift=None, remark=None):
	"""Create + log a department consumption entry in one step (the dialog UX).
	Auth = recipient gate (see module docstring). REPORTING ONLY — no stock."""
	_require_enabled()
	_recipient_gate(category)

	if isinstance(materials, str):
		materials = json.loads(materials or "[]")
	rows = [m for m in (materials or []) if flt(m.get("qty")) > 0]
	if not rows:
		frappe.throw(_("Enter a quantity for at least one material."))

	doc = frappe.get_doc({
		"doctype": DOCTYPE,
		"production_entry": production_entry,
		"bom": bom,
		"category": category,
		"posting_date": posting_date or nowdate(),
		"shift": shift,
		"remark": remark,
		"materials": [{
			"item_code": m.get("item_code"),
			"std_qty": flt(m.get("std_qty")),
			"qty": flt(m.get("qty")),
			"uom": m.get("uom"),
			"remark": (m.get("remark") or "")[:140],
		} for m in rows],
	})
	# Recipient gate above IS the authorization (Lesson 224 pairing documented
	# in the module docstring) — dept users hold no create perm on the doctype.
	doc.insert(ignore_permissions=True)

	user = frappe.session.user
	doc.db_set("submitted_by", user, update_modified=False)
	doc.db_set("logged_at", now_datetime(), update_modified=False)
	doc.db_set("status", "Logged", update_modified=False)  # status LAST (L288)
	doc.add_comment("Comment", _(
		"Department consumption logged by {0} for category {1} (reporting only)."
	).format(user, category))
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "status": "Logged"}


@frappe.whitelist(methods=["POST"])
def cancel_department_entry(name, reason):
	"""Creator or admin cancels a Logged entry (reporting correction)."""
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
	doc.db_set("status", "Cancelled", update_modified=False)
	doc.add_comment("Comment", _(
		"Cancelled by {0}. Reason: {1}").format(user, frappe.utils.escape_html(reason)))
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "status": "Cancelled"}
