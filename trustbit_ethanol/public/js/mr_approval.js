// BBF MR Approval v2.0 — Cost-center-based routing with stepper
frappe.ui.form.on("Material Request", {
	refresh(frm) {
		if (frm.is_new()) return;
		_load_mr_context(frm);
	}
});

function _load_mr_context(frm) {
	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.get_approval_context",
		args: { doctype: "Material Request", docname: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			const ctx = r.message;
			if (!ctx.approval_enabled) return;

			_render_mr_status(frm, ctx);
			_hide_standard_submit(frm, ctx);
			_render_mr_stepper(frm, ctx);
			_render_mr_info(frm, ctx);
			_render_mr_buttons(frm, ctx);
			_lock_mr_fields(frm, ctx);
			_render_mr_timeline(frm);
		},
		error() {}
	});
}

function _render_mr_status(frm, ctx) {
	const status = ctx.status;
	if (!status) return;

	const colors = {
		"Approved": "green",
		"Rejected": "red",
		"Revised": "orange",
	};

	let color = colors[status];
	if (!color) {
		if (status.startsWith("Pending")) color = "blue";
		else color = "blue";
	}

	frm.page.set_indicator(status, color);
}

function _hide_standard_submit(frm, ctx) {
	if (!ctx.status || ctx.status === "Draft" || ctx.status === "") return;
	frm.page.clear_primary_action();
	frm.dashboard.clear_headline();
	$(frm.page.wrapper).find(".form-message.blue").hide();
}

function _render_mr_stepper(frm, ctx) {
	// Remove old stepper
	$(frm.fields_dict.bbf_mr_section?.wrapper || frm.page.wrapper)
		.find(".bbf-mr-stepper").remove();

	const chain = ctx.approval_chain || [];
	if (!chain.length) return;

	let html = '<div class="bbf-mr-stepper" style="padding: 15px 0; margin: 10px 0;">';
	html += '<div style="display: flex; align-items: flex-start; justify-content: center; gap: 0;">';

	chain.forEach((step, i) => {
		let circleColor = "#d1d5db";
		let circleContent = i + 1;
		let labelColor = "#6b7280";
		let pulse = "";

		if (step.status === "done") {
			circleColor = "#10b981";
			circleContent = "✓";
			labelColor = "#10b981";
		} else if (step.status === "current") {
			circleColor = step.action_type === "Review" ? "#3b82f6" : "#10b981";
			pulse = "animation: bbf-mr-pulse 2s infinite;";
			labelColor = circleColor;
		} else if (step.status === "skipped") {
			circleColor = "#ef4444";
			circleContent = "✕";
		}

		html += '<div style="display: flex; flex-direction: column; align-items: center; min-width: 80px; max-width: 120px;">';
		html += `<div style="width: 36px; height: 36px; border-radius: 50%; background: ${circleColor}; color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 14px; ${pulse}">`;
		html += circleContent;
		html += '</div>';

		html += `<div style="font-size: 11px; color: ${labelColor}; margin-top: 6px; text-align: center; font-weight: 600;">`;
		html += frappe.utils.escape_html(step.role);
		html += '</div>';

		let badgeColor = step.action_type === "Review" ? "#dbeafe" : "#d1fae5";
		let badgeText = step.action_type === "Review" ? "#1d4ed8" : "#065f46";
		html += `<div style="font-size: 9px; background: ${badgeColor}; color: ${badgeText}; padding: 1px 6px; border-radius: 8px; margin-top: 3px;">`;
		html += frappe.utils.escape_html(step.action_type);
		html += '</div>';

		if (step.by) {
			html += `<div style="font-size: 10px; color: #9ca3af; margin-top: 2px;">${frappe.utils.escape_html(step.by)}</div>`;
		}
		if (step.date) {
			html += `<div style="font-size: 9px; color: #d1d5db;">${frappe.utils.escape_html(step.date)}</div>`;
		}

		html += '</div>';

		if (i < chain.length - 1) {
			const lineColor = step.status === "done" ? "#10b981" : "#d1d5db";
			html += `<div style="flex: 1; height: 2px; border-top: 2px solid ${lineColor}; margin-top: 18px; min-width: 30px;"></div>`;
		}
	});

	html += '</div></div>';

	html += `<style>
		@keyframes bbf-mr-pulse {
			0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
			50% { box-shadow: 0 0 0 8px rgba(59, 130, 246, 0); }
		}
	</style>`;

	const $section = $(frm.fields_dict.bbf_mr_section?.wrapper);
	if ($section.length) {
		$section.prepend(html);
	}
}

function _render_mr_info(frm, ctx) {
	if (!ctx.status || ctx.status === "Draft" || ctx.status === "") return;

	let info = "";
	if (ctx.mr_route) {
		info += `<span style="color: #6366f1; font-weight: 600;">${frappe.utils.escape_html(ctx.mr_route)}</span>`;
	}
	if (ctx.total_steps) {
		if (info) info += " | ";
		info += `${ctx.total_steps} step${ctx.total_steps > 1 ? 's' : ''}`;
	}

	if (info) {
		frm.dashboard.set_headline(info);
	}
}

function _render_mr_buttons(frm, ctx) {
	$(".bbf-mr-btn").remove();

	if (ctx.can_submit_for_approval) {
		frm.add_custom_button(__("Submit for Approval"), () => {
			frappe.call({
				method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.get_submit_target",
				args: { doctype: "Material Request", docname: frm.doc.name },
				callback(r) {
					const target = r.message?.target_label || "Approver";
					frappe.confirm(
						__("Submit this MR for approval? It will be sent to <b>{0}</b>.", [target]),
						() => _call_mr_action(frm, "submit_for_approval", { doctype: "Material Request", docname: frm.doc.name })
					);
				},
				error() {
					frappe.confirm(
						__("Submit this MR for approval?"),
						() => _call_mr_action(frm, "submit_for_approval", { doctype: "Material Request", docname: frm.doc.name })
					);
				}
			});
		}, null).addClass("btn-primary bbf-mr-btn");
	}

	if (ctx.can_review) {
		frm.add_custom_button(__("Reviewed & Forward"), () => {
			_show_mr_comment_dialog(frm, "Review Comment", (comment) => {
				_call_mr_action(frm, "approve_document", { doctype: "Material Request", docname: frm.doc.name, comment });
			});
		}, null).addClass("btn-primary bbf-mr-btn");
	}

	if (ctx.can_final_approve) {
		frm.add_custom_button(__("Approve"), () => {
			_show_mr_comment_dialog(frm, "Approval Comment", (comment) => {
				_call_mr_action(frm, "approve_document", { doctype: "Material Request", docname: frm.doc.name, comment });
			});
		}, null).addClass("btn-success bbf-mr-btn");
	}

	if (ctx.can_revise) {
		frm.add_custom_button(__("Revise"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Revise MR"),
				fields: [
					{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), reqd: 1 },
					{ fieldtype: "Small Text", fieldname: "comment", label: __("Comment (optional)") }
				],
				primary_action_label: __("Send for Revision"),
				primary_action(values) {
					d.hide();
					_call_mr_action(frm, "revise_document", {
						doctype: "Material Request", docname: frm.doc.name,
						reason: values.reason, comment: values.comment || ""
					});
				}
			});
			d.show();
		}, null).addClass("bbf-mr-btn");
	}

	if (ctx.can_reject) {
		frm.add_custom_button(__("Reject"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Reject MR"),
				fields: [
					{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), reqd: 1 },
					{ fieldtype: "Small Text", fieldname: "comment", label: __("Comment (optional)") }
				],
				primary_action_label: __("Reject"),
				primary_action(values) {
					d.hide();
					_call_mr_action(frm, "reject_document", {
						doctype: "Material Request", docname: frm.doc.name,
						reason: values.reason, comment: values.comment || ""
					});
				}
			});
			d.show();
		}, null).addClass("bbf-mr-btn");
	}

	if (ctx.can_resubmit) {
		frm.add_custom_button(__("Resubmit for Approval"), () => {
			frappe.confirm(
				__("Resubmit this MR for approval?"),
				() => _call_mr_action(frm, "resubmit_document", { doctype: "Material Request", docname: frm.doc.name })
			);
		}, null).addClass("btn-primary bbf-mr-btn");
	}
}

function _show_mr_comment_dialog(frm, title, callback) {
	const d = new frappe.ui.Dialog({
		title: __(title),
		fields: [
			{ fieldtype: "Small Text", fieldname: "comment", label: __("Comment (optional)") }
		],
		primary_action_label: __("Confirm"),
		primary_action(values) {
			d.hide();
			callback(values.comment || "");
		}
	});
	d.show();
}

function _call_mr_action(frm, method, args) {
	frappe.call({
		method: `trustbit_ethanol.bbf_gate_entry.bbf_po_approval.${method}`,
		args,
		freeze: true,
		freeze_message: __("Processing..."),
		callback() { frm.reload_doc(); },
		error() { frm.reload_doc(); }
	});
}

function _lock_mr_fields(frm, ctx) {
	const status = ctx.status || "";
	const should_lock = ctx.is_pending || status === "Rejected" || status === "Revised";
	if (!should_lock) return;

	const fields_to_lock = [
		"material_request_type", "schedule_date", "transaction_date",
		"items", "cost_center", "project",
		"tc_name", "terms",
	];

	fields_to_lock.forEach(f => {
		frm.set_df_property(f, "read_only", 1);
	});
}

function _render_mr_timeline(frm) {
	$(frm.fields_dict.bbf_mr_log_section?.wrapper).find(".bbf-mr-timeline").remove();

	const logs = frm.doc.bbf_mr_log || [];
	if (!logs.length) return;

	let html = '<div class="bbf-mr-timeline" style="padding: 10px 0;">';

	const sorted_logs = [...logs].sort((a, b) => {
		return new Date(b.action_date) - new Date(a.action_date);
	});

	sorted_logs.forEach(log => {
		const colors = {
			"Submitted": "#3b82f6",
			"Reviewed": "#3b82f6",
			"Final Approved": "#10b981",
			"Revised": "#f97316",
			"Rejected": "#ef4444",
			"Resubmitted": "#6366f1",
		};
		const color = colors[log.action] || "#6b7280";
		const date = log.action_date ? frappe.datetime.str_to_user(log.action_date) : "";

		html += `<div style="display: flex; align-items: flex-start; margin-bottom: 8px;">`;
		html += `<div style="width: 10px; height: 10px; border-radius: 50%; background: ${color}; margin-top: 5px; margin-right: 10px; flex-shrink: 0;"></div>`;
		html += `<div style="flex: 1;">`;
		html += `<div style="font-size: 12px;"><strong style="color: ${color};">${frappe.utils.escape_html(log.action)}</strong>`;
		html += ` by ${frappe.utils.escape_html(log.action_by_name || log.action_by || "System")}`;
		if (log.action_by_role) html += ` <span style="color: #9ca3af;">(${frappe.utils.escape_html(log.action_by_role)})</span>`;
		html += `</div>`;
		html += `<div style="font-size: 11px; color: #9ca3af;">${frappe.utils.escape_html(log.from_state || "")} → ${frappe.utils.escape_html(log.to_state || "")}</div>`;
		if (log.comment) {
			html += `<div style="font-size: 11px; color: #6b7280; margin-top: 2px;">${frappe.utils.escape_html(log.comment)}</div>`;
		}
		html += `<div style="font-size: 10px; color: #d1d5db;">${frappe.utils.escape_html(date)}</div>`;
		html += `</div></div>`;
	});

	html += '</div>';

	const $section = $(frm.fields_dict.bbf_mr_log_section?.wrapper);
	if ($section.length) {
		$section.prepend(html);
	}
}
