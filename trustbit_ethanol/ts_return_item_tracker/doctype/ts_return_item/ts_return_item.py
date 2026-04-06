import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowtime


class TSReturnItem(Document):
	def after_insert(self):
		"""Process opening stock on first creation."""
		if flt(self.opening_stock) > 0:
			self._apply_opening_stock()

	def _apply_opening_stock(self):
		"""Set current_stock from opening_stock and create a ledger entry."""
		qty = flt(self.opening_stock)
		if qty <= 0:
			return

		# Update current_stock via db_set (avoid save loop)
		self.db_set("current_stock", qty)

		# Create ledger entry for audit trail
		frappe.get_doc({
			"doctype": "TS Return Item Ledger",
			"asset_item": self.name,
			"item_name": self.item_name,
			"transaction_type": "Opening",
			"qty_change": qty,
			"balance_qty": qty,
			"posting_date": getdate(),
			"posting_time": nowtime(),
			"remarks": f"Opening stock: {qty}",
		}).insert(ignore_permissions=True)
