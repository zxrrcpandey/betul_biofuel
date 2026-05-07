"""
TS Quality Inspection — v2.9.6.

QI Inspector creates → fills parameters + decision → submits.
On QI submit: a draft TS Deduction Suggestion is auto-created and Grain Manager
is notified. Grain Manager confirms/overrides the deduction in the Suggestion
form (per-role doctype boundary, Lesson 205). DS auto-creates from Suggestion
on Suggestion submit.

Submittable DocType (docstatus 0/1/2). Fields:
  - quality_report_no: auto-generated BBPL-QR-YY-##### (race-safe via make_autoname)
  - lab_register_no: optional manual reference
  - qc_template: drives parameters child table population
  - bag_type / bag_count / bag_weight_kg: bagging info
  - parameters: TS QI Parameter Result child rows
  - total_deduction_pct / total_deduction_kg: auto-calc from rows

Lifecycle:
  before_insert  → autofetch refs (token, GE, PO, item) + auto-generate Quality Report No
  validate       → calc legacy variances + per-row deductions + total_deduction_pct
  before_submit  → mandate decision + qc_template + parameters actual values
  on_submit      → auto-create draft TS Deduction Suggestion + notify Grain Manager
  on_cancel      → cascade-cancel linked Suggestion (which cascades to draft DS)

Lesson references:
  - 175: whitelisted mutation methods declare methods=["POST"].
  - 176: frappe.flags.in_xxx wrap in try/finally.
  - 196: notification failure NEVER blocks submit (try/except).
  - 200: amend cycle preserves `amended_from` — exempt from autoname collision.
  - 205: per-role doctype boundary (Suggestion replaces inline-edit on QI).
"""

import frappe
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import flt, escape_html


def _format_standard(target, min_v, max_v):
	"""Format the QI parameter Standard column.

	Priority:
	  1. target_value if explicitly set on template
	  2. "min - max" range when both bounds are set (handles Min=0)
	  3. "≤ max" or "≥ min" when only one bound is set
	  4. "0" as final fallback
	"""
	def _fmt(v):
		# strip trailing zeros: 4000.000 → "4000", 14.5 → "14.5"
		return f"{flt(v):g}"

	if target is not None:
		return _fmt(target)
	if min_v is not None and max_v is not None:
		return f"{_fmt(min_v)} - {_fmt(max_v)}"
	if max_v is not None:
		return f"≤ {_fmt(max_v)}"
	if min_v is not None:
		return f"≥ {_fmt(min_v)}"
	return "0"


class TSQualityInspection(Document):
	# ── lifecycle ──────────────────────────────────────────────────────
	def before_insert(self):
		if not self.inspector:
			self.inspector = frappe.session.user
		self._validate_token_status()
		self._auto_fetch_references()
		# Quality Report No is auto-generated on first save (idempotent — only if blank)
		if not self.quality_report_no:
			self.quality_report_no = self._generate_quality_report_no()

	def validate(self):
		self._calculate_variances()
		# v2.9.0.8: rate-based per-row recalc BEFORE summing into total
		self._recalc_param_deductions()
		self._calculate_total_deductions()
		# v2.9.6: friendly error when Reject/Hold decision lacks a reason.
		self._validate_hold_reason()

	def _validate_hold_reason(self):
		if self.decision in ("Hold", "Reject") and not (self.hold_reason or "").strip():
			frappe.throw(
				f"You picked Decision = '{self.decision}'. "
				"Please type a short reason in the 'Hold / Reject Reason' box and save again.",
				title="Reason Required",
			)

	def before_submit(self):
		# Hard gates on submit
		if not self.decision:
			frappe.throw("Please set a Decision (Accept / Reject / Hold) before submitting.")
		# v2.9.0.13: grade mandatory check removed (field hidden per user request).
		if not self.qc_template:
			frappe.throw("QC Template is mandatory before submitting.")
		# Parameter rows recommended (not strictly mandatory — some templates may have 0 params)
		# but if a row exists, actual_value must be filled for non-Boolean params
		for row in (self.parameters or []):
			if row.parameter_type and row.parameter_type != "Boolean":
				if row.actual_value is None or str(row.actual_value).strip() == "":
					frappe.throw(
						f"Parameter '{row.parameter_label}' (row {row.idx}) requires an Actual Value."
					)

	def on_submit(self):
		# v2.9.6: auto-create Deduction Suggestion + notify Grain Manager.
		# Wrapped — Suggestion creation failure NEVER blocks QI submit (Lesson 196).
		from trustbit_ethanol.ts_gate_entry.doctype.ts_deduction_suggestion.ts_deduction_suggestion import (
			auto_create_suggestion_for_qi,
			notify_grain_manager_for_qi,
		)
		if self.qc_template:
			try:
				auto_create_suggestion_for_qi(self)
			except Exception as e:
				frappe.log_error(
					message=f"QI {self.name} auto-create Suggestion failed: {e}",
					title="ts_quality_inspection.on_submit auto_suggestion",
				)
		try:
			notify_grain_manager_for_qi(self)
		except Exception as e:
			frappe.log_error(
				message=f"QI {self.name} on_submit notification failed: {e}",
				title="ts_quality_inspection.on_submit notify",
			)

	def on_cancel(self):
		# v2.9.6: Cascade-cancel linked Suggestion (which then cascades to draft DS).
		try:
			self._cancel_linked_suggestions()
		except Exception as e:
			frappe.log_error(
				message=f"QI {self.name} on_cancel Suggestion cascade failed: {e}",
				title="ts_quality_inspection.on_cancel suggestion_cascade",
			)

		# Reset linked PR.ts_qc_status to Pending if currently Approved/Rejected
		# from this QI (best-effort; idempotent).
		token = self.token_number
		if not token:
			return
		try:
			prs = frappe.get_all(
				"Purchase Receipt",
				filters={"ts_token": token, "docstatus": ["!=", 2]},
				pluck="name",
			)
			frappe.flags.in_qc_auto_update = True
			try:
				for pr_name in prs:
					frappe.db.set_value(
						"Purchase Receipt", pr_name, "ts_qc_status",
						"Pending", update_modified=False,
					)
					frappe.get_doc("Purchase Receipt", pr_name).add_comment(
						"Info",
						f"[QC_STATUS_RESET] PR ts_qc_status reset to Pending after QI {self.name} cancelled by {frappe.session.user}",
					)
			finally:
				frappe.flags.in_qc_auto_update = False
		except Exception as e:
			frappe.log_error(
				message=f"QI {self.name} on_cancel PR reset failed: {e}",
				title="ts_quality_inspection.on_cancel",
			)

	def _cancel_linked_suggestions(self):
		"""Cancel any submitted Suggestion linked to this QI when QI is cancelled.

		Suggestion's own on_cancel will cascade to draft DS (or warn for submitted DS).
		Lesson 176: wrap flag mutation in try/finally.
		"""
		linked = frappe.get_all(
			"TS Deduction Suggestion",
			filters={"quality_inspection": self.name, "docstatus": 1},
			pluck="name",
		)
		if not linked:
			return
		frappe.flags.in_qi_internal = True
		try:
			for s_name in linked:
				try:
					s = frappe.get_doc("TS Deduction Suggestion", s_name)
					s.cancel()
					s.add_comment(
						"Info",
						f"[QI_CASCADE_CANCEL] Suggestion auto-cancelled because parent QI {self.name} was cancelled.",
					)
				except Exception as e:
					frappe.log_error(
						message=f"Suggestion {s_name} cascade-cancel failed: {e}",
						title="ts_quality_inspection cascade Suggestion cancel",
					)
		finally:
			frappe.flags.in_qi_internal = False

	# ── helpers ────────────────────────────────────────────────────────
	def _validate_token_status(self):
		if not self.token_number:
			return
		# Lesson 200 — amend cycle bypass: cancelled QI being re-created via amend
		# legitimately reuses the original token even if status has progressed.
		if getattr(self, "amended_from", None):
			return
		token_status = frappe.db.get_value("TS Token", self.token_number, "status")
		# QI eligible from Gross Weighed through Unloading and post-weighing stages.
		# 'Quality Done' is INTENTIONALLY excluded — a QI already exists for this token,
		# blocking duplicate creation. Re-create via amend instead (handled above).
		# v2.9.5.2 — removed ts_qc_gate_enabled branch on QI creation.
		# v2.9.6  — added Unloading; excluded Quality Done.
		allowed = (
			"Gross Weighed", "Unloading", "Tare Weighed", "GRN Created",
			"Plant Exited", "Campus Exited", "Exited",
		)
		if token_status not in allowed:
			if token_status == "Quality Done":
				frappe.throw(
					f"Token {self.token_number} already has a Quality Inspection "
					"(stage: 'Quality Done'). To re-do, cancel the existing QI and use the "
					"'Amend' button on the cancelled record instead of creating a new one."
				)
			frappe.throw(
				f"Token {self.token_number} is at stage '{token_status}'. "
				f"Quality Inspection can only be created for tokens in: {', '.join(allowed)}."
			)

	def _auto_fetch_references(self):
		if not self.token_number:
			return
		# v2.9.0.8: pull vehicle_number + custom_rst_number directly from Token
		token_data = frappe.db.get_value(
			"TS Token",
			self.token_number,
			["vehicle_number", "custom_rst_number"],
			as_dict=True,
		)
		if token_data:
			if not self.vehicle_number:
				self.vehicle_number = token_data.get("vehicle_number")
			if not self.rst_number:
				self.rst_number = token_data.get("custom_rst_number")
		gate_entry = frappe.db.get_value(
			"TS Gate Entry",
			{"token_number": self.token_number, "docstatus": 1},
			["name", "purchase_order"],
			as_dict=True,
		)
		if gate_entry:
			self.gate_entry = gate_entry.name
			self.purchase_order = gate_entry.purchase_order
			items = frappe.db.get_all(
				"TS Gate Entry Item",
				filters={"parent": gate_entry.name},
				fields=["item_code", "item_name"],
				limit=1,
			)
			if items:
				self.item_code = items[0].item_code
				if not self.item_name:
					self.item_name = items[0].item_name
			# v2.9.0.8: pull supplier_name from PO → party_name
			if gate_entry.purchase_order and not self.party_name:
				supplier_name = frappe.db.get_value(
					"Purchase Order",
					gate_entry.purchase_order,
					"supplier_name",
				)
				if supplier_name:
					self.party_name = supplier_name

	def _generate_quality_report_no(self):
		"""Race-safe Quality Report No: BBPL-QR-YY-##### using Frappe's make_autoname."""
		return make_autoname("BBPL-QR-.YY.-.#####")

	def _calculate_variances(self):
		"""Legacy coal variance calc (kept for backward compat with dashboards)."""
		if self.item_category == "Coal":
			if flt(self.actual_gcv) and flt(self.po_gcv):
				self.gcv_variance_percent = round(
					((flt(self.actual_gcv) - flt(self.po_gcv)) / flt(self.po_gcv)) * 100, 2
				)
			if flt(self.po_moisture_percent) and flt(self.actual_moisture_percent):
				self.moisture_variance_percent = round(
					flt(self.actual_moisture_percent) - flt(self.po_moisture_percent), 3
				)

	def _recalc_param_deductions(self):
		"""v2.9.6 direction-aware deduction calc. Per row:

		    shortfall = max(0, min_value - actual)   ← fires when actual is BELOW min
		    excess    = max(0, actual - max_value)   ← fires when actual is ABOVE max

		Direction controls which side counts:
		    - "Higher is Better"  → only shortfall  (e.g. Starch, GCV — low is bad)
		    - "Lower is Better"   → only excess     (e.g. Moisture, Impurity — high is bad)
		    - "In Range" / blank  → shortfall + excess  (penalty either side)

		    deduction_pct = (counted_units) * deduction_per_unit
		"""
		for row in (self.parameters or []):
			if row.parameter_type != "Numeric":
				continue
			try:
				actual = flt(row.actual_value)
				min_val = flt(row.min_value)
				max_val = flt(row.max_value)
				rate = flt(row.deduction_per_unit) or 1.0
			except (TypeError, ValueError):
				continue
			if not row.actual_value or str(row.actual_value).strip() == "":
				row.deduction_pct = 0
				continue
			shortfall = max(0, min_val - actual) if row.min_value is not None else 0
			excess = max(0, actual - max_val) if row.max_value is not None else 0
			direction = (row.direction or "In Range")
			if direction == "Higher is Better":
				counted = shortfall
			elif direction == "Lower is Better":
				counted = excess
			else:  # In Range or blank
				counted = shortfall + excess
			row.deduction_pct = round(counted * rate, 3)

	def _calculate_total_deductions(self):
		"""Sum row-level deduction_pct → total_deduction_pct → total_deduction_kg."""
		total_pct = sum(flt(row.deduction_pct) for row in (self.parameters or []))
		self.total_deduction_pct = round(total_pct, 3)
		bag_count = flt(self.bag_count) or 0
		bag_weight = flt(self.bag_weight_kg) or 0
		self.total_deduction_kg = round((total_pct / 100.0) * bag_count * bag_weight, 3)

	# ── whitelisted helpers ────────────────────────────────────────────
	@frappe.whitelist(methods=["POST"])
	def populate_from_template(self, template_name=None):
		"""Populate parameters child table from QC Template (server-side instance method).

		Race-safe: clears existing rows then appends. Persists via .save().
		"""
		template_name = template_name or self.qc_template
		if not template_name:
			frappe.throw("QC Template is required.")
		tpl = frappe.get_doc("TS QC Template", template_name)
		self.set("parameters", [])
		for tpl_row in (tpl.parameters or []):
			self.append("parameters", {
				"parameter_code": tpl_row.parameter_code,
				"parameter_label": tpl_row.parameter_label,
				"parameter_type": tpl_row.parameter_type,
				"uom": tpl_row.uom,
				"standard_value": _format_standard(
					tpl_row.target_value, tpl_row.min_value, tpl_row.max_value
				),
				"direction": tpl_row.get("direction") or "In Range",
				"min_value": tpl_row.min_value,
				"max_value": tpl_row.max_value,
				"is_critical": tpl_row.is_critical,
				"deduction_per_unit": flt(tpl_row.get("deduction_per_unit")) or 1.0,
				"deduction_pct": 0,
			})
		if tpl.supports_bags and tpl.bag_type and not self.bag_type:
			self.bag_type = tpl.bag_type
			weight = frappe.db.get_value("TS Bag Type Master", tpl.bag_type, "standard_weight_kg")
			if weight:
				self.bag_weight_kg = weight
		self.save(ignore_permissions=False)
		return {"populated": len(self.parameters)}


@frappe.whitelist(methods=["POST"])
def populate_template_rows(qi_name=None, template_name=None):
	"""Module-level whitelisted helper. Returns the rows that should populate the
	child table on the client form WITHOUT persisting (client may not have saved yet).
	"""
	if not template_name:
		frappe.throw("template_name is required")

	# Permission gate — read perm on TS QC Template + write/create on TS QI
	if not frappe.has_permission("TS QC Template", "read"):
		raise frappe.PermissionError("No read permission on TS QC Template")
	# v2.9.12.7 — temp/new names ("new-ts-quality-inspection-XXX") cannot be passed
	# to has_permission(doc=name): Frappe internally fetches the doc and raises
	# DoesNotExistError ("X not found"). For new unsaved docs OR nonexistent names,
	# fall through to the create-perm path (Lesson 166 + Lesson 224).
	is_existing = bool(
		qi_name
		and isinstance(qi_name, str)
		and not qi_name.startswith("new-")
		and frappe.db.exists("TS Quality Inspection", qi_name)
	)
	if is_existing:
		if not frappe.has_permission("TS Quality Inspection", "write", doc=qi_name):
			raise frappe.PermissionError("No write permission on this Quality Inspection")
	else:
		if not frappe.has_permission("TS Quality Inspection", "create"):
			raise frappe.PermissionError("No create permission on TS Quality Inspection")

	if not frappe.db.exists("TS QC Template", template_name):
		frappe.throw(f"QC Template '{escape_html(template_name)}' not found")

	tpl = frappe.get_doc("TS QC Template", template_name)
	rows = []
	for tpl_row in (tpl.parameters or []):
		rows.append({
			"parameter_code": tpl_row.parameter_code,
			"parameter_label": tpl_row.parameter_label,
			"parameter_type": tpl_row.parameter_type,
			"uom": tpl_row.uom,
			"standard_value": _format_standard(
				tpl_row.target_value, tpl_row.min_value, tpl_row.max_value
			),
			"direction": tpl_row.get("direction") or "In Range",
			"min_value": tpl_row.min_value,
			"max_value": tpl_row.max_value,
			"is_critical": tpl_row.is_critical,
			"deduction_per_unit": flt(tpl_row.get("deduction_per_unit")) or 1.0,
			"deduction_pct": 0,
		})

	result = {"parameters": rows}
	if tpl.supports_bags and tpl.bag_type:
		result["bag_type"] = tpl.bag_type
	return result
