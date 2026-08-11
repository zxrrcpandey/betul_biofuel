// TS Monthly Purchase Summary — BBPL Report Updated 11 Aug 2026.xlsx (#12).
// Month x Cost Centre: Allowed (Budget x Monthly Distribution), Purchase Value,
// Balance, Extra Expense and carry-forward Balance Allocated. The month window
// slices the DISPLAY only — the carry-forward always runs from FY month 1.
frappe.query_reports["TS Monthly Purchase Summary"] = {
	filters: [
		{
			fieldname: "fiscal_year",
			label: __("Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			default: frappe.defaults.get_user_default("fiscal_year"),
			reqd: 1
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center"
		},
		{
			fieldname: "from_month",
			label: __("From Month"),
			fieldtype: "Select",
			options: [""],
			default: ""
		},
		{
			fieldname: "to_month",
			label: __("To Month"),
			fieldtype: "Select",
			options: [""],
			default: ""
		}
	],
	onload(report) {
		const fill = () => {
			const fy = report.get_filter_value("fiscal_year");
			if (!fy) return;
			frappe.db.get_value("Fiscal Year", fy, ["year_start_date"]).then((r) => {
				if (!r || !r.message || !r.message.year_start_date) return;
				const start = frappe.datetime.str_to_obj(r.message.year_start_date);
				const labels = [];
				for (let i = 0; i < 12; i++) {
					const d = new Date(start.getFullYear(), start.getMonth() + i, 1);
					labels.push(moment(d).format("MMM YYYY"));
				}
				for (const f of ["from_month", "to_month"]) {
					const ff = report.get_filter(f);
					if (ff) {
						ff.df.options = [""].concat(labels).join("\n");
						ff.refresh();
					}
				}
			});
		};
		fill();
		const fyf = report.get_filter("fiscal_year");
		if (fyf) fyf.df.onchange = fill;
	},
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "extra_expense" && data && data.extra_expense > 0) {
			value = `<span style="color:var(--red-500,#b94a48);font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "balance" && data && data.balance < 0) {
			value = `<span style="color:var(--orange-500,#d97706);font-weight:600">${value}</span>`;
		}
		return value;
	}
};
