import frappe
from frappe.model.document import Document


class TSOMCDepotPlan(Document):
	"""Plain child table. The `quarter` field is derived + stamped by the parent
	TS OMC Supply Target.validate() from `instructed_on` vs the ESY quarter
	windows (read-only here). No standalone controller logic."""

	pass
