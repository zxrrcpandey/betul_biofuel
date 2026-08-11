// TS Monthly Service Summary — BBPL Report Updated 11 Aug 2026.xlsx (#14).
// One row per Service Request MR item x linked Work Order (PO); sparse data is
// the normal state — blanks mean "no WO yet", not an error.
frappe.query_reports["TS Monthly Service Summary"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("MR Date From"),
			fieldtype: "Date",
			default: frappe.defaults.get_user_default("year_start_date") ||
				frappe.datetime.add_months(frappe.datetime.get_today(), -12)
		},
		{
			fieldname: "to_date",
			label: __("MR Date To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today()
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier"
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center"
		},
		{
			fieldname: "material_request",
			label: __("Service Request (MR)"),
			fieldtype: "Link",
			options: "Material Request"
		},
		{
			fieldname: "include_draft",
			label: __("Include Draft Service Requests"),
			fieldtype: "Check",
			default: 0
		}
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "balance_qty" && data &&
			data.balance_qty !== null && data.balance_qty !== undefined && data.balance_qty > 0) {
			value = `<span style="color:var(--red-500,#b94a48);font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "mr_status" && data && data.mr_status === "Draft") {
			const safe = frappe.utils.escape_html(data.mr_status);
			value = `<span style="color:var(--orange-500,#d97706);font-weight:600">${safe}</span>`;
		}
		return value;
	}
};
