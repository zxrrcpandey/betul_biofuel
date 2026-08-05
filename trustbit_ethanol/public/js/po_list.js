// TS PO + MR List — Override indicator + fix docstatus filter for approval
// statuses + force ID column to position 2 (Lessons 226, 228).
// v2.9.8.30 — extend setup_columns monkey-patch + hide_name_column to also
// handle Material Request (same pattern as Purchase Order).

frappe.listview_settings = frappe.listview_settings || {};
const _TS_LIST_DOCTYPES = ["Purchase Order", "Material Request"];
_TS_LIST_DOCTYPES.forEach((dt) => {
	frappe.listview_settings[dt] = frappe.listview_settings[dt] || {};
	frappe.listview_settings[dt].hide_name_column = true;
});

(function() {
	if (!(frappe.views && frappe.views.ListView && frappe.views.ListView.prototype)) return;
	const _proto = frappe.views.ListView.prototype;
	if (_proto._ts_setup_columns_patched) return;
	_proto._ts_setup_columns_patched = true;

	const _orig_setup_columns = _proto.setup_columns;
	_proto.setup_columns = function() {
		_orig_setup_columns.apply(this, arguments);
		// v2.27.2 — the v2.18.11 hardcoded PO column lock is REMOVED: it made
		// every admin "Pick Columns" edit cosmetically ignored while the
		// after_migrate seeder reset List View Settings underneath (root cause
		// of "columns keep changing on prod"). PO now falls through to the
		// generic branch below: columns are List View Settings-driven (admin
		// owns the set), only the ID position is enforced.
		if (!_TS_LIST_DOCTYPES.includes(this.doctype)) return;
		if (!Array.isArray(this.columns)) return;
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

// v2.27.2 — UNUSED (kept only for reference; call site removed above). Was the
// v2.18.11 fixed PO column set. Do NOT re-wire: the global List View Settings
// row is the single source of truth for columns now.
function _ts_lock_po_columns(lv) {
	try {
		if (!lv || !Array.isArray(lv.columns) || !lv.columns.length) return;
		const get_df = frappe.meta.get_docfield.bind(null, "Purchase Order");
		const fld = (fn) => {
			const df = get_df(fn);
			return df ? { type: "Field", df: df } : null;
		};
		const subject = lv.columns[0]; // Subject = title_field (supplier_name)
		const tag = (lv.columns[1] && lv.columns[1].type === "Tag") ? lv.columns[1] : { type: "Tag" };
		const status_col = lv.columns.find((c) => c && c.type === "Status") || { type: "Status" };
		const locked = [
			subject,
			tag,
			{ type: "Field", df: { label: __("ID"), fieldname: "name" } },
			status_col,
			fld("per_received"),
			fld("per_billed"),
			fld("ts_approval_status"),
			fld("total"),
			fld("cost_center"),
		].filter(Boolean);
		lv.columns = locked;
	} catch (e) {
		// Never let the column lock break list rendering — fall back to whatever
		// _orig_setup_columns produced.
	}
}

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
		// v2.17.4: drill to the EXACT status clicked (was `like,Pending%`, which
		// showed ALL pending statuses when you clicked one "Pending PM" badge).
		if (bbf && bbf.startsWith("Pending"))  return [bbf, "orange", "ts_approval_status,=," + bbf];
		if (bbf && bbf.startsWith("On Hold"))  return [bbf, "yellow", "ts_approval_status,=," + bbf];
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
		// v2.17.4: a manual "Approval Status" pick must win over the role-scoped
		// "My Pending" default — else the two filters AND to zero rows (e.g. Pending PM).
		if (typeof window.ts_reconcile_manual_status_filter === "function") {
			window.ts_reconcile_manual_status_filter(listview, "ts_approval_status");
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

function _fix_docstatus_for_approval_filter(listview) {
	try {
		const filters = listview.get_filters_for_args();
		const has_approval = filters.some(f =>
			f[1] === "ts_approval_status" && f[3] && f[3] !== "Approved"
		);
		if (has_approval) {
			// v2.28.5: only when a docstatus filter actually EXISTS. FilterArea
			// .remove() unconditionally runs filter_list.apply() -> on_change ->
			// debounced refresh -> this wrapper again: with the "My Pending"
			// `in [...]` filter permanently satisfying has_approval, an
			// unconditional remove() self-sustained a ~3s reportview.get poll
			// loop in every patched PO/MR tab, forever.
			const _fl = listview.filter_area && listview.filter_area.filter_list;
			if (_fl && typeof _fl.get_filter === "function" && _fl.get_filter("docstatus")) {
				// Remove docstatus filter so Draft (pending) POs show
				listview.filter_area.remove("docstatus");
			}
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

// v2.28.5 — export for po_list_hold.js's settings.onload/refresh chain (cold-load
// fix). frappe's list_factory.make() fires "page-change" DURING make_page()
// argument evaluation and assigns cur_list only AFTER the constructor returns, so
// the handler above misses the FIRST paint after any F5/hard-refresh — no toggle,
// no TS indicators — until the user navigates away and back. settings.onload
// (list_view.js:333) receives the instance directly; the chain lives in
// po_list_hold.js because doctype_list_js is evaluated AFTER erpnext's
// whole-object listview_settings assignment (L332). The window export is the
// explicit cross-file contract: po_list_hold.js runs in a separate evaluation
// context (`new Function(__list_js)`), where only globals are reachable — and
// it stays mandatory if this file ever moves into a .bundle.js.
window.ts_patch_po_list = _patch_po_list;
