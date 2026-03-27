// BBF PO List View — swap Approval Status before Status column
frappe.listview_settings["Purchase Order"] = frappe.listview_settings["Purchase Order"] || {};

const orig_po_onload = frappe.listview_settings["Purchase Order"].onload;
frappe.listview_settings["Purchase Order"].onload = function (listview) {
	if (orig_po_onload) orig_po_onload(listview);
	_swap_approval_column(listview, "bbf_approval_status", "status");
};

const orig_po_refresh = frappe.listview_settings["Purchase Order"].refresh;
frappe.listview_settings["Purchase Order"].refresh = function (listview) {
	if (orig_po_refresh) orig_po_refresh(listview);
	_swap_approval_column(listview, "bbf_approval_status", "status");
};

function _swap_approval_column(listview, approval_field, status_field) {
	if (!listview || !listview.columns) return;
	const approval_idx = listview.columns.findIndex(c => c.df && c.df.fieldname === approval_field);
	const status_idx = listview.columns.findIndex(c => c.df && c.df.fieldname === status_field);
	if (approval_idx > status_idx && status_idx >= 0) {
		const temp = listview.columns[approval_idx];
		listview.columns[approval_idx] = listview.columns[status_idx];
		listview.columns[status_idx] = temp;
		listview.render();
	}
}
