frappe.ui.form.on("BBF Gate Entry", {
	refresh(frm) {
		// Print button with format selection
		if (frm.doc.docstatus === 1 && frm.doc.token_number) {
			let _open_ge_print = function (format) {
				window.open(
					frappe.urllib.get_full_url(
						"/printview?doctype=BBF%20Gate%20Entry&name=" +
						encodeURIComponent(frm.doc.name) +
						"&format=" + encodeURIComponent(format)
					), "_blank"
				);
			};
			let is_g2_only = frappe.user.has_role("G2 Gate Operator")
				&& !frappe.user.has_role("IT Head")
				&& !frappe.user.has_role("System Manager");
			if (is_g2_only) {
				frappe.db.get_single_value("BBF Settings", "g2_print_mode").then(mode => {
					if (mode === "Detailed + Slip") {
						frm.add_custom_button(__("Detailed"), () => _open_ge_print("BBF Gate Entry Detailed"), __("Print"));
					}
					frm.add_custom_button(__("Slip"), () => _open_ge_print("BBF Gate Entry Slip"), __("Print"));
				});
			} else {
				frm.add_custom_button(__("Detailed"), () => _open_ge_print("BBF Gate Entry Detailed"), __("Print"));
				frm.add_custom_button(__("Slip"), () => _open_ge_print("BBF Gate Entry Slip"), __("Print"));
			}
		}

		// Add PO button (before submit)
		if (!frm.doc.docstatus) {
			frm.add_custom_button(__("Add Purchase Order"), function () {
				// Determine supplier filter (must match first PO's supplier)
				let supplier_filter = {};
				if (frm.doc.po_list && frm.doc.po_list.length > 0) {
					let first_supplier = frm.doc.po_list[0].supplier_name;
					if (first_supplier) {
						supplier_filter.supplier_name = first_supplier;
					}
				}

				// Get already-added POs to exclude
				let existing_pos = (frm.doc.po_list || []).map(r => r.purchase_order);

				let d = new frappe.ui.Dialog({
					title: __("Select Purchase Order"),
					fields: [
						{
							fieldname: "purchase_order",
							fieldtype: "Link",
							label: "Purchase Order",
							options: "Purchase Order",
							reqd: 1,
							get_query: function () {
								let filters = {
									docstatus: 1,
									status: ["not in", ["Closed", "Cancelled", "Completed"]],
									per_received: ["<", 100],
									name: ["not in", existing_pos]
								};
								if (supplier_filter.supplier_name) {
									filters.supplier_name = supplier_filter.supplier_name;
								}
								return { filters: filters };
							}
						}
					],
					primary_action_label: __("Add"),
					primary_action(values) {
						frm.call("add_purchase_order", { po_name: values.purchase_order }).then(() => {
							frm.refresh_fields();
							frm.dirty();
							frappe.show_alert({
								message: __("PO {0} added with items", [values.purchase_order]),
								indicator: "green"
							});
						});
						d.hide();
					}
				});
				d.show();
			}).addClass("btn-primary-dark");

			// Fetch All PO Items button
			if (frm.doc.po_list && frm.doc.po_list.length > 0) {
				frm.add_custom_button(__("Refresh PO Items"), function () {
					frm.call("fetch_po_items").then(() => {
						frm.refresh_fields();
						frappe.show_alert({
							message: __("PO Items refreshed from all linked POs"),
							indicator: "green"
						});
					});
				});
			}
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
		// Filter token_number to only show tokens at "Token Generated" stage
		frm.set_query("token_number", function () {
			return {
				filters: {
					status: ["in", ["Token Generated"]],
					entry_type: "Material"
				}
			};
		});

		// Filter purchase_order (legacy field) to only show open POs
		frm.set_query("purchase_order", function () {
			let filters = {
				docstatus: 1,
				status: ["not in", ["Closed", "Cancelled", "Completed"]],
				per_received: ["<", 100]
			};
			if (frm.doc.supplier_name) {
				filters.supplier_name = frm.doc.supplier_name;
			}
			return { filters: filters };
		});

		// Filter PO in child table to same supplier
		frm.set_query("purchase_order", "po_list", function () {
			let filters = {
				docstatus: 1,
				status: ["not in", ["Closed", "Cancelled", "Completed"]],
				per_received: ["<", 100]
			};
			if (frm.doc.supplier_name) {
				filters.supplier_name = frm.doc.supplier_name;
			}
			return { filters: filters };
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
								fieldname: "po_list_html",
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
							<td><button class="btn btn-xs btn-primary select-po">Add</button></td>
						</tr>`;
					});
					html += "</tbody></table>";

					d.fields_dict.po_list_html.$wrapper.html(html);
					d.fields_dict.po_list_html.$wrapper.find(".select-po").each(function (i) {
						$(this).data("po", r.message[i].name);
					});
					d.fields_dict.po_list_html.$wrapper.find(".select-po").on("click", function () {
						let po_name = $(this).data("po");
						frm.call("add_purchase_order", { po_name: po_name }).then(() => {
							frm.refresh_fields();
							frm.dirty();
							frappe.show_alert({
								message: __("PO {0} added", [po_name]),
								indicator: "green"
							});
						});
						d.hide();
					});
					d.show();
				} else {
					frappe.msgprint(__("No matching Purchase Orders found"));
				}
			}
		});
	},

	purchase_order(frm) {
		// Legacy: when purchase_order is set directly, auto-add to po_list
		if (frm.doc.purchase_order && (!frm.doc.po_list || frm.doc.po_list.length === 0)) {
			frm.call("add_purchase_order", { po_name: frm.doc.purchase_order }).then(() => {
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
