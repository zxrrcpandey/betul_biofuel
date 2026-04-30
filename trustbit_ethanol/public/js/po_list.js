// TS PO + MR List — Override indicator + fix docstatus filter for approval
// statuses + force ID column to position 2 (Lessons 226, 228).
// v2.9.8.30 — extend setup_columns monkey-patch + hide_name_column to also
// handle Material Request (same pattern as Purchase Order).

frappe.listview_settings = frappe.listview_settings || {};
const _TS_LIST_DOCTYPES = ["Purchase Order", "Material Request"];
_TS_LIST_DOCTYPES.forEach((dt) => {
	frappe.listview_settings[dt] = frappe.listview_settings[dt] || {};
	frappe.listview_settings[dt].hide_name_column = true;
});

(function() {
	if (!(frappe.views && frappe.views.ListView && frappe.views.ListView.prototype)) return;
	const _proto = frappe.views.ListView.prototype;
	if (_proto._ts_setup_columns_patched) return;
	_proto._ts_setup_columns_patched = true;

	const _orig_setup_columns = _proto.setup_columns;
	_proto.setup_columns = function() {
		_orig_setup_columns.apply(this, arguments);
		if (!_TS_LIST_DOCTYPES.includes(this.doctype)) return;
		if (!Array.isArray(this.columns)) return;
		if (this.columns[2] && this.columns[2].df && this.columns[2].df.fieldname === "name") return;
		const idx = this.columns.findIndex(c => c && c.df && c.df.fieldname === "name");
		let name_col;
		if (idx >= 0) {
			name_col = this.columns.splice(idx, 1)[0];
		} else {
			name_col = { type: "Field", df: { label: __("ID"), fieldname: "name" } };
		}
		this.columns.splice(2, 0, name_col);
	};
})();

$(document).on("page-change", function() {
	if (cur_list && cur_list.doctype === "Purchase Order") {
		_patch_po_list(cur_list);
	}
});

function _patch_po_list(listview) {
	if (listview._ts_patched) return;
	listview._ts_patched = true;

	// Override get_indicator to show TS approval status only.
	// v2.9.8.12: never fall through to Frappe's native PO `status` indicator —
	// previously, an Approved or null ts_approval_status fell back to `orig(doc)`
	// which rendered the native "Status" pill alongside the "Approval Status"
	// column. User asked to hide the native Status column on the list view.
	listview.settings.get_indicator = function(doc) {
		const bbf = doc.ts_approval_status;
		if (bbf === "Approved")        return ["Approved", "green",  "ts_approval_status,=,Approved"];
		if (bbf === "Rejected")        return ["Rejected", "red",    "ts_approval_status,=,Rejected"];
		if (bbf === "Revised")         return ["Revised",  "orange", "ts_approval_status,=,Revised"];
		if (bbf === "Not Submitted")   return ["Not Submitted", "gray", "ts_approval_status,=,Not Submitted"];
		if (bbf && bbf.startsWith("Pending"))  return [bbf, "orange", "ts_approval_status,like,Pending%"];
		if (bbf && bbf.startsWith("On Hold"))  return [bbf, "yellow", "ts_approval_status,like,On Hold%"];
		// Empty / Draft / unknown — show neutral Draft pill, NOT native Frappe status.
		return ["Draft", "gray", "ts_approval_status,in,,Draft,Not Submitted"];
	};

	// Add ts_approval_status to fetched fields
	if (!listview.settings.add_fields) listview.settings.add_fields = [];
	if (!listview.settings.add_fields.includes("ts_approval_status")) {
		listview.settings.add_fields.push("ts_approval_status");
	}

	// When Approval Status filter is set to Pending/Rejected/Revised,
	// auto-remove docstatus filter so Draft POs show up
	const orig_refresh = listview.refresh.bind(listview);
	listview.refresh = function() {
		_fix_docstatus_for_approval_filter(listview);
		return orig_refresh();
	};

	// v2.8.8: Multi-select Cost Center filter button
	if (typeof window.ts_add_cc_filter_button === "function") {
		window.ts_add_cc_filter_button(listview);
	}

	// v2.8.13: Role-scoped "My Pending Approvals" default filter + toggle
	if (typeof window.ts_apply_my_approvals_filter === "function") {
		window.ts_apply_my_approvals_filter(listview);
	}
}

function _fix_docstatus_for_approval_filter(listview) {
	try {
		const filters = listview.get_filters_for_args();
		const has_approval = filters.some(f =>
			f[1] === "ts_approval_status" && f[3] && f[3] !== "Approved"
		);
		if (has_approval) {
			// Remove docstatus filter so Draft (pending) POs show
			listview.filter_area.remove("docstatus");
		}
	} catch(e) {}
}

// v2.9.8.27 — Width-tighten attempts (.23/.24/.25/.26) all caused column
// alignment bugs in Frappe v15 (header/data shift, blank cells). Reverted
// the entire width-fix path. ID at position 2 (v2.9.8.22 setup_columns
// monkey-patch above) still works. Empty space between columns is
// Frappe's default `flex: 1` distribution — accepted as a known
// limitation pending a cleaner solution (e.g. add more columns to the
// list view to fill the gaps; will iterate based on user preference).
