frappe.ui.form.on("BBF Unloading Entry", {
	refresh(frm) {
		if (frm.doc.name && !frm.is_new()) {
			if (frm.doc.status === "Pending") {
				frm.add_custom_button(__("Start Unloading"), function () {
					frm.call("start_unloading").then(() => {
						frm.reload_doc();
						frappe.show_alert({
							message: __("Unloading started"),
							indicator: "blue"
						});
					});
				}, __("Actions"));
			}

			if (frm.doc.status === "Unloading") {
				frm.add_custom_button(__("End Unloading"), function () {
					frappe.confirm(
						__("Confirm unloading is complete?"),
						function () {
							frm.call("end_unloading").then(() => {
								frm.reload_doc();
								frappe.show_alert({
									message: __("Unloading completed"),
									indicator: "green"
								});
							});
						}
					);
				}, __("Actions"));
			}
		}

		// Color-code
		let color = { "Pending": "orange", "Unloading": "blue", "Completed": "green" };
		if (frm.doc.status) {
			frm.page.set_indicator(__(frm.doc.status), color[frm.doc.status] || "grey");
		}
	},

	token_number(frm) {
		if (frm.doc.token_number) {
			frappe.db.get_value("BBF Gate Entry",
				{ token_number: frm.doc.token_number, docstatus: 1 },
				["name", "purchase_order"]
			).then(r => {
				if (r.message) {
					frm.set_value("gate_entry", r.message.name);
				}
			});
		}
	}
});
