# Copyright (c) 2026, Trustbit Software and contributors
# TS Returnable Gate Pass — controller (v2.47.0, RGP Phase A2).
#
# Design contract (plan 28 Aug 2026, user-approved):
# - Status lives in a read_only Select mutated ONLY via db_set from the POST
#   endpoints in ts_rgp.py. All actor/timestamp trios are control-plane fields
#   guarded by _block_gate_field_tampering (L162) — imported from
#   ts_po_approval, never re-implemented. The one legitimate doc.save() path
#   (record_rgp_return) sets doc.flags.ts_approval_workflow_call in try/finally
#   (L176) exactly like the Stores MR flow.
# - qty_returned / balance_qty / totals are RECOMPUTED from the returns table
#   on every save — tampering with them is self-healing.
# - Sec 143 clocks derive from challan_date (Inputs +1y, Capital Goods +3y,
#   moulds/dies/jigs/fixtures/tools EXEMPT); alarm at +10 months (decision D7).

import re

import frappe
from frappe import _
from frappe.utils import add_days, add_months, add_years, cint, flt, getdate

# Shared with ts_rgp.record_rgp_return (M-1/R-3): DENY-list — File.set_file_name
# strips only "/" (frappe file.py:414), so quotes/angle brackets can survive into
# a genuine file_url and must be blocked. Everything else a real upload can carry
# is allowed: commas, "&", "+", spaces, parens, non-ASCII (Devanagari) filenames.
# Was an allow-list [A-Za-z0-9._ ()-] until 3 Sep 2026 — it rejected the perfectly
# valid "/files/71ZQjdvguTL._AC_UF1000,1000_QL80_.jpg" purely for the comma, which
# BRICKED the whole pass: _validate_returns() runs from validate() AND
# before_update_after_submit, so one bad row blocked every later save, not just
# that return. A narrower allow-list is not "safer" here — it is an outage.
# N-3: "/" stays EXCLUDED — Frappe uploads write flat into files/ (File.folder is
# a doc attribute, not a URL segment), so a path-bearing URL is never legitimate.
# Bidi overrides (U+202A-202E, U+2066-2069) are denied too: never legitimate in a
# filename, and they spoof the displayed extension.
# The substantive gate remains the File-row existence check at both call sites.
_RETURN_PHOTO_RE = re.compile(
	r"^/(private/)?files/[^/\\<>\"'`\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]+$")

from trustbit_ethanol.ts_gate_entry.ts_po_approval import _block_gate_field_tampering

# Control-plane fields writable ONLY via db_set from ts_rgp.py / ts_rgp_gate.py
# endpoints (Phase B adds the gate trios' writers; fields ship now so B is
# pure code). read_only:1 never blocks REST writes — this guard does.
RGP_GATE_FIELDS = (
	"status",
	"is_overdue",
	"issued_by", "issued_at",
	"g2_out_by", "g2_out_at", "g2_out_remark",
	"g1_out_by", "g1_out_at", "g1_out_remark",
	"g1_in_by", "g1_in_at", "g1_in_remark",
	"g2_in_by", "g2_in_at", "g2_in_remark",
	"verified_by", "verified_at",
	"gate_entry_in",
	"close_short_requested_by", "close_short_requested_at", "close_short_reason",
	"close_short_approved_by", "close_short_approved_at", "close_short_qty",
)

# Statuses in which material is physically outside the plant — cancellation
# and item edits are forbidden here.
OUTSIDE_STATUSES = ("Out of Plant", "At Vendor", "Partially Returned")
TERMINAL_STATUSES = ("Verified - Closed", "Closed Short", "Cancelled")
# Statuses that still count as "open" for the D3 Work-Order conversion gate.
OPEN_STATUSES = ("Draft", "Issued", "Out of Plant", "At Vendor", "Partially Returned", "Returned")

SEC143_EXEMPT_GOODS = "Moulds/Dies/Jigs/Fixtures/Tools"
EWAY_VALUE_THRESHOLD = 50000


class TSReturnableGatePass(frappe.model.document.Document):
	def before_insert(self):
		if not self.challan_date:
			self.challan_date = frappe.utils.today()
		if not self.expected_return_date:
			self.expected_return_date = add_days(self.challan_date, 7)

	def validate(self):
		self._validate_source_mr()
		self._validate_dates()
		self._validate_items()
		# N-2: also gate DRAFT-stage returns rows — without this, rows
		# hand-appended before submit would be credited by _compute unchecked
		self._validate_returns()
		self._set_address_display()
		self._compute()

	def _set_address_display(self):
		"""Live-data DEFECT-1 (28 Aug 2026): nothing populated
		supplier_address_display, so the Rule 55 challan printed a blank
		consignee address even when the store picked a real Address. Server-side
		so API-created passes get it too; auto-picks the vendor's default
		address when none is chosen. Fail-soft — address rendering must never
		block the pass."""
		try:
			from frappe.contacts.doctype.address.address import (
				get_address_display,
				get_default_address,
			)
			if self.supplier and not self.supplier_address:
				self.supplier_address = get_default_address("Supplier", self.supplier)
			if self.supplier_address:
				self.supplier_address_display = get_address_display(self.supplier_address)
			else:
				self.supplier_address_display = ""
		except Exception:
			frappe.log_error(title="RGP address display failed",
				message=frappe.get_traceback())
			frappe.clear_messages()

	def before_save(self):
		_block_gate_field_tampering(self, RGP_GATE_FIELDS)

	def before_update_after_submit(self):
		"""H-1/H-2 (security scan 28 Aug 2026): Frappe runs validate()/before_save
		ONLY when _action == "save" — a SUBMITTED doc's save takes
		"update_after_submit", which skips both. Without this hook the L162
		tamper guard is inert for the pass's whole submitted life (any writer
		could forge status/verified_by via frappe.client.set_value and release
		the D3 Work-Order lock), and record_rgp_return's save would persist
		return rows WITHOUT recomputing balances (verify unreachable,
		over-returns possible). This is the submitted-life twin of
		validate() + before_save()."""
		_block_gate_field_tampering(self, RGP_GATE_FIELDS)
		self._validate_returns()
		self._compute()

	def _validate_returns(self):
		"""DEFECT-5 (code-tester): the returns table is allow_on_submit, so a
		direct doc.save() could append rows without the endpoint's D6 checks —
		and once balances recompute (H-2 fix), such rows would CREDIT the pass.
		The document layer is therefore the authoritative gate: every return
		row needs a real uploaded photo, a condition, and a matching serial,
		regardless of which path wrote it."""
		items_by_name = {row.name: row for row in (self.items or [])}
		for ret in (self.returns or []):
			if flt(ret.qty) <= 0:
				frappe.throw(_("Return row {0}: quantity must be greater than "
					"zero.").format(ret.idx))
			if not (ret.condition_in or "").strip():
				frappe.throw(_("Return row {0}: Condition In is mandatory "
					"(D6).").format(ret.idx))
			photo = (ret.return_photo or "").strip()
			if not _RETURN_PHOTO_RE.match(photo) or \
					not frappe.db.exists("File", {"file_url": photo}):
				frappe.throw(_("Return row {0}: a real uploaded photo is "
					"mandatory (D6).").format(ret.idx))
			src = items_by_name.get(ret.rgp_item_row)
			if src and cint(src.is_serialized):
				if (ret.serial_no_in or "").strip() != (src.serial_no_out or "").strip():
					frappe.throw(_("Return row {0}: serial mismatch — the same "
						"unit must return (D6).").format(ret.idx))

	def before_submit(self):
		# Submit freezes the pass content; the Issue action (ts_rgp.issue_rgp)
		# then opens it for the gate. Re-validate the essentials at the freeze.
		# DEFECT-1 (code-tester): supplier + condition_out are deliberately NOT
		# reqd in the schema — Create RGP inserts a stub the store completes —
		# so Submit is where they become mandatory.
		if not self.supplier:
			frappe.throw(_("Vendor is required before the pass can be submitted."))
		self._validate_items()
		if not (self.items or []):
			frappe.throw(_("At least one item line is required."))
		for row in (self.items or []):
			if not (row.condition_out or "").strip():
				frappe.throw(_("Row {0}: Condition Out is required before "
					"submitting.").format(row.idx))

	def on_submit(self):
		_rgp_log(self, "Submitted", "Draft", "Draft",
			comment=_("Pass content frozen; awaiting Issue by Stores."))

	def before_cancel(self):
		if (self.status or "Draft") in OUTSIDE_STATUSES:
			frappe.throw(
				_("Cannot cancel {0}: material is outside the plant (status {1}). "
				  "Record the return or use Close Short.").format(self.name, self.status),
				title=_("Material Outside"),
			)
		if (self.status or "") in ("Verified - Closed", "Closed Short"):
			frappe.throw(_("A closed pass cannot be cancelled."))

	def on_cancel(self):
		from_state = self.status or "Draft"
		self.db_set("status", "Cancelled", update_modified=True)
		_rgp_log(self, "Cancelled", from_state, "Cancelled")

	# ── validation helpers ─────────────────────────────────────────────

	def _validate_source_mr(self):
		if not self.material_request:
			return
		mr = frappe.db.get_value(
			"Material Request", self.material_request,
			["material_request_type", "docstatus", "ts_mr_status"], as_dict=True)
		if not mr:
			frappe.throw(_("Material Request {0} not found.").format(self.material_request))
		if mr.material_request_type != "Service Request":
			frappe.throw(
				_("{0} is a {1} indent — an RGP can only be issued against a "
				  "Service Request.").format(self.material_request, mr.material_request_type))
		if cint(mr.docstatus) != 1 or (mr.ts_mr_status or "") != "Approved":
			frappe.throw(
				_("{0} is not an approved indent (status: {1}). The indent must "
				  "complete its approval chain before an RGP is issued.")
				.format(self.material_request, mr.ts_mr_status or "Draft"))

	def _validate_dates(self):
		if self.challan_date and self.expected_return_date:
			if getdate(self.expected_return_date) < getdate(self.challan_date):
				frappe.throw(_("Expected Return Date cannot be before the Challan Date."))

	def _validate_items(self):
		for row in (self.items or []):
			if flt(row.qty_out) <= 0:
				frappe.throw(_("Row {0}: Qty Out must be greater than zero.").format(row.idx))
			if cint(row.is_serialized) and not (row.serial_no_out or "").strip():
				frappe.throw(
					_("Row {0}: {1} is serialized — Serial No (Out) is required (D6).")
					.format(row.idx, row.item_code))

	# ── computation ────────────────────────────────────────────────────

	def _compute(self):
		returned_by_row = {}
		returned_total = 0.0
		for ret in (self.returns or []):
			key = ret.rgp_item_row or ret.item_code
			returned_by_row[key] = returned_by_row.get(key, 0.0) + flt(ret.qty)
			returned_total += flt(ret.qty)

		total_out = 0.0
		total_value = 0.0
		for row in (self.items or []):
			# precision-rounded (security R-1): the raw float product differs
			# from the stored decimal(21,9) on re-read, which would trip the
			# update-after-submit changed-field check on every return save
			row.amount = flt(flt(row.qty_out) * flt(row.rate), row.precision("amount"))
			row.qty_returned = flt(
				returned_by_row.get(row.name) or returned_by_row.get(row.item_code) or 0.0)
			if flt(row.qty_returned) > flt(row.qty_out):
				frappe.throw(
					_("Row {0}: returned qty {1} exceeds qty out {2}.")
					.format(row.idx, row.qty_returned, row.qty_out))
			row.balance_qty = flt(row.qty_out) - flt(row.qty_returned)
			total_out += flt(row.qty_out)
			total_value += flt(row.amount)

		self.total_qty_out = total_out
		self.total_taxable_value = total_value
		self.total_qty_returned = returned_total
		self.total_balance = total_out - returned_total

		self._compute_statutory()

	def _compute_statutory(self):
		# Sec 143 clocks — from the CHALLAN date, never the expected date.
		if self.challan_date and self.goods_type != SEC143_EXEMPT_GOODS:
			years = 3 if self.goods_type == "Capital Goods" else 1
			self.sec143_due_date = add_years(self.challan_date, years)
			self.sec143_alarm_date = add_months(self.challan_date, 10)
		else:
			self.sec143_due_date = None
			self.sec143_alarm_date = None

		# Inter-state: first two GSTIN digits are the state code.
		company_gstin = ""
		try:
			company_gstin = frappe.get_cached_value("Company", self.company, "gstin") or ""
		except Exception:
			company_gstin = ""
		vendor_gstin = (self.supplier_gstin or "").strip()
		self.is_interstate = 1 if (
			company_gstin and vendor_gstin and company_gstin[:2] != vendor_gstin[:2]
		) else 0

		self.eway_bill_required = 1 if (
			flt(self.total_taxable_value) > EWAY_VALUE_THRESHOLD or cint(self.is_interstate)
		) else 0


def _rgp_log(doc, action, from_state, to_state, comment=""):
	"""Append an immutable trail row (TS Approval Log reused as rgp_log).

	Direct insert with parenttype/parentfield — same shape as the PO/MR
	engine's _log_approval_action; works on submitted parents. Fail-soft:
	a trail failure must never abort the business action (L238).

	⚠ CONSUMER CONTRACT (predictor, 28 Aug 2026): every existing reader of
	tabTS Approval Log filters by parenttype/parentfield (usage report,
	exec API, PO/MR grids) — that scoping is the ONLY thing keeping RGP
	rows (parenttype 'TS Returnable Gate Pass', incl. reused verbs like
	'Rejected'/'Submitted') out of PO/MR decision counts. Any future query
	on this table MUST carry a parenttype filter."""
	try:
		role = ""
		for r in frappe.get_roles(frappe.session.user):
			if r in ("Stores Manager", "Stores User", "CEO", "IT Head", "System Manager",
					"G1 Security", "G2 Gate Operator"):
				role = r
				break
		frappe.get_doc({
			"doctype": "TS Approval Log",
			"parent": doc.name,
			"parenttype": "TS Returnable Gate Pass",
			"parentfield": "rgp_log",
			"action": action,
			"from_state": from_state,
			"to_state": to_state,
			"action_by": frappe.session.user,
			"action_by_name": frappe.utils.get_fullname(frappe.session.user),
			"action_by_role": role,
			"action_date": frappe.utils.now_datetime(),
			"comment": (comment or "")[:500],
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="RGP trail write failed",
			message=frappe.get_traceback())
		frappe.clear_messages()
