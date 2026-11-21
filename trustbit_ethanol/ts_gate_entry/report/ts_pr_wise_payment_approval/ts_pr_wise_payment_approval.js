// TS PR Wise Payment Approval — BBPL Report Updated 11 Aug 2026.xlsx (#10).
// One row per submitted PO item x linked submitted PI; advance payments
// grouped per Payment Entry with positionally-aligned IDs / dates / UTRs.
frappe.query_reports["TS PR Wise Payment Approval"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("PO Date From"),
			fieldtype: "Date",
			default: frappe.defaults.get_user_default("year_start_date") ||
				frappe.datetime.add_months(frappe.datetime.get_today(), -12)
		},
		{
			fieldname: "to_date",
			label: __("PO Date To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today()
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
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center"
		},
		{
			fieldname: "has_invoice",
			label: __("Has Invoice"),
			fieldtype: "Select",
			options: ["", "Yes", "No"].join("\n"),
			default: ""
		},
		{
			fieldname: "has_advance",
			label: __("Has Advance Payment"),
			fieldtype: "Select",
			options: ["", "Yes", "No"].join("\n"),
			default: ""
		},
		{
			// must match GRN_STYLES in the .py
			fieldname: "grn_style",
			label: __("GRN Style"),
			fieldtype: "Select",
			options: [
				"C - One row per GRN",
				"A - GRNs in one cell, item amount",
				"B - GRNs in one cell, GRN total incl. GST"
			].join("\n"),
			default: "C - One row per GRN"
		}
	],
	after_datatable_render(datatable) {
		// each PO is one band, gray / white in turn (all rows of a PO share it);
		// style scoped to this table instance, rebuilt on every refresh
		if (datatable._ts_po_bands) datatable._ts_po_bands.remove();
		const data = (frappe.query_report && frappe.query_report.data) || [];
		const gray = [];
		let band = 0;
		data.forEach((r, i) => {
			if (i && r.purchase_order !== data[i - 1].purchase_order) band ^= 1;
			if (band) gray.push(`.${datatable.style.scopeClass} .dt-row-${i} .dt-cell`);
		});
		if (!gray.length) return;
		const el = document.createElement("style");
		// short rules: the browser drops part of one very long selector list
		let css = "";
		for (let i = 0; i < gray.length; i += 500) {
			css += `${gray.slice(i, i + 500).join(",")}{background-color:var(--control-bg,#f3f3f3)}`;
		}
		el.textContent = css;
		datatable.wrapper.insertBefore(el, datatable.datatableWrapper);
		datatable._ts_po_bands = el;
	},
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "pi_outstanding" && data &&
			data.pi_outstanding !== null && data.pi_outstanding !== undefined && data.pi_outstanding > 0) {
			value = `<span style="color:var(--red-500,#b94a48);font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "advance_paid" && data && data.advance_paid > 0) {
			value = `<span style="color:var(--blue-500,#2563eb);font-weight:600">${value}</span>`;
		}
		return value;
	}
};
