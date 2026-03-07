frappe.ui.form.on("BBF Transport Master", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Add Vehicle"), function () {
				let d = new frappe.ui.Dialog({
					title: __("Add Vehicle"),
					fields: [
						{
							fieldname: "vehicle_number",
							fieldtype: "Data",
							label: "Vehicle Number",
							reqd: 1,
							description: "e.g. MP09AB1234"
						},
						{
							fieldname: "vehicle_type",
							fieldtype: "Select",
							label: "Vehicle Type",
							options: "\nTruck\nFour Wheeler\nTwo Wheeler\nThree Wheeler\nTanker\nOther",
							reqd: 1
						},
						{
							fieldname: "capacity_kg",
							fieldtype: "Float",
							label: "Capacity (KG)"
						}
					],
					primary_action_label: __("Add"),
					primary_action(values) {
						frappe.call({
							method: "trustbit_ethanol.bbf_gate_entry.doctype.bbf_transport_master.bbf_transport_master.add_vehicle",
							args: {
								transporter: frm.doc.name,
								vehicle_number: values.vehicle_number,
								vehicle_type: values.vehicle_type,
								capacity_kg: values.capacity_kg || 0
							},
							callback(r) {
								if (!r.exc) {
									d.hide();
									frm.reload_doc();
									frappe.show_alert({
										message: __("Vehicle {0} added", [values.vehicle_number]),
										indicator: "green"
									});
								}
							}
						});
					}
				});
				d.show();
			}).addClass("btn-primary-dark");
		}
	}
});
