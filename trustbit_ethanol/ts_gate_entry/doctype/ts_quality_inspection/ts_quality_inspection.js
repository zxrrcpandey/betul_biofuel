// TS Quality Inspection — v2.9.6
// Submittable. Dual naming display. Template-driven param table.
// Total Deduction auto-calc. Cancel + Amend supported via Frappe native UI.
//
// v2.9.6: Actual Deduction layer moved to TS Deduction Suggestion (per-role doctype).
//         QI form is now QI-Inspector-only. On submit, a draft Suggestion is
//         auto-created and Grain Manager is notified.

const QI_LOCK_FIELDS = [
	"item_category", "qc_template", "bag_type", "bag_count",
	"moisture_percent", "impurity_percent", "foreign_matter_percent",
	"starch_content", "actual_gcv", "actual_moisture_percent",
	"grade", "decision", "hold_reason", "remarks"
];

frappe.ui.form.on("TS Quality Inspection", {
	refresh(frm) {
		if (frm.doc.quality_report_no) {
			frm.set_intro(
				`Quality Report No: ${frappe.utils.escape_html(frm.doc.quality_report_no)}`,
				"blue"
			);
		}

		// v2.9.6.1 — pure inline grid for fast Actual Value entry.
		// CSS-based block: hide the row-form expansion + the open-row pencil.
		// This is robust to Frappe's internal API changes.
		if (frm.fields_dict.parameters && frm.fields_dict.parameters.$wrapper) {
			const $w = frm.fields_dict.parameters.$wrapper;
			$w.addClass("ts-qi-inline-only");
			// Inject style once globally (idempotent — second .find returns same element).
			if (!document.getElementById("ts-qi-inline-only-style")) {
				const css = `
.ts-qi-inline-only .grid-form-row { display: none !important; }
.ts-qi-inline-only .btn-open-row { display: none !important; }
.ts-qi-inline-only .grid-row-open { display: none !important; }
.ts-qi-inline-only .row-edit { display: none !important; }
.ts-qi-inline-only .grid-row.grid-row-open { display: none !important; }
				`;
				const style = document.createElement("style");
				style.id = "ts-qi-inline-only-style";
				style.textContent = css;
				document.head.appendChild(style);
			}
			// Also kill the JS-side row-form open methods.
			if (frm.fields_dict.parameters.grid) {
				const grid = frm.fields_dict.parameters.grid;
				grid.no_open = true;
				if (grid.df) grid.df.editable_grid = 1;
				const _killToggle = function () {
					(grid.grid_rows || []).forEach(function (row) {
						if (!row) return;
						row.toggle_view = function () { return this; };
						row.show_form = function () { return this; };
						row.open_form = function () { return this; };
					});
				};
				_killToggle();
				setTimeout(_killToggle, 100);
				const _origRefresh = grid.refresh.bind(grid);
				grid.refresh = function () {
					_origRefresh.apply(this, arguments);
					_killToggle();
				};
			}
		}

		// Submitted state — lock all editable fields
		if (frm.doc.docstatus === 1) {
			QI_LOCK_FIELDS.forEach(function (f) {
				frm.set_df_property(f, "read_only", 1);
			});
		}

		// v2.9.6 — show Suggestion + DS reference buttons if linked records exist
		if (frm.doc.docstatus === 1) {
			frappe.db.get_list("TS Deduction Suggestion", {
				filters: {
					quality_inspection: frm.doc.name,
					docstatus: ["!=", 2]
				},
				fields: ["name"],
				limit: 1
			}).then(rows => {
				if (rows && rows.length) {
					frm.add_custom_button(__("View Deduction Suggestion"), function () {
						frappe.set_route("Form", "TS Deduction Suggestion", rows[0].name);
					});
				}
			});
			frappe.db.get_list("TS Deduction Sheet", {
				filters: {
					quality_inspection: frm.doc.name,
					docstatus: ["!=", 2]
				},
				fields: ["name"],
				limit: 1
			}).then(rows => {
				if (rows && rows.length) {
					frm.add_custom_button(__("View Deduction Sheet"), function () {
						frappe.set_route("Form", "TS Deduction Sheet", rows[0].name);
					});
				}
			});
		}
	},

	setup(frm) {
		frm.set_query("token_number", function () {
			return { filters: { purpose: "Raw Material" } };
		});
		frm.set_query("qc_template", function () {
			let filters = { is_active: 1 };
			if (frm.doc.item_category) {
				filters.category = frm.doc.item_category;
			}
			return { filters: filters };
		});
		frm.set_query("bag_type", function () {
			return { filters: { is_active: 1 } };
		});
	},

	token_number(frm) {
		// v2.9.0.12: clear stale auto-fetched fields when token changes
		frm.set_value("vehicle_number", null);
		frm.set_value("party_name", null);
		frm.set_value("rst_number", null);
		if (!frm.doc.token_number) return;
		frappe.db.get_value("TS Token", frm.doc.token_number,
			["vehicle_number", "custom_rst_number"]
		).then(t => {
			if (t.message) {
				if (t.message.vehicle_number) frm.set_value("vehicle_number", t.message.vehicle_number);
				if (t.message.custom_rst_number) frm.set_value("rst_number", t.message.custom_rst_number);
			}
		});
		frappe.db.get_list("TS Weighbridge Log", {
			filters: { token_number: frm.doc.token_number, docstatus: ["!=", 2] },
			fields: ["rst_number"],
			limit: 1,
			order_by: "creation desc"
		}).then(wbs => {
			if (wbs && wbs.length && wbs[0].rst_number) {
				frm.set_value("rst_number", wbs[0].rst_number);
			}
		});
		frappe.db.get_list("TS Gate Entry", {
			filters: { token_number: frm.doc.token_number, docstatus: 1 },
			fields: ["name", "purchase_order"],
			limit: 1
		}).then(ges => {
			if (ges && ges.length && ges[0].name) {
				const ge = ges[0];
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
				return { message: { name: ge.name, purchase_order: ge.purchase_order } };
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
		if (!frm.doc.qc_template) return;
		if (frm.doc.docstatus !== 0) return;

		const populate = function () {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.doctype.ts_quality_inspection.ts_quality_inspection.populate_template_rows",
				type: "POST",
				args: {
					qi_name: frm.doc.name || null,
					template_name: frm.doc.qc_template
				},
				callback: function (r) {
					if (r.message && r.message.parameters) {
						frm.clear_table("parameters");
						r.message.parameters.forEach(function (row) {
							let child = frm.add_child("parameters");
							Object.assign(child, row);
						});
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
				function () { /* cancel */ }
			);
		} else {
			populate();
		}
	},

	bag_count(frm) {
		calc_totals(frm);
	},

	bag_type(frm) {
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

// v2.9.6: direction-aware rate-based per-row recalc (mirror of server-side _recalc_param_deductions).
// Direction controls which side(s) fire:
//   "Higher is Better" → only shortfall (deduct when actual < min, e.g. Starch)
//   "Lower is Better"  → only excess    (deduct when actual > max, e.g. Moisture)
//   "In Range" / blank → both (deduct outside [min, max])
function _ts_recalc_deduction(row) {
	if (!row) return;
	if (row.parameter_type && row.parameter_type !== "Numeric") return;
	if (!row.actual_value || String(row.actual_value).trim() === "") {
		row.deduction_pct = 0;
		return;
	}
	const actual = parseFloat(row.actual_value);
	if (isNaN(actual)) {
		row.deduction_pct = 0;
		return;
	}
	const min_val = parseFloat(row.min_value);
	const max_val = parseFloat(row.max_value);
	const shortfall = (!isNaN(min_val) && row.min_value !== null && row.min_value !== undefined)
		? Math.max(0, min_val - actual) : 0;
	const excess = (!isNaN(max_val) && row.max_value !== null && row.max_value !== undefined)
		? Math.max(0, actual - max_val) : 0;
	const direction = row.direction || "In Range";
	let counted = 0;
	if (direction === "Higher is Better") {
		counted = shortfall;
	} else if (direction === "Lower is Better") {
		counted = excess;
	} else {
		counted = shortfall + excess;
	}
	const rate = parseFloat(row.deduction_per_unit) || 1.0;
	row.deduction_pct = Math.round(counted * rate * 1000) / 1000;
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
