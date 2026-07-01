"""Accounts Pending Billing Dashboard — API (v2.10.1.0).

Single-section dashboard for Accounts team listing submitted Purchase Receipts
that are not yet fully billed (per_billed < 100). Each row offers an "Open"
link and a one-click "Create Purchase Invoice" action that uses ERPNext's
native make_purchase_invoice mapper to spawn a Draft PI for review + submit.

Mutation endpoint is POST-only (Lesson 175), guarded by explicit
has_permission calls (Lesson 224), parameterised SQL only, FOR UPDATE
race-guard, and server-side XSS escape.
"""

import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, escape_html, flt, getdate

APB_VERSION = "v1.0.0-2026-05-16"

READ_ROLES = {
	"Accounts User", "Accounts Manager", "Auditor",
	"IT Head", "System Manager", "Administrator",
}

MUTATE_ROLES = {
	"Accounts User", "Accounts Manager",
	"IT Head", "System Manager", "Administrator",
}

# Maximum rows the dashboard renders in one fetch. Keeps the SQL bounded and
# the JS render < 2s. Older PRs (FIFO ordering) are surfaced first.
ROW_LIMIT = 500

# Pattern accepted for Purchase Receipt names. Frappe document names allow
# alphanum + '-' + '/' + '_'; length capped at 140 (the `name` column cap).
_NAME_RE = re.compile(r"^[A-Za-z0-9\-/_]{1,140}$")


def _check_read():
	roles = set(frappe.get_roles(frappe.session.user))
	if not (READ_ROLES & roles):
		frappe.throw(
			_("You do not have access to the Accounts Pending Billing Dashboard."),
			frappe.PermissionError,
		)


def _is_auditor_readonly():
	"""True when the user has Auditor role but no role from MUTATE_ROLES."""
	roles = set(frappe.get_roles(frappe.session.user))
	return ("Auditor" in roles) and not (MUTATE_ROLES & roles)


def _esc(v):
	"""Server-side XSS escape for any string returned to the browser."""
	return escape_html(str(v or ""))


def _age_days(posting_date):
	if not posting_date:
		return 0
	try:
		from datetime import date
		d = getdate(posting_date)
		return max(0, (date.today() - d).days)
	except Exception:
		return 0


def _fetch_po_lists(pr_names):
	"""For each PR name, return distinct PO names referenced by its items."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause
	if not pr_names:
		return {}
	# Confidentiality leak-plug: drop PO names the user is not allowed to see
	# (maize-confidential POs) via an EXISTS guard on the parent Purchase Order.
	conf_po = confidential_sql_clause("po", doctype="Purchase Order")
	conf_exists = (
		f""" AND EXISTS (SELECT 1 FROM `tabPurchase Order` po
		     WHERE po.name = pri.purchase_order {conf_po})"""
		if conf_po else ""
	)
	rows = frappe.db.sql(
		f"""
		SELECT pri.parent AS parent, pri.purchase_order AS purchase_order
		FROM `tabPurchase Receipt Item` pri
		WHERE pri.parent IN %(pr_names)s
		  AND IFNULL(pri.purchase_order, '') != ''
		  {conf_exists}
		""",
		{"pr_names": tuple(pr_names)},
		as_dict=True,
	)
	out = {}
	for r in rows:
		out.setdefault(r["parent"], [])
		if r["purchase_order"] not in out[r["parent"]]:
			out[r["parent"]].append(r["purchase_order"])
	return out


def _fetch_items_for_pills(pr_names, limit_per_pr=8):
	"""First N items per PR for the expander row."""
	if not pr_names:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT parent, item_code, item_name, qty, uom
		FROM `tabPurchase Receipt Item`
		WHERE parent IN %(pr_names)s
		ORDER BY parent ASC, idx ASC
		""",
		{"pr_names": tuple(pr_names)},
		as_dict=True,
	)
	out = {}
	for r in rows:
		out.setdefault(r["parent"], [])
		if len(out[r["parent"]]) < limit_per_pr:
			out[r["parent"]].append({
				"item_code": _esc(r["item_code"]),
				"item_name": _esc(r["item_name"]),
				"qty": flt(r["qty"], 3),
				"uom": _esc(r["uom"]),
			})
	return out


def _fetch_draft_pi_refs(pr_names):
	"""Map PR name → name of any Draft Purchase Invoice that already references it."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause
	if not pr_names:
		return {}
	# Confidentiality leak-plug: hide confidential (maize-derived) draft PIs
	# from non-allowed users.
	conf_pi = confidential_sql_clause("pi", doctype="Purchase Invoice")
	rows = frappe.db.sql(
		f"""
		SELECT pii.purchase_receipt AS pr, pii.parent AS pi_name
		FROM `tabPurchase Invoice Item` pii
		JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
		WHERE pi.docstatus = 0
		  AND pii.purchase_receipt IN %(pr_names)s
		  {conf_pi}
		""",
		{"pr_names": tuple(pr_names)},
		as_dict=True,
	)
	out = {}
	for r in rows:
		# First draft PI wins (rows may repeat per PI line).
		out.setdefault(r["pr"], r["pi_name"])
	return out


# ═══════════════════════════════════════════════════════════════════════
#  READ ENDPOINT
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_dashboard_data():
	"""Return all rows + counts + flags for the dashboard."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause
	_check_read()

	# Confidentiality leak-plug: hide confidential (maize-derived) Purchase
	# Receipts from non-allowed users (e.g. Auditor).
	conf_pr = confidential_sql_clause("pr", doctype="Purchase Receipt")

	base = frappe.db.sql(
		f"""
		SELECT
			pr.name, pr.posting_date, pr.supplier, pr.supplier_name,
			pr.grand_total, pr.per_billed, pr.currency, pr.company,
			pr.status, pr.is_return
		FROM `tabPurchase Receipt` pr
		WHERE pr.docstatus = 1
		  AND IFNULL(pr.per_billed, 0) < 100
		  AND IFNULL(pr.status, '') != 'Closed'
		  AND IFNULL(pr.is_return, 0) = 0
		  {conf_pr}
		ORDER BY pr.posting_date ASC, pr.creation ASC
		LIMIT %s
		""",
		(ROW_LIMIT,),
		as_dict=True,
	)

	# Permission post-filter — drop rows the user can't read (e.g. company
	# user-permission restrictions). Cheap call on already-fetched names.
	readable = [
		r for r in base
		if frappe.has_permission("Purchase Receipt", "read", doc=r["name"])
	]
	names = [r["name"] for r in readable]

	po_map = _fetch_po_lists(names)
	items_map = _fetch_items_for_pills(names)
	draft_pi_map = _fetch_draft_pi_refs(names)

	rows = []
	for r in readable:
		gt = flt(r["grand_total"])
		pb = flt(r["per_billed"])
		billed = gt * (pb / 100.0) if pb else 0.0
		age = _age_days(r["posting_date"])
		po_list = po_map.get(r["name"], [])
		rows.append({
			"pr": _esc(r["name"]),
			"posting_date": str(r["posting_date"]) if r["posting_date"] else "",
			"supplier": _esc(r["supplier"]),
			"supplier_name": _esc(r["supplier_name"] or r["supplier"]),
			"po": _esc(po_list[0]) if po_list else "",
			"po_count": len(po_list),
			"po_list": [_esc(x) for x in po_list],
			"grand_total": gt,
			"billed_amount": billed,
			"per_billed": pb,
			"currency": _esc(r["currency"] or ""),
			"company": _esc(r["company"] or ""),
			"age_days": age,
			"has_draft_pi": r["name"] in draft_pi_map,
			"draft_pi": _esc(draft_pi_map.get(r["name"], "")),
			"items": items_map.get(r["name"], []),
		})

	counts = {
		"pending": len(rows),
		"partial": sum(1 for r in rows if 0 < r["per_billed"] < 100),
		"aged": sum(1 for r in rows if r["age_days"] > 30),
	}

	return {
		"rows": rows,
		"counts": counts,
		"auditor_readonly": _is_auditor_readonly(),
		"version": APB_VERSION,
	}


# ═══════════════════════════════════════════════════════════════════════
#  MUTATION ENDPOINT — POST-only (CSRF, Lesson 175)
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist(methods=["POST"])
@rate_limit(key="apb_create_pi", limit=30, seconds=60)
def create_pi_from_pr(pr_name=None):
	"""Create a Draft Purchase Invoice from the given Purchase Receipt.

	Uses ERPNext's native make_purchase_invoice mapper so taxes, project,
	cost-centre, payment-terms, address and MR/PO lineage are all preserved.
	Returns the new PI's name; the JS redirects the user to its form for
	review + submit.
	"""
	# 1. Input validation
	pr_name = (pr_name or "").strip()
	if not pr_name:
		frappe.throw(_("Purchase Receipt name is required."))
	if not _NAME_RE.match(pr_name):
		frappe.throw(_("Invalid Purchase Receipt name format."))

	# 2. Role + permission gates (Lesson 224 — both layers).
	#    has_permission(PI, create) throws PermissionError for Auditor.
	frappe.has_permission("Purchase Invoice", "create", throw=True)
	frappe.has_permission("Purchase Receipt", "read", doc=pr_name, throw=True)

	# 3. FOR UPDATE row lock — blocks concurrent Create-PI on the same PR.
	row = frappe.db.sql(
		"""
		SELECT name, docstatus, status, is_return, per_billed
		FROM `tabPurchase Receipt`
		WHERE name = %s
		FOR UPDATE
		""",
		(pr_name,),
		as_dict=True,
	)
	if not row:
		frappe.throw(
			_("Purchase Receipt {0} not found.").format(pr_name),
			frappe.DoesNotExistError,
		)
	row = row[0]

	# 4. State validation (re-checked under the lock).
	if cint(row["docstatus"]) != 1:
		frappe.throw(_("Purchase Receipt {0} is not submitted.").format(pr_name))
	if row["status"] == "Closed":
		frappe.throw(_("Purchase Receipt {0} is Closed; cannot create invoice.").format(pr_name))
	if cint(row["is_return"]):
		frappe.throw(_("Purchase Receipt {0} is a return; create a Debit Note instead.").format(pr_name))
	if flt(row["per_billed"]) >= 100:
		frappe.throw(_("Purchase Receipt {0} is already fully billed.").format(pr_name))

	# 5. Invoke the ERPNext native mapper + insert as Draft.
	try:
		from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
			make_purchase_invoice,
		)
		pi = make_purchase_invoice(pr_name)
		pi.flags.ignore_permissions = False
		pi.insert()
	except frappe.ValidationError:
		# Let Frappe's _server_messages propagate to the browser dialog.
		raise
	except Exception as e:
		try:
			frappe.log_error(
				title="apb.create_pi_from_pr failed",
				message=(
					f"PR={pr_name} user={frappe.session.user}\n"
					f"err={e}\n{frappe.get_traceback()}"
				),
			)
		except Exception:
			pass
		frappe.throw(_("Could not create invoice — contact IT. (See Error Log.)"))

	return {
		"purchase_invoice": pi.name,
		"message": _("Draft Purchase Invoice {0} created. Review and submit.").format(pi.name),
	}
