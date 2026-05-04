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

		// v2.9.10 — Render Related Documents (Connections) panel
		if (!frm.is_new()) {
			_ts_load_connections(frm);
		}

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


// ════════════════════════════════════════════════════════════════════════
//  v2.9.10 — Related Documents (Connections) panel
// ════════════════════════════════════════════════════════════════════════

const _TS_STATUS_COLOR = {
	"Draft": "gray", "Not Submitted": "gray", "Submitted": "green",
	"Cancelled": "red", "Approved": "green", "Rejected": "red",
	"On Hold": "orange",
};

function _ts_status_class(status) {
	if (!status) return "ts-pill-gray";
	if (status.startsWith("Pending")) return "ts-pill-orange";
	if (_TS_STATUS_COLOR[status]) return "ts-pill-" + _TS_STATUS_COLOR[status];
	if (status.indexOf("Approved") !== -1) return "ts-pill-green";
	if (status.indexOf("Reject") !== -1) return "ts-pill-red";
	if (status.indexOf("Cancel") !== -1) return "ts-pill-red";
	return "ts-pill-blue";
}

function _ts_render_connections_skeleton(wrapper) {
	const sk = `
	<div class="ts-conn-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;">
		${[1,2,3,4].map(() => `
		<div class="ts-conn-card" style="border:1px solid var(--border-color);border-radius:6px;padding:10px;background:var(--bg-color);">
			<div style="height:14px;width:50%;background:var(--gray-100);margin-bottom:8px;border-radius:3px;"></div>
			<div style="height:10px;width:80%;background:var(--gray-50);margin-bottom:6px;border-radius:3px;"></div>
			<div style="height:10px;width:65%;background:var(--gray-50);border-radius:3px;"></div>
		</div>
		`).join("")}
	</div>`;
	wrapper.html(sk);
}

function _ts_render_connections(wrapper, sections) {
	if (!sections || sections.length === 0) {
		wrapper.html(`<div style="color:var(--text-muted);font-style:italic;padding:8px;">No related documents.</div>`);
		return;
	}
	const css = `
	<style>
	.ts-conn-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px; padding:6px 0; }
	.ts-conn-card { border:1px solid var(--border-color); border-radius:6px; padding:12px; background:var(--bg-color); min-height:80px; }
	.ts-conn-card-header { font-weight:bold; font-size:13px; color:var(--heading-color); margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid var(--border-color); }
	.ts-conn-card-icon { font-size:16px; margin-right:6px; }
	.ts-conn-row { display:block; padding:5px 0; line-height:1.4; }
	.ts-conn-row + .ts-conn-row { border-top:1px dashed var(--border-color); margin-top:4px; }
	.ts-conn-row a { color:var(--blue-500); text-decoration:none; font-weight:500; word-break:break-all; }
	.ts-conn-row a:hover { text-decoration:underline; }
	.ts-conn-doctype { font-size:10px; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:1px; }
	.ts-conn-cancelled a { text-decoration:line-through; color:var(--text-muted); }
	.ts-conn-pill { display:inline-block; font-size:10px; padding:2px 8px; border-radius:10px; font-weight:600; margin-left:6px; vertical-align:middle; }
	.ts-pill-gray { background:var(--gray-100); color:var(--gray-700); }
	.ts-pill-green { background:var(--green-100); color:var(--green-700); }
	.ts-pill-red { background:var(--red-100); color:var(--red-700); }
	.ts-pill-orange { background:var(--orange-100); color:var(--orange-700); }
	.ts-pill-blue { background:var(--blue-100); color:var(--blue-700); }
	.ts-conn-empty-section { color:var(--text-muted); font-style:italic; font-size:11px; }
	.ts-conn-more { font-size:11px; color:var(--blue-500); margin-top:6px; cursor:pointer; }
	</style>`;

	const cards_html = sections.map(section => {
		const items = section.items || [];
		const rows = items.length === 0
			? `<div class="ts-conn-empty-section">— No documents —</div>`
			: items.map(it => {
				const cancelled_cls = it.docstatus === 2 ? "ts-conn-cancelled" : "";
				const dt_label = frappe.utils.escape_html(it.doctype || "");
				const name_esc = frappe.utils.escape_html(it.name || "");
				const status_esc = frappe.utils.escape_html(it.status || "");
				const url_esc = frappe.utils.escape_html(it.url || "#");
				const pill_cls = _ts_status_class(it.status);
				return `
				<div class="ts-conn-row ${cancelled_cls}">
					<div class="ts-conn-doctype">${dt_label}</div>
					<a href="${url_esc}">${name_esc}</a>
					${status_esc ? `<span class="ts-conn-pill ${pill_cls}">${status_esc}</span>` : ""}
				</div>`;
			}).join("");
		const icon = section.icon ? `<span class="ts-conn-card-icon">${section.icon}</span>` : "";
		const label = frappe.utils.escape_html(section.label || "");
		return `
		<div class="ts-conn-card">
			<div class="ts-conn-card-header">${icon}${label}</div>
			${rows}
		</div>`;
	}).join("");

	wrapper.html(css + `<div class="ts-conn-grid">${cards_html}</div>`);
}

function _ts_load_connections(frm) {
	const wrapper = frm.fields_dict.ts_connections_html
		? frm.fields_dict.ts_connections_html.$wrapper : null;
	if (!wrapper) return;

	_ts_render_connections_skeleton(wrapper);

	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.doctype.ts_deduction_sheet.ts_deduction_sheet.get_connections",
		args: { ds_name: frm.doc.name },
		callback: function (r) {
			if (!r || !r.message) {
				wrapper.html(`<div style="color:var(--red-500);">⚠️ Could not load connections.
					<a href="javascript:void(0)" onclick="cur_frm.refresh()">Retry</a></div>`);
				return;
			}
			if (r.message.error === "no_permission") {
				wrapper.html(`<div style="color:var(--text-muted);">No permission to view related documents.</div>`);
				return;
			}
			_ts_render_connections(wrapper, r.message.sections || []);
		},
		error: function () {
			wrapper.html(`<div style="color:var(--red-500);">⚠️ Could not load connections.
				<a href="javascript:void(0)" onclick="cur_frm.refresh()">Retry</a></div>`);
		},
	});
}
