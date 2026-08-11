// TS Supplier Payment Status — BBPL Report Updated 11 Aug 2026.xlsx (#11).
// One row per submitted Purchase Invoice, chain walked backward:
//   S.No. | MR No | PO No | PR No | PI No | Supplier | Supp Invoice No |
//   Currency | Invoice Amount | Advance Paid Amount | Outstanding Amount |
//   Payment Status
frappe.query_reports["TS Supplier Payment Status"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			// dynamic FY start — never a build-time literal
			default: frappe.defaults.get_user_default("year_start_date") ||
				frappe.datetime.add_months(frappe.datetime.get_today(), -12),
			reqd: 1
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company")
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier"
		},
		{
			fieldname: "purchase_order",
			label: __("Purchase Order"),
			fieldtype: "Link",
			options: "Purchase Order"
		},
		{
			fieldname: "payment_status",
			label: __("Payment Status"),
			fieldtype: "Select",
			// Seeded from live values in onload so no phantom always-0 options.
			options: [""],
			default: ""
		},
		{
			fieldname: "outstanding_only",
			label: __("Outstanding Only"),
			fieldtype: "Check",
			default: 0
		}
	],
	onload(report) {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Purchase Invoice",
				filters: [["docstatus", "=", 1]],
				fields: ["distinct status as status"],
				limit_page_length: 0
			},
			callback(r) {
				if (!r || !r.message) return;
				const vals = r.message.map((d) => d.status).filter(Boolean).sort();
				const f = report.get_filter("payment_status");
				if (f) {
					f.df.options = [""].concat(vals).join("\n");
					f.refresh();
				}
			}
		});
	},
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "pi_status" && data && data.pi_status) {
			const st = data.pi_status;
			const map = {
				"Paid": "var(--green-600,#16a34a)",
				"Partly Paid": "var(--orange-500,#d97706)",
				"Unpaid": "var(--blue-500,#2563eb)",
				"Overdue": "var(--red-500,#b94a48)",
				"Return": "var(--gray-600,#6b7280)",
				"Debit Note Issued": "var(--gray-600,#6b7280)"
			};
			const colour = map[st];
			if (colour) {
				// span built from the RAW value, escaped — the datatable renders
				// cell content as HTML. Unmapped statuses render plain, uncoloured.
				const safe = frappe.utils.escape_html(st);
				value = `<span style="color:${colour};font-weight:600">${safe}</span>`;
			}
		}
		if (column.fieldname === "outstanding_amount" && data && data.outstanding_amount > 0) {
			value = `<span style="color:var(--red-500,#b94a48);font-weight:600">${value}</span>`;
		}
		return value;
	}
};
