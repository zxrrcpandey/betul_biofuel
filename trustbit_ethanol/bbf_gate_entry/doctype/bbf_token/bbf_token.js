frappe.ui.form.on("BBF Token", {
	refresh(frm) {
		// Hide barcode and token_number on new unsaved form
		if (frm.is_new()) {
			frm.set_df_property("barcode", "hidden", 1);
			frm.set_df_property("token_number", "hidden", 1);
		} else {
			frm.set_df_property("barcode", "hidden", 0);
			frm.set_df_property("token_number", "hidden", 0);

			// Add Print Token button
			frm.add_custom_button(__("Print Token"), function () {
				frm.print_doc();
			}).addClass("btn-primary-dark");
		}

		if (frm.doc.status && frm.doc.status !== "Exited" && frm.doc.status !== "Token Generated") {
			frm.add_custom_button(__("Mark Exit"), function () {
				frappe.confirm(
					__("Are you sure you want to mark this vehicle as exited?"),
					function () {
						frm.call("mark_exit").then(() => {
							frm.reload_doc();
							frappe.show_alert({
								message: __("Vehicle marked as exited"),
								indicator: "green"
							});
						});
					}
				);
			}, __("Actions"));
		}

		// Color-code status
		if (frm.doc.status === "Exited") {
			frm.page.set_indicator(__("Exited"), "green");
		} else if (frm.doc.status === "Token Generated") {
			frm.page.set_indicator(__("Token Generated"), "blue");
		} else {
			frm.page.set_indicator(__(frm.doc.status), "orange");
		}
	}
});
