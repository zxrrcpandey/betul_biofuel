// TS Open RGP Register — submitted Returnable Gate Passes with signed ageing
// on expected_return_date (negative = still due, positive = days overdue).
// Vendor is deliberately a Data column (a Link -> Supplier column would gate
// the report for users without Supplier read). No indicator-pill spans —
// unreliable inside datatable cells; plain colored text only.
frappe.query_reports["TS Open RGP Register"] = {
	filters: [
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "MultiSelectList",
			default: ["Issued", "Out of Plant", "At Vendor", "Partially Returned", "Returned"],
			get_data: function (txt) {
				// LIVE distinct statuses (TS Pending MR idiom) — the picker can
				// never offer a value that returns zero rows by construction.
				return frappe.db
					.get_list("TS Returnable Gate Pass", {
						filters: { docstatus: 1 },
						fields: ["status"],
						group_by: "status",
						limit: 0,
					})
					.then(function (rows) {
						return (rows || [])
							.map(function (d) {
								return d.status;
							})
							.filter(Boolean)
							.filter(function (s) {
								return !txt || s.toLowerCase().indexOf(txt.toLowerCase()) !== -1;
							})
							.sort()
							.map(function (s) {
								return { value: s, description: "" };
							});
					});
			},
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "from_date",
			label: __("Challan Date From"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("Challan Date To"),
			fieldtype: "Date",
		},
		{
			fieldname: "overdue_only",
			label: __("Overdue Only"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
		},
	],
	formatter: function (value, row, column, data, default_formatter) {
		// House pattern: run the default formatter FIRST, then wrap.
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (column.fieldname === "status" && data.status) {
			var status_colors = {
				"Issued": "var(--blue-500,#2563eb)",
				"Returned": "var(--blue-500,#2563eb)",
				"Out of Plant": "var(--orange-500,#d97706)",
				"At Vendor": "var(--orange-500,#d97706)",
				"Partially Returned": "var(--orange-500,#d97706)",
				"Verified - Closed": "var(--green-500,#16a34a)",
				"Closed Short": "var(--red-500,#b94a48)",
			};
			var status_color = status_colors[data.status];
			if (status_color) {
				var safe_status = frappe.utils.escape_html(data.status);
				value =
					'<span style="color:' + status_color + ';font-weight:600">' + safe_status + "</span>";
			}
		}

		if (column.fieldname === "age_days" && data.age_days != null) {
			var n = data.age_days;
			if (n < 0) {
				// Still due — default color, "Nd left".
				value = frappe.utils.escape_html(Math.abs(n) + "d left");
			} else if (n <= 2) {
				value =
					'<span style="color:var(--orange-500,#d97706);font-weight:600">' +
					frappe.utils.escape_html(String(n)) +
					"</span>";
			} else if (n <= 7) {
				value =
					'<span style="color:var(--orange-500,#d97706);font-weight:700">' +
					frappe.utils.escape_html("▲ " + n) +
					"</span>";
			} else {
				value =
					'<span style="color:var(--red-500,#b94a48);font-weight:700">' +
					frappe.utils.escape_html("▲ " + n + "d over") +
					"</span>";
			}
		}

		if (
			column.fieldname === "expected_return_date" &&
			data.expected_return_date &&
			data.age_days != null &&
			data.age_days > 0
		) {
			value =
				'<span style="color:var(--orange-500,#d97706);font-weight:600">' + value + "</span>";
		}

		if (column.fieldname === "balance_qty" && data.balance_qty > 0) {
			value = '<span style="color:var(--red-500,#b94a48);font-weight:600">' + value + "</span>";
		}

		return value;
	},
};
