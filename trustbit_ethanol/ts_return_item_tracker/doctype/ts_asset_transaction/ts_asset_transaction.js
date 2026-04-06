frappe.ui.form.on("TS Asset Transaction", {
	refresh(frm) {
		// Status indicator
		const status_map = {
			"Draft": "orange",
			"Pending Approval": "blue",
			"Approved": "cyan",
			"Completed": "green",
			"Rejected": "red",
			"Cancelled": "grey",
		};
		if (frm.doc.status && status_map[frm.doc.status]) {
			frm.page.set_indicator(frm.doc.status, status_map[frm.doc.status]);
		}

		if (frm.is_new()) return;

		// Lock fields on Completed
		if (frm.doc.status === "Completed") {
			frm.disable_save();
		}

		// Draft + Discard → "Submit for Approval" button
		if (frm.doc.status === "Draft" && frm.doc.transaction_type === "Discard") {
			frm.add_custom_button(__("Submit for Approval"), () => {
				frappe.confirm(
					__("Submit this Discard for CEO/MD approval?"),
					() => {
						frappe.call({
							method: "trustbit_ethanol.ts_return_item_tracker.ts_asset_api.submit_for_discard_approval",
							args: { transaction_name: frm.doc.name },
							callback: () => frm.reload_doc(),
						});
					}
				);
			}).addClass("btn-primary-dark");
		}

		// Draft + non-Discard → "Complete Transaction" button
		if (frm.doc.status === "Draft" && frm.doc.transaction_type !== "Discard") {
			frm.add_custom_button(__("Complete Transaction"), () => {
				frappe.confirm(
					__("Complete this {0} transaction? Stock will be updated.", [frm.doc.transaction_type]),
					() => {
						frappe.call({
							method: "trustbit_ethanol.ts_return_item_tracker.ts_asset_api.complete_transaction",
							args: { transaction_name: frm.doc.name },
							callback: () => {
								frappe.show_alert({ message: __("Transaction completed!"), indicator: "green" });
								frm.reload_doc();
							},
						});
					}
				);
			}).addClass("btn-primary-dark");
		}

		// Pending Approval → Approve / Reject (for CEO/MD)
		if (frm.doc.status === "Pending Approval") {
			frm.add_custom_button(__("Approve Discard"), () => {
				frappe.call({
					method: "trustbit_ethanol.ts_return_item_tracker.ts_asset_api.approve_discard",
					args: { transaction_name: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			}).addClass("btn-success");

			frm.add_custom_button(__("Reject Discard"), () => {
				frappe.prompt(
					{ fieldtype: "Small Text", label: "Reason", fieldname: "reason", reqd: 1 },
					(values) => {
						frappe.call({
							method: "trustbit_ethanol.ts_return_item_tracker.ts_asset_api.reject_discard",
							args: { transaction_name: frm.doc.name, reason: values.reason },
							callback: () => frm.reload_doc(),
						});
					},
					__("Reject Reason")
				);
			}).addClass("btn-danger");
		}
	},
});
