// TS Deduction Suggestion — v2.9.6
// Minimal form handlers. All edit gating is via DocPerm.

frappe.ui.form.on("TS Deduction Suggestion", {
	refresh(frm) {
		if (frm.doc.quality_inspection) {
			frm.set_intro(
				`Source: ${frappe.utils.escape_html(frm.doc.quality_inspection)}`,
				"blue"
			);
		}
		_ts_render_dsg_delta(frm);
	},

	actual_pct(frm) {
		_ts_render_dsg_delta(frm);
	},
});

function _ts_render_dsg_delta(frm) {
	if (frm.doc.actual_pct === null || frm.doc.actual_pct === undefined) return;
	if (frm.doc.system_pct === null || frm.doc.system_pct === undefined) return;
	const delta = flt(frm.doc.actual_pct) - flt(frm.doc.system_pct);
	const abs_delta = Math.abs(delta);
	if (abs_delta > 0.01) {
		const color = delta > 0 ? "orange" : "blue";
		frm.dashboard.add_indicator(
			__("Δ Actual vs System: {0}%", [delta.toFixed(3)]),
			color
		);
	} else {
		frm.dashboard.add_indicator(__("Actual matches System Value"), "green");
	}
}
