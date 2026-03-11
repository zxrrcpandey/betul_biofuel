frappe.ui.form.on("BBF Approval Limit", {
	refresh(frm) {
		frm.set_query("role", function () {
			return {
				filters: {
					disabled: 0
				}
			};
		});
	}
});
