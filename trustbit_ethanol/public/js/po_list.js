// BBF PO List — Override indicator to show Approval Status
$(document).on("page-change", function() {
	if (cur_list && cur_list.doctype === "Purchase Order") {
		_patch_po_list(cur_list);
	}
});

function _patch_po_list(listview) {
	if (listview._bbf_patched) return;
	listview._bbf_patched = true;

	// Store original get_indicator
	const orig = listview.settings.get_indicator;

	listview.settings.get_indicator = function(doc) {
		const bbf = doc.bbf_approval_status;
		if (bbf && bbf !== "Approved") {
			// Show BBF status as primary indicator for non-approved
			if (bbf.startsWith("Pending")) return [bbf, "orange", "bbf_approval_status,like,Pending%"];
			if (bbf === "Rejected") return [bbf, "red", "bbf_approval_status,=,Rejected"];
			if (bbf === "Revised") return [bbf, "orange", "bbf_approval_status,=,Revised"];
			if (bbf.startsWith("On Hold")) return [bbf, "yellow", "bbf_approval_status,like,On Hold%"];
		}
		// For Approved or no BBF status, use original ERPNext indicator
		if (orig) return orig(doc);
	};

	// Add bbf_approval_status to fetched fields
	if (!listview.settings.add_fields) listview.settings.add_fields = [];
	if (!listview.settings.add_fields.includes("bbf_approval_status")) {
		listview.settings.add_fields.push("bbf_approval_status");
	}
}
