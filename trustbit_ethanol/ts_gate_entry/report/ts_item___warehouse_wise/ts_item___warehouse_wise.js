// TS Item - Warehouse Wise — filter definitions + cell formatters.
// Dual-layout report: pivot (≤ ts_pivot_warehouse_threshold) OR tree (>).
// Server (.py) picks the layout; this JS only renders + formats.

frappe.query_reports["TS Item - Warehouse Wise"] = {
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
			fieldname: "as_on_date",
			label: __("As-on Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "MultiSelectList",
			options: "Warehouse",
			get_data: function (txt) {
				return frappe.db.get_link_options("Warehouse", txt);
			},
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "MultiSelectList",
			options: "Cost Center",
			get_data: function (txt) {
				return frappe.db.get_link_options("Cost Center", txt);
			},
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "MultiSelectList",
			options: "Item Group",
			get_data: function (txt) {
				return frappe.db.get_link_options("Item Group", txt);
			},
		},
		{
			fieldname: "item",
			label: __("Item"),
			fieldtype: "MultiSelectList",
			options: "Item",
			get_data: function (txt) {
				return frappe.db.get_link_options("Item", txt);
			},
		},
		{
			fieldname: "force_layout",
			label: __("Force Layout"),
			fieldtype: "Select",
			options: "Auto\nForce Pivot\nForce Tree",
			default: "Auto",
		},
		{
			fieldname: "show_zero",
			label: __("Show Zero-Stock Items"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "show_offsetting",
			label: __("Show Offsetting CC Movements"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		// Highlight Unallocated CC + group rows. Indent + is_group already
		// drive Frappe's native tree chevrons + bold.
		if (column.fieldname === "name" && data && data._is_unalloc) {
			return `<span style="color:#92400e;font-weight:600;">${formatted}</span>`;
		}
		return formatted;
	},
};
