"""RST Number helper APIs — uniqueness check for WB Op live feedback.

v2.8.2: introduced alongside RST Number hardening on TS Weighbridge Log.
Used exclusively by the WB Op form to warn on duplicate entry BEFORE save,
so the operator can verify the paper slip instead of hitting a save-time
IntegrityError or friendly throw from the validate() path.

Not sensitive — returns only conflicting log/token names (no weights, no
supplier data). Still mutation-guarded via methods=["POST"] per Lesson 175
to keep the endpoint off the GET-scraping surface.
"""

import frappe
from frappe import _


@frappe.whitelist(methods=["POST"])
def check_rst_unique(rst_number, current_log=None):
	"""Return {'unique': bool, 'conflict_log': str|None, 'conflict_token': str|None}.
	Used by WB Op UI for debounced live check before save."""
	if not rst_number:
		return {"unique": True, "conflict_log": None, "conflict_token": None}
	filters = {"rst_number": rst_number}
	if current_log:
		filters["name"] = ["!=", current_log]
	existing = frappe.db.get_value(
		"TS Weighbridge Log",
		filters,
		["name", "token_number"],
		as_dict=True,
	)
	if existing:
		return {
			"unique": False,
			"conflict_log": existing.name,
			"conflict_token": existing.token_number,
		}
	return {"unique": True, "conflict_log": None, "conflict_token": None}
