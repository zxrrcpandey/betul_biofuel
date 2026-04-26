"""
TS Deduction Sheet — v2.9.0 Day 4 restructure.

Submittable DocType (docstatus 0/1/2). 3-layer model:
  Layer 1 — System: copies from QI total_deduction_pct + total_deduction_kg
            (read_only=1 + permlevel=1 — tamper-protected per Lesson 162).
  Layer 2 — Tilok (Grain Manager): fills actual_deduction_pct + reason if
            differs from system value.
  Layer 3 — Accounts Manager: submits the doc (only Accounts Manager + SM can submit).

On submit: copies actual_deduction_pct/kg/reason to the linked Purchase Invoice
custom fields (ts_ds_actual_deduction_pct/kg/reason). PI gate (Step 9) blocks
PI submit if grain item with submitted QI has no submitted DS.

Lesson references:
  - 162: control-plane fields (system_deduction_*) need server-side tamper guard
         even if permlevel=1 — because frappe.client.set_value bypasses JS.
  - 175: whitelisted mutation methods declare methods=["POST"].
  - 176: frappe.flags.in_xxx wrap in try/finally.
  - 197: each DocType has its own audit-log pattern; DS uses doc.add_comment.
  - 200: amend cycle preserves amended_from — exempt amend-inherited values.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime, escape_html


SUBMIT_ROLES = {"Accounts Manager", "System Manager", "Administrator"}
SYSTEM_FIELD_OVERRIDE_ROLES = {"System Manager", "Administrator"}
SYSTEM_FIELDS = ("system_deduction_pct", "system_deduction_kg")


class TSDeductionSheet(Document):
	# ── lifecycle ──────────────────────────────────────────────────────
	def before_insert(self):
		self._auto_fetch_references()
		self._copy_qi_system_deduction()
		# Default actual_deduction_pct to system value on create
		if self.system_deduction_pct is not None and self.actual_deduction_pct is None:
			self.actual_deduction_pct = self.system_deduction_pct

	def before_save(self):
		# Tamper guard for system_deduction_* fields (Lesson 162).
		# Honour amended_from exemption (Lesson 200).
		self._block_system_field_tampering()
		# Track who filled actual_deduction_pct (Layer 2 audit)
		if not self.is_new() and self.has_value_changed("actual_deduction_pct"):
			self.filled_by = frappe.session.user
			self.filled_at = now_datetime()
		# Sync ds_number = name on first save
		if not self.ds_number and self.name:
			self.ds_number = self.name

	def validate(self):
		self._validate_qi_link()
		self._calculate_actual_deduction_kg()
		self._validate_actual_vs_system()
		self._calculate_legacy_values()
		self._update_legacy_override_flags()
		self._calculate_legacy_totals()

	def before_submit(self):
		# Submit-role gate: only Accounts Manager + SM can submit (Layer 3).
		# Lesson: ignore_permissions inserts (e.g. amend) need to bypass — handled by frappe.flags.
		if frappe.session.user == "Administrator":
			pass  # admin always allowed
		elif getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_migrate", False):
			pass  # migration / install bypass
		else:
			user_roles = set(frappe.get_roles(frappe.session.user))
			if not (user_roles & SUBMIT_ROLES):
				raise frappe.PermissionError(
					"Only Accounts Manager / System Manager can submit a Deduction Sheet."
				)
		# Mandatory checks
		if not self.quality_inspection:
			frappe.throw("Quality Inspection link is mandatory before submit.")
		if self.actual_deduction_pct is None:
			frappe.throw("Actual Deduction % is mandatory before submit.")
		# Verify QI is itself submitted
		qi_docstatus = frappe.db.get_value("TS Quality Inspection", self.quality_inspection, "docstatus")
		if qi_docstatus != 1:
			frappe.throw(
				f"Linked Quality Inspection {self.quality_inspection} is not submitted "
				f"(docstatus {qi_docstatus}). Submit the QI first."
			)

	def on_submit(self):
		# Copy actual_deduction_* into linked PI custom fields (best-effort, audit-logged)
		try:
			self._propagate_to_pi()
		except Exception as e:
			frappe.log_error(
				message=f"DS {self.name} on_submit PI propagation failed: {e}",
				title="ts_deduction_sheet on_submit",
			)
		# Audit log
		self.add_comment(
			"Info",
			f"[DS_SUBMITTED] by {frappe.session.user}: "
			f"system={self.system_deduction_pct or 0}%, "
			f"actual={self.actual_deduction_pct or 0}% "
			f"(Δ {flt(self.actual_deduction_pct or 0) - flt(self.system_deduction_pct or 0):+.3f}%)"
		)

	def on_cancel(self):
		# Clear PI custom fields if they reference this DS
		if not self.quality_inspection:
			return
		try:
			pis = frappe.get_all(
				"Purchase Invoice",
				filters={"ts_ds_reference": self.name, "docstatus": ["!=", 2]},
				pluck="name",
			)
			for pi in pis:
				frappe.db.set_value(
					"Purchase Invoice", pi,
					{
						"ts_ds_actual_deduction_pct": None,
						"ts_ds_actual_deduction_kg": None,
						"ts_ds_actual_deduction_reason": None,
						"ts_ds_reference": None,
					},
					update_modified=False,
				)
				frappe.get_doc("Purchase Invoice", pi).add_comment(
					"Info", f"[DS_CANCELLED] DS {self.name} cancelled — DS fields cleared."
				)
		except Exception as e:
			frappe.log_error(
				message=f"DS {self.name} on_cancel PI cleanup failed: {e}",
				title="ts_deduction_sheet on_cancel",
			)

	# ── helpers ────────────────────────────────────────────────────────
	def _auto_fetch_references(self):
		if not self.quality_inspection:
			return
		qi = frappe.get_doc("TS Quality Inspection", self.quality_inspection)
		self.token_number = self.token_number or qi.token_number
		self.gate_entry = qi.gate_entry
		self.purchase_order = qi.purchase_order
		self.item_code = qi.item_code
		self.item_name = qi.item_name
		self.item_category = qi.item_category

		# Legacy quality results carry-over
		if qi.item_category == "Grain":
			self.qi_impurity_percent = qi.impurity_percent
			self.qi_moisture_percent = qi.moisture_percent
		elif qi.item_category == "Coal":
			self.qi_po_gcv = qi.po_gcv
			self.qi_actual_gcv = qi.actual_gcv
			self.qi_po_moisture = qi.po_moisture_percent
			self.qi_actual_moisture = qi.actual_moisture_percent
			self.hold_on_gcv = (
				1 if qi.actual_gcv and qi.po_gcv and qi.actual_gcv < qi.po_gcv else 0
			)
			self.hold_on_moisture = (
				1 if qi.actual_moisture_percent and qi.po_moisture_percent
				and qi.actual_moisture_percent > qi.po_moisture_percent
				else 0
			)

		if qi.purchase_order:
			self.supplier_name = frappe.db.get_value(
				"Purchase Order", qi.purchase_order, "supplier_name"
			)

	def _copy_qi_system_deduction(self):
		"""Layer 1 — copy QI's total_deduction_pct/kg into system_* fields."""
		if not self.quality_inspection:
			return
		qi_data = frappe.db.get_value(
			"TS Quality Inspection", self.quality_inspection,
			["total_deduction_pct", "total_deduction_kg"],
			as_dict=True,
		)
		if qi_data:
			self.system_deduction_pct = qi_data.total_deduction_pct
			self.system_deduction_kg = qi_data.total_deduction_kg

	def _validate_qi_link(self):
		"""1:1 enforcement — only one DS per submitted QI."""
		if not self.quality_inspection:
			return
		# Check no other non-cancelled DS exists for this QI
		other = frappe.db.get_value(
			"TS Deduction Sheet",
			{
				"quality_inspection": self.quality_inspection,
				"docstatus": ["!=", 2],
				"name": ["!=", self.name or "__new__"],
			},
			"name",
		)
		if other:
			frappe.throw(
				f"A Deduction Sheet ({other}) already exists for Quality Inspection "
				f"{self.quality_inspection}. Only one DS per QI is allowed."
			)

	def _block_system_field_tampering(self):
		"""
		Lesson 162 — server-side guard against frappe.client.set_value bypass.
		Only System Manager / Administrator can change system_deduction_* fields.
		Honour amended_from (Lesson 200) — amend inherits from cancelled DS.
		"""
		if frappe.session.user == "Administrator":
			return
		if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_migrate", False):
			return
		if getattr(frappe.flags, "in_ds_internal", False):
			return

		# On amend, system fields legitimately copy from amended_from. Allow.
		if self.is_new() and getattr(self, "amended_from", None):
			return

		# On fresh insert, _copy_qi_system_deduction runs in before_insert; this hook
		# fires in before_save AFTER that. So is_new + first save with values pulled
		# from QI is legitimate. We only guard against MUTATION on existing docs.
		if self.is_new():
			return

		user_roles = set(frappe.get_roles(frappe.session.user))
		if user_roles & SYSTEM_FIELD_OVERRIDE_ROLES:
			return

		for f in SYSTEM_FIELDS:
			if self.has_value_changed(f):
				raise frappe.PermissionError(
					f"Field '{f}' is auto-fetched from Quality Inspection and "
					f"cannot be manually edited. Only System Manager / Administrator may override."
				)

	def _calculate_actual_deduction_kg(self):
		"""Auto-calc actual_deduction_kg = actual_pct × bag_count × bag_weight_kg from QI."""
		if not self.quality_inspection:
			return
		if self.actual_deduction_pct is None:
			return
		qi_data = frappe.db.get_value(
			"TS Quality Inspection", self.quality_inspection,
			["bag_count", "bag_weight_kg"],
			as_dict=True,
		)
		if qi_data:
			bag_count = flt(qi_data.bag_count) or 0
			bag_weight = flt(qi_data.bag_weight_kg) or 0
			self.actual_deduction_kg = round(
				(flt(self.actual_deduction_pct) / 100.0) * bag_count * bag_weight, 3
			)

	def _validate_actual_vs_system(self):
		"""Reason mandatory when actual_pct differs from system_pct by > 0.01%."""
		if self.actual_deduction_pct is None or self.system_deduction_pct is None:
			return
		delta = abs(flt(self.actual_deduction_pct) - flt(self.system_deduction_pct))
		if delta > 0.01:
			if not (self.actual_deduction_reason or "").strip():
				frappe.throw(
					f"Actual Deduction % ({self.actual_deduction_pct}) differs from "
					f"System Deduction % ({self.system_deduction_pct}) by {delta:.3f}%. "
					f"Please provide a Reason for Difference."
				)

	# ── legacy compute (preserved for backward compat) ────────────────
	def _calculate_legacy_values(self):
		self.invoice_value = flt(self.invoice_qty) * flt(self.item_rate)
		self.net_weight = flt(self.invoice_qty) - flt(self.weight_deduction)
		self.mrn_amount = flt(self.net_weight) * flt(self.item_rate)

	def _update_legacy_override_flags(self):
		for row in (self.deductions or []):
			if flt(row.actual_amount) != flt(row.calculated_amount) and flt(row.calculated_amount) > 0:
				row.is_overridden = 1
			else:
				row.is_overridden = 0

	def _calculate_legacy_totals(self):
		self.total_deduction = sum(flt(row.actual_amount) for row in (self.deductions or []))
		self.net_payable = flt(self.mrn_amount) - flt(self.total_deduction)

	# ── PI propagation (on submit) ────────────────────────────────────
	def _propagate_to_pi(self):
		"""
		Find the PI linked to this DS's QI/PR/PO chain and copy actual_*.

		Strategy: find PR linked to this token, then find PI items.purchase_receipt
		matching that PR. Multiple PIs per PR is possible — update all.
		"""
		token = self.token_number
		if not token:
			return
		prs = frappe.get_all(
			"Purchase Receipt",
			filters={"ts_token": token, "docstatus": 1, "is_return": 0},
			pluck="name",
		)
		if not prs:
			return

		# Find PIs that reference any of these PRs (via items.purchase_receipt)
		pi_names = frappe.db.sql_list("""
			SELECT DISTINCT pii.parent
			FROM `tabPurchase Invoice Item` pii
			INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
			WHERE pii.purchase_receipt IN %(prs)s
			  AND pi.docstatus != 2
		""", {"prs": tuple(prs) if len(prs) > 1 else (prs[0],)})

		if not pi_names:
			return

		frappe.flags.in_ds_internal = True
		try:
			for pi_name in pi_names:
				frappe.db.set_value("Purchase Invoice", pi_name, {
					"ts_ds_actual_deduction_pct": self.actual_deduction_pct,
					"ts_ds_actual_deduction_kg": self.actual_deduction_kg,
					"ts_ds_actual_deduction_reason": self.actual_deduction_reason,
					"ts_ds_reference": self.name,
				}, update_modified=False)
				try:
					frappe.get_doc("Purchase Invoice", pi_name).add_comment(
						"Info",
						f"[DS_APPLIED] DS {self.name} submitted — "
						f"actual deduction {self.actual_deduction_pct or 0}% / "
						f"{self.actual_deduction_kg or 0} kg copied to PI."
					)
				except Exception:
					pass  # comment is best-effort
		finally:
			frappe.flags.in_ds_internal = False

	# ── whitelisted helpers ────────────────────────────────────────────
	# Lesson 175: instance method that calls self.save() must be POST-only
	# to enforce CSRF protection.
	@frappe.whitelist(methods=["POST"])
	def calculate_deductions(self):
		"""Legacy calculate (preserved for backward compat with old DS workflow)."""
		if not flt(self.invoice_qty):
			frappe.throw("Please enter Invoice Qty before calculating legacy deductions")
		if not flt(self.item_rate):
			frappe.throw("Please enter Item Rate before calculating legacy deductions")

		self.deductions = []

		if self.item_category == "Grain":
			self._calc_legacy_grain()
		elif self.item_category == "Coal":
			self._calc_legacy_coal()

		self._calculate_legacy_values()
		self._calculate_legacy_totals()
		self.save(ignore_permissions=False)
		return {"total_deduction": self.total_deduction, "net_payable": self.net_payable}

	def _calc_legacy_grain(self):
		net_wt = flt(self.net_weight)
		mrn = flt(self.mrn_amount)
		if flt(self.unloading_rate_per_bag):
			amt = net_wt * flt(self.unloading_rate_per_bag)
			self.append("deductions", {
				"deduction_type": "Unloading",
				"description": f"Net Wt ({net_wt} KG) x Rs {self.unloading_rate_per_bag}/KG",
				"base_value": net_wt,
				"rate": flt(self.unloading_rate_per_bag),
				"rate_type": "Per KG",
				"calculated_amount": amt,
				"actual_amount": amt,
			})
		if flt(self.dhalta_rate_gm_per_qtl):
			dhalta_pct = flt(self.dhalta_rate_gm_per_qtl) / 1000
			amt = mrn * dhalta_pct / 100
			self.append("deductions", {
				"deduction_type": "Dhalta (Spillage)",
				"description": f"MRN ({mrn}) x {self.dhalta_rate_gm_per_qtl}g/qtl",
				"base_value": mrn,
				"rate": flt(self.dhalta_rate_gm_per_qtl),
				"rate_type": "Per Qtl",
				"calculated_amount": amt,
				"actual_amount": amt,
			})
		if flt(self.qi_impurity_percent):
			amt = mrn * flt(self.qi_impurity_percent) / 100
			self.append("deductions", {
				"deduction_type": "Impurity",
				"description": f"MRN ({mrn}) x {self.qi_impurity_percent}%",
				"base_value": mrn,
				"rate": flt(self.qi_impurity_percent),
				"rate_type": "Percentage",
				"calculated_amount": amt,
				"actual_amount": amt,
			})
		if flt(self.brokerage_rate_per_mt):
			net_wt_mt = flt(net_wt) / 1000
			amt = net_wt_mt * flt(self.brokerage_rate_per_mt)
			self.append("deductions", {
				"deduction_type": "Brokerage",
				"description": f"Net Wt ({net_wt} KG) x Rs {self.brokerage_rate_per_mt}/MT",
				"base_value": net_wt,
				"rate": flt(self.brokerage_rate_per_mt),
				"rate_type": "Per MT",
				"calculated_amount": amt,
				"actual_amount": amt,
			})

	def _calc_legacy_coal(self):
		invoice_qty = flt(self.invoice_qty)
		item_rate = flt(self.item_rate)
		if flt(self.qi_actual_gcv) and flt(self.qi_po_gcv) and flt(self.qi_actual_gcv) < flt(self.qi_po_gcv):
			gcv_short = flt(self.qi_po_gcv) - flt(self.qi_actual_gcv)
			amt = (gcv_short / flt(self.qi_po_gcv)) * invoice_qty * item_rate
			self.append("deductions", {
				"deduction_type": "GCV Shortfall",
				"description": f"PO {self.qi_po_gcv}, Actual {self.qi_actual_gcv}, Short {gcv_short}",
				"base_value": invoice_qty * item_rate,
				"rate": gcv_short,
				"rate_type": "Fixed",
				"calculated_amount": amt,
				"actual_amount": amt,
			})
		if flt(self.qi_actual_moisture) and flt(self.qi_po_moisture) and flt(self.qi_actual_moisture) > flt(self.qi_po_moisture):
			excess = flt(self.qi_actual_moisture) - flt(self.qi_po_moisture)
			amt = (excess / 100) * invoice_qty * item_rate
			self.append("deductions", {
				"deduction_type": "Moisture Excess",
				"description": f"PO {self.qi_po_moisture}, Actual {self.qi_actual_moisture}, Excess {excess}",
				"base_value": invoice_qty * item_rate,
				"rate": excess,
				"rate_type": "Percentage",
				"calculated_amount": amt,
				"actual_amount": amt,
			})
