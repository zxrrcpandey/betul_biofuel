frappe.ui.form.on("TS Approval Limit", {
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
