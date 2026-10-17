// Copyright (c) 2026, Trustbit Software and contributors
// v2.59.0 — Notification Audience. The rows are fixed by the code; only the
// ticks and the skip list are edited here. The server re-validates on save.

frappe.ui.form.on("TS Notification Audience", {
	setup(frm) {
		// IT Head usually cannot read User: search through the admin-only query
		// and skip the client link check (the server still checks each user).
		frm.set_query("skip_accounts", () => ({
			query: "trustbit_ethanol.ts_gate_entry.ts_notification_routing.user_search",
		}));
		frm.fields_dict.skip_accounts.df.ignore_link_validation = 1;
	},
	refresh(frm) {
		frm.set_df_property("events", "cannot_add_rows", true);
		frm.set_df_property("events", "cannot_delete_rows", true);
		// v2.61.0 — heads and notify-only people are edited on their own page.
		frm.add_custom_button(__("Assign people"), () => frappe.set_route("assign-people"));
	},
});
