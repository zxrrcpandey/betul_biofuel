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
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime, escape_html


SUBMIT_ROLES = {"Accounts Manager", "System Manager", "Administrator"}
SYSTEM_FIELD_OVERRIDE_ROLES = {"System Manager", "Administrator"}
SYSTEM_FIELDS = ("system_deduction_pct", "system_deduction_kg")
# v2.9.5 — snapshot fields copied from parent QI; never user-editable on DS.
ACTUAL_SNAPSHOT_FIELDS = ("actual_deduction_pct", "actual_deduction_kg", "actual_deduction_reason")
SNAPSHOT_TOLERANCE = 0.001  # %  — guard against float-rounding when comparing snapshots

# v2.9.8 — standard deduction line types auto-populated on insert.
# Sourced from PO ts_deduction_overrides[deduction_code] when override exists; skip otherwise.
STANDARD_DEDUCTION_TYPES = ("Quality Deduction", "Dhalta", "Brokerage", "Unloading Charge")
DEDUCTION_CODE_MAP = {
	"Quality Deduction": "Quality",   # rate from Suggestion.actual_pct (not PO Override)
	"Dhalta": "Dhalta",
	"Brokerage": "Brokerage",
	"Unloading Charge": "Unloading",
}
# Display units for the line table — DEFAULT (overridden by actual rate_type at calc time)
DEDUCTION_UNIT_MAP = {
	"Quality Deduction": "%",
	"Dhalta": "kg/q",
	"Brokerage": "%",  # default; flips based on rate_type if overridden
	"Unloading Charge": "Rs/Bag",
}


def _unit_from_rate_type(rate_type):
	"""Map a rate_type string to a display unit for the line table."""
	rt = (rate_type or "").strip().lower()
	return {
		"percentage": "%",
		"per qtl": "Rs/q",
		"per quintal": "Rs/q",
		"per mt": "Rs/MT",
		"per metric ton": "Rs/MT",
		"per bag": "Rs/Bag",
		"per kg": "Rs/kg",
		"kg/q": "kg/q",
	}.get(rt, rate_type or "")


@frappe.whitelist(methods=["POST"])
def force_refresh_deduction_sheet(name):
	"""v2.9.8.4 — explicit re-fetch endpoint for the 'Refresh from Sources' button.

	Clears all v2.9.8 derived fields + the deduction lines table, then saves
	the doc. validate() runs the re-fetch helpers which now find empty fields
	and re-pull from the upstream chain (PO → PR → QI → Suggestion).

	Only allowed on draft DS. Returns the new state.
	"""
	doc = frappe.get_doc("TS Deduction Sheet", name)
	if doc.docstatus != 0:
		frappe.throw("Only draft Deduction Sheets can be refreshed.")

	# v2.9.8.x — explicit write-perm check BEFORE ignore_permissions save.
	# ignore_permissions is needed below to bypass the system_deduction_*
	# permlevel=1 guard during the controlled re-fetch path; the perm check
	# here ensures only users authorised to write the DS can trigger it.
	frappe.has_permission("TS Deduction Sheet", "write", doc=doc, throw=True)

	# v2.9.8.8 — graceful guard: if linked QI is missing/deleted, log + return
	# diagnostic instead of crashing on the validate path.
	if doc.quality_inspection and not frappe.db.exists(
		"TS Quality Inspection", doc.quality_inspection
	):
		frappe.log_error(
			message=f"DS {name} references missing QI {doc.quality_inspection}",
			title="ts_deduction_sheet.force_refresh orphan",
		)
		return {
			"ok": False,
			"name": name,
			"error": f"Linked Quality Inspection '{doc.quality_inspection}' no longer exists. Manual cleanup required.",
		}

	# Clear derived fields so the `if not self.X` guards in _auto_fetch_v298_fields
	# don't skip them. These will all be re-fetched in validate().
	clearable = [
		"grn_reference", "net_qty_kg", "net_qty_quintal", "rate_per_quintal",
		"grn_amount", "bag_count",
		"supplier_name", "item_code", "item_name", "item_category",
		"purchase_order", "token_number",
	]
	for f in clearable:
		setattr(doc, f, None)
	# Clear deduction lines so _auto_populate_deduction_lines re-creates them
	doc.set("deductions", [])
	doc.flags.ignore_permissions = True
	doc.save()
	return {
		"ok": True,
		"name": name,
		"grn_reference": doc.grn_reference,
		"grn_amount": flt(doc.grn_amount),
		"total_deduction": flt(doc.total_deduction),
		"net_payable": flt(doc.net_payable),
		"line_count": len(doc.deductions or []),
	}


def _is_full_override_enabled():
	"""v2.9.8 kill switch — when ON, CEO can edit any deduction line field.
	Raw SQL on tabSingles per Lesson 171/172. Fail closed.
	"""
	try:
		v = frappe.db.sql(
			"""SELECT value FROM tabSingles
			   WHERE doctype='TS Settings' AND field='ts_ds_full_override_enabled' LIMIT 1"""
		)
		return bool(v and v[0][0] and str(v[0][0]).strip() not in ("0", "", "None", "False", "false"))
	except Exception:
		return False


class TSDeductionSheet(Document):
	# ── lifecycle ──────────────────────────────────────────────────────
	def before_insert(self):
		self._auto_fetch_references()
		self._copy_qi_system_deduction()
		# v2.9.6: snapshot Layer-2 from TS Deduction Suggestion (per-role doctype boundary).
		# Falls back to system value defaults if no Suggestion is linked (legacy/manual path).
		self._copy_from_suggestion()
		# v2.9.8: auto-fetch new derived fields + populate 3 standard deduction lines
		self._auto_fetch_v298_fields()
		self._auto_populate_deduction_lines()

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
		# v2.9.8.1: re-fetch missing v2.9.8 derived fields on EVERY save —
		# handles the case where DS was drafted before PR existed (PR created later).
		# Only fills NULL/0 fields; doesn't overwrite manual values.
		self._auto_fetch_v298_fields()
		# v2.9.8.1: also re-populate deduction lines when empty (e.g. DS created
		# before PO was linked, then PO added). Idempotent — only fires if empty.
		if not self.deductions:
			self._auto_populate_deduction_lines()
		# v2.9.8: recompute deduction line amounts from header values + sources.
		# Mirrors Quality line.rate to legacy actual_deduction_pct so dashboards stay consistent.
		self._recalculate_deduction_amounts()
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
		# v2.9.8 hard switch: net_payable = grn_amount − total_deduction.
		# grn_amount is the new authoritative figure (Net Qty Quintal × Rate per Quintal).
		# Legacy mrn_amount path is preserved only as fallback for legacy DS rows
		# that don't have grn_amount populated yet.
		if flt(self.grn_amount) > 0:
			self.net_payable = flt(self.grn_amount) - flt(self.total_deduction)
		else:
			self.net_payable = flt(self.mrn_amount) - flt(self.total_deduction)

	# ── v2.9.8 — auto-fetch new derived header fields ────────────────────
	def _auto_fetch_v298_fields(self):
		"""Populate posting_date, grn_reference, net_qty_kg/quintal,
		rate_per_quintal, bag_count, grn_amount on insert.
		"""
		from frappe.utils import today
		if not self.posting_date:
			self.posting_date = today()

		# v2.9.8.1: backfill missing PO from QI (when DS was created before PO chain ran).
		# QI's purchase_order is set in QI._auto_fetch_references → snapshot to DS.
		# v2.9.8.8: silently skip if linked QI no longer exists (orphan DS — survives validate).
		if self.quality_inspection and not frappe.db.exists(
			"TS Quality Inspection", self.quality_inspection
		):
			return  # orphan — leave fields as-is; user must cancel + recreate
		if self.quality_inspection and not self.purchase_order:
			qi_po = frappe.db.get_value(
				"TS Quality Inspection", self.quality_inspection, "purchase_order"
			)
			if qi_po:
				self.purchase_order = qi_po
		# Same for token_number, supplier_name, item_code if missing
		if self.quality_inspection:
			qi_data = frappe.db.get_value(
				"TS Quality Inspection",
				self.quality_inspection,
				["token_number", "item_code", "item_name", "item_category", "party_name"],
				as_dict=True,
			)
			if qi_data:
				if not self.token_number:
					self.token_number = qi_data.token_number
				if not self.item_code:
					self.item_code = qi_data.item_code
				if not self.item_name:
					self.item_name = qi_data.item_name
				if not self.item_category:
					self.item_category = qi_data.item_category
				if not self.supplier_name:
					self.supplier_name = qi_data.party_name

		# bag_count from QI
		if self.quality_inspection and not self.bag_count:
			self.bag_count = frappe.db.get_value(
				"TS Quality Inspection", self.quality_inspection, "bag_count"
			) or 0

		# rate_per_quintal from PO Item (kg→quintal conversion if needed)
		if self.purchase_order and self.item_code and not self.rate_per_quintal:
			po_item = frappe.db.get_value(
				"Purchase Order Item",
				{"parent": self.purchase_order, "item_code": self.item_code},
				["rate", "uom"],
				as_dict=True,
			)
			if po_item:
				rate = flt(po_item.rate)
				uom = (po_item.uom or "").lower()
				if uom in ("kg", "kilogram"):
					self.rate_per_quintal = round(rate * 100, 2)
				elif uom in ("ton", "mt", "metric ton"):
					self.rate_per_quintal = round(rate / 10, 2)
				else:  # default = quintal/qtl
					self.rate_per_quintal = round(rate, 2)

		# grn_reference + net_qty_kg + grn_amount all from PR (the actual GRN doc)
		# v2.9.8.9: GRN Amount now sourced from PR.net_total (the real receipt value)
		# instead of computed (qty × po_rate) — keeps DS consistent with what was
		# actually received + billed at the GRN.
		if self.token_number and not self.grn_reference:
			pr = frappe.get_all(
				"Purchase Receipt",
				filters={"ts_token": self.token_number, "docstatus": ["!=", 2]},
				fields=["name", "ts_wb_net_kg", "total_qty", "net_total"],
				limit=1,
			)
			if pr:
				self.grn_reference = pr[0].name
				if not self.net_qty_kg:
					self.net_qty_kg = flt(pr[0].ts_wb_net_kg or 0)
				# v2.9.8.9: GRN Amount from PR.net_total (authoritative)
				if not self.grn_amount:
					self.grn_amount = flt(pr[0].net_total or 0)

		# Derived: net_qty_quintal = kg / 100
		self.net_qty_quintal = round(flt(self.net_qty_kg) / 100.0, 3)
		# v2.9.8.9: only fall back to (qty × rate) if PR didn't provide net_total.
		# Once PR.net_total is set, that's authoritative.
		if not flt(self.grn_amount):
			self.grn_amount = round(flt(self.net_qty_quintal) * flt(self.rate_per_quintal), 2)

	# ── v2.9.8 — auto-populate 3 standard deduction lines ────────────────
	def _auto_populate_deduction_lines(self):
		"""Idempotent — skip if any existing rows present (amend / reload).

		v2.9.8.1 — restructured so Quality Deduction line populates even when
		PO is missing (Quality only needs Suggestion/QI). Dhalta + Unloading
		require PO override rows; skipped if PO/overrides absent.
		"""
		if self.deductions:
			return

		# Row 1: Quality Deduction — rate from Suggestion.actual_pct (already on DS as actual_deduction_pct)
		# Does NOT require PO — populates whenever a Suggestion is linked OR system_deduction_pct exists.
		quality_pct = flt(self.actual_deduction_pct) or flt(self.system_deduction_pct) or 0
		if quality_pct > 0 or self.deduction_suggestion or self.quality_inspection:
			self.append("deductions", {
				"deduction_type": "Quality Deduction",
				"description": "Auto-fetched from Deduction Suggestion (Grain Manager)",
				"rate": quality_pct,
				"rate_type": "Percentage",
				"unit": DEDUCTION_UNIT_MAP["Quality Deduction"],
				"base_value": flt(self.grn_amount),
				"calculated_amount": 0,
				"actual_amount": 0,
				"is_overridden": 0,
			})

		# Rows 2 + 3: Dhalta + Unloading — require PO with override rows.
		# Skip silently if PO missing or no override rows.
		if not self.purchase_order:
			return
		overrides = frappe.get_all(
			"TS PO Deduction Override",
			filters={"parent": self.purchase_order, "parenttype": "Purchase Order"},
			fields=["deduction_code", "default_rate", "default_rate_type", "override_rate", "override_rate_type"],
		)
		# Index by uppercase code (case-insensitive — POs may have DHALTA or Dhalta)
		by_code = {(o.deduction_code or "").strip().upper(): o for o in overrides}

		def _effective_rate(row):
			"""Return override_rate if user customized (>0), else default_rate."""
			if not row:
				return 0, None
			ov = flt(row.override_rate)
			if ov > 0:
				return ov, row.override_rate_type
			return flt(row.default_rate), row.default_rate_type

		# v2.9.8.7 — GENERIC PO-driven population. Loop through ALL overrides on the PO
		# (skip QUALITY — that comes from Suggestion above) and create a line for each
		# code with effective rate > 0. Display label maps known codes to canonical
		# names; unknown codes use Title-cased code as label. Rate-type-aware formula
		# in _recalculate_deduction_amounts handles all units uniformly.
		KNOWN_LABELS = {
			"DHALTA": ("Dhalta", "kg/q"),
			"BROKERAGE": ("Brokerage", None),  # unit derived from rate_type
			"UNLOADING": ("Unloading Charge", "Rs/Bag"),
		}
		for o in overrides:
			code = (o.deduction_code or "").strip().upper()
			if code in ("", "QUALITY"):  # Quality comes from Suggestion, handled above
				continue
			rate, rt = _effective_rate(o)
			if rate <= 0:
				continue
			label_unit = KNOWN_LABELS.get(code)
			if label_unit:
				dtype, fixed_unit = label_unit
				display_unit = fixed_unit if fixed_unit else _unit_from_rate_type(rt)
			else:
				# Unknown code — use Title-case of the code
				dtype = (o.deduction_label or code.title())
				display_unit = _unit_from_rate_type(rt)
			self.append("deductions", {
				"deduction_type": dtype,
				"description": "Auto-fetched from PO Deduction Terms",
				"rate": rate,
				"rate_type": rt or "Percentage",
				"unit": display_unit,
				"base_value": 0,  # set in recalc based on rate_type
				"calculated_amount": 0,
				"actual_amount": 0,
				"is_overridden": 0,
			})

	# ── v2.9.8 — recompute line amounts on every save ────────────────────
	def _recalculate_deduction_amounts(self):
		"""Walks the 3 standard rows + applies formulas. Mirrors Quality line.rate
		back to legacy actual_deduction_pct so qc_dashboard_api stays consistent.

		Default behaviour: line.rate is locked to source (Suggestion / PO Override).
		If ts_ds_full_override_enabled=1, rate is left as user-edited.
		"""
		full_override = _is_full_override_enabled()
		rate_per_kg = flt(self.rate_per_quintal) / 100.0 if flt(self.rate_per_quintal) else 0

		# Live re-source rates from upstream UNLESS full-override is on
		# v2.9.8.1 — case-insensitive code lookup + fallback to default_rate when override=0
		# v2.9.8.5 — also capture rate_type so Brokerage etc. can switch unit semantics
		if not full_override and self.purchase_order:
			overrides = frappe.get_all(
				"TS PO Deduction Override",
				filters={"parent": self.purchase_order, "parenttype": "Purchase Order"},
				fields=["deduction_code", "default_rate", "default_rate_type",
				        "override_rate", "override_rate_type"],
			)
			by_code = {}
			for o in overrides:
				code = (o.deduction_code or "").strip().upper()
				ov = flt(o.override_rate)
				if ov > 0:
					by_code[code] = (ov, o.override_rate_type)
				else:
					by_code[code] = (flt(o.default_rate), o.default_rate_type)
		else:
			by_code = {}

		for row in (self.deductions or []):
			dtype = row.deduction_type or ""

			# Re-source rate + rate_type from PO (snapshot integrity preserved unless full-override on).
			# v2.9.8.7 — generic: match line.deduction_type back to upstream PO override code.
			if not full_override:
				if dtype == "Quality Deduction":
					row.rate = flt(self.actual_deduction_pct) or flt(self.system_deduction_pct) or 0
				else:
					# Map Title-case label back to upstream UPPER code
					code_lookup = {
						"Dhalta": "DHALTA",
						"Brokerage": "BROKERAGE",
						"Unloading Charge": "UNLOADING",
					}.get(dtype, (dtype or "").upper())
					if code_lookup in by_code:
						rate, rt = by_code[code_lookup]
						row.rate = rate
						# Allow rate_type + unit to flip if user changed it on PO
						if dtype == "Brokerage" and rt:
							row.rate_type = rt
							row.unit = _unit_from_rate_type(rt)
						elif dtype not in ("Dhalta", "Unloading Charge"):
							# unknown code — adapt rate_type display
							row.rate_type = rt or row.rate_type
							row.unit = _unit_from_rate_type(rt) if rt else row.unit

			# Compute amount per type
			if dtype == "Quality Deduction":
				amt = flt(self.grn_amount) * flt(row.rate) / 100.0
				row.base_value = flt(self.grn_amount)
				row.formula_summary = (
					f"GRN ₹{flt(self.grn_amount):.2f} × {flt(row.rate):.3f}% = ₹{amt:.2f}"
				)
			elif dtype == "Dhalta":
				kg = flt(self.net_qty_quintal) * flt(row.rate)
				amt = kg * rate_per_kg
				row.base_value = flt(self.net_qty_quintal)
				row.formula_summary = (
					f"{flt(self.net_qty_quintal):.3f} q × {flt(row.rate):.3f} kg/q × "
					f"₹{rate_per_kg:.2f}/kg = ₹{amt:.2f}"
				)
			elif dtype == "Brokerage":
				rt = (row.rate_type or "Percentage").strip().lower()
				rate = flt(row.rate)
				if rt == "percentage":
					amt = flt(self.grn_amount) * rate / 100.0
					row.base_value = flt(self.grn_amount)
					row.formula_summary = f"GRN ₹{flt(self.grn_amount):.2f} × {rate:.3f}% = ₹{amt:.2f}"
				elif rt in ("per qtl", "per quintal"):
					amt = flt(self.net_qty_quintal) * rate
					row.base_value = flt(self.net_qty_quintal)
					row.formula_summary = f"{flt(self.net_qty_quintal):.3f} q × ₹{rate:.2f}/q = ₹{amt:.2f}"
				elif rt in ("per mt", "per metric ton"):
					mt = flt(self.net_qty_kg) / 1000.0
					amt = mt * rate
					row.base_value = mt
					row.formula_summary = f"{mt:.3f} MT × ₹{rate:.2f}/MT = ₹{amt:.2f}"
				elif rt == "per bag":
					amt = flt(self.bag_count) * rate
					row.base_value = flt(self.bag_count)
					row.formula_summary = f"{int(flt(self.bag_count))} bags × ₹{rate:.2f}/bag = ₹{amt:.2f}"
				else:
					# Unknown rate_type — fallback to percentage of GRN
					amt = flt(self.grn_amount) * rate / 100.0
					row.base_value = flt(self.grn_amount)
					row.formula_summary = f"GRN ₹{flt(self.grn_amount):.2f} × {rate:.3f}% = ₹{amt:.2f} (fallback)"
			elif dtype == "Unloading Charge":
				amt = flt(self.bag_count) * flt(row.rate)
				row.base_value = flt(self.bag_count)
				row.formula_summary = (
					f"{int(flt(self.bag_count))} bags × ₹{flt(row.rate):.2f}/bag = ₹{amt:.2f}"
				)
			else:
				# v2.9.8.7 — generic rate_type dispatch for any unknown deduction code.
				# Same engine as Brokerage: figure out the unit-of-measure from rate_type
				# and apply the appropriate formula.
				rt = (row.rate_type or "Percentage").strip().lower()
				rate = flt(row.rate)
				if rt == "percentage":
					amt = flt(self.grn_amount) * rate / 100.0
					row.base_value = flt(self.grn_amount)
					row.formula_summary = f"GRN ₹{flt(self.grn_amount):.2f} × {rate:.3f}% = ₹{amt:.2f}"
				elif rt in ("per qtl", "per quintal"):
					amt = flt(self.net_qty_quintal) * rate
					row.base_value = flt(self.net_qty_quintal)
					row.formula_summary = f"{flt(self.net_qty_quintal):.3f} q × ₹{rate:.2f}/q = ₹{amt:.2f}"
				elif rt in ("per mt", "per metric ton"):
					mt = flt(self.net_qty_kg) / 1000.0
					amt = mt * rate
					row.base_value = mt
					row.formula_summary = f"{mt:.3f} MT × ₹{rate:.2f}/MT = ₹{amt:.2f}"
				elif rt == "per bag":
					amt = flt(self.bag_count) * rate
					row.base_value = flt(self.bag_count)
					row.formula_summary = f"{int(flt(self.bag_count))} bags × ₹{rate:.2f}/bag = ₹{amt:.2f}"
				elif rt == "per kg":
					amt = flt(self.net_qty_kg) * rate
					row.base_value = flt(self.net_qty_kg)
					row.formula_summary = f"{flt(self.net_qty_kg):.3f} kg × ₹{rate:.2f}/kg = ₹{amt:.2f}"
				else:
					# Unknown rate_type — preserve manually-entered amount
					amt = flt(row.calculated_amount or row.actual_amount)
					row.formula_summary = f"({rt}) — manual entry: ₹{amt:.2f}"

			row.calculated_amount = round(amt, 2)
			# actual_amount: respect user's manual override when is_overridden=1; else mirror calculated
			if not row.is_overridden:
				row.actual_amount = round(amt, 2)

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


# ═══════════════════════════════════════════════════════════════════════
#  v2.9.10 — CONNECTIONS PANEL ENDPOINT
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_connections(ds_name):
	"""Return related-docs structure for the DS Connections panel.

	Read-only endpoint. Two-layer permission gating:
	  1. Caller must have READ on the DS itself (throws if denied)
	  2. Per-row READ check on each related doc — silently OMITS docs the
	     user can't see (Lesson 168: operator roles often lack read on PI/SE).
	  3. Each per-doctype query is wrapped in try/except so a single broken
	     query doesn't kill the whole panel.

	Returns:
	  {
	    "sections": [
	      {"label": "Source Chain", "icon": "🚛", "items": [
	        {"doctype": "TS Token", "name": "...", "label": "...", "status": "...",
	         "docstatus": 1, "url": "/app/ts-token/..."}
	      ]},
	      ...
	    ]
	  }
	"""
	# 1. Read permission on DS itself
	if not frappe.has_permission("TS Deduction Sheet", "read",
		doc=ds_name, throw=False):
		return {"sections": [], "error": "no_permission"}

	ds = frappe.db.get_value("TS Deduction Sheet", ds_name,
		["token_number", "gate_entry", "purchase_order", "quality_inspection",
		 "grn_reference", "deduction_suggestion", "amended_from"], as_dict=True)
	if not ds:
		return {"sections": [], "error": "not_found"}

	def _safe(fn):
		"""Run a section-builder; return [] on any error (logged)."""
		try:
			return fn() or []
		except Exception as e:
			try:
				frappe.log_error(
					title="v2.9.10 get_connections",
					message=f"DS {ds_name}: {fn.__name__} failed: {e}",
				)
			except Exception:
				pass
			return []

	def _doc(doctype, name, status_field=None, label_field=None):
		"""Build a single connection row dict if user has read perm; else None."""
		if not name:
			return None
		try:
			if not frappe.has_permission(doctype, "read", doc=name, throw=False):
				return None  # silently omit (Lesson 168)
			fields = ["docstatus"]
			if status_field:
				fields.append(status_field)
			if label_field:
				fields.append(label_field)
			row = frappe.db.get_value(doctype, name, fields, as_dict=True)
			if not row:
				return None
			docstatus_label = {0: "Draft", 1: "Submitted", 2: "Cancelled"}[int(row.docstatus or 0)]
			status = row.get(status_field) if status_field else docstatus_label
			# Override docstatus_label for cancelled
			if int(row.docstatus or 0) == 2:
				status = "Cancelled"
			return {
				"doctype": doctype,
				"name": name,
				"label": (row.get(label_field) if label_field else "") or "",
				"status": status or docstatus_label,
				"docstatus": int(row.docstatus or 0),
				"url": "/app/" + doctype.lower().replace(" ", "-") + "/" + name,
			}
		except Exception:
			return None

	# Section 1 — Source Chain
	def source_chain():
		items = []
		t = _doc("TS Token", ds.token_number, status_field="status")
		if t: items.append(t)
		g = _doc("TS Gate Entry", ds.gate_entry, status_field=None)
		if g: items.append(g)
		return items

	# Section 2 — Procurement
	def procurement():
		items = []
		# Material Request(s) via PO.items[].material_request
		if ds.purchase_order:
			mr_names = frappe.db.sql_list("""
				SELECT DISTINCT material_request
				FROM `tabPurchase Order Item`
				WHERE parent = %s AND material_request IS NOT NULL AND material_request != ''
			""", (ds.purchase_order,))
			for mr_name in (mr_names or [])[:10]:
				m = _doc("Material Request", mr_name, status_field="status")
				if m: items.append(m)
		# Purchase Order
		p = _doc("Purchase Order", ds.purchase_order, status_field="status")
		if p: items.append(p)
		return items

	# Section 3 — Receipt + Quality
	def receipt_quality():
		items = []
		pr = _doc("Purchase Receipt", ds.grn_reference, status_field="status")
		if pr: items.append(pr)
		qi = _doc("TS Quality Inspection", ds.quality_inspection, status_field=None)
		if qi: items.append(qi)
		ds_sugg = _doc("TS Deduction Suggestion", ds.deduction_suggestion, status_field="status")
		if ds_sugg: items.append(ds_sugg)
		return items

	# Section 4 — Settlement
	def settlement():
		items = []
		# Purchase Invoice(s) via PI Item.purchase_receipt = DS.grn_reference
		if ds.grn_reference:
			pi_names = frappe.db.sql_list("""
				SELECT DISTINCT parent
				FROM `tabPurchase Invoice Item`
				WHERE purchase_receipt = %s
			""", (ds.grn_reference,))
			for pi_name in (pi_names or [])[:10]:
				p = _doc("Purchase Invoice", pi_name, status_field="status")
				if p: items.append(p)
		# Stock Entry(s) via SE Detail.material_request OR SE Detail child rows referencing the PR
		se_set = set()
		if ds.grn_reference:
			# SE that reverses or relates to this PR — ERPNext SE Detail has
			# `material_request` (SE from MR) but no direct PR link by default;
			# use SE.purchase_receipt header field if present (Frappe v15 has it
			# on SE for return-from-purchase). Fallback: skip.
			try:
				se_via_pr = frappe.db.sql_list("""
					SELECT name FROM `tabStock Entry`
					WHERE purchase_receipt_no = %s AND docstatus < 2
				""", (ds.grn_reference,)) or []
				se_set.update(se_via_pr)
			except Exception:
				pass  # column may not exist on this site — silently skip
		# Also: SE built from any of the linked Material Requests via PO chain
		if ds.purchase_order:
			mr_names = frappe.db.sql_list("""
				SELECT DISTINCT material_request
				FROM `tabPurchase Order Item`
				WHERE parent = %s AND material_request IS NOT NULL AND material_request != ''
			""", (ds.purchase_order,)) or []
			for mr_name in mr_names:
				se_via_mr = frappe.db.sql_list("""
					SELECT DISTINCT parent FROM `tabStock Entry Detail`
					WHERE material_request = %s
				""", (mr_name,)) or []
				se_set.update(se_via_mr)
		for se_name in sorted(se_set, reverse=True)[:10]:
			s = _doc("Stock Entry", se_name, status_field=None)
			if s: items.append(s)
		return items

	sections = [
		{"label": "Source Chain", "icon": "🚛", "items": _safe(source_chain)},
		{"label": "Procurement", "icon": "📋", "items": _safe(procurement)},
		{"label": "Receipt + Quality", "icon": "📦", "items": _safe(receipt_quality)},
		{"label": "Settlement", "icon": "💰", "items": _safe(settlement)},
	]

	return {"sections": sections}
