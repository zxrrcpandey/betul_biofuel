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


def _parse_filters(filters):
	"""Shared filter parse/validate for the ledger export endpoints."""
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except json.JSONDecodeError:
			frappe.throw(_("Invalid filters payload"))
	if not isinstance(filters, dict):
		frappe.throw(_("Filters must be a dict"))
	if not filters.get("company") or not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("Company, From Date and To Date are required"))
	return filters


@frappe.whitelist(methods=["POST"])
def export_material_issue_ledger_pdf(filters=None):
	# Mirrors export_stock_ledger_pdf for the TS Material Issue Ledger report.
	# Gate on Stock Entry read — matches the report's own execute() permission.
	if not frappe.has_permission("Stock Entry", "read"):
		frappe.throw(_("Not permitted to read Stock Entry"), frappe.PermissionError)

	filters = _parse_filters(filters)

	# Alias the MI Ledger's own get_data / ROW_LIMIT so we never shadow the
	# module-level FIFO import that export_stock_ledger_pdf depends on.
	from trustbit_ethanol.ts_gate_entry.report.ts_material_issue_ledger.ts_material_issue_ledger import (
		get_data as get_mi_data,
		get_consolidated_data,
		_is_consolidated,
		ROW_LIMIT as MI_ROW_LIMIT,
	)

	consolidated = _is_consolidated(filters)
	try:
		if consolidated:
			rows, truncated = get_consolidated_data(filters)
		else:
			rows, truncated = get_mi_data(filters)
	except Exception as e:
		frappe.log_error(title="ts_pdf_export mi_get_data", message=str(e))
		raise

	if consolidated:
		# Grand totals over item rows only (subtotal rows already aggregate them).
		item_rows = [r for r in rows if not r.get("is_subtotal")]
		total_requested = total_pending = 0
		total_issued = sum(flt(r.get("total_issued")) for r in item_rows)
		total_value = sum(flt(r.get("total_value")) for r in item_rows)
	else:
		total_requested = sum(flt(r.get("requested_qty")) for r in rows)
		total_issued = sum(flt(r.get("issued_qty")) for r in rows)
		total_pending = sum(flt(r.get("pending_qty")) for r in rows)
		total_value = sum(flt(r.get("value")) for r in rows)

	template_path = os.path.join(os.path.dirname(__file__), "..", "report",
	                             "ts_material_issue_ledger", "pdf_template.html")
	template_path = os.path.normpath(template_path)
	with open(template_path, "r", encoding="utf-8") as f:
		template = f.read()

	context = {
		"report_title": "TS Material Issue Ledger",
		"report_subtitle": ("Consolidated Consumption by Cost Center"
		                    if consolidated else "Detailed Ledger — Audit Report"),
		"consolidated": consolidated,
		"company_name": filters.get("company"),
		"logo_url": LOGO_URL,
		"filters": filters,
		"rows": rows,
		"total_requested": total_requested,
		"total_issued": total_issued,
		"total_pending": total_pending,
		"total_value": total_value,
		"truncated": truncated,
		"row_limit": MI_ROW_LIMIT,
		"generated_at": now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
		"generated_by": frappe.session.user,
		"app_version": f"v{APP_VERSION}",
	}

	html = frappe.render_template(template, context)
	pdf_bytes = get_pdf(html, options={"orientation": "Landscape", "page-size": "A4"})

	tag = "Consolidated" if consolidated else "Ledger"
	abbr = (filters.get("company") or "BBPL").replace(" ", "_")[:12]
	fname = f"TS_Material_Issue_{tag}_{abbr}_{filters.get('from_date')}_to_{filters.get('to_date')}.pdf"
	frappe.local.response.filename = fname
	frappe.local.response.filecontent = pdf_bytes
	frappe.local.response.type = "download"
