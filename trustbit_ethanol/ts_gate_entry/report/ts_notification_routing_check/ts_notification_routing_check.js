// Copyright (c) 2026, Trustbit Software and contributors
// v2.58.0 — Notification Routing Check (read-only; role gate is server-side).

frappe.query_reports["TS Notification Routing Check"] = {
	filters: [
		{
			fieldname: "user",
			label: __("User"),
			fieldtype: "Link",
			options: "User",
			reqd: 1,
			// Admin-only search (IT Head cannot read other Users, so the stock
			// search and link check would refuse them); it also leaves out
			// Administrator / Guest, who never receive role-wide notifications.
			get_query: () => ({
				query: "trustbit_ethanol.ts_gate_entry.ts_notification_routing.user_search",
			}),
			ignore_link_validation: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "gets" && data) {
			const color = data.gets === __("No") ? "orange" : "green";
			value = `<span class="indicator-pill ${color}">${value}</span>`;
		}
		return value;
	},
};
