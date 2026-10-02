// TS Grain Analysis — client sheet "V1.8 - 3 / grain analysis report" (2 Oct 2026).
// One row per Deduction Sheet (Grain by default), legs LEFT-joined:
// Weighbridge Log (gross/net/tare/RST), Token (vehicle), PR, PO, PI (TDS).
frappe.query_reports["TS Grain Analysis"] = {
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
			fieldname: "item_category",
			label: __("Category"),
			fieldtype: "Select",
			options: ["Grain", "Coal", "Other", "All"].join("\n"),
			default: "Grain"
		},
		{
			fieldname: "purchase_order",
			label: __("Purchase Order"),
			fieldtype: "Link",
			options: "Purchase Order"
		},
		{
			fieldname: "token",
			label: __("Token"),
			fieldtype: "Link",
			options: "TS Token"
		},
		{
			fieldname: "submitted_only",
			label: __("Submitted Sheets Only"),
			fieldtype: "Check",
			default: 0
		}
	],
	formatter(value, row, column, data, default_formatter) {
		// Stored-XSS guard: the grid injects formatter output as raw HTML, and the
		// Data cells carry free text low-trust users can write (vehicle_number via
		// G1/G2/reception). Server-side strip_html misses UNCLOSED tags, so every
		// Data value is escaped here before rendering. Exports keep raw values.
		if (column.fieldtype === "Data" && value != null && value !== "") {
			value = frappe.utils.escape_html(String(value));
		}
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "ds_status" && data && data.ds_status === "Draft") {
			const safe = frappe.utils.escape_html(data.ds_status);
			value = `<span style="color:var(--orange-500,#d97706);font-weight:600">${safe}</span>`;
		}
		if (column.fieldname === "pr_no" && data && !data.pr_no) {
			value = `<span style="color:var(--orange-500,#d97706)">—</span>`;
		}
		return value;
	}
};
