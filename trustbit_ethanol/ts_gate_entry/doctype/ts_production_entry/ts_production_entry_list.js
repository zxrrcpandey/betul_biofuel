// TS Production Entry — list view (22 Jul 2026). The stored status stays
// "Pending Stores Release" while departments release (single-status design,
// v2.21); the dashboard overlays "Pending Department Release" via
// pending_department_map(). Apply the SAME display-only overlay here so the
// list shows which runs are actually waiting on departments. The endpoint is
// role-gated to the release-zone audience; others just see the stored status.
frappe.listview_settings["TS Production Entry"] = {
	_dept_pending: {},

	onload(listview) { this._load_map(listview); },
	refresh(listview) { this._load_map(listview); },

	_load_map(listview) {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_production_dept_release.pending_department_map",
			callback: (r) => {
				const next = r.message || {};
				const changed = JSON.stringify(next) !== JSON.stringify(this._dept_pending);
				this._dept_pending = next;
				// rows render before the fetch returns — re-render once on change
				if (changed && listview && listview.render_list) listview.render_list();
			},
			error: () => {},  // fail soft — fall back to the stored status
		});
	},

	_dept_label(doc) {
		return doc.ts_variance_status === "Pending Stores Release" &&
			this._dept_pending[doc.name] ? __("Pending Department Release") : null;
	},

	get_indicator(doc) {
		const dept = this._dept_label(doc);
		if (dept) return [dept, "yellow", "ts_variance_status,=,Pending Stores Release"];
		const colors = {
			"Draft": "gray", "Pending Stores Release": "orange", "Released": "blue",
			"Awaiting Department Production": "purple", "Pending Material Request": "cyan",
			"Awaiting Distribution": "pink", "Completed": "green", "Rejected": "red",
			"Cancelled": "gray",
		};
		const st = doc.ts_variance_status || "Draft";
		return [__(st), colors[st] || "gray", "ts_variance_status,=," + st];
	},

	formatters: {
		// keep the Status COLUMN consistent with the indicator pill
		ts_variance_status(value, df, doc) {
			const dept = frappe.listview_settings["TS Production Entry"]._dept_label(doc);
			return frappe.utils.escape_html(dept || value || "");
		},
	},
};
