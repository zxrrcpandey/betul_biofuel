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

		# Gate Pass: set initial status
		if self.entry_type == "Gate Pass":
			self.gate_pass_status = "Inside Campus"

		# Auto-fill host name from destination default
		if self.entry_type == "Gate Pass" and self.destination:
			default_host = frappe.db.get_value(
				"BBF Gate Pass Destination", self.destination, "default_host"
			)
			if default_host and not self.host_name:
				self.host_name = default_host

	def generate_token_number(self):
		date_part = getdate().strftime("%y%m%d")
		settings = frappe.get_single("BBF Settings")
		digits = max(int(settings.token_suffix_digits or 4), 2)
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
		if self.entry_type == "Material":
			self.calculate_turnaround()

		# Gate Pass validation
		if self.entry_type == "Gate Pass":
			if not self.visitor_name:
				frappe.throw("Visitor Name is required for Gate Pass")
			if not self.visit_purpose:
				frappe.throw("Visit Purpose is required for Gate Pass")
			if not self.destination:
				frappe.throw("Destination is required for Gate Pass")

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
			return max(round(diff / 60, 1), 0)
		return 0

	@staticmethod
	def _format_duration(minutes):
		"""Format minutes into human-readable duration string."""
		if not minutes or minutes < 0:
			return "0m"
		hours = int(minutes // 60)
		mins = int(minutes % 60)
		if hours > 0:
			return f"{hours}h {mins}m"
		return f"{mins}m"

	@frappe.whitelist()
	def create_grn(self):
		"""Create a Purchase Receipt (GRN) from token data."""
		if self.entry_type == "Gate Pass":
			frappe.throw("GRN cannot be created for Gate Pass entries")

		allowed_roles = {"Accounts Manager", "Accounts User", "Stores User", "IT Head", "System Manager"}
		if not allowed_roles.intersection(set(frappe.get_roles())):
			frappe.throw("You do not have permission to create GRN")

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

		# Get PO details and validate it's submitted
		po = frappe.get_doc("Purchase Order", gate_entry.purchase_order)
		if po.docstatus != 1:
			frappe.throw(f"Purchase Order {po.name} is not submitted")

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

			# Get rate: per-item from PO, fallback to deduction sheet rate
			po_item = frappe.db.get_value(
				"Purchase Order Item",
				{"parent": po.name, "item_code": ge_item.item_code},
				["name", "rate"],
				as_dict=True
			)

			if po_item:
				item_rate = flt(po_item.rate)
				po_item_name = po_item.name
			else:
				item_rate = flt(deduction_sheet.item_rate) if deduction_sheet and deduction_sheet.item_rate else 0
				po_item_name = None

			pr_item = {
				"item_code": ge_item.item_code,
				"item_name": ge_item.item_name,
				"qty": received_qty,
				"uom": ge_item.uom or "Kg",
				"stock_uom": ge_item.uom or "Kg",
				"rate": item_rate,
				"warehouse": accepted_warehouse or warehouse,
				"purchase_order": po.name,
				"purchase_order_item": po_item_name,
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

		# Update token (use db_set to skip mandatory field validation)
		self.db_set({
			"grn_time": now_datetime(),
			"status": "GRN Created",
			"purchase_receipt": pr.name
		})

		frappe.msgprint(
			f"Purchase Receipt <b>{pr.name}</b> created successfully",
			title="GRN Created",
			indicator="green"
		)

		return {"purchase_receipt": pr.name, "docstatus": pr.docstatus}

	@frappe.whitelist()
	def mark_exit(self):
		# Prevent double-exit
		if self.entry_type == "Gate Pass":
			if self.gate_pass_status == "Exited":
				frappe.throw("This gate pass is already marked as exited")
			if self.gate_pass_status == "Inside Plant":
				frappe.throw("Visitor is still inside the plant. Please log G2 exit first.")
		else:
			if self.status == "Exited":
				frappe.throw("This token is already marked as exited")

		# Material tokens: exit restrictions based on purpose and weighing
		if self.entry_type == "Material":
			if self.purpose == "Raw Material" and self.status != "GRN Created":
				frappe.throw("Raw Material tokens can only be marked as exited after GRN is created")

			# Non-RM with requires_weighing: must complete weighbridge (Tare Weighed or GRN Created)
			if self.purpose != "Raw Material":
				requires_weighing = frappe.db.get_value(
					"BBF Gate Entry",
					{"token_number": self.name, "docstatus": 1},
					"requires_weighing"
				)
				if requires_weighing and self.status not in ("Tare Weighed", "GRN Created"):
					frappe.throw("This vehicle requires weighing. Please complete weighbridge (gross + tare) before marking exit.")

		exit_time = now_datetime()
		self.g1_exit_time = exit_time

		if self.entry_type == "Gate Pass":
			# Calculate campus duration
			campus_minutes = self._diff_minutes(self.g1_entry_time, exit_time)
			self.db_set({
				"g1_exit_time": exit_time,
				"gate_pass_status": "Exited",
				"status": "Exited",
				"total_campus_time": self._format_duration(campus_minutes),
				"total_turnaround_minutes": campus_minutes
			})
		else:
			self.status = "Exited"
			self.db_set({
				"g1_exit_time": exit_time,
				"status": "Exited"
			})
			self._update_vehicle_master()
			self._update_transport_master()

	@frappe.whitelist()
	def g2_log_entry(self):
		"""G2 operator logs visitor entry into the plant."""
		if self.entry_type != "Gate Pass":
			frappe.throw("G2 checkpoint is only for Gate Pass entries")

		if self.gate_pass_status != "Inside Campus":
			frappe.throw("Visitor must be 'Inside Campus' to log G2 entry")

		# Verify destination has G2 checkpoint
		has_g2 = frappe.db.get_value(
			"BBF Gate Pass Destination", self.destination, "has_g2_checkpoint"
		)
		if not has_g2:
			frappe.throw(f"Destination '{self.destination}' does not have a G2 checkpoint")

		# Role check — only G2 Gate Operator, IT Head, System Manager
		allowed_roles = {"G2 Gate Operator", "IT Head", "System Manager"}
		if not allowed_roles.intersection(set(frappe.get_roles())):
			frappe.throw("You do not have permission to log G2 entry")

		entry_time = now_datetime()
		self.db_set({
			"g2_entry_time_gp": entry_time,
			"g2_entry_by": frappe.session.user,
			"gate_pass_status": "Inside Plant"
		})

		frappe.msgprint("G2 entry logged successfully", indicator="green")

	@frappe.whitelist()
	def g2_log_exit(self):
		"""G2 operator logs visitor exit from the plant."""
		if self.entry_type != "Gate Pass":
			frappe.throw("G2 checkpoint is only for Gate Pass entries")

		if self.gate_pass_status != "Inside Plant":
			frappe.throw("Visitor must be 'Inside Plant' to log G2 exit")

		# Role check — only G2 Gate Operator, IT Head, System Manager
		allowed_roles = {"G2 Gate Operator", "IT Head", "System Manager"}
		if not allowed_roles.intersection(set(frappe.get_roles())):
			frappe.throw("You do not have permission to log G2 exit")

		exit_time = now_datetime()
		# Calculate plant duration
		plant_minutes = self._diff_minutes(self.g2_entry_time_gp, exit_time)

		self.db_set({
			"g2_exit_time_gp": exit_time,
			"g2_exit_by": frappe.session.user,
			"gate_pass_status": "Inside Campus",
			"total_plant_time": self._format_duration(plant_minutes)
		})

		frappe.msgprint("G2 exit logged successfully", indicator="green")

	def _update_vehicle_master(self):
		if not self.vehicle_number or not frappe.db.exists("BBF Vehicle Master", self.vehicle_number):
			return

		vehicle = frappe.get_doc("BBF Vehicle Master", self.vehicle_number)
		vehicle.total_trips = (vehicle.total_trips or 0) + 1
		vehicle.last_visit_date = getdate()

		if self.total_turnaround_minutes:
			prev_trips = max(vehicle.total_trips - 1, 0)
			prev_total = (vehicle.avg_turnaround_minutes or 0) * prev_trips
			vehicle.avg_turnaround_minutes = round(
				(prev_total + self.total_turnaround_minutes) / vehicle.total_trips, 1
			)

		vehicle.save(ignore_permissions=True)

	def _update_transport_master(self):
		transporter_name = frappe.db.get_value(
			"BBF Gate Entry", {"token_number": self.name}, "transporter"
		)
		if not transporter_name or not frappe.db.exists("BBF Transport Master", transporter_name):
			return

		transporter = frappe.get_doc("BBF Transport Master", transporter_name)
		transporter.total_trips = (transporter.total_trips or 0) + 1
		transporter.last_trip_date = getdate()

		if self.total_turnaround_minutes:
			prev_trips = max(transporter.total_trips - 1, 0)
			prev_total = (transporter.avg_turnaround_minutes or 0) * prev_trips
			transporter.avg_turnaround_minutes = round(
				(prev_total + self.total_turnaround_minutes) / transporter.total_trips, 1
			)

		transporter.save(ignore_permissions=True)
