// Copyright (c) 2026, Trustbit Software and contributors
// TS Notification Read Audit — filters are ADVISORY ONLY; the server
// re-validates every one of them (role gate first, then dates/span/user/
// category/status). Status strings must match the .py vocabulary exactly.

frappe.query_reports["TS Notification Read Audit"] = {
	onload(report) {
		// Evidence-archive retrieval (code-tester finding: the endpoint had no
		// UI trigger). Server sets response.type="download", so navigate —
		// frappe.call would swallow the attachment. Role gate is server-side.
		report.page.add_inner_button(__("Download Evidence Archive"), () => {
			frappe.prompt(
				{
					fieldname: "month",
					label: __("Month (YYYY_MM)"),
					fieldtype: "Data",
					default: frappe.datetime.get_today().slice(0, 7).replace("-", "_"),
					reqd: 1,
				},
				(v) => {
					window.open(
						"/api/method/trustbit_ethanol.ts_gate_entry.report" +
						".ts_notification_read_audit.ts_notification_read_audit" +
						".download_snapshot?month=" + encodeURIComponent(v.month)
					);
				},
				__("Monthly Evidence Archive"),
				__("Download")
			);
		});
	},
	filters: [
		{
			fieldname: "user",
			label: __("User"),
			fieldtype: "Link",
			options: "User",
			// system accounts are excluded from the report rows server-side —
			// keep the autocomplete consistent so they never appear here either
			get_query: () => ({
				filters: { name: ["not in", ["Administrator", "Guest"]] },
			}),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Select",
			options: [
				"all", "PO", "MR", "Inspection", "Production",
				"Gate", "Budget", "Mention", "Other",
			].join("\n"),
			default: "all",
		},
		{
			fieldname: "status",
			label: __("Trail Status"),
			fieldtype: "Select",
			options: [
				"",
				"Pre-trail — no data possible",
				"Read — opened by user",
				"Read — bulk cleared after delivery",
				"Read — bulk cleared, no prior UI delivery",
				"Delivered to UI — not read",
				"No UI delivery recorded",
				"Read flag set outside trail",
			].join("\n"),
		},
		{
			fieldname: "only_unread",
			label: __("Unread only"),
			fieldtype: "Check",
			default: 0,
		},
	],
};
