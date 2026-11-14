// Copyright (c) 2026, Trustbit Software and contributors
// TS Order Watch: one parent row per finding, its orders and facts as children.

frappe.query_reports["TS Order Watch"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -90),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			// An MD-only login cannot read Company: its Link search and check would refuse it, so it gets a
			// plain text box (the report checks the name).
			fieldname: "company",
			label: __("Company"),
			fieldtype: frappe.model.can_read("Company") ? "Link" : "Data",
			options: frappe.model.can_read("Company") ? "Company" : "",
		},
	],
	onload(report) {
		// Runs after a link's values are set and before the first run. A company name never holds < or >:
		// such a value (a crafted link) is dropped, as the print view writes filter values as they are.
		const company = report.get_filter("company");
		if (/[<>]/.test(company.get_value() || "")) {
			return company.set_value("");
		}
	},
	tree: true,
	initial_depth: 0,
	formatter(value, row, column, data, default_formatter) {
		// Stored-XSS guard (house rule): Data cells carry free text (supplier and
		// item names), and the grid injects formatter output as raw HTML.
		if (column.fieldtype === "Data" && value != null && value !== "") {
			value = frappe.utils.escape_html(String(value));
		}
		value = default_formatter(value, row, column, data);
		const colour = data && {
			"Serious": "var(--red-600, #dc2626)",
			"To check": "var(--orange-600, #ea580c)",
			"Note": "var(--gray-600, #6b7280)",
		}[data.level];
		if (column.fieldname === "level" && colour) {
			value = `<span style="color:${colour};font-weight:600">${value}</span>`;
		} else if (data && data.indent === 0 && value) {
			value = `<span style="font-weight:600">${value}</span>`;
		}
		return value;
	},
};
