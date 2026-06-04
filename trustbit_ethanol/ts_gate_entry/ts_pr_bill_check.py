"""
v2.8.0.1 — Supplier Invoice duplicate check on Purchase Receipt.

Warns (not blocks) when the same supplier + bill_no combination already exists
on another submitted Purchase Receipt. Prevents accidental double-entry when
a supplier sends the same invoice with two trucks.

IT Head / System Manager / Administrator can save anyway — the check is a
guard-rail, not a hard gate.
"""

import frappe
from frappe.utils import getdate


def validate_unique_supplier_bill(doc, method=None):
	"""PR.validate hook — warn on duplicate supplier bill_no."""
	if not doc.get("bill_no") or not doc.get("supplier"):
		return

	# Find other non-cancelled PRs with same supplier + bill_no (case-insensitive exact match)
	duplicates = frappe.db.sql(
		"""
		SELECT name, posting_date, docstatus
		FROM `tabPurchase Receipt`
		WHERE supplier = %s
		  AND bill_no = %s
		  AND name != %s
		  AND docstatus != 2
		LIMIT 5
		""",
		(doc.supplier, doc.bill_no, doc.name or ""),
		as_dict=True,
	)

	if not duplicates:
		return

	# Build warning message with links to the duplicate PR(s)
	dup_list = "<br>".join(
		f"• <a href='/app/purchase-receipt/{d.name}' target='_blank'>{d.name}</a>"
		f" (posted {d.posting_date}, {'submitted' if d.docstatus == 1 else 'draft'})"
		for d in duplicates
	)

	msg = (
		f"⚠️ <b>Duplicate Supplier Invoice detected</b><br>"
		f"Supplier <b>{frappe.utils.escape_html(doc.supplier)}</b> already has "
		f"Purchase Receipt(s) with Invoice No <b>{frappe.utils.escape_html(doc.bill_no)}</b>:<br><br>"
		f"{dup_list}<br><br>"
		f"If this is a legitimate second GRN against the same invoice (e.g. partial delivery), "
		f"proceed with save. Otherwise, cancel and use the existing PR."
	)

	# Privileged roles get a softer indicator; everyone else gets a louder warning.
	allowed_bypass = {"IT Head", "System Manager", "Administrator"}
	user_roles = set(frappe.get_roles(frappe.session.user))

	if user_roles & allowed_bypass:
		frappe.msgprint(msg, title="Duplicate Invoice (informational)", indicator="orange")
	else:
		# Non-privileged users get a modal warning but save still proceeds
		# (this is a warning, not a block — user can override by acknowledging).
		frappe.msgprint(msg, title="Duplicate Invoice Warning", indicator="red", alert=True)


def require_supplier_bill_on_submit(doc, method=None):
	"""PR.before_submit — Supplier Invoice No + Date are MANDATORY before submitting.

	v2.16.4 — enforced at SUBMIT (not on every save): the Stores Receiving
	Dashboard / Token GRN create DRAFT Purchase Receipts that legitimately have no
	supplier invoice yet at goods-receipt time. Drafts may be saved without these;
	a finalized (submitted) PR must always record the supplier invoice.
	"""
	missing = []
	if not (doc.get("bill_no") or "").strip():
		missing.append("Supplier Invoice No")
	if not doc.get("bill_date"):
		missing.append("Supplier Invoice Date")
	if missing:
		frappe.throw(
			"Please enter <b>{0}</b> before submitting this Purchase Receipt.".format(
				" and ".join(missing)
			),
			title="Supplier Invoice Required",
		)
