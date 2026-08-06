import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, flt, getdate, get_datetime, cint


class TSGateEntry(Document):
	def before_insert(self):
		# Own G2 stamp — entry_date/entry_time are fetched from the Token (G1
		# arrival), so this is the only field recording when G2 actually
		# processed the vehicle. Default: server clock — an API-supplied value
		# must not override the audit stamp. Sole exception: an active
		# Post-Dated window, where the operator records the real (past) G2
		# time for paper catch-up entries. Second precision: the desk control
		# round-trips without microseconds, so a microsecond stamp would make
		# every later re-save look like a change to _guard_g2_stamp_change.
		supplied = self.g2_entry_datetime
		self.g2_entry_datetime = now_datetime().replace(microsecond=0)
		if supplied:
			self._apply_post_dated_g2_stamp(supplied)

	def _apply_post_dated_g2_stamp(self, supplied):
		"""Keep a client-supplied G2 stamp only under an active Post-Dated
		window. No window → keep the server stamp silently (REST/import
		inserts never errored on this field). Window active → the form field
		is editable, so a bad value must THROW: silently replacing what the
		operator typed would ship a stamp they never saw."""
		from trustbit_ethanol.ts_gate_entry.ts_post_dated import (
			check_post_dated_access,
			validate_post_dated_date,
		)
		access = check_post_dated_access("TS Gate Entry", self.token_number)
		if not access.get("enabled"):
			return
		supplied = get_datetime(supplied).replace(microsecond=0)
		if supplied > now_datetime():
			frappe.throw(_("G2 Entry Date & Time cannot be in the future."))
		validate_post_dated_date("TS Gate Entry", supplied.date(), self.token_number)
		self.g2_entry_datetime = supplied

	def _guard_g2_stamp_change(self):
		"""g2_entry_datetime is server-owned: changing it on an existing doc
		is allowed ONLY inside an active Post-Dated window, never into the
		future. Gated on is_new() and NOT docstatus — submit runs validate
		with docstatus already 1, so a set-and-submit call would slip past a
		docstatus gate. Closes the v2.29.9 draft-REST-overwrite finding."""
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		old = get_datetime(before.g2_entry_datetime).replace(microsecond=0) if before.g2_entry_datetime else None
		new = get_datetime(self.g2_entry_datetime).replace(microsecond=0) if self.g2_entry_datetime else None
		if new == old:
			# A Transaction-wise window is scoped to ONE token, so a backdated
			# stamp must be re-authorized when the token link changes — else a
			# two-save token swap carries the stamp onto an unauthorized GE.
			token_changed = (self.token_number or "") != (before.token_number or "")
			if not token_changed or not new or new.date() >= getdate():
				return
		if not new:
			frappe.throw(_("G2 Entry Date & Time cannot be cleared."))
		from trustbit_ethanol.ts_gate_entry.ts_post_dated import (
			check_post_dated_access,
			validate_post_dated_date,
		)
		access = check_post_dated_access("TS Gate Entry", self.token_number)
		if not access.get("enabled"):
			frappe.throw(_("G2 Entry Date & Time can only be changed during an active Post-Dated Entry window."))
		if new > now_datetime():
			frappe.throw(_("G2 Entry Date & Time cannot be in the future."))
		validate_post_dated_date("TS Gate Entry", new.date(), self.token_number)
		self.g2_entry_datetime = new

	def validate(self):
		# Post-dated entry validation (past dates need approval, future dates blocked)
		if self.entry_date and getdate(self.entry_date) != getdate():
			from trustbit_ethanol.ts_gate_entry.ts_post_dated import validate_post_dated_date
			validate_post_dated_date("TS Gate Entry", self.entry_date, self.token_number)

		self._guard_g2_stamp_change()

		if self.stock_direction == "Stock OUT":
			self._validate_stock_out()
		else:
			self._force_raw_material_for_grain()
			self._sync_purchase_order_from_po_list()
			self._validate_same_supplier()
			self.validate_po_remaining_qty()
			self._set_grain_deferred_marker()
		self.set_route()
		self.validate_token_status()

	def _force_raw_material_for_grain(self):
		"""Grain (Maize/Rice/DORB) is always Raw Material — auto-set material_flow so
		the operator only picks the Material Type. Mirrors the form JS and keeps
		routing + the deferred marker consistent for any entry path (form/API/import)."""
		from trustbit_ethanol.ts_gate_entry.ts_grain_defer import GRAIN_MATERIAL_TYPES
		if self.ts_material_type in GRAIN_MATERIAL_TYPES and self.stock_direction != "Stock OUT":
			self.material_flow = "Raw Material"

	def _set_grain_deferred_marker(self):
		"""Server-authoritative grain-deferred flag, re-derived every save (so a
		client tamper is overwritten). Set when a grain Raw-Material Stock-IN has
		no PO linked yet; cleared otherwise. Drives the G2-exit hold + Section G."""
		from trustbit_ethanol.ts_gate_entry.ts_grain_defer import is_grain_stock_in
		has_po = bool(self.po_list) or bool(self.purchase_order)
		self.ts_po_deferred = 1 if (
			is_grain_stock_in(self.ts_material_type, self.material_flow, self.stock_direction)
			and not has_po
		) else 0

	@staticmethod
	def _assert_po_not_confidential(po_name):
		"""Confidentiality back-door plug (Lesson 297 / audit HIGH): block reading
		a CONFIDENTIAL Purchase Order by exact ID when the user is not on the
		allow-list. Non-confidential POs pass through, so normal PO linking is
		unaffected."""
		if not po_name:
			return
		from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential
		if user_sees_confidential("Purchase Order"):
			return
		if cint(frappe.db.get_value("Purchase Order", po_name, "ts_confidential")):
			frappe.throw(
				_("Purchase Order {0} is confidential and cannot be linked here.").format(po_name),
				frappe.PermissionError,
			)

	def _sync_purchase_order_from_po_list(self):
		"""Keep purchase_order field in sync with first PO in po_list for backward compatibility."""
		if self.po_list and len(self.po_list) > 0:
			self.purchase_order = self.po_list[0].purchase_order
			if not self.supplier_name:
				self.supplier_name = self.po_list[0].supplier_name
		elif self.purchase_order and (not self.po_list or len(self.po_list) == 0):
			# Legacy: single PO entered directly — auto-add to po_list
			self._assert_po_not_confidential(self.purchase_order)
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
			self._assert_po_not_confidential(row.purchase_order)
			po = frappe.get_doc("Purchase Order", row.purchase_order)
			if po.per_received >= 100:
				frappe.throw(f"Purchase Order {row.purchase_order} is already 100% received. Please remove it.")

	def on_submit(self):
		self._require_po_unless_deferred()
		self._check_blacklist_on_submit()
		self._copy_vehicle_driver_to_token()
		self.update_token_status()
		self.set_gate_entry_status()
		if self.stock_direction != "Stock OUT":
			self._create_material_inspection()

	def _require_po_unless_deferred(self):
		"""A Raw-Material Stock-IN must carry a Purchase Order at submit UNLESS it
		is grain-deferred (grain POs are linked later by Stores). Closes the hole
		where a non-grain vehicle silently skips PO linking → un-receivable truck."""
		if (self.stock_direction != "Stock OUT"
				and self.material_flow == "Raw Material"
				and not (self.po_list or self.purchase_order)
				and not cint(self.ts_po_deferred)):
			frappe.throw(_(
				"Link at least one Purchase Order before submitting this Raw-Material "
				"gate entry."
			))

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

	def on_cancel(self):
		"""v2.8.3.2: reset Token when Gate Entry is cancelled.

		Mirrors the existing `pr_on_cancel_clear_token` hook pattern.
		Behavior depends on Token's current status:

		- **Pre-weighing** (PO Linked / G1 Entered / G2 Entered / SI Linked) →
		  no WB Log created yet, so it's safe to reset Token.status back to
		  'Token Generated'. User can then amend the Gate Entry or create a
		  fresh one.
		- **Post-weighing** (Gross Weighed / Tare Weighed / Gross Recorded /
		  Tare Recorded / Loading Done / Dispatch Ready) → Weighbridge Log
		  exists with real weights; cancelling Gate Entry would orphan it.
		  Block with a clear instruction to delete the WB Log first.
		- **Post-GRN** (GRN Created / Plant Exited / Campus Exited / Exited) →
		  Purchase Receipt exists; the PR cancel hook owns the Token reset.
		  Block with instruction to cancel PR first.
		- **Already reset** (Token Generated) → nothing to do.

		Frappe rolls back the docstatus transition if this hook throws, so
		`frappe.throw` here correctly prevents an orphaning cancel.
		"""
		if not self.token_number:
			return
		token = frappe.db.get_value(
			"TS Token", self.token_number, ["name", "status"], as_dict=True
		)
		if not token:
			return

		PRE_WEIGHING = ("PO Linked", "G1 Entered", "G2 Entered", "SI Linked")
		POST_WEIGHING = (
			"Gross Weighed", "Tare Weighed",
			"Gross Recorded", "Tare Recorded",
			"Loading Done", "Dispatch Ready",
		)
		POST_GRN = ("GRN Created", "Plant Exited", "Campus Exited", "Exited")

		if token.status in PRE_WEIGHING:
			frappe.db.set_value("TS Token", token.name, "status", "Token Generated")
			frappe.msgprint(
				_("Token {0} has been reset to 'Token Generated'. You can now amend or re-enter.").format(token.name),
				alert=True, indicator="blue",
			)
		elif token.status in POST_WEIGHING:
			frappe.throw(_(
				"Cannot cancel Gate Entry — Token {0} is at '{1}'. "
				"A Weighbridge Log exists for this token. "
				"Delete the Weighbridge Log first (IT Head has delete permission), "
				"then cancel this Gate Entry."
			).format(token.name, token.status))
		elif token.status in POST_GRN:
			frappe.throw(_(
				"Cannot cancel Gate Entry — Token {0} is at '{1}'. "
				"A Purchase Receipt has been created. "
				"Cancel the Purchase Receipt first (the PR cancel will auto-reset "
				"the Token), then cancel this Gate Entry."
			).format(token.name, token.status))
		# If status is already "Token Generated" — no-op, safe to continue cancel.

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
			self._assert_po_not_confidential(po_name)
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

		self._assert_po_not_confidential(po_name)
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
