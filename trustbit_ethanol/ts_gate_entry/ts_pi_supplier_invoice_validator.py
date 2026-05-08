"""v2.9.13 — Purchase Invoice supplier-invoice strict-match validator.

Blocks PI submission unless its bill_no + bill_date strictly match every linked
Purchase Receipt's bill_no + bill_date. Skipped when no PR is linked (direct
invoice). Empty values are NOT allowed on either side when a PR is linked.
"""
import frappe
from frappe import _
from frappe.utils import escape_html


def validate_supplier_invoice_match(doc, method=None):
    linked_prs = sorted({(it.purchase_receipt or "").strip() for it in (doc.get("items") or [])} - {""})
    if not linked_prs:
        return

    pi_no = (doc.bill_no or "").strip()
    pi_date = doc.bill_date

    if not pi_no or not pi_date:
        frappe.throw(
            _("Enter <b>Supplier Invoice No</b> and <b>Supplier Invoice Date</b> on this Purchase Invoice before submitting (linked Purchase Receipts: {0}).").format(escape_html(", ".join(linked_prs))),
            title=_("Supplier Invoice Required"),
        )

    pr_rows = frappe.get_all(
        "Purchase Receipt",
        filters={"name": ["in", linked_prs]},
        fields=["name", "bill_no", "bill_date"],
    )
    by_name = {r.name: r for r in pr_rows}

    distinct_no = {(by_name[n].bill_no or "").strip() for n in linked_prs}
    distinct_date = {by_name[n].bill_date for n in linked_prs}

    if len(distinct_no) > 1 or len(distinct_date) > 1:
        rows = "<br>".join("&bull; " + escape_html(n) + ": " + escape_html(by_name[n].bill_no or "(empty)") + " / " + escape_html(str(by_name[n].bill_date) if by_name[n].bill_date else "(empty)") for n in linked_prs)
        frappe.throw(
            _("Linked Purchase Receipts have conflicting Supplier Invoice No / Date — fix the receipts so they match before submitting this Invoice:<br>{0}").format(rows),
            title=_("Conflicting Supplier Invoice on Receipts"),
        )

    for n in linked_prs:
        pr_no = (by_name[n].bill_no or "").strip()
        pr_date = by_name[n].bill_date
        if not pr_no or not pr_date:
            frappe.throw(
                _("Purchase Receipt <b>{0}</b> is missing Supplier Invoice No / Date. Amend the receipt and set both fields, then submit this Invoice.").format(escape_html(n)),
                title=_("Receipt Missing Supplier Invoice"),
            )
        if pr_no != pi_no or str(pr_date) != str(pi_date):
            frappe.throw(
                _("Supplier Invoice mismatch with Purchase Receipt <b>{0}</b>:<br>Invoice: {1} / {2}<br>Receipt: {3} / {4}").format(
                    escape_html(n), escape_html(pi_no), escape_html(str(pi_date)), escape_html(pr_no), escape_html(str(pr_date))
                ),
                title=_("Supplier Invoice Mismatch"),
            )
