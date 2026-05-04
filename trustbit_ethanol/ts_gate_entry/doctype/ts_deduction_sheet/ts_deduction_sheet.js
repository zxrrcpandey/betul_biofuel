// TS Deduction Sheet — v2.9.0 Day 4 / v2.9.5 snapshot model
// Submittable. 3-layer display:
//   Layer 1 (System) — read-only, fetched from QI on insert
//   Layer 2 (Actual) — v2.9.5: snapshot from parent QI, unconditionally read-only
//   Layer 3 (Submit) — Accounts Manager / SM only
//
// v2.9.5: actual_deduction_* fields are ALWAYS read-only here. Editing happens
// on the source QI before submit. DS auto-creates from QI submit.

frappe.ui.form.on("TS Deduction Sheet", {
	refresh(frm) {
		// v2.9.5.1 — use frm.set_intro (NOT set_headline — Lesson 163: dashboard wipes on refresh)
		if (frm.doc.ds_number || frm.doc.name) {
			let ds = frappe.utils.escape_html(frm.doc.ds_number || frm.doc.name || "");
			let qi_part = frm.doc.quality_inspection
				? ` · QI: ${frappe.utils.escape_html(frm.doc.quality_inspection)}`
				: "";
			frm.set_intro(`DS Number: ${ds}${qi_part}`, "blue");
		}

		// v2.9.11 — Connections panel render handled by shared
		// /assets/trustbit_ethanol/js/ts_connections.js (auto-installs).

		// v2.9.8 — derived header fields are always RO on form
		[
			"posting_date", "grn_reference", "rate_per_quintal",
			"net_qty_kg", "net_qty_quintal", "bag_count", "grn_amount"
		].forEach(function (f) {
			frm.set_df_property(f, "read_only", 1);
		});

		// v2.9.8.3 — warning banner when GRN not yet linked on draft DS
		if (frm.doc.docstatus === 0 && !frm.doc.grn_reference && frm.doc.token_number) {
			frm.dashboard.add_indicator(
				__("⚠ GRN not linked yet — once a Purchase Receipt is created for this Token, click '🔄 Refresh from Sources' to pull the latest data."),
				"orange"
			);
		}

		// v2.9.8.4 — manual refresh button. Calls server-side endpoint that
		// CLEARS all derived fields first (defeating the "if not self.X" guards)
		// then re-fetches from PO / PR / QI / Suggestion via validate().
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("🔄 Refresh from Sources"), function () {
				frappe.confirm(
					__("This will clear the Deduction Details table and re-fetch all values from PO / PR / QI / Suggestion. Manual line overrides will be lost. Continue?"),
					function () {
						frappe.show_alert({
							message: __("Refreshing from sources..."),
							indicator: "blue"
						}, 3);
						frappe.call({
							method: "trustbit_ethanol.ts_gate_entry.doctype.ts_deduction_sheet.ts_deduction_sheet.force_refresh_deduction_sheet",
							type: "POST",
							args: { name: frm.doc.name },
							freeze: true,
							freeze_message: __("Refreshing from sources..."),
							callback: function (r) {
								if (r && r.message && r.message.ok) {
									const m = r.message;
									frappe.show_alert({
										message: __("✓ Refreshed. GRN: {0} · Total: ₹{1} · Lines: {2}",
											[m.grn_reference || "(none)", m.total_deduction.toFixed(2), m.line_count]),
										indicator: "green"
									}, 7);
									frm.reload_doc();
								}
							},
							error: function (err) {
								frappe.show_alert({
									message: __("Refresh failed — see console"),
									indicator: "red"
								}, 8);
								console.error("[v2.9.8.4] force-refresh failed:", err);
							}
						});
					}
				);
			}).addClass("btn-default");
		}

		// v2.9.8.1 — auto-refresh when GRN appears upstream after DS was drafted.
		// Detect: draft + grn_reference empty + token set → probe for PR.
		// If a PR now exists for the token, trigger save() to fetch + recalc.
		// Guard via frm.__ts_v298_auto_refreshed so we only fire once per session.
		if (
			frm.doc.docstatus === 0 &&
			!frm.doc.grn_reference &&
			frm.doc.token_number &&
			!frm.__ts_v298_auto_refreshed
		) {
			frm.__ts_v298_auto_refreshed = true;
			frappe.db.get_list("Purchase Receipt", {
				filters: { ts_token: frm.doc.token_number, docstatus: ["!=", 2] },
				fields: ["name"],
				limit: 1
			}).then(prs => {
				if (prs && prs.length && !frm.is_new() && !frm.is_dirty()) {
					frappe.show_alert({
						message: __("GRN found ({0}) — refreshing deduction values…", [prs[0].name]),
						indicator: "blue"
					}, 4);
					frm.save().then(() => {
						frappe.show_alert({
							message: __("Deduction Sheet refreshed with GRN data."),
							indicator: "green"
						}, 4);
					}).catch(err => {
						// Swallow auto-save errors — user can still save manually
						console.warn("[v2.9.8.1] auto-refresh save failed:", err);
					});
				}
			}).catch(() => {
				// Probe failed (network / permission) — silent fall-back
			});
		}

		// v2.9.8 — by default, line.rate is locked to its source. Allow editing
		// only when CEO has flipped ts_ds_full_override_enabled. is_overridden +
		// override_reason + actual_amount remain editable in the standard path.
		frappe.call({
			method: "frappe.client.get_value",
			args: {doctype: "TS Settings", filters: {}, fieldname: "ts_ds_full_override_enabled"},
			callback(r) {
				const full_override = r && r.message
					&& String(r.message.ts_ds_full_override_enabled || "0") === "1";
				const grid = frm.fields_dict.deductions && frm.fields_dict.deductions.grid;
				if (!grid) return;
				if (frm.doc.docstatus === 0 && !full_override) {
					grid.update_docfield_property("rate", "read_only", 1);
					grid.update_docfield_property("base_value", "read_only", 1);
					grid.update_docfield_property("calculated_amount", "read_only", 1);
				}
				grid.refresh();
			}
		});

		// v2.9.5 — Layer-1 system fields + Layer-2 actual_deduction_* are
		// UNCONDITIONALLY read-only on the form (snapshots from QI).
		[
			"system_deduction_pct", "system_deduction_kg",
			"actual_deduction_pct", "actual_deduction_kg", "actual_deduction_reason",
			"filled_by", "filled_at"
		].forEach(function (f) {
			frm.set_df_property(f, "read_only", 1);
		});

		// v2.9.5 — informational banner pointing to source QI for editing
		if (frm.doc.docstatus === 0 && frm.doc.quality_inspection) {
			let qi_link = `/app/ts-quality-inspection/${encodeURIComponent(frm.doc.quality_inspection)}`;
			frm.dashboard.add_indicator(
				__("Actual values are snapshots from QI — edit on QI before submit"),
				"blue"
			);
		}

		// Submitted state — lock other editable legacy fields too
		if (frm.doc.docstatus === 1) {
			["decision", "decision_remarks",
			 "invoice_qty", "item_rate", "weight_deduction",
			 "unloading_rate_per_bag", "dhalta_rate_gm_per_qtl", "brokerage_rate_per_mt"
			].forEach(function (f) {
				frm.set_df_property(f, "read_only", 1);
			});
		}

		// v2.9.5.1 — Cancelled state via add_indicator (NOT set_headline_alert — Lesson 163)
		if (frm.doc.docstatus === 2) {
			frm.dashboard.add_indicator(__("Cancelled"), "red");
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

		// v2.9.8.2 — show delta indicator with the Grain Manager's name (filled_by user)
		if (frm.doc.system_deduction_pct !== null && frm.doc.system_deduction_pct !== undefined &&
		    frm.doc.actual_deduction_pct !== null && frm.doc.actual_deduction_pct !== undefined) {
			const delta = flt(frm.doc.actual_deduction_pct) - flt(frm.doc.system_deduction_pct);
			const abs_delta = Math.abs(delta);
			const _render_indicator = (full_name) => {
				const who = full_name ? `${full_name}` : __("Grain Manager");
				if (abs_delta > 0.01) {
					const color = delta > 0 ? "orange" : "blue";
					frm.dashboard.add_indicator(
						__("{0}: Δ {1}% vs System", [who, delta.toFixed(3)]),
						color
					);
				} else {
					frm.dashboard.add_indicator(
						__("{0}: matches System value", [who]),
						"green"
					);
				}
			};
			if (frm.doc.filled_by) {
				frappe.db.get_value("User", frm.doc.filled_by, "full_name").then(r => {
					_render_indicator(r && r.message && r.message.full_name);
				});
			} else {
				_render_indicator(null);
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
		// v2.9.5 — manual DS create still allowed (legacy path / admin override).
		// Snapshots Layer 1 + Layer 2 from the QI on link change.
		if (!frm.doc.quality_inspection) return;
		frappe.db.get_value("TS Quality Inspection", frm.doc.quality_inspection,
			["total_deduction_pct", "total_deduction_kg", "token_number",
			 "gate_entry", "purchase_order", "item_code", "item_name",
			 "item_category", "bag_count", "bag_weight_kg",
			 "moisture_percent", "impurity_percent",
			 "po_gcv", "actual_gcv", "po_moisture_percent", "actual_moisture_percent",
			 "actual_deduction_pct", "actual_deduction_kg", "actual_deduction_reason"]
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

				// v2.9.5 — snapshot Layer-2 from QI (or fall back to system)
				if (r.message.actual_deduction_pct !== null && r.message.actual_deduction_pct !== undefined) {
					frm.set_value("actual_deduction_pct", r.message.actual_deduction_pct);
					frm.set_value("actual_deduction_kg", r.message.actual_deduction_kg);
					frm.set_value("actual_deduction_reason", r.message.actual_deduction_reason);
				} else if (frm.doc.actual_deduction_pct === null || frm.doc.actual_deduction_pct === undefined) {
					// Legacy QI fall-back — use system value
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

// v2.9.11 — Connections panel render moved to shared
// /assets/trustbit_ethanol/js/ts_connections.js (auto-installs refresh hook).
