# Copyright (c) 2026, Trustbit Software
# TS BOM Connector — links the main (stock-impacting) RS BOM to the supporting
# department BOMs (reporting-only). Deliberately NOT ERPNext multi-level BOM:
# department BOMs must never roll cost/stock into the main product (the engine
# sets use_multi_level_bom=0 everywhere). See project_production_multi_flow.md.

import frappe
from frappe import _
from frappe.model.document import Document


class TSBOMConnector(Document):
	def validate(self):
		self._validate_main_bom()
		self._validate_department_lines()

	def _validate_main_bom(self):
		bom = frappe.db.get_value(
			"BOM", self.main_bom, ["is_active", "docstatus", "item"], as_dict=True)
		if not bom:
			frappe.throw(_("Main BOM {0} not found.").format(self.main_bom))
		if bom.docstatus != 1 or not bom.is_active:
			frappe.throw(_("Main BOM {0} must be an ACTIVE, SUBMITTED BOM.").format(self.main_bom))
		if self.main_category:
			if not frappe.db.get_value(
					"TS Production BOM Category", self.main_category, "is_production"):
				frappe.throw(_(
					"Main Category {0} must have 'Is Production' checked — the main BOM "
					"is the stock-impacting one."
				).format(self.main_category))

	def _validate_department_lines(self):
		seen = set()
		for row in (self.department_boms or []):
			if row.bom == self.main_bom:
				frappe.throw(_(
					"Row #{0}: {1} is the MAIN BOM — it cannot also be a department line."
				).format(row.idx, row.bom))
			if row.bom in seen:
				frappe.throw(_("Row #{0}: duplicate BOM {1}.").format(row.idx, row.bom))
			seen.add(row.bom)
			cat = frappe.db.get_value(
				"TS Production BOM Category", row.category,
				["is_production", "active"], as_dict=True)
			if not cat:
				frappe.throw(_("Row #{0}: category {1} not found.").format(row.idx, row.category))
			if cat.is_production:
				frappe.throw(_(
					"Row #{0}: category {1} is a PRODUCTION category — department lines "
					"must use reporting-only categories (Is Production = 0)."
				).format(row.idx, row.category))
			# reporting_only is read-only + default 1; re-assert server-side so a
			# forged payload can never flip a department line to stock-impacting.
			row.reporting_only = 1
