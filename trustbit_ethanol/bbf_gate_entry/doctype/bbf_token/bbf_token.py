import random
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, time_diff_in_seconds, getdate, nowtime, flt


class BBFToken(Document):
	def before_insert(self):
		self.generate_token_number()
		self.g1_entry_time = now_datetime()
		self.entry_date = getdate()
		self.entry_time = nowtime()
		self.status = "Token Generated"

	def generate_token_number(self):
		date_part = getdate().strftime("%y%m%d")
		settings = frappe.get_single("BBF Settings")
		digits = settings.token_suffix_digits or 4
		min_val = 10 ** (digits - 1)
		max_val = (10 ** digits) - 1

		for _ in range(100):
			suffix = random.randint(min_val, max_val)
			token = f"TKN-{date_part}-{suffix}"
			if not frappe.db.exists("BBF Token", token):
				self.token_number = token
				return

		frappe.throw("Could not generate unique token number. Please try again.")

	def validate(self):
		self.calculate_turnaround()

	def calculate_turnaround(self):
		self.g1_to_g2_minutes = self._diff_minutes(self.g1_entry_time, self.g2_link_time)
		self.g2_to_wb_minutes = self._diff_minutes(self.g2_link_time, self.wb_gross_time)
		self.wb_to_quality_minutes = self._diff_minutes(self.wb_gross_time, self.quality_time)
		self.quality_to_grading_minutes = self._diff_minutes(self.quality_time, self.grading_time)
		self.grading_to_unload_minutes = self._diff_minutes(self.grading_time, self.unload_start_time)
		self.unloading_duration_minutes = self._diff_minutes(self.unload_start_time, self.unload_end_time)
		self.unload_to_tare_minutes = self._diff_minutes(self.unload_end_time, self.wb_tare_time)
		self.tare_to_grn_minutes = self._diff_minutes(self.wb_tare_time, self.grn_time)
		self.total_turnaround_minutes = self._diff_minutes(self.g1_entry_time, self.g1_exit_time)

	@staticmethod
	def _diff_minutes(start, end):
		if start and end:
			diff = time_diff_in_seconds(end, start)
			return round(diff / 60, 1)
		return 0

	@frappe.whitelist()
	def create_grn(self):
		"""Create a Purchase Receipt (GRN) from token data."""
		if self.status != "Tare Weighed":
			frappe.throw("GRN can only be created when status is 'Tare Weighed'")

		if self.purchase_receipt:
			frappe.throw(f"Purchase Receipt {self.purchase_receipt} already exists for this token")

		# Gather all linked data
		gate_entry = frappe.db.get_value(
			"BBF Gate Entry",
			{"token_number": self.name, "docstatus": 1},
			["name", "purchase_order", "transporter", "lr_number", "lr_date", "supplier_name"],
			as_dict=True
		)
		if not gate_entry:
			frappe.throw("No submitted Gate Entry found for this token")
		if not gate_entry.purchase_order:
			frappe.throw("No Purchase Order linked in the Gate Entry")

		# Get PO details
		po = frappe.get_doc("Purchase Order", gate_entry.purchase_order)

		# Get weighbridge net weight
		wb_log = frappe.db.get_value(
			"BBF Weighbridge Log",
			{"token_number": self.name},
			["gross_weight", "tare_weight", "net_weight"],
			as_dict=True
		)
		if not wb_log or not wb_log.net_weight:
			frappe.throw("Weighbridge Log with net weight not found. Ensure both gross and tare weights are recorded.")

		# Get deduction sheet if exists
		deduction_sheet = frappe.db.get_value(
			"BBF Deduction Sheet",
			{"token_number": self.name, "status": "Approved"},
			["name", "net_weight", "net_payable", "total_deduction", "item_rate"],
			as_dict=True
		)

		# Get settings
		settings = frappe.get_single("BBF Settings")
		warehouse = settings.default_warehouse
		accepted_warehouse = settings.default_accepted_warehouse

		if not warehouse:
			frappe.throw("Please set Default Warehouse in BBF Settings before creating GRN")

		# Build Purchase Receipt items from PO items
		pr_items = []
		gate_entry_items = frappe.get_all(
			"BBF Gate Entry Item",
			filters={"parent": gate_entry.name},
			fields=["item_code", "item_name", "ordered_qty", "uom"]
		)

		if not gate_entry_items:
			frappe.throw("No items found in Gate Entry")

		net_weight = flt(wb_log.net_weight)

		for idx, ge_item in enumerate(gate_entry_items):
			# For single-item deliveries (most common), use full net weight
			# For multi-item, proportionally distribute
			if len(gate_entry_items) == 1:
				received_qty = net_weight
			else:
				total_ordered = sum(flt(i.ordered_qty) for i in gate_entry_items)
				proportion = flt(ge_item.ordered_qty) / total_ordered if total_ordered else 1
				received_qty = net_weight * proportion

			# Get rate from deduction sheet or PO
			item_rate = 0
			if deduction_sheet and deduction_sheet.item_rate:
				item_rate = flt(deduction_sheet.item_rate)
			else:
				# Get rate from PO
				po_item_rate = frappe.db.get_value(
					"Purchase Order Item",
					{"parent": po.name, "item_code": ge_item.item_code},
					"rate"
				)
				item_rate = flt(po_item_rate)

			pr_item = {
				"item_code": ge_item.item_code,
				"item_name": ge_item.item_name,
				"qty": received_qty,
				"uom": ge_item.uom or "Kg",
				"stock_uom": ge_item.uom or "Kg",
				"rate": item_rate,
				"warehouse": accepted_warehouse or warehouse,
				"purchase_order": po.name,
				"purchase_order_item": frappe.db.get_value(
					"Purchase Order Item",
					{"parent": po.name, "item_code": ge_item.item_code},
					"name"
				),
			}
			pr_items.append(pr_item)

		# Create Purchase Receipt
		pr = frappe.get_doc({
			"doctype": "Purchase Receipt",
			"supplier": po.supplier,
			"supplier_name": po.supplier_name,
			"posting_date": getdate(),
			"company": po.company,
			"currency": po.currency,
			"buying_price_list": po.buying_price_list,
			"set_warehouse": warehouse,
			"items": pr_items,
			"bbf_token": self.name,
			"bbf_gate_entry": gate_entry.name,
			"lr_no": gate_entry.lr_number or "",
			"lr_date": gate_entry.lr_date,
			"transporter_name": gate_entry.transporter or "",
		})

		pr.flags.ignore_permissions = True
		pr.insert()

		# Auto-submit if configured
		if settings.auto_submit_grn:
			pr.submit()

		# Update token
		self.grn_time = now_datetime()
		self.status = "GRN Created"
		self.purchase_receipt = pr.name
		self.save(ignore_permissions=True)

		frappe.msgprint(
			f"Purchase Receipt <b>{pr.name}</b> created successfully",
			title="GRN Created",
			indicator="green"
		)

		return {"purchase_receipt": pr.name, "docstatus": pr.docstatus}

	@frappe.whitelist()
	def mark_exit(self):
		self.g1_exit_time = now_datetime()
		self.status = "Exited"
		self.save(ignore_permissions=True)
		self._update_vehicle_master()
		self._update_transport_master()

	def _update_vehicle_master(self):
		if not self.vehicle_number or not frappe.db.exists("BBF Vehicle Master", self.vehicle_number):
			return

		vehicle = frappe.get_doc("BBF Vehicle Master", self.vehicle_number)
		vehicle.total_trips = (vehicle.total_trips or 0) + 1
		vehicle.last_visit_date = getdate()

		if self.total_turnaround_minutes:
			prev_total = (vehicle.avg_turnaround_minutes or 0) * max((vehicle.total_trips - 1), 1)
			vehicle.avg_turnaround_minutes = round(
				(prev_total + self.total_turnaround_minutes) / vehicle.total_trips, 1
			)

		vehicle.save(ignore_permissions=True)

	def _update_transport_master(self):
		gate_entry = frappe.db.get_value(
			"BBF Gate Entry", {"token_number": self.name}, "transporter"
		)
		if not gate_entry:
			return

		transporter = frappe.get_doc("BBF Transport Master", gate_entry)
		transporter.total_trips = (transporter.total_trips or 0) + 1
		transporter.last_trip_date = getdate()

		if self.total_turnaround_minutes:
			prev_total = (transporter.avg_turnaround_minutes or 0) * max((transporter.total_trips - 1), 1)
			transporter.avg_turnaround_minutes = round(
				(prev_total + self.total_turnaround_minutes) / transporter.total_trips, 1
			)

		transporter.save(ignore_permissions=True)
