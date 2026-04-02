frappe.ui.form.on("TS Settings", {
	refresh(frm) {
		// Hide Approval Settings entirely for non-CEO/MD/System Manager
		const allowed = ["CEO", "MD", "System Manager"];
		const can_see = frappe.user_roles.some(r => allowed.includes(r));
		if (!can_see) {
			frm.set_df_property("post_dated_approval_section", "hidden", 1);
			frm.set_df_property("require_ceo_approval_post_dated", "hidden", 1);
		}
	}
});
