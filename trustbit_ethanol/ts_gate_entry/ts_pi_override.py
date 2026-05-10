"""v2.9.14.4 — Purchase Invoice class override.

Overrides ERPNext's `validate_purchase_receipt_if_update_stock` to allow
update_stock=1 on Purchase Returns even when items have purchase_receipt set.

ERPNext's same file (purchase_invoice.py) at lines 715-723 has a status updater
that EXPLICITLY supports update_stock=1 + is_return=1 — the validator at
line 727 is an oversight that didn't get the same exception. This override
restores consistency: forward PI from PR is still blocked (prevents double
stock IN); Return PI from PR is now allowed (legitimate return stock OUT).
"""
import frappe
from frappe import _
from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice


class TSPurchaseInvoice(PurchaseInvoice):
	def validate_purchase_receipt_if_update_stock(self):
		# Forward PI from PR + update_stock=1 → block (preserves native ERPNext
		# behavior, avoids double stock IN).
		# Return PI from PR + update_stock=1 → allow (return stock OUT is the
		# user's intended movement; status_updater handles per-item ledgers).
		if self.update_stock and not self.is_return:
			for item in self.get("items"):
				if item.purchase_receipt:
					frappe.throw(
						_("Stock cannot be updated against Purchase Receipt {0}").format(item.purchase_receipt)
					)
