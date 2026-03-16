frappe.ui.form.on("BBF Token", {
	refresh(frm) {
		// Hide barcode and token_number on new unsaved form
		if (frm.is_new()) {
			frm.set_df_property("barcode", "hidden", 1);
			frm.set_df_property("token_number", "hidden", 1);
		} else {
			frm.set_df_property("barcode", "hidden", 0);
			frm.set_df_property("token_number", "hidden", 0);

			// Lock fields after token is saved (prevent editing)
			frm.set_df_property("purpose", "read_only", 1);
			frm.set_df_property("vehicle_number", "read_only", 1);
			frm.set_df_property("driver_name", "read_only", 1);
			frm.set_df_property("driver_mobile", "read_only", 1);
			frm.set_df_property("driver_license_number", "read_only", 1);

			// Add Print Token button
			frm.add_custom_button(__("Print Token"), function () {
				frm.print_doc();
			}).addClass("btn-primary-dark");
		}

		// Create GRN button - shown when Tare Weighed and no PR yet
		if (frm.doc.status === "Tare Weighed" && !frm.doc.purchase_receipt) {
			frm.add_custom_button(__("Create GRN"), function () {
				frappe.confirm(
					__("Create Purchase Receipt (GRN) for this token?<br><br>This will generate a Purchase Receipt against the linked Purchase Order with the net weight from the Weighbridge."),
					function () {
						frm.call("create_grn").then((r) => {
							frm.reload_doc();
							if (r.message && r.message.purchase_receipt) {
								frappe.show_alert({
									message: __("GRN {0} created successfully", [r.message.purchase_receipt]),
									indicator: "green"
								});
							}
						});
					}
				);
			}).addClass("btn-primary");
		}

		// View GRN button - shown when GRN exists
		if (frm.doc.purchase_receipt) {
			frm.add_custom_button(__("View GRN"), function () {
				frappe.set_route("Form", "Purchase Receipt", frm.doc.purchase_receipt);
			}, __("Actions"));
		}

		// Mark Exit button logic:
		// - Raw Material tokens: only after GRN Created
		// - Visitor/Non-Raw Material/Service/Other: any stage after Token Generated
		// - Not shown for Weighbridge Operator role
		let show_mark_exit = false;
		if (frm.doc.status && frm.doc.status !== "Exited") {
			let is_raw_material = frm.doc.purpose === "Raw Material";
			if (is_raw_material) {
				show_mark_exit = frm.doc.status === "GRN Created";
			} else {
				// Non-RM: can exit at any stage (visitor, service, etc.)
				show_mark_exit = true;
			}
		}

		if (show_mark_exit && !frappe.user.has_role("Weighbridge Operator")) {
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
		} else if (frm.doc.status === "GRN Created") {
			frm.page.set_indicator(__("GRN Created"), "green");
		} else if (frm.doc.status === "Token Generated") {
			frm.page.set_indicator(__("Token Generated"), "blue");
		} else {
			frm.page.set_indicator(__(frm.doc.status), "orange");
		}
	}
});
