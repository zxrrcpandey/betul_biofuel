# Copyright (c) 2026, Trustbit Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TSProductionMaterial(Document):
	"""Child row: one raw material consumed in a TS Production Entry.
	Holds the BOM-standard qty vs the operator-entered actual qty + variance %.
	All derived values are computed by the parent controller."""

	pass
