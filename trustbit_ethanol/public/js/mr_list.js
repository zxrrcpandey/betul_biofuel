// BBF MR List View — swap Approval Status before Status column
frappe.listview_settings["Material Request"] = frappe.listview_settings["Material Request"] || {};

const orig_mr_onload = frappe.listview_settings["Material Request"].onload;
frappe.listview_settings["Material Request"].onload = function (listview) {
	if (orig_mr_onload) orig_mr_onload(listview);
	_swap_mr_approval_column(listview, "bbf_mr_status", "status");
};

const orig_mr_refresh = frappe.listview_settings["Material Request"].refresh;
frappe.listview_settings["Material Request"].refresh = function (listview) {
	if (orig_mr_refresh) orig_mr_refresh(listview);
	_swap_mr_approval_column(listview, "bbf_mr_status", "status");
};

function _swap_mr_approval_column(listview, approval_field, status_field) {
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
