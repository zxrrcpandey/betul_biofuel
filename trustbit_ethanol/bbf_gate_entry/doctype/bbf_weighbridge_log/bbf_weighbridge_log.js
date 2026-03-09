frappe.ui.form.on("BBF Weighbridge Log", {
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

		// Disable tare weight if unloading not complete
		if (!frm.doc.unloading_complete) {
			frm.set_df_property("tare_weight", "read_only", 1);
			frm.set_df_property("tare_weight", "description",
				"Tare weight can only be entered after unloading is confirmed complete");
		} else if (!frm.doc.tare_weight) {
			frm.set_df_property("tare_weight", "read_only", 0);
			frm.set_df_property("tare_weight", "description", "");
		}
	},

	setup(frm) {
		// Only show Raw Material tokens that are at PO Linked stage (ready for weighbridge)
		frm.set_query("token_number", function () {
			return {
				filters: {
					status: ["in", ["PO Linked"]],
					purpose: "Raw Material"
				}
			};
		});
	},

	token_number(frm) {
		if (frm.doc.token_number) {
			frappe.db.get_value("BBF Gate Entry",
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
