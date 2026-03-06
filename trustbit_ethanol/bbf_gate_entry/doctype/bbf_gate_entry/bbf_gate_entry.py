import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class BBFGateEntry(Document):
	def validate(self):
		self.set_route()

	def set_route(self):
		if self.material_flow == "Raw Material":
			self.route_to = "Weighbridge"
		elif self.material_flow == "Non-Raw Material":
			self.route_to = "Stores/Department"

	def on_submit(self):
		self.update_token_status()
		self.set_gate_entry_status()

	def update_token_status(self):
		token = frappe.get_doc("BBF Token", self.token_number)
		token.g2_link_time = now_datetime()
		token.status = "PO Linked"
		token.save(ignore_permissions=True)

	def set_gate_entry_status(self):
		if self.material_flow == "Raw Material":
			self.db_set("status", "Sent to Weighbridge")
		else:
			self.db_set("status", "Sent to Stores")

	@frappe.whitelist()
	def fetch_po_items(self):
		if not self.purchase_order:
			frappe.throw("Please select a Purchase Order first")

		po = frappe.get_doc("Purchase Order", self.purchase_order)
		self.po_items = []
		for item in po.items:
			self.append("po_items", {
				"item_code": item.item_code,
				"item_name": item.item_name,
				"ordered_qty": item.qty,
				"uom": item.uom
			})
		self.supplier_name = po.supplier_name
