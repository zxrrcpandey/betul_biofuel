// TS PO List — Executive Hold display (v2.28.5)
//
// A held PO must read "On Hold" in the list. ts_approval_status is deliberately
// NEVER overwritten when a PO goes on hold — an Approved PO has to stay
// "Approved" for every downstream consumer, and Resume has to restore the exact
// step — so the hold is surfaced here as a pure DISPLAY override, keyed on the
// tamper-guarded ts_po_on_hold flag.
//
// WHY THIS IS A SEPARATE FILE, LOADED VIA doctype_list_js:
// erpnext/buying/doctype/purchase_order/purchase_order_list.js line 1 is a
// WHOLE-OBJECT assignment — `frappe.listview_settings["Purchase Order"] = {...}`
// — evaluated at frappe.model.init_doctype(). That runs AFTER the desk bundle
// (app_include_js, where po_list.js lives), so anything po_list.js sets at
// module scope is wiped before first use. doctype_list_js content is appended
// to __list_js AFTER the core file (frappe/desk/form/meta.py:103 then :115),
// so what we set here survives — and it lands before the list's first data
// fetch, which matters because add_fields decides what gets queried.
//
// PURCHASE ORDER ONLY. Material Request stores a real plain "On Hold" status of
// its own (v2.28.1) and must not inherit a PO-only field or formatter.

frappe.listview_settings["Purchase Order"] = frappe.listview_settings["Purchase Order"] || {};

(function () {
	const po = frappe.listview_settings["Purchase Order"];

	// (a) make sure the flag is actually fetched with the list rows.
	// add_fields is consumed ONCE in setup_fields, BEFORE settings.onload fires
	// (base_list init task order) — so it must be set here at module scope; adding
	// it from onload only ever helps the NEXT instance. ts_approval_status is
	// included defensively too (v2.28.5): it is in_list_view today, but columns
	// are admin-owned since v2.27.3, and if an admin drops that column the
	// formatter and indicator would silently lose their data.
	po.add_fields = (po.add_fields || []).slice();
	if (!po.add_fields.includes("ts_po_on_hold")) po.add_fields.push("ts_po_on_hold");
	if (!po.add_fields.includes("ts_approval_status")) po.add_fields.push("ts_approval_status");

	// (b) the Approval Status COLUMN itself — this is what the user reads.
	// list_view.js:907-911 interpolates the formatter's return value raw, so the
	// contract is to return already-escaped HTML.
	//
	// EVERY status gets a pill, not just On Hold. When only the hold value was
	// styled, the rest of the column ("Pending CEO", "Approved", "Rejected") read
	// as unstyled plain text beside it and looked broken — reported by the user.
	// Colours follow the row-indicator language already used in po_list.js, with
	// Pending in blue so an exceptional hold stays visually distinct from the
	// normal in-progress states.
	const STATUS_COLOR = {
		"Approved": "green",
		"Rejected": "red",
		"Revised": "purple",
		"Not Submitted": "gray",
		"Draft": "gray",
	};
	function _status_color(value) {
		if (STATUS_COLOR[value]) return STATUS_COLOR[value];
		if (value && value.indexOf("Pending") === 0) return "blue";
		if (value && value.indexOf("Awaiting") === 0) return "yellow";
		return "gray";
	}
	function _pill(color, label, tip) {
		return (
			'<span class="indicator-pill ' + color + '" title="' +
			frappe.utils.escape_html(tip || label) + '">' +
			frappe.utils.escape_html(label) +
			"</span>"
		);
	}

	po.formatters = Object.assign({}, po.formatters, {
		ts_approval_status: function (value, df, doc) {
			if (cint(doc && doc.ts_po_on_hold)) {
				// keep the real step on hover — it is where Resume sends the PO back to
				return _pill("orange", __("On Hold"),
					value ? __("On executive hold — resumes to: {0}", [value])
						: __("On executive hold"));
			}
			if (!value) return "";
			return _pill(_status_color(value), value);
		},
	});
})();

// (c) row indicator, so the pill agrees with the column and drills to held POs.
// po_list.js's _patch_po_list re-assigns settings.get_indicator for EVERY new
// ListView instance, so we re-wrap whenever we see an unwrapped base function.
// The marker lives on the wrapper we install and is tested against the CURRENT
// get_indicator — a fresh base is wrapped exactly once, and layers can never
// accumulate across repeated invocations from any trigger below.
function _ts_wrap_hold_indicator(settings) {
	if (!settings || (settings.get_indicator && settings.get_indicator._ts_hold_wrapped)) return;

	const _inner = settings.get_indicator;
	const wrapped = function (doc) {
		if (cint(doc && doc.ts_po_on_hold)) {
			return [__("On Hold"), "orange", "ts_po_on_hold,=,1"];
		}
		return _inner ? _inner(doc) : undefined;
	};
	wrapped._ts_hold_wrapped = true;
	settings.get_indicator = wrapped;
}

$(document).on("page-change", function () {
	if (!(cur_list && cur_list.doctype === "Purchase Order")) return;
	_ts_wrap_hold_indicator(cur_list.settings);
});

// (d) v2.28.5 — cold-load fix. frappe's list_factory.make() evaluates
// `me.make_page(...)` as a constructor ARGUMENT — container.change_to() fires
// "page-change" inside it — and set_cur_list() runs only after the constructor
// returns (and on the cached-page branch, change_to likewise precedes on_show).
// So on any F5 directly on the list, page-change fires while cur_list is still
// null/stale: po_list.js's handler AND the one above miss the first paint — no
// toggle buttons, no TS indicators, no hold pill — until the user navigates away
// and back. settings.onload (list_view.js:333, once per instance) and
// settings.refresh (base_list.js:512, every render) receive the listview
// INSTANCE, so there is no race. The page-change handlers stay as belt-and-braces.
//
// ORDER MATTERS: settings is the GLOBAL frappe.listview_settings object shared by
// every PO ListView instance, while _ts_patched is per-instance — so a second
// instance (e.g. Report view) re-runs _patch_po_list, which re-assigns a FRESH
// get_indicator and silently drops the hold wrapper. ts_patch_po_list must
// therefore run FIRST and the hold re-wrap SECOND, in BOTH chains below.
(function () {
	const po = frappe.listview_settings["Purchase Order"];

	function _patch(listview) {
		if (!listview) return;
		// try/catch: a TS failure must never suppress erpnext's chained half
		// (Close/Reopen menu + bulk actions used by every Purchase Manager).
		try {
			if (typeof window.ts_patch_po_list === "function") {
				// Full patch: TS get_indicator + docstatus-filter fix + toggle.
				window.ts_patch_po_list(listview);
			} else if (typeof window.ts_apply_my_approvals_filter === "function") {
				// Browser pinned to a pre-v2.28.5 po_list.js (bare-/assets 1y
				// cache, L318/319) without the export: toggle + role filter still
				// install; the indicator override arrives with ?v=3. Idempotent
				// vs a later page-change patch via _already_applied.
				window.ts_apply_my_approvals_filter(listview);
			}
		} catch (e) {
			// swallow — erpnext half already ran; TS pieces retry on page-change
		}
		// Own guard: the hold pill is LIVE on prod — a throw in the patch above
		// must not skip the re-wrap (the wrap must also run when the patch threw
		// AFTER re-assigning a fresh, unwrapped get_indicator).
		try {
			_ts_wrap_hold_indicator(listview.settings);
		} catch (e) {}
	}

	// __list_js can be evaluated more than once per session (clear_cache +
	// meta reload). erpnext's onload is NOT idempotent (add_menu_item appends
	// Close/Reopen every call), so guard the chain installation itself.
	if (!(po.onload && po.onload._ts_chained)) {
		const _core_onload = po.onload;
		const chained_onload = function (listview) {
			if (_core_onload) _core_onload(listview);
			_patch(listview);
		};
		chained_onload._ts_chained = true;
		po.onload = chained_onload;
	}

	if (!(po.refresh && po.refresh._ts_chained)) {
		const _core_refresh = po.refresh;
		const chained_refresh = function (listview) {
			if (_core_refresh) _core_refresh(listview);
			// Re-assert on every render — O(1) no-op when already patched
			// (_ts_patched / _already_applied / _ts_hold_wrapped guards), and the
			// recovery path for an instance whose onload ran before the exports
			// existed (blocked asset fetch / stale service-worker cache).
			_patch(listview);
		};
		chained_refresh._ts_chained = true;
		po.refresh = chained_refresh;
	}
})();
