frappe.ui.form.on("TS Gate Entry", {
	refresh(frm) {
		// Post-dated entry check — ONLY on brand-new Gate Entries being created (Lesson 166).
		// Previously triggered on every draft view, polluting the screen and silently
		// unlocking entry_date/entry_time on already-saved drafts.
		if (frm.is_new()) {
			// Apply cached result instantly (prevents lock on re-render)
			if (frm._pd_access && frm._pd_access.enabled) {
				_ge_pd_apply(frm, frm._pd_access);
			}
			// Fetch fresh result (first load or refresh)
			if (!frm._pd_fetched) {
				frm._pd_fetched = true;
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.ts_post_dated.check_post_dated_access",
					args: { doctype: "TS Gate Entry", token_number: frm.doc.token_number || "" },
					async: true,
					callback(r) {
						if (r.message && r.message.enabled) {
							frm._pd_access = r.message;
							_ge_pd_apply(frm, r.message);
						}
					}
				});
			}
		} else {
			// Saved draft or submitted — remove any stale banner
			$(frm.wrapper).find(".pd-banner").remove();
		}

		// Custom Print PDF button (direct PDF download, bypasses /printview preview)
		// Matches MR/PO pattern — single top-level button with format selection prompt.
		// Shows on any saved doc (draft or submitted), NOT on brand-new unsaved.
		if (!frm.is_new() && frm.doc.token_number) {
			_ts_add_gate_entry_print_button(frm);
		}

		// Stock Direction toggle — show/hide sections
		let is_stock_out = frm.doc.stock_direction === "Stock OUT";
		// PO sections: hide for Stock OUT
		frm.set_df_property("po_section", "hidden", is_stock_out ? 1 : 0);
		frm.set_df_property("po_list_section", "hidden", is_stock_out ? 1 : 0);
		// SI section: show for Stock OUT only
		frm.set_df_property("si_section", "hidden", is_stock_out ? 0 : 1);
		// Material Flow section: hide entirely for Stock OUT
		frm.set_df_property("flow_section", "hidden", is_stock_out ? 1 : 0);
		frm.set_df_property("material_flow", "reqd", is_stock_out ? 0 : 1);
		// Transport section: hide for Stock OUT (no LR for outbound)
		frm.set_df_property("transport_section", "hidden", is_stock_out ? 1 : 0);
		// Items section: hide for Stock OUT (items auto-fetched from SI, read-only)
		if (is_stock_out) {
			let has_items = frm.doc.po_items && frm.doc.po_items.length > 0;
			frm.set_df_property("items_section", "hidden", has_items ? 0 : 1);
			if (has_items) {
				let el = frm.fields_dict.items_section;
				if (el && el.$wrapper) {
					el.$wrapper.find(".section-head, .control-label").text("Sales Invoice Items");
				}
				frm.fields_dict.po_items.grid.update_docfield_property("purchase_order", "hidden", 1);
				frm.fields_dict.po_items.grid.df.read_only = 1;
				frm.fields_dict.po_items.grid.refresh();
			}
		} else {
			frm.set_df_property("items_section", "hidden", 0);
			frm.fields_dict.po_items.grid.update_docfield_property("purchase_order", "hidden", 0);
			frm.fields_dict.po_items.grid.df.read_only = 0;
			frm.fields_dict.po_items.grid.refresh();
		}
		// Remove Add PO button for Stock OUT
		if (is_stock_out) {
			frm.remove_custom_button(__("Add Purchase Order"));
		}

		// Fetch SI Items button (Stock OUT, before submit)
		if (!frm.doc.docstatus && frm.doc.stock_direction === "Stock OUT" && frm.doc.sales_invoice) {
			frm.add_custom_button(__("Fetch SI Items"), function () {
				frm.call("fetch_si_items").then(() => {
					frm.refresh_fields();
					frappe.show_alert({
						message: __("Items fetched from Sales Invoice"),
						indicator: "green"
					});
				});
			}).addClass("btn-primary-dark");
		}

		// Add PO button (before submit, Stock IN only)
		if (!frm.doc.docstatus && frm.doc.stock_direction !== "Stock OUT") {
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
			frm.set_df_property("sales_invoice", "read_only", 1);
			frm.set_df_property("stock_direction", "read_only", 1);
			frm.set_df_property("material_flow", "read_only", 1);
			frm.set_df_property("transporter", "read_only", 1);
			frm.set_df_property("lr_number", "read_only", 1);
			frm.set_df_property("lr_date", "read_only", 1);
			frm.set_df_property("vehicle_master", "read_only", 1);
			frm.set_df_property("driver", "read_only", 1);
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

		// Filter Sales Invoice for Stock OUT
		frm.set_query("sales_invoice", function () {
			return {
				filters: {
					docstatus: 1,
					status: ["not in", ["Cancelled", "Credit Note Issued"]]
				}
			};
		});

		// Vehicle Master + Driver Master filters removed — fields hidden at G2

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
			frappe.db.get_list("TS Gate Entry", {
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
					return;
				}

				// Fetch G1 driver details from Token (without fetch_from to keep fields editable)
				frappe.db.get_value("TS Token", frm.doc.token_number,
					["ts_driver_name_g1", "ts_driver_license_g1", "ts_driver_mobile_g1"]
				).then(t => {
					if (t.message) {
						if (t.message.ts_driver_name_g1) frm.set_value("ts_g1_driver_name", t.message.ts_driver_name_g1);
						if (t.message.ts_driver_license_g1) frm.set_value("ts_g1_driver_license", t.message.ts_driver_license_g1);
						if (t.message.ts_driver_mobile_g1) frm.set_value("ts_g1_driver_mobile", t.message.ts_driver_mobile_g1);
					}
				});
			});
		}
	},

	search_po_button(frm) {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.api.get_purchase_orders",
			args: {
				po_id: frm.doc.po_search_id || "",
				po_date: frm.doc.po_search_date || ""
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

	stock_direction(frm) {
		if (frm.doc.stock_direction === "Stock OUT") {
			frm.set_value("route_to", "Weighbridge");
			frm.set_value("material_flow", "Non-Raw Material");
		}
		frm.trigger("refresh");
	},

	sales_invoice(frm) {
		if (frm.doc.sales_invoice && frm.doc.stock_direction === "Stock OUT") {
			frm.call("fetch_si_items").then(() => {
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

function _ge_pd_apply(frm, access) {
	try {
		const $target = $(frm.wrapper).find(".form-page");
		if (!$target.find(".pd-banner").length) {
			$target.prepend(`<div class="pd-banner" style="padding:10px 16px;background:#e3f2fd;border:1px solid #bbdefb;border-radius:6px;margin:8px 15px;font-size:13px;color:#1565c0;">
				<strong>&#128197; Post-Dated Entry Enabled</strong> — Dates <strong>${_fmt_pd_date(access.from_date)}</strong> to <strong>${_fmt_pd_date(access.to_date)}</strong> allowed.
			</div>`);
		}
		["entry_date", "entry_time"].forEach(fn => {
			frm.set_df_property(fn, "read_only", 0);
			if (frm.fields_dict[fn] && frm.fields_dict[fn].$wrapper)
				frm.fields_dict[fn].$wrapper.find("input").css({"border-color": "#2490ef", "background": "#f0f7ff"});
		});
	} catch(e) {}
}

// Format "2026-05-14" → "14 May 2026"; "2026-05-14 23:59:59" → "14 May 2026 23:59:59"
function _fmt_pd_date(s) {
	if (!s) return "";
	const parts = String(s).split(" ");
	const date_part = parts[0];
	const time_part = parts[1] || "";
	const d = new Date(date_part + "T00:00:00");
	if (isNaN(d.getTime())) return s;
	const formatted = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
	return time_part ? `${formatted} ${time_part}` : formatted;
}

// ═══════════════════════════════════════════════════════════
//  CUSTOM PRINT BUTTON — Direct PDF download (matches MR/PO pattern)
//  Respects TS Settings.g2_print_mode for G2-only operators:
//    - "Slip Only"      → only Slip available
//    - "Detailed + Slip" (or unset) → both available
// ═══════════════════════════════════════════════════════════

function _ts_add_gate_entry_print_button(frm) {
	// Hide Frappe's default print icon + menu item (same as MR/PO)
	setTimeout(() => {
		frm.page.wrapper.find('.btn[data-original-title="Print"], .btn-print-preview, a[title="Print"]').hide();
		frm.page.menu_btn_group.find('.dropdown-item').each(function() {
			if ($(this).text().trim() === "Print") $(this).hide();
		});
	}, 500);

	// Determine which formats this user can access
	const is_g2_only = frappe.user.has_role("G2 Gate Operator")
		&& !frappe.user.has_role("IT Head")
		&& !frappe.user.has_role("System Manager")
		&& !frappe.user.has_role("Administrator");

	const _open_prompt = (formats) => {
		frappe.prompt({
			fieldtype: "Select",
			label: "Print Format",
			fieldname: "format",
			options: formats.join("\n"),
			default: formats[0],
			reqd: 1,
		}, (values) => {
			const url = `/api/method/frappe.utils.print_format.download_pdf?doctype=TS%20Gate%20Entry&name=${encodeURIComponent(frm.doc.name)}&format=${encodeURIComponent(values.format)}&no_letterhead=0`;
			window.open(url, "_blank");
		}, __("Select Print Format"), __("Download PDF"));
	};

	// Standalone top-level Print PDF button
	frm.add_custom_button(__("🖨 Print PDF"), () => {
		if (is_g2_only) {
			// G2-only operators don't have TS Settings read permission, so
			// frappe.db.get_single_value fails. Use a whitelisted helper
			// that returns ONLY the g2_print_mode value (Lesson 168).
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.api.get_g2_print_mode",
				callback(r) {
					const mode = (r.message && r.message.g2_print_mode) || "Detailed + Slip";
					if (mode === "Slip Only") {
						_open_prompt(["TS Gate Entry Slip"]);
					} else {
						_open_prompt(["TS Gate Entry Detailed", "TS Gate Entry Slip"]);
					}
				},
				error() {
					// Safer fallback for G2: Slip only
					_open_prompt(["TS Gate Entry Slip"]);
				}
			});
		} else {
			// IT Head / System Manager / Administrator: both formats always
			_open_prompt(["TS Gate Entry Detailed", "TS Gate Entry Slip"]);
		}
	});
}
