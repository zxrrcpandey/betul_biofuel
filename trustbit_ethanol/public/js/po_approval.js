// TS PO Approval v2.0 — Category-based routing with dynamic stepper
frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		_force_po_grid_columns(frm);
		if (frm.is_new()) return;
		_ts_add_print_button(frm, "TS Purchase Order");
		_load_approval_context(frm);
		_load_budget_indicator(frm);
		_load_lifecycle_tracker(frm);
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
		method: "trustbit_ethanol.ts_gate_entry.ts_po_approval.get_approval_context",
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
	// Only hide standard Submit on Draft POs (docstatus=0).
	// Do NOT clear primary action on Submitted (Cancel) or Cancelled (Amend) POs.
	if (frm.doc.docstatus === 0) {
		frm.page.clear_primary_action();
		_hide_frappe_po_submit_ui(frm);
	}
}

function _hide_frappe_po_submit_ui(frm) {
	$(frm.page.wrapper).find(".form-message.blue").each(function() {
		if ($(this).text().indexOf("Submit this document") !== -1) {
			$(this).hide();
		}
	});
}

function _show_ts_po_banner(frm, key, html, bgColor, borderColor) {
	$(frm.page.wrapper).find(`.ts-banner[data-key="${key}"]`).remove();
	const banner = `<div class="ts-banner" data-key="${key}" style="
		padding: 8px 15px;
		margin: 0 15px 8px;
		background: ${bgColor};
		border-left: 3px solid ${borderColor};
		border-radius: 4px;
		font-size: 12px;
	">${html}</div>`;
	const $dashboard = $(frm.page.wrapper).find(".form-dashboard-section");
	if ($dashboard.length) {
		$dashboard.after(banner);
	}
}

function _render_stepper(frm, ctx) {
	// Remove old stepper
	$(frm.fields_dict.ts_approval_section?.wrapper || frm.page.wrapper)
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

	// Only inject animation CSS once
	if (!document.getElementById("bbf-pulse-style")) {
		html += `<style id="bbf-pulse-style">
			@keyframes bbf-pulse {
				0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
				50% { box-shadow: 0 0 0 8px rgba(59, 130, 246, 0); }
			}
		</style>`;
	}

	// Insert stepper
	const $section = $(frm.fields_dict.ts_approval_section?.wrapper);
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

	_show_ts_po_banner(frm, "info", info, "#eff6ff", "#3b82f6");
}

function _render_buttons(frm, ctx) {
	// Remove old approval buttons only (don't clear standard Frappe menu items)
	$(".bbf-approval-btn").remove();

	if (ctx.can_submit_for_approval) {
		frm.add_custom_button(__("Submit for Approval"), () => {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.ts_po_approval.get_submit_target",
				args: { doctype: "Purchase Order", docname: frm.doc.name },
				freeze: true,
				freeze_message: __("Checking..."),
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
							method: "trustbit_ethanol.ts_gate_entry.ts_po_approval.send_to_md",
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
			if (frm.is_dirty()) {
				frappe.confirm(
					__("You have unsaved changes. Save first, then resubmit?"),
					() => {
						frm.save().then(() => {
							frappe.confirm(
								__("Resubmit this PO for approval? It will restart from the first step."),
								() => _call_action(frm, "resubmit_document", { doctype: "Purchase Order", docname: frm.doc.name, mode: "restart" })
							);
						});
					}
				);
				return;
			}
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
		method: `trustbit_ethanol.ts_gate_entry.ts_po_approval.${method}`,
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
		method: "trustbit_ethanol.ts_gate_entry.ts_budget.check_budget_for_po",
		args: { docname: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			const b = r.message;

			// Remove old indicator
			$(frm.fields_dict.ts_approval_section?.wrapper || frm.page.wrapper)
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

			const $section = $(frm.fields_dict.ts_approval_section?.wrapper);
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
	const should_lock = ctx.is_pending || status.startsWith("Awaiting") || status === "Rejected" || status === "Approved";
	// NOTE: "Revised" intentionally excluded — creator must edit items before resubmitting (Lesson 81)
	if (!should_lock) return;

	const fields_to_lock = [
		"supplier", "supplier_name", "schedule_date", "transaction_date",
		"currency", "buying_price_list", "price_list_currency",
		"plc_conversion_rate", "conversion_rate",
		"apply_discount_on", "additional_discount_percentage", "discount_amount",
		"items", "taxes", "pricing_rules",
		"tc_name", "terms",
		"payment_schedule", "payment_terms_template",
		"cost_center",
	];

	fields_to_lock.forEach(f => {
		frm.set_df_property(f, "read_only", 1);
	});
}

function _render_timeline(frm) {
	// Remove old timeline
	$(frm.fields_dict.ts_approval_log_section?.wrapper).find(".bbf-timeline").remove();

	const logs = frm.doc.ts_approval_log || [];
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

	const $section = $(frm.fields_dict.ts_approval_log_section?.wrapper);
	if ($section.length) {
		$section.prepend(html);
	}
}

/* ── Delivery Lifecycle Tracker ─────────────────────────────────────── */

function _load_lifecycle_tracker(frm) {
	// Only for submitted/cancelled POs (deliveries require submitted PO)
	if (frm.doc.docstatus < 1) return;

	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.api.get_po_lifecycle",
		args: { po_name: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			_render_lifecycle(frm, r.message);
		},
		error() {} // Silent fail
	});
}

function _render_lifecycle(frm, data) {
	$(frm.page.wrapper).find(".bbf-lifecycle-tracker").remove();

	const deliveries = data.deliveries || [];
	if (!deliveries.length) return;

	// Inject pulse animation for lifecycle (amber)
	if (!document.getElementById("bbf-lifecycle-pulse-style")) {
		const style = document.createElement("style");
		style.id = "bbf-lifecycle-pulse-style";
		style.textContent = `
			@keyframes bbf-lc-pulse {
				0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.4); }
				50% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); }
			}
		`;
		document.head.appendChild(style);
	}

	const exited = deliveries.filter(d => d.token_status === "Exited").length;
	const active = deliveries.length - exited;

	let html = '<div class="bbf-lifecycle-tracker" style="margin: 10px 15px 15px; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; background: white;">';

	// Header
	html += '<div style="padding: 10px 15px; background: #f0f9ff; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center;">';
	html += `<div style="font-weight: 700; font-size: 13px; color: #0f172a;">Deliveries <span style="font-weight: 400; color: #64748b; font-size: 12px;">(${deliveries.length})</span></div>`;
	if (deliveries.length > 0) {
		let summary = [];
		if (exited) summary.push(`<span style="color: #10b981;">${exited} completed</span>`);
		if (active) summary.push(`<span style="color: #f59e0b;">${active} in progress</span>`);
		html += `<div style="font-size: 11px; color: #64748b;">${summary.join(" &middot; ")}</div>`;
	}
	html += "</div>";

	deliveries.forEach((d, idx) => {
		const isLast = idx === deliveries.length - 1;
		html += `<div style="padding: 14px 15px; ${!isLast ? "border-bottom: 1px solid #f1f5f9;" : ""}">`;

		// Row 1: Token + Vehicle | Status + Date
		html += '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">';
		html += '<div style="display: flex; align-items: center; gap: 10px;">';
		html += `<a href="/app/bbf-token/${encodeURIComponent(d.token)}" style="font-weight: 700; font-size: 13px; color: #1d4ed8; text-decoration: none;">${_lc_esc(d.token_number)}</a>`;
		if (d.vehicle_number) {
			html += `<span style="font-size: 12px; color: #475569; background: #f1f5f9; padding: 1px 8px; border-radius: 4px;">${_lc_esc(d.vehicle_number)}</span>`;
		}
		html += "</div>";
		html += '<div style="display: flex; align-items: center; gap: 8px;">';
		const sc = _lc_status_color(d.token_status);
		html += `<span style="font-size: 11px; padding: 2px 10px; border-radius: 12px; background: ${sc.bg}; color: ${sc.text}; font-weight: 600;">${_lc_esc(d.token_status)}</span>`;
		if (d.entry_date) {
			html += `<span style="font-size: 11px; color: #94a3b8;">${_lc_esc(d.entry_date)}</span>`;
		}
		html += "</div></div>";

		// Row 2: Flow type + Weight summary
		html += '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">';
		html += `<span style="font-size: 11px; color: #94a3b8;">${_lc_esc(d.stock_direction)} &middot; ${_lc_esc(d.material_flow)}</span>`;
		const wb = d.docs.weighbridge;
		if (wb && (wb.gross_weight || wb.tare_weight)) {
			html += '<span style="font-size: 11px; color: #64748b;">';
			if (wb.gross_weight) html += `Gross: ${_lc_fmt_wt(wb.gross_weight)}`;
			if (wb.tare_weight) html += ` | Tare: ${_lc_fmt_wt(wb.tare_weight)}`;
			if (wb.net_weight) html += ` | <strong>Net: ${_lc_fmt_wt(wb.net_weight)}</strong>`;
			html += "</span>";
		}
		html += "</div>";

		// Row 3: Progress stepper
		html += '<div style="display: flex; align-items: center; margin: 12px 0 10px; padding: 0 5px;">';
		d.steps.forEach((step, si) => {
			let cc = "#d1d5db", content = si + 1, lc = "#94a3b8", pulse = "";
			if (step.status === "done") {
				cc = "#10b981"; content = "&#10003;"; lc = "#10b981";
			} else if (step.status === "current") {
				cc = "#f59e0b"; pulse = "animation: bbf-lc-pulse 2s infinite;"; lc = "#f59e0b";
			}

			html += '<div style="display: flex; flex-direction: column; align-items: center; flex: 1; min-width: 0;">';
			html += `<div style="width: 26px; height: 26px; border-radius: 50%; background: ${cc}; color: white; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 10px; ${pulse}">${content}</div>`;
			html += `<div style="font-size: 9px; color: ${lc}; margin-top: 3px; text-align: center; font-weight: 500;">${_lc_esc(step.short)}</div>`;
			html += "</div>";

			if (si < d.steps.length - 1) {
				const lineColor = step.status === "done" ? "#10b981" : "#e5e7eb";
				html += `<div style="flex: 0.5; height: 2px; border-top: 2px solid ${lineColor}; margin-bottom: 16px;"></div>`;
			}
		});
		html += "</div>";

		// Row 4: Document links
		html += '<div style="display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 11px; padding-top: 6px; border-top: 1px solid #f1f5f9;">';
		const links = _lc_get_doc_links(d);
		links.forEach(dl => {
			if (dl.doc) {
				const url = `/app/${_lc_slug(dl.dt)}/${encodeURIComponent(dl.doc.name)}`;
				html += `<span><span style="color: #94a3b8;">${dl.label}:</span> <a href="${url}" style="color: #1d4ed8; text-decoration: none; font-weight: 500;">${_lc_esc(dl.doc.name)}</a>`;
				if (dl.doc.status) {
					html += ` <span style="color: ${_lc_doc_status_color(dl.doc.status)}; font-size: 10px;">(${_lc_esc(dl.doc.status)})</span>`;
				}
				html += "</span>";
			} else {
				html += `<span style="color: #d1d5db;">${dl.label}: &mdash;</span>`;
			}
		});
		html += "</div>";

		html += "</div>"; // end delivery card
	});

	html += "</div>"; // end tracker

	// Insert before form-layout (between dashboard and form fields)
	const $layout = $(frm.layout.wrapper);
	if ($layout.length) {
		$layout.before(html);
	}
}

function _lc_esc(v) {
	return frappe.utils.escape_html(v || "");
}

function _lc_fmt_wt(val) {
	if (!val) return "&mdash;";
	return parseFloat(val).toLocaleString("en-IN", { maximumFractionDigits: 2 }) + " KG";
}

function _lc_slug(doctype) {
	return doctype.toLowerCase().replace(/ /g, "-");
}

function _lc_status_color(status) {
	if (status === "Exited") return { bg: "#d1fae5", text: "#065f46" };
	if (status === "GRN Created" || status === "Dispatch Ready") return { bg: "#dcfce7", text: "#166534" };
	if (status === "Token Generated") return { bg: "#f1f5f9", text: "#475569" };
	return { bg: "#fef3c7", text: "#92400e" };
}

function _lc_doc_status_color(status) {
	if (["Completed", "Approved", "Submitted"].includes(status)) return "#10b981";
	if (["Pending", "Pending Inspection", "Draft"].includes(status)) return "#f59e0b";
	if (["Rejected", "Cancelled"].includes(status)) return "#ef4444";
	return "#94a3b8";
}

function _lc_get_doc_links(d) {
	const docs = d.docs;
	const links = [
		{ label: "Gate Entry", dt: "TS Gate Entry", doc: docs.gate_entry },
	];

	if (d.material_flow === "Raw Material") {
		links.push({ label: "Weighbridge", dt: "TS Weighbridge Log", doc: docs.weighbridge });
		links.push({ label: "Quality", dt: "TS Quality Inspection", doc: docs.quality_inspection });
		links.push({ label: "Deduction", dt: "TS Deduction Sheet", doc: docs.deduction_sheet });
	} else {
		if (docs.weighbridge) {
			links.push({ label: "Weighbridge", dt: "TS Weighbridge Log", doc: docs.weighbridge });
		}
		if (docs.material_inspection) {
			links.push({ label: "Inspection", dt: "TS Material Inspection", doc: docs.material_inspection });
		}
	}

	if (d.stock_direction === "Stock OUT") {
		links.push({ label: "Delivery Note", dt: "Delivery Note", doc: docs.delivery_note });
	} else {
		links.push({ label: "GRN", dt: "Purchase Receipt", doc: docs.purchase_receipt });
	}

	return links;
}

function _force_po_grid_columns(frm) {
	try {
		["ts_delivery_location", "ts_item_remark"].forEach(fn => {
			let df = frappe.meta.get_docfield("Purchase Order Item", fn, frm.doc.name);
			if (df) {
				df.in_list_view = 1;
				df.columns = 2;
			}
		});

		const grid = frm.fields_dict.items?.grid;
		if (!grid) return;

		(grid.grid_rows || []).forEach(row => {
			(row.docfields || []).forEach(f => {
				if (["ts_delivery_location", "ts_item_remark"].includes(f.fieldname)) {
					f.in_list_view = 1;
					f.columns = 2;
				}
			});
		});

		grid.refresh();
	} catch(e) {}
}

// ═══════════════════════════════════════════════════════════
//  CUSTOM PRINT BUTTON — Direct PDF download (bypasses Frappe print preview)
// ═══════════════════════════════════════════════════════════

function _ts_add_print_button(frm, default_format) {
	// Hide ONLY Frappe's print icon (the printer button in toolbar)
	setTimeout(() => {
		// Target specifically the print icon button — not other buttons
		frm.page.wrapper.find('.btn[data-original-title="Print"], .btn-print-preview, a[title="Print"]').hide();
		// Hide "Print" from menu (... dropdown) — match exact text only
		frm.page.menu_btn_group.find('.dropdown-item').each(function() {
			if ($(this).text().trim() === "Print") $(this).hide();
		});
	}, 500);

	// Add our custom Print button as standalone (not inside Actions)
	frm.add_custom_button(__("🖨 Print PDF"), () => {
		const formats = ["TS Purchase Order", "TS Purchase Order (Clean)"];

		frappe.prompt({
			fieldtype: "Select",
			label: "Print Format",
			fieldname: "format",
			options: formats.join("\n"),
			default: default_format || formats[0],
			reqd: 1,
		}, (values) => {
			_ts_download_pdf(frm, values.format);
		}, __("Select Print Format"), __("Download PDF"));
	});
}

function _ts_download_pdf(frm, format) {
	const url = `/api/method/frappe.utils.print_format.download_pdf?doctype=${encodeURIComponent(frm.doc.doctype)}&name=${encodeURIComponent(frm.doc.name)}&format=${encodeURIComponent(format)}&no_letterhead=0`;
	window.open(url, "_blank");
}
