// TS PO List — Override indicator + fix docstatus filter for approval statuses
$(document).on("page-change", function() {
	if (cur_list && cur_list.doctype === "Purchase Order") {
		_patch_po_list(cur_list);
	}
});

function _patch_po_list(listview) {
	if (listview._ts_patched) return;
	listview._ts_patched = true;

	// Override get_indicator to show TS approval status
	const orig = listview.settings.get_indicator;
	listview.settings.get_indicator = function(doc) {
		const bbf = doc.ts_approval_status;
		if (bbf && bbf !== "Approved" && bbf !== "Not Submitted") {
			if (bbf.startsWith("Pending")) return [bbf, "orange", "ts_approval_status,like,Pending%"];
			if (bbf === "Rejected") return [bbf, "red", "ts_approval_status,=,Rejected"];
			if (bbf === "Revised") return [bbf, "orange", "ts_approval_status,=,Revised"];
			if (bbf.startsWith("On Hold")) return [bbf, "yellow", "ts_approval_status,like,On Hold%"];
		}
		if (orig) return orig(doc);
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
