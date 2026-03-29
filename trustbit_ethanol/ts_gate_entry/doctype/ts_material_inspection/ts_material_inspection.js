frappe.ui.form.on("TS Material Inspection", {
	refresh(frm) {
		// Status indicator
		let color_map = {
			"Pending Inspection": "orange",
			"Approved": "green",
			"Partially Approved": "blue",
			"On Hold": "yellow",
			"Rejected": "red",
			"Auto-Proceeded": "grey"
		};
		if (frm.doc.status) {
			frm.page.set_indicator(__(frm.doc.status), color_map[frm.doc.status] || "grey");
		}

		// Lock items table fields based on status
		let is_actionable = frm.doc.status === "Pending Inspection" || frm.doc.status === "On Hold";

		if (!frm.is_new() && is_actionable) {
			// Check if user can inspect
			let user = frappe.session.user;
			let can_inspect = (
				user === frm.doc.requester ||
				user === frm.doc.department_head ||
				frappe.user.has_role("IT Head") ||
				frappe.user.has_role("System Manager")
			);

			if (can_inspect) {
				// Approve All button
				frm.add_custom_button(__("Approve All"), function () {
					frappe.confirm(
						__("Approve all items? This will allow GRN to proceed."),
						function () {
							frm.call("approve_all").then(() => {
								frm.reload_doc();
								frappe.show_alert({ message: __("All items approved"), indicator: "green" });
							});
						}
					);
				}).addClass("btn-success");

				// Reject All button
				frm.add_custom_button(__("Reject All"), function () {
					let d = new frappe.ui.Dialog({
						title: __("Reject All Items"),
						fields: [
							{
								fieldname: "reason",
								fieldtype: "Small Text",
								label: __("Rejection Reason"),
								reqd: 1
							}
						],
						primary_action_label: __("Reject All"),
						primary_action: function () {
							frm.call("reject_all", { reason: d.get_value("reason") }).then(() => {
								d.hide();
								frm.reload_doc();
								frappe.show_alert({ message: __("All items rejected"), indicator: "red" });
							});
						}
					});
					d.show();
				}).addClass("btn-danger");

				// Submit Item-wise button
				frm.add_custom_button(__("Submit Item-wise"), function () {
					// Check all items have a status
					let pending = (frm.doc.items || []).filter(r => r.item_status === "Pending");
					if (pending.length) {
						frappe.msgprint(__("Please set a status for all items before submitting. {0} item(s) still Pending.", [pending.length]));
						return;
					}
					frappe.confirm(
						__("Submit item-wise inspection decisions?"),
						function () {
							frm.call("submit_itemwise").then(() => {
								frm.reload_doc();
							});
						}
					);
				}).addClass("btn-primary");
			}
		}

		// Lock fields after inspection is done
		if (!is_actionable && !frm.is_new()) {
			frm.set_df_property("overall_action", "read_only", 1);
			frm.set_df_property("rejection_reason", "read_only", 1);
			// Lock all item rows
			frm.fields_dict.items.grid.grid_rows.forEach(row => {
				row.toggle_editable("approved_qty", false);
				row.toggle_editable("held_qty", false);
				row.toggle_editable("rejected_qty", false);
				row.toggle_editable("item_status", false);
				row.toggle_editable("remark", false);
			});
		}
	},

	overall_action(frm) {
		if (frm.doc.overall_action === "Approve All") {
			(frm.doc.items || []).forEach(item => {
				frappe.model.set_value(item.doctype, item.name, "item_status", "Approved");
				frappe.model.set_value(item.doctype, item.name, "approved_qty", item.received_qty);
				frappe.model.set_value(item.doctype, item.name, "held_qty", 0);
				frappe.model.set_value(item.doctype, item.name, "rejected_qty", 0);
			});
			frm.refresh_field("items");
		} else if (frm.doc.overall_action === "Reject All") {
			(frm.doc.items || []).forEach(item => {
				frappe.model.set_value(item.doctype, item.name, "item_status", "Rejected");
				frappe.model.set_value(item.doctype, item.name, "rejected_qty", item.received_qty);
				frappe.model.set_value(item.doctype, item.name, "approved_qty", 0);
				frappe.model.set_value(item.doctype, item.name, "held_qty", 0);
			});
			frm.refresh_field("items");
		}
	}
});

frappe.ui.form.on("TS Material Inspection Item", {
	item_status(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.item_status === "Approved") {
			frappe.model.set_value(cdt, cdn, "approved_qty", row.received_qty);
			frappe.model.set_value(cdt, cdn, "held_qty", 0);
			frappe.model.set_value(cdt, cdn, "rejected_qty", 0);
		} else if (row.item_status === "Rejected") {
			frappe.model.set_value(cdt, cdn, "rejected_qty", row.received_qty);
			frappe.model.set_value(cdt, cdn, "approved_qty", 0);
			frappe.model.set_value(cdt, cdn, "held_qty", 0);
		} else if (row.item_status === "Held") {
			frappe.model.set_value(cdt, cdn, "held_qty", row.received_qty);
			frappe.model.set_value(cdt, cdn, "approved_qty", 0);
			frappe.model.set_value(cdt, cdn, "rejected_qty", 0);
		}
	}
});
