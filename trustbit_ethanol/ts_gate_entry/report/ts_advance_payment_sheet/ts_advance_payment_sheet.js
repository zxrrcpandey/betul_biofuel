// TS Advance Payment Sheet — client sheet "V1.8 - 2" (2 Oct 2026).
// One row per submitted PO with an advance payment term (label contains
// 'advance') or a term-less paid "General Advance"; grouped by cost centre
// with injected subtotal rows.
frappe.query_reports["TS Advance Payment Sheet"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.defaults.get_user_default("year_start_date") ||
				frappe.datetime.add_months(frappe.datetime.get_today(), -12)
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today()
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center"
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier"
		},
		{
			fieldname: "advance_type",
			label: __("Advance Type"),
			fieldtype: "Select",
			options: ["All", "Term Advance", "General Advance"].join("\n"),
			default: "All"
		},
		{
			fieldname: "show_settled",
			label: __("Include Settled Advances"),
			fieldtype: "Check",
			default: 0
		}
	],
	formatter(value, row, column, data, default_formatter) {
		// Stored-XSS guard: the grid injects formatter output as raw HTML and
		// Data cells carry free text (item names, locations, supplier names).
		// Server-side strip_html misses UNCLOSED tags, so every Data value is
		// escaped here before rendering. Exports keep raw values.
		if (column.fieldtype === "Data" && value != null && value !== "") {
			value = frappe.utils.escape_html(String(value));
		}
		value = default_formatter(value, row, column, data);
		if (data && data.is_subtotal) {
			value = `<span style="font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "status" && data && data.is_general) {
			value = `<span style="color:var(--blue-500,#2563eb)">${value}</span>`;
		}
		if (column.fieldname === "balance" && data && !data.is_subtotal &&
			data.balance != null && flt(data.balance) > 0) {
			value = `<span style="color:var(--orange-500,#d97706);font-weight:600">${value}</span>`;
		}
		return value;
	}
};
