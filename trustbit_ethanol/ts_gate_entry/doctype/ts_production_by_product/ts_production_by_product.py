# Copyright (c) 2026, Trustbit Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TSProductionByProduct(Document):
	"""Child row: one by-product (e.g. DDGS / DWGS / LCO2) produced in a TS
	Production Entry. By-products are data-driven from the BOM Scrap Items;
	this row holds the BOM-standard qty vs the actual qty + variance %.
	All derived values are computed by the parent controller."""

	pass
