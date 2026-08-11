// TS PR Wise Payment Approval — BBPL Report Updated 11 Aug 2026.xlsx (#10).
// One row per submitted PO item x linked submitted PI; advance payments
// grouped per Payment Entry with positionally-aligned IDs / dates / UTRs.
frappe.query_reports["TS PR Wise Payment Approval"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("PO Date From"),
			fieldtype: "Date",
			default: frappe.defaults.get_user_default("year_start_date") ||
				frappe.datetime.add_months(frappe.datetime.get_today(), -12)
		},
		{
			fieldname: "to_date",
			label: __("PO Date To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today()
		},
		{
			fieldname: "purchase_order",
			label: __("Purchase Order"),
			fieldtype: "Link",
			options: "Purchase Order"
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
			fieldname: "has_invoice",
			label: __("Has Invoice"),
			fieldtype: "Select",
			options: ["", "Yes", "No"].join("\n"),
			default: ""
		},
		{
			fieldname: "has_advance",
			label: __("Has Advance Payment"),
			fieldtype: "Select",
			options: ["", "Yes", "No"].join("\n"),
			default: ""
		}
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "pi_outstanding" && data &&
			data.pi_outstanding !== null && data.pi_outstanding !== undefined && data.pi_outstanding > 0) {
			value = `<span style="color:var(--red-500,#b94a48);font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "advance_paid" && data && data.advance_paid > 0) {
			value = `<span style="color:var(--blue-500,#2563eb);font-weight:600">${value}</span>`;
		}
		return value;
	}
};
