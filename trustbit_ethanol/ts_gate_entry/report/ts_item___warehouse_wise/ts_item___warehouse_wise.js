// TS Item - Warehouse Wise — filter definitions.
// Dual-layout report: pivot (≤ ts_pivot_warehouse_threshold) OR tree (>).
// Server (.py) picks the layout. No cost-centre filter: stock has no cost centre.

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
	],
};
