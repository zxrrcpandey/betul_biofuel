/**
 * BBF Material Request Approval — Client Script
 *
 * Injected via doctype_js hook. Single-level approval:
 *   Draft → Pending Department Head → Approved / Revised / Rejected
 */

frappe.ui.form.on("Material Request", {
	refresh(frm) {
		if (frm.is_new()) return;
		_load_mr_context(frm);
	},
});


function _load_mr_context(frm) {
	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.get_approval_context",
		args: { doctype: "Material Request", docname: frm.doc.name },
		callback(r) {
			if (!r.message || !r.message.approval_enabled) return;
			var ctx = r.message;

			_render_mr_status(frm, ctx);
			_render_mr_buttons(frm, ctx);
		}
	});
}


function _render_mr_status(frm, ctx) {
	var status = ctx.status || "Draft";
	if (!status || status === "Draft") return;

	var color = "blue";
	if (status === "Approved") color = "green";
	else if (status === "Rejected") color = "red";
	else if (status === "Revised") color = "orange";
	else if (status === "Pending Department Head") color = "yellow";

	frm.dashboard.set_headline_alert(
		'<span class="indicator-pill ' + color + '">' +
			frappe.utils.escape_html(status) +
		"</span>"
	);
}


function _render_mr_buttons(frm, ctx) {
	// Submit for Approval
	if (ctx.can_submit_for_approval) {
		frm.add_custom_button(__("Submit for Approval"), function () {
			frappe.confirm(
				__("Submit this Material Request for Department Head approval?"),
				function () {
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.submit_for_approval",
						args: { doctype: "Material Request", docname: frm.doc.name },
						callback: function () {
							frm.reload_doc();
							frappe.show_alert({ message: __("MR submitted for approval"), indicator: "blue" });
						}
					});
				}
			);
		}, __("Approval"));
		frm.change_custom_button_type(__("Submit for Approval"), __("Approval"), "primary");
	}

	// Approve
	if (ctx.can_approve) {
		frm.add_custom_button(__("Approve"), function () {
			var d = new frappe.ui.Dialog({
				title: __("Approve Material Request"),
				fields: [{
					fieldname: "comment",
					fieldtype: "Small Text",
					label: __("Comment (optional)"),
				}],
				primary_action_label: __("Approve"),
				primary_action: function (values) {
					d.hide();
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.approve_document",
						args: { doctype: "Material Request", docname: frm.doc.name, comment: values.comment || "" },
						callback: function () {
							frm.reload_doc();
							frappe.show_alert({ message: __("MR approved!"), indicator: "green" });
						}
					});
				}
			});
			d.show();
		}, __("Approval"));
		frm.change_custom_button_type(__("Approve"), __("Approval"), "success");
	}

	// Revise
	if (ctx.can_revise) {
		frm.add_custom_button(__("Revise"), function () {
			var d = new frappe.ui.Dialog({
				title: __("Revise Material Request"),
				fields: [
					{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Revision Reason"),
						reqd: 1,
					},
					{
						fieldname: "comment",
						fieldtype: "Small Text",
						label: __("Additional Comment"),
					}
				],
				primary_action_label: __("Send Back"),
				primary_action: function (values) {
					if (!values.reason) {
						frappe.throw(__("Revision reason is mandatory"));
						return;
					}
					d.hide();
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.revise_document",
						args: {
							doctype: "Material Request",
							docname: frm.doc.name,
							reason: values.reason,
							comment: values.comment || "",
						},
						callback: function () {
							frm.reload_doc();
							frappe.show_alert({ message: __("MR sent back for revision"), indicator: "orange" });
						}
					});
				}
			});
			d.show();
		}, __("Approval"));
	}

	// Reject
	if (ctx.can_reject) {
		frm.add_custom_button(__("Reject"), function () {
			var d = new frappe.ui.Dialog({
				title: __("Reject Material Request"),
				fields: [{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Rejection Reason"),
					reqd: 1,
				}],
				primary_action_label: __("Reject"),
				primary_action: function (values) {
					if (!values.reason) {
						frappe.throw(__("Rejection reason is mandatory"));
						return;
					}
					d.hide();
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.reject_document",
						args: {
							doctype: "Material Request",
							docname: frm.doc.name,
							reason: values.reason,
						},
						callback: function () {
							frm.reload_doc();
							frappe.show_alert({ message: __("MR rejected"), indicator: "red" });
						}
					});
				}
			});
			d.show();
		}, __("Approval"));
	}

	// Resubmit
	if (ctx.can_resubmit) {
		frm.add_custom_button(__("Resubmit for Approval"), function () {
			frappe.confirm(
				__("Resubmit this Material Request for Department Head approval?"),
				function () {
					frappe.call({
						method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.resubmit_document",
						args: { doctype: "Material Request", docname: frm.doc.name },
						callback: function () {
							frm.reload_doc();
							frappe.show_alert({ message: __("MR resubmitted"), indicator: "blue" });
						}
					});
				}
			);
		}, __("Approval"));
		frm.change_custom_button_type(__("Resubmit for Approval"), __("Approval"), "primary");
	}
}
