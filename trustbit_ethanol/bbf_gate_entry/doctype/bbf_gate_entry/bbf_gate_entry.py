import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, flt


class BBFGateEntry(Document):
	def validate(self):
		self.set_route()
		self.validate_token_status()
		self.validate_po_remaining_qty()

	def set_route(self):
		if self.material_flow == "Raw Material":
			self.route_to = "Weighbridge"
		elif self.material_flow == "Non-Raw Material":
			self.route_to = "Stores/Department"

	def validate_token_status(self):
		if not self.token_number:
			return
		token_status = frappe.db.get_value("BBF Token", self.token_number, "status")
		if token_status and token_status != "Token Generated":
			frappe.throw(f"Token {self.token_number} is already at stage '{token_status}'. Only tokens with status 'Token Generated' can be linked to a Gate Entry.")

	def validate_po_remaining_qty(self):
		if not self.purchase_order:
			return
		po = frappe.get_doc("Purchase Order", self.purchase_order)
		if po.per_received >= 100:
			frappe.throw(f"Purchase Order {self.purchase_order} is already 100% received. Please select a different PO.")

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
