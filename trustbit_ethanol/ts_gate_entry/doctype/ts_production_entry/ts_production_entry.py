# Copyright (c) 2026, Trustbit Technologies and contributors
# For license information, please see license.txt
#
# TS Production Entry — Production Logging (Dashboard #2).
#
# A Production Manager picks a BOM, enters produced qty + by-products, edits the
# raw-material RELEASE request, then submits for a single Store-Manager release
# gate. On release the system creates+submits a Work Order, transfers raw material
# Stores -> WIP (Material Transfer for Manufacture), auto-completes Job Cards for
# operations BOMs, submits a native Manufacture Stock Entry (consumes WIP per the
# BOM recipe scaled to produced qty), closes the Work Order, and auto-returns any
# over-released surplus WIP back to Stores. Variance is informational only — there
# is NO CEO approval gate in this flow.
#
# State machine lives in ts_variance_status (NOT docstatus): is_submittable=0.
#   Draft -> Pending Stores Release -> Released -> Completed
#   Pending Stores Release -> Rejected ;  Rejected -> Draft (revise)
#   Released -> Pending Stores Release (release SE cancelled, revert-on-cancel)
#   Completed -> Cancelled (Manufacture SE cancelled, reverse-sync)
# 'Released' is a recoverable mid-chain state: complete_released_production() can
# re-run steps 7-10 idempotently so a Work Order is never half-completed.
#
# Heavy logic lives across:
#   - ts_production_api.py     : variance engine, valuation hard-block, Manufacture
#                                SE builder, on_cancel reverse-sync, notifications
#   - ts_production_release.py : the Store-Manager release gate (whitelisted)
#   - ts_production_wo.py      : Work-Order + Job-Card automation engine
# so the controller stays thin.

from frappe.model.document import Document

from trustbit_ethanol.ts_gate_entry import ts_production_api as api


class TSProductionEntry(Document):
	def validate(self):
		# Pull Standard from the BOM (company / finished item / standard batch qty / uom)
		api.apply_bom_defaults(self)
		# Recompute std_qty (scaled to actual produced) + per-row + header variance %
		api.compute_and_set_variance(self)
		# Block negatives + sanity
		api.validate_entry(self)

	def before_save(self):
		# Tamper guard: block REST/form mutation of control-plane fields.
		# Workflow endpoints use db_set (skip save hooks) so this never fires on them;
		# it only fires when a user tries to flip status / linkage directly.
		api.guard_control_fields(self)
