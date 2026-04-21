frappe.ui.form.on("TS Post Dated Entry Request", {
	refresh(frm) {
		// Hide standard Save for non-Draft
		if (!frm.is_new() && frm.doc.status !== "Draft") {
			frm.disable_save();
		}

		// Status indicator
		const colors = {
			"Draft": "grey", "Pending Approval": "orange", "Active": "green",
			"Approved": "green", "Rejected": "red", "Expired": "darkgrey"
		};
		frm.page.set_indicator(frm.doc.status, colors[frm.doc.status] || "grey");

		// ── IT Head buttons ──
		if (frm.doc.status === "Draft" && !frm.is_new()) {
			frappe.db.get_single_value("TS Settings", "require_ceo_approval_post_dated").then(val => {
				const require_approval = cint(val);
				const btn_label = require_approval ? __("Submit for CEO Approval") : __("Activate Now");
				const confirm_msg = require_approval
					? __("Submit this request for CEO approval?")
					: __("Activate post-dated entry now? (No CEO approval required)");

				frm.add_custom_button(btn_label, () => {
					frappe.confirm(confirm_msg, () => {
						frappe.call({
							method: "trustbit_ethanol.ts_gate_entry.ts_post_dated.submit_request",
							args: { request_name: frm.doc.name },
							callback: () => frm.reload_doc(),
						});
					});
				}, null).addClass("btn-primary");
			});
		}

		// ── CEO buttons ──
		if (frm.doc.status === "Pending Approval") {
			if (frappe.user_roles.includes("CEO") || frappe.user_roles.includes("System Manager")) {
				frm.add_custom_button(__("Approve"), () => {
					frappe.confirm(
						__("Approve this post-dated entry request?"),
						() => {
							frappe.call({
								method: "trustbit_ethanol.ts_gate_entry.ts_post_dated.approve_request",
								args: { request_name: frm.doc.name },
								callback: () => frm.reload_doc(),
							});
						}
					);
				}, null).addClass("btn-success");

				frm.add_custom_button(__("Reject"), () => {
					frappe.prompt(
						{ fieldtype: "Small Text", label: "Rejection Reason", reqd: 1 },
						(values) => {
							frappe.call({
								method: "trustbit_ethanol.ts_gate_entry.ts_post_dated.reject_request",
								args: { request_name: frm.doc.name, reason: values.value },
								callback: () => frm.reload_doc(),
							});
						},
						__("Reject Post-Dated Entry Request")
					);
				}, null).addClass("btn-danger");
			}
		}

		// ── Active banner ──
		if (frm.doc.status === "Active") {
			frm.dashboard.set_headline(
				__("Active until {0}", [frappe.datetime.str_to_user(frm.doc.valid_until)]),
				"green"
			);
		}

		// ── Expired banner ──
		if (frm.doc.status === "Expired") {
			frm.dashboard.set_headline(
				__("This request expired on {0}", [frappe.datetime.str_to_user(frm.doc.valid_until)]),
				"darkgrey"
			);
		}

		// ── Rejected banner ──
		if (frm.doc.status === "Rejected") {
			frm.dashboard.set_headline(
				__("Rejected: {0}", [frm.doc.rejection_reason || "No reason given"]),
				"red"
			);
		}

		// v2.8.11 Phase 2: bilingual approval banner
		if (typeof window.ts_render_approval_banner === "function") {
			window.ts_render_approval_banner(frm);
		}
	},

	before_submit(frm) {
		// v2.8.11 Phase 2: submit-on-behalf warning
		if (typeof window.ts_check_submit_on_behalf === "function") {
			return window.ts_check_submit_on_behalf(frm).then(proceed => {
				if (!proceed) {
					frappe.validated = false;
					return Promise.reject();
				}
			});
		}
	},

	request_type(frm) {
		// Clear conditional fields when type changes
		if (frm.doc.request_type !== "Transaction-wise") {
			frm.set_value("token_number", "");
		}
		if (frm.doc.request_type !== "DocType-wise") {
			frm.set_value("allowed_doctypes", []);
		}
	},
});
