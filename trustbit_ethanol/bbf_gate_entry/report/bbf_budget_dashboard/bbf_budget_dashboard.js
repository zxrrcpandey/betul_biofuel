frappe.query_reports["BBF Budget Dashboard"] = {
	filters: [
		{
			fieldname: "fiscal_year",
			label: __("Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			default: frappe.defaults.get_global_default("fiscal_year"),
			reqd: 1,
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_global_default("company"),
			reqd: 1,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "status") {
			if (data.status === "Exceeded") {
				value = `<span style="color: #dc2626; font-weight: 700;">⚠ ${value}</span>`;
			} else if (data.status === "Warning") {
				value = `<span style="color: #d97706; font-weight: 600;">⚡ ${value}</span>`;
			} else {
				value = `<span style="color: #059669; font-weight: 600;">✓ ${value}</span>`;
			}
		}

		if (column.fieldname === "available" && data.available < 0) {
			value = `<span style="color: #dc2626; font-weight: 700;">${value}</span>`;
		}

		if (column.fieldname === "utilization_pct") {
			const pct = data.utilization_pct || 0;
			const color = pct > 100 ? "#dc2626" : (pct >= 80 ? "#d97706" : "#059669");
			value = `<span style="color: ${color}; font-weight: 600;">${value}</span>`;
		}

		return value;
	}
};
