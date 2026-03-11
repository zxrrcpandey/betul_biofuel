/**
 * BBF Purchase Order Approval — Client Script
 *
 * Injected via doctype_js hook. Manages:
 *   - Approval stepper progress indicator
 *   - Action buttons (Submit, Approve, Forward, Revise, Reject, Resubmit)
 *   - Field locking during approval
 *   - Approval history timeline
 */

frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		if (frm.is_new()) return;
		_load_approval_context(frm);
	},
});


function _load_approval_context(frm) {
	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.get_approval_context",
		args: { doctype: "Purchase Order", docname: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			var ctx = r.message;
			if (!ctx.approval_enabled) return;

			_render_stepper(frm, ctx);
			_render_amount_info(frm, ctx);
			_render_buttons(frm, ctx);
			_lock_fields(frm, ctx);
			_render_timeline(frm);
		}
	});
}


// ── Stepper ──────────────────────────────────────────────────────────

function _render_stepper(frm, ctx) {
	var chain = ctx.approval_chain || [];
	if (!chain.length) return;

	// Remove previous stepper
	frm.fields_dict.bbf_approval_section &&
		$(frm.fields_dict.bbf_approval_section.wrapper).find(".bbf-stepper").remove();

	var steps_html = chain.map(function (step) {
		var icon, cls;
		if (step.status === "approved") {
			icon = "&#10003;";
			cls = "bbf-step--done";
		} else if (step.status === "current") {
			icon = step.level;
			cls = "bbf-step--active";
		} else {
			icon = step.level;
			cls = "bbf-step--pending";
		}

		var detail = "";
		if (step.by) {
			detail = '<div class="bbf-step__detail">' + frappe.utils.escape_html(step.by) + "</div>";
		}

		return (
			'<div class="bbf-step ' + cls + '">' +
				'<div class="bbf-step__circle">' + icon + "</div>" +
				'<div class="bbf-step__label">' + frappe.utils.escape_html(step.role) + "</div>" +
				detail +
			"</div>"
		);
	}).join('<div class="bbf-step__line"></div>');

	var status = ctx.status || "Draft";
	var badge_cls = "blue";
	if (status === "Approved") badge_cls = "green";
	else if (status === "Rejected") badge_cls = "red";
	else if (status === "Revised") badge_cls = "orange";
	else if (status.startsWith("Pending")) badge_cls = "yellow";

	var stepper = $(
		'<div class="bbf-stepper">' +
			'<div class="bbf-stepper__header">' +
				'<span class="indicator-pill ' + badge_cls + '">' + frappe.utils.escape_html(status) + "</span>" +
			"</div>" +
			'<div class="bbf-stepper__steps">' + steps_html + "</div>" +
		"</div>"
	);

	// Insert after the section break
	if (frm.fields_dict.bbf_approval_section) {
		$(frm.fields_dict.bbf_approval_section.wrapper).prepend(stepper);
	}

	// Inject CSS if not already present
	if (!document.getElementById("bbf-stepper-css")) {
		var style = document.createElement("style");
		style.id = "bbf-stepper-css";
		style.textContent =
			".bbf-stepper { padding: 15px; margin-bottom: 10px; }" +
			".bbf-stepper__header { margin-bottom: 12px; }" +
			".bbf-stepper__steps { display: flex; align-items: flex-start; justify-content: center; flex-wrap: wrap; gap: 0; }" +
			".bbf-step { display: flex; flex-direction: column; align-items: center; min-width: 90px; }" +
			".bbf-step__circle { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center;" +
			"  justify-content: center; font-weight: bold; font-size: 14px; border: 2px solid var(--gray-400);" +
			"  color: var(--gray-500); background: var(--gray-100); }" +
			".bbf-step--done .bbf-step__circle { background: var(--green-500); border-color: var(--green-500); color: #fff; }" +
			".bbf-step--active .bbf-step__circle { background: var(--blue-500); border-color: var(--blue-500); color: #fff;" +
			"  animation: bbf-pulse 1.5s infinite; }" +
			"@keyframes bbf-pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(37,139,239,0.4); } 50% { box-shadow: 0 0 0 8px rgba(37,139,239,0); } }" +
			".bbf-step__label { margin-top: 6px; font-size: 11px; color: var(--gray-600); text-align: center; max-width: 100px; }" +
			".bbf-step__detail { font-size: 10px; color: var(--green-600); text-align: center; }" +
			".bbf-step__line { width: 40px; height: 2px; background: var(--gray-300); margin-top: 18px; flex-shrink: 0; }" +
			".bbf-step--done + .bbf-step__line { background: var(--green-500); }";
		document.head.appendChild(style);
	}
}


// ── Amount Info ──────────────────────────────────────────────────────

function _render_amount_info(frm, ctx) {
	if (!ctx.required_level) return;

	var chain = ctx.approval_chain || [];
	var max_role = "";
	for (var i = 0; i < chain.length; i++) {
		if (chain[i].level === ctx.required_level) {
			max_role = chain[i].role;
			break;
		}
	}

	var amount_str = format_currency(frm.doc.grand_total || 0);
	var msg = amount_str + " — Requires " + frappe.utils.escape_html(max_role) + " approval (Level " + ctx.required_level + ")";

	frm.dashboard.set_headline_alert(
		'<span style="font-size:13px;">' + msg + "</span>"
	);
}


// ── Action Buttons ───────────────────────────────────────────────────

function _render_buttons(frm, ctx) {
	// Submit for Approval
	if (ctx.can_submit_for_approval) {
		frm.add_custom_button(__("Submit for Approval"), function () {
			frappe.confirm(
				__("Submit this PO for approval? It will be sent to {0}.", [__("Department Head")]),
				function () { _call_action(frm, "submit_for_approval", {}); }
			);
		}, __("Approval"));
		frm.change_custom_button_type(__("Submit for Approval"), __("Approval"), "primary");
	}

	// Approve & Forward
	if (ctx.can_approve && !ctx.can_final_approve) {
		frm.add_custom_button(__("Approve & Forward"), function () {
			_show_comment_dialog(frm, "Approve & Forward", function (comment) {
				_call_action(frm, "approve_document", { comment: comment });
			});
		}, __("Approval"));
		frm.change_custom_button_type(__("Approve & Forward"), __("Approval"), "primary");
	}

	// Final Approve
	if (ctx.can_final_approve) {
		frm.add_custom_button(__("Final Approve"), function () {
			_show_comment_dialog(frm, "Final Approve", function (comment) {
				_call_action(frm, "approve_document", { comment: comment });
			});
		}, __("Approval"));
		frm.change_custom_button_type(__("Final Approve"), __("Approval"), "success");
	}

	// Revise
	if (ctx.can_revise) {
		frm.add_custom_button(__("Revise"), function () {
			_show_revise_dialog(frm, ctx);
		}, __("Approval"));
	}

	// Reject
	if (ctx.can_reject) {
		frm.add_custom_button(__("Reject"), function () {
			_show_reject_dialog(frm);
		}, __("Approval"));
	}

	// Resubmit
	if (ctx.can_resubmit) {
		frm.add_custom_button(__("Resubmit for Approval"), function () {
			_show_resubmit_dialog(frm);
		}, __("Approval"));
		frm.change_custom_button_type(__("Resubmit for Approval"), __("Approval"), "primary");
	}
}


function _show_comment_dialog(frm, title, callback) {
	var d = new frappe.ui.Dialog({
		title: __(title),
		fields: [
			{
				fieldname: "comment",
				fieldtype: "Small Text",
				label: __("Comment (optional)"),
			}
		],
		primary_action_label: __(title),
		primary_action: function (values) {
			d.hide();
			callback(values.comment || "");
		}
	});
	d.show();
}


function _show_revise_dialog(frm, ctx) {
	var fields = [
		{
			fieldname: "reason",
			fieldtype: "Small Text",
			label: __("Revision Reason"),
			reqd: 1,
		}
	];

	// Add revise-to dropdown if options exist
	if (ctx.revise_to_options && ctx.revise_to_options.length > 0) {
		var options = ctx.revise_to_options.map(function (o) {
			return o.label;
		});
		fields.push({
			fieldname: "revise_to",
			fieldtype: "Select",
			label: __("Send Back To"),
			options: options,
			default: options[0],
		});
	}

	fields.push({
		fieldname: "comment",
		fieldtype: "Small Text",
		label: __("Additional Comment"),
	});

	var d = new frappe.ui.Dialog({
		title: __("Revise Purchase Order"),
		fields: fields,
		primary_action_label: __("Send Back"),
		primary_action: function (values) {
			if (!values.reason) {
				frappe.throw(__("Revision reason is mandatory"));
				return;
			}
			d.hide();

			var revise_to_level = null;
			if (values.revise_to && ctx.revise_to_options) {
				for (var i = 0; i < ctx.revise_to_options.length; i++) {
					if (ctx.revise_to_options[i].label === values.revise_to) {
						revise_to_level = ctx.revise_to_options[i].level;
						break;
					}
				}
			}

			frappe.call({
				method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.revise_document",
				args: {
					doctype: "Purchase Order",
					docname: frm.doc.name,
					reason: values.reason,
					revise_to_level: revise_to_level,
					comment: values.comment || "",
				},
				callback: function () {
					frm.reload_doc();
					frappe.show_alert({ message: __("PO sent back for revision"), indicator: "orange" });
				}
			});
		}
	});
	d.show();
}


function _show_reject_dialog(frm) {
	var d = new frappe.ui.Dialog({
		title: __("Reject Purchase Order"),
		fields: [
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Rejection Reason"),
				reqd: 1,
			},
			{
				fieldname: "comment",
				fieldtype: "Small Text",
				label: __("Additional Comment"),
			}
		],
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
					doctype: "Purchase Order",
					docname: frm.doc.name,
					reason: values.reason,
					comment: values.comment || "",
				},
				callback: function () {
					frm.reload_doc();
					frappe.show_alert({ message: __("PO has been rejected"), indicator: "red" });
				}
			});
		}
	});
	d.show();
}


function _show_resubmit_dialog(frm) {
	var d = new frappe.ui.Dialog({
		title: __("Resubmit for Approval"),
		fields: [
			{
				fieldname: "mode",
				fieldtype: "Select",
				label: __("Resubmit Mode"),
				options: "Restart from Department Head\nRe-enter at reviser level",
				default: "Restart from Department Head",
				reqd: 1,
			}
		],
		primary_action_label: __("Resubmit"),
		primary_action: function (values) {
			d.hide();
			var mode = values.mode === "Restart from Department Head" ? "restart" : "reenter";
			frappe.call({
				method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.resubmit_document",
				args: {
					doctype: "Purchase Order",
					docname: frm.doc.name,
					mode: mode,
				},
				callback: function () {
					frm.reload_doc();
					frappe.show_alert({ message: __("PO resubmitted for approval"), indicator: "blue" });
				}
			});
		}
	});
	d.show();
}


// ── API Call Helper ──────────────────────────────────────────────────

function _call_action(frm, method, args) {
	args.doctype = "Purchase Order";
	args.docname = frm.doc.name;

	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval." + method,
		args: args,
		callback: function (r) {
			frm.reload_doc();
			if (r.message && r.message.status === "approved") {
				frappe.show_alert({ message: __("PO has been approved and submitted!"), indicator: "green" });
			} else if (r.message && r.message.status === "forwarded") {
				frappe.show_alert({ message: __("PO forwarded to next approver"), indicator: "blue" });
			} else {
				frappe.show_alert({ message: __("Action completed"), indicator: "blue" });
			}
		}
	});
}


// ── Field Locking ────────────────────────────────────────────────────

function _lock_fields(frm, ctx) {
	if (!ctx.is_pending) return;

	// Lock all editable PO fields during approval
	var fields_to_lock = [
		"supplier", "supplier_name", "schedule_date", "transaction_date",
		"currency", "buying_price_list", "price_list_currency",
		"plc_conversion_rate", "conversion_rate",
		"tc_name", "terms", "taxes_and_charges",
		"apply_discount_on", "additional_discount_percentage",
		"discount_amount", "payment_terms_template",
	];

	fields_to_lock.forEach(function (f) {
		frm.set_df_property(f, "read_only", 1);
	});

	// Lock items table
	frm.set_df_property("items", "read_only", 1);
	frm.set_df_property("taxes", "read_only", 1);
}


// ── Approval Timeline ───────────────────────────────────────────────

function _render_timeline(frm) {
	var logs = frm.doc.bbf_approval_log || [];
	if (!logs.length) return;

	// Remove previous timeline
	$(frm.fields_dict.bbf_approval_log_section &&
		frm.fields_dict.bbf_approval_log_section.wrapper).find(".bbf-timeline").remove();

	var items = logs.map(function (log) {
		var icon_cls = "blue";
		if (log.action === "Final Approved" || log.action === "Approved") icon_cls = "green";
		else if (log.action === "Rejected") icon_cls = "red";
		else if (log.action === "Revised") icon_cls = "orange";
		else if (log.action === "Resubmitted") icon_cls = "blue";
		else if (log.action === "Forwarded") icon_cls = "blue";

		var comment_html = "";
		if (log.comment) {
			comment_html = '<div class="bbf-tl__comment">' + frappe.utils.escape_html(log.comment) + "</div>";
		}

		var date_str = log.action_date ? frappe.datetime.str_to_user(log.action_date) : "";

		return (
			'<div class="bbf-tl__item">' +
				'<div class="bbf-tl__dot bbf-tl__dot--' + icon_cls + '"></div>' +
				'<div class="bbf-tl__content">' +
					'<div class="bbf-tl__action">' +
						'<strong>' + frappe.utils.escape_html(log.action) + "</strong>" +
						" by " + frappe.utils.escape_html(log.action_by_name || log.action_by || "") +
						(log.action_by_role ? " (" + frappe.utils.escape_html(log.action_by_role) + ")" : "") +
					"</div>" +
					'<div class="bbf-tl__meta">' +
						frappe.utils.escape_html(log.from_state || "") + " &rarr; " +
						frappe.utils.escape_html(log.to_state || "") +
						" &middot; " + date_str +
					"</div>" +
					comment_html +
				"</div>" +
			"</div>"
		);
	}).join("");

	var timeline = $(
		'<div class="bbf-timeline">' +
			'<div class="bbf-tl__list">' + items + "</div>" +
		"</div>"
	);

	if (frm.fields_dict.bbf_approval_log_section) {
		$(frm.fields_dict.bbf_approval_log_section.wrapper).prepend(timeline);
	}

	// Inject CSS
	if (!document.getElementById("bbf-timeline-css")) {
		var style = document.createElement("style");
		style.id = "bbf-timeline-css";
		style.textContent =
			".bbf-timeline { padding: 10px 15px; }" +
			".bbf-tl__list { border-left: 2px solid var(--gray-300); padding-left: 20px; }" +
			".bbf-tl__item { position: relative; margin-bottom: 16px; }" +
			".bbf-tl__dot { width: 12px; height: 12px; border-radius: 50%; position: absolute; left: -27px; top: 4px; }" +
			".bbf-tl__dot--green { background: var(--green-500); }" +
			".bbf-tl__dot--blue { background: var(--blue-500); }" +
			".bbf-tl__dot--orange { background: var(--orange-500); }" +
			".bbf-tl__dot--red { background: var(--red-500); }" +
			".bbf-tl__action { font-size: 13px; }" +
			".bbf-tl__meta { font-size: 11px; color: var(--gray-600); margin-top: 2px; }" +
			".bbf-tl__comment { font-size: 12px; color: var(--gray-700); margin-top: 4px; padding: 6px 10px;" +
			"  background: var(--gray-50); border-radius: 4px; border-left: 3px solid var(--gray-300); }";
		document.head.appendChild(style);
	}
}
