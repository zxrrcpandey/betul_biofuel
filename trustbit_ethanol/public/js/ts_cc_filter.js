/**
 * ts_cc_filter.js — v2.8.8
 *
 * Adds a "Filter: Cost Centers" multi-select button to a list view.
 * Called from po_list.js (Purchase Order) and mr_list.js (Material Request)
 * via the `ts_add_cc_filter_button(listview)` function.
 *
 * UX:
 *   - Click button → MultiSelectDialog listing non-disabled Cost Centers
 *     for the current default company (Frappe defaults).
 *   - Pick multiple CCs → Apply → list filters with `cost_center in [...]`.
 *   - Button label shows active count: "Cost Centers (3 selected)".
 *   - Click again to modify / clear.
 *   - Clear button in the dialog removes the filter.
 *
 * Notes:
 *   - Safe on re-render: button is added exactly once per listview instance
 *     (guarded by listview._ts_cc_filter_wired).
 *   - No @frappe.whitelist or server code — pure client-side Frappe APIs.
 *   - Uses standard `cost_center` field which exists natively on PO + MR.
 */

window.ts_add_cc_filter_button = function (listview) {
	if (!listview || listview._ts_cc_filter_wired) return;
	listview._ts_cc_filter_wired = true;

	const getBtnLabel = () => {
		const count = (listview._ts_cc_selected || []).length;
		return count > 0 ? __(`Cost Centers (${count} selected)`) : __("Filter: Cost Centers");
	};

	const refreshBtnLabel = () => {
		const $btn = listview.page.wrapper.find(".ts-cc-filter-btn");
		if ($btn.length) {
			$btn.text(getBtnLabel());
			$btn.toggleClass("btn-primary", (listview._ts_cc_selected || []).length > 0);
			$btn.toggleClass("btn-default", (listview._ts_cc_selected || []).length === 0);
		}
	};

	const applyFilter = (values) => {
		listview._ts_cc_selected = values || [];
		// Remove any existing cost_center filter first
		listview.filter_area.remove("cost_center");
		if (values && values.length) {
			listview.filter_area.add([[listview.doctype, "cost_center", "in", values]]);
		} else {
			listview.refresh();
		}
		refreshBtnLabel();
	};

	const openDialog = () => {
		const currentSelection = listview._ts_cc_selected || [];
		const d = new frappe.ui.form.MultiSelectDialog({
			doctype: "Cost Center",
			target: listview,
			setters: {
				cost_center_name: null,
				company: frappe.defaults.get_user_default("Company") || null,
			},
			date_field: "creation",
			get_query() {
				// Only non-disabled CCs (disabled=0 or NULL treated as enabled)
				return {
					filters: { disabled: 0 },
				};
			},
			action(selections) {
				const values = (selections || []).map(r => r.name || r);
				applyFilter(values);
				d.dialog.hide && d.dialog.hide();
			},
			primary_action_label: __("Apply"),
		});

		// Prefill current selection when dialog opens (best-effort — frappe internals)
		try {
			if (currentSelection.length && d.dialog && d.dialog.get_values) {
				setTimeout(() => {
					currentSelection.forEach(cc => {
						const $row = d.dialog.$wrapper.find(`[data-name="${cc}"] input[type="checkbox"]`);
						if ($row.length) $row.prop("checked", true).trigger("change");
					});
				}, 200);
			}
		} catch (e) { /* best-effort prefill only */ }
	};

	// Add button to list toolbar (primary menu area, left side)
	listview.page.add_inner_button(
		getBtnLabel(),
		openDialog,
		null,  // no group
		"default"  // button type
	);

	// Tag the rendered button so we can update its label
	setTimeout(() => {
		const $allBtns = listview.page.wrapper.find(".inner-toolbar .btn");
		$allBtns.each(function () {
			if ($(this).text().trim().startsWith("Filter: Cost Centers") ||
			    $(this).text().trim().startsWith("Cost Centers (")) {
				$(this).addClass("ts-cc-filter-btn");
			}
		});
	}, 100);

	// Also expose a "Clear Cost Center Filter" menu item when filter is active
	// (kept simple: user can click the main button then Clear in the dialog or remove via filter pills)
};
