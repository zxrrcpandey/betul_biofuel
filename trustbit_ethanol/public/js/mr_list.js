// BBF MR List — Override indicator to show Approval Status
$(document).on("page-change", function() {
	if (cur_list && cur_list.doctype === "Material Request") {
		_patch_mr_list(cur_list);
	}
});

function _patch_mr_list(listview) {
	if (listview._bbf_patched) return;
	listview._bbf_patched = true;

	const orig = listview.settings.get_indicator;

	listview.settings.get_indicator = function(doc) {
		const bbf = doc.bbf_mr_status;
		if (bbf && bbf !== "Approved") {
			if (bbf.startsWith("Pending")) return [bbf, "orange", "bbf_mr_status,like,Pending%"];
			if (bbf === "Rejected") return [bbf, "red", "bbf_mr_status,=,Rejected"];
			if (bbf === "Revised") return [bbf, "orange", "bbf_mr_status,=,Revised"];
			if (bbf.startsWith("On Hold")) return [bbf, "yellow", "bbf_mr_status,like,On Hold%"];
		}
		if (orig) return orig(doc);
	};

	if (!listview.settings.add_fields) listview.settings.add_fields = [];
	if (!listview.settings.add_fields.includes("bbf_mr_status")) {
		listview.settings.add_fields.push("bbf_mr_status");
	}
}
