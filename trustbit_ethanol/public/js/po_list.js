// TS PO List — Override indicator + fix docstatus filter for approval statuses
// + force ID column to position 2 of the list (Lesson 226).
//
// v2.9.8.22 — Reliable approach: monkey-patch the prototype `setup_columns`
// method so our `name` column injection runs INSIDE setup_columns. Both the
// header render and the data render use the same final `this.columns` array
// snapshot, so they stay aligned. v2.9.8.16's onload-based injection was
// post-setup and only updated the data render path; the header was already
// painted from the original (unmodified) array, causing the off-by-one
// misalignment that broke prod's PO list (6-field config) on 30 Apr.
// Gated on `this.doctype === "Purchase Order"` so other list views unaffected.
//
// hide_name_column=true suppresses Frappe's default auto-append of `name` at
// the end (which would create a duplicate column at position end + position 2).

frappe.listview_settings = frappe.listview_settings || {};
frappe.listview_settings["Purchase Order"] = frappe.listview_settings["Purchase Order"] || {};
frappe.listview_settings["Purchase Order"].hide_name_column = true;

(function() {
	if (!(frappe.views && frappe.views.ListView && frappe.views.ListView.prototype)) return;
	const _proto = frappe.views.ListView.prototype;
	if (_proto._ts_setup_columns_patched) return;  // idempotent — patch once per page load
	_proto._ts_setup_columns_patched = true;

	const _orig_setup_columns = _proto.setup_columns;
	_proto.setup_columns = function() {
		_orig_setup_columns.apply(this, arguments);
		if (this.doctype !== "Purchase Order") return;
		if (!Array.isArray(this.columns)) return;
		// Inject `name` column at index 2 (after Subject + Tag) idempotently.
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
