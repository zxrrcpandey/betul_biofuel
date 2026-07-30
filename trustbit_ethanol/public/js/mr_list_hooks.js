// TS Material Request list hooks — loaded via doctype_list_js (v2.28.5)
//
// WHY THIS FILE EXISTS: erpnext/stock/doctype/material_request/
// material_request_list.js line 1 is a WHOLE-OBJECT assignment —
// `frappe.listview_settings["Material Request"] = {...}` — evaluated at
// frappe.model.init_doctype(), i.e. AFTER the desk bundle where mr_list.js
// lives, wiping anything it set at module scope (the same trap as the PO side;
// see po_list_hold.js + Lesson 332). doctype_list_js content is appended to
// __list_js AFTER the core file (frappe/desk/form/meta.py:103 then :115), so
// what is chained here survives.
//
// WHY settings.onload AND NOT page-change: frappe's list_factory.make()
// evaluates `me.make_page(...)` as a constructor ARGUMENT — container
// .change_to() fires "page-change" inside it — and set_cur_list() runs only
// after the constructor returns. So on any cold load (F5/hard-refresh directly
// on the list) the page-change fires while cur_list is still null and
// mr_list.js's handler misses the first paint entirely: no All Records /
// My Pending / My Submissions toggle, no TS indicators, until the user
// navigates away and back. settings.onload (list_view.js:333, once per
// instance) and settings.refresh (base_list.js:512, every render) receive the
// listview INSTANCE — no cur_list, no race. mr_list.js's page-change handler
// stays as belt-and-braces; every installed patch is idempotent
// (_ts_patched / _already_applied / DOM-presence guards).

frappe.listview_settings["Material Request"] = frappe.listview_settings["Material Request"] || {};

(function () {
	const mr = frappe.listview_settings["Material Request"];

	// add_fields is consumed ONCE in setup_fields, BEFORE settings.onload fires —
	// so it must be set here at module scope. ts_mr_status is in_list_view today,
	// but columns are admin-owned since v2.27.3; if an admin drops that column the
	// indicator override would silently lose its data.
	mr.add_fields = (mr.add_fields || []).slice();
	if (!mr.add_fields.includes("ts_mr_status")) mr.add_fields.push("ts_mr_status");

	function _patch(listview) {
		if (!listview) return;
		// try/catch: a TS failure must never suppress erpnext's chained half.
		try {
			if (typeof window.ts_patch_mr_list === "function") {
				// Full patch: TS get_indicator + docstatus-filter fix + toggle.
				window.ts_patch_mr_list(listview);
			} else if (typeof window.ts_apply_my_approvals_filter === "function") {
				// Browser pinned to a pre-v2.28.5 mr_list.js (bare-/assets 1y
				// cache, L318/319) without the export: toggle + role filter still
				// install; the indicator override arrives with ?v=3. Idempotent
				// vs a later page-change patch via _already_applied.
				window.ts_apply_my_approvals_filter(listview);
			}
		} catch (e) {
			// swallow — TS pieces retry on page-change
		}
	}

	// __list_js can be evaluated more than once per session (clear_cache + meta
	// reload) — guard the chain installation itself so nothing stacks.
	// erpnext's material_request_list.js currently has NO onload, hence the
	// null-safety; the chain shape still preserves one if a future erpnext adds it.
	if (!(mr.onload && mr.onload._ts_chained)) {
		const _core_onload = mr.onload;
		const chained_onload = function (listview) {
			if (_core_onload) _core_onload(listview);
			_patch(listview);
		};
		chained_onload._ts_chained = true;
		mr.onload = chained_onload;
	}

	if (!(mr.refresh && mr.refresh._ts_chained)) {
		const _core_refresh = mr.refresh;
		const chained_refresh = function (listview) {
			if (_core_refresh) _core_refresh(listview);
			// Re-assert on every render — O(1) no-op when already patched, and
			// the recovery path for an instance whose onload ran before the
			// export existed (blocked asset fetch / stale service-worker cache).
			_patch(listview);
		};
		chained_refresh._ts_chained = true;
		mr.refresh = chained_refresh;
	}
})();
