"""v2.9.14.4 — Purchase Invoice class override.

Overrides ERPNext's `validate_purchase_receipt_if_update_stock` to allow
update_stock=1 on Purchase Returns even when items have purchase_receipt set.

ERPNext's same file (purchase_invoice.py) at lines 715-723 has a status updater
that EXPLICITLY supports update_stock=1 + is_return=1 — the validator at
line 727 is an oversight that didn't get the same exception. This override
restores consistency: forward PI from PR is still blocked (prevents double
stock IN); Return PI from PR is now allowed (legitimate return stock OUT).

v2.9.16.4 — also snapshots Token / RST / QI / DS / PR context fields from
the first linked PR (via items[].purchase_receipt). Header-level display
summary; ERPNext's stock/billing logic continues to read per-item
purchase_receipt as before.
"""
import frappe
from frappe import _
from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice


class TSPurchaseInvoice(PurchaseInvoice):
	def validate(self):
		self._snapshot_receipt_context_from_pr()
		super().validate()

	def validate_purchase_receipt_if_update_stock(self):
		# Forward PI from PR + update_stock=1 → block (preserves native ERPNext
		# behavior, avoids double stock IN).
		# Return PI from PR + update_stock=1 → allow (return stock OUT is the
		# user's intended movement; status_updater handles per-item ledgers).
		if self.update_stock and not self.is_return:
			for item in self.get("items"):
				if item.purchase_receipt:
					frappe.throw(
						_("Stock cannot be updated against Purchase Receipt {0}").format(item.purchase_receipt)
					)

	def _snapshot_receipt_context_from_pr(self):
		"""Populate ts_token / ts_rst_number / ts_quality_inspection /
		ts_deduction_sheet / ts_purchase_receipt from the FIRST PR linked via
		items[].purchase_receipt. Only fires when ts_token is empty (one-shot
		snapshot at insert; preserves user overrides on edit)."""
		if getattr(self, "ts_token", None):
			return
		first_pr = None
		for item in (self.get("items") or []):
			if item.purchase_receipt:
				first_pr = item.purchase_receipt
				break
		if not first_pr:
			return
		pr = frappe.db.get_value(
			"Purchase Receipt", first_pr,
			["ts_token", "custom_rst_number"],
			as_dict=True,
		)
		if not pr:
			return
		self.ts_purchase_receipt = first_pr
		self.ts_token = pr.ts_token or None
		self.ts_rst_number = pr.custom_rst_number or None
		# DS link chain: PR.ts_token -> QI(token_number=...) -> DS(quality_inspection=...)
		qi = ds = None
		if pr.ts_token:
			qi = frappe.db.get_value(
				"TS Quality Inspection",
				{"token_number": pr.ts_token, "docstatus": ["!=", 2]},
				"name",
			)
			if qi:
				ds = frappe.db.get_value(
					"TS Deduction Sheet",
					{"quality_inspection": qi, "docstatus": ["!=", 2]},
					"name",
				)
		self.ts_quality_inspection = qi or None
		self.ts_deduction_sheet = ds or None

		# v2.11.x — DS→PI timing-bug fix. The DS's own `_propagate_to_pi` runs in
		# its on_submit; if the DS is submitted BEFORE this PI is created, that
		# call finds no PI and the actual_deduction_* values never reach the PI
		# (saw PINV-26-00230 — DS submitted at 12:06:11, PI created at 12:06:43,
		# fields stayed empty). Pull them now from the PI side as a one-shot at
		# insert (the early-return on ts_token above gates this to insert only),
		# mirroring the DS's _propagate_to_pi logic. Best-effort: never break
		# PI validation on a DS-fetch failure.
		if ds:
			try:
				ds_vals = frappe.db.get_value(
					"TS Deduction Sheet", ds,
					["docstatus", "actual_deduction_pct",
					 "actual_deduction_kg", "actual_deduction_reason"],
					as_dict=True,
				)
				if ds_vals and ds_vals.docstatus == 1:
					self.ts_ds_reference = ds
					self.ts_ds_actual_deduction_pct = ds_vals.actual_deduction_pct
					self.ts_ds_actual_deduction_kg = ds_vals.actual_deduction_kg
					self.ts_ds_actual_deduction_reason = ds_vals.actual_deduction_reason
			except Exception as e:
				frappe.log_error(
					title=f"PI {self.name or '<new>'} DS fetch failed",
					message=f"{type(e).__name__}: {e}",
				)
