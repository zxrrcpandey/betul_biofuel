// v2.8.3.2: list view indicator override — show the real business status
// instead of Frappe's default "Draft" label (this doctype is docstatus=0 forever).
// Fallback label "Live" for any row without a status value.

frappe.listview_settings["TS Weighbridge Log"] = {
	get_indicator: function (doc) {
		const map = {
			"Gross Recorded":       ["Gross Recorded",       "blue",   "status,=,Gross Recorded"],
			"Awaiting Unloading":   ["Awaiting Unloading",   "orange", "status,=,Awaiting Unloading"],
			"Awaiting Tare Weight": ["Awaiting Tare Weight", "yellow", "status,=,Awaiting Tare Weight"],
			"Awaiting Tare":        ["Awaiting Tare",        "yellow", "status,=,Awaiting Tare"],
			"Awaiting Loading":     ["Awaiting Loading",     "yellow", "status,=,Awaiting Loading"],
			"Completed":            ["Completed",            "green",  "status,=,Completed"],
		};
		return map[doc.status] || ["Live", "grey", "status,is,set"];
	},
};
