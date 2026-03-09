frappe.ui.form.on("BBF Quality Inspection", {
	refresh(frm) {
		// Status indicators
		if (frm.doc.status === "Completed") {
			frm.page.set_indicator(__("Completed"), "green");
		} else if (frm.doc.status === "Rejected") {
			frm.page.set_indicator(__("Rejected"), "red");
		} else if (frm.doc.status === "In Progress") {
			frm.page.set_indicator(__("In Progress"), "orange");
		} else {
			frm.page.set_indicator(__("Pending"), "blue");
		}

		// Lock all fields after completion/rejection
		if (frm.doc.status === "Completed" || frm.doc.status === "Rejected") {
			frm.set_df_property("item_category", "read_only", 1);
			frm.set_df_property("moisture_percent", "read_only", 1);
			frm.set_df_property("impurity_percent", "read_only", 1);
			frm.set_df_property("foreign_matter_percent", "read_only", 1);
			frm.set_df_property("starch_content", "read_only", 1);
			frm.set_df_property("actual_gcv", "read_only", 1);
			frm.set_df_property("actual_moisture_percent", "read_only", 1);
			frm.set_df_property("grade", "read_only", 1);
			frm.set_df_property("decision", "read_only", 1);
			frm.set_df_property("hold_reason", "read_only", 1);
			frm.set_df_property("remarks", "read_only", 1);
		}

		// Complete Inspection button - fix operator precedence with parentheses
		if (!frm.is_new() && (frm.doc.status === "Pending" || frm.doc.status === "In Progress")) {
			frm.add_custom_button(__("Complete Inspection"), function () {
				if (!frm.doc.grade) {
					frappe.msgprint(__("Please set a Grade first"));
					return;
				}
				if (!frm.doc.decision) {
					frappe.msgprint(__("Please set a Decision first"));
					return;
				}
				frappe.confirm(
					__("Complete this inspection with decision: <b>{0}</b>?", [frm.doc.decision]),
					function () {
						frm.call("complete_inspection").then(() => {
							frm.reload_doc();
						});
					}
				);
			}).addClass("btn-primary-dark");
		}

		// Create Deduction Sheet button (after completion, if accepted)
		if (frm.doc.status === "Completed" && frm.doc.decision === "Accept") {
			frappe.db.count("BBF Deduction Sheet", {quality_inspection: frm.doc.name}).then(count => {
				if (count === 0) {
					frm.add_custom_button(__("Create Deduction Sheet"), function () {
						frappe.new_doc("BBF Deduction Sheet", {
							token_number: frm.doc.token_number,
							quality_inspection: frm.doc.name
						});
					}).addClass("btn-primary");
				}
			});
		}
	},

	setup(frm) {
		// Only show tokens at Gross Weighed stage (ready for QC)
		frm.set_query("token_number", function () {
			return {
				filters: {
					status: ["in", ["Gross Weighed"]],
					purpose: "Raw Material"
				}
			};
		});
	},

	token_number(frm) {
		if (frm.doc.token_number) {
			// Auto-fetch item_category from the item's item_group
			frappe.db.get_value("BBF Gate Entry",
				{ token_number: frm.doc.token_number, docstatus: 1 },
				["name", "purchase_order"]
			).then(r => {
				if (r.message && r.message.name) {
					// Fetch items from gate entry
					frappe.db.get_list("BBF Gate Entry Item", {
						filters: { parent: r.message.name },
						fields: ["item_code", "item_name"],
						limit: 1
					}).then(items => {
						if (items && items.length) {
							frm.set_value("item_code", items[0].item_code);
							frm.set_value("item_name", items[0].item_name);
							// Try to auto-detect item_category from item_group
							frappe.db.get_value("Item", items[0].item_code, "item_group").then(ig => {
								if (ig.message && ig.message.item_group) {
									let group = ig.message.item_group.toLowerCase();
									if (group.includes("grain") || group.includes("maize") || group.includes("rice") || group.includes("corn")) {
										frm.set_value("item_category", "Grain");
									} else if (group.includes("coal")) {
										frm.set_value("item_category", "Coal");
									}
								}
							});
						}
					});
				}
			});
		}
	},

	item_category(frm) {
		// When category changes and status is Pending, mark as In Progress
		if (frm.doc.status === "Pending" && frm.doc.item_category) {
			frm.set_value("status", "In Progress");
		}
	},

	actual_gcv(frm) {
		calculate_coal_variances(frm);
	},

	actual_moisture_percent(frm) {
		calculate_coal_variances(frm);
	},

	after_save(frm) {
		// Open a new blank form after saving (for rapid inspections)
		if (frm.doc.status === "Pending" || frm.doc.status === "In Progress") {
			frappe.set_route("Form", "BBF Quality Inspection", "new");
		}
	}
});

function calculate_coal_variances(frm) {
	if (frm.doc.item_category !== "Coal") return;

	if (frm.doc.po_gcv && frm.doc.actual_gcv) {
		let variance = ((frm.doc.actual_gcv - frm.doc.po_gcv) / frm.doc.po_gcv) * 100;
		frm.set_value("gcv_variance_percent", flt(variance, 2));
	}
	if (frm.doc.po_moisture_percent && frm.doc.actual_moisture_percent) {
		let variance = frm.doc.actual_moisture_percent - frm.doc.po_moisture_percent;
		frm.set_value("moisture_variance_percent", flt(variance, 3));
	}
}
