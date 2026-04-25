// v2.8.13 — Role-scoped default filter for TS Post Dated Entry Request list.
$(document).on("page-change", function () {
	if (cur_list && cur_list.doctype === "TS Post Dated Entry Request") {
		_patch_post_dated_list(cur_list);
	}
});

function _patch_post_dated_list(listview) {
	if (listview._ts_patched_my_approvals) return;
	listview._ts_patched_my_approvals = true;
	if (typeof window.ts_apply_my_approvals_filter === "function") {
		window.ts_apply_my_approvals_filter(listview);
	}
}
