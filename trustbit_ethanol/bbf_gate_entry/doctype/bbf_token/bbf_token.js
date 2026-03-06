frappe.ui.form.on("BBF Token", {
	refresh(frm) {
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
