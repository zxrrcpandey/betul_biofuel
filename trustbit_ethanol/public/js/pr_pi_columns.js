// Force ts_delivery_location and ts_item_remark columns visible on Purchase Receipt and Purchase Invoice
// Frappe __UserSettings cache overrides Custom Field in_list_view, so we force on every form load

function _force_grid_columns(frm) {
	try {
		const grid = frm.fields_dict.items?.grid;
		if (!grid) return;

		const meta_fields = grid.meta?.fields || grid.df?.fields || [];
		meta_fields.forEach(f => {
			if (["ts_delivery_location", "ts_item_remark"].includes(f.fieldname)) {
				f.in_list_view = 1;
				f.columns = 2;
			}
		});

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
	refresh(frm) { _force_grid_columns(frm); },
	items_on_form_rendered(frm) { _force_grid_columns(frm); }
});

frappe.ui.form.on("Purchase Invoice", {
	refresh(frm) { _force_grid_columns(frm); },
	items_on_form_rendered(frm) { _force_grid_columns(frm); }
});
