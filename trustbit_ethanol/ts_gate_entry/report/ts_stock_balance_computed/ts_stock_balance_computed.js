frappe.query_reports["TS Stock Balance Computed"] = {
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
			default: frappe.datetime.month_start(),
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
		{ fieldname: "warehouse_type", label: __("Warehouse Type"), fieldtype: "Link", options: "Warehouse Type" },
		{ fieldname: "include_zero_stock_items", label: __("Include Zero Stock Items"), fieldtype: "Check", default: 0 },
		{ fieldname: "show_drift_columns", label: __("Show Drift Columns"), fieldtype: "Check", default: 1 },
		{ fieldname: "show_variant_attributes", label: __("Show Variant Attributes"), fieldtype: "Check", default: 0 },
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "drift_pct" && data) {
			const drift = parseFloat(data.drift_pct);
			if (!isNaN(drift) && Math.abs(drift) > 1.0) {
				return `<span style="color: ${drift > 0 ? '#c0392b' : '#2980b9'}; font-weight: 600;">${formatted}</span>`;
			}
		}
		if (column.fieldname === "val_rate" && data && data.stored_val_rate) {
			const computed = parseFloat(data.val_rate);
			const stored = parseFloat(data.stored_val_rate);
			if (!isNaN(computed) && !isNaN(stored) && stored !== 0) {
				const pct = ((computed - stored) / stored) * 100;
				if (Math.abs(pct) > 1.0) {
					return `<span style="font-weight: 600;">${formatted}</span>`;
				}
			}
		}
		return formatted;
	},
};
