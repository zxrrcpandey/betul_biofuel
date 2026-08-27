# TS Tally Export — .xlsx endpoint (v2.44)
# Builds ALL THREE template sheets (Pmt Receipt / JV / Sales) from the report's own
# row-builders and returns one workbook. Read-only: no DB write of any kind.
import json
import re

import frappe
import openpyxl
from frappe import _
from frappe.utils import getdate
from frappe.utils.xlsxutils import make_xlsx

from trustbit_ethanol.ts_gate_entry.report.ts_tally_export import ts_tally_export as rpt

EXPORT_ROLES = ("Accounts User", "Accounts Manager", "System Manager")
MAX_ROWS = 50000  # per sheet


def _inert(v):
	# openpyxl types any str starting with "=" as a formula that Excel evaluates on
	# open; "+", "-", "@" stay inline strings, so only "=" needs the apostrophe guard.
	return "'" + v if isinstance(v, str) and v.startswith("=") else v


def _cell(v):
	return "" if v is None else _inert(v)


@frappe.whitelist(methods=["POST"])
def export_tally_excel(filters=None):
	"""One-click Tally workbook: 3 sheets in template order, exact header labels,
	real date cells (System Settings date format -> dd-MM-yyyy), no formulas."""
	frappe.only_for(EXPORT_ROLES)
	# Builders use raw SQL, so every source doctype is gated explicitly (fail-closed:
	# the export builds all three sheets, so the user must be able to read all three).
	for dt in rpt.SHEET_DT.values():
		frappe.has_permission(dt, "read", throw=True)

	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except json.JSONDecodeError:
			frappe.throw(_("Invalid filters payload"))
	if not isinstance(filters, dict):
		frappe.throw(_("Filters must be a dict"))

	sheets = rpt.build_all(filters)  # validates from/to
	for name, (rows, _notes) in sheets.items():
		if len(rows) > MAX_ROWS:
			frappe.throw(_("Sheet {0} has {1} rows (limit {2}). Please narrow the date range.")
				.format(name, len(rows), MAX_ROWS))

	# make_xlsx PREPENDS each sheet (create_sheet(name, 0)) and saves the whole
	# workbook on every call, so iterate in reverse and keep the LAST buffer. A normal
	# Workbook must be passed in — the default write-only one cannot be reused.
	wb = openpyxl.Workbook()
	wb.remove(wb.active)
	xlsx = None
	for name in reversed(rpt.SHEET_ORDER):
		data = [rpt.headers(name)] + [[_cell(c) for c in r] for r in sheets[name][0]]
		xlsx = make_xlsx(data, name, wb=wb, column_widths=rpt.widths(name))

	company = filters.get("company")
	tag = (frappe.db.get_value("Company", company, "abbr") or company) if company else "ALL"
	tag = re.sub(r"[^A-Za-z0-9_-]+", "_", str(tag))[:20]
	frappe.local.response.filename = "TS_Tally_Export_{0}_{1}_to_{2}.xlsx".format(
		tag, getdate(filters["from_date"]), getdate(filters["to_date"]))
	frappe.local.response.filecontent = xlsx.getvalue()
	frappe.local.response.type = "download"
