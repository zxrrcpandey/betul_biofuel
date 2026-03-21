frappe.ui.form.on("BBF Gate Entry", {
	refresh(frm) {
		// Print button with format selection
		if (frm.doc.docstatus === 1 && frm.doc.token_number) {
			frm.add_custom_button(__("Print Detailed"), function () {
				window.open(
					frappe.urllib.get_full_url(
						"/printview?doctype=BBF%20Gate%20Entry&name=" +
						encodeURIComponent(frm.doc.name) +
						"&format=BBF%20Gate%20Entry%20Detailed"
					), "_blank"
				);
			}, __("Print"));
			frm.add_custom_button(__("Print Slip"), function () {
				window.open(
					frappe.urllib.get_full_url(
						"/printview?doctype=BBF%20Gate%20Entry&name=" +
						encodeURIComponent(frm.doc.name) +
						"&format=BBF%20Gate%20Entry%20Slip"
					), "_blank"
				);
			}, __("Print"));
		}

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

		// Lock fields after submit
		if (frm.doc.docstatus === 1) {
			frm.set_df_property("token_number", "read_only", 1);
			frm.set_df_property("purchase_order", "read_only", 1);
			frm.set_df_property("material_flow", "read_only", 1);
			frm.set_df_property("transporter", "read_only", 1);
			frm.set_df_property("lr_number", "read_only", 1);
			frm.set_df_property("lr_date", "read_only", 1);
		}
	},

	setup(frm) {
		// Filter token_number to only show tokens at "Token Generated" stage (any purpose)
		frm.set_query("token_number", function () {
			return {
				filters: {
					status: ["in", ["Token Generated"]]
				}
			};
		});

		// Filter purchase_order to only show open POs with remaining qty
		frm.set_query("purchase_order", function () {
			return {
				filters: {
					docstatus: 1,
					status: ["not in", ["Closed", "Cancelled", "Completed"]],
					per_received: ["<", 100]
				}
			};
		});
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

					let esc = frappe.utils.escape_html;
					let html = '<table class="table table-bordered">';
					html += "<thead><tr><th>PO</th><th>Supplier</th><th>Date</th><th>Total Qty</th><th>Received %</th><th>Action</th></tr></thead><tbody>";
					r.message.forEach(po => {
						html += `<tr>
							<td>${esc(po.name)}</td>
							<td>${esc(po.supplier_name || "")}</td>
							<td>${esc(po.transaction_date || "")}</td>
							<td>${esc(po.total_qty || "")}</td>
							<td>${esc(po.per_received || 0)}%</td>
							<td><button class="btn btn-xs btn-primary select-po">Select</button></td>
						</tr>`;
					});
					html += "</tbody></table>";

					d.fields_dict.po_list.$wrapper.html(html);
					d.fields_dict.po_list.$wrapper.find(".select-po").each(function (i) {
						$(this).data("po", r.message[i].name);
					});
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
			frm.set_value("requires_weighing", 0);
		} else if (frm.doc.material_flow === "Non-Raw Material") {
			if (frm.doc.requires_weighing) {
				frm.set_value("route_to", "Weighbridge");
			} else {
				frm.set_value("route_to", "Stores/Department");
			}
		}
	},

	requires_weighing(frm) {
		if (frm.doc.material_flow === "Non-Raw Material") {
			if (frm.doc.requires_weighing) {
				frm.set_value("route_to", "Weighbridge");
			} else {
				frm.set_value("route_to", "Stores/Department");
			}
		}
	}
});
