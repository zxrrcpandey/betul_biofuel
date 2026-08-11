# TS Lead Time Analysis — BBPL Report Updated 11 Aug 2026.xlsx, sheet
# "Lead Time Analysis" (Wave 3 #7; plan: PLAN_bbpl_wave3_reports.md)
#
# ONE ROW PER (submitted MR Item child row) x (distinct doc-level chain path).
# A chain path is a DISTINCT (po, pr, pi) tuple:
#   MR -> PO  via `PO Item.material_request`   (doc-level; line-level
#             material_request_item is unreliable and never read)
#   PO -> PR  via `PR Item.purchase_order`
#   PR -> PI  via `PI Item.purchase_receipt`   (PR-preferred and PR-exclusive:
#             pairing an invoice through its receipt is the only way column
#             "PR to PI days" is well-defined)
#   PO -> PI  via `PI Item.purchase_order`     (fallback ONLY when the path has
#             no PR at all — branches disjoint by construction)
# Multi-item MRs render every item row against every chain path of the MR
# (deliberate: doc-level links cannot say which PO covered which item; all
# numeric columns carry disable_total). An MR with no PO renders one row per
# item with all chain columns blank — rows are never dropped for missing legs.
# Day-deltas are None (blank) whenever a leg is missing — never 0.
#
# Universe: material_request_type filter, DEFAULT 'Purchase' — Service/Issue/
# Transfer MRs can never chain to a PO and would pollute the MR-Only counts;
# 'All' widens deliberately.
#
# Confidentiality: every hop carries BOTH confidential_sql_clause and
# build_match_conditions, alias-repointed. Each leg applies its OWN clause; a
# hidden PR does NOT hide the PI (the PI carries its own flag and re-attaches
# via its PO link when the path shows no PR — accepted, documented behaviour).

import frappe
from frappe import _
from frappe.utils import getdate, strip_html

from trustbit_ethanol.ts_gate_entry.report import report_utils as ru


ROW_LIMIT = 5000
IN_CHUNK = 1000

MR_TYPES = ("All", "Purchase", "Material Transfer", "Material Issue", "Manufacture", "Service Request")
CHAIN_STAGES = ("", "MR Only (No PO)", "PO Placed (No PR)", "Received (No PI)", "Invoiced")


def execute(filters=None):
	if not frappe.has_permission("Material Request", "read"):
		frappe.throw(_("Not permitted to read Material Request"), frappe.PermissionError)

	filters = filters or {}
	_validate_filters(filters)
	rows, truncated = get_data(filters)
	return get_columns(), rows, None, None, _summary(rows, truncated)


def _parse_date(val, label):
	try:
		return getdate(val)
	except Exception:
		frappe.throw(_("Invalid {0}").format(label))


def _validate_filters(filters):
	frm = _parse_date(filters["from_date"], "From Date") if filters.get("from_date") else None
	to = _parse_date(filters["to_date"], "To Date") if filters.get("to_date") else None
	if frm and to and frm > to:
		frappe.throw(_("From Date cannot be after To Date"))
	if filters.get("chain_stage") and filters["chain_stage"] not in CHAIN_STAGES:
		frappe.throw(_("Invalid Chain Stage filter"))
	if filters.get("material_request_type") and filters["material_request_type"] not in MR_TYPES:
		frappe.throw(_("Invalid MR Type filter"))


def get_columns():
	return [
		{"fieldname": "sr", "label": _("S.No."), "fieldtype": "Int", "width": 60, "disable_total": 1},
		{"fieldname": "mr_no", "label": _("MR No"), "fieldtype": "Link", "options": "Material Request", "width": 180},
		{"fieldname": "mr_date", "label": _("MR Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 190},
		{"fieldname": "mr_approved_date", "label": _("MR Approval Date"), "fieldtype": "Date", "width": 120},
		{"fieldname": "po_no", "label": _("PO No"), "fieldtype": "Link", "options": "Purchase Order", "width": 170},
		{"fieldname": "po_date", "label": _("PO Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "po_approved_date", "label": _("PO Approval Date"), "fieldtype": "Date", "width": 120},
		{"fieldname": "pr_no", "label": _("Purchase Receipt"), "fieldtype": "Link", "options": "Purchase Receipt", "width": 170},
		{"fieldname": "pr_date", "label": _("PR Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "pi_no", "label": _("Purchase Invoice"), "fieldtype": "Data", "width": 170},
		{"fieldname": "pi_date", "label": _("PI Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "mr_to_pr_days", "label": _("MR to PR (Days)"), "fieldtype": "Int", "width": 110, "disable_total": 1},
		{"fieldname": "po_to_pr_days", "label": _("PO to PR (Days)"), "fieldtype": "Int", "width": 110, "disable_total": 1},
		{"fieldname": "mr_to_po_days", "label": _("MR to PO (Days)"), "fieldtype": "Int", "width": 110, "disable_total": 1},
		{"fieldname": "pr_to_pi_days", "label": _("PR to PI (Days)"), "fieldtype": "Int", "width": 110, "disable_total": 1},
		{"fieldname": "mr_to_pi_days", "label": _("MR to PI (Days)"), "fieldtype": "Int", "width": 110, "disable_total": 1},
	]


def _clauses(doctype, alias, extra=None):
	"""Submitted-only clause list for one hop, via the shared helper.
	Callers MUST gate on ru.hop_readable() first (report_utils rule 1)."""
	conds = [f"{alias}.docstatus = 1"] + ru.conf_match_clauses(doctype, alias)
	if extra:
		conds += extra
	return conds


def get_data(filters):
	params = {}
	mr_conds = _clauses("Material Request", "mr")
	if filters.get("from_date"):
		mr_conds.append("mr.transaction_date >= %(from_date)s")
		params["from_date"] = getdate(filters["from_date"])
	if filters.get("to_date"):
		mr_conds.append("mr.transaction_date <= %(to_date)s")
		params["to_date"] = getdate(filters["to_date"])
	if filters.get("material_request"):
		mr_conds.append("mr.name = %(material_request)s")
		params["material_request"] = filters["material_request"]
	mr_type = filters.get("material_request_type") or "Purchase"
	if mr_type != "All":
		mr_conds.append("mr.material_request_type = %(mr_type)s")
		params["mr_type"] = mr_type
	if filters.get("item_code"):
		mr_conds.append("mri.item_code = %(item_code)s")
		params["item_code"] = filters["item_code"]

	anchors = frappe.db.sql(
		f"""
		SELECT
			mr.name AS mr_no, mr.transaction_date AS mr_date,
			DATE(mr.ts_mr_approved_date) AS mr_approved_date,
			mri.item_code, mri.item_name, mri.idx AS item_idx
		FROM `tabMaterial Request` mr
		JOIN `tabMaterial Request Item` mri ON mri.parent = mr.name
		WHERE {" AND ".join(mr_conds)}
		ORDER BY mr.transaction_date DESC, mr.name, mri.idx
		""",
		params,
		as_dict=True,
	)

	mr_names = {a["mr_no"] for a in anchors}
	# MR -> PO
	po_of_mr = {}
	po_meta = {}
	if mr_names and ru.hop_readable("Purchase Order"):
		po_conds = _clauses("Purchase Order", "po", ["IFNULL(poi.material_request, '') != ''"])
		po_params = {}
		if filters.get("supplier"):
			po_conds.append("po.supplier = %(supplier)s")
			po_params["supplier"] = filters["supplier"]
		for chunk in ru.chunked(mr_names):
			for x in frappe.db.sql(
				f"""SELECT DISTINCT poi.material_request AS mr, po.name,
					po.transaction_date AS po_date, DATE(po.ts_approved_date) AS po_approved_date
				FROM `tabPurchase Order Item` poi
				JOIN `tabPurchase Order` po ON po.name = poi.parent
				WHERE poi.material_request IN %(names)s AND {" AND ".join(po_conds)}""",
				dict(po_params, names=chunk),
				as_dict=True,
			):
				po_of_mr.setdefault(x["mr"], []).append(x["name"])
				po_meta[x["name"]] = x

	po_names = set(po_meta)
	# PO -> PR
	pr_of_po = {}
	pr_meta = {}
	if po_names and ru.hop_readable("Purchase Receipt"):
		pr_conds = _clauses("Purchase Receipt", "pr", ["IFNULL(pri.purchase_order, '') != ''"])
		for chunk in ru.chunked(po_names):
			for x in frappe.db.sql(
				f"""SELECT DISTINCT pri.purchase_order AS po, pr.name, pr.posting_date AS pr_date
				FROM `tabPurchase Receipt Item` pri
				JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
				WHERE pri.purchase_order IN %(names)s AND {" AND ".join(pr_conds)}""",
				{"names": chunk},
				as_dict=True,
			):
				pr_of_po.setdefault(x["po"], []).append(x["name"])
				pr_meta[x["name"]] = x

	pr_names = set(pr_meta)
	# PR -> PI (receipt-linked)
	pi_of_pr = {}
	pi_meta = {}
	pi_ok = ru.hop_readable("Purchase Invoice")
	if pr_names and pi_ok:
		pi_conds = _clauses("Purchase Invoice", "pi", ["IFNULL(pii.purchase_receipt, '') != ''"])
		for chunk in ru.chunked(pr_names):
			for x in frappe.db.sql(
				f"""SELECT DISTINCT pii.purchase_receipt AS pr, pi.name, pi.posting_date AS pi_date
				FROM `tabPurchase Invoice Item` pii
				JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
				WHERE pii.purchase_receipt IN %(names)s AND {" AND ".join(pi_conds)}""",
				{"names": chunk},
				as_dict=True,
			):
				pi_of_pr.setdefault(x["pr"], []).append(x["name"])
				pi_meta[x["name"]] = x

	# PO -> PI (fallback, only used on PR-less paths)
	pi_of_po = {}
	if po_names and pi_ok:
		pi_conds = _clauses("Purchase Invoice", "pi", ["IFNULL(pii.purchase_order, '') != ''"])
		for chunk in ru.chunked(po_names):
			for x in frappe.db.sql(
				f"""SELECT DISTINCT pii.purchase_order AS po, pi.name, pi.posting_date AS pi_date
				FROM `tabPurchase Invoice Item` pii
				JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
				WHERE pii.purchase_order IN %(names)s AND {" AND ".join(pi_conds)}""",
				{"names": chunk},
				as_dict=True,
			):
				pi_of_po.setdefault(x["po"], []).append(x["name"])
				pi_meta[x["name"]] = x

	# ---- path assembly: (po, pr, pi) tuples per MR ----
	def paths_for(mr):
		out = []
		pos = sorted(po_of_mr.get(mr, ()))
		if not pos:
			return [(None, None, None)]
		for po in pos:
			prs = sorted(pr_of_po.get(po, ()))
			if prs:
				for pr in prs:
					pis = sorted(pi_of_pr.get(pr, ()))
					if pis:
						out += [(po, pr, pi) for pi in pis]
					else:
						out.append((po, pr, None))
			else:
				pis = sorted(pi_of_po.get(po, ()))
				if pis:
					out += [(po, None, pi) for pi in pis]
				else:
					out.append((po, None, None))
		return out

	stage = filters.get("chain_stage") or ""
	supplier_set = bool(filters.get("supplier"))
	path_cache = {}
	rows = []
	truncated = False
	for a in anchors:
		if a["mr_no"] not in path_cache:
			path_cache[a["mr_no"]] = paths_for(a["mr_no"])
		for po, pr, pi in path_cache[a["mr_no"]]:
			if supplier_set and po is None:
				continue  # supplier filter: only chains through that supplier's POs
			if stage:
				if stage == "MR Only (No PO)" and po is not None:
					continue
				if stage == "PO Placed (No PR)" and not (po is not None and pr is None and pi is None):
					continue
				if stage == "Received (No PI)" and not (pr is not None and pi is None):
					continue
				if stage == "Invoiced" and pi is None:
					continue
			pom = po_meta.get(po) or {}
			prm = pr_meta.get(pr) or {}
			pim = pi_meta.get(pi) or {}
			mr_d, po_d = a["mr_date"], pom.get("po_date")
			pr_d, pi_d = prm.get("pr_date"), pim.get("pi_date")
			rows.append({
				"mr_no": a["mr_no"], "mr_date": mr_d,
				"item_code": a["item_code"],
				"item_name": " ".join(strip_html(a["item_name"]).split()) if a["item_name"] else "",
				"mr_approved_date": a["mr_approved_date"],
				"po_no": po, "po_date": po_d, "po_approved_date": pom.get("po_approved_date"),
				"pr_no": pr, "pr_date": pr_d,
				"pi_no": pi or "", "pi_date": pi_d,
				"mr_to_pr_days": (pr_d - mr_d).days if (pr_d and mr_d) else None,
				"po_to_pr_days": (pr_d - po_d).days if (pr_d and po_d) else None,
				"mr_to_po_days": (po_d - mr_d).days if (po_d and mr_d) else None,
				"pr_to_pi_days": (pi_d - pr_d).days if (pi_d and pr_d) else None,
				"mr_to_pi_days": (pi_d - mr_d).days if (pi_d and mr_d) else None,
			})
			if len(rows) > ROW_LIMIT:
				truncated = True
				break
		if truncated:
			break
	if truncated:
		rows = rows[:ROW_LIMIT]
	for i, r in enumerate(rows, start=1):
		r["sr"] = i
	return rows, truncated


def _summary(rows, truncated):
	mrs = {r["mr_no"] for r in rows}
	full = {r["mr_no"] for r in rows if r["po_no"] and r["pr_no"] and r["pi_no"]}
	mr_only = {r["mr_no"] for r in rows if not r["po_no"]} - {r["mr_no"] for r in rows if r["po_no"]}
	pi_days = [r["mr_to_pi_days"] for r in rows if r["mr_to_pi_days"] is not None]

	out = []
	if truncated:
		out.append({"label": _("Result truncated"),
		            "value": _("first {0} rows — cards reflect shown rows only").format(ROW_LIMIT),
		            "datatype": "Data", "indicator": "Orange"})
	out += [
		{"label": _("Material Requests"), "value": len(mrs), "datatype": "Int"},
		{"label": _("Full Chains (MR→PO→PR→PI)"), "value": len(full), "datatype": "Int",
		 "indicator": "Green" if full else "Orange"},
		{"label": _("MR Only (No PO)"), "value": len(mr_only), "datatype": "Int",
		 "indicator": "Orange" if mr_only else "Green"},
	]
	if pi_days:
		out.append({"label": _("Avg MR→PI Days"), "value": round(sum(pi_days) / len(pi_days), 1),
		            "datatype": "Float"})
	return out
