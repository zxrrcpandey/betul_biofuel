// TS Material Issue Ledger — v2.10.1.0
// Combined view of Material Issue requests (MR type=Material Issue) and their
// actual issues (SE purpose=Material Issue) with Requested vs Issued vs Pending
// per row. Status filter post-computed. Standalone SE issues (no MR origin)
// included via UNION pass.

frappe.query_reports["TS Material Issue Ledger"] = {
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
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "source_warehouse",
			label: __("Source Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "use_location",
			label: __("Use Location (contains)"),
			fieldtype: "Data",
			description: __("Substring match against the request's Use Location"),
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["All", "Pending", "Partial", "Fulfilled", "Standalone"].join("\n"),
			default: "All",
		},
		{
			fieldname: "include_cancelled",
			label: __("Include Cancelled"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (!data) return formatted;
		// Status indicator pill
		if (column.fieldname === "status" && value) {
			const map = {
				"Pending": "red",
				"Partial": "orange",
				"Fulfilled": "green",
				"Standalone": "blue",
			};
			const color = map[value] || "gray";
			return `<span class="indicator-pill ${color}">${frappe.utils.escape_html(value)}</span>`;
		}
		// Pending Qty colored cell
		if (column.fieldname === "pending_qty") {
			const pending = parseFloat(value);
			if (pending > 0) {
				return `<span style="color:#cc0000;font-weight:600;">${formatted}</span>`;
			}
			if (pending === 0 && data.status === "Fulfilled") {
				return `<span style="color:#16a34a;">${formatted}</span>`;
			}
		}
		// Issued Qty subtle green when matches requested
		if (column.fieldname === "issued_qty") {
			const iss = parseFloat(value);
			const req = parseFloat(data.requested_qty);
			if (iss > 0 && iss >= req && req > 0) {
				return `<span style="color:#16a34a;">${formatted}</span>`;
			}
		}
		return formatted;
	},
};
