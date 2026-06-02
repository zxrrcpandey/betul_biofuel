// Copyright (c) 2026, Trustbit Technologies and contributors
// For license information, please see license.txt
//
// TS Production Entry form — v2.15.0
//   - "Pull Standard from BOM"  (Draft): data-driven materials + by-products
//   - "Submit Production Entry" (Draft): within tolerance auto-posts, breach -> CEO
//   - "Approve" / "Reject"      (Pending CEO + CEO/SM): variance approval
//   - Status intro banner (frm.set_intro — re-applied each refresh, NOT set_headline)
//   - Locks editable fields once the entry leaves Draft

const TS_PROD_VERSION = "v2.15.0-2026-06-01-r2";

frappe.ui.form.on("TS Production Entry", {
	refresh(frm) {
		frm.__ts_prod_version = TS_PROD_VERSION;
		_ts_prod_lock_fields(frm);
		_ts_prod_banner(frm);
		_ts_prod_buttons(frm);
	},

	bom(frm) {
		// New BOM picked on a draft — invite the user to pull the standard.
		if (frm.is_new() || frm.doc.ts_variance_status === "Draft") {
			frm.dashboard.clear_comment();
		}
	},

	actual_produced_qty(frm) {
		// Recompute happens server-side on save; nudge the user to save to see variance.
		if (frm.doc.ts_variance_status === "Draft" && !frm.is_new()) {
			frm.dirty();
		}
	},
});

function _ts_prod_is_ceo(frm) {
	const roles = frappe.user_roles || [];
	return roles.includes("CEO") || roles.includes("System Manager") || frappe.session.user === "Administrator";
}

function _ts_prod_lock_fields(frm) {
	// Once out of Draft, lock the operator-editable fields (server also enforces).
	const locked = frm.doc.ts_variance_status && frm.doc.ts_variance_status !== "Draft";
	const fields = ["bom", "standard_batches", "posting_date", "shift", "batch_ref", "work_order", "actual_produced_qty"];
	fields.forEach((f) => frm.set_df_property(f, "read_only", locked ? 1 : 0));
	["materials", "byproducts"].forEach((g) => {
		const grid = frm.fields_dict[g] && frm.fields_dict[g].grid;
		if (grid) {
			grid.cannot_add_rows = !!locked;
			grid.set_column_disp && grid.toggle_enable && grid.toggle_enable("actual_qty", !locked);
		}
	});
}

function _ts_prod_banner(frm) {
	if (frm.is_new()) return;
	const s = frm.doc.ts_variance_status;
	const mv = flt(frm.doc.material_variance_pct);
	const pv = flt(frm.doc.produced_variance_pct);
	if (s === "Pending CEO") {
		frm.set_intro(
			__("Variance breach (material {0}% / produced {1}%) — pending CEO approval. Stock has NOT been posted.",
				[mv.toFixed(2), pv.toFixed(2)]),
			"orange"
		);
	} else if (s === "Posted") {
		frm.set_intro(
			__("Posted. Manufacture Stock Entry {0} created (Ethanol + by-products).",
				[frm.doc.linked_stock_entry || ""]),
			"green"
		);
	} else if (s === "Rejected") {
		frm.set_intro(__("Rejected by CEO. {0}", [frm.doc.rejection_reason || ""]), "red");
	} else if (s === "Cancelled") {
		frm.set_intro(__("Cancelled — the linked Manufacture Stock Entry was cancelled."), "red");
	} else if (s === "Draft" && cint(frm.doc.variance_breach)) {
		frm.set_intro(
			__("This run breaches the variance tolerance (material {0}% / produced {1}%). Submitting will route it to the CEO.",
				[mv.toFixed(2), pv.toFixed(2)]),
			"orange"
		);
	} else {
		frm.set_intro("");
	}
}

function _ts_prod_buttons(frm) {
	if (frm.is_new()) return;
	const s = frm.doc.ts_variance_status;

	if (s === "Draft" && frm.doc.bom) {
		frm.add_custom_button(__("Pull Standard from BOM"), () => _ts_prod_pull_standard(frm));
	}

	if (s === "Draft") {
		frm.add_custom_button(__("Submit Production Entry"), () => {
			frappe.confirm(
				__("Submit this production run? Within tolerance it will post a Manufacture Stock Entry; a variance breach will be routed to the CEO."),
				// Save any unsaved edits FIRST so the server posts the actuals on screen, not stale values.
				() => _ts_prod_save_then(frm, () => _ts_prod_call("submit_production", { name: frm.doc.name }, frm))
			);
		}).addClass("btn-primary");
	}

	if (s === "Rejected") {
		frm.add_custom_button(__("Revise & Reopen"), () => {
			frappe.confirm(
				__("Reopen this rejected entry as a Draft so you can edit the actuals and resubmit it?"),
				() => _ts_prod_call("revise_production", { name: frm.doc.name }, frm)
			);
		}).addClass("btn-primary");
	}

	if (s === "Pending CEO" && _ts_prod_is_ceo(frm)) {
		frm.add_custom_button(__("Approve"), () => {
			frappe.confirm(
				__("Approve this variance and post the Manufacture Stock Entry?"),
				() => _ts_prod_call("approve_production", { name: frm.doc.name }, frm)
			);
		}, __("Variance")).addClass("btn-primary");

		frm.add_custom_button(__("Reject"), () => {
			frappe.prompt(
				[{ fieldname: "reason", fieldtype: "Small Text", label: __("Rejection Reason (min 10 chars)"), reqd: 1 }],
				(v) => _ts_prod_call("reject_production", { name: frm.doc.name, reason: v.reason }, frm),
				__("Reject Production Variance"), __("Reject")
			);
		}, __("Variance"));
	}

	if (frm.doc.linked_stock_entry) {
		frm.add_custom_button(__("Manufacture Stock Entry"), () => {
			frappe.set_route("Form", "Stock Entry", frm.doc.linked_stock_entry);
		}, __("View"));
	}
}

function _ts_prod_pull_standard(frm) {
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_production_api.fetch_bom_standard",
		args: { bom: frm.doc.bom },
		freeze: true,
		freeze_message: __("Pulling standard from BOM..."),
		callback(r) {
			const d = r.message || {};
			if (!d.materials) {
				frappe.msgprint(__("Could not read the BOM."));
				return;
			}
			frm.set_value("company", d.company);
			frm.set_value("production_item", d.production_item);
			frm.set_value("production_item_name", d.production_item_name);
			frm.set_value("standard_qty", d.standard_qty);
			frm.set_value("production_uom", d.production_uom);
			if (!flt(frm.doc.actual_produced_qty)) {
				frm.set_value("actual_produced_qty", d.standard_qty);
			}

			frm.clear_table("materials");
			(d.materials || []).forEach((m) => {
				const row = frm.add_child("materials");
				row.item_code = m.item_code;
				row.item_name = m.item_name;
				row.std_qty = m.std_qty;
				row.actual_qty = m.std_qty; // pre-fill actual = standard; operator edits
				row.uom = m.uom;
				row.source_warehouse = m.source_warehouse;
			});
			frm.refresh_field("materials");

			frm.clear_table("byproducts");
			(d.byproducts || []).forEach((b) => {
				const row = frm.add_child("byproducts");
				row.item_code = b.item_code;
				row.item_name = b.item_name;
				row.std_qty = b.std_qty;
				row.actual_qty = b.std_qty;
				row.uom = b.uom;
				row.rate = b.rate;
				row.target_warehouse = b.target_warehouse;
			});
			frm.refresh_field("byproducts");

			if (!(d.byproducts || []).length) {
				frappe.msgprint({
					title: __("No by-products in this BOM"),
					message: __("This BOM has no Scrap Items, so no by-products will be produced. Add Scrap Items to the BOM (e.g. DDGS / DWGS / LCO2) if it should yield by-products."),
					indicator: "blue",
				});
			}
			frappe.show_alert({ message: __("Standard pulled. Enter your actuals, then save."), indicator: "green" });
		},
	});
}

function _ts_prod_save_then(frm, cb) {
	// Persist unsaved edits before a workflow action so the server acts on what's on screen.
	if (frm.is_dirty()) {
		frm.save().then(cb).catch(() => {});
	} else {
		cb();
	}
}

function _ts_prod_call(method, args, frm) {
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_production_api." + method,
		args: args,
		freeze: true,
		freeze_message: __("Processing..."),
		callback(r) {
			const m = r.message || {};
			if (m.message) {
				frappe.show_alert({ message: m.message, indicator: m.stock_entry ? "green" : "orange" });
			}
			frm.reload_doc();
		},
	});
}
