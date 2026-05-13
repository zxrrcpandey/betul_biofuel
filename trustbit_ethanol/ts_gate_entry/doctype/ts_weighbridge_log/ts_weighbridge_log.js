// v2.8.2: RST uniqueness check debounce timer (module-scoped so we only
// hit the server once per typing pause, not on every keystroke).
let _ts_rst_debounce_timer = null;

frappe.ui.form.on("TS Weighbridge Log", {
	refresh(frm) {
		// Color-code status
		let color = {
			"Gross Recorded": "blue",
			"Awaiting Unloading": "orange",
			"Awaiting Tare Weight": "yellow",
			"Completed": "green"
		};
		if (frm.doc.status) {
			frm.page.set_indicator(__(frm.doc.status), color[frm.doc.status] || "grey");
		}

		// Lock gross_weight after initial save (prevent editing after first entry)
		if (!frm.is_new() && frm.doc.gross_weight) {
			frm.set_df_property("gross_weight", "read_only", 1);
		}

		// Lock tare_weight after it has been recorded
		if (frm.doc.tare_weight) {
			frm.set_df_property("tare_weight", "read_only", 1);
		}

		// v2.8.2: RST Number UX — numeric keypad, mobile input hints,
		// and read-only once the token has reached Completed (PR likely exists).
		if (frm.fields_dict.rst_number && frm.fields_dict.rst_number.$input) {
			frm.fields_dict.rst_number.$input
				.attr("inputmode", "numeric")
				.attr("pattern", "[0-9]*");
		}
		// If Completed (weighbridge flow finished), downstream PR may reference
		// this RST — lock to avoid silent propagation drift. CTO/IT Head can still
		// amend via server-side tamper-guard logic.
		if (!frm.is_new() && frm.doc.rst_number && frm.doc.status === "Completed") {
			frm.set_df_property("rst_number", "read_only", 1);
		}

		// v2.8 flow: Gross → Tare direct (no unloading gate).
		// Legacy flow (flag off): require unloading_complete before Tare.
		// v2.9.16.6: use role-scoped helper (Lesson 168) — direct get_single_value
		// triggers "No permission for TS Settings" for Weighbridge Operator role.
		let is_non_rm_weighing = frm.doc.material_flow === "Non-Raw Material";
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.api.get_flow_v28_flag",
			callback: function (r) {
				const v28_enabled = (r.message || {}).enabled;
				if (v28_enabled || is_non_rm_weighing) {
					// Tare allowed directly after gross
					if (!frm.doc.tare_weight && frm.doc.gross_weight) {
						frm.set_df_property("tare_weight", "read_only", 0);
						frm.set_df_property("tare_weight", "description", "");
					}
				} else if (!frm.doc.unloading_complete) {
					frm.set_df_property("tare_weight", "read_only", 1);
					frm.set_df_property("tare_weight", "description",
						"Tare weight can only be entered after unloading is confirmed complete");
				} else if (!frm.doc.tare_weight) {
					frm.set_df_property("tare_weight", "read_only", 0);
					frm.set_df_property("tare_weight", "description", "");
				}
			}
		});

		// ── Fetch Weight Buttons ──
		_add_fetch_weight_buttons(frm);
	},

	setup(frm) {
		// Show tokens at PO Linked stage: Raw Material OR Non-RM with requires_weighing
		frm.set_query("token_number", function () {
			return {
				query: "trustbit_ethanol.ts_gate_entry.api.get_weighbridge_tokens"
			};
		});
	},

	token_number(frm) {
		if (frm.doc.token_number) {
			frappe.db.get_value("TS Gate Entry",
				{ token_number: frm.doc.token_number, docstatus: 1 },
				["name", "purchase_order", "material_flow"]
			).then(r => {
				if (r.message) {
					frm.set_value("gate_entry", r.message.name);
					frm.set_value("purchase_order", r.message.purchase_order);
					frm.set_value("material_flow", r.message.material_flow);
				}
			});
		}
	},

	rst_number(frm) {
		// v2.8.2: strip non-digit characters + debounced uniqueness check.
		if (!frm.doc.rst_number) {
			return;
		}
		const cleaned = String(frm.doc.rst_number).replace(/[^0-9]/g, "");
		if (cleaned !== frm.doc.rst_number) {
			// Recursive set is guarded by the (cleaned !== current) check above.
			frm.set_value("rst_number", cleaned);
			return;
		}
		if (!cleaned) {
			return;
		}

		// Debounced live uniqueness check (500ms pause after last keystroke).
		if (_ts_rst_debounce_timer) {
			clearTimeout(_ts_rst_debounce_timer);
		}
		_ts_rst_debounce_timer = setTimeout(() => {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.rst_api.check_rst_unique",
				args: {
					rst_number: cleaned,
					current_log: frm.doc.name || null,
				},
				callback(r) {
					if (r && r.message && r.message.unique === false) {
						frappe.show_alert({
							message: __("RST Number {0} already used on log {1} (Token {2})",
								[cleaned, r.message.conflict_log, r.message.conflict_token || "-"]),
							indicator: "red",
						}, 7);
					}
				},
			});
		}, 500);
	},
});


function _add_fetch_weight_buttons(frm) {
	// Remove old buttons
	$(frm.page.wrapper).find(".ts-fetch-weight-btn").remove();

	// Fetch Gross Weight button — show when gross_weight is editable (not yet recorded)
	if (!frm.doc.gross_weight || frm.is_new()) {
		const $gross_field = $(frm.fields_dict.gross_weight?.wrapper);
		if ($gross_field.length) {
			$('<button class="btn btn-xs btn-primary ts-fetch-weight-btn" style="margin-top:5px;">'
				+ '<i class="fa fa-download"></i> Fetch Gross Weight from Weighbridge'
				+ '</button>')
				.appendTo($gross_field)
				.on("click", function() {
					_fetch_weight(frm, "gross_weight", $(this));
				});
		}
	}

	// Fetch Tare Weight button — show when tare_weight is editable
	const tare_readonly = frm.fields_dict.tare_weight?.df?.read_only;
	if (!frm.doc.tare_weight && !tare_readonly) {
		const $tare_field = $(frm.fields_dict.tare_weight?.wrapper);
		if ($tare_field.length) {
			$('<button class="btn btn-xs btn-success ts-fetch-weight-btn" style="margin-top:5px;">'
				+ '<i class="fa fa-download"></i> Fetch Tare Weight from Weighbridge'
				+ '</button>')
				.appendTo($tare_field)
				.on("click", function() {
					_fetch_weight(frm, "tare_weight", $(this));
				});
		}
	}
}

function _fetch_weight(frm, target_field, $btn) {
	const label = target_field === "gross_weight" ? "Gross" : "Tare";

	$btn.prop("disabled", true).html('<i class="fa fa-spinner fa-spin"></i> Reading...');

	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.api.fetch_weighbridge_weight",
		freeze: false,
		callback(r) {
			if (r.message && r.message.weight_kg !== undefined) {
				const weight = r.message.weight_kg;

				if (weight <= 0) {
					frappe.msgprint({
						title: __("Zero Weight"),
						message: __("Weighbridge returned 0 KG. Please ensure the vehicle is on the weighbridge and try again."),
						indicator: "orange"
					});
					$btn.prop("disabled", false).html('<i class="fa fa-download"></i> Fetch ' + label + ' Weight from Weighbridge');
					return;
				}

				frm.set_value(target_field, weight);
				frappe.show_alert({
					message: __(label + " Weight: {0} KG fetched from weighbridge", [weight.toLocaleString()]),
					indicator: "green"
				}, 5);

				$btn.removeClass("btn-primary btn-success").addClass("btn-default")
					.html('<i class="fa fa-check"></i> ' + weight.toLocaleString() + ' KG');
			}
		},
		error() {
			$btn.prop("disabled", false).html('<i class="fa fa-download"></i> Fetch ' + label + ' Weight from Weighbridge');
		}
	});
}
