// TS PO Report — BBPL Report.xlsx spec (27 Jul 2026), read-only.
// One row per Purchase Order item:
//   S.No. | PO ID | PO Status | Material Request | Item Code | Item Name |
//   Description | Item Remark | Quantity | UOM | Price List Rate |
//   Discount Amount | Rate | Amount | IGST | SGST | CGST | Grand Total |
//   PO Creator | Received Qty | MR Creator
// GST is derived per item by apportioning the PO header tax rows — see the
// module docstring for why the PO Item GST columns cannot be read directly.
frappe.query_reports["TS PO Report"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("PO Date From"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3)
		},
		{
			fieldname: "to_date",
			label: __("PO Date To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today()
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
			fieldname: "item_code",
			label: __("Item Code"),
			fieldtype: "Link",
			options: "Item"
		},
		{
			fieldname: "material_request",
			label: __("Material Request"),
			fieldtype: "Link",
			options: "Material Request"
		},
		{
			fieldname: "approval_status",
			label: __("PO Status"),
			fieldtype: "Select",
			// Filled from the approval statuses actually present on live POs, so the
			// dropdown can never offer a value that returns zero rows by construction.
			options: [""],
			default: ""
		},
		{
			fieldname: "include_draft",
			label: __("Include Draft POs"),
			fieldtype: "Check",
			default: 0
		}
	],
	onload(report) {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Purchase Order",
				filters: [["ts_approval_status", "!=", ""]],
				fields: ["distinct ts_approval_status as ts_approval_status"],
				limit_page_length: 0
			},
			callback(r) {
				if (!r || !r.message) return;
				const vals = r.message.map((d) => d.ts_approval_status).filter(Boolean).sort();
				const f = report.get_filter("approval_status");
				if (f) {
					f.df.options = [""].concat(vals).join("\n");
					f.refresh();
				}
			}
		});
	},
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "po_status" && data && data.po_status) {
			const st = data.po_status;
			let colour = null;
			if (st === "Approved") colour = "var(--green-600,#16a34a)";
			else if (st === "Rejected") colour = "var(--red-500,#b94a48)";
			else if (st.indexOf("Pending") === 0) colour = "var(--orange-500,#d97706)";
			if (colour) {
				// Escape from the RAW value: the datatable renders cells as HTML and
				// "Pending" is matched as a prefix.
				const safe = frappe.utils.escape_html(st);
				value = `<span style="color:${colour};font-weight:600">${safe}</span>`;
			}
		}
		// A line with nothing received yet is the actionable case.
		if (column.fieldname === "received_qty" && data && !data.received_qty) {
			value = `<span style="color:var(--orange-500,#d97706)">${value}</span>`;
		}
		return value;
	}
};
