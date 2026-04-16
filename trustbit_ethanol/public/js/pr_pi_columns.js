// Force ts_delivery_location and ts_item_remark columns visible on Purchase Receipt and Purchase Invoice
// Frappe __UserSettings cache overrides Custom Field in_list_view, so we force on every form load

function _force_grid_columns(frm, child_dt) {
	try {
		["ts_delivery_location", "ts_item_remark"].forEach(fn => {
			let df = frappe.meta.get_docfield(child_dt, fn, frm.doc.name);
			if (df) {
				df.in_list_view = 1;
				df.columns = 2;
			}
		});

		const grid = frm.fields_dict.items?.grid;
		if (!grid) return;

		(grid.grid_rows || []).forEach(row => {
			(row.docfields || []).forEach(f => {
				if (["ts_delivery_location", "ts_item_remark"].includes(f.fieldname)) {
					f.in_list_view = 1;
					f.columns = 2;
				}
			});
		});

		grid.refresh();
	} catch(e) {}
}

frappe.ui.form.on("Purchase Receipt", {
	refresh(frm) {
		_force_grid_columns(frm, "Purchase Receipt Item");
		_show_grn_source_indicator(frm);
	}
});

function _show_grn_source_indicator(frm) {
	$(".ts-grn-source-banner").remove();
	if (frm.is_new()) return;

	const is_direct = frm.doc.ts_direct_po_grn;
	const is_non_weighing = frm.doc.ts_non_weighing_grn;
	const source = frm.doc.ts_stores_dashboard_source;
	const token = frm.doc.ts_token;

	if (is_direct) {
		frm.set_df_property("ts_token", "description", "Not applicable — this GRN was created from a Direct PO (no gate entry / token)");
		frm.get_field("ts_token").$wrapper.find(".control-label").html("TS Token <span style='color:#d97706;font-weight:normal;font-size:11px;'> — Direct PO</span>");
	} else if (is_non_weighing && token) {
		frm.set_df_property("ts_token", "description", "Non-RM without weighing — qty from PO ordered quantity");
	} else if (token) {
		frm.set_df_property("ts_token", "description", "Weighed material — qty from weighbridge net weight");
	}

	if (source) {
		const labels = {
			"Section A": {text: "Weighed Token", color: "#2563eb"},
			"Section B": {text: "Non-RM Token (No Weighing)", color: "#d97706"},
			"Section C": {text: "Direct PO (No Token)", color: "#16a34a"},
		};
		const lbl = labels[source] || {text: source, color: "#64748b"};
		const html = `<div class="ts-grn-source-banner" style="margin-bottom:10px;padding:8px 14px;border-radius:6px;border-left:4px solid ${lbl.color};background:var(--fg-color, #f8fafc);font-size:12px;color:var(--text-color);">
			<strong style="color:${lbl.color};">GRN Source: ${lbl.text}</strong>
			${is_direct ? " — No gate entry or token required for this cost center." : ""}
		</div>`;
		$(frm.fields_dict.ts_token.wrapper).before(html);
	}
}

frappe.ui.form.on("Purchase Invoice", {
	refresh(frm) { _force_grid_columns(frm, "Purchase Invoice Item"); }
});
