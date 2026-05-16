"""Accounts Pending Billing Dashboard — permission seed.

Adds the Custom DocPerm row that lets users carrying ONLY the Auditor role
read Purchase Receipts so the /app/accounts-pending-billing dashboard can
return non-empty rows for them. Auditor stays read-only (no write / create /
submit / cancel / amend / delete / share).

Lives outside seed_data.py so the MR_FULL / PO_FULL feature locks aren't
touched. Idempotent; safe to re-run.
"""

import frappe


_APB_AUDITOR_PERM = {
	"parent": "Purchase Receipt",
	"parenttype": "DocType",
	"parentfield": "permissions",
	"role": "Auditor",
	"permlevel": 0,
	"read": 1,
	"report": 1,
	"export": 1,
	"print": 1,
	"email": 1,
}


def seed_apb_auditor_pr_read():
	"""Insert Auditor → Purchase Receipt read-only Custom DocPerm if missing."""
	if not frappe.db.exists("Role", "Auditor"):
		return

	existing = frappe.db.exists(
		"Custom DocPerm",
		{"parent": "Purchase Receipt", "role": "Auditor", "permlevel": 0},
	)
	if existing:
		return

	doc = frappe.get_doc({"doctype": "Custom DocPerm", **_APB_AUDITOR_PERM})
	doc.db_insert()
	frappe.db.commit()
	frappe.clear_cache(doctype="Purchase Receipt")
