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

		// Complete Inspection button
		if (!frm.is_new() && frm.doc.status === "Pending" || frm.doc.status === "In Progress") {
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
