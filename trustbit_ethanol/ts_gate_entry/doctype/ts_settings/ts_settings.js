frappe.ui.form.on("TS Settings", {
	refresh(frm) {
		// Lock CEO Approval checkbox for non-CEO/MD/System Manager
		const allowed = ["CEO", "MD", "System Manager"];
		const can_change = frappe.user_roles.some(r => allowed.includes(r));
		if (!can_change) {
			frm.set_df_property("require_ceo_approval_post_dated", "read_only", 1);
		}
	}
});
