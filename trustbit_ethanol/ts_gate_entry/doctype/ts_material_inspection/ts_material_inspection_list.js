// v2.8.3.2: list view indicator override — show inspection decision status
// instead of Frappe's default "Draft" label (non-submittable doctype).

frappe.listview_settings["TS Material Inspection"] = {
	get_indicator: function (doc) {
		const map = {
			"Pending Inspection":  ["Pending Inspection",  "orange", "status,=,Pending Inspection"],
			"Approved":            ["Approved",            "green",  "status,=,Approved"],
			"Partially Approved":  ["Partially Approved",  "yellow", "status,=,Partially Approved"],
			"On Hold":             ["On Hold",             "yellow", "status,=,On Hold"],
			"Rejected":            ["Rejected",            "red",    "status,=,Rejected"],
			"Auto-Proceeded":      ["Auto-Proceeded",      "blue",   "status,=,Auto-Proceeded"],
		};
		return map[doc.status] || ["Live", "grey", "status,is,set"];
	},
};
