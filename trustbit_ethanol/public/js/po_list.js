// TS PO List — Override indicator + fix docstatus filter for approval statuses
// + force ID column to position 2 of the list (Lesson 226).

// v2.9.8.16 — Frappe v15's setup_columns() either auto-appends `name` at the
// END (when title_field is set) or omits it entirely (when hide_name_column).
// Frappe's meta layer doesn't expose `name` as a real DocField, so PropSets
// `in_list_view=1` are no-ops. The ONLY way to put ID at a non-end position
// is to inject a synthetic name column into listview.columns AFTER setup_columns
// runs. We do that here via the listview lifecycle.
frappe.listview_settings = frappe.listview_settings || {};
frappe.listview_settings["Purchase Order"] = frappe.listview_settings["Purchase Order"] || {};
frappe.listview_settings["Purchase Order"].hide_name_column = true;

// Inject name column at index 2 (after Subject + Tag) on every column setup.
function _ts_inject_name_column(listview) {
	if (!listview || !Array.isArray(listview.columns)) return;
	const cols = listview.columns;
	// Already at desired position?
	if (cols[2] && cols[2].df && cols[2].df.fieldname === "name") return;
	// Remove name from wherever it currently lives
	const idx = cols.findIndex(c => c && c.df && c.df.fieldname === "name");
	let name_col;
	if (idx >= 0) {
		name_col = cols.splice(idx, 1)[0];
	} else {
		name_col = { type: "Field", df: { label: __("ID"), fieldname: "name" } };
	}
	// Insert at position 2 (after Subject + Tag)
	cols.splice(2, 0, name_col);
}

// listview_settings.onload fires once after listview construction, before render.
const _orig_onload_po = frappe.listview_settings["Purchase Order"].onload;
frappe.listview_settings["Purchase Order"].onload = function(listview) {
	if (typeof _orig_onload_po === "function") {
		try { _orig_onload_po(listview); } catch (e) {}
	}
	_ts_inject_name_column(listview);
};

$(document).on("page-change", function() {
	if (cur_list && cur_list.doctype === "Purchase Order") {
		_patch_po_list(cur_list);
	}
});

function _patch_po_list(listview) {
	if (listview._ts_patched) return;
	listview._ts_patched = true;

	// v2.9.8.16: also wrap refresh_columns so any later meta/list-view-settings
	// reload (filter changes, doctype reload, etc.) re-injects name at pos 2.
	const _orig_refresh_cols = listview.refresh_columns && listview.refresh_columns.bind(listview);
	if (_orig_refresh_cols) {
		listview.refresh_columns = function(meta, lvs) {
			_orig_refresh_cols(meta, lvs);
			_ts_inject_name_column(listview);
		};
	}
	// Defensive: in case onload didn't fire (or fired before listview.columns
	// was set), inject now.
	_ts_inject_name_column(listview);

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
