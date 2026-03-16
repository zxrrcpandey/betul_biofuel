import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class BBFWeighbridgeLog(Document):
	def before_insert(self):
		self.auto_fetch_from_gate_entry()
		self.validate_token_status()

	def auto_fetch_from_gate_entry(self):
		if not self.token_number:
			return

		gate_entry = frappe.db.get_value(
			"BBF Gate Entry",
			{"token_number": self.token_number, "docstatus": 1},
			["name", "purchase_order", "material_flow"],
			as_dict=True
		)
		if gate_entry:
			self.gate_entry = gate_entry.name
			self.purchase_order = gate_entry.purchase_order
			self.material_flow = gate_entry.material_flow

	def validate_token_status(self):
		if not self.token_number:
			return
		token_status = frappe.db.get_value("BBF Token", self.token_number, "status")
		if token_status not in ("PO Linked",):
			frappe.throw(f"Token {self.token_number} is at stage '{token_status}'. Only tokens with status 'PO Linked' can be weighed.")

	def validate(self):
		self.set_operators()
		self.calculate_net_weight()
		self.update_status()

	def set_operators(self):
		if self.gross_weight and not self.gross_operator:
			self.gross_operator = frappe.session.user
		if self.tare_weight and not self.tare_operator:
			self.tare_operator = frappe.session.user

	def calculate_net_weight(self):
		if self.gross_weight and self.tare_weight:
			net = self.gross_weight - self.tare_weight
			if net < 0:
				frappe.throw(f"Net weight cannot be negative ({net} KG). Tare weight ({self.tare_weight}) exceeds gross weight ({self.gross_weight}).")
			self.net_weight = net
			self._calculate_weight_difference()

	def _calculate_weight_difference(self):
		if not self.purchase_order or not self.net_weight:
			return

		from frappe.utils import flt
		po_items = frappe.get_all(
			"Purchase Order Item",
			filters={"parent": self.purchase_order},
			fields=["qty", "stock_qty"]
		)
		ordered_qty = sum(flt(item.stock_qty or item.qty) for item in po_items)

		if ordered_qty:
			self.weight_difference_percent = round(
				((self.net_weight - ordered_qty) / ordered_qty) * 100, 2
			)

	def update_status(self):
		if self.tare_weight and self.gross_weight:
			self.status = "Completed"
		elif self.unloading_complete and self.gross_weight and not self.tare_weight:
			self.status = "Awaiting Tare Weight"
		elif self.gross_weight and not self.tare_weight:
			self.status = "Awaiting Unloading"
		else:
			self.status = "Gross Recorded"

	def after_insert(self):
		self.update_token_gross()

	def update_token_gross(self):
		if self.gross_weight and self.token_number:
			self.gross_weight_time = now_datetime()
			self.db_set("gross_weight_time", self.gross_weight_time)

			token = frappe.get_doc("BBF Token", self.token_number)
			token.db_set({
				"wb_gross_time": now_datetime(),
				"status": "Gross Weighed"
			})

	def on_update(self):
		if self.has_value_changed("tare_weight") and self.tare_weight:
			self.db_set("tare_weight_time", now_datetime())
			self.db_set("tare_operator", frappe.session.user)

			token = frappe.get_doc("BBF Token", self.token_number)
			token.db_set({
				"wb_tare_time": now_datetime(),
				"status": "Tare Weighed"
			})
