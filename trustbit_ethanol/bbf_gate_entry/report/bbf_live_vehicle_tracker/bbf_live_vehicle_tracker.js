frappe.query_reports["BBF Live Vehicle Tracker"] = {
	filters: [],
	formatter: function (value, row, column, data) {
		if (column.fieldname === "indicator") {
			let colors = {
				"GREEN": "#28a745",
				"YELLOW": "#ffc107",
				"RED": "#dc3545"
			};
			let color = colors[value] || "#6c757d";
			value = `<span style="color: ${color}; font-weight: bold">●</span>`;
		}
		if (column.fieldname === "minutes_at_stage" && value > 30) {
			value = `<span style="color: red; font-weight: bold">${value}</span>`;
		}
		return value;
	}
};
