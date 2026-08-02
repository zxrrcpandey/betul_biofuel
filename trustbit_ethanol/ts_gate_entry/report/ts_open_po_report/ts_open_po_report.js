// TS Open PO Report — BBPL Report.xlsx spec (27 Jul 2026), read-only.
// One row per PO ITEM of a still-open submitted PO:
//   S.No. | Item Name | Item Code | Item Remark | PO ID | MR ID | Supplier |
//   Cost Center | PO Amount | Ordered Qty | Received Qty | PO Balance Qty |
//   UOM | Delivery Date | PO Date | PO Creator
// "Open" = submitted, not Closed/Completed/Delivered/Cancelled/Stopped, and
// per_received < 100 unless "Include Fully Received" is ticked.
frappe.query_reports["TS Open PO Report"] = {
	filters: [
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center"
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier"
		},
		{
			fieldname: "item_code",
			label: __("Item Code"),
			fieldtype: "Link",
			options: "Item"
		},
		{
			fieldname: "from_date",
			label: __("PO Date From"),
			fieldtype: "Date"
		},
		{
			fieldname: "to_date",
			label: __("PO Date To"),
			fieldtype: "Date"
		},
		{
			fieldname: "include_fully_received",
			label: __("Include Fully Received"),
			fieldtype: "Check",
			default: 0
		},
		{
			fieldname: "hide_fully_received_lines",
			label: __("Hide Fully Received Lines"),
			fieldtype: "Check",
			default: 0
		}
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "balance_qty" && data && data.balance_qty > 0) {
			value = `<span style="color:var(--red-500,#b94a48);font-weight:600">${value}</span>`;
		}
		// Overdue delivery on a line that still has a pending balance.
		if (
			column.fieldname === "delivery_date" &&
			data &&
			data.delivery_date &&
			data.balance_qty > 0 &&
			frappe.datetime.get_diff(frappe.datetime.get_today(), data.delivery_date) > 0
		) {
			value = `<span style="color:var(--orange-500,#d97706);font-weight:600">${value}</span>`;
		}
		return value;
	}
};
