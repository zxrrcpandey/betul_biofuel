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

		// Disable tare weight if unloading not complete (Non-RM weighing skips unloading)
		let is_non_rm_weighing = frm.doc.material_flow === "Non-Raw Material";
		if (is_non_rm_weighing) {
			// Non-RM weighing: tare allowed directly after gross (no unloading needed)
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
	}
});
