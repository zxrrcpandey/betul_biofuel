// TS MR Approval Date Wise — BBPL Report.xlsx spec (27 Jul 2026), read-only.
// One row per Material Request Item line of an approved MR:
//   S.No. | Approval Date | Approved By | MR Date | MR ID | Item Code |
//   Item Name | Description | Qty | UOM | MR Creator | Cost Center
frappe.query_reports["TS MR Approval Date Wise"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
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
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center"
		},
		{
			fieldname: "approved_by",
			label: __("Approved By"),
			fieldtype: "Link",
			options: "User"
		},
		{
			fieldname: "material_request_type",
			label: __("MR Type"),
			fieldtype: "Select",
			options: ["", "Purchase", "Material Transfer", "Material Issue", "Manufacture", "Service Request"].join("\n"),
			default: ""
		}
	]
};
