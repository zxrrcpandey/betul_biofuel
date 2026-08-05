import random
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, time_diff_in_seconds, getdate, nowtime, flt, cint


# v2.8.3 Two-Pass Gate Flow role constants
TWO_PASS_G2_ROLE = "G2 Gate Operator"
TWO_PASS_G1_ROLE = "G1 Security"

# v2.18.0 Non-Raw-Material Stores-approved exit
NON_RM_PURPOSE = "Non-Raw Material"
STORES_EXIT_APPROVE_ROLES = ("Stores User", "Stores Manager")
# Statuses that mean the vehicle has already left — never re-process exit from these.
_EXITED_STATUSES = ("Plant Exited", "Campus Exited", "Exited")


def _two_pass_flag_on():
	"""v2.8.3: feature flag for two-pass material gate flow.
	Fail-closed: any exception treated as OFF (legacy behavior preserved).
	Lesson 171/172: tabSingles has no `modified` column; Check fields return 0 not None."""
	try:
		return bool(frappe.db.get_single_value("TS Settings", "ts_two_pass_gates_enabled"))
	except Exception:
		return False


class TSToken(Document):
	def before_insert(self):
		self._check_blacklist()
		self.generate_token_number()
		# Post-dated entry: use user-provided dates if set, otherwise current
		if not self.entry_date:
			self.entry_date = getdate()
		if not self.entry_time:
			self.entry_time = nowtime()
		if not self.g1_entry_time:
			self.g1_entry_time = now_datetime()
		self.status = "Token Generated"

		# Gate Pass: set initial status
		if self.entry_type == "Gate Pass":
			self.gate_pass_status = "Inside Campus"

			# Auto-fill from TS Visitor master if linked
			if self.visitor and not self.visitor_name:
				visitor = frappe.get_doc("TS Visitor", self.visitor)
				self.visitor_name = visitor.visitor_name
				self.visitor_company = self.visitor_company or visitor.visitor_company
				self.contact_number = self.contact_number or visitor.contact_number
				self.id_proof_type = self.id_proof_type or visitor.id_proof_type
				self.id_proof_number = self.id_proof_number or visitor.id_proof_number
				if not self.visitor_photo and visitor.visitor_photo:
					self.visitor_photo = visitor.visitor_photo

			# Auto-create TS Visitor master for new visitors
			if not self.visitor and self.visitor_name:
				existing = frappe.db.get_value(
					"TS Visitor",
					{"visitor_name": self.visitor_name, "contact_number": self.contact_number or ""},
					"name"
				)
				if existing:
					self.visitor = existing
				else:
					new_visitor = frappe.get_doc({
						"doctype": "TS Visitor",
						"visitor_name": self.visitor_name,
						"visitor_company": self.visitor_company,
						"contact_number": self.contact_number,
						"id_proof_type": self.id_proof_type,
						"id_proof_number": self.id_proof_number,
						"visitor_photo": self.visitor_photo,
						"first_visit_date": getdate(),
					})
					new_visitor.insert(ignore_permissions=True)
					self.visitor = new_visitor.name

			# Admin Reception: auto-set destination to Admin Office
			if frappe.session.user != "Administrator":
				user_roles = set(frappe.get_roles())
				is_admin_reception = "Admin Reception" in user_roles
				is_g1 = "G1 Security" in user_roles
				if is_admin_reception and not is_g1 and not self.destination:
					self.destination = "Admin Office"

		# Auto-fill host name from destination default
		if self.entry_type == "Gate Pass" and self.destination:
			default_host = frappe.db.get_value(
				"TS Gate Pass Destination", self.destination, "default_host"
			)
			if default_host and not self.host_name:
				self.host_name = default_host

	def _check_blacklist(self):
		"""Block token creation for blacklisted vehicles or drivers.
		vehicle_number is now Data field — lookup by name match in Vehicle Master.
		Driver check only if driver Link is set (filled at G2).
		"""
		if self.vehicle_number:
			# vehicle_number is Data — check if it exists in Vehicle Master and is blacklisted
			bl = frappe.db.get_value(
				"TS Vehicle Master", self.vehicle_number,
				["is_blacklisted", "blacklist_reason"], as_dict=True
			)
			if bl and bl.is_blacklisted:
				reason = bl.blacklist_reason or "No reason specified"
				frappe.throw(
					f"Vehicle <b>{self.vehicle_number}</b> is blacklisted and cannot enter the premises.<br><br>"
					f"<b>Reason:</b> {reason}<br><br>"
					f"Contact IT Head to resolve.",
					title="Blacklisted Vehicle"
				)

		if self.driver:
			bl = frappe.db.get_value(
				"TS Driver Master", self.driver,
				["is_blacklisted", "blacklist_reason"], as_dict=True
			)
			if bl and bl.is_blacklisted:
				reason = bl.blacklist_reason or "No reason specified"
				frappe.throw(
					f"Driver <b>{self.driver}</b> is blacklisted and cannot enter the premises.<br><br>"
					f"<b>Reason:</b> {reason}<br><br>"
					f"Contact IT Head to resolve.",
					title="Blacklisted Driver"
				)

	def generate_token_number(self):
		# Format: BBPL-TKN-YYYY-NNNNN (sequential per year)
		year = getdate(self.entry_date).strftime("%Y") if self.entry_date else getdate().strftime("%Y")
		prefix = f"BBPL-TKN-{year}-"

		# Get next sequential number
		last = frappe.db.sql(
			"SELECT name FROM `tabTS Token` WHERE name LIKE %s ORDER BY name DESC LIMIT 1",
			prefix + "%", as_list=True
		)

		if last and last[0][0]:
			try:
				last_num = int(last[0][0].split("-")[-1])
			except (ValueError, IndexError):
				last_num = 0
		else:
			last_num = 0

		next_num = last_num + 1
		token = f"{prefix}{next_num:05d}"

		# Safety: ensure uniqueness
		for _ in range(100):
			if not frappe.db.exists("TS Token", token):
				self.token_number = token
				return
			next_num += 1
			token = f"{prefix}{next_num:05d}"

		frappe.throw("Could not generate unique token number. Please try again.")

	def validate(self):
		# Post-dated entry: validate date (past dates need approval, future dates blocked)
		if self.entry_date and getdate(self.entry_date) != getdate():
			from trustbit_ethanol.ts_gate_entry.ts_post_dated import validate_post_dated_date
			validate_post_dated_date("TS Token", self.entry_date)

		if self.entry_type == "Material":
			if not self.vehicle_number:
				frappe.throw("Vehicle Number is required for Material entries")
			self.calculate_turnaround()

		# Gate Pass validation
		if self.entry_type == "Gate Pass":
			if not self.visitor_name:
				frappe.throw("Visitor Name is required for Gate Pass")
			if not self.visit_purpose:
				frappe.throw("Visit Purpose is required for Gate Pass")
			if not self.destination:
				frappe.throw("Destination is required for Gate Pass")

	def before_save(self):
		# v2.18.0: protect the Non-RM exit-approval control-plane fields from REST
		# tamper. These permlevel-0 fields are set ONLY by approve_non_rm_exit (via
		# db_set, which skips save hooks), so this guard fires only on unauthorised
		# frappe.client.set_value mutations (Lesson 162/176). Administrator / System
		# Manager bypass is built into the shared helper (REUSE, not rebuild).
		from trustbit_ethanol.ts_gate_entry.ts_po_approval import _block_gate_field_tampering
		_block_gate_field_tampering(self, (
			"non_rm_exit_approved",
			"non_rm_exit_approved_by",
			"non_rm_exit_approved_at",
		))

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

		if self.stock_direction == "Stock OUT":
			frappe.throw("GRN cannot be created for Stock OUT entries. Use Mark Exit to create Delivery Note.")

		allowed_roles = {"Accounts Manager", "Accounts User", "Stores User", "IT Head", "System Manager"}
		if not allowed_roles.intersection(set(frappe.get_roles())):
			frappe.throw("You do not have permission to create GRN")

		if self.status != "Tare Weighed":
			frappe.throw("GRN can only be created when status is 'Tare Weighed'")

		if self.purchase_receipt:
			frappe.throw(f"Purchase Receipt {self.purchase_receipt} already exists for this token")

		# Material Inspection gate — guarded by TS Settings.block_grn_on_inspection_pending.
		# v2.8.1 Phase B: this flag should be OFF; QC enforcement moves to PI validate hook.
		# Rejected is STILL blocked unconditionally (rejected stock should not enter warehouse).
		if self.purpose != "Raw Material":
			from trustbit_ethanol.ts_gate_entry.doctype.ts_material_inspection.ts_material_inspection import get_inspection_status_for_token
			insp = get_inspection_status_for_token(self.name)
			if insp:
				if insp.status == "Rejected":
					# Rejected always blocks — no amount of flag flipping allows rejected stock through
					frappe.throw("GRN cannot be created — material inspection has been rejected. Please resolve the inspection first.")

				# Pending / On Hold block only when the legacy flag is ON (v2.7 behavior)
				block_on_inspection = frappe.db.get_single_value("TS Settings", "block_grn_on_inspection_pending")
				if block_on_inspection:
					if insp.status == "On Hold":
						frappe.throw("GRN cannot be created — material inspection is on hold. Please resolve held items first.")
					elif insp.status == "Pending Inspection":
						frappe.throw("GRN cannot be created — material inspection is still pending. Please wait for inspection approval, turn off 'Block GRN on Inspection Pending' in TS Settings, or contact IT Head.")

		# Gather all linked data
		gate_entry = frappe.db.get_value(
			"TS Gate Entry",
			{"token_number": self.name, "docstatus": 1},
			["name", "purchase_order", "transporter", "lr_number", "lr_date", "supplier_name"],
			as_dict=True
		)
		if not gate_entry:
			frappe.throw("No submitted Gate Entry found for this token")
		if not gate_entry.purchase_order:
			frappe.throw("No Purchase Order linked in the Gate Entry")

		# Get primary PO for PR header (supplier, company, currency)
		po = frappe.get_doc("Purchase Order", gate_entry.purchase_order)
		if po.docstatus != 1:
			frappe.throw(f"Purchase Order {po.name} is not submitted")

		# Get weighbridge net weight
		wb_log = frappe.db.get_value(
			"TS Weighbridge Log",
			{"token_number": self.name},
			["gross_weight", "tare_weight", "net_weight"],
			as_dict=True
		)
		if not wb_log or not wb_log.net_weight:
			frappe.throw("Weighbridge Log with net weight not found. Ensure both gross and tare weights are recorded.")

		# Get deduction sheet if exists
		deduction_sheet = frappe.db.get_value(
			"TS Deduction Sheet",
			{"token_number": self.name, "status": "Approved"},
			["name", "net_weight", "net_payable", "total_deduction", "item_rate"],
			as_dict=True
		)

		# Get settings
		settings = frappe.get_single("TS Settings")
		warehouse = settings.default_warehouse
		accepted_warehouse = settings.default_accepted_warehouse

		if not warehouse:
			frappe.throw("Please set Default Warehouse in TS Settings before creating GRN")

		# Build Purchase Receipt items from PO items
		pr_items = []
		gate_entry_items = frappe.get_all(
			"TS Gate Entry Item",
			filters={"parent": gate_entry.name},
			fields=["item_code", "item_name", "ordered_qty", "uom", "purchase_order"]
		)

		if not gate_entry_items:
			frappe.throw("No items found in Gate Entry")

		net_weight_kg = flt(wb_log.net_weight)

		for idx, ge_item in enumerate(gate_entry_items):
			# For single-item deliveries (most common), use full net weight
			# For multi-item, proportionally distribute
			if len(gate_entry_items) == 1:
				weight_kg = net_weight_kg
			else:
				total_ordered = sum(flt(i.ordered_qty) for i in gate_entry_items)
				proportion = flt(ge_item.ordered_qty) / total_ordered if total_ordered else 1
				weight_kg = net_weight_kg * proportion

			item_uom = ge_item.uom or "Kg"

			# Convert KG to PO's UOM
			if item_uom == "Kg":
				received_qty = weight_kg
			else:
				conversion_factor = self._get_uom_conversion_to_kg(ge_item.item_code, item_uom)
				if conversion_factor and flt(conversion_factor) > 0:
					received_qty = flt(weight_kg / conversion_factor, 3)
				else:
					# No conversion found — use KG as-is and log warning
					received_qty = weight_kg
					item_uom = "Kg"
					frappe.msgprint(
						f"No UOM conversion found for {ge_item.item_code}: {ge_item.uom} → Kg. Using KG as quantity.",
						indicator="orange"
					)

			# Get rate: per-item from its own PO, fallback to deduction sheet rate
			item_po_name = ge_item.purchase_order or po.name
			po_item = frappe.db.get_value(
				"Purchase Order Item",
				{"parent": item_po_name, "item_code": ge_item.item_code},
				["name", "rate"],
				as_dict=True
			)

			if po_item:
				item_rate = flt(po_item.rate)
				po_item_name = po_item.name
			else:
				item_rate = flt(deduction_sheet.item_rate) if deduction_sheet and deduction_sheet.item_rate else 0
				po_item_name = None

			# Fetch ts_delivery_location and ts_item_remark from PO Item (fetch_from doesn't trigger in programmatic creation)
			ts_delivery_location = ""
			ts_item_remark = ""
			if po_item_name:
				ts_delivery_location = frappe.db.get_value("Purchase Order Item", po_item_name, "ts_delivery_location") or ""
				ts_item_remark = frappe.db.get_value("Purchase Order Item", po_item_name, "ts_item_remark") or ""

			# v2.8.1.2: propagate PO HEADER project to PR item (uniform across rows).
			# ERPNext PR validation rejects mixed project values in the same PR.
			item_po_project = frappe.db.get_value("Purchase Order", item_po_name, "project") or po.project or ""

			pr_item = {
				"item_code": ge_item.item_code,
				"item_name": ge_item.item_name,
				"qty": received_qty,
				"uom": item_uom,
				"stock_uom": item_uom,
				"rate": item_rate,
				"warehouse": accepted_warehouse or warehouse,
				"purchase_order": item_po_name,
				"purchase_order_item": po_item_name,
				"project": item_po_project,
				"ts_delivery_location": ts_delivery_location,
				"ts_item_remark": ts_item_remark,
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
			"ts_token": self.name,
			"ts_gate_entry": gate_entry.name,
			"lr_no": gate_entry.lr_number or "",
			"lr_date": gate_entry.lr_date,
			"transporter_name": gate_entry.transporter or "",
		})

		# v2.8.1.3: copy missing PO header fields that ERPNext's make_purchase_receipt
		# mapper would normally propagate (tax template, project, cost center, terms,
		# payment terms, addresses, contact, discount, taxes child table).
		# v2.8.2: `self` (Token) passed so RST Number flows through to PR.
		from trustbit_ethanol.ts_gate_entry.stores_receiving_api import _copy_po_header_fields
		_copy_po_header_fields(pr, po, self)

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
		# v2.9.8.20: Raw Material tokens ALWAYS use the two-step exit
		# (Record G2 Exit → Record G1 Final Exit). mark_exit is for
		# (a) Gate Pass tokens (visitors), and (b) non-Raw-Material Material
		# tokens that don't go through GRN — e.g. Stock OUT or short-haul
		# vehicles without weighing. Reject Raw Material callers with a clear
		# message pointing at the new buttons.
		if self.entry_type == "Material" and (self.purpose == "Raw Material" or _two_pass_flag_on()):
			frappe.throw(
				"This token uses the two-step gate exit flow. Use 'Record G2 Exit' "
				"(when status = GRN Created) followed by 'Record G1 Final Exit' "
				"(when status = Plant Exited)."
			)

		# Prevent double-exit
		if self.entry_type == "Gate Pass":
			if self.gate_pass_status == "Exited":
				frappe.throw("This gate pass is already marked as exited")
			if self.gate_pass_status == "Inside Plant":
				frappe.throw("Visitor is still inside the plant. Please log G2 exit first.")
		else:
			if self.status in ("Exited", "Campus Exited"):
				frappe.throw("This token is already marked as exited")

		# Admin Reception can mark exit too
		if self.entry_type == "Gate Pass":
			allowed_roles = {"G1 Security", "Admin Reception", "IT Head", "System Manager"}
			if not allowed_roles.intersection(set(frappe.get_roles())):
				frappe.throw("You do not have permission to mark exit")

		# Material tokens: exit restrictions based on purpose, weighing, and stock direction
		if self.entry_type == "Material":
			if not self.purpose:
				frappe.throw("This token has no purpose set. Gate Entry must be submitted at G2 before exit.")

			# Stock OUT: must have completed weighbridge (Gross Recorded or Dispatch Ready)
			if self.stock_direction == "Stock OUT":
				if self.status not in ("Gross Recorded", "Dispatch Ready"):
					frappe.throw("Stock OUT vehicle must complete weighbridge (Tare + Loading + Gross) before exit.")
			elif self.purpose == "Raw Material" and self.status != "GRN Created":
				frappe.throw("Raw Material tokens can only be marked as exited after GRN is created")
			elif self.purpose != "Raw Material":
				# Non-RM with requires_weighing: must complete weighbridge
				requires_weighing = frappe.db.get_value(
					"TS Gate Entry",
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
			# Update TS Visitor stats
			self._update_visitor_stats(campus_minutes)
		else:
			# Stock OUT: create Delivery Note on exit
			if self.stock_direction == "Stock OUT":
				self._create_delivery_note()

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
			"TS Gate Pass Destination", self.destination, "has_g2_checkpoint"
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

	def _get_uom_conversion_to_kg(self, item_code, uom):
		"""Get how many KG per 1 unit of the given UOM.
		Returns conversion factor (e.g., Quintal → 100, MT → 1000).
		"""
		from frappe.utils import flt

		# 1. Check item-level UOM conversion
		if item_code:
			conversions = frappe.get_all(
				"UOM Conversion Detail",
				filters={"parent": item_code},
				fields=["uom", "conversion_factor"]
			)
			conv_map = {c.uom: flt(c.conversion_factor) for c in conversions}

			if uom in conv_map and "Kg" in conv_map:
				# conversion_factor is relative to stock_uom
				# e.g., if stock_uom=Quintal: Kg=0.01, Quintal=1
				# So 1 Quintal = 1/0.01 = 100 Kg
				uom_factor = conv_map[uom]
				kg_factor = conv_map["Kg"]
				if kg_factor > 0:
					return uom_factor / kg_factor

		# 2. Check global UOM Conversion Factor
		global_factor = frappe.db.get_value(
			"UOM Conversion Factor",
			{"from_uom": uom, "to_uom": "Kg"},
			"value"
		)
		if global_factor and flt(global_factor) > 0:
			return flt(global_factor)

		# Reverse lookup
		reverse_factor = frappe.db.get_value(
			"UOM Conversion Factor",
			{"from_uom": "Kg", "to_uom": uom},
			"value"
		)
		if reverse_factor and flt(reverse_factor) > 0:
			return 1 / flt(reverse_factor)

		# 3. Known conversions fallback
		known = {"Quintal": 100, "MT": 1000, "Ton": 1000, "Gram": 0.001}
		return known.get(uom)

	def _create_delivery_note(self):
		"""Create Delivery Note for Stock OUT on exit."""
		gate_entry = frappe.db.get_value(
			"TS Gate Entry",
			{"token_number": self.name, "docstatus": 1},
			["name", "sales_invoice"],
			as_dict=True
		)
		if not gate_entry or not gate_entry.sales_invoice:
			frappe.throw("No submitted Gate Entry with Sales Invoice found for this token")

		si = frappe.get_doc("Sales Invoice", gate_entry.sales_invoice)

		# Get weighbridge net weight
		wb_log = frappe.db.get_value(
			"TS Weighbridge Log",
			{"token_number": self.name},
			["gross_weight", "tare_weight", "net_weight"],
			as_dict=True
		)

		# Build DN items from SI items
		dn_items = []
		gate_entry_items = frappe.get_all(
			"TS Gate Entry Item",
			filters={"parent": gate_entry.name},
			fields=["item_code", "item_name", "ordered_qty", "uom"]
		)

		net_weight_kg = flt(wb_log.net_weight) if wb_log and wb_log.net_weight else 0

		for ge_item in gate_entry_items:
			# Proportional weight distribution
			if len(gate_entry_items) == 1:
				weight_kg = net_weight_kg
			else:
				total_ordered = sum(flt(i.ordered_qty) for i in gate_entry_items)
				proportion = flt(ge_item.ordered_qty) / total_ordered if total_ordered else 1
				weight_kg = net_weight_kg * proportion

			item_uom = ge_item.uom or "Kg"
			if item_uom == "Kg":
				qty = weight_kg
			else:
				conversion_factor = self._get_uom_conversion_to_kg(ge_item.item_code, item_uom)
				if conversion_factor and flt(conversion_factor) > 0:
					qty = flt(weight_kg / conversion_factor, 3)
				else:
					qty = weight_kg
					item_uom = "Kg"

			# Find matching SI item for rate and reference
			si_item = frappe.db.get_value(
				"Sales Invoice Item",
				{"parent": si.name, "item_code": ge_item.item_code},
				["name", "rate", "warehouse"],
				as_dict=True
			)

			dn_items.append({
				"item_code": ge_item.item_code,
				"item_name": ge_item.item_name,
				"qty": qty if qty > 0 else flt(ge_item.ordered_qty),
				"uom": item_uom,
				"rate": flt(si_item.rate) if si_item else 0,
				"warehouse": si_item.warehouse if si_item else None,
				"against_sales_invoice": si.name,
				"si_detail": si_item.name if si_item else None,
			})

		settings = frappe.get_single("TS Settings")

		dn = frappe.get_doc({
			"doctype": "Delivery Note",
			"customer": si.customer,
			"customer_name": si.customer_name,
			"posting_date": getdate(),
			"company": si.company,
			"currency": si.currency,
			"selling_price_list": si.selling_price_list,
			"set_warehouse": settings.default_warehouse,
			"items": dn_items,
		})

		dn.flags.ignore_permissions = True
		dn.insert()
		dn.submit()

		self.db_set("delivery_note", dn.name)

		frappe.msgprint(
			f"Delivery Note <b>{dn.name}</b> created and submitted",
			title="Delivery Note Created",
			indicator="green"
		)

	def _update_vehicle_master(self):
		if not self.vehicle_number or not frappe.db.exists("TS Vehicle Master", self.vehicle_number):
			return

		vehicle = frappe.get_doc("TS Vehicle Master", self.vehicle_number)
		vehicle.total_trips = (vehicle.total_trips or 0) + 1
		vehicle.last_visit_date = getdate()

		if self.total_turnaround_minutes:
			prev_trips = max(vehicle.total_trips - 1, 0)
			prev_total = (vehicle.avg_turnaround_minutes or 0) * prev_trips
			vehicle.avg_turnaround_minutes = round(
				(prev_total + self.total_turnaround_minutes) / vehicle.total_trips, 1
			)

		vehicle.save(ignore_permissions=True)

	def _update_visitor_stats(self, campus_minutes):
		"""Update TS Visitor master with visit statistics."""
		if not self.visitor or not frappe.db.exists("TS Visitor", self.visitor):
			return

		visitor = frappe.get_doc("TS Visitor", self.visitor)
		visitor.total_visits = (visitor.total_visits or 0) + 1
		visitor.last_visit_date = getdate()

		if not visitor.first_visit_date:
			visitor.first_visit_date = getdate()

		if campus_minutes and campus_minutes > 0:
			prev_visits = max(visitor.total_visits - 1, 0)
			prev_total = (visitor.average_duration_minutes or 0) * prev_visits
			visitor.average_duration_minutes = round(
				(prev_total + campus_minutes) / visitor.total_visits, 1
			)

		visitor.save(ignore_permissions=True)

	def _update_transport_master(self):
		transporter_name = frappe.db.get_value(
			"TS Gate Entry", {"token_number": self.name}, "transporter"
		)
		if not transporter_name or not frappe.db.exists("TS Transport Master", transporter_name):
			return

		transporter = frappe.get_doc("TS Transport Master", transporter_name)
		transporter.total_trips = (transporter.total_trips or 0) + 1
		transporter.last_trip_date = getdate()

		if self.total_turnaround_minutes:
			prev_trips = max(transporter.total_trips - 1, 0)
			prev_total = (transporter.avg_turnaround_minutes or 0) * prev_trips
			transporter.avg_turnaround_minutes = round(
				(prev_total + self.total_turnaround_minutes) / transporter.total_trips, 1
			)

		transporter.save(ignore_permissions=True)


# =============================================================================
# v2.8.3 Two-Pass Gate Flow — module-level whitelisted POST APIs
# -----------------------------------------------------------------------------
# These methods own the final 3 status transitions when `ts_two_pass_gates_enabled`
# is ON. All mutation endpoints declare methods=["POST"] (Lesson 175).
# Role checks are enforced server-side (IT Head / System Manager retain break-glass
# override). No `allow_guest` — operators authenticate via standard Frappe session.
# =============================================================================


@frappe.whitelist(methods=["POST"])
def g2_mat_log_entry(token_name):
	"""Record G2 Entry for a Material token.

	Role: G2 Gate Operator (IT Head / System Manager may override).
	Required prior status: 'G1 Entered' (flag ON) or legacy 'PO Linked' (backward compat).
	Writes: g2_mat_entry_time, g2_mat_entry_by, status='G2 Entered'.
	"""
	# v2.8.3 Phase 4 Fix 7: hard existence check before any load (defence in depth).
	if not frappe.db.exists("TS Token", token_name):
		frappe.throw(_("Token {0} not found").format(token_name))
	user_roles = set(frappe.get_roles())
	if TWO_PASS_G2_ROLE not in user_roles and "IT Head" not in user_roles and "System Manager" not in user_roles:
		frappe.throw(_("Only G2 Gate Operator can record G2 entry"), frappe.PermissionError)
	tok = frappe.get_doc("TS Token", token_name)
	if tok.entry_type != "Material":
		frappe.throw(_("G2 material entry is only valid for Material tokens (not Gate Pass)."))
	valid_prev = ("G1 Entered", "PO Linked")
	if tok.status not in valid_prev:
		frappe.throw(_("Token {0} is at '{1}' — cannot record G2 Entry (expected 'G1 Entered').").format(token_name, tok.status))
	tok.db_set({
		"g2_mat_entry_time": now_datetime(),
		"g2_mat_entry_by": frappe.session.user,
		"status": "G2 Entered",
	})
	return {"ok": True, "token": token_name, "status": "G2 Entered"}


@frappe.whitelist(methods=["POST"])
def g2_mat_log_exit(token_name):
	"""Record G2 Exit (first exit checkpoint — leaving the plant area).

	Role: G2 Gate Operator (IT Head / System Manager may override).
	Required prior status: 'Tare Weighed' or 'GRN Created'.
	Writes: g2_mat_exit_time, g2_mat_exit_by, status='Plant Exited'.
	"""
	# v2.8.3 Phase 4 Fix 7: hard existence check before any load (defence in depth).
	if not frappe.db.exists("TS Token", token_name):
		frappe.throw(_("Token {0} not found").format(token_name))
	user_roles = set(frappe.get_roles())
	if TWO_PASS_G2_ROLE not in user_roles and "IT Head" not in user_roles and "System Manager" not in user_roles:
		frappe.throw(_("Only G2 Gate Operator can record G2 exit"), frappe.PermissionError)
	tok = frappe.get_doc("TS Token", token_name)
	if tok.entry_type != "Material":
		frappe.throw(_("G2 material exit is only valid for Material tokens (not Gate Pass)."))
	# Grain deferred-linking HOLD (Option 2, ts_grain_defer). A grain truck that
	# skipped PO linking at G2 (its Gate Entry has ts_po_deferred=1) cannot record
	# G2 Exit until Stores links the PO and the GRN is created — that stamps
	# token.purchase_receipt, which releases this hold. Keyed strictly on the
	# marker (via ts_grain_defer.exit_hold_active) so every non-grain / non-deferred
	# token is byte-unaffected.
	from trustbit_ethanol.ts_gate_entry.ts_grain_defer import exit_hold_active
	if exit_hold_active(tok):
		from frappe.utils import escape_html
		frappe.throw(
			_(
				"Vehicle {0}: the grain Purchase Order is not linked yet. Stores must "
				"link the PO and create the GRN (Stores Receiving Dashboard → "
				"Grain — Awaiting PO Link) before this vehicle can record G2 Exit."
			).format(escape_html(tok.vehicle_number or token_name)),
			title=_("Awaiting PO Link"),
		)
	# v2.18.0: a Non-Raw-Material token that Stores approved for exit may record
	# G2 Exit from its natural resting status (no weighbridge / GRN needed). The
	# Raw-Material and weighed flows keep the original Tare Weighed / GRN Created
	# gate untouched.
	_non_rm_approved = (
		cint(getattr(tok, "non_rm_exit_approved", 0))
		and _non_rm_flow_confirmed(token_name)
		and tok.status not in _EXITED_STATUSES
	)
	if tok.status not in ("Tare Weighed", "GRN Created") and not _non_rm_approved:
		frappe.throw(_("Token {0} is at '{1}' — cannot record G2 Exit (expected 'Tare Weighed'/'GRN Created', or a Stores-approved Non-Raw-Material token).").format(token_name, tok.status))
	tok.db_set({
		"g2_mat_exit_time": now_datetime(),
		"g2_mat_exit_by": frappe.session.user,
		"status": "Plant Exited",
	})
	return {"ok": True, "token": token_name, "status": "Plant Exited"}


@frappe.whitelist(methods=["POST"])
def g1_final_exit(token_name):
	"""Record G1 Final Exit (vehicle physically leaves the campus — terminal).

	Role: G1 Security (IT Head / System Manager / Admin Reception may override).
	Required prior status: 'Plant Exited' (when flag ON). When flag OFF we accept
	legacy 'Tare Weighed' / 'GRN Created' / 'Exited' / 'Campus Exited' so existing
	tokens aren't stranded. Always transitions status to 'Campus Exited'.
	"""
	# v2.8.3 Phase 4 Fix 7: hard existence check before any load (defence in depth).
	if not frappe.db.exists("TS Token", token_name):
		frappe.throw(_("Token {0} not found").format(token_name))
	user_roles = set(frappe.get_roles())
	if (TWO_PASS_G1_ROLE not in user_roles
			and "IT Head" not in user_roles
			and "System Manager" not in user_roles
			and "Admin Reception" not in user_roles):
		frappe.throw(_("Only G1 Security can record final G1 exit"), frappe.PermissionError)
	tok = frappe.get_doc("TS Token", token_name)
	if tok.entry_type != "Material":
		frappe.throw(_("G1 final exit API is only for Material tokens (use mark_exit for Gate Pass)."))
	# v2.9.8.20: G1 Final Exit is now ALWAYS the second step of Material exit —
	# always require status == "Plant Exited" (set by g2_mat_log_exit). Removed
	# the flag-aware fallback that accepted legacy single-pass statuses; the
	# legacy mark_exit path is now blocked for Raw Material tokens entirely
	# (see mark_exit() below) so this endpoint is the only path to Campus Exited.
	if tok.status != "Plant Exited":
		frappe.throw(_("Token {0} is at '{1}' — G2 Exit must be recorded first (expected 'Plant Exited').").format(token_name, tok.status))
	tok.db_set({
		"g1_exit_time": now_datetime(),
		"status": "Campus Exited",
	})
	# Refresh vehicle/transport masters to capture turnaround even on the new path
	try:
		tok.reload()
		tok._update_vehicle_master()
		tok._update_transport_master()
	except Exception as e:
		frappe.log_error(message=f"g1_final_exit post-write update: {e}", title="g1_final_exit")
	return {"ok": True, "token": token_name, "status": "Campus Exited"}


def _non_rm_flow_confirmed(token_name):
	"""The token's `purpose` is permlevel-0 and REST-writable by gate roles, so
	it must never solely gate the weighbridge/GRN-free exit (security H1, 5 Aug).
	The SUBMITTED Gate Entry's material_flow is the tamper-resistant source —
	reqd, immutable after submit, and the very value update_token_status mirrors
	into purpose. Fail-closed: no submitted Non-RM Gate Entry (or it was
	cancelled later) → not a Non-RM flow."""
	return bool(frappe.db.exists(
		"TS Gate Entry",
		{"token_number": token_name, "docstatus": 1, "material_flow": NON_RM_PURPOSE},
	))


@frappe.whitelist(methods=["POST"])
def approve_non_rm_exit(token_name):
	"""v2.18.0: Stores approves a Non-Raw-Material vehicle to exit.

	Decouples the non-RM exit from the two-pass flag, the weighbridge, and the
	GRN: once approved, g2_mat_log_exit accepts the token (→ 'Plant Exited') and
	g1_final_exit releases it (→ 'Campus Exited'). Writes three permlevel-0
	control-plane fields via db_set (which skips save hooks); REST tamper of those
	fields is blocked by TSToken.before_save (Lesson 162/176).

	Role: Stores User / Stores Manager (IT Head / System Manager break-glass).
	Mutation endpoint → POST only (Lesson 175).
	"""
	# Defence in depth: hard existence check before any load (mirrors g2/g1 exit).
	if not frappe.db.exists("TS Token", token_name):
		frappe.throw(_("Token {0} not found").format(token_name))
	user_roles = set(frappe.get_roles())
	allowed = set(STORES_EXIT_APPROVE_ROLES) | {"IT Head", "System Manager"}
	if not (allowed & user_roles):
		frappe.throw(_("Only Stores can approve a Non-Raw-Material vehicle exit"), frappe.PermissionError)
	tok = frappe.get_doc("TS Token", token_name)
	if tok.entry_type != "Material":
		frappe.throw(_("Exit approval is only valid for Material tokens (not Gate Pass)."))
	# Resolve the flow from the SUBMITTED Gate Entry, never from tok.purpose —
	# purpose is REST-writable by gate roles, so trusting it would let a
	# re-labeled Raw-Material token skip the weighbridge/GRN via this path.
	if not _non_rm_flow_confirmed(token_name):
		frappe.throw(_("Token {0} has no submitted Non-Raw-Material Gate Entry — exit approval does not apply.").format(token_name))
	if tok.status in _EXITED_STATUSES:
		frappe.throw(_("Token {0} has already exited (status '{1}').").format(token_name, tok.status))
	if cint(getattr(tok, "non_rm_exit_approved", 0)):
		frappe.throw(_("Exit is already approved for token {0}.").format(token_name))
	tok.db_set({
		"non_rm_exit_approved": 1,
		"non_rm_exit_approved_by": frappe.session.user,
		"non_rm_exit_approved_at": now_datetime(),
	})
	return {"ok": True, "token": token_name, "approved": True}
