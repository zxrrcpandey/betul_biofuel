// TS Lead Time Analysis — BBPL Report Updated 11 Aug 2026.xlsx (#7).
// One row per MR item x distinct doc-level chain path (MR -> PO -> PR -> PI).
// Day-delta columns are blank when a leg is missing — never 0.
frappe.query_reports["TS Lead Time Analysis"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("MR Date From"),
			fieldtype: "Date",
			default: frappe.defaults.get_user_default("year_start_date") ||
				frappe.datetime.add_months(frappe.datetime.get_today(), -12)
		},
		{
			fieldname: "to_date",
			label: __("MR Date To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today()
		},
		{
			fieldname: "material_request_type",
			label: __("MR Type"),
			fieldtype: "Select",
			options: ["All", "Purchase", "Material Transfer", "Material Issue", "Manufacture", "Service Request"].join("\n"),
			// Purchase by default: other purposes can never chain to a PO and
			// would pollute the MR-Only counts.
			default: "Purchase"
		},
		{
			fieldname: "material_request",
			label: __("Material Request"),
			fieldtype: "Link",
			options: "Material Request"
		},
		{
			fieldname: "item_code",
			label: __("Item Code"),
			fieldtype: "Link",
			options: "Item"
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier"
		},
		{
			fieldname: "chain_stage",
			label: __("Chain Stage"),
			fieldtype: "Select",
			options: ["", "MR Only (No PO)", "PO Placed (No PR)", "Received (No PI)", "Invoiced"].join("\n"),
			default: ""
		}
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// Long total lead time deserves attention; blank stays blank.
		if (column.fieldname === "mr_to_pi_days" && data &&
			data.mr_to_pi_days !== null && data.mr_to_pi_days !== undefined && data.mr_to_pi_days > 45) {
			value = `<span style="color:var(--red-500,#b94a48);font-weight:600">${value}</span>`;
		}
		return value;
	}
};
