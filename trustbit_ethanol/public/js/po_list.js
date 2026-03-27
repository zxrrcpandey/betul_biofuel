// BBF PO List View — reorder Approval Status before Status
frappe.listview_settings["Purchase Order"] = Object.assign(
	frappe.listview_settings["Purchase Order"] || {},
	{
		onload(listview) {
			_reorder_columns(listview, "bbf_approval_status", "status");
		},
		refresh(listview) {
			_reorder_columns(listview, "bbf_approval_status", "status");
		}
	}
);

function _reorder_columns(listview, move_field, before_field) {
	if (!listview || !listview.columns) return;
	const move_idx = listview.columns.findIndex(c => c.df && c.df.fieldname === move_field);
	const before_idx = listview.columns.findIndex(c => c.df && c.df.fieldname === before_field);
	if (move_idx < 0 || before_idx < 0 || move_idx <= before_idx) return;
	const col = listview.columns.splice(move_idx, 1)[0];
	listview.columns.splice(before_idx, 0, col);
}
