# TS Deduction Suggestion — v2.9.6
# Grain Manager confirms or overrides the QI system deduction value.
# One Suggestion per submitted QI (1:1). Submission auto-creates draft TS Deduction Sheet.
# Cancellation cascades to draft DS only (submitted DS must be cancelled manually first).

import frappe
from frappe.model.document import Document
from frappe.utils import flt


SUGGESTION_EDIT_ROLES = {"Grain Manager", "System Manager", "IT Head"}
DELTA_TOLERANCE = 0.01  # %  — reason mandatory above this delta


class TSDeductionSuggestion(Document):

	def before_insert(self):
		self._validate_qi_state()
		self._enforce_one_per_qi()
		self._snapshot_from_qi()

	def validate(self):
		self._validate_reason_on_delta()

	def before_submit(self):
		self._validate_submit_role()
		if self.actual_pct is None:
			frappe.throw("Actual Deduction % is mandatory before submitting.")

	def on_submit(self):
		try:
			self._auto_create_deduction_sheet()
		except Exception as e:
			frappe.log_error(
				message=f"Suggestion {self.name} auto-DS creation failed: {e}",
				title="ts_deduction_suggestion on_submit",
			)

	def on_cancel(self):
		try:
			self._cascade_cancel_deduction_sheet()
		except Exception as e:
			frappe.log_error(
				message=f"Suggestion {self.name} DS cascade failed: {e}",
				title="ts_deduction_suggestion on_cancel",
			)

	# ── helpers ──────────────────────────────────────────────────────

	def _validate_qi_state(self):
		if not self.quality_inspection:
			frappe.throw("Quality Inspection link is mandatory.")
		qi_status = frappe.db.get_value("TS Quality Inspection", self.quality_inspection, "docstatus")
		if qi_status is None:
			frappe.throw(f"Quality Inspection {self.quality_inspection} not found.")
		if qi_status != 1:
			frappe.throw(
				f"Source Quality Inspection {self.quality_inspection} must be submitted first."
			)

	def _enforce_one_per_qi(self):
		existing = frappe.db.get_value(
			"TS Deduction Suggestion",
			{"quality_inspection": self.quality_inspection, "docstatus": ["!=", 2]},
			"name",
		)
		if existing and existing != self.name:
			frappe.throw(
				f"A non-cancelled Deduction Suggestion ({existing}) already exists "
				f"for QI {self.quality_inspection}."
			)

	def _snapshot_from_qi(self):
		qi = frappe.db.get_value(
			"TS Quality Inspection",
			self.quality_inspection,
			["token_number", "item_category", "total_deduction_pct"],
			as_dict=True,
		)
		if not self.token_number:
			self.token_number = qi.token_number
		if not self.item_category:
			self.item_category = qi.item_category
		# Always snapshot system_pct from QI — read-only field, fresh each insert
		self.system_pct = flt(qi.total_deduction_pct or 0)
		# Default actual_pct to system value (Grain Manager overrides if needed)
		if self.actual_pct is None:
			self.actual_pct = self.system_pct

	def _validate_reason_on_delta(self):
		if self.actual_pct is None or self.system_pct is None:
			return
		delta = abs(flt(self.actual_pct) - flt(self.system_pct))
		if delta > DELTA_TOLERANCE and not (self.actual_reason or "").strip():
			frappe.throw(
				f"Actual Deduction ({flt(self.actual_pct):.3f}%) differs from System "
				f"({flt(self.system_pct):.3f}%) by {delta:.3f}%. Reason is mandatory."
			)

	def _validate_submit_role(self):
		if frappe.session.user == "Administrator":
			return
		user_roles = set(frappe.get_roles())
		if not (user_roles & SUGGESTION_EDIT_ROLES):
			raise frappe.PermissionError(
				"Only Grain Manager / System Manager / IT Head may submit a Deduction Suggestion."
			)

	def _auto_create_deduction_sheet(self):
		"""Idempotent — skip if any non-cancelled DS already exists for this QI.

		v2.9.6: check by QI link (not by Suggestion link) to handle legacy DS rows
		from v2.9.5.x or older flows that were created without a Suggestion link.
		If a legacy draft DS exists, we link the Suggestion to it so future flows
		stay consistent.
		"""
		existing = frappe.db.get_value(
			"TS Deduction Sheet",
			{"quality_inspection": self.quality_inspection, "docstatus": ["!=", 2]},
			["name", "docstatus", "deduction_suggestion"],
			as_dict=True,
		)
		if existing:
			# Legacy DS exists. If it's a draft and unlinked, attach this Suggestion.
			if existing.docstatus == 0 and not existing.deduction_suggestion:
				try:
					frappe.db.set_value(
						"TS Deduction Sheet", existing.name,
						"deduction_suggestion", self.name,
						update_modified=False,
					)
				except Exception:
					pass
			return
		frappe.flags.in_qi_internal = True
		try:
			ds = frappe.new_doc("TS Deduction Sheet")
			ds.quality_inspection = self.quality_inspection
			ds.deduction_suggestion = self.name
			ds.token_number = self.token_number
			ds.flags.ignore_permissions = True
			ds.insert(ignore_permissions=True)
		finally:
			frappe.flags.in_qi_internal = False
		try:
			self.add_comment(
				"Info",
				f"[DSG_AUTO_DS] DS {ds.name} auto-created on Suggestion submit "
				f"by {frappe.session.user}.",
			)
		except Exception:
			pass

	def _cascade_cancel_deduction_sheet(self):
		"""Cancel only DRAFT DS automatically. If DS is submitted, warn user."""
		ds_rows = frappe.get_all(
			"TS Deduction Sheet",
			filters={"deduction_suggestion": self.name, "docstatus": ["!=", 2]},
			fields=["name", "docstatus"],
		)
		for row in ds_rows:
			if row.docstatus == 0:
				frappe.flags.in_qi_internal = True
				try:
					frappe.delete_doc(
						"TS Deduction Sheet", row.name, ignore_permissions=True, force=True
					)
				finally:
					frappe.flags.in_qi_internal = False
			else:
				frappe.msgprint(
					f"Deduction Sheet {row.name} is submitted. "
					"Cancel the DS manually first if you need to revoke this Suggestion.",
					indicator="orange",
					alert=True,
				)


# ── module-level helpers (called from QI on_submit) ─────────────────


def get_grain_manager_recipients():
	"""Return enabled User emails holding Grain Manager role.

	Pure role-driven (no hardcoded recipients — feedback_over_engineering_audit Rule 6).
	"""
	role_users = frappe.get_all(
		"Has Role",
		filters={"role": "Grain Manager", "parenttype": "User"},
		pluck="parent",
	)
	if not role_users:
		return []
	enabled = frappe.get_all(
		"User",
		filters={"name": ["in", role_users], "enabled": 1},
		pluck="name",
	)
	return [u for u in enabled if u and u not in ("Administrator", "Guest")]


def notify_grain_manager_for_qi(qi_doc):
	"""Bell + email to all Grain Manager users when QI is submitted."""
	recipients = get_grain_manager_recipients()
	if not recipients:
		return
	from frappe.utils import escape_html

	esc_qi = escape_html(qi_doc.name or "")
	esc_user = escape_html(frappe.session.user or "")
	esc_total = escape_html(str(qi_doc.total_deduction_pct or 0))
	subject = f"QI {esc_qi} submitted — Deduction Suggestion ready for review"
	message = (
		f"<p>Quality Inspection <b>{esc_qi}</b> has been submitted by {esc_user}.</p>"
		f"<p>System Total Deduction: <b>{esc_total}%</b></p>"
		f"<p>A draft <b>Deduction Suggestion</b> has been auto-created. "
		f"Please review and submit it.</p>"
		f"<p><a href='/app/ts-quality-inspection/{esc_qi}'>View QI</a></p>"
	)
	for user in recipients:
		try:
			note = frappe.new_doc("Notification Log")
			note.subject = subject
			note.email_content = message
			note.for_user = user
			note.document_type = "TS Quality Inspection"
			note.document_name = qi_doc.name
			note.from_user = frappe.session.user or "Administrator"
			note.insert(ignore_permissions=True)
		except Exception:
			pass
	try:
		frappe.sendmail(
			recipients=list(recipients),
			subject=subject,
			message=message,
			reference_doctype="TS Quality Inspection",
			reference_name=qi_doc.name,
			delayed=False,
		)
	except Exception as e:
		frappe.log_error(
			message=f"QI {qi_doc.name} sendmail failed: {e}",
			title="ts_deduction_suggestion notify",
		)


def auto_create_suggestion_for_qi(qi_doc):
	"""Idempotent — skip if a non-cancelled Suggestion already references this QI."""
	existing = frappe.db.get_value(
		"TS Deduction Suggestion",
		{"quality_inspection": qi_doc.name, "docstatus": ["!=", 2]},
		"name",
	)
	if existing:
		return existing
	frappe.flags.in_qi_internal = True
	try:
		s = frappe.new_doc("TS Deduction Suggestion")
		s.quality_inspection = qi_doc.name
		s.flags.ignore_permissions = True
		s.insert(ignore_permissions=True)
	finally:
		frappe.flags.in_qi_internal = False
	return s.name
