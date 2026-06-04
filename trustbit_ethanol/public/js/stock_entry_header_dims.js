// v2.16.5 — Stock Entry header accounting defaults cascade to item rows.
// Picking "Cost Center (all items)" / "Project (all items)" applies it to every
// existing item row instantly; a newly added row inherits the current header
// value. The server before_validate (ts_se_header_dimensions.cascade_se_header_dimensions)
// is the fill-blank safety net for programmatic rows and on save.
//
// For a Material Issue both header fields are MANDATORY (red asterisk + blocks
// save) — set client-side so the programmatic MR-Transfer draft (server insert)
// is not affected. The server before_submit guard (ts_stock_issue_warning) is
// the integrity backstop that the cascaded row values actually landed.

frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		_ts_toggle_header_reqd(frm);
	},
	purpose(frm) {
		_ts_toggle_header_reqd(frm);
	},
	ts_set_cost_center(frm) {
		_ts_apply_to_rows(frm, "ts_set_cost_center", "cost_center");
	},
	ts_set_project(frm) {
		_ts_apply_to_rows(frm, "ts_set_project", "project");
	},
});

function _ts_toggle_header_reqd(frm) {
	// Material Issue → header Cost Center + Project are required (UI-enforced).
	const req = frm.doc.purpose === "Material Issue" ? 1 : 0;
	frm.set_df_property("ts_set_cost_center", "reqd", req);
	frm.set_df_property("ts_set_project", "reqd", req);
}

frappe.ui.form.on("Stock Entry Detail", {
	items_add(frm, cdt, cdn) {
		// A new row inherits the current header defaults (fill-blank).
		const row = frappe.get_doc(cdt, cdn);
		if (!row) return;
		if (frm.doc.ts_set_cost_center && !row.cost_center) {
			frappe.model.set_value(cdt, cdn, "cost_center", frm.doc.ts_set_cost_center);
		}
		if (frm.doc.ts_set_project && !row.project) {
			frappe.model.set_value(cdt, cdn, "project", frm.doc.ts_set_project);
		}
	},
});

function _ts_apply_to_rows(frm, header_field, row_field) {
	const val = frm.doc[header_field];
	if (!val) return;
	(frm.doc.items || []).forEach((row) => {
		frappe.model.set_value(row.doctype, row.name, row_field, val);
	});
	frm.refresh_field("items");
}
