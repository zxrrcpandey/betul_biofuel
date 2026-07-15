// Copyright (c) 2026, Trustbit Software
frappe.query_reports["TS Department Release Variance"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
		  default: frappe.datetime.add_months(frappe.datetime.get_today(), -1) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date",
		  default: frappe.datetime.get_today() },
		{ fieldname: "category", label: __("Department"), fieldtype: "Link",
		  options: "TS Production BOM Category" },
		{ fieldname: "production_entry", label: __("Production Run"), fieldtype: "Link",
		  options: "TS Production Entry" },
		{ fieldname: "status", label: __("Slot Status"), fieldtype: "Select",
		  options: "\nPending\nReleased\nCancelled" },
	],
};
