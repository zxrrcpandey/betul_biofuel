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
			["name", "purchase_order", "material_flow", "stock_direction", "sales_invoice"],
			as_dict=True
		)
		if gate_entry:
			self.gate_entry = gate_entry.name
			self.purchase_order = gate_entry.purchase_order
			self.material_flow = gate_entry.material_flow
			self.stock_direction = gate_entry.stock_direction or "Stock IN"

	def validate_token_status(self):
		if not self.token_number:
			return
		token = frappe.db.get_value("BBF Token", self.token_number, ["status", "stock_direction"], as_dict=True)
		if not token:
			return
		if token.stock_direction == "Stock OUT":
			# Stock OUT: accepted statuses
			if token.status not in ("SI Linked", "Tare Recorded", "Loading Done"):
				frappe.throw(f"Token {self.token_number} is at stage '{token.status}'. Stock OUT tokens need status 'SI Linked', 'Tare Recorded', or 'Loading Done'.")
		else:
			if token.status not in ("PO Linked",):
				frappe.throw(f"Token {self.token_number} is at stage '{token.status}'. Only tokens with status 'PO Linked' can be weighed.")

	def validate(self):
		self.set_operators()
		self.calculate_net_weight()
		self.fetch_po_uom()
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

	def fetch_po_uom(self):
		"""Fetch UOM from PO and calculate conversion for display."""
		from frappe.utils import flt
		if not self.purchase_order or not self.net_weight:
			return

		# Get first PO item's UOM (primary item)
		po_item = frappe.db.get_value(
			"Purchase Order Item",
			{"parent": self.purchase_order},
			["uom", "item_code", "conversion_factor"],
			as_dict=True,
			order_by="idx"
		)
		if not po_item:
			return

		po_uom = po_item.uom
		self.po_uom = po_uom

		if not po_uom or po_uom == "Kg":
			# Same UOM, no conversion needed
			self.conversion_factor = 1
			self.net_weight_in_po_uom = self.net_weight
			return

		# Get conversion factor: how many KG per 1 PO UOM
		# Check item-level UOM conversion first
		conversion = frappe.db.get_value(
			"UOM Conversion Detail",
			{"parent": po_item.item_code, "uom": "Kg"},
			"conversion_factor"
		)
		if conversion:
			# conversion_factor on item = how many stock_uom per 1 of this UOM
			# e.g., Quintal item has Kg conversion_factor = 0.01 (1 Kg = 0.01 Quintal)
			# We need: 1 Quintal = 100 Kg
			po_uom_conversion = frappe.db.get_value(
				"UOM Conversion Detail",
				{"parent": po_item.item_code, "uom": po_uom},
				"conversion_factor"
			)
			if po_uom_conversion:
				kg_conversion = frappe.db.get_value(
					"UOM Conversion Detail",
					{"parent": po_item.item_code, "uom": "Kg"},
					"conversion_factor"
				)
				if kg_conversion and flt(kg_conversion) > 0:
					# conversion_factor = PO UOM factor / Kg factor
					# e.g., Quintal=1, Kg=0.01 → 1 Quintal = 1/0.01 = 100 Kg
					factor = flt(po_uom_conversion) / flt(kg_conversion)
					self.conversion_factor = factor
					self.net_weight_in_po_uom = round(self.net_weight / factor, 3) if factor else 0
					return

		# Fallback: check global UOM Conversion Factor
		global_factor = frappe.db.get_value(
			"UOM Conversion Factor",
			{"from_uom": po_uom, "to_uom": "Kg"},
			"value"
		)
		if global_factor and flt(global_factor) > 0:
			self.conversion_factor = flt(global_factor)
			self.net_weight_in_po_uom = round(self.net_weight / flt(global_factor), 3)
			return

		# Reverse lookup
		reverse_factor = frappe.db.get_value(
			"UOM Conversion Factor",
			{"from_uom": "Kg", "to_uom": po_uom},
			"value"
		)
		if reverse_factor and flt(reverse_factor) > 0:
			self.conversion_factor = round(1 / flt(reverse_factor), 6)
			self.net_weight_in_po_uom = round(self.net_weight * flt(reverse_factor), 3)
			return

		# Known conversions fallback
		known = {"Quintal": 100, "MT": 1000, "Ton": 1000, "Gram": 0.001}
		if po_uom in known:
			self.conversion_factor = known[po_uom]
			self.net_weight_in_po_uom = round(self.net_weight / known[po_uom], 3)

	def _is_non_rm_weighing(self):
		"""Check if this is a Non-RM token with requires_weighing (no unloading needed)."""
		if not self.token_number:
			return False
		purpose = frappe.db.get_value("BBF Token", self.token_number, "purpose")
		if purpose != "Raw Material":
			ge = frappe.db.get_value(
				"BBF Gate Entry",
				{"token_number": self.token_number, "docstatus": 1},
				"requires_weighing"
			)
			return bool(ge)
		return False

	def _is_stock_out(self):
		"""Check if this weighbridge log is for a Stock OUT token."""
		if hasattr(self, 'stock_direction') and self.stock_direction == "Stock OUT":
			return True
		if self.token_number:
			sd = frappe.db.get_value("BBF Token", self.token_number, "stock_direction")
			return sd == "Stock OUT"
		return False

	def update_status(self):
		if self._is_stock_out():
			# Stock OUT: Tare first → Loading → Gross
			if self.tare_weight and self.gross_weight:
				self.status = "Completed"
			elif self.tare_weight and not self.gross_weight:
				self.status = "Awaiting Loading"
			else:
				self.status = "Awaiting Tare"
		else:
			# Stock IN: Gross first → Unloading → Tare
			if self.tare_weight and self.gross_weight:
				self.status = "Completed"
			elif self.gross_weight and not self.tare_weight:
				if self._is_non_rm_weighing() or self.unloading_complete:
					self.status = "Awaiting Tare Weight"
				else:
					self.status = "Awaiting Unloading"
			else:
				self.status = "Gross Recorded"

	def after_insert(self):
		if self._is_stock_out():
			self._update_token_tare_first()
		else:
			self.update_token_gross()

	def _update_token_tare_first(self):
		"""Stock OUT: tare weight is recorded first (empty vehicle)."""
		if self.tare_weight and self.token_number:
			self.db_set("tare_weight_time", now_datetime())
			self.db_set("tare_operator", frappe.session.user)

			token = frappe.get_doc("BBF Token", self.token_number)
			token.db_set({
				"wb_tare_time": now_datetime(),
				"status": "Tare Recorded"
			})

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
		if self._is_stock_out():
			# Stock OUT: gross weight is the second weight
			if self.has_value_changed("gross_weight") and self.gross_weight:
				self.db_set("gross_weight_time", now_datetime())
				self.db_set("gross_operator", frappe.session.user)

				token = frappe.get_doc("BBF Token", self.token_number)
				token.db_set({
					"wb_gross_time": now_datetime(),
					"status": "Gross Recorded"
				})
		else:
			# Stock IN: tare weight is the second weight
			if self.has_value_changed("tare_weight") and self.tare_weight:
				self.db_set("tare_weight_time", now_datetime())
				self.db_set("tare_operator", frappe.session.user)

				token = frappe.get_doc("BBF Token", self.token_number)
				token.db_set({
					"wb_tare_time": now_datetime(),
					"status": "Tare Weighed"
				})
