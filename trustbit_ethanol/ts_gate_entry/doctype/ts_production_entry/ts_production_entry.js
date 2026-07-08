// Copyright (c) 2026, Trustbit Technologies and contributors
// For license information, please see license.txt
//
// TS Production Entry form — Production Logging (single Store-Manager release gate).
//   - "Pull Standard from BOM"  (Draft): data-driven materials + by-products
//   - "Submit for Release"      (Draft): creates the Work Order + draft release SE,
//                                        routes to the Stores Manager (NO CEO gate)
//   - "Approve Release" / "Reject" (Pending Stores Release + Stores Mgr): runs the
//                                        Work-Order / Job-Card / Manufacture chain
//   - "Complete Production"      (Released + Stores Mgr): idempotent recovery re-run
//   - Status intro banner (frm.set_intro — re-applied each refresh, NOT set_headline)
//   - Locks editable fields once the entry leaves Draft

const TS_PROD_VERSION = "v2.18.x-prodlogging-2026-06-25";

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

function _ts_prod_is_store_manager(frm) {
	const roles = frappe.user_roles || [];
	return roles.includes("Stores Manager") || roles.includes("System Manager") || frappe.session.user === "Administrator";
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
	if (s === "Pending Stores Release") {
		frm.set_intro(
			__("Awaiting Stores Manager raw-material release. Work Order {0} created; the release Stock Entry {1} is a DRAFT (no stock moved yet).",
				[frm.doc.work_order || "", frm.doc.release_stock_entry || ""]),
			"orange"
		);
	} else if (s === "Released") {
		frm.set_intro(
			__("Raw material RELEASED to WIP (Release SE {0}). Completing production — if this is stuck, click 'Complete Production' to retry.",
				[frm.doc.release_stock_entry || ""]),
			"blue"
		);
	} else if (s === "Completed") {
		frm.set_intro(
			__("Completed. Manufacture Stock Entry {0} created (finished good + by-products). {1}",
				[frm.doc.linked_stock_entry || "", frm.doc.wip_reconcile_note || ""]),
			"green"
		);
	} else if (s === "Rejected") {
		frm.set_intro(__("Release rejected. {0}", [frm.doc.rejection_reason || ""]), "red");
	} else if (s === "Cancelled") {
		frm.set_intro(__("Cancelled — the linked Manufacture Stock Entry was cancelled."), "red");
	} else if (s === "Draft") {
		frm.set_intro(
			__("Draft. Edit the raw-material rows (this drives the RELEASE only; the finished-goods consumption follows the BOM recipe), then Submit for Release."),
			"blue"
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
		frm.add_custom_button(__("Submit for Release"), () => {
			frappe.confirm(
				__("Submit this run for Stores Manager release? It will create a Work Order and a DRAFT raw-material release Stock Entry, then notify the Stores Manager. No stock moves until the Stores Manager approves the release."),
				// Save any unsaved edits FIRST so the server acts on the actuals on screen.
				() => _ts_prod_save_then(frm, () => _ts_prod_call("submit_for_release", { name: frm.doc.name }, frm))
			);
		}).addClass("btn-primary");
	}

	if (s === "Rejected") {
		frm.add_custom_button(__("Revise & Reopen"), () => {
			frappe.confirm(
				__("Reopen this rejected entry as a Draft so you can edit the raw materials and resubmit it?"),
				() => _ts_prod_call("revise_production", { name: frm.doc.name }, frm)
			);
		}).addClass("btn-primary");
	}

	if (s === "Pending Stores Release" && _ts_prod_is_store_manager(frm)) {
		frm.add_custom_button(__("Approve Release"), () => {
			frappe.confirm(
				__("Approve the raw-material release? This transfers material Stores → WIP, auto-completes any Job Cards, posts the Manufacture Stock Entry, and closes the Work Order."),
				() => _ts_prod_call("approve_release", { name: frm.doc.name }, frm)
			);
		}, __("Release")).addClass("btn-primary");

		// Business rule (3 Jul): Store Managers RELEASE ONLY — Reject is an
		// ADMIN-ONLY recovery action (server enforces this in reject_release).
		// D2 fix (4 Jul, U4): the server's _is_admin admits only System Manager /
		// Administrator — IT Head was shown a button that always PermissionErrored.
		// User decided NOT to widen authority, so the button now matches the server.
		const _roles = frappe.user_roles || [];
		if (_roles.includes("System Manager") || frappe.session.user === "Administrator") {
			frm.add_custom_button(__("Reject (admin)"), () => {
				frappe.prompt(
					[{ fieldname: "reason", fieldtype: "Small Text", label: __("Rejection Reason (min 10 chars)"), reqd: 1 }],
					(v) => _ts_prod_call("reject_release", { name: frm.doc.name, reason: v.reason }, frm),
					__("Reject Raw-Material Release"), __("Reject")
				);
			}, __("Release"));
		}
	}

	if (s === "Released" && _ts_prod_is_store_manager(frm)) {
		frm.add_custom_button(__("Complete Production"), () => {
			frappe.confirm(
				__("Retry completing this released run? Already-done steps are skipped — Job Cards, the Manufacture Stock Entry, and the Work Order close are re-run safely."),
				() => _ts_prod_call("complete_released_production", { name: frm.doc.name }, frm)
			);
		}, __("Release")).addClass("btn-primary");
	}

	if (frm.doc.work_order) {
		frm.add_custom_button(__("Work Order"), () => {
			frappe.set_route("Form", "Work Order", frm.doc.work_order);
		}, __("View"));
	}
	if (frm.doc.release_stock_entry) {
		frm.add_custom_button(__("Release Stock Entry"), () => {
			frappe.set_route("Form", "Stock Entry", frm.doc.release_stock_entry);
		}, __("View"));
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

// Release-flow endpoints live in ts_production_release.py; everything else (the
// legacy revise_production) lives in ts_production_api.py. Route by method name.
const _TS_PROD_RELEASE_METHODS = new Set([
	"submit_for_release", "approve_release", "reject_release", "complete_released_production",
]);

function _ts_prod_call(method, args, frm) {
	const mod = _TS_PROD_RELEASE_METHODS.has(method)
		? "trustbit_ethanol.ts_gate_entry.ts_production_release."
		: "trustbit_ethanol.ts_gate_entry.ts_production_api.";
	frappe.call({
		method: mod + method,
		args: args,
		freeze: true,
		freeze_message: __("Processing..."),
		callback(r) {
			const m = r.message || {};
			if (m.message) {
				const ok = !!(m.ok || m.stock_entry || m.status === "ok");
				frappe.show_alert({ message: m.message, indicator: ok ? "green" : "orange" });
			}
			frm.reload_doc();
		},
	});
}
