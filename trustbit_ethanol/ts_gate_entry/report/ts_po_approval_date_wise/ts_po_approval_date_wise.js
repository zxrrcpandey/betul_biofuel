// TS PO Approval Date Wise — BBPL Report.xlsx spec (27 Jul 2026), read-only.
// One row per approved Purchase Order:
//   S.No. | Approval Date | Approved By | PO ID | Supplier | Supplier Name |
//   Supplier Address & Contact | Cost Center | PO Amount | PO Amount With GST |
//   PO Status
// PO Status = the BBF approval state (ts_approval_status), not the ERPNext
// document status — confirmed with the user on 27 Jul 2026.
frappe.query_reports["TS PO Approval Date Wise"] = {
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
			fieldname: "approved_by",
			label: __("Approved By"),
			fieldtype: "Link",
			options: "User"
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
		}
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "approval_status" && data && data.approval_status) {
			const st = data.approval_status;
			let colour = null;
			if (st === "Approved") colour = "var(--green-600,#16a34a)";
			else if (st === "Rejected") colour = "var(--red-500,#b94a48)";
			else if (st.indexOf("Pending") === 0) colour = "var(--orange-500,#d97706)";
			if (colour) {
				// Re-escape from the RAW value rather than re-wrapping the formatted
				// one: the datatable renders cell content as HTML, and "Pending…" is a
				// prefix match, so anything stored past that prefix must not execute.
				const safe = frappe.utils.escape_html(st);
				value = `<span style="color:${colour};font-weight:600">${safe}</span>`;
			}
		}
		return value;
	}
};
