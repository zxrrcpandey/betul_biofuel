// TS PO List — Override indicator + fix docstatus filter for approval statuses.
//
// v2.9.8.21 — REVERTED v2.9.8.16's JS column injection.
// The `_ts_inject_name_column` post-setup splice into listview.columns
// caused a CRITICAL header/data misalignment on prod (6-field tabList View
// Settings JSON vs demo's 4-field). Frappe v15 renders headers and data from
// different snapshots of the columns array, so post-setup mutation creates an
// off-by-one between header labels and the doc data underneath. ID is left at
// Frappe's natural last-column position; column alignment is the priority.
// (Putting ID at position 2 isn't reliably achievable in Frappe v15 without
// an underlying DocField — see Lesson 226 for the failed approaches.)

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
