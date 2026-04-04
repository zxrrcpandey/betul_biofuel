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

		// Draft + Discard → "Submit for Approval" button
		if (frm.doc.status === "Draft" && frm.doc.transaction_type === "Discard") {
			frm.add_custom_button(__("Submit for Approval"), () => {
				frappe.call({
					method: "trustbit_ethanol.ts_asset_tracker.ts_asset_api.submit_for_discard_approval",
					args: { transaction_name: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			}, __("Actions")).addClass("btn-primary-dark");
		}

		// Draft + non-Discard → "Complete Transaction" button (fallback if auto-complete didn't run)
		if (frm.doc.status === "Draft" && frm.doc.transaction_type !== "Discard") {
			frm.add_custom_button(__("Complete Transaction"), () => {
				frappe.call({
					method: "trustbit_ethanol.ts_asset_tracker.ts_asset_api.complete_transaction",
					args: { transaction_name: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			}, __("Actions")).addClass("btn-primary-dark");
		}

		// Pending Approval → Approve / Reject (for CEO/MD)
		if (frm.doc.status === "Pending Approval") {
			frm.add_custom_button(__("Approve Discard"), () => {
				frappe.call({
					method: "trustbit_ethanol.ts_asset_tracker.ts_asset_api.approve_discard",
					args: { transaction_name: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			}, __("Actions")).addClass("btn-success");

			frm.add_custom_button(__("Reject Discard"), () => {
				frappe.prompt(
					{ fieldtype: "Small Text", label: "Reason", fieldname: "reason", reqd: 1 },
					(values) => {
						frappe.call({
							method: "trustbit_ethanol.ts_asset_tracker.ts_asset_api.reject_discard",
							args: { transaction_name: frm.doc.name, reason: values.reason },
							callback: () => frm.reload_doc(),
						});
					},
					__("Reject Reason")
				);
			}, __("Actions")).addClass("btn-danger");
		}
	},
});
