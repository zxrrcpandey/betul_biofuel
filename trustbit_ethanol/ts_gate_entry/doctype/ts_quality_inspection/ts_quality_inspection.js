// TS Quality Inspection — v2.9.0 Day 4
// Submittable. Dual naming display. Template-driven param table.
// Total Deduction auto-calc. Cancel + Amend supported via Frappe native UI.

frappe.ui.form.on("TS Quality Inspection", {
	refresh(frm) {
		// Title hint: show Quality Report No prominently in the header
		if (frm.doc.quality_report_no) {
			frm.dashboard.set_headline(
				`<b>Quality Report No:</b> ${frappe.utils.escape_html(frm.doc.quality_report_no)}`
			);
		}

		// Submitted state — lock all editable fields
		if (frm.doc.docstatus === 1) {
			frm.set_df_property("item_category", "read_only", 1);
			frm.set_df_property("qc_template", "read_only", 1);
			frm.set_df_property("bag_type", "read_only", 1);
			frm.set_df_property("bag_count", "read_only", 1);
			frm.set_df_property("moisture_percent", "read_only", 1);
			frm.set_df_property("impurity_percent", "read_only", 1);
			frm.set_df_property("foreign_matter_percent", "read_only", 1);
			frm.set_df_property("starch_content", "read_only", 1);
			frm.set_df_property("actual_gcv", "read_only", 1);
			frm.set_df_property("actual_moisture_percent", "read_only", 1);
			frm.set_df_property("grade", "read_only", 1);
			frm.set_df_property("decision", "read_only", 1);
			frm.set_df_property("hold_reason", "read_only", 1);
			frm.set_df_property("remarks", "read_only", 1);
		}

		// "Create Deduction Sheet" button — only on submitted + decision Accept and no DS yet
		if (frm.doc.docstatus === 1 && frm.doc.decision === "Accept") {
			frappe.db.count("TS Deduction Sheet", {
				quality_inspection: frm.doc.name,
				docstatus: ["!=", 2]
			}).then(count => {
				if (count === 0) {
					frm.add_custom_button(__("Create Deduction Sheet"), function () {
						frappe.new_doc("TS Deduction Sheet", {
							quality_inspection: frm.doc.name,
							token_number: frm.doc.token_number
						});
					}).addClass("btn-primary");
				}
			});
		}
	},

	setup(frm) {
		// Filter token dropdown — Gross Weighed onwards (when QC gate ON, broader)
		frm.set_query("token_number", function () {
			return {
				filters: {
					purpose: "Raw Material"
				}
			};
		});

		// Filter QC Template dropdown to Active templates matching item_category
		frm.set_query("qc_template", function () {
			let filters = { is_active: 1 };
			if (frm.doc.item_category) {
				filters.category = frm.doc.item_category;
			}
			return { filters: filters };
		});

		// Filter bag_type to Active bag types only
		frm.set_query("bag_type", function () {
			return { filters: { is_active: 1 } };
		});
	},

	token_number(frm) {
		if (!frm.doc.token_number) return;
		// v2.9.0.9: live auto-fetch vehicle_number + rst_number from Token (before save)
		frappe.db.get_value("TS Token", frm.doc.token_number,
			["vehicle_number", "custom_rst_number"]
		).then(t => {
			if (t.message) {
				if (t.message.vehicle_number) frm.set_value("vehicle_number", t.message.vehicle_number);
				if (t.message.custom_rst_number) frm.set_value("rst_number", t.message.custom_rst_number);
			}
		});
		// v2.9.0.11: RST from any Weighbridge Log (draft or submitted) — drop docstatus filter
		// since on Gross-Weighed tokens the WB Log is still docstatus=0
		frappe.db.get_list("TS Weighbridge Log", {
			filters: { token_number: frm.doc.token_number, docstatus: ["!=", 2] },
			fields: ["rst_number"],
			limit: 1,
			order_by: "creation desc"
		}).then(wbs => {
			if (wbs && wbs.length && wbs[0].rst_number && !frm.doc.rst_number) {
				frm.set_value("rst_number", wbs[0].rst_number);
			}
		});
		// Auto-fetch item info from Gate Entry + party_name from PO (use get_list for explicit dict response)
		frappe.db.get_list("TS Gate Entry", {
			filters: { token_number: frm.doc.token_number, docstatus: 1 },
			fields: ["name", "purchase_order"],
			limit: 1
		}).then(ges => {
			if (ges && ges.length && ges[0].name) {
				const ge = ges[0];
				// v2.9.0.11 FIX: use get_list to guarantee dict return
				if (ge.purchase_order) {
					frappe.db.get_list("Purchase Order", {
						filters: { name: ge.purchase_order },
						fields: ["supplier_name", "supplier"],
						limit: 1
					}).then(pos => {
						if (pos && pos.length) {
							const sup = pos[0].supplier_name || pos[0].supplier;
							if (sup) frm.set_value("party_name", sup);
						}
					});
				}
				// alias for downstream item-fetch (still uses ge.name)
				const r = { message: { name: ge.name, purchase_order: ge.purchase_order } };
				return r;
			}
			return null;
		}).then(r => {
			if (r && r.message && r.message.name) {
				frappe.db.get_list("TS Gate Entry Item", {
					filters: { parent: r.message.name },
					fields: ["item_code", "item_name"],
					limit: 1
				}).then(items => {
					if (items && items.length) {
						frm.set_value("item_code", items[0].item_code);
						frm.set_value("item_name", items[0].item_name);
						// Auto-detect category from item_group
						frappe.db.get_value("Item", items[0].item_code, "item_group").then(ig => {
							if (ig.message && ig.message.item_group) {
								let group = ig.message.item_group.toLowerCase();
								if (group.includes("grain") || group.includes("maize") || group.includes("rice") || group.includes("corn")) {
									frm.set_value("item_category", "Grain");
								} else if (group.includes("coal")) {
									frm.set_value("item_category", "Coal");
								}
							}
						});
					}
				});
			}
		});
	},

	qc_template(frm) {
		// On QC Template change: populate parameters child table.
		// If rows already exist, confirm before clearing.
		if (!frm.doc.qc_template) return;
		if (frm.doc.docstatus !== 0) return;  // submitted/cancelled — never repopulate

		const populate = function () {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.doctype.ts_quality_inspection.ts_quality_inspection.populate_template_rows",
				type: "POST",  // Lesson 175 — server endpoint declares methods=["POST"]
				args: {
					qi_name: frm.doc.name || null,
					template_name: frm.doc.qc_template,
					qi_doc: frm.doc
				},
				callback: function (r) {
					if (r.message && r.message.parameters) {
						frm.clear_table("parameters");
						r.message.parameters.forEach(function (row) {
							let child = frm.add_child("parameters");
							Object.assign(child, row);
						});
						// Auto-fetch bag_type from template if user hasn't picked one yet
						if (r.message.bag_type && !frm.doc.bag_type) {
							frm.set_value("bag_type", r.message.bag_type);
						}
						frm.refresh_field("parameters");
						calc_totals(frm);
						frappe.show_alert({
							message: __("Loaded {0} parameter(s) from template", [r.message.parameters.length]),
							indicator: "green"
						}, 3);
					}
				}
			});
		};

		const has_rows = (frm.doc.parameters || []).length > 0;
		if (has_rows) {
			frappe.confirm(
				__("Replacing parameters will discard {0} existing row(s). Continue?", [frm.doc.parameters.length]),
				populate,
				function () { /* cancel — preserve existing rows */ }
			);
		} else {
			populate();
		}
	},

	bag_count(frm) {
		calc_totals(frm);
	},

	bag_type(frm) {
		// fetch_from on bag_weight_kg pulls from bag_type.standard_weight_kg automatically.
		// Then recalc.
		setTimeout(() => calc_totals(frm), 100);
	},

	actual_gcv(frm) {
		calculate_coal_variances(frm);
	},

	actual_moisture_percent(frm) {
		calculate_coal_variances(frm);
	}
});

// Child table — recompute totals when deduction_pct changes
frappe.ui.form.on("TS QI Parameter Result", {
	// v2.9.0.8: actual_value entry triggers rate-based recalc
	actual_value(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		_ts_recalc_deduction(row);
		frm.refresh_field("parameters");
		calc_totals(frm);
	},
	deduction_pct(frm) {
		calc_totals(frm);
	},
	parameters_remove(frm) {
		calc_totals(frm);
	}
});

// v2.9.0.8: rate-based per-row recalc (Option B).
// Mirror of server-side _recalc_param_deductions in ts_quality_inspection.py.
function _ts_recalc_deduction(row) {
	if (!row) return;
	if (row.parameter_type && row.parameter_type !== "Numeric") {
		return;  // Only Numeric rows auto-calc
	}
	if (!row.actual_value || String(row.actual_value).trim() === "") {
		row.deduction_pct = 0;
		return;
	}
	const actual = parseFloat(row.actual_value);
	const max_val = parseFloat(row.max_value);
	if (isNaN(actual)) {
		row.deduction_pct = 0;
		return;
	}
	const max_safe = isNaN(max_val) ? 0 : max_val;
	const excess = Math.max(0, actual - max_safe);
	const rate = parseFloat(row.deduction_per_unit) || 1.0;  // default 1.0 if not set
	row.deduction_pct = Math.round(excess * rate * 1000) / 1000;  // 3 decimal precision
}

function calc_totals(frm) {
	let total_pct = 0;
	(frm.doc.parameters || []).forEach(function (row) {
		total_pct += flt(row.deduction_pct || 0);
	});
	frm.set_value("total_deduction_pct", flt(total_pct, 3));
	let total_kg = (total_pct / 100.0) * flt(frm.doc.bag_count || 0) * flt(frm.doc.bag_weight_kg || 0);
	frm.set_value("total_deduction_kg", flt(total_kg, 3));
}

function calculate_coal_variances(frm) {
	if (frm.doc.item_category !== "Coal") return;
	if (frm.doc.po_gcv && frm.doc.actual_gcv) {
		let variance = ((frm.doc.actual_gcv - frm.doc.po_gcv) / frm.doc.po_gcv) * 100;
		frm.set_value("gcv_variance_percent", flt(variance, 2));
	}
	if (frm.doc.po_moisture_percent && frm.doc.actual_moisture_percent) {
		let variance = frm.doc.actual_moisture_percent - frm.doc.po_moisture_percent;
		frm.set_value("moisture_variance_percent", flt(variance, 3));
	}
}
