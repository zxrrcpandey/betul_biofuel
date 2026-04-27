"""
TS Quality Inspection — v2.9.0 Day 4 restructure.

Submittable DocType (docstatus 0/1/2). Drops legacy `status` field.
Adds:
  - quality_report_no: auto-generated BBPL-QR-YY-##### (race-safe via make_autoname)
  - lab_register_no: optional manual reference
  - qc_template: drives parameters child table population
  - bag_type / bag_count / bag_weight_kg: bagging info
  - parameters: TS QI Parameter Result child rows
  - total_deduction_pct / total_deduction_kg: auto-calc from rows

Lifecycle:
  before_insert  → autofetch refs (token, GE, PO, item) + auto-generate Quality Report No
  validate       → calc legacy variances + total_deduction_pct + total_deduction_kg
  before_submit  → mandate decision + grade + parameters
  on_submit      → kept for ts_qc_auto_reject hook (now reads decision instead of status)
  on_cancel      → unwind PR.ts_qc_status flag

Lesson references:
  - 192: NEVER use `after_save` doc_event — use `on_update`. We use Frappe's native submit hooks.
  - 200: amend cycle preserves `amended_from` — exempt from autoname collision (unique idx tolerates).
"""

import frappe
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import now_datetime, flt, escape_html


GRAIN_NOTIFY_RECIPIENTS = [
	"grain.manager@betulbiofuel.com",  # Tilok Katariya
]
GRAIN_NOTIFY_ROLES = ["Accounts Manager", "Accounts User", "Grain Manager"]


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

	def before_submit(self):
		# Hard gates on submit
		if not self.decision:
			frappe.throw("Please set a Decision (Accept / Reject / Hold) before submitting.")
		if not self.grade:
			frappe.throw("Please set a Grade before submitting.")
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
		# Send notification to Accounts Manager + Accounts User + Grain Manager (Tilok)
		# Wrapped in try so notification failure NEVER blocks submit (Lesson 196 spirit).
		try:
			self._notify_deduction_review()
		except Exception as e:
			frappe.log_error(
				message=f"QI {self.name} on_submit notification failed: {e}",
				title="ts_quality_inspection.on_submit notify",
			)

	def on_cancel(self):
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

	# ── helpers ────────────────────────────────────────────────────────
	def _validate_token_status(self):
		if not self.token_number:
			return
		token_status = frappe.db.get_value("TS Token", self.token_number, "status")
		# Allow QI from Gross Weighed onwards (matches v2.8.1 Phase B flexible flow)
		allowed_new = (
			"Gross Weighed", "Tare Weighed", "GRN Created",
			"Plant Exited", "Campus Exited", "Exited",
		)
		allowed_legacy = ("Gross Weighed",)
		try:
			qc_gate_on = bool(frappe.db.get_single_value("TS Settings", "ts_qc_gate_enabled"))
		except Exception:
			qc_gate_on = False
		allowed = allowed_new if qc_gate_on else allowed_legacy
		if token_status not in allowed:
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
		"""
		Race-safe Quality Report No: BBPL-QR-YY-##### using Frappe's make_autoname.

		make_autoname uses tabSeries with row-level locking — same pattern as
		BBPL-MR-* counters (Lesson 136). NEVER use SELECT-then-INSERT.
		"""
		# Format: BBPL-QR-YY-##### (5-digit zero-padded, year-2-digit)
		series_pattern = "BBPL-QR-.YY.-.#####"
		generated = make_autoname(series_pattern)
		return generated

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
		"""v2.9.0.8 rate-based deduction calc. Per row:

		    deduction_pct = max(0, (actual_value - max_value)) * deduction_per_unit

		Default rate = 1.0 if deduction_per_unit empty (matches Option A simple subtract).
		Only Numeric parameter_type rows are processed; Boolean/Text/Select skipped.
		Lesson 200: amend cycle preserves amended_from. We still recalc on validate
		but the JSON makes deduction_pct read_only so the field remains tamper-protected
		via standard Frappe permissions (no manual entry).
		"""
		for row in (self.parameters or []):
			if row.parameter_type != "Numeric":
				continue  # skip Pass/Fail / Boolean / Text / Select
			try:
				actual = flt(row.actual_value)
				max_val = flt(row.max_value)
				rate = flt(row.deduction_per_unit) or 1.0
			except (TypeError, ValueError):
				continue
			# Guard: empty/null actual_value flt() returns 0; if user hasn't entered
			# a value yet, leave deduction at 0 instead of "0 - max" negative.
			if not row.actual_value or str(row.actual_value).strip() == "":
				row.deduction_pct = 0
				continue
			excess = max(0, actual - max_val)
			row.deduction_pct = round(excess * rate, 3)

	def _calculate_total_deductions(self):
		"""Sum row-level deduction_pct → total_deduction_pct → total_deduction_kg."""
		total_pct = sum(flt(row.deduction_pct) for row in (self.parameters or []))
		self.total_deduction_pct = round(total_pct, 3)
		# Total kg = total_pct / 100 * bag_count * bag_weight_kg
		bag_count = flt(self.bag_count) or 0
		bag_weight = flt(self.bag_weight_kg) or 0
		self.total_deduction_kg = round((total_pct / 100.0) * bag_count * bag_weight, 3)

	def _notify_deduction_review(self):
		"""On submit, notify Accounts Manager + Accounts User + grain manager (Tilok)."""
		# Build recipient set — only enabled users to avoid notifying disabled accounts.
		recipients = set()
		role_users = frappe.get_all(
			"Has Role",
			filters={"role": ["in", GRAIN_NOTIFY_ROLES], "parenttype": "User"},
			pluck="parent",
		)
		# Filter to enabled User accounts only (avoids notifying retired / disabled users)
		if role_users:
			enabled_users = frappe.get_all(
				"User",
				filters={"name": ["in", role_users], "enabled": 1},
				pluck="name",
			)
			for u in enabled_users:
				if u and u not in ("Administrator", "Guest"):
					recipients.add(u)
		# Hardcoded grain manager email (Tilok) — only if such user exists AND enabled
		for email in GRAIN_NOTIFY_RECIPIENTS:
			if frappe.db.exists("User", {"name": email, "enabled": 1}):
				recipients.add(email)
		if not recipients:
			return

		subject = (
			f"Quality Report {self.quality_report_no} ready for deduction review"
		)
		# DS form pre-linked to this QI:
		ds_link = (
			f"/app/ts-deduction-sheet/new?quality_inspection={self.name}"
		)
		qi_link = f"/app/ts-quality-inspection/{self.name}"
		body_html = (
			f"<p>Quality Report <b>{escape_html(self.quality_report_no or self.name)}</b> "
			f"has been submitted and is ready for deduction review.</p>"
			f"<table style='border-collapse:collapse;margin:8px 0;'>"
			f"<tr><td style='padding:4px 12px 4px 0;'><b>Quality Report No:</b></td>"
			f"<td>{escape_html(self.quality_report_no or '')}</td></tr>"
			f"<tr><td style='padding:4px 12px 4px 0;'><b>Token:</b></td>"
			f"<td>{escape_html(self.token_number or '')}</td></tr>"
			f"<tr><td style='padding:4px 12px 4px 0;'><b>Item:</b></td>"
			f"<td>{escape_html(self.item_name or self.item_code or '')}</td></tr>"
			f"<tr><td style='padding:4px 12px 4px 0;'><b>Total Deduction %:</b></td>"
			f"<td>{escape_html(str(self.total_deduction_pct or 0))}</td></tr>"
			f"<tr><td style='padding:4px 12px 4px 0;'><b>Total Deduction (Kg):</b></td>"
			f"<td>{escape_html(str(self.total_deduction_kg or 0))}</td></tr>"
			f"<tr><td style='padding:4px 12px 4px 0;'><b>Decision:</b></td>"
			f"<td>{escape_html(self.decision or '')}</td></tr>"
			f"</table>"
			f"<p>"
			f"<a href='{escape_html(ds_link)}' "
			f"style='display:inline-block;padding:8px 16px;background:#5e64ff;"
			f"color:#fff;text-decoration:none;border-radius:4px;font-weight:600;'>"
			f"View Details &amp; Create Deduction Sheet</a>"
			f"&nbsp;&nbsp;"
			f"<a href='{escape_html(qi_link)}' "
			f"style='display:inline-block;padding:8px 16px;background:#fff;"
			f"color:#5e64ff;text-decoration:none;border:1px solid #5e64ff;"
			f"border-radius:4px;font-weight:600;'>"
			f"View Quality Report</a>"
			f"</p>"
		)

		# Bell notifications (Notification Log) for each recipient
		for user in recipients:
			try:
				frappe.get_doc({
					"doctype": "Notification Log",
					"for_user": user,
					"subject": subject,
					"email_content": body_html,
					"type": "Alert",
					"document_type": "TS Quality Inspection",
					"document_name": self.name,
				}).insert(ignore_permissions=True)
			except Exception as e:
				frappe.log_error(
					message=f"Notification Log insert failed for {user}: {e}",
					title="ts_quality_inspection notify",
				)

		# Email — best-effort, don't block on smtp failure
		try:
			frappe.sendmail(
				recipients=list(recipients),
				subject=subject,
				message=body_html,
				reference_doctype="TS Quality Inspection",
				reference_name=self.name,
				now=False,  # queue
			)
		except Exception as e:
			frappe.log_error(
				message=f"sendmail failed for QI {self.name}: {e}",
				title="ts_quality_inspection sendmail",
			)

	# ── whitelisted helpers ────────────────────────────────────────────
	# Lesson 175: instance method that calls self.save() must be POST-only
	# to enforce CSRF protection.
	@frappe.whitelist(methods=["POST"])
	def populate_from_template(self, template_name=None):
		"""
		Populate parameters child table from QC Template (server-side instance method).

		Race-safe: clears existing rows then appends. Caller (JS) should
		confirm before invoking if rows already exist. Persists via .save().
		"""
		template_name = template_name or self.qc_template
		if not template_name:
			frappe.throw("QC Template is required.")
		tpl = frappe.get_doc("TS QC Template", template_name)
		# Clear existing parameter rows
		self.set("parameters", [])
		for tpl_row in (tpl.parameters or []):
			self.append("parameters", {
				"parameter_code": tpl_row.parameter_code,
				"parameter_label": tpl_row.parameter_label,
				"parameter_type": tpl_row.parameter_type,
				"uom": tpl_row.uom,
				"standard_value": tpl_row.target_value,
				"min_value": tpl_row.min_value,
				"max_value": tpl_row.max_value,
				"is_critical": tpl_row.is_critical,
				# v2.9.0.8: copy rate from template (default 1.0 = simple subtract)
				"deduction_per_unit": flt(tpl_row.get("deduction_per_unit")) or 1.0,
				"deduction_pct": 0,
			})
		# Auto-set bag_type from template default if blank
		if tpl.supports_bags and tpl.bag_type and not self.bag_type:
			self.bag_type = tpl.bag_type
			weight = frappe.db.get_value("TS Bag Type Master", tpl.bag_type, "standard_weight_kg")
			if weight:
				self.bag_weight_kg = weight
		self.save(ignore_permissions=False)
		return {"populated": len(self.parameters)}


@frappe.whitelist(methods=["POST"])
def populate_template_rows(qi_name=None, template_name=None, qi_doc=None):
	"""
	Module-level whitelisted helper. Returns the rows that should populate the
	child table on the client form WITHOUT persisting (client may not have
	saved yet). Permission check: caller must have write perm on TS QI
	(or create perm if qi_name is None).

	Lesson 175: mutation-style endpoint — POST only.
	"""
	if not template_name:
		frappe.throw("template_name is required")

	# Permission gate — read perm on TS QC Template + write/create on TS QI
	if not frappe.has_permission("TS QC Template", "read"):
		raise frappe.PermissionError("No read permission on TS QC Template")
	if qi_name:
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
			"standard_value": tpl_row.target_value,
			"min_value": tpl_row.min_value,
			"max_value": tpl_row.max_value,
			"is_critical": tpl_row.is_critical,
			# v2.9.0.8: rate-based deduction calc
			"deduction_per_unit": flt(tpl_row.get("deduction_per_unit")) or 1.0,
			"deduction_pct": 0,
		})

	result = {"parameters": rows}
	if tpl.supports_bags and tpl.bag_type:
		result["bag_type"] = tpl.bag_type
	return result
