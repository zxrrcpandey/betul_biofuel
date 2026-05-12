frappe.query_reports["TS Stock Ledger FIFO"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse" },
		{ fieldname: "cost_center", label: __("Cost Center"), fieldtype: "Link", options: "Cost Center" },
		{ fieldname: "include_cancelled", label: __("Include Cancelled"), fieldtype: "Check", default: 0 },
	],

	onload(report) {
		report.page.add_inner_button(__("Export Branded PDF"), () => {
			const filters = report.get_values();
			if (!filters.company || !filters.from_date || !filters.to_date) {
				frappe.msgprint(__("Please set Company, From Date and To Date before exporting."));
				return;
			}
			frappe.confirm(
				__("Export current report rows to a branded PDF?"),
				() => _trigger_pdf_export(filters)
			);
		});
	},

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "valuation_method_display" && value) {
			const color = value === "FIFO" ? "green" : value === "LIFO" ? "orange" : "blue";
			return `<span class="indicator-pill ${color}">${frappe.utils.escape_html(value)}</span>`;
		}
		return formatted;
	},
};

function _trigger_pdf_export(filters) {
	frappe.dom.freeze(__("Generating PDF..."));
	fetch("/api/method/trustbit_ethanol.ts_gate_entry.api.ts_pdf_export.export_stock_ledger_pdf", {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			"X-Frappe-CSRF-Token": frappe.csrf_token,
		},
		body: JSON.stringify({ filters }),
	})
		.then((res) => {
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			return res.blob();
		})
		.then((blob) => {
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			const fname = `TS_Stock_Ledger_FIFO_${filters.from_date}_to_${filters.to_date}.pdf`;
			a.download = fname;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
		})
		.catch((err) => {
			frappe.msgprint({
				title: __("Export Failed"),
				message: __("Could not generate PDF: {0}", [err.message]),
				indicator: "red",
			});
		})
		.finally(() => frappe.dom.unfreeze());
}
