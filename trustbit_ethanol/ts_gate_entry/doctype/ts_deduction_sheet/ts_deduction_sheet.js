frappe.ui.form.on("TS Deduction Sheet", {
	refresh(frm) {
		// Status indicators
		if (frm.doc.status === "Approved") {
			frm.page.set_indicator(__("Approved"), "green");
		} else if (frm.doc.status === "Calculated") {
			frm.page.set_indicator(__("Calculated"), "orange");
		} else {
			frm.page.set_indicator(__("Draft"), "blue");
		}

		// Calculate Deductions button
		if (!frm.is_new() && (frm.doc.status === "Draft" || frm.doc.status === "Calculated")) {
			frm.add_custom_button(__("Calculate Deductions"), function () {
				if (!frm.doc.invoice_qty || !frm.doc.item_rate) {
					frappe.msgprint(__("Please enter Invoice Qty and Item Rate first"));
					return;
				}
				frm.call("calculate_deductions").then(() => {
					frm.reload_doc();
					frappe.show_alert({
						message: __("Deductions calculated successfully"),
						indicator: "green"
					});
				});
			}).addClass("btn-primary");
		}

		// Approve button
		if (frm.doc.status === "Calculated") {
			frm.add_custom_button(__("Approve Deductions"), function () {
				if (!frm.doc.decision) {
					frappe.msgprint(__("Please set a Decision first"));
					return;
				}
				frappe.confirm(
					__("Approve deductions? Net Payable: <b>{0}</b>", [
						format_currency(frm.doc.net_payable)
					]),
					function () {
						frm.call("approve_deductions").then(() => {
							frm.reload_doc();
							frappe.show_alert({
								message: __("Deduction Sheet approved"),
								indicator: "green"
							});
						});
					}
				);
			}).addClass("btn-primary-dark");
		}

		// Hold indicators for Coal
		if (frm.doc.item_category === "Coal") {
			if (frm.doc.hold_on_gcv) {
				frm.dashboard.add_comment(__("GCV below PO threshold - Vehicle Hold recommended"), "red", true);
			}
			if (frm.doc.hold_on_moisture) {
				frm.dashboard.add_comment(__("Moisture above PO threshold - Vehicle Hold recommended"), "red", true);
			}
		}
	},

	calculate_deductions_button(frm) {
		if (!frm.doc.invoice_qty || !frm.doc.item_rate) {
			frappe.msgprint(__("Please enter Invoice Qty and Item Rate first"));
			return;
		}
		frm.call("calculate_deductions").then(() => {
			frm.reload_doc();
		});
	},

	invoice_qty(frm) {
		calculate_weight_values(frm);
	},

	item_rate(frm) {
		calculate_weight_values(frm);
	},

	weight_deduction(frm) {
		calculate_weight_values(frm);
	},

	quality_inspection(frm) {
		if (frm.doc.quality_inspection) {
			frappe.db.get_doc("TS Quality Inspection", frm.doc.quality_inspection).then(qi => {
				frm.set_value("token_number", qi.token_number);
				frm.set_value("gate_entry", qi.gate_entry);
				frm.set_value("purchase_order", qi.purchase_order);
				frm.set_value("item_code", qi.item_code);
				frm.set_value("item_name", qi.item_name);
				frm.set_value("item_category", qi.item_category);

				if (qi.item_category === "Grain") {
					frm.set_value("qi_impurity_percent", qi.impurity_percent);
					frm.set_value("qi_moisture_percent", qi.moisture_percent);
				} else if (qi.item_category === "Coal") {
					frm.set_value("qi_po_gcv", qi.po_gcv);
					frm.set_value("qi_actual_gcv", qi.actual_gcv);
					frm.set_value("qi_po_moisture", qi.po_moisture_percent);
					frm.set_value("qi_actual_moisture", qi.actual_moisture_percent);
					frm.set_value("hold_on_gcv", qi.actual_gcv && qi.po_gcv && qi.actual_gcv < qi.po_gcv ? 1 : 0);
					frm.set_value("hold_on_moisture", qi.actual_moisture_percent && qi.po_moisture_percent && qi.actual_moisture_percent > qi.po_moisture_percent ? 1 : 0);
				}

				// Fetch supplier
				if (qi.purchase_order) {
					frappe.db.get_value("Purchase Order", qi.purchase_order, "supplier_name").then(r => {
						if (r.message) {
							frm.set_value("supplier_name", r.message.supplier_name);
						}
					});
				}
			});
		}
	}
});

function calculate_weight_values(frm) {
	let invoice_qty = flt(frm.doc.invoice_qty);
	let item_rate = flt(frm.doc.item_rate);
	let weight_ded = flt(frm.doc.weight_deduction);

	frm.set_value("invoice_value", invoice_qty * item_rate);
	frm.set_value("net_weight", invoice_qty - weight_ded);
	frm.set_value("mrn_amount", (invoice_qty - weight_ded) * item_rate);
}
