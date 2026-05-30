import json

import frappe
from frappe import _
from frappe.utils import flt
from frappe.utils.xlsxutils import make_xlsx

# Fieldnames whose column totals are summed in the footer row.
_TOTAL_FIELDS = ("requested_qty", "issued_qty", "pending_qty", "value")
_NUMERIC_TYPES = ("Float", "Currency", "Int")


@frappe.whitelist(methods=["POST"])
def export_material_issue_ledger_excel(filters=None):
	"""Dedicated .xlsx export for the TS Material Issue Ledger report.

	Emits every column the on-screen report defines (in lockstep with
	get_columns()), one row per result row, plus a TOTALS footer. Reuses the
	report's own get_data() so the export can never drift from the grid.
	Frappe already ships a built-in Menu -> Export; this gives a one-click,
	all-columns, branded-filename button next to the PDF export.
	"""
	if not frappe.has_permission("Stock Entry", "read"):
		frappe.throw(_("Not permitted to read Stock Entry"), frappe.PermissionError)

	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except json.JSONDecodeError:
			frappe.throw(_("Invalid filters payload"))
	if not isinstance(filters, dict):
		frappe.throw(_("Filters must be a dict"))
	if not filters.get("company") or not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("Company, From Date and To Date are required"))

	from trustbit_ethanol.ts_gate_entry.report.ts_material_issue_ledger.ts_material_issue_ledger import (
		get_columns,
		get_data,
	)

	columns = get_columns()
	try:
		rows, _truncated = get_data(filters)
	except Exception as e:
		frappe.log_error(title="ts_excel_export mi_get_data", message=str(e))
		raise

	header = [c.get("label") for c in columns]
	# Excel column width ~= report px width / 7 (clamped to a sane range).
	widths = [max(8, min(60, int(flt(c.get("width", 100)) / 7))) for c in columns]

	xlsx_data = [header]
	for r in rows:
		out_row = []
		for c in columns:
			fn = c.get("fieldname")
			val = r.get(fn)
			if c.get("fieldtype") in _NUMERIC_TYPES:
				out_row.append(flt(val))
			else:
				out_row.append("" if val is None else val)
		xlsx_data.append(out_row)

	# TOTALS footer row aligned to column positions.
	totals_by_field = {f: sum(flt(r.get(f)) for r in rows) for f in _TOTAL_FIELDS}
	totals_row = []
	for idx, c in enumerate(columns):
		fn = c.get("fieldname")
		if idx == 0:
			totals_row.append(f"TOTALS ({len(rows)} rows)")
		elif fn in totals_by_field:
			totals_row.append(totals_by_field[fn])
		else:
			totals_row.append("")
	xlsx_data.append(totals_row)

	content = make_xlsx(xlsx_data, "Material Issue Ledger", column_widths=widths).getvalue()

	abbr = (filters.get("company") or "BBPL").replace(" ", "_")[:12]
	fname = f"TS_Material_Issue_Ledger_{abbr}_{filters.get('from_date')}_to_{filters.get('to_date')}.xlsx"
	frappe.local.response.filename = fname
	frappe.local.response.filecontent = content
	frappe.local.response.type = "download"
