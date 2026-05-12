import json
import os

import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe.utils.pdf import get_pdf

from trustbit_ethanol import __version__ as APP_VERSION
from trustbit_ethanol.ts_gate_entry.report.ts_stock_ledger_fifo.ts_stock_ledger_fifo import (
	get_data,
	ROW_LIMIT,
)

LOGO_URL = "/files/client_logo.png"


@frappe.whitelist(methods=["POST"])
def export_stock_ledger_pdf(filters=None):
	if not frappe.has_permission("Stock Ledger Entry", "read"):
		frappe.throw(_("Not permitted to read Stock Ledger"), frappe.PermissionError)

	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except json.JSONDecodeError:
			frappe.throw(_("Invalid filters payload"))
	if not isinstance(filters, dict):
		frappe.throw(_("Filters must be a dict"))

	if not filters.get("company") or not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("Company, From Date and To Date are required"))

	try:
		rows, truncated = get_data(filters)
	except Exception as e:
		frappe.log_error(title="ts_pdf_export get_data", message=str(e))
		raise

	total_amount = sum(flt(r.get("total_amount")) for r in rows)
	total_in = sum(flt(r.get("in_qty")) for r in rows)
	total_out = sum(flt(r.get("out_qty")) for r in rows)

	template_path = os.path.join(os.path.dirname(__file__), "..", "report",
	                             "ts_stock_ledger_fifo", "pdf_template.html")
	template_path = os.path.normpath(template_path)
	with open(template_path, "r", encoding="utf-8") as f:
		template = f.read()

	context = {
		"report_title": "TS Stock Ledger FIFO",
		"company_name": filters.get("company"),
		"logo_url": LOGO_URL,
		"filters": filters,
		"rows": rows,
		"total_amount": total_amount,
		"total_in": total_in,
		"total_out": total_out,
		"truncated": truncated,
		"row_limit": ROW_LIMIT,
		"generated_at": now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
		"generated_by": frappe.session.user,
		"app_version": f"v{APP_VERSION}",
	}

	html = frappe.render_template(template, context)
	pdf_bytes = get_pdf(html, options={"orientation": "Landscape", "page-size": "A4"})

	abbr = (filters.get("company") or "BBPL").replace(" ", "_")[:12]
	fname = f"TS_Stock_Ledger_FIFO_{abbr}_{filters.get('from_date')}_to_{filters.get('to_date')}.pdf"
	frappe.local.response.filename = fname
	frappe.local.response.filecontent = pdf_bytes
	frappe.local.response.type = "download"
