frappe.ui.form.on("BBF Gate Entry", {
	refresh(frm) {
		if (frm.doc.purchase_order && !frm.doc.docstatus) {
			frm.add_custom_button(__("Fetch PO Items"), function () {
				frm.call("fetch_po_items").then(() => {
					frm.refresh_fields();
					frappe.show_alert({
						message: __("PO Items fetched successfully"),
						indicator: "green"
					});
				});
			});
		}
	},

	token_number(frm) {
		if (frm.doc.token_number) {
			// Check if token already has a gate entry
			frappe.db.get_list("BBF Gate Entry", {
				filters: {
					token_number: frm.doc.token_number,
					docstatus: ["!=", 2],
					name: ["!=", frm.doc.name || ""]
				},
				limit: 1
			}).then(r => {
				if (r && r.length) {
					frappe.msgprint(__("This token already has a Gate Entry: {0}", [r[0].name]));
					frm.set_value("token_number", "");
				}
			});
		}
	},

	search_po_button(frm) {
		frappe.call({
			method: "trustbit_ethanol.bbf_gate_entry.api.get_purchase_orders",
			args: {
				po_id: frm.doc.po_search_id || "",
				po_date: frm.doc.po_search_date || "",
				tentative_qty: frm.doc.po_search_qty || 0
			},
			callback: function (r) {
				if (r.message && r.message.length) {
					let d = new frappe.ui.Dialog({
						title: __("Select Purchase Order"),
						fields: [
							{
								fieldname: "po_list",
								fieldtype: "HTML"
							}
						]
					});

					let html = '<table class="table table-bordered">';
					html += "<thead><tr><th>PO</th><th>Supplier</th><th>Date</th><th>Total Qty</th><th>Action</th></tr></thead><tbody>";
					r.message.forEach(po => {
						html += `<tr>
							<td>${po.name}</td>
							<td>${po.supplier_name || ""}</td>
							<td>${po.transaction_date || ""}</td>
							<td>${po.total_qty || ""}</td>
							<td><button class="btn btn-xs btn-primary select-po" data-po="${po.name}">Select</button></td>
						</tr>`;
					});
					html += "</tbody></table>";

					d.fields_dict.po_list.$wrapper.html(html);
					d.fields_dict.po_list.$wrapper.find(".select-po").on("click", function () {
						frm.set_value("purchase_order", $(this).data("po"));
						d.hide();
						frm.call("fetch_po_items").then(() => {
							frm.refresh_fields();
						});
					});
					d.show();
				} else {
					frappe.msgprint(__("No matching Purchase Orders found"));
				}
			}
		});
	},

	purchase_order(frm) {
		if (frm.doc.purchase_order) {
			frm.call("fetch_po_items").then(() => {
				frm.refresh_fields();
			});
		}
	},

	material_flow(frm) {
		if (frm.doc.material_flow === "Raw Material") {
			frm.set_value("route_to", "Weighbridge");
		} else if (frm.doc.material_flow === "Non-Raw Material") {
			frm.set_value("route_to", "Stores/Department");
		}
	}
});
