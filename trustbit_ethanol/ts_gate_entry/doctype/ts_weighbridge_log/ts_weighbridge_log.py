import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


# v2.8.2: roles permitted to amend RST Number after a Purchase Receipt
# has been created referencing it. Matches the pattern used for other
# control-plane overrides (Post-Dated Entry, PO Approval). CTO + IT Head
# are the human authorities; System Manager + Administrator are the
# break-glass roles. Do not widen without CTO sign-off.
RST_TAMPER_BYPASS_ROLES = {"CTO", "IT Head", "System Manager", "Administrator"}


def _is_flow_v28_enabled():
	"""v2.8.0 flow: Gross → Tare direct, no Unloading gate. Feature flag on TS Settings."""
	try:
		return bool(frappe.db.get_single_value("TS Settings", "ts_flow_v28_enabled"))
	except Exception:
		return False


def _is_rst_enforcement_enabled():
	"""v2.8.2: kill-switch for RST Number enforcement.

	Default OFF (0) at deploy per guardian recommendation — flip ON only
	after production rollout is verified. Lesson 171/172 guarded: `tabSingles`
	has no `modified` column and Check fields return 0 not None, so wrap
	in try/except and coerce via bool() for fail-closed safety (treat any
	exception as 'feature off', since the flag defaults to 0 anyway).
	"""
	try:
		return bool(frappe.db.get_single_value("TS Settings", "ts_rst_enforcement_enabled"))
	except Exception:
		return False


class TSWeighbridgeLog(Document):
	def before_insert(self):
		self.auto_fetch_from_gate_entry()
		self.validate_token_status()

	def auto_fetch_from_gate_entry(self):
		if not self.token_number:
			return

		gate_entry = frappe.db.get_value(
			"TS Gate Entry",
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
		token = frappe.db.get_value("TS Token", self.token_number, ["status", "stock_direction"], as_dict=True)
		if not token:
			return
		if token.stock_direction == "Stock OUT":
			# Stock OUT: accepted statuses
			if token.status not in ("SI Linked", "Tare Recorded", "Loading Done"):
				frappe.throw(f"Token {self.token_number} is at stage '{token.status}'. Stock OUT tokens need status 'SI Linked', 'Tare Recorded', or 'Loading Done'.")
		else:
			# v2.8.3: when two-pass flag ON, Stock IN WB log is accepted at
			# 'G2 Entered' (validate_two_pass_gate will enforce the hard gate
			# at Gross save). Legacy tokens still gated on 'PO Linked'.
			allowed = ("PO Linked", "G2 Entered")
			if token.status not in allowed:
				frappe.throw(
					f"Token {self.token_number} is at stage '{token.status}'. "
					f"Only tokens with status 'PO Linked' or 'G2 Entered' can be weighed."
				)

	def validate(self):
		self.set_operators()
		self.calculate_net_weight()
		self.fetch_po_uom()
		self.update_status()
		self.validate_rst_number()
		self.validate_two_pass_gate()

	def validate_two_pass_gate(self):
		"""v2.8.3: if two-pass flag ON, block WB Gross save unless the Token
		has passed G2 Entry (status == 'G2 Entered'). Backward compat:
		- flag OFF → skip (legacy flow).
		- no token_number / no gross_weight → skip (drafts, Tare-only rows).
		- not the first Gross save → skip (already past the gate; avoid
		  spurious throws on Tare update, operator corrections, etc.).
		"""
		try:
			flag = bool(frappe.db.get_single_value("TS Settings", "ts_two_pass_gates_enabled"))
		except Exception:
			flag = False
		if not flag:
			return
		if not self.token_number or not self.gross_weight:
			return
		if self._is_stock_out():
			return
		before = self.get_doc_before_save()
		prev_gross = (before.gross_weight if before else None)
		is_first_gross = bool(self.gross_weight) and not prev_gross
		if not is_first_gross:
			return
		token_status = frappe.db.get_value("TS Token", self.token_number, "status")
		if token_status != "G2 Entered":
			frappe.throw(
				_("Two-Pass Gate Flow: Token must be at status 'G2 Entered' before Gross weighing (currently '{0}'). Ask G2 Gate Operator to record G2 Entry first.").format(token_status)
			)

	def validate_rst_number(self):
		"""v2.8.2: RST Number validation — numeric, mandatory at Gross, unique, tamper-guarded.

		Gated by `ts_rst_enforcement_enabled` feature flag on TS Settings (default OFF).
		When flag is 0, all validation skipped — backward compat for 89 legacy tokens.

		Validation rules (flag ON):
		  1. If `gross_weight` is being set for the first time → RST required.
		  2. If RST present → must match `^[0-9]+$` (digits only).
		  3. If RST present → length 1..20.
		  4. If RST present → globally unique across TS Weighbridge Log.
		  5. If RST changed AND a non-cancelled PR references the OLD value →
		     block unless user has CTO / IT Head / System Manager / Administrator role.
		"""
		if not _is_rst_enforcement_enabled():
			return

		before = self.get_doc_before_save()

		# Rule 1 — mandatory when gross_weight transitions from empty to set
		gross_changed = self.has_value_changed("gross_weight")
		prev_gross = (before.gross_weight if before else None)
		is_first_gross = bool(self.gross_weight) and gross_changed and not prev_gross
		if is_first_gross and not self.rst_number:
			frappe.throw(_("RST Number is required to record Gross Weight"))

		if not self.rst_number:
			# Nothing more to validate if RST not set (covers pre-gross drafts).
			return

		# Rule 2 — numeric only
		if not re.match(r"^[0-9]+$", str(self.rst_number)):
			frappe.throw(_("RST Number must contain digits only (got '{0}').").format(self.rst_number))

		# Rule 3 — length 1..20
		rst_len = len(str(self.rst_number))
		if rst_len < 1 or rst_len > 20:
			frappe.throw(_("RST Number length must be between 1 and 20 digits (got {0}).").format(rst_len))

		# Rule 4 — global uniqueness (friendly error; DB unique index is the hard guarantee)
		existing_name = frappe.db.exists(
			"TS Weighbridge Log",
			{"rst_number": self.rst_number, "name": ["!=", self.name or ""]},
		)
		if existing_name:
			existing_token = frappe.db.get_value("TS Weighbridge Log", existing_name, "token_number") or "-"
			frappe.throw(_(
				"RST Number {0} already used on Weighbridge Log {1} (Token {2}). "
				"Please verify the slip."
			).format(self.rst_number, existing_name, existing_token))

		# Rule 5 — tamper guard post-PR
		if before and before.rst_number and before.rst_number != self.rst_number:
			pr_using_old = frappe.db.exists(
				"Purchase Receipt",
				{"custom_rst_number": before.rst_number, "docstatus": ["!=", 2]},
			)
			if pr_using_old:
				user_roles = set(frappe.get_roles(frappe.session.user))
				if not (RST_TAMPER_BYPASS_ROLES & user_roles):
					frappe.throw(_(
						"RST Number is locked — Purchase Receipt {0} already uses this RST. "
						"Contact CTO or IT Head to amend."
					).format(pr_using_old))

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
			self.tare_percent = round((self.tare_weight / self.gross_weight) * 100, 2) if self.gross_weight else 0
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
		purpose = frappe.db.get_value("TS Token", self.token_number, "purpose")
		if purpose != "Raw Material":
			ge = frappe.db.get_value(
				"TS Gate Entry",
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
			sd = frappe.db.get_value("TS Token", self.token_number, "stock_direction")
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
			# Stock IN: v2.8 flow is Gross → Tare direct (no Unload gate).
			# Legacy flow (flag off) keeps the unloading_complete gate.
			if self.tare_weight and self.gross_weight:
				self.status = "Completed"
			elif self.gross_weight and not self.tare_weight:
				if _is_flow_v28_enabled() or self._is_non_rm_weighing() or self.unloading_complete:
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
		"""Stock OUT: tare weight is recorded first (empty vehicle).

		v2.8.3.1: handle one-shot insert where BOTH tare AND gross are
		saved together (back-dated entries, bulk imports, hardware feeds).
		Without this, Token would get stuck at 'Tare Recorded' because
		on_update does NOT fire on the insert-only transaction.
		"""
		if self.tare_weight and self.token_number:
			self.db_set("tare_weight_time", now_datetime())
			self.db_set("tare_operator", frappe.session.user)

			token = frappe.get_doc("TS Token", self.token_number)
			token_updates = {
				"wb_tare_time": now_datetime(),
				"status": "Tare Recorded",
			}

			# v2.8.3.1: one-shot insert gross mirror for Stock OUT.
			# If gross_weight is also set at insert time, mirror it too so
			# Token reaches 'Gross Recorded' directly. Normal 2-save flow
			# leaves gross_weight empty at first save — this block is skipped.
			if self.gross_weight:
				self.db_set("gross_weight_time", now_datetime())
				self.db_set("gross_operator", frappe.session.user)
				token_updates["wb_gross_time"] = now_datetime()
				token_updates["status"] = "Gross Recorded"

			token.db_set(token_updates)

	def update_token_gross(self):
		if self.gross_weight and self.token_number:
			self.gross_weight_time = now_datetime()
			self.db_set("gross_weight_time", self.gross_weight_time)

			token = frappe.get_doc("TS Token", self.token_number)
			# v2.8.2: mirror RST Number onto Token (custom field) so it
			# propagates downstream to Purchase Receipt at GRN creation.
			token_updates = {
				"wb_gross_time": now_datetime(),
				"status": "Gross Weighed",
				"custom_rst_number": self.rst_number,
			}

			# v2.8.3.1: one-shot insert tare mirror for Stock IN.
			# If tare_weight is ALSO set at insert time (back-dated entry,
			# bulk import, or hardware feed that posts both weights together),
			# mirror the tare state to Token too. Without this the Token
			# would be stuck at 'Gross Weighed' and Stores Receiving Dashboard
			# wouldn't pick it up — exactly the BBPL-TKN-2026-00309 incident.
			#
			# Gated by the SAME flow conditions update_status() uses for the
			# Awaiting-Tare transition (v2.8 flag ON OR Non-RM weighing OR
			# unloading_complete). Legacy flow (v28=0) with no unloading
			# confirmation still respects the Unloading gate.
			#
			# Normal 2-save UI flow leaves tare_weight empty at first save,
			# so this block is skipped there — zero regression risk.
			if self.tare_weight:
				flow_ok = (
					_is_flow_v28_enabled()
					or self._is_non_rm_weighing()
					or self.unloading_complete
				)
				if flow_ok:
					self.db_set("tare_weight_time", now_datetime())
					self.db_set("tare_operator", frappe.session.user)
					token_updates["wb_tare_time"] = now_datetime()
					token_updates["status"] = "Tare Weighed"

			token.db_set(token_updates)

			# Grain deferred-linking: notify the store team once when a grain
			# truck reaches Tare Weighed (fail-soft — never blocks weighment).
			if token_updates.get("status") == "Tare Weighed":
				from trustbit_ethanol.ts_gate_entry.ts_grain_defer import maybe_notify_grain_pending
				maybe_notify_grain_pending(self.token_number)

	def on_update(self):
		if self._is_stock_out():
			# Stock OUT: gross weight is the second weight
			if self.has_value_changed("gross_weight") and self.gross_weight:
				self.db_set("gross_weight_time", now_datetime())
				self.db_set("gross_operator", frappe.session.user)

				token = frappe.get_doc("TS Token", self.token_number)
				token.db_set({
					"wb_gross_time": now_datetime(),
					"status": "Gross Recorded"
				})
		else:
			# Stock IN: tare weight is the second weight
			if self.has_value_changed("tare_weight") and self.tare_weight:
				self.db_set("tare_weight_time", now_datetime())
				self.db_set("tare_operator", frappe.session.user)

				token = frappe.get_doc("TS Token", self.token_number)
				# v2.30.0 (audit M2): a tare-weight correction after the vehicle
				# has departed must not resurrect it onto the in-plant roster —
				# the WB Log keeps the corrected weights; the exited token's
				# status and original tare timestamp are terminal.
				if token.status not in ("Plant Exited", "Campus Exited", "Exited"):
					token.db_set({
						"wb_tare_time": now_datetime(),
						"status": "Tare Weighed"
					})

				# Grain deferred-linking: notify the store team once when a grain
				# truck reaches Tare Weighed (fail-soft — never blocks weighment).
				from trustbit_ethanol.ts_gate_entry.ts_grain_defer import maybe_notify_grain_pending
				maybe_notify_grain_pending(self.token_number)

		# v2.8.2: mirror rst_number to Token when entered/edited AFTER first
		# save (after_insert's update_token_gross runs only once). Without
		# this, typing RST in a subsequent save would never propagate to
		# Token.custom_rst_number, so Purchase Receipt would get an empty RST.
		if self.has_value_changed("rst_number") and self.rst_number and self.token_number:
			frappe.db.set_value(
				"TS Token", self.token_number,
				"custom_rst_number", self.rst_number,
				update_modified=False,
			)
