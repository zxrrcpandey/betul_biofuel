import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class TSDeductionSheet(Document):
	def before_insert(self):
		self.auto_fetch_references()

	def auto_fetch_references(self):
		if not self.quality_inspection:
			return

		qi = frappe.get_doc("TS Quality Inspection", self.quality_inspection)
		self.token_number = self.token_number or qi.token_number
		self.gate_entry = qi.gate_entry
		self.purchase_order = qi.purchase_order
		self.item_code = qi.item_code
		self.item_name = qi.item_name
		self.item_category = qi.item_category

		# Fetch quality results
		if qi.item_category == "Grain":
			self.qi_impurity_percent = qi.impurity_percent
			self.qi_moisture_percent = qi.moisture_percent
		elif qi.item_category == "Coal":
			self.qi_po_gcv = qi.po_gcv
			self.qi_actual_gcv = qi.actual_gcv
			self.qi_po_moisture = qi.po_moisture_percent
			self.qi_actual_moisture = qi.actual_moisture_percent
			self.hold_on_gcv = 1 if qi.actual_gcv and qi.po_gcv and qi.actual_gcv < qi.po_gcv else 0
			self.hold_on_moisture = 1 if qi.actual_moisture_percent and qi.po_moisture_percent and qi.actual_moisture_percent > qi.po_moisture_percent else 0

		# Fetch supplier from PO
		if qi.purchase_order:
			self.supplier_name = frappe.db.get_value("Purchase Order", qi.purchase_order, "supplier_name")

	def validate(self):
		self.calculate_values()
		self.update_override_flags()
		self.calculate_totals()

	def calculate_values(self):
		self.invoice_value = flt(self.invoice_qty) * flt(self.item_rate)
		self.net_weight = flt(self.invoice_qty) - flt(self.weight_deduction)
		self.mrn_amount = flt(self.net_weight) * flt(self.item_rate)

	def update_override_flags(self):
		for row in self.deductions:
			if flt(row.actual_amount) != flt(row.calculated_amount) and flt(row.calculated_amount) > 0:
				row.is_overridden = 1
			else:
				row.is_overridden = 0

	def calculate_totals(self):
		self.total_deduction = sum(flt(row.actual_amount) for row in self.deductions)
		self.net_payable = flt(self.mrn_amount) - flt(self.total_deduction)

	@frappe.whitelist()
	def calculate_deductions(self):
		"""Auto-populate deduction lines based on item category."""
		if not flt(self.invoice_qty):
			frappe.throw("Please enter Invoice Qty before calculating deductions")
		if not flt(self.item_rate):
			frappe.throw("Please enter Item Rate before calculating deductions")

		self.deductions = []

		if self.item_category == "Grain":
			self._calculate_grain_deductions()
		elif self.item_category == "Coal":
			self._calculate_coal_deductions()

		# Recalculate values
		self.calculate_values()
		self.calculate_totals()
		self.status = "Calculated"
		self.save(ignore_permissions=True)

		return {"status": "success", "total_deduction": self.total_deduction, "net_payable": self.net_payable}

	def _calculate_grain_deductions(self):
		net_wt = flt(self.net_weight)
		mrn = flt(self.mrn_amount)

		# 1. Unloading Deduction (Net Weight x Rate per KG)
		unloading_amt = flt(net_wt) * flt(self.unloading_rate_per_bag)
		if flt(self.unloading_rate_per_bag):
			self.append("deductions", {
				"deduction_type": "Unloading",
				"description": f"Net Wt ({net_wt} KG) x Rs {self.unloading_rate_per_bag}/KG",
				"base_value": net_wt,
				"rate": flt(self.unloading_rate_per_bag),
				"rate_type": "Per KG",
				"calculated_amount": unloading_amt,
				"actual_amount": unloading_amt
			})

		# 2. Dhalta Deduction (MRN Amount x Dhalta%)
		# Dhalta rate is in gm per qtl, convert to percentage: (gm/qtl) / 1000 * 100 = gm/10
		dhalta_pct = flt(self.dhalta_rate_gm_per_qtl) / 1000  # gm to kg ratio per qtl (100kg)
		dhalta_amt = flt(mrn) * dhalta_pct / 100
		if flt(self.dhalta_rate_gm_per_qtl):
			self.append("deductions", {
				"deduction_type": "Dhalta (Spillage)",
				"description": f"MRN ({mrn}) x {self.dhalta_rate_gm_per_qtl}g/qtl",
				"base_value": mrn,
				"rate": flt(self.dhalta_rate_gm_per_qtl),
				"rate_type": "Per Qtl",
				"calculated_amount": dhalta_amt,
				"actual_amount": dhalta_amt
			})

		# 3. Impurity Deduction (MRN Amount x Impurity%)
		impurity_amt = flt(mrn) * flt(self.qi_impurity_percent) / 100
		if flt(self.qi_impurity_percent):
			self.append("deductions", {
				"deduction_type": "Impurity",
				"description": f"MRN ({mrn}) x {self.qi_impurity_percent}%",
				"base_value": mrn,
				"rate": flt(self.qi_impurity_percent),
				"rate_type": "Percentage",
				"calculated_amount": impurity_amt,
				"actual_amount": impurity_amt
			})

		# 4. Brokerage (Net Weight in KG / 1000 to get MT, then x Rate per MT)
		net_wt_mt = flt(net_wt) / 1000  # Convert KG to Metric Tons
		brokerage_amt = net_wt_mt * flt(self.brokerage_rate_per_mt)
		if flt(self.brokerage_rate_per_mt):
			self.append("deductions", {
				"deduction_type": "Brokerage",
				"description": f"Net Wt ({net_wt} KG = {round(net_wt_mt, 3)} MT) x Rs {self.brokerage_rate_per_mt}/MT",
				"base_value": net_wt,
				"rate": flt(self.brokerage_rate_per_mt),
				"rate_type": "Per MT",
				"calculated_amount": brokerage_amt,
				"actual_amount": brokerage_amt
			})

	def _calculate_coal_deductions(self):
		invoice_qty = flt(self.invoice_qty)
		item_rate = flt(self.item_rate)

		# GCV Deduction: if Actual GCV < PO GCV
		if flt(self.qi_actual_gcv) and flt(self.qi_po_gcv) and flt(self.qi_actual_gcv) < flt(self.qi_po_gcv):
			gcv_shortfall = flt(self.qi_po_gcv) - flt(self.qi_actual_gcv)
			gcv_debit = (gcv_shortfall / flt(self.qi_po_gcv)) * invoice_qty * item_rate
			self.append("deductions", {
				"deduction_type": "GCV Shortfall",
				"description": f"PO GCV: {self.qi_po_gcv}, Actual: {self.qi_actual_gcv}, Shortfall: {gcv_shortfall}",
				"base_value": invoice_qty * item_rate,
				"rate": gcv_shortfall,
				"rate_type": "Fixed",
				"calculated_amount": gcv_debit,
				"actual_amount": gcv_debit
			})

		# Moisture Deduction: if Actual Moisture > PO Moisture
		# Deduction = (excess moisture% / 100) * invoice_value
		if flt(self.qi_actual_moisture) and flt(self.qi_po_moisture) and flt(self.qi_actual_moisture) > flt(self.qi_po_moisture):
			moisture_excess = flt(self.qi_actual_moisture) - flt(self.qi_po_moisture)
			moisture_debit = (moisture_excess / 100) * invoice_qty * item_rate
			self.append("deductions", {
				"deduction_type": "Moisture Excess",
				"description": f"PO Moisture: {self.qi_po_moisture}, Actual: {self.qi_actual_moisture}, Excess: {moisture_excess}",
				"base_value": invoice_qty * item_rate,
				"rate": moisture_excess,
				"rate_type": "Percentage",
				"calculated_amount": moisture_debit,
				"actual_amount": moisture_debit
			})

	@frappe.whitelist()
	def approve_deductions(self):
		# Idempotency: already approved
		if self.status == "Approved":
			return {"status": "Approved", "net_payable": self.net_payable}

		if not self.deductions:
			frappe.throw("Please calculate deductions first")
		if not self.decision:
			frappe.throw("Please set a Decision before approving")

		# v2.8.1 Phase B: Accounts Manager + IT Head + SM + Administrator only
		allowed = {"Accounts Manager", "IT Head", "System Manager", "Administrator"}
		user_roles = set(frappe.get_roles(frappe.session.user))
		if not (user_roles & allowed):
			raise frappe.PermissionError(
				"Only Accounts Manager / IT Head / System Manager can approve deductions"
			)

		self.status = "Approved"
		self.save(ignore_permissions=True)

		# Audit trail
		self.add_comment(
			"Info",
			f"[DEDUCTION_APPROVED] by {frappe.session.user}: "
			f"total ₹{self.total_deduction or 0}, net payable ₹{self.net_payable or 0}"
		)

		# Update token timestamps (grading_time still useful for audit).
		# v2.8.1: DO NOT set token.status = "Graded" anymore — that state is retired.
		# The legacy write still applies under flag OFF (v2.7 path).
		try:
			qc_gate_on = bool(frappe.db.get_single_value("TS Settings", "ts_qc_gate_enabled"))
		except Exception:
			qc_gate_on = False

		token = frappe.get_doc("TS Token", self.token_number)
		if qc_gate_on:
			# v2.8.1: only update timestamp, no status change
			token.db_set("grading_time", now_datetime())
		else:
			# Legacy v2.7 behavior — write Graded status
			token.db_set({
				"grading_time": now_datetime(),
				"status": "Graded"
			})

		return {"status": "Approved", "net_payable": self.net_payable}
