/**
 * ts_cc_filter.js — v2.8.8.3
 *
 * No-op helper kept for backward compatibility with po_list.js / mr_list.js
 * which still call `window.ts_add_cc_filter_button(listview)`.
 *
 * As of v2.8.8.3, Cost Center filtering is handled by a native Frappe
 * standard filter (via Property Setter `cost_center.in_standard_filter=1`
 * seeded by `setup_cc_standard_filter.py`). Frappe renders the Link
 * autocomplete in `.standard-filter-section` automatically — no JS needed.
 *
 * Users can multi-select via the existing `+ Filter` button with the
 * `in` operator if they need more than one CC at a time.
 *
 * This no-op keeps the callers wired without needing to edit the
 * PO_FULL / MR_FULL-locked list JS files.
 */
window.ts_add_cc_filter_button = function (listview) {
	// Intentionally empty — standard Frappe filter handles CC filtering.
	// See setup_cc_standard_filter.py for the Property Setter seed.
};
