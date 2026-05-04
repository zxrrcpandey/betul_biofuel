# Copyright (c) 2026 Trustbit Technologies. All rights reserved.
"""v2.9.11 — Shared Connections panel module.

Central dispatcher for the Related Documents panel on:
  - TS Deduction Sheet (v2.9.10 origin — refactored to use this module)
  - Material Request
  - Purchase Order
  - Purchase Receipt
  - TS Quality Inspection
  - Purchase Invoice

Two-layer permission gating:
  1. Source doc read-permission (throws if denied) — caller-side
  2. Per-row read check on each related doc — silently rendered as
     "🔒 No permission" row instead of being omitted (v2.9.11 user
     decision: transparency over privacy for this internal app)

Cap-at-10 per doctype with `_total` count surfaced for the JS "+ N more"
dialog opener.

Lessons applied:
  - 162: never bypass tamper guards (we don't write — read-only)
  - 168: per-row read check; doctype meta cache requires Lesson 234 flush
  - 175: GET endpoint, no CSRF concern
  - 237: frappe.log_error uses kwargs (title=, message=)
  - 238: notification side-effects wrapped in try/except (we don't notify)
"""

import frappe
from frappe import _

# ────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ────────────────────────────────────────────────────────────────────────

ALLOWED_DOCTYPES = (
	"TS Deduction Sheet",
	"Material Request",
	"Purchase Order",
	"Purchase Receipt",
	"TS Quality Inspection",
	"Purchase Invoice",
)


@frappe.whitelist()
def get_connections(doctype, name):
	"""Central dispatcher — called from ts_connections.js for all 6 doctypes.

	Returns:
	  {"sections": [
	    {"label": "...", "icon": "🚛", "items": [
	      {"doctype": "...", "name": "...", "status": "...",
	       "docstatus": 0|1|2|-1, "url": "...", "no_permission": bool,
	       "non_clickable": bool},
	    ], "total_count": int, "all_names": [...]},
	    ...
	  ]}
	"""
	if doctype not in ALLOWED_DOCTYPES:
		return {"sections": [], "error": "unsupported_doctype"}

	if not frappe.has_permission(doctype, "read", doc=name, throw=False):
		return {"sections": [], "error": "no_permission"}

	builders = {
		"TS Deduction Sheet": _build_for_ds,
		"Material Request": _build_for_mr,
		"Purchase Order": _build_for_po,
		"Purchase Receipt": _build_for_pr,
		"TS Quality Inspection": _build_for_qi,
		"Purchase Invoice": _build_for_pi,
	}
	try:
		return builders[doctype](name)
	except Exception as e:
		try:
			frappe.log_error(
				title="v2.9.11 get_connections",
				message=f"{doctype} {name}: {e}",
			)
		except Exception:
			pass
		return {"sections": [], "error": "internal"}


# ────────────────────────────────────────────────────────────────────────
#  SHARED HELPERS
# ────────────────────────────────────────────────────────────────────────

CAP = 10  # show first 10 per doctype, surface +N more for dialog


def _safe(fn):
	"""Section-builder wrapper. Returns [] on any error (logged)."""
	try:
		return fn() or []
	except Exception as e:
		try:
			frappe.log_error(
				title="v2.9.11 builder failed",
				message=f"{fn.__name__}: {e}",
			)
		except Exception:
			pass
		return []


def _doc(doctype, name, status_field=None):
	"""Build a single connection row dict. Returns None for empty input,
	a 'no permission' stub when user can't read, or a full row otherwise.

	Permission-denied row (v2.9.11 — show greyed instead of omit):
	  {"doctype": "...", "name": "...", "status": "🔒 No permission",
	   "docstatus": -1, "url": None, "no_permission": True,
	   "non_clickable": True}
	"""
	if not name:
		return None
	if not frappe.has_permission(doctype, "read", doc=name, throw=False):
		return {
			"doctype": doctype,
			"name": name,
			"status": "🔒 No permission",
			"docstatus": -1,
			"url": None,
			"no_permission": True,
			"non_clickable": True,
		}
	try:
		fields = ["docstatus"]
		if status_field:
			fields.append(status_field)
		row = frappe.db.get_value(doctype, name, fields, as_dict=True)
		if not row:
			return None
		ds = int(row.docstatus or 0)
		ds_label = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(ds, "")
		status = row.get(status_field) if status_field else ds_label
		if ds == 2:
			status = "Cancelled"
		return {
			"doctype": doctype,
			"name": name,
			"status": status or ds_label,
			"docstatus": ds,
			"url": "/app/" + doctype.lower().replace(" ", "-") + "/" + name,
		}
	except Exception:
		return None


def _section(label, icon, items_or_none, all_names=None):
	"""Wrap items into a section with cap-at-10 + total_count."""
	items = items_or_none or []
	# Drop None/falsy
	items = [it for it in items if it]
	total = len(all_names) if all_names is not None else len(items)
	# Sort: docstatus DESC (Submitted first) → docstatus 0 (Draft) before -1 (no_perm)
	# Cancelled (docstatus=2) goes last
	def _sort_key(it):
		ds = it.get("docstatus", 0)
		# Order: 1 (submitted) > 0 (draft) > -1 (no perm) > 2 (cancelled)
		order = {1: 0, 0: 1, -1: 2, 2: 3}
		return (order.get(ds, 9), it.get("name") or "")
	items.sort(key=_sort_key)
	# Cap at CAP for inline display; all_names retains full list for dialog
	visible = items[:CAP]
	return {
		"label": label,
		"icon": icon,
		"items": visible,
		"total_count": total,
		"has_more": total > len(visible),
	}


def _resolve_doc_for(doctype, names, status_field=None):
	"""Map a list of names → list of doc-row dicts (None entries dropped)."""
	if not names:
		return []
	out = []
	seen = set()
	for n in names:
		if not n or n in seen:
			continue
		seen.add(n)
		d = _doc(doctype, n, status_field=status_field)
		if d:
			out.append(d)
	return out


def _qis_from_prs(pr_iterable):
	"""Resolve the set of TS Quality Inspections reachable from one or more
	Purchase Receipts. v2.9.11.1 — replaces the broken
	`WHERE TS QI.purchase_receipt = %s` pattern (QI has no such column).

	Three resolution paths (BBPL flow uses A + B; C is rarely populated):
	  A. PR.ts_token → QI.token_number   (BBPL grain GRN flow)
	  B. DS.grn_reference = PR → DS.quality_inspection   (deduction flow)
	  C. PR Item.quality_inspection   (standard ERPNext field)
	"""
	prs = [p for p in (pr_iterable or []) if p]
	if not prs:
		return []
	out = set()
	# Path A — Token-based
	try:
		rows = frappe.db.sql("""
			SELECT DISTINCT qi.name
			FROM `tabPurchase Receipt` pr
			JOIN `tabTS Quality Inspection` qi ON qi.token_number = pr.ts_token
			WHERE pr.name IN %(prs)s
			  AND pr.ts_token IS NOT NULL AND pr.ts_token != ''
		""", {"prs": tuple(prs)}) or []
		out.update(r[0] for r in rows)
	except Exception:
		pass
	# Path B — DS-based
	try:
		rows = frappe.db.sql("""
			SELECT DISTINCT quality_inspection FROM `tabTS Deduction Sheet`
			WHERE grn_reference IN %(prs)s
			  AND quality_inspection IS NOT NULL AND quality_inspection != ''
		""", {"prs": tuple(prs)}) or []
		out.update(r[0] for r in rows)
	except Exception:
		pass
	# Path C — standard PR Item field (defensive; usually empty on BBPL)
	try:
		rows = frappe.db.sql("""
			SELECT DISTINCT quality_inspection FROM `tabPurchase Receipt Item`
			WHERE parent IN %(prs)s
			  AND quality_inspection IS NOT NULL AND quality_inspection != ''
		""", {"prs": tuple(prs)}) or []
		out.update(r[0] for r in rows)
	except Exception:
		pass
	return list(out)


def _ds_suggestions_for_qis(qi_iterable):
	"""Resolve TS Deduction Suggestions reachable from one or more QIs via
	the TS Deduction Sheet back-ref. QI has no `deduction_suggestion`
	column — only the DS does.
	"""
	qis = [q for q in (qi_iterable or []) if q]
	if not qis:
		return []
	try:
		rows = frappe.db.sql("""
			SELECT DISTINCT deduction_suggestion FROM `tabTS Deduction Sheet`
			WHERE quality_inspection IN %(qis)s
			  AND deduction_suggestion IS NOT NULL AND deduction_suggestion != ''
		""", {"qis": tuple(qis)}) or []
		return [r[0] for r in rows]
	except Exception:
		return []


# ────────────────────────────────────────────────────────────────────────
#  PER-DOCTYPE BUILDERS
# ────────────────────────────────────────────────────────────────────────

def _build_for_ds(name):
	"""TS Deduction Sheet — copy of v2.9.10 implementation, adapted to shared helpers."""
	ds = frappe.db.get_value("TS Deduction Sheet", name,
		["token_number", "gate_entry", "purchase_order", "quality_inspection",
		 "grn_reference", "deduction_suggestion"], as_dict=True)
	if not ds:
		return {"sections": []}

	# Source Chain
	def source_chain():
		return [
			_doc("TS Token", ds.token_number, status_field="status"),
			_doc("TS Gate Entry", ds.gate_entry),
		]

	# Procurement
	def procurement():
		mr_names = []
		if ds.purchase_order:
			mr_names = frappe.db.sql_list("""
				SELECT DISTINCT material_request FROM `tabPurchase Order Item`
				WHERE parent = %s AND material_request IS NOT NULL AND material_request != ''
			""", (ds.purchase_order,)) or []
		mrs = _resolve_doc_for("Material Request", mr_names[:CAP], status_field="status")
		po = _doc("Purchase Order", ds.purchase_order, status_field="status")
		return mrs + ([po] if po else [])

	# Receipt + Quality
	def receipt_quality():
		return [
			_doc("Purchase Receipt", ds.grn_reference, status_field="status"),
			_doc("TS Quality Inspection", ds.quality_inspection),
			_doc("TS Deduction Suggestion", ds.deduction_suggestion, status_field="status"),
		]

	# Settlement
	def settlement():
		pi_names = []
		if ds.grn_reference:
			pi_names = frappe.db.sql_list("""
				SELECT DISTINCT parent FROM `tabPurchase Invoice Item`
				WHERE purchase_receipt = %s
			""", (ds.grn_reference,)) or []
		pis = _resolve_doc_for("Purchase Invoice", pi_names[:CAP], status_field="status")
		se_set = set()
		if ds.grn_reference:
			try:
				se_via_pr = frappe.db.sql_list("""
					SELECT name FROM `tabStock Entry`
					WHERE purchase_receipt_no = %s AND docstatus < 2
				""", (ds.grn_reference,)) or []
				se_set.update(se_via_pr)
			except Exception:
				pass
		if ds.purchase_order:
			mr_names = frappe.db.sql_list("""
				SELECT DISTINCT material_request FROM `tabPurchase Order Item`
				WHERE parent = %s AND material_request IS NOT NULL AND material_request != ''
			""", (ds.purchase_order,)) or []
			for mr_name in mr_names:
				se_via_mr = frappe.db.sql_list("""
					SELECT DISTINCT parent FROM `tabStock Entry Detail`
					WHERE material_request = %s
				""", (mr_name,)) or []
				se_set.update(se_via_mr)
		ses = _resolve_doc_for("Stock Entry", sorted(se_set, reverse=True)[:CAP])
		return pis + ses

	return {"sections": [
		_section("Source Chain", "🚛", _safe(source_chain)),
		_section("Procurement", "📋", _safe(procurement)),
		_section("Receipt + Quality", "📦", _safe(receipt_quality)),
		_section("Settlement", "💰", _safe(settlement)),
	]}


def _build_for_mr(name):
	"""Material Request — itself, plus connected POs/PRs/PIs/SEs/QIs."""

	def source_chain():
		# MR doesn't have direct Token. Try via linked PO's Gate Entry.
		po_names = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabPurchase Order Item`
			WHERE material_request = %s
		""", (name,)) or []
		token_set = set()
		ge_set = set()
		for po in po_names:
			try:
				rows = frappe.db.sql("""
					SELECT token_number, name FROM `tabTS Gate Entry`
					WHERE purchase_order = %s
				""", (po,), as_dict=True) or []
				for r in rows:
					if r.token_number: token_set.add(r.token_number)
					if r.name: ge_set.add(r.name)
			except Exception:
				pass
		tokens = _resolve_doc_for("TS Token", list(token_set)[:CAP], status_field="status")
		ges = _resolve_doc_for("TS Gate Entry", list(ge_set)[:CAP])
		return tokens + ges

	def procurement():
		po_names = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabPurchase Order Item`
			WHERE material_request = %s
		""", (name,)) or []
		pos = _resolve_doc_for("Purchase Order", po_names[:CAP], status_field="status")
		return pos

	def receipt_quality():
		pr_names = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabPurchase Receipt Item`
			WHERE material_request = %s
		""", (name,)) or []
		prs = _resolve_doc_for("Purchase Receipt", pr_names[:CAP], status_field="status")
		qi_names = _qis_from_prs(pr_names)
		qis = _resolve_doc_for("TS Quality Inspection", qi_names[:CAP])
		return prs + qis

	def settlement():
		pi_names = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabPurchase Invoice Item`
			WHERE material_request = %s
		""", (name,)) or []
		pis = _resolve_doc_for("Purchase Invoice", pi_names[:CAP], status_field="status")
		se_names = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabStock Entry Detail`
			WHERE material_request = %s
		""", (name,)) or []
		ses = _resolve_doc_for("Stock Entry", se_names[:CAP])
		return pis + ses

	return {"sections": [
		_section("Source Chain", "🚛", _safe(source_chain)),
		_section("Procurement", "📋", _safe(procurement)),
		_section("Receipt + Quality", "📦", _safe(receipt_quality)),
		_section("Settlement", "💰", _safe(settlement)),
	]}


def _build_for_po(name):
	"""Purchase Order — Token via GE, MRs via items, PRs/QIs/PIs via PR chain."""

	def source_chain():
		try:
			rows = frappe.db.sql("""
				SELECT token_number, name FROM `tabTS Gate Entry`
				WHERE purchase_order = %s
			""", (name,), as_dict=True) or []
		except Exception:
			rows = []
		token_set = set()
		ge_set = set()
		for r in rows:
			if r.token_number: token_set.add(r.token_number)
			if r.name: ge_set.add(r.name)
		tokens = _resolve_doc_for("TS Token", list(token_set)[:CAP], status_field="status")
		ges = _resolve_doc_for("TS Gate Entry", list(ge_set)[:CAP])
		return tokens + ges

	def procurement():
		mr_names = frappe.db.sql_list("""
			SELECT DISTINCT material_request FROM `tabPurchase Order Item`
			WHERE parent = %s AND material_request IS NOT NULL AND material_request != ''
		""", (name,)) or []
		return _resolve_doc_for("Material Request", mr_names[:CAP], status_field="status")

	def receipt_quality():
		pr_names = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabPurchase Receipt Item`
			WHERE purchase_order = %s
		""", (name,)) or []
		prs = _resolve_doc_for("Purchase Receipt", pr_names[:CAP], status_field="status")
		qi_names = _qis_from_prs(pr_names)
		qis = _resolve_doc_for("TS Quality Inspection", qi_names[:CAP])
		return prs + qis

	def settlement():
		pi_names = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabPurchase Invoice Item`
			WHERE purchase_order = %s
		""", (name,)) or []
		pis = _resolve_doc_for("Purchase Invoice", pi_names[:CAP], status_field="status")
		# SE via MRs traversed from PO
		mr_names = frappe.db.sql_list("""
			SELECT DISTINCT material_request FROM `tabPurchase Order Item`
			WHERE parent = %s AND material_request IS NOT NULL AND material_request != ''
		""", (name,)) or []
		se_set = set()
		for mr_name in mr_names:
			try:
				se = frappe.db.sql_list("""
					SELECT DISTINCT parent FROM `tabStock Entry Detail`
					WHERE material_request = %s
				""", (mr_name,)) or []
				se_set.update(se)
			except Exception:
				pass
		ses = _resolve_doc_for("Stock Entry", sorted(se_set, reverse=True)[:CAP])
		return pis + ses

	return {"sections": [
		_section("Source Chain", "🚛", _safe(source_chain)),
		_section("Procurement", "📋", _safe(procurement)),
		_section("Receipt + Quality", "📦", _safe(receipt_quality)),
		_section("Settlement", "💰", _safe(settlement)),
	]}


def _build_for_pr(name):
	"""Purchase Receipt — Token + GE direct, MRs/POs from items, PIs/SEs from PI chain."""
	pr = frappe.db.get_value("Purchase Receipt", name,
		["ts_token", "ts_gate_entry"], as_dict=True) or {}

	def source_chain():
		return [
			_doc("TS Token", pr.get("ts_token"), status_field="status"),
			_doc("TS Gate Entry", pr.get("ts_gate_entry")),
		]

	def procurement():
		rows = frappe.db.sql("""
			SELECT DISTINCT material_request, purchase_order
			FROM `tabPurchase Receipt Item` WHERE parent = %s
		""", (name,), as_dict=True) or []
		mr_set = {r.material_request for r in rows if r.material_request}
		po_set = {r.purchase_order for r in rows if r.purchase_order}
		mrs = _resolve_doc_for("Material Request", list(mr_set)[:CAP], status_field="status")
		pos = _resolve_doc_for("Purchase Order", list(po_set)[:CAP], status_field="status")
		return mrs + pos

	def receipt_quality():
		qi_names = _qis_from_prs([name])
		qis = _resolve_doc_for("TS Quality Inspection", qi_names[:CAP])
		ds_names = frappe.db.sql_list("""
			SELECT name FROM `tabTS Deduction Sheet` WHERE grn_reference = %s
		""", (name,)) or []
		dss = _resolve_doc_for("TS Deduction Sheet", ds_names[:CAP])
		sugg_names = _ds_suggestions_for_qis(qi_names)
		suggs = _resolve_doc_for("TS Deduction Suggestion", sugg_names[:CAP],
			status_field="status")
		return qis + dss + suggs

	def settlement():
		pi_names = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabPurchase Invoice Item`
			WHERE purchase_receipt = %s
		""", (name,)) or []
		pis = _resolve_doc_for("Purchase Invoice", pi_names[:CAP], status_field="status")
		se_set = set()
		try:
			se_via_pr = frappe.db.sql_list("""
				SELECT name FROM `tabStock Entry`
				WHERE purchase_receipt_no = %s
			""", (name,)) or []
			se_set.update(se_via_pr)
		except Exception:
			pass
		ses = _resolve_doc_for("Stock Entry", sorted(se_set, reverse=True)[:CAP])
		return pis + ses

	return {"sections": [
		_section("Source Chain", "🚛", _safe(source_chain)),
		_section("Procurement", "📋", _safe(procurement)),
		_section("Receipt + Quality", "📦", _safe(receipt_quality)),
		_section("Settlement", "💰", _safe(settlement)),
	]}


def _build_for_qi(name):
	"""TS Quality Inspection — Token + GE direct; PR resolved via Token (or
	DS or PR Item.quality_inspection back-ref); MRs/POs from PR Items; PIs +
	Stock Entries from PR.

	QI itself does NOT have a `purchase_receipt` Link field. Resolution
	priority for PR(s):
	  1. TS Token.purchase_receipt (Token sets PR on grain GRN flow)
	  2. tabPurchase Receipt Item.quality_inspection back-reference
	  3. tabTS Deduction Sheet.ts_purchase_receipt where DS.quality_inspection = QI
	"""
	qi = frappe.db.get_value("TS Quality Inspection", name,
		["token_number", "gate_entry", "purchase_order"],
		as_dict=True) or {}

	pr_set = set()
	# 1. Via Token
	if qi.get("token_number"):
		pr_via_token = frappe.db.get_value("TS Token", qi.token_number, "purchase_receipt")
		if pr_via_token:
			pr_set.add(pr_via_token)
	# 2. Via PR Item back-ref
	try:
		pri_prs = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabPurchase Receipt Item`
			WHERE quality_inspection = %s
		""", (name,)) or []
		pr_set.update(pri_prs)
	except Exception:
		pass
	# 3. Via DS
	try:
		ds_pr_rows = frappe.db.sql("""
			SELECT ts_purchase_receipt FROM `tabTS Deduction Sheet`
			WHERE quality_inspection = %s AND ts_purchase_receipt IS NOT NULL
		""", (name,), as_dict=True) or []
		pr_set.update(r.ts_purchase_receipt for r in ds_pr_rows if r.ts_purchase_receipt)
	except Exception:
		pass

	pr_list = list(pr_set)

	def source_chain():
		return [
			_doc("TS Token", qi.get("token_number"), status_field="status"),
			_doc("TS Gate Entry", qi.get("gate_entry")),
		]

	def procurement():
		mr_set = set()
		po_set = set()
		# Direct PO link from QI
		if qi.get("purchase_order"):
			po_set.add(qi.purchase_order)
		# Indirect via PR Items
		if pr_list:
			rows = frappe.db.sql("""
				SELECT DISTINCT material_request, purchase_order
				FROM `tabPurchase Receipt Item`
				WHERE parent IN %(prs)s
			""", {"prs": tuple(pr_list)}, as_dict=True) or []
			mr_set = {r.material_request for r in rows if r.material_request}
			po_set.update(r.purchase_order for r in rows if r.purchase_order)
		mrs = _resolve_doc_for("Material Request", list(mr_set)[:CAP], status_field="status")
		pos = _resolve_doc_for("Purchase Order", list(po_set)[:CAP], status_field="status")
		return mrs + pos

	def receipt_quality():
		prs = _resolve_doc_for("Purchase Receipt", pr_list[:CAP], status_field="status")
		ds_names = []
		try:
			ds_names = frappe.db.sql_list("""
				SELECT name FROM `tabTS Deduction Sheet` WHERE quality_inspection = %s
			""", (name,)) or []
		except Exception:
			pass
		dss = _resolve_doc_for("TS Deduction Sheet", ds_names[:CAP])
		# Resolve TS Deduction Suggestion via DS back-ref (not a QI field).
		sugg_names = []
		try:
			sugg_names = frappe.db.sql_list("""
				SELECT DISTINCT deduction_suggestion FROM `tabTS Deduction Sheet`
				WHERE quality_inspection = %s AND deduction_suggestion IS NOT NULL
			""", (name,)) or []
		except Exception:
			pass
		suggs = _resolve_doc_for("TS Deduction Suggestion", sugg_names[:CAP],
			status_field="status")
		return prs + dss + suggs

	def settlement():
		if not pr_list:
			return []
		pi_names = frappe.db.sql_list("""
			SELECT DISTINCT parent FROM `tabPurchase Invoice Item`
			WHERE purchase_receipt IN %(prs)s
		""", {"prs": tuple(pr_list)}) or []
		pis = _resolve_doc_for("Purchase Invoice", pi_names[:CAP],
			status_field="status")
		se_set = set()
		try:
			se = frappe.db.sql_list("""
				SELECT name FROM `tabStock Entry`
				WHERE purchase_receipt_no IN %(prs)s
			""", {"prs": tuple(pr_list)}) or []
			se_set.update(se)
		except Exception:
			pass
		ses = _resolve_doc_for("Stock Entry", sorted(se_set, reverse=True)[:CAP])
		return pis + ses

	return {"sections": [
		_section("Source Chain", "🚛", _safe(source_chain)),
		_section("Procurement", "📋", _safe(procurement)),
		_section("Receipt + Quality", "📦", _safe(receipt_quality)),
		_section("Settlement", "💰", _safe(settlement)),
	]}


def _build_for_pi(name):
	"""Purchase Invoice — Token+GE via PR, MRs/POs/PRs from items, SEs from PR/MR chain."""
	# Get unique PRs referenced from PI items
	rows = frappe.db.sql("""
		SELECT DISTINCT material_request, purchase_order, purchase_receipt
		FROM `tabPurchase Invoice Item` WHERE parent = %s
	""", (name,), as_dict=True) or []
	mr_set = {r.material_request for r in rows if r.material_request}
	po_set = {r.purchase_order for r in rows if r.purchase_order}
	pr_set = {r.purchase_receipt for r in rows if r.purchase_receipt}

	def source_chain():
		token_set = set()
		ge_set = set()
		for pr in pr_set:
			try:
				row = frappe.db.get_value("Purchase Receipt", pr,
					["ts_token", "ts_gate_entry"], as_dict=True) or {}
				if row.get("ts_token"): token_set.add(row.ts_token)
				if row.get("ts_gate_entry"): ge_set.add(row.ts_gate_entry)
			except Exception:
				pass
		tokens = _resolve_doc_for("TS Token", list(token_set)[:CAP], status_field="status")
		ges = _resolve_doc_for("TS Gate Entry", list(ge_set)[:CAP])
		return tokens + ges

	def procurement():
		# v2.9.11.1 — extend MR/PO surfacing through the PR chain so PIs
		# with only `purchase_receipt` (no item-level PO/MR) still show
		# their upstream PO + MR.
		expanded_mr = set(mr_set)
		expanded_po = set(po_set)
		if pr_set:
			try:
				rows = frappe.db.sql("""
					SELECT DISTINCT material_request, purchase_order
					FROM `tabPurchase Receipt Item` WHERE parent IN %(prs)s
				""", {"prs": tuple(pr_set)}, as_dict=True) or []
				expanded_mr.update(r.material_request for r in rows if r.material_request)
				expanded_po.update(r.purchase_order for r in rows if r.purchase_order)
			except Exception:
				pass
		mrs = _resolve_doc_for("Material Request", list(expanded_mr)[:CAP],
			status_field="status")
		pos = _resolve_doc_for("Purchase Order", list(expanded_po)[:CAP],
			status_field="status")
		return mrs + pos

	def receipt_quality():
		prs = _resolve_doc_for("Purchase Receipt", list(pr_set)[:CAP],
			status_field="status")
		qi_names = _qis_from_prs(pr_set)
		qis = _resolve_doc_for("TS Quality Inspection", qi_names[:CAP])
		ds_set = set()
		for pr in pr_set:
			try:
				dss = frappe.db.sql_list("""
					SELECT name FROM `tabTS Deduction Sheet` WHERE grn_reference = %s
				""", (pr,)) or []
				ds_set.update(dss)
			except Exception:
				pass
		dss = _resolve_doc_for("TS Deduction Sheet", list(ds_set)[:CAP])
		return prs + qis + dss

	def settlement():
		se_set = set()
		for pr in pr_set:
			try:
				se = frappe.db.sql_list("""
					SELECT name FROM `tabStock Entry` WHERE purchase_receipt_no = %s
				""", (pr,)) or []
				se_set.update(se)
			except Exception:
				pass
		for mr_name in mr_set:
			try:
				se = frappe.db.sql_list("""
					SELECT DISTINCT parent FROM `tabStock Entry Detail`
					WHERE material_request = %s
				""", (mr_name,)) or []
				se_set.update(se)
			except Exception:
				pass
		return _resolve_doc_for("Stock Entry", sorted(se_set, reverse=True)[:CAP])

	return {"sections": [
		_section("Source Chain", "🚛", _safe(source_chain)),
		_section("Procurement", "📋", _safe(procurement)),
		_section("Receipt + Quality", "📦", _safe(receipt_quality)),
		_section("Settlement", "💰", _safe(settlement)),
	]}
