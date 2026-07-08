# Copyright (c) 2026, Trustbit Software
# TS Production Department Entry — a REPORTING-ONLY log of the raw material a
# supporting department (WTP, Electricity...) consumed for a production run.
#
# INVARIANT: this doctype NEVER creates a Stock Entry / SLE / GL row — there is
# structurally no stock-posting code path here or in ts_production_dept.py.
# Mutations flow through the whitelisted endpoints (recipient-gated); the
# control-plane fields are tamper-guarded (Lesson 162).

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

# Control-plane fields — writable only via the endpoints' db_set (Lesson 162).
CONTROL_FIELDS = ["status", "submitted_by", "logged_at", "bom_connector", "notified_at"]


class TSProductionDepartmentEntry(Document):
	def validate(self):
		self._validate_category()
		self._validate_bom_membership()
		self._validate_materials()
		# The run's company ALWAYS wins — Frappe auto-fills `company` from the
		# GLOBAL default before validate() (Lesson 150), which mis-attributes on a
		# multi-company install if we only fill-blank (code-tester finding, 2 Jul).
		self.company = frappe.db.get_value(
			"TS Production Entry", self.production_entry, "company") or self.company

	def before_save(self):
		from trustbit_ethanol.ts_gate_entry.ts_po_approval import _block_gate_field_tampering
		_block_gate_field_tampering(self, CONTROL_FIELDS)

	def _validate_category(self):
		cat = frappe.db.get_value(
			"TS Production BOM Category", self.category,
			["is_production", "active"], as_dict=True)
		if not cat:
			frappe.throw(_("Category {0} not found.").format(self.category))
		if cat.is_production:
			frappe.throw(_(
				"Category {0} is a PRODUCTION category — department entries use "
				"reporting-only categories (Is Production = 0)."
			).format(self.category))

	def _validate_bom_membership(self):
		"""When the run's BOM has an active connector, this entry's (bom, category)
		must be one of that connector's department lines."""
		main_bom = frappe.db.get_value(
			"TS Production Entry", self.production_entry, "bom")
		if not main_bom:
			return
		connectors = frappe.get_all(
			"TS BOM Connector", filters={"main_bom": main_bom, "active": 1},
			pluck="name", limit=0)
		if not connectors:
			return  # no connector configured for this run's BOM — standalone log allowed
		line = frappe.get_all(
			"TS BOM Connector Line",
			filters={"parent": ["in", connectors], "bom": self.bom, "category": self.category},
			limit=1)
		if not line:
			frappe.throw(_(
				"BOM {0} / category {1} is not a department line of the connector "
				"for this production run ({2})."
			).format(self.bom, self.category, ", ".join(connectors)))
		if not self.bom_connector:
			# informational back-link; control-plane so set via flags-free direct assign
			# during insert only (guard compares against DB, so first insert is free).
			self.bom_connector = connectors[0]

	def _validate_materials(self):
		# A system-created gate entry (Multiple-flow submit) starts EMPTY in
		# Draft/Pending — the department fills actuals when it logs. Materials
		# become mandatory the moment the entry is (being) Logged; the endpoint
		# additionally enforces qty > 0 per used row.
		if not (self.materials or []):
			if (self.status or "Draft") in ("Draft", "Pending"):
				return
			frappe.throw(_("Add at least one material row."))
		for row in self.materials:
			if flt(row.qty) < 0:
				frappe.throw(_("Row #{0}: quantity cannot be negative.").format(row.idx))
		if (self.status or "Draft") not in ("Draft", "Pending") and \
				not any(flt(r.qty) > 0 for r in self.materials):
			frappe.throw(_("At least one material row must have a quantity > 0."))
