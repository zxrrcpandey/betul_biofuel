"""
TS Deduction Sheet — v2.9.0 Day 4 / v2.9.5 snapshot model.

Submittable DocType (docstatus 0/1/2). 3-layer model:
  Layer 1 — System: copies from QI total_deduction_pct + total_deduction_kg
            (read_only=1 + permlevel=1 — tamper-protected per Lesson 162).
  Layer 2 — Snapshot of QI's actual_deduction_pct/kg/reason — v2.9.5 these are
            now FILLED ON THE QI BEFORE SUBMIT and DS auto-creates from QI.
            DS Layer 2 fields are read_only + permlevel=1; mutation guarded.
  Layer 3 — Accounts Manager: submits the doc (only Accounts Manager + SM can submit).

On submit: copies actual_deduction_pct/kg/reason to the linked Purchase Invoice
custom fields (ts_ds_actual_deduction_pct/kg/reason). PI gate (Step 9) blocks
PI submit if grain item with submitted QI has no submitted DS.

v2.9.5 changes:
  - DS auto-creates on QI submit (see ts_quality_inspection._auto_create_deduction_sheet)
  - Layer 2 actual_deduction_* fields are now SNAPSHOTS from the parent QI
  - User no longer fills actual_deduction_pct on DS — it is copied
  - Snapshot-integrity validate enforces DS values match the QI (defence-in-depth)

Lesson references:
  - 162: control-plane fields (system_deduction_*, actual_deduction_*) need
         server-side tamper guard even if permlevel=1 — frappe.client.set_value bypasses JS.
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
# v2.9.5 — snapshot fields copied from parent QI; never user-editable on DS.
ACTUAL_SNAPSHOT_FIELDS = ("actual_deduction_pct", "actual_deduction_kg", "actual_deduction_reason")
SNAPSHOT_TOLERANCE = 0.001  # %  — guard against float-rounding when comparing snapshots


class TSDeductionSheet(Document):
	# ── lifecycle ──────────────────────────────────────────────────────
	def before_insert(self):
		self._auto_fetch_references()
		self._copy_qi_system_deduction()
		# v2.9.6: snapshot Layer-2 from TS Deduction Suggestion (per-role doctype boundary).
		# Falls back to system value defaults if no Suggestion is linked (legacy/manual path).
		self._copy_from_suggestion()

	def before_save(self):
		# Tamper guard for system_deduction_* fields (Lesson 162).
		# Honour amended_from exemption (Lesson 200).
		self._block_system_field_tampering()
		# v2.9.5: also tamper-guard the actual_deduction_* snapshot fields.
		self._block_actual_snapshot_tampering()
		# v2.9.6: filled_by/filled_at reflect the Suggestion's submission (snapshot).
		# Falls back to current user when no Suggestion is linked (legacy/manual path).
		if self.is_new() and not self.filled_by and self.actual_deduction_pct is not None:
			self.filled_by = self._get_suggestion_owner() or frappe.session.user
			self.filled_at = self._get_suggestion_owner_at() or now_datetime()
		# Sync ds_number = name on first save
		if not self.ds_number and self.name:
			self.ds_number = self.name

	def validate(self):
		self._validate_qi_link()
		# v2.9.5: enforce snapshot integrity — actual_* must match parent QI.
		# This is defence-in-depth in case permlevel=1 is bypassed.
		self._validate_snapshot_integrity()
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

	# ── v2.9.6 Suggestion snapshot helpers ────────────────────────────
	def _copy_from_suggestion(self):
		"""Snapshot Layer-2 fields from linked TS Deduction Suggestion on insert.

		Suggestion is the source of truth for actual_deduction_*. If no Suggestion
		is linked (legacy/manual DS create path), fall back to system_* defaults.
		"""
		if not self.deduction_suggestion:
			# Legacy / manual path — fall back to system value
			if self.system_deduction_pct is not None and self.actual_deduction_pct is None:
				self.actual_deduction_pct = self.system_deduction_pct
				self.actual_deduction_kg = self.system_deduction_kg
			return
		s = frappe.db.get_value(
			"TS Deduction Suggestion", self.deduction_suggestion,
			["docstatus", "actual_pct", "actual_reason"],
			as_dict=True,
		)
		if not s:
			return
		self.actual_deduction_pct = s.actual_pct
		self.actual_deduction_reason = s.actual_reason
		# Compute kg from the snapshot pct + bag info on the QI (unchanged formula)
		bag_info = frappe.db.get_value(
			"TS Quality Inspection", self.quality_inspection,
			["bag_count", "bag_weight_kg"],
			as_dict=True,
		)
		if bag_info:
			pct = flt(s.actual_pct) or 0
			count = flt(bag_info.bag_count) or 0
			weight = flt(bag_info.bag_weight_kg) or 0
			self.actual_deduction_kg = round((pct / 100.0) * count * weight, 3)

	def _get_suggestion_owner(self):
		if not self.deduction_suggestion:
			return None
		return frappe.db.get_value(
			"TS Deduction Suggestion", self.deduction_suggestion, "owner"
		)

	def _get_suggestion_owner_at(self):
		if not self.deduction_suggestion:
			return None
		# Use the Suggestion's modified timestamp at submit time (close enough)
		return frappe.db.get_value(
			"TS Deduction Suggestion", self.deduction_suggestion, "modified"
		)

	def _validate_snapshot_integrity(self):
		"""Defence-in-depth — ensure DS Layer-2 values match the source.

		v2.9.6 source: TS Deduction Suggestion (when linked).
		Legacy fall-back: skip integrity check when no Suggestion is linked.
		"""
		if not self.deduction_suggestion:
			return  # legacy / manual DS — no Suggestion source to validate against
		s = frappe.db.get_value(
			"TS Deduction Suggestion", self.deduction_suggestion,
			["docstatus", "actual_pct", "actual_reason"],
			as_dict=True,
		)
		if not s or s.docstatus != 1:
			return  # only validate against submitted Suggestions

		ds_pct = flt(self.actual_deduction_pct or 0)
		s_pct = flt(s.actual_pct or 0)
		if abs(ds_pct - s_pct) > SNAPSHOT_TOLERANCE:
			frappe.throw(
				f"Snapshot integrity violation: DS actual_deduction_pct ({ds_pct}) "
				f"does not match parent Suggestion ({s_pct}). DS values are immutable "
				f"snapshots — edit on the source Suggestion before submit."
			)
		ds_reason = (self.actual_deduction_reason or "").strip()
		s_reason = (s.actual_reason or "").strip()
		if ds_reason != s_reason:
			frappe.throw(
				"Snapshot integrity violation: DS actual_deduction_reason does not "
				"match parent Suggestion."
			)

	def _block_actual_snapshot_tampering(self):
		"""Lesson 162 mirror — block mutation of snapshot fields on existing DS.

		Allow:
		  - Administrator
		  - migrate / install flags
		  - frappe.flags.in_qi_internal (auto-create path from QI)
		  - frappe.flags.in_ds_internal (DS internal propagation)
		  - new docs (snapshot is filled in before_insert)
		  - amend cycle (amended_from set on insert)
		"""
		if frappe.session.user == "Administrator":
			return
		if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_migrate", False):
			return
		if getattr(frappe.flags, "in_qi_internal", False):
			return
		if getattr(frappe.flags, "in_ds_internal", False):
			return
		if self.is_new():
			return

		user_roles = set(frappe.get_roles(frappe.session.user))
		if user_roles & SYSTEM_FIELD_OVERRIDE_ROLES:
			return

		for f in ACTUAL_SNAPSHOT_FIELDS:
			if self.has_value_changed(f):
				raise frappe.PermissionError(
					f"Field '{f}' is a snapshot from the parent Quality Inspection and "
					f"cannot be edited on the Deduction Sheet. Edit on the source QI."
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
