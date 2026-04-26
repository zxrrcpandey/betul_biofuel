// TS Deduction Sheet — v2.9.0 Day 4
// Submittable. 3-layer display:
//   Layer 1 (System) — read-only, fetched from QI on insert
//   Layer 2 (Actual) — Tilok edits; reason mandatory if differs > 0.01%
//   Layer 3 (Submit) — Accounts Manager / SM only

frappe.ui.form.on("TS Deduction Sheet", {
	refresh(frm) {
		// Header banner: show DS number + linked QI
		if (frm.doc.ds_number || frm.doc.name) {
			let ds = frappe.utils.escape_html(frm.doc.ds_number || frm.doc.name || "");
			let qr_part = frm.doc.quality_inspection
				? ` &middot; QI: <a href="/app/ts-quality-inspection/${encodeURIComponent(frm.doc.quality_inspection)}">${frappe.utils.escape_html(frm.doc.quality_inspection)}</a>`
				: "";
			frm.dashboard.set_headline(`<b>DS Number:</b> ${ds}${qr_part}`);
		}

		// Layered visibility — system fields ALWAYS read-only on form
		frm.set_df_property("system_deduction_pct", "read_only", 1);
		frm.set_df_property("system_deduction_kg", "read_only", 1);

		// Submitted state — lock all editable fields
		if (frm.doc.docstatus === 1) {
			["actual_deduction_pct", "actual_deduction_reason", "decision",
			 "decision_remarks", "invoice_qty", "item_rate", "weight_deduction",
			 "unloading_rate_per_bag", "dhalta_rate_gm_per_qtl", "brokerage_rate_per_mt"
			].forEach(function (f) {
				frm.set_df_property(f, "read_only", 1);
			});
		}

		// Cancelled state
		if (frm.doc.docstatus === 2) {
			frm.dashboard.set_headline_alert(__("This Deduction Sheet has been Cancelled."), "red");
		}

		// Hold indicators for Coal (legacy)
		if (frm.doc.item_category === "Coal") {
			if (frm.doc.hold_on_gcv) {
				frm.dashboard.add_comment(__("GCV below PO threshold - Vehicle Hold recommended"), "red", true);
			}
			if (frm.doc.hold_on_moisture) {
				frm.dashboard.add_comment(__("Moisture above PO threshold - Vehicle Hold recommended"), "red", true);
			}
		}

		// Show delta indicator (system vs actual)
		if (frm.doc.system_deduction_pct !== null && frm.doc.system_deduction_pct !== undefined &&
		    frm.doc.actual_deduction_pct !== null && frm.doc.actual_deduction_pct !== undefined) {
			let delta = flt(frm.doc.actual_deduction_pct) - flt(frm.doc.system_deduction_pct);
			let abs_delta = Math.abs(delta);
			if (abs_delta > 0.01) {
				let color = delta > 0 ? "orange" : "blue";
				frm.dashboard.add_indicator(
					__("Δ vs System: {0}%", [delta.toFixed(3)]),
					color
				);
			} else {
				frm.dashboard.add_indicator(__("Matches System Value"), "green");
			}
		}
	},

	setup(frm) {
		frm.set_query("quality_inspection", function () {
			// Only submitted QIs (docstatus = 1)
			return { filters: { docstatus: 1 } };
		});
	},

	quality_inspection(frm) {
		if (!frm.doc.quality_inspection) return;
		// Fetch QI total_deduction_pct/kg + bag info to populate system fields and references
		frappe.db.get_value("TS Quality Inspection", frm.doc.quality_inspection,
			["total_deduction_pct", "total_deduction_kg", "token_number",
			 "gate_entry", "purchase_order", "item_code", "item_name",
			 "item_category", "bag_count", "bag_weight_kg",
			 "moisture_percent", "impurity_percent",
			 "po_gcv", "actual_gcv", "po_moisture_percent", "actual_moisture_percent"]
		).then(r => {
			if (r.message) {
				frm.set_value("system_deduction_pct", r.message.total_deduction_pct);
				frm.set_value("system_deduction_kg", r.message.total_deduction_kg);
				frm.set_value("token_number", r.message.token_number);
				frm.set_value("gate_entry", r.message.gate_entry);
				frm.set_value("purchase_order", r.message.purchase_order);
				frm.set_value("item_code", r.message.item_code);
				frm.set_value("item_name", r.message.item_name);
				frm.set_value("item_category", r.message.item_category);

				// Legacy quality field carry-over
				if (r.message.item_category === "Grain") {
					frm.set_value("qi_impurity_percent", r.message.impurity_percent);
					frm.set_value("qi_moisture_percent", r.message.moisture_percent);
				} else if (r.message.item_category === "Coal") {
					frm.set_value("qi_po_gcv", r.message.po_gcv);
					frm.set_value("qi_actual_gcv", r.message.actual_gcv);
					frm.set_value("qi_po_moisture", r.message.po_moisture_percent);
					frm.set_value("qi_actual_moisture", r.message.actual_moisture_percent);
					frm.set_value("hold_on_gcv",
						r.message.actual_gcv && r.message.po_gcv && r.message.actual_gcv < r.message.po_gcv ? 1 : 0);
					frm.set_value("hold_on_moisture",
						r.message.actual_moisture_percent && r.message.po_moisture_percent && r.message.actual_moisture_percent > r.message.po_moisture_percent ? 1 : 0);
				}

				// Pre-fill actual if blank (Layer 2 default = system value)
				if (frm.doc.actual_deduction_pct === null || frm.doc.actual_deduction_pct === undefined) {
					frm.set_value("actual_deduction_pct", r.message.total_deduction_pct);
				}

				// Supplier name
				if (r.message.purchase_order) {
					frappe.db.get_value("Purchase Order", r.message.purchase_order, "supplier_name").then(s => {
						if (s.message) {
							frm.set_value("supplier_name", s.message.supplier_name);
						}
					});
				}
			}
		});
	},

	actual_deduction_pct(frm) {
		// Recalc actual_deduction_kg via QI bag info
		if (frm.doc.quality_inspection) {
			frappe.db.get_value("TS Quality Inspection", frm.doc.quality_inspection,
				["bag_count", "bag_weight_kg"]).then(r => {
				if (r.message) {
					let pct = flt(frm.doc.actual_deduction_pct || 0);
					let count = flt(r.message.bag_count || 0);
					let weight = flt(r.message.bag_weight_kg || 0);
					let kg = (pct / 100.0) * count * weight;
					frm.set_value("actual_deduction_kg", flt(kg, 3));
				}
			});
		}

		// Highlight reason field if differs > 0.01%
		let delta = Math.abs(flt(frm.doc.actual_deduction_pct || 0) - flt(frm.doc.system_deduction_pct || 0));
		if (delta > 0.01) {
			frm.set_df_property("actual_deduction_reason", "reqd", 1);
		} else {
			frm.set_df_property("actual_deduction_reason", "reqd", 0);
		}
	},

	calculate_deductions_button(frm) {
		if (frm.doc.docstatus !== 0) {
			frappe.msgprint(__("Cannot calculate on submitted/cancelled DS."));
			return;
		}
		if (!frm.doc.invoice_qty || !frm.doc.item_rate) {
			frappe.msgprint(__("Please enter Invoice Qty and Item Rate first"));
			return;
		}
		frm.call("calculate_deductions").then(r => {
			if (r.message) {
				frm.reload_doc();
				frappe.show_alert({
					message: __("Legacy deductions calculated. Total: ₹{0}", [r.message.total_deduction]),
					indicator: "green"
				}, 4);
			}
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
