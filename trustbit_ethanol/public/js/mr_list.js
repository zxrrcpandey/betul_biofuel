// TS MR List — Override indicator + fix docstatus filter for approval statuses
$(document).on("page-change", function() {
	if (cur_list && cur_list.doctype === "Material Request") {
		_patch_mr_list(cur_list);
	}
});

function _patch_mr_list(listview) {
	if (listview._ts_patched) return;
	listview._ts_patched = true;

	const orig = listview.settings.get_indicator;
	listview.settings.get_indicator = function(doc) {
		const bbf = doc.ts_mr_status;
		if (bbf && bbf !== "Approved" && bbf !== "Not Submitted") {
			// v2.17.4: drill to the EXACT status clicked (was `like,Pending%`/`like,On Hold%`).
			if (bbf.startsWith("Pending")) return [bbf, "orange", "ts_mr_status,=," + bbf];
			if (bbf === "Rejected") return [bbf, "red", "ts_mr_status,=,Rejected"];
			if (bbf === "Revised") return [bbf, "orange", "ts_mr_status,=,Revised"];
			if (bbf.startsWith("On Hold")) return [bbf, "yellow", "ts_mr_status,=," + bbf];
		}
		if (orig) return orig(doc);
	};

	if (!listview.settings.add_fields) listview.settings.add_fields = [];
	if (!listview.settings.add_fields.includes("ts_mr_status")) {
		listview.settings.add_fields.push("ts_mr_status");
	}

	const orig_refresh = listview.refresh.bind(listview);
	listview.refresh = function() {
		_fix_mr_docstatus_for_approval_filter(listview);
		// v2.17.4: a manual "Approval Status" pick must win over the role-scoped
		// "My Pending" default — else the two filters AND to zero rows.
		if (typeof window.ts_reconcile_manual_status_filter === "function") {
			window.ts_reconcile_manual_status_filter(listview, "ts_mr_status");
		}
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

function _fix_mr_docstatus_for_approval_filter(listview) {
	try {
		const filters = listview.get_filters_for_args();
		const has_approval = filters.some(f =>
			f[1] === "ts_mr_status" && f[3] && f[3] !== "Approved"
		);
		if (has_approval) {
			// v2.28.5: only when a docstatus filter actually EXISTS — an
			// unconditional FilterArea.remove() re-triggers refresh and
			// self-sustains a ~3s poll loop (see the twin comment in po_list.js).
			const _fl = listview.filter_area && listview.filter_area.filter_list;
			if (_fl && typeof _fl.get_filter === "function" && _fl.get_filter("docstatus")) {
				listview.filter_area.remove("docstatus");
			}
		}
	} catch(e) {}
}

// v2.28.5 — export for mr_list_hooks.js's settings.onload/refresh chain (cold-load
// fix; see the twin comment at the end of po_list.js — same race, same reason).
window.ts_patch_mr_list = _patch_mr_list;
