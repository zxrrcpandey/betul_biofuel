frappe.ui.form.on("BBF Budget Proposal", {
	refresh(frm) {
		_render_status_indicator(frm);
		_render_buttons(frm);
		_lock_fields(frm);
	},

	cost_center(frm) {
		if (frm.doc.cost_center && frm.doc.fiscal_year) {
			_fetch_last_year(frm);
		}
	},

	fiscal_year(frm) {
		if (frm.doc.cost_center && frm.doc.fiscal_year) {
			_fetch_last_year(frm);
		}
	}
});

function _render_status_indicator(frm) {
	const status = frm.doc.status;
	if (!status || status === "Draft") return;

	const colors = {
		"Pending CEO": "blue",
		"Approved": "green",
		"Revised": "orange",
		"Rejected": "red",
	};

	frm.page.set_indicator(status, colors[status] || "blue");
}

function _render_buttons(frm) {
	$(".bbf-budget-btn").remove();

	const status = frm.doc.status || "Draft";
	const user = frappe.session.user;
	const is_proposer = (user === frm.doc.proposed_by || user === frm.doc.owner);
	const is_ceo = frappe.user.has_role("CEO") || frappe.user.has_role("System Manager");

	// Fetch Last Year Data button (always available in Draft)
	if (status === "Draft" && frm.doc.cost_center && frm.doc.fiscal_year) {
		frm.add_custom_button(__("Fetch Last Year Data"), () => {
			_fetch_last_year(frm);
		}, null).addClass("bbf-budget-btn");
	}

	// Submit to CEO (DH in Draft)
	if (status === "Draft" && !frm.is_new()) {
		frm.add_custom_button(__("Submit to CEO"), () => {
			frappe.confirm(
				__("Submit this budget proposal to CEO for approval?"),
				() => {
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_budget.submit_budget_proposal",
						args: { proposal_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Submitting..."),
						callback() { frm.reload_doc(); },
						error() { frm.reload_doc(); }
					});
				}
			);
		}, null).addClass("btn-primary bbf-budget-btn");
	}

	// CEO actions (Pending CEO)
	if (status === "Pending CEO" && is_ceo) {
		frm.add_custom_button(__("Approve Budget"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Approve Budget Proposal"),
				fields: [
					{ fieldtype: "Small Text", fieldname: "comment", label: __("Comment (optional)") }
				],
				primary_action_label: __("Approve & Activate"),
				primary_action(values) {
					d.hide();
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_budget.approve_budget_proposal",
						args: { proposal_name: frm.doc.name, comment: values.comment || "" },
						freeze: true,
						freeze_message: __("Approving and creating budget..."),
						callback(r) {
							frm.reload_doc();
							if (r.message && r.message.budget_name) {
								frappe.show_alert({
									message: __("Budget {0} created and activated!", [r.message.budget_name]),
									indicator: "green"
								});
							}
						},
						error() { frm.reload_doc(); }
					});
				}
			});
			d.show();
		}, null).addClass("btn-success bbf-budget-btn");

		frm.add_custom_button(__("Revise"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Send Back for Revision"),
				fields: [
					{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), reqd: 1 }
				],
				primary_action_label: __("Send for Revision"),
				primary_action(values) {
					d.hide();
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_budget.revise_budget_proposal",
						args: { proposal_name: frm.doc.name, reason: values.reason },
						freeze: true,
						callback() { frm.reload_doc(); },
						error() { frm.reload_doc(); }
					});
				}
			});
			d.show();
		}, null).addClass("bbf-budget-btn");

		frm.add_custom_button(__("Reject"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Reject Budget Proposal"),
				fields: [
					{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), reqd: 1 }
				],
				primary_action_label: __("Reject"),
				primary_action(values) {
					d.hide();
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_budget.reject_budget_proposal",
						args: { proposal_name: frm.doc.name, reason: values.reason },
						freeze: true,
						callback() { frm.reload_doc(); },
						error() { frm.reload_doc(); }
					});
				}
			});
			d.show();
		}, null).addClass("bbf-budget-btn");
	}

	// Resubmit (proposer, after revision)
	if (status === "Revised" && is_proposer) {
		frm.add_custom_button(__("Resubmit to CEO"), () => {
			frappe.confirm(
				__("Resubmit this budget proposal to CEO?"),
				() => {
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_budget.resubmit_budget_proposal",
						args: { proposal_name: frm.doc.name },
						freeze: true,
						callback() { frm.reload_doc(); },
						error() { frm.reload_doc(); }
					});
				}
			);
		}, null).addClass("btn-primary bbf-budget-btn");
	}
}

function _lock_fields(frm) {
	const status = frm.doc.status || "Draft";

	if (status === "Approved" || status === "Rejected") {
		// Lock everything
		frm.set_df_property("cost_center", "read_only", 1);
		frm.set_df_property("fiscal_year", "read_only", 1);
		frm.set_df_property("monthly_distribution", "read_only", 1);
		frm.set_df_property("budget_items", "read_only", 1);
	} else if (status === "Pending CEO") {
		// DH fields locked, CEO can edit approved amounts
		frm.set_df_property("cost_center", "read_only", 1);
		frm.set_df_property("fiscal_year", "read_only", 1);
		// CEO can still edit ceo_approved_amount in the child table
	}
}

function _fetch_last_year(frm) {
	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_budget.fetch_last_year_data",
		args: {
			cost_center: frm.doc.cost_center,
			fiscal_year: frm.doc.fiscal_year
		},
		freeze: true,
		freeze_message: __("Fetching last year data..."),
		callback(r) {
			if (!r.message) return;
			const data = r.message;

			frm.set_value("last_year_fiscal", data.last_year_fiscal || "");
			frm.set_value("last_year_total_budget", data.last_year_total_budget || 0);
			frm.set_value("last_year_total_actual", data.last_year_total_actual || 0);
			frm.set_value("last_year_utilization_pct", data.last_year_utilization_pct || 0);

			// Pre-fill budget items from last year accounts
			if (data.accounts && data.accounts.length && (!frm.doc.budget_items || !frm.doc.budget_items.length)) {
				frm.clear_table("budget_items");
				data.accounts.forEach(acc => {
					const row = frm.add_child("budget_items");
					row.account = acc.account;
					row.account_name = acc.account_name;
					row.last_year_budget = acc.last_year_budget;
					row.last_year_actual = acc.last_year_actual;
					row.proposed_amount = acc.last_year_budget; // Default: same as last year
					row.ceo_approved_amount = acc.last_year_budget;
					row.variance_pct = 0;
				});
				frm.refresh_field("budget_items");
				frappe.show_alert({
					message: __("{0} accounts pre-filled from {1}", [data.accounts.length, data.last_year_fiscal]),
					indicator: "green"
				});
			}
		},
		error() {
			frappe.show_alert({ message: __("Could not fetch last year data"), indicator: "orange" });
		}
	});
}
