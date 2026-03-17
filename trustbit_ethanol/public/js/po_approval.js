// BBF PO Approval v2.0 — Category-based routing with dynamic stepper
frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		if (frm.is_new()) return;
		_load_approval_context(frm);
		_load_budget_indicator(frm);
	},
	cost_center(frm) {
		if (!frm.is_new()) _load_budget_indicator(frm);
	},
	validate(frm) {
		if (!frm.doc.cost_center) {
			frappe.validated = false;
			frappe.throw(__("Cost Center is mandatory on Purchase Orders for budget control."));
		}
	}
});

function _load_approval_context(frm) {
	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.get_approval_context",
		args: { doctype: "Purchase Order", docname: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			const ctx = r.message;
			if (!ctx.approval_enabled) return;

			_override_po_indicator(frm, ctx);
			_hide_po_standard_submit(frm, ctx);
			_render_stepper(frm, ctx);
			_render_amount_info(frm, ctx);
			_render_buttons(frm, ctx);
			_lock_fields(frm, ctx);
			_render_timeline(frm);
		},
		error() {
			// Silently fail — approval UI just won't show
		}
	});
}

function _override_po_indicator(frm, ctx) {
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
		else if (status.startsWith("Awaiting")) color = "yellow";
		else color = "blue";
	}

	frm.page.set_indicator(status, color);
}

function _hide_po_standard_submit(frm, ctx) {
	if (!ctx.status || ctx.status === "Draft" || ctx.status === "") return;
	frm.page.clear_primary_action();
	frm.dashboard.clear_headline();
	$(frm.page.wrapper).find(".form-message.blue").hide();
}

function _render_stepper(frm, ctx) {
	// Remove old stepper
	$(frm.fields_dict.bbf_approval_section?.wrapper || frm.page.wrapper)
		.find(".bbf-stepper").remove();

	const chain = ctx.approval_chain || [];
	if (!chain.length) return;

	let html = '<div class="bbf-stepper" style="padding: 15px 0; margin: 10px 0;">';
	html += '<div style="display: flex; align-items: flex-start; justify-content: center; gap: 0;">';

	chain.forEach((step, i) => {
		// Circle color based on status and action_type
		let circleColor = "#d1d5db"; // pending gray
		let circleContent = i + 1;
		let labelColor = "#6b7280";
		let pulse = "";

		if (step.status === "done") {
			circleColor = "#10b981"; // green
			circleContent = "✓";
			labelColor = "#10b981";
		} else if (step.status === "current") {
			if (step.action_type === "Review") {
				circleColor = "#3b82f6"; // blue for review
			} else if (step.action_type === "Final Approve") {
				circleColor = "#10b981"; // green for final
			} else {
				circleColor = "#f59e0b"; // amber for approve
			}
			pulse = "animation: bbf-pulse 2s infinite;";
			labelColor = circleColor;
		} else if (step.status === "skipped") {
			circleColor = "#ef4444"; // red
			circleContent = "✕";
		}

		// Step circle
		html += '<div style="display: flex; flex-direction: column; align-items: center; min-width: 80px; max-width: 120px;">';
		html += `<div style="width: 36px; height: 36px; border-radius: 50%; background: ${circleColor}; color: white; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 14px; ${pulse}">`;
		html += circleContent;
		html += '</div>';

		// Role label
		html += `<div style="font-size: 11px; color: ${labelColor}; margin-top: 6px; text-align: center; font-weight: 600;">`;
		html += frappe.utils.escape_html(step.role);
		html += '</div>';

		// Action type badge
		let badgeColor = "#e5e7eb";
		let badgeText = "#374151";
		if (step.action_type === "Review") { badgeColor = "#dbeafe"; badgeText = "#1d4ed8"; }
		else if (step.action_type === "Final Approve") { badgeColor = "#d1fae5"; badgeText = "#065f46"; }
		else if (step.action_type === "Approve") { badgeColor = "#fef3c7"; badgeText = "#92400e"; }

		html += `<div style="font-size: 9px; background: ${badgeColor}; color: ${badgeText}; padding: 1px 6px; border-radius: 8px; margin-top: 3px;">`;
		html += frappe.utils.escape_html(step.action_type);
		if (step.is_manual) html += " ⚡";
		html += '</div>';

		// Actor info
		if (step.by) {
			html += `<div style="font-size: 10px; color: #9ca3af; margin-top: 2px;">${frappe.utils.escape_html(step.by)}</div>`;
		}
		if (step.date) {
			html += `<div style="font-size: 9px; color: #d1d5db;">${frappe.utils.escape_html(step.date)}</div>`;
		}

		html += '</div>';

		// Connector line between steps
		if (i < chain.length - 1) {
			const lineColor = step.status === "done" ? "#10b981" : "#d1d5db";
			const lineStyle = chain[i + 1].is_manual ? "dashed" : "solid";
			html += `<div style="flex: 1; height: 2px; border-top: 2px ${lineStyle} ${lineColor}; margin-top: 18px; min-width: 30px;"></div>`;
		}
	});

	html += '</div></div>';

	// Add pulse animation CSS
	html += `<style>
		@keyframes bbf-pulse {
			0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
			50% { box-shadow: 0 0 0 8px rgba(59, 130, 246, 0); }
		}
	</style>`;

	// Insert stepper
	const $section = $(frm.fields_dict.bbf_approval_section?.wrapper);
	if ($section.length) {
		$section.prepend(html);
	}
}

function _render_amount_info(frm, ctx) {
	if (!ctx.status || ctx.status === "Draft" || ctx.status === "") return;

	const amount = format_currency(ctx.po_amount);
	let info = `<strong>${amount}</strong>`;

	if (ctx.purchase_category) {
		info += ` | <span style="color: #6366f1; font-weight: 600;">${frappe.utils.escape_html(ctx.purchase_category)}</span>`;
	}
	if (ctx.total_steps) {
		info += ` | ${ctx.total_steps} step${ctx.total_steps > 1 ? 's' : ''}`;
	}

	frm.dashboard.set_headline(info);
}

function _render_buttons(frm, ctx) {
	// Remove old approval buttons only (don't clear standard Frappe menu items)
	$(".bbf-approval-btn").remove();

	if (ctx.can_submit_for_approval) {
		frm.add_custom_button(__("Submit for Approval"), () => {
			frappe.call({
				method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.get_submit_target",
				args: { doctype: "Purchase Order", docname: frm.doc.name },
				callback(r) {
					const target = r.message?.target_label || "Approver";
					frappe.confirm(
						__("Submit this PO for approval? It will be sent to <b>{0}</b>.", [target]),
						() => _call_action(frm, "submit_for_approval", { doctype: "Purchase Order", docname: frm.doc.name })
					);
				},
				error() {
					frappe.confirm(
						__("Submit this PO for approval?"),
						() => _call_action(frm, "submit_for_approval", { doctype: "Purchase Order", docname: frm.doc.name })
					);
				}
			});
		}, null).addClass("btn-primary bbf-approval-btn");
	}

	if (ctx.can_review) {
		frm.add_custom_button(__("Reviewed & Forward"), () => {
			_show_comment_dialog(frm, "Review Comment", (comment) => {
				_call_action(frm, "approve_document", { doctype: "Purchase Order", docname: frm.doc.name, comment });
			});
		}, null).addClass("btn-primary bbf-approval-btn");
	}

	if (ctx.can_approve) {
		frm.add_custom_button(__("Approve & Forward"), () => {
			_show_comment_dialog(frm, "Approval Comment", (comment) => {
				_call_action(frm, "approve_document", { doctype: "Purchase Order", docname: frm.doc.name, comment });
			});
		}, null).addClass("btn-primary bbf-approval-btn");
	}

	if (ctx.can_final_approve) {
		frm.add_custom_button(__("Final Approve"), () => {
			_show_comment_dialog(frm, "Final Approval Comment", (comment) => {
				_call_action(frm, "approve_document", { doctype: "Purchase Order", docname: frm.doc.name, comment });
			});
		}, null).addClass("btn-success bbf-approval-btn");
	}

	if (ctx.can_send_to_md) {
		frm.add_custom_button(__("Send to MD"), () => {
			frappe.confirm(
				__("Send this PO to the Managing Director for final approval?"),
				() => {
					_show_comment_dialog(frm, "Comment for MD", (comment) => {
						frappe.call({
							method: "trustbit_ethanol.bbf_gate_entry.bbf_po_approval.send_to_md",
							args: { docname: frm.doc.name, comment },
							freeze: true,
							freeze_message: __("Sending to MD..."),
							callback() { frm.reload_doc(); },
							error() { frm.reload_doc(); }
						});
					});
				}
			);
		}, null).addClass("btn-warning bbf-approval-btn").css({ "color": "#92400e", "border-color": "#f59e0b", "background": "#fef3c7" });
	}

	if (ctx.can_revise) {
		frm.add_custom_button(__("Revise"), () => {
			_show_revise_dialog(frm);
		}, null).addClass("bbf-approval-btn");
	}

	if (ctx.can_reject) {
		frm.add_custom_button(__("Reject"), () => {
			_show_reject_dialog(frm);
		}, null).addClass("bbf-approval-btn");
	}

	if (ctx.can_resubmit) {
		frm.add_custom_button(__("Resubmit for Approval"), () => {
			frappe.confirm(
				__("Resubmit this PO for approval? It will restart from the first step."),
				() => _call_action(frm, "resubmit_document", { doctype: "Purchase Order", docname: frm.doc.name, mode: "restart" })
			);
		}, null).addClass("btn-primary bbf-approval-btn");
	}
}

function _show_comment_dialog(frm, title, callback) {
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

function _show_revise_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Revise PO"),
		fields: [
			{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason for Revision"), reqd: 1 },
			{ fieldtype: "Small Text", fieldname: "comment", label: __("Additional Comment (optional)") }
		],
		primary_action_label: __("Send for Revision"),
		primary_action(values) {
			d.hide();
			_call_action(frm, "revise_document", {
				doctype: "Purchase Order", docname: frm.doc.name,
				reason: values.reason, comment: values.comment || ""
			});
		}
	});
	d.show();
}

function _show_reject_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Reject PO"),
		fields: [
			{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason for Rejection"), reqd: 1 },
			{ fieldtype: "Small Text", fieldname: "comment", label: __("Additional Comment (optional)") }
		],
		primary_action_label: __("Reject"),
		primary_action(values) {
			d.hide();
			_call_action(frm, "reject_document", {
				doctype: "Purchase Order", docname: frm.doc.name,
				reason: values.reason, comment: values.comment || ""
			});
		}
	});
	d.show();
}

function _call_action(frm, method, args) {
	frappe.call({
		method: `trustbit_ethanol.bbf_gate_entry.bbf_po_approval.${method}`,
		args,
		freeze: true,
		freeze_message: __("Processing..."),
		callback() { frm.reload_doc(); },
		error() { frm.reload_doc(); }
	});
}

function _load_budget_indicator(frm) {
	if (frm.is_new() || !frm.doc.cost_center) return;

	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_budget.check_budget_for_po",
		args: { docname: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			const b = r.message;

			// Remove old indicator
			$(frm.fields_dict.bbf_approval_section?.wrapper || frm.page.wrapper)
				.find(".bbf-budget-indicator").remove();

			if (b.status === "no_cc" || b.status === "no_budget") return;

			const colors = { green: "#10b981", yellow: "#f59e0b", red: "#ef4444" };
			const bgColors = { green: "#d1fae5", yellow: "#fef3c7", red: "#fee2e2" };
			const borderColors = { green: "#6ee7b7", yellow: "#fcd34d", red: "#fca5a5" };
			const color = colors[b.color] || colors.green;
			const bg = bgColors[b.color] || bgColors.green;
			const border = borderColors[b.color] || borderColors.green;

			let html = `<div class="bbf-budget-indicator" style="padding: 10px 15px; margin: 8px 0; background: ${bg}; border: 1px solid ${border}; border-radius: 8px; font-size: 12px;">`;
			html += `<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">`;

			// Budget bar
			const pct = Math.min(b.utilization_pct || 0, 100);
			html += `<div style="flex: 1; min-width: 200px;">`;
			html += `<div style="display: flex; justify-content: space-between; margin-bottom: 4px;">`;
			html += `<span style="font-weight: 600; color: ${color};">Budget: ${frappe.utils.escape_html(b.cost_center)}</span>`;
			html += `<span style="font-weight: 600; color: ${color};">${b.utilization_pct}% used</span>`;
			html += `</div>`;
			html += `<div style="height: 8px; background: #e5e7eb; border-radius: 4px; overflow: hidden;">`;
			html += `<div style="height: 100%; width: ${pct}%; background: ${color}; border-radius: 4px;"></div>`;
			html += `</div>`;
			html += `</div>`;

			// Numbers
			html += `<div style="display: flex; gap: 15px; font-size: 11px; color: #374151;">`;
			html += `<div><strong>Annual:</strong> ${format_currency(b.annual_budget)}</div>`;
			html += `<div><strong>Committed:</strong> ${format_currency(b.committed)}</div>`;
			html += `<div><strong>Spent:</strong> ${format_currency(b.actual_spent)}</div>`;
			html += `<div style="font-weight: 700; color: ${color};"><strong>Available:</strong> ${format_currency(b.available)}</div>`;
			html += `</div>`;

			html += `</div>`;

			if (b.status === "exceeded") {
				html += `<div style="margin-top: 6px; padding: 6px 10px; background: #fef2f2; border-radius: 4px; color: #991b1b; font-size: 11px; font-weight: 600;">`;
				html += `⚠ Budget exceeded by ${format_currency(b.shortfall)}. PO submission will be blocked. CEO can override during approval.`;
				html += `</div>`;
			}

			html += `</div>`;

			const $section = $(frm.fields_dict.bbf_approval_section?.wrapper);
			if ($section.length) {
				$section.find(".bbf-budget-indicator").remove();
				$section.prepend(html);
			}
		},
		error() {} // Silent fail
	});
}

function _lock_fields(frm, ctx) {
	// Lock PO fields during approval and after terminal states
	const status = ctx.status || "";
	const should_lock = ctx.is_pending || status.startsWith("Awaiting") || status === "Rejected" || status === "Revised";
	if (!should_lock) return;

	const fields_to_lock = [
		"supplier", "supplier_name", "schedule_date", "transaction_date",
		"currency", "buying_price_list", "price_list_currency",
		"plc_conversion_rate", "conversion_rate",
		"apply_discount_on", "additional_discount_percentage", "discount_amount",
		"items", "taxes", "pricing_rules",
		"tc_name", "terms",
		"payment_schedule", "payment_terms_template",
	];

	fields_to_lock.forEach(f => {
		frm.set_df_property(f, "read_only", 1);
	});
}

function _render_timeline(frm) {
	// Remove old timeline
	$(frm.fields_dict.bbf_approval_log_section?.wrapper).find(".bbf-timeline").remove();

	const logs = frm.doc.bbf_approval_log || [];
	if (!logs.length) return;

	let html = '<div class="bbf-timeline" style="padding: 10px 0;">';

	// Reverse to show newest first
	const sorted_logs = [...logs].sort((a, b) => {
		return new Date(b.action_date) - new Date(a.action_date);
	});

	sorted_logs.forEach(log => {
		const colors = {
			"Submitted": "#3b82f6",
			"Reviewed": "#3b82f6",
			"Forwarded": "#8b5cf6",
			"Approved": "#10b981",
			"Final Approved": "#10b981",
			"Sent to MD": "#f59e0b",
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

	const $section = $(frm.fields_dict.bbf_approval_log_section?.wrapper);
	if ($section.length) {
		$section.prepend(html);
	}
}
