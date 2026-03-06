frappe.query_reports["BBF Vehicle Turnaround Report"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1)
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today()
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nToken Generated\nPO Linked\nGross Weighed\nUnloading\nTare Weighed\nQuality Done\nGRN Created\nExited"
		}
	],
	formatter: function (value, row, column, data) {
		if (column.fieldname === "total_turnaround_minutes" && value) {
			let settings_threshold = 120;  // 2 hours default
			if (value > settings_threshold) {
				value = `<span style="color: red; font-weight: bold">${value}</span>`;
			}
		}
		return value;
	}
};
