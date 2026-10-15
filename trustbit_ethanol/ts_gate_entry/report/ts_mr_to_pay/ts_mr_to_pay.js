// TS MR to Pay — client sheet "V1.8 - 1", tab "MR to PAY" (BBPL #8).
// One row per submitted MR x distinct doc-level chain path (MR -> PO -> PR -> PI),
// with the PO advance, invoice and payment columns alongside.
frappe.query_reports["TS MR to Pay"] = {
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
			options: ["Purchase & Service", "Purchase", "Service Request", "All"].join("\n"),
			// Issue / Transfer MRs never chain to a PO and would only add blank rows.
			default: "Purchase & Service"
		},
		{
			fieldname: "material_request",
			label: __("Material Request"),
			fieldtype: "Link",
			options: "Material Request"
		},
		{
			fieldname: "purchase_order",
			label: __("Purchase Order"),
			fieldtype: "Link",
			options: "Purchase Order"
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier"
		}
	],
	formatter(value, row, column, data, default_formatter) {
		// Stored-XSS guard: the grid injects formatter output as raw HTML and
		// Data cells carry free text (supplier invoice no., UTR). Server-side
		// strip_html misses UNCLOSED tags, so every Data value is escaped here
		// before rendering. Exports keep raw values.
		if (column.fieldtype === "Data" && value != null && value !== "") {
			value = frappe.utils.escape_html(String(value));
		}
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "due_date" && data && data.is_overdue) {
			value = `<span style="color:var(--red-500,#dc2626);font-weight:600">${value}</span>`;
		}
		return value;
	}
};
