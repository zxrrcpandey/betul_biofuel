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

	// (a) make sure the flag is actually fetched with the list rows
	po.add_fields = (po.add_fields || []).slice();
	if (!po.add_fields.includes("ts_po_on_hold")) po.add_fields.push("ts_po_on_hold");

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
// accumulate across repeated page-changes.
$(document).on("page-change", function () {
	if (!(cur_list && cur_list.doctype === "Purchase Order")) return;
	const settings = cur_list.settings;
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
});
