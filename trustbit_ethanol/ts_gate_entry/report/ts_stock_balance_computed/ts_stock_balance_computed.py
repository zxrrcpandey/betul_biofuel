import frappe
from frappe import _
from frappe.utils import flt

from erpnext.stock.report.stock_balance.stock_balance import execute as native_execute

DRIFT_HIGHLIGHT_THRESHOLD_PCT = 1.0


def execute(filters=None):
	if not frappe.has_permission("Stock Ledger Entry", "read"):
		frappe.throw(_("Not permitted to read Stock Ledger"), frappe.PermissionError)

	filters = filters or {}
	if not isinstance(filters, frappe._dict):
		filters = frappe._dict(filters)
	result = native_execute(filters)
	columns, data, message, chart, report_summary = _unpack(result)

	val_rate_idx = _find_column_index(columns, "val_rate")
	if val_rate_idx is None:
		return columns, data, message, chart, report_summary

	_relabel_val_rate(columns[val_rate_idx])

	show_drift = bool(filters.get("show_drift_columns", 1))
	if show_drift:
		columns = _inject_drift_columns(columns, val_rate_idx)

	for row in data:
		_compute_row(row, show_drift=show_drift)

	report_summary = _augment_summary(report_summary, data, show_drift)
	return columns, data, message, chart, report_summary


def _unpack(result):
	if isinstance(result, tuple):
		columns = result[0] if len(result) > 0 else []
		data = result[1] if len(result) > 1 else []
		message = result[2] if len(result) > 2 else None
		chart = result[3] if len(result) > 3 else None
		summary = result[4] if len(result) > 4 else None
		return columns, data, message, chart, summary
	return [], [], None, None, None


def _find_column_index(columns, fieldname):
	for i, col in enumerate(columns):
		if isinstance(col, dict) and col.get("fieldname") == fieldname:
			return i
	return None


def _relabel_val_rate(col):
	col["label"] = _("Valuation Rate (Computed)")
	col["disable_total"] = 1  # summing a rate is meaningless


def _inject_drift_columns(columns, val_rate_idx):
	stored_col = {
		"label": _("Valuation Rate (Stored)"),
		"fieldname": "stored_val_rate",
		"fieldtype": "Currency",
		"options": "Company:company:default_currency",
		"width": 140,
		"disable_total": 1,
	}
	drift_col = {
		"label": _("Drift %"),
		"fieldname": "drift_pct",
		"fieldtype": "Percent",
		"width": 100,
		"disable_total": 1,
	}
	return columns[: val_rate_idx + 1] + [stored_col, drift_col] + columns[val_rate_idx + 1 :]


def _compute_row(row, show_drift):
	if not isinstance(row, dict):
		return
	bal_qty = flt(row.get("bal_qty"))
	bal_val = flt(row.get("bal_val"))
	stored_rate = flt(row.get("val_rate"))

	if show_drift:
		row["stored_val_rate"] = stored_rate

	if bal_qty:
		computed = bal_val / bal_qty
	else:
		computed = 0.0
	row["val_rate"] = computed

	if show_drift:
		if stored_rate:
			row["drift_pct"] = ((computed - stored_rate) / stored_rate) * 100.0
		elif computed:
			row["drift_pct"] = 100.0
		else:
			row["drift_pct"] = 0.0


def _augment_summary(report_summary, data, show_drift):
	summary = list(report_summary or [])
	rows_with_drift = 0
	if show_drift:
		rows_with_drift = sum(
			1 for r in data if isinstance(r, dict) and abs(flt(r.get("drift_pct"))) > DRIFT_HIGHLIGHT_THRESHOLD_PCT
		)
	summary.append({"label": _("Rows"), "value": len(data), "datatype": "Int"})
	if show_drift:
		summary.append({
			"label": _("Rows with Drift > {0}%").format(DRIFT_HIGHLIGHT_THRESHOLD_PCT),
			"value": rows_with_drift,
			"datatype": "Int",
			"indicator": "Red" if rows_with_drift else "Green",
		})
	return summary
