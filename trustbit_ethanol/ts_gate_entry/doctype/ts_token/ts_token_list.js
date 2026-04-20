// v2.8.3.2: list view indicator override — show token lifecycle status
// instead of Frappe's default "Draft" label (non-submittable doctype).
// Covers all token states from v2.5 through v2.8.3.

frappe.listview_settings["TS Token"] = {
	get_indicator: function (doc) {
		const map = {
			// Early states
			"Token Generated": ["Token Generated", "grey",   "status,=,Token Generated"],
			"PO Linked":       ["PO Linked",       "blue",   "status,=,PO Linked"],
			"SI Linked":       ["SI Linked",       "blue",   "status,=,SI Linked"],

			// v2.8.3 two-pass gates
			"G1 Entered":      ["G1 Entered",      "blue",   "status,=,G1 Entered"],
			"G2 Entered":      ["G2 Entered",      "blue",   "status,=,G2 Entered"],

			// Weighing
			"Gross Weighed":   ["Gross Weighed",   "orange", "status,=,Gross Weighed"],
			"Tare Weighed":    ["Tare Weighed",    "orange", "status,=,Tare Weighed"],
			"Gross Recorded":  ["Gross Recorded",  "orange", "status,=,Gross Recorded"],
			"Tare Recorded":   ["Tare Recorded",   "orange", "status,=,Tare Recorded"],
			"Loading Done":    ["Loading Done",    "orange", "status,=,Loading Done"],
			"Dispatch Ready":  ["Dispatch Ready",  "orange", "status,=,Dispatch Ready"],

			// QC / Inspection (optional states)
			"Quality Done":    ["Quality Done",    "yellow", "status,=,Quality Done"],
			"Graded":          ["Graded",          "yellow", "status,=,Graded"],
			"Unloading":       ["Unloading",       "yellow", "status,=,Unloading"],

			// Completion
			"GRN Created":     ["GRN Created",     "green",  "status,=,GRN Created"],

			// v2.8.3 exit states
			"Plant Exited":    ["Plant Exited",    "purple", "status,=,Plant Exited"],
			"Campus Exited":   ["Campus Exited",   "green",  "status,=,Campus Exited"],

			// Legacy terminal state (pre-v2.8.3 rename)
			"Exited":          ["Exited",          "green",  "status,=,Exited"],
		};
		return map[doc.status] || ["Live", "grey", "status,is,set"];
	},
};
