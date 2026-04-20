import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, flt, getdate


class TSGateEntry(Document):
	def validate(self):
		# Post-dated entry validation (past dates need approval, future dates blocked)
		if self.entry_date and getdate(self.entry_date) != getdate():
			from trustbit_ethanol.ts_gate_entry.ts_post_dated import validate_post_dated_date
			validate_post_dated_date("TS Gate Entry", self.entry_date, self.token_number)

		if self.stock_direction == "Stock OUT":
			self._validate_stock_out()
		else:
			self._sync_purchase_order_from_po_list()
			self._validate_same_supplier()
			self.validate_po_remaining_qty()
		self.set_route()
		self.validate_token_status()

	def _sync_purchase_order_from_po_list(self):
		"""Keep purchase_order field in sync with first PO in po_list for backward compatibility."""
		if self.po_list and len(self.po_list) > 0:
			self.purchase_order = self.po_list[0].purchase_order
			if not self.supplier_name:
				self.supplier_name = self.po_list[0].supplier_name
		elif self.purchase_order and (not self.po_list or len(self.po_list) == 0):
			# Legacy: single PO entered directly — auto-add to po_list
			po = frappe.get_doc("Purchase Order", self.purchase_order)
			self.append("po_list", {
				"purchase_order": self.purchase_order,
				"supplier_name": po.supplier_name,
				"grand_total": po.grand_total,
				"item_count": len(po.items)
			})
			self.supplier_name = po.supplier_name

	def _validate_same_supplier(self):
		"""All POs must be from the same supplier."""
		if not self.po_list or len(self.po_list) <= 1:
			return

		suppliers = set()
		for row in self.po_list:
			supplier = frappe.db.get_value("Purchase Order", row.purchase_order, "supplier")
			if supplier:
				suppliers.add(supplier)

		if len(suppliers) > 1:
			frappe.throw("All Purchase Orders must be from the same supplier. Found multiple suppliers.")

	def _validate_stock_out(self):
		"""Validate Stock OUT gate entry."""
		if not self.sales_invoice:
			frappe.throw("Sales Invoice is required for Stock OUT entries")
		si = frappe.get_doc("Sales Invoice", self.sales_invoice)
		if si.docstatus != 1:
			frappe.throw(f"Sales Invoice {self.sales_invoice} is not submitted")

	def set_route(self):
		if self.stock_direction == "Stock OUT":
			self.route_to = "Weighbridge"
			self.material_flow = self.material_flow or "Non-Raw Material"
			return
		if self.material_flow == "Raw Material":
			self.route_to = "Weighbridge"
		elif self.material_flow == "Non-Raw Material":
			if self.requires_weighing:
				self.route_to = "Weighbridge"
			else:
				self.route_to = "Stores/Department"

	def validate_token_status(self):
		if not self.token_number:
			return
		if not frappe.db.exists("TS Token", self.token_number):
			frappe.throw(f"Token {self.token_number} does not exist")
		token_status = frappe.db.get_value("TS Token", self.token_number, "status")
		# Allow "PO Linked" when amending a gate entry (token was already advanced)
		allowed = ["Token Generated"]
		if self.amended_from:
			allowed.append("PO Linked")
		if token_status and token_status not in allowed:
			frappe.throw(f"Token {self.token_number} is already at stage '{token_status}'. Only tokens with status 'Token Generated' can be linked to a Gate Entry.")

	def validate_po_remaining_qty(self):
		if self.stock_direction == "Stock OUT" or not self.po_list:
			return
		for row in self.po_list:
			if not row.purchase_order:
				continue
			po = frappe.get_doc("Purchase Order", row.purchase_order)
			if po.per_received >= 100:
				frappe.throw(f"Purchase Order {row.purchase_order} is already 100% received. Please remove it.")

	def on_submit(self):
		self._check_blacklist_on_submit()
		self._copy_vehicle_driver_to_token()
		self.update_token_status()
		self.set_gate_entry_status()
		if self.stock_direction != "Stock OUT":
			self._create_material_inspection()

	def _check_blacklist_on_submit(self):
		"""Re-check blacklist at G2 submit for vehicle and driver."""
		vehicle_number = frappe.db.get_value("TS Token", self.token_number, "vehicle_number")
		if vehicle_number and frappe.db.exists("TS Vehicle Master", vehicle_number):
			bl = frappe.db.get_value(
				"TS Vehicle Master", vehicle_number,
				["is_blacklisted", "blacklist_reason"], as_dict=True
			)
			if bl and bl.is_blacklisted:
				reason = bl.blacklist_reason or "No reason specified"
				frappe.throw(
					f"Vehicle <b>{vehicle_number}</b> is blacklisted.<br><b>Reason:</b> {reason}<br>Contact IT Head.",
					title="Blacklisted Vehicle"
				)

		if self.driver and frappe.db.exists("TS Driver Master", self.driver):
			bl = frappe.db.get_value(
				"TS Driver Master", self.driver,
				["is_blacklisted", "blacklist_reason"], as_dict=True
			)
			if bl and bl.is_blacklisted:
				reason = bl.blacklist_reason or "No reason specified"
				frappe.throw(
					f"Driver <b>{self.driver}</b> is blacklisted.<br><b>Reason:</b> {reason}<br>Contact IT Head.",
					title="Blacklisted Driver"
				)

	def _copy_vehicle_driver_to_token(self):
		"""Copy vehicle master and driver details from Gate Entry to Token."""
		if not self.token_number:
			return
		updates = {}
		if self.driver:
			driver_doc = frappe.get_doc("TS Driver Master", self.driver)
			updates["driver"] = self.driver
			updates["driver_name"] = driver_doc.driver_name
			updates["driver_mobile"] = driver_doc.mobile_number
			updates["driver_license_number"] = driver_doc.license_number
		if self.vehicle_master:
			vehicle_doc = frappe.get_doc("TS Vehicle Master", self.vehicle_master)
			updates["vehicle_type"] = vehicle_doc.vehicle_type
		if updates:
			token = frappe.get_doc("TS Token", self.token_number)
			token.db_set(updates)

	def update_token_status(self):
		token = frappe.get_doc("TS Token", self.token_number)
		# v2.8.3: flag-aware status on Gate Entry submit — when two-pass gates are ON,
		# Stock IN tokens stop at 'G1 Entered' so g2_mat_log_entry can then advance them.
		try:
			two_pass_on = bool(frappe.db.get_single_value("TS Settings", "ts_two_pass_gates_enabled"))
		except Exception:
			two_pass_on = False
		if self.stock_direction == "Stock OUT":
			token.db_set({
				"g2_link_time": now_datetime(),
				"status": "SI Linked",
				"purpose": self.material_flow,
				"stock_direction": "Stock OUT"
			})
		else:
			new_status = "G1 Entered" if two_pass_on else "PO Linked"
			token.db_set({
				"g2_link_time": now_datetime(),
				"status": new_status,
				"purpose": self.material_flow,
				"stock_direction": "Stock IN"
			})

	def set_gate_entry_status(self):
		if self.stock_direction == "Stock OUT":
			self.db_set("status", "Sent to Weighbridge")
			return
		if self.material_flow == "Raw Material" or self.requires_weighing:
			self.db_set("status", "Sent to Weighbridge")
		else:
			self.db_set("status", "Sent to Stores")

	@frappe.whitelist()
	def fetch_po_items(self):
		"""Fetch items from all POs in po_list (or from purchase_order for backward compat)."""
		self.po_items = []

		po_names = []
		if self.po_list and len(self.po_list) > 0:
			po_names = [row.purchase_order for row in self.po_list if row.purchase_order]
		elif self.purchase_order:
			po_names = [self.purchase_order]

		if not po_names:
			frappe.throw("Please add at least one Purchase Order first")

		for po_name in po_names:
			po = frappe.get_doc("Purchase Order", po_name)
			for item in po.items:
				self.append("po_items", {
					"item_code": item.item_code,
					"item_name": item.item_name,
					"ordered_qty": item.qty,
					"uom": item.uom,
					"purchase_order": po_name
				})

		if not self.supplier_name and po_names:
			self.supplier_name = frappe.db.get_value("Purchase Order", po_names[0], "supplier_name")

	@frappe.whitelist()
	def add_purchase_order(self, po_name):
		"""Add a PO to the po_list and fetch its items."""
		if not po_name:
			frappe.throw("Please specify a Purchase Order")

		# Check not already added
		existing = [row.purchase_order for row in (self.po_list or [])]
		if po_name in existing:
			frappe.throw(f"Purchase Order {po_name} is already added")

		po = frappe.get_doc("Purchase Order", po_name)

		# Validate same supplier
		if self.po_list and len(self.po_list) > 0:
			first_supplier = frappe.db.get_value("Purchase Order", self.po_list[0].purchase_order, "supplier")
			if po.supplier != first_supplier:
				frappe.throw(f"All POs must be from the same supplier. First PO supplier: {first_supplier}, this PO supplier: {po.supplier}")

		if po.per_received >= 100:
			frappe.throw(f"Purchase Order {po_name} is already 100% received")

		if po.docstatus != 1:
			frappe.throw(f"Purchase Order {po_name} is not submitted")

		# Add to PO list
		self.append("po_list", {
			"purchase_order": po_name,
			"supplier_name": po.supplier_name,
			"grand_total": po.grand_total,
			"item_count": len(po.items)
		})

		# Add items
		for item in po.items:
			self.append("po_items", {
				"item_code": item.item_code,
				"item_name": item.item_name,
				"ordered_qty": item.qty,
				"uom": item.uom,
				"purchase_order": po_name
			})

		# Set supplier from first PO
		if not self.supplier_name:
			self.supplier_name = po.supplier_name
		if not self.purchase_order:
			self.purchase_order = po_name

	@frappe.whitelist()
	def fetch_si_items(self):
		"""Fetch items from Sales Invoice for Stock OUT."""
		if not self.sales_invoice:
			frappe.throw("Please select a Sales Invoice first")

		si = frappe.get_doc("Sales Invoice", self.sales_invoice)
		self.po_items = []
		for item in si.items:
			self.append("po_items", {
				"item_code": item.item_code,
				"item_name": item.item_name,
				"ordered_qty": item.qty,
				"uom": item.uom
			})

		self.customer_name = si.customer_name

	def _create_material_inspection(self):
		"""Auto-create material inspection for Non-RM gate entries."""
		if self.material_flow != "Raw Material":
			from trustbit_ethanol.ts_gate_entry.doctype.ts_material_inspection.ts_material_inspection import create_material_inspection
			create_material_inspection(self.name)
