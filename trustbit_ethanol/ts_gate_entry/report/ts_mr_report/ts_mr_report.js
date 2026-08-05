// TS MR Report — BBPL Report.xlsx spec (27 Jul 2026), sheet "MR report".
// One row per MR item line with its downstream fulfilment:
//   S.No. | MR ID | Doc Status | Item Code | Item Name | Description |
//   Item Remark | Quantity | Completed Qty | Received Qty | Pending Quantity |
//   UOM | Cost Center | Define Use Location | Responsible Person
// Completed/Received come from ERPNext's own MR Item rollups (ordered_qty /
// received_qty) — see the module docstring for why deriving them from PO/PR
// line links produces zeros on this data.
frappe.query_reports["TS MR Report"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("MR Date From"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3)
		},
		{
			fieldname: "to_date",
			label: __("MR Date To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today()
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
			fieldname: "status",
			label: __("Doc Status"),
			fieldtype: "Select",
			options: ["", "Draft", "Pending", "Ordered", "Partially Ordered", "Received",
				"Partially Received", "Transferred", "Issued", "Stopped", "Cancelled"].join("\n"),
			default: ""
		},
		{
			fieldname: "material_request_type",
			label: __("MR Type"),
			fieldtype: "Select",
			options: ["", "Purchase", "Material Transfer", "Material Issue", "Manufacture", "Service Request"].join("\n"),
			default: ""
		},
		{
			fieldname: "only_pending",
			label: __("Only Lines With Pending Qty"),
			fieldtype: "Check",
			default: 0
		},
		{
			fieldname: "include_cancelled",
			label: __("Include Cancelled"),
			fieldtype: "Check",
			default: 0
		}
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "pending_qty" && data && data.pending_qty > 0) {
			value = `<span style="color:var(--red-500,#b94a48);font-weight:600">${value}</span>`;
		}
		// A line nobody has raised a PO for yet is the actionable case.
		if (column.fieldname === "completed_qty" && data && !data.completed_qty) {
			value = `<span style="color:var(--orange-500,#d97706);font-weight:600">${value}</span>`;
		}
		return value;
	}
};
