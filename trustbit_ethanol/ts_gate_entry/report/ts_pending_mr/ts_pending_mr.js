// TS Pending MR — BBPL Report.xlsx spec (27 Jul 2026), sheet "Panding MR".
// One row per MR item line of a Material Request still awaiting approval:
//   S.No. | MR Date | Pending By | Cost Center | MR ID | Item Code |
//   Item Name | Description | Qty | UOM | MR Creator
// Pending = ts_mr_status LIKE 'Pending%', docstatus 0 or 1 (cancelled MRs keep
// a stale "Pending ..." label and are excluded).
frappe.query_reports["TS Pending MR"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("MR Date From"),
			fieldtype: "Date"
		},
		{
			fieldname: "to_date",
			label: __("MR Date To"),
			fieldtype: "Date"
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center"
		},
		{
			fieldname: "pending_status",
			label: __("Pending With"),
			fieldtype: "Select",
			// Populated from the live MR approval statuses actually in use, so the
			// dropdown can never offer a stage that returns zero rows by construction.
			options: [""],
			default: ""
		},
		{
			fieldname: "material_request_type",
			label: __("MR Type"),
			fieldtype: "Select",
			options: ["", "Purchase", "Material Transfer", "Material Issue", "Manufacture", "Service Request"].join("\n"),
			default: ""
		}
	],
	onload(report) {
		// Fill "Pending With" from the statuses that actually exist on live MRs.
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Material Request",
				filters: [
					["ts_mr_status", "like", "Pending%"],
					["docstatus", "in", [0, 1]]
				],
				fields: ["distinct ts_mr_status as ts_mr_status"],
				limit_page_length: 0
			},
			callback(r) {
				if (!r || !r.message) return;
				const vals = r.message
					.map((d) => d.ts_mr_status)
					.filter(Boolean)
					.sort();
				const f = report.get_filter("pending_status");
				if (f) {
					f.df.options = [""].concat(vals).join("\n");
					f.refresh();
				}
			}
		});
	},
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "pending_by" && data && data.pending_by) {
			const safe = frappe.utils.escape_html(data.pending_by);
			value = `<span style="color:var(--orange-500,#d97706);font-weight:600">${safe}</span>`;
		}
		return value;
	}
};
