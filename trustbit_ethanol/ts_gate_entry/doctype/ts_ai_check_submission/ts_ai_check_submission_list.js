// The list shows where each check stands. Without this a submitted list would only say "Submitted"
// and a draft "Draft" in red, which tells the submitter nothing about the check.

frappe.listview_settings["TS AI Check Submission"] = {
	add_fields: ["status", "docstatus"],
	has_indicator_for_draft: 1,
	get_indicator(doc) {
		const colour = {
			Draft: "grey",
			"Being checked": "orange",
			"Report sent": "green",
			"Could not be read": "red",
			"Check failed": "red",
		}[doc.status];
		if (colour) return [__(doc.status), colour, "status,=," + doc.status];
	},
};
