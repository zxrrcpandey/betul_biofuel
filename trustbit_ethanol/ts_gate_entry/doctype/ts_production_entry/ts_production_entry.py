# Copyright (c) 2026, Trustbit Technologies and contributors
# For license information, please see license.txt
#
# TS Production Entry — captures Standard (from native BOM) vs Actual material
# consumed + actual produced quantity, computes variance %, and (auto when within
# tolerance, or after CEO approval when a breach) creates+submits a native
# Manufacture Stock Entry (finished good + DDGS/DWGS/LCO2 auto scrap rows).
#
# State machine lives in ts_variance_status (NOT docstatus): is_submittable=0.
#   Draft -> Pending CEO -> Posted | Rejected ;  Posted -> Cancelled (SE cancel).
#
# Heavy logic (variance, valuation hard-block, SE builder, approval endpoints,
# notifications, Stock Entry on_cancel reverse-sync) lives in ts_production_api.py
# so the controller stays thin and the whitelisted endpoints are co-located.

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
