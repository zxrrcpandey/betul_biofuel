import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowtime


class TSReturnItem(Document):
	def validate(self):
		# Auto-set item_code from erp_item
		if self.erp_item and not self.item_code:
			self.item_code = frappe.db.get_value("Item", self.erp_item, "item_code") or self.erp_item

		# Check duplicate erp_item (1:1 mapping)
		if self.erp_item:
			existing = frappe.db.get_value("TS Return Item",
				{"erp_item": self.erp_item, "name": ["!=", self.name]}, "name")
			if existing:
				frappe.throw(
					f"ERPNext Item <b>{self.erp_item}</b> is already linked to "
					f"<a href='/app/ts-return-item/{existing}'>{existing}</a>. "
					"Each item can only have one Return Item entry."
				)

	def after_insert(self):
		"""Process opening stock on first creation."""
		if flt(self.opening_stock) > 0:
			self._apply_opening_stock()

	def _apply_opening_stock(self):
		"""Set current_stock from opening_stock and create a ledger entry."""
		qty = flt(self.opening_stock)
		if qty <= 0:
			return

		self.db_set("current_stock", qty)

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
