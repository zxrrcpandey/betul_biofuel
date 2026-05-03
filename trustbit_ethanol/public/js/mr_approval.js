// TS MR Approval v2.5 — Cost-center-based routing with stepper, Hold/Resume, CC config
//
// v2.9.0.6 (Bug 11.F): Auto-default item row Cost Center from parent MR's CC.
// Previously item rows inherited cost_center from Item.default_cost_center
// (often "Main - BBPL"), causing User Permission errors for users restricted
// to specific CCs (e.g. Capex - BBPL only). Now row CC always matches parent.
frappe.ui.form.on("Material Request Item", {
	items_add(frm, cdt, cdn) {
		// When a new row is added, immediately default cost_center to parent MR's CC
		const row = locals[cdt][cdn];
		if (frm.doc.cost_center && !row.cost_center) {
			frappe.model.set_value(cdt, cdn, "cost_center", frm.doc.cost_center);
		}
	},
	item_code(frm, cdt, cdn) {
		// After Frappe's native item fetch_from auto-fills row.cost_center
		// from Item.default_cost_center, override it with parent MR's CC if set.
		// 150ms delay ensures Frappe's fetch_from completes first.
		const row = locals[cdt][cdn];
		if (frm.doc.cost_center) {
			setTimeout(() => {
				if (row.cost_center !== frm.doc.cost_center) {
					frappe.model.set_value(cdt, cdn, "cost_center", frm.doc.cost_center);
				}
			}, 150);
		}
	}
});

frappe.ui.form.on("Material Request", {
	refresh(frm) {
		// CC filter: show only CCs where user is Creator (if CC config exists)
		_setup_cc_filter(frm);
		// Show stock availability columns in items table
		_setup_stock_columns(frm);
		// Override item filter: allow non-stock items for Service Request
		_setup_item_query(frm);
		if (frm.is_new()) return;
		// v2.9.9 — Stores flow branch (Material Transfer + Material Issue):
		// uses Stores Manager workflow, NOT the Purchase chain. Skip
		// _load_mr_context, budget banner, AVP banner, Service Request
		// PO button — none of those apply.
		if (frm.doc.material_request_type === "Material Transfer"
				|| frm.doc.material_request_type === "Material Issue") {
			_ts_add_mr_print_button(frm);
			_load_transfer_context(frm);
			return;
		}
		// Custom print button (direct PDF, bypasses Frappe print preview)
		_ts_add_mr_print_button(frm);
		// Load approval context first (adds buttons), then budget banner
		_load_mr_context(frm);
		// v2.8.10: bilingual approval banner
		if (typeof window.ts_render_approval_banner === "function") {
			window.ts_render_approval_banner(frm);
		}
		// Show budget warning if CC is set (after approval context)
		if (frm.doc.cost_center) {
			setTimeout(() => _check_cc_budget(frm), 500);
		}
		// Service Request: PO creation via custom v2.8.11.4 helper that
		// flips type before calling the standard mapper. ERPNext native
		// make_purchase_order rejects Service Request type; helper
		// converts to Purchase + logs audit comment + returns the PO.
		if (frm.doc.material_request_type === "Service Request"
			&& frm.doc.docstatus === 1
			&& frm.doc.per_ordered < 100) {
			frm.add_custom_button(__("Purchase Order"),
				() => _ts_create_po_from_service_request(frm),
				__("Create"));
		}
		// Post-approval "Request Revision" button (Path 3 — soft revise / notify-only).
		// Added directly in refresh() so it works regardless of approval context load state.
		_maybe_add_post_approval_revision_button(frm);
	},
	// v2.9.8.36 — preserve cost_center when ts_project (MR's project field) changes.
	// MR has NO native `project` field at header level — only ts_project Custom Field.
	// The framework's Link-field refresh can wipe cost_center as a side-effect of
	// any dimension/link change. Snapshot+restore handler covers it.
	ts_project(frm) {
		const cc_before = frm.doc.cost_center;
		if (!cc_before) return;
		setTimeout(() => {
			if (frm.doc.cost_center !== cc_before) {
				frm.set_value("cost_center", cc_before);
			}
		}, 200);
	},
	cost_center(frm) {
		if (!frm.doc.cost_center) return;
		// v2.9.0.6 (Bug 11.F): propagate parent CC to all existing item rows
		// so user doesn't hit User Permission errors mid-form when item rows
		// inherited a different (Item.default_cost_center) CC like "Main - BBPL".
		(frm.doc.items || []).forEach(row => {
			if (row.cost_center !== frm.doc.cost_center) {
				frappe.model.set_value(row.doctype, row.name, "cost_center", frm.doc.cost_center);
			}
		});
		// Check if selected CC is Direct PO
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config.check_direct_po_cc",
			args: { cost_center: frm.doc.cost_center },
			callback(r) {
				if (r.message && r.message.is_direct_po) {
					frappe.msgprint({
						title: __("Direct PO Cost Center"),
						indicator: "orange",
						message: r.message.message,
					});
				}
			}
		});
		// Show budget status warning
		_check_cc_budget(frm);
	},
	before_submit(frm) {
		// v2.8.10: bilingual submit-on-behalf warning
		if (typeof window.ts_check_submit_on_behalf === "function") {
			return window.ts_check_submit_on_behalf(frm).then(proceed => {
				if (!proceed) {
					frappe.validated = false;  // blocks submit
					return Promise.reject();
				}
			});
		}
	}
});

function _setup_cc_filter(frm) {
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config.get_user_allowed_cost_centers",
		async: false,
		callback(r) {
			const allowed_ccs = r.message || [];
			if (allowed_ccs.length > 0) {
				// User has CC restriction — filter dropdown
				frm.set_query("cost_center", function() {
					return {
						filters: { "is_group": 0, "name": ["in", allowed_ccs] }
					};
				});
			} else {
				// No restriction (unrestricted role or no CC configs) — show all
				frm.set_query("cost_center", function() {
					return { filters: { "is_group": 0 } };
				});
			}
		}
	});
}

function _load_mr_context(frm) {
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_po_approval.get_approval_context",
		args: { doctype: "Material Request", docname: frm.doc.name },
		callback(r) {
			if (!r.message) return;
			const ctx = r.message;
			if (!ctx.approval_enabled) return;

			_render_mr_status(frm, ctx);
			_render_mr_stepper(frm, ctx);
			_render_mr_info(frm, ctx);
			_lock_mr_fields(frm, ctx);
			_render_mr_timeline(frm);
			// Delay button rendering to ensure Frappe's own render cycle is complete
			setTimeout(() => {
				_hide_standard_submit(frm, ctx);
				_render_mr_buttons(frm, ctx);
			}, 200);
		},
		error() { console.warn("TS: MR approval context failed"); }
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
		if (status.startsWith("On Hold")) color = "orange";
		else if (status.startsWith("Pending")) color = "blue";
		else color = "blue";
	}

	frm.page.set_indicator(status, color);

	// Show hold reason banner — use our own container (not set_headline which Frappe wipes on refresh)
	if (ctx.is_on_hold && ctx.hold_reason) {
		_show_ts_banner(frm, "hold",
			`<span style="color: #f59e0b; font-weight: 600;">⏸ On Hold</span> — ${frappe.utils.escape_html(ctx.hold_reason)}`,
			"#fffbeb", "#f59e0b"
		);
	}
}

function _hide_standard_submit(frm, ctx) {
	// Only hide standard Submit on Draft MRs (docstatus=0).
	// Do NOT clear primary action on Submitted (Cancel) or Cancelled (Amend) MRs.
	if (frm.doc.docstatus === 0) {
		frm.page.clear_primary_action();
		if (ctx.status === "Rejected") {
			frm.disable_save();
		}
	}
	// Hide Frappe's "Submit this document" intro + standard Submit button
	_hide_frappe_submit_ui(frm);
	// Keep checking for 2 seconds — Frappe may re-add them after async calls
	let checks = 0;
	const interval = setInterval(() => {
		_hide_frappe_submit_ui(frm);
		checks++;
		if (checks > 10) clearInterval(interval);
	}, 200);
}

function _hide_frappe_submit_ui(frm) {
	// Hide "Submit this document to confirm" intro banner
	$(frm.page.wrapper).find(".form-message.blue").each(function() {
		if ($(this).text().indexOf("Submit this document") !== -1) {
			$(this).hide();
		}
	});
	// Hide standard Submit button (not our "Submit for Approval")
	$(frm.page.wrapper).find('.btn-primary-dark:contains("Submit")').not(':contains("Approval")').hide();
}

// ═══════════════════════════════════════════════════════════
//  TS BANNER — our own container that Frappe can't wipe
// ═══════════════════════════════════════════════════════════

function _show_ts_banner(frm, key, html, bgColor, borderColor) {
	// Remove old banner with same key
	$(frm.page.wrapper).find(`.ts-banner[data-key="${key}"]`).remove();

	const banner = `<div class="ts-banner" data-key="${key}" style="
		padding: 8px 15px;
		margin: 0 15px 8px;
		background: ${bgColor};
		border-left: 3px solid ${borderColor};
		border-radius: 4px;
		font-size: 12px;
	">${html}</div>`;

	// Insert after form-dashboard-section (always exists, Frappe doesn't touch children we add)
	const $dashboard = $(frm.page.wrapper).find(".form-dashboard-section");
	if ($dashboard.length) {
		$dashboard.after(banner);
	}
}

function _render_mr_stepper(frm, ctx) {
	// Remove old stepper
	$(frm.fields_dict.ts_mr_section?.wrapper || frm.page.wrapper)
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

	// Only inject animation CSS once
	if (!document.getElementById("bbf-mr-pulse-style")) {
		html += `<style id="bbf-mr-pulse-style">
			@keyframes bbf-mr-pulse {
				0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
				50% { box-shadow: 0 0 0 8px rgba(59, 130, 246, 0); }
			}
		</style>`;
	}

	const $section = $(frm.fields_dict.ts_mr_section?.wrapper);
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
		_show_ts_banner(frm, "info", info, "#eff6ff", "#3b82f6");
	}
}

function _render_mr_buttons(frm, ctx) {
	$(".bbf-mr-btn").remove();

	if (ctx.can_submit_for_approval) {
		frm.add_custom_button(__("Submit for Approval"), () => {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.ts_po_approval.get_submit_target",
				args: { doctype: "Material Request", docname: frm.doc.name },
				freeze: true,
				freeze_message: __("Checking..."),
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

	if (ctx.can_hold) {
		frm.add_custom_button(__("Hold"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Put MR On Hold"),
				fields: [
					{ fieldtype: "Small Text", fieldname: "reason", label: __("Hold Reason"), reqd: 1,
					  description: __("Explain why this MR is being put on hold") }
				],
				primary_action_label: __("Put On Hold"),
				primary_action(values) {
					d.hide();
					frappe.call({
						method: "trustbit_ethanol.ts_gate_entry.ts_po_approval.hold_mr",
						args: { docname: frm.doc.name, reason: values.reason },
						freeze: true,
						freeze_message: __("Putting on hold..."),
						callback() { frm.reload_doc(); },
						error() { frm.reload_doc(); }
					});
				}
			});
			d.show();
		}, null).addClass("bbf-mr-btn").css({"background-color": "#f59e0b", "color": "white"});
	}

	if (ctx.can_resume) {
		frm.add_custom_button(__("Resume"), () => {
			frappe.confirm(
				__("Resume this MR from hold? It will return to pending approval."),
				() => {
					frappe.call({
						method: "trustbit_ethanol.ts_gate_entry.ts_po_approval.resume_mr",
						args: { docname: frm.doc.name },
						freeze: true,
						freeze_message: __("Resuming..."),
						callback() { frm.reload_doc(); },
						error() { frm.reload_doc(); }
					});
				}
			);
		}, null).addClass("btn-primary bbf-mr-btn");
	}

	if (ctx.can_resubmit) {
		frm.add_custom_button(__("Resubmit for Approval"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint({
					title: __("Unsaved Changes"),
					message: __("Please save your changes first, then click Resubmit for Approval."),
					indicator: "orange"
				});
				return;
			}
			frappe.confirm(
				__("Resubmit this MR for approval?"),
				() => _call_mr_action(frm, "resubmit_document", { doctype: "Material Request", docname: frm.doc.name })
			);
		}, null).addClass("btn-primary bbf-mr-btn");
	}

	// v2.9.7 — AVP Deputy mode: CEO can approve at Pending AVP when AVP unavailable.
	// Button visibility = (status==Pending AVP) AND (user has CEO role) AND (kill switch ON).
	// Server re-validates everything; this is just UX gating.
	if (frm.doc.ts_mr_status === "Pending AVP" && frappe.user_roles.includes("CEO")) {
		_ts_render_avp_deputy_button(frm);
	}
}

function _ts_render_avp_deputy_button(frm) {
	// v2.9.7 — uses dedicated whitelisted helper because the kill-switch field
	// is at permlevel=1 (IT Head only) and frappe.client.get_value strips it
	// from the response for CEO. The helper does a raw-SQL read of tabSingles
	// (same pattern as server-side _is_deputy_mode_enabled).
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_avp_deputy.is_avp_deputy_enabled",
		callback(r) {
			if (!r || !r.message) return;
			frm.add_custom_button(__("Approve as AVP Deputy"), () => {
				_ts_open_avp_deputy_dialog(frm);
			}, __("Actions")).addClass("btn-warning bbf-mr-btn");
		}
	});
}

function _ts_open_avp_deputy_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Approve as AVP Deputy"),
		fields: [
			{
				fieldtype: "HTML",
				options: `<div class="alert alert-warning" style="margin-bottom:10px;">
					<b>${__("AVP Deputy Approval")}</b><br>
					${__("You are approving this MR on behalf of AVP. This is logged in the audit trail and AVP + creator will be emailed.")}
				</div>`
			},
			{
				fieldtype: "Small Text",
				fieldname: "reason",
				label: __("Reason (optional but recommended)"),
				description: __("Why are you approving on behalf of AVP? Example: 'AVP unavailable till Friday' or 'Urgent — AVP travelling.'")
			}
		],
		primary_action_label: __("Approve as AVP Deputy"),
		primary_action(values) {
			d.hide();
			frappe.confirm(
				__("Approve this MR on behalf of AVP? This action is logged."),
				() => {
					frappe.call({
						method: "trustbit_ethanol.ts_gate_entry.ts_avp_deputy.approve_as_avp_deputy",
						type: "POST",
						args: {
							mr_name: frm.doc.name,
							reason: values.reason || ""
						},
						freeze: true,
						freeze_message: __("Approving as AVP Deputy..."),
						callback(r) {
							if (r && r.message && r.message.ok) {
								frappe.show_alert({
									message: __("Approved. New status: {0}", [r.message.new_status || ""]),
									indicator: "green"
								}, 5);
								frm.reload_doc();
							}
						}
					});
				}
			);
		}
	});
	d.show();
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
		method: `trustbit_ethanol.ts_gate_entry.ts_po_approval.${method}`,
		args,
		freeze: true,
		freeze_message: __("Processing..."),
		callback() { frm.reload_doc(); },
		error() { frm.reload_doc(); }
	});
}

function _lock_mr_fields(frm, ctx) {
	const status = ctx.status || "";
	const should_lock = ctx.is_pending || ctx.is_on_hold || status === "Rejected";
	if (!should_lock) return;

	// v2.8.12 — CEO + MD get unrestricted field access on unsubmitted docs.
	// Server-side before_save is the source of truth; Python re-checks roles.
	if (frm.doc.docstatus === 0 && typeof window.ts_is_executive_override_user === "function"
		&& window.ts_is_executive_override_user()) {
		return;
	}

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
	$(frm.fields_dict.ts_mr_log_section?.wrapper).find(".bbf-mr-timeline").remove();

	const logs = frm.doc.ts_mr_log || [];
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
			"Held": "#f59e0b",
			"Resumed": "#3b82f6",
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

	const $section = $(frm.fields_dict.ts_mr_log_section?.wrapper);
	if ($section.length) {
		$section.prepend(html);
	}
}


// ═══════════════════════════════════════════════════════════════
//  ITEM QUERY — allow non-stock items for Service Request
// ═══════════════════════════════════════════════════════════════

function _setup_item_query(frm) {
	frm.set_query("item_code", "items", function(doc) {
		if (doc.material_request_type === "Service Request") {
			return {
				query: "erpnext.controllers.queries.item_query",
				filters: { is_purchase_item: 1 },
			};
		}
		// Default: let ERPNext handle it (stock items for Stock, purchase items for Purchase)
		return {};
	});
}


// ═══════════════════════════════════════════════════════════════
//  STOCK AVAILABILITY IN MR ITEMS TABLE
// ═══════════════════════════════════════════════════════════════

function _setup_stock_columns(frm) {
	try {
		// Force columns via frappe.meta (the canonical way — survives __UserSettings)
		["ts_delivery_location", "ts_item_remark", "actual_qty"].forEach(fn => {
			let df = frappe.meta.get_docfield("Material Request Item", fn, frm.doc.name);
			if (df) {
				df.in_list_view = 1;
				df.columns = 2;
			}
		});

		const grid = frm.fields_dict.items?.grid;
		if (!grid) return;

		// Also force on existing rows
		(grid.grid_rows || []).forEach(row => {
			(row.docfields || []).forEach(f => {
				if (["ts_delivery_location", "ts_item_remark", "actual_qty"].includes(f.fieldname)) {
					f.in_list_view = 1;
					f.columns = 2;
				}
			});
		});

		grid.refresh();
		_colorize_stock_rows(frm);
	} catch(e) {
		// Silently fail — stock columns are cosmetic, don't break approval flow
	}
}

function _colorize_stock_rows(frm) {
	// Add color indicators to actual_qty cells in the grid
	setTimeout(() => {
		const grid = frm.fields_dict.items?.grid;
		if (!grid) return;

		grid.grid_rows.forEach(row => {
			const qty = flt(row.doc.qty);
			const actual = flt(row.doc.actual_qty);
			const $row = $(row.row);

			// Find the actual_qty cell
			const $cell = $row.find('[data-fieldname="actual_qty"]');
			if (!$cell.length) return;

			// Remove old badges
			$cell.find(".stock-badge").remove();

			if (!row.doc.item_code) return;

			let color, label;
			if (actual <= 0) {
				color = "#ef4444"; label = "Out of Stock";
			} else if (actual < qty) {
				color = "#f59e0b"; label = "Low Stock";
			} else {
				color = "#10b981"; label = "In Stock";
			}

			$cell.find(".static-area, .like-disabled-input").css("color", color).css("font-weight", "bold");
			$cell.append(
				'<span class="stock-badge" style="font-size:9px;color:' + color +
				';display:block;margin-top:-2px;">' + label + '</span>'
			);
		});
	}, 300);
}

// Refresh stock colors when item or warehouse changes
frappe.ui.form.on("Material Request Item", {
	item_code(frm) {
		setTimeout(() => _colorize_stock_rows(frm), 500);
	},
	warehouse(frm) {
		setTimeout(() => _colorize_stock_rows(frm), 500);
	},
	qty(frm) {
		setTimeout(() => _colorize_stock_rows(frm), 300);
	},
	items_add(frm) {
		setTimeout(() => _colorize_stock_rows(frm), 500);
	},
	items_remove(frm) {
		setTimeout(() => _colorize_stock_rows(frm), 300);
	}
});


// ═══════════════════════════════════════════════════════════════
//  BUDGET WARNING ON MR (informational only, no block)
// ═══════════════════════════════════════════════════════════════

function _check_cc_budget(frm) {
	if (!frm.doc.cost_center) return;

	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_budget.get_cc_budget_status",
		args: { cost_center: frm.doc.cost_center },
		callback(r) {
			// Remove old budget banner
			$(frm.page.wrapper).find(".bbf-budget-banner").remove();

			if (!r.message || !r.message.has_budget) return;

			const d = r.message;
			const pct = d.budget_monthly > 0 ? (d.used / d.budget_monthly * 100) : 0;

			let color, icon, status_text;
			if (d.used > d.budget_monthly) {
				color = "#ef4444"; icon = "⛔"; status_text = "EXCEEDED";
			} else if (pct > 80) {
				color = "#f59e0b"; icon = "⚠️"; status_text = "Nearing Limit";
			} else {
				color = "#10b981"; icon = "✅"; status_text = "Within Budget";
			}

			const fmt = (v) => "₹" + Math.round(v).toLocaleString("en-IN");

			const html = `
				<div class="bbf-budget-banner" style="
					background: ${d.used > d.budget_monthly ? '#fef2f2' : pct > 80 ? '#fffbeb' : '#ecfdf5'};
					border: 1px solid ${color};
					border-radius: 8px;
					padding: 10px 16px;
					margin: 8px 0;
					font-size: 12px;
				">
					<div style="font-weight:bold;color:${color};margin-bottom:4px;">
						${icon} Budget Status: ${status_text} (${d.month_name})
					</div>
					<table style="border:none;width:100%;font-size:11px;">
						<tr>
							<td style="border:none;padding:2px 8px;">Monthly Budget: <strong>${fmt(d.budget_monthly)}</strong></td>
							<td style="border:none;padding:2px 8px;">Used (PO committed): <strong style="color:${pct > 100 ? '#ef4444' : '#374151'}">${fmt(d.used)}</strong></td>
							<td style="border:none;padding:2px 8px;">Remaining: <strong style="color:${d.remaining < 0 ? '#ef4444' : '#10b981'}">${fmt(d.remaining)}</strong></td>
							<td style="border:none;padding:2px 8px;">Utilization: <strong>${pct.toFixed(1)}%</strong></td>
						</tr>
					</table>
				</div>`;

			// Insert after cost_center field
			const $cc_field = $(frm.fields_dict.cost_center?.wrapper);
			if ($cc_field.length) {
				$cc_field.after(html);
			}
		}
	});
}

// ═══════════════════════════════════════════════════════════
//  v2.8.11.4 — Service Request → Purchase Order helper
// ═══════════════════════════════════════════════════════════

function _ts_create_po_from_service_request(frm) {
	// Bilingual confirmation — ERPNext mapper rejects Service Request type;
	// this flips to Purchase + logs audit before creating PO.
	const en = "This MR is a Service Request. Creating a Purchase Order will convert its type to Purchase. Proceed?";
	const hi = "यह सामग्री अनुरोध एक सेवा अनुरोध है। क्रय आदेश बनाने पर इसका प्रकार Purchase में बदल जाएगा। आगे बढ़ें?";
	const dialog_html = `
		<div style="padding: 6px 0; font-size: 13px; line-height: 1.55;">
			<div style="margin-bottom: 10px;">${en}</div>
			<div style="color: #475569; padding-top: 8px; border-top: 1px dashed #cbd5e1;">${hi}</div>
		</div>
	`;
	const d = new frappe.ui.Dialog({
		title: __("Convert Service Request → Purchase Order"),
		fields: [{ fieldtype: "HTML", options: dialog_html }],
		primary_action_label: __("Proceed"),
		primary_action: () => {
			d.hide();
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.ts_sr_to_po.convert_sr_to_po",
				args: { mr_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Creating Purchase Order..."),
				callback: function (r) {
					if (r && r.message) {
						// frappe returns a frappe.get_doc-like dict; sync + open
						frappe.model.sync(r.message);
						frappe.set_route("Form", "Purchase Order", r.message.name);
					}
				},
				error: function () {
					// Frappe's native AJAX error handler shows server frappe.throw msgs.
					// Don't double-display.
				},
			});
		},
		secondary_action_label: __("Cancel"),
		secondary_action: () => d.hide(),
	});
	d.show();
}


// ═══════════════════════════════════════════════════════════
//  CUSTOM PRINT BUTTON — Direct PDF download
// ═══════════════════════════════════════════════════════════

function _ts_add_mr_print_button(frm) {
	// Hide ONLY Frappe's print icon + menu item
	setTimeout(() => {
		frm.page.wrapper.find('.btn[data-original-title="Print"], .btn-print-preview, a[title="Print"]').hide();
		frm.page.menu_btn_group.find('.dropdown-item').each(function() {
			if ($(this).text().trim() === "Print") $(this).hide();
		});
	}, 500);

	// Standalone print button (not inside Actions)
	frm.add_custom_button(__("🖨 Print PDF"), () => {
		// v2.9.8.34: BBPL Material Request added as the new default format.
		const formats = ["BBPL Material Request", "TS Material Request", "TS Material Request (Clean)"];
		frappe.prompt({
			fieldtype: "Select",
			label: "Print Format",
			fieldname: "format",
			options: formats.join("\n"),
			default: "BBPL Material Request",
			reqd: 1,
		}, (values) => {
			const url = `/api/method/frappe.utils.print_format.download_pdf?doctype=Material%20Request&name=${encodeURIComponent(frm.doc.name)}&format=${encodeURIComponent(values.format)}&no_letterhead=0`;
			window.open(url, "_blank");
		}, __("Select Print Format"), __("Download PDF"));
	});
}

// ═══════════════════════════════════════════════════════════
//  POST-APPROVAL REVISION REQUEST (Path 3 — soft revise / notify-only)
//  Added for Purchase User / Purchase Manager / IT Head so they can
//  ask the creator to cancel + amend an already-approved MR.
// ═══════════════════════════════════════════════════════════

function _maybe_add_post_approval_revision_button(frm) {
	// Only show on submitted MRs (docstatus = 1)
	if (frm.doc.docstatus !== 1) return;

	// Role check (client-side — server re-validates)
	const allowed_roles = [
		"Purchase User", "Purchase Manager", "IT Head",
		"AVP", "CEO", "MD", "System Manager", "Administrator"
	];
	const user_roles = frappe.user_roles || [];
	if (!allowed_roles.some(r => user_roles.includes(r))) return;

	// Remove any previous instance of THIS button only (unique class — don't share with bbf-mr-btn
	// because _render_mr_buttons() does a global $(".bbf-mr-btn").remove() that would kill us).
	$(".bbf-mr-rev-req-btn").remove();

	frm.add_custom_button(__("Request Revision"), () => {
		// Pre-flight check: query linked POs
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_mr_post_approval_revision.can_request_revision",
			args: { mr_name: frm.doc.name },
			callback(r) {
				const info = r.message || {};
				if (!info.allowed) {
					const reasons = {
						"role_missing": __("You don't have permission to request MR revision."),
						"mr_not_found": __("MR not found."),
						"not_submitted": __("Revision request only allowed on approved (submitted) MRs.")
					};
					frappe.msgprint({
						title: __("Cannot Request Revision"),
						message: reasons[info.reason] || __("Not allowed."),
						indicator: "red"
					});
					return;
				}

				let warning_html = "";
				if (info.has_linked_pos) {
					const po_list = (info.linked_pos || []).slice(0, 5).join(", ");
					const more = (info.linked_pos || []).length > 5 ? ` (+ ${info.linked_pos.length - 5} more)` : "";
					warning_html = `
						<div style="background: #fee2e2; border-left: 3px solid #ef4444; padding: 10px 14px; border-radius: 4px; margin-bottom: 14px; color: #991b1b;">
							<b>⚠ Blocked:</b> ${info.linked_pos.length} Purchase Order(s) already created from this MR:
							<br><code>${frappe.utils.escape_html(po_list)}${more}</code>
							<br><br>Cancel or amend those POs first, then request revision. The creator cannot cancel this MR while POs reference it.
						</div>
					`;
				}

				const d = new frappe.ui.Dialog({
					title: __("Request MR Revision"),
					fields: [
						{
							fieldtype: "HTML",
							fieldname: "info_html",
							options: `
								${warning_html}
								<div style="background: #eff6ff; border-left: 3px solid #3b82f6; padding: 10px 14px; border-radius: 4px; margin-bottom: 12px; font-size: 12px;">
									<b>ℹ How this works:</b>
									<ul style="margin: 6px 0 0 16px; padding: 0;">
										<li>This is a <b>notification-only</b> request — the MR state is NOT changed</li>
										<li>The creator will get a bell notification + email with your reason</li>
										<li>They must manually <b>Cancel → Amend</b> the MR to apply changes</li>
										<li>An audit log entry will be added to this MR's timeline</li>
									</ul>
								</div>
							`
						},
						{
							fieldtype: "Small Text",
							fieldname: "reason",
							label: __("Reason for revision"),
							reqd: 1,
							description: __("Explain what needs to change. This will be shown to the creator.")
						}
					],
					primary_action_label: info.has_linked_pos ? __("Blocked by linked POs") : __("Send Revision Request"),
					primary_action(values) {
						if (info.has_linked_pos) {
							frappe.msgprint({
								title: __("Cannot Proceed"),
								message: __("Cancel the linked POs first."),
								indicator: "red"
							});
							return;
						}
						if (!values.reason || !values.reason.trim()) {
							frappe.msgprint({
								title: __("Reason Required"),
								message: __("Please enter a reason for the revision request."),
								indicator: "orange"
							});
							return;
						}
						d.hide();
						frappe.call({
							method: "trustbit_ethanol.ts_gate_entry.ts_mr_post_approval_revision.request_mr_revision",
							args: { mr_name: frm.doc.name, reason: values.reason.trim() },
							freeze: true,
							freeze_message: __("Sending revision request..."),
							callback(res) {
								if (res.message && res.message.status === "ok") {
									frappe.show_alert({
										message: res.message.message || __("Revision request sent."),
										indicator: "green"
									}, 7);
									frm.reload_doc();
								}
							}
						});
					}
				});

				if (info.has_linked_pos) {
					// Disable the primary action button visually
					setTimeout(() => {
						d.get_primary_btn().prop("disabled", true).css("opacity", "0.5");
					}, 100);
				}

				d.show();
			}
		});
	}, null).addClass("bbf-mr-rev-req-btn").css({"background-color": "#f59e0b", "color": "white", "border-color": "#d97706"});
}


// ═══════════════════════════════════════════════════════════════════════
//  v2.9.9 — Material Transfer Independent Flow
// ═══════════════════════════════════════════════════════════════════════

function _load_transfer_context(frm) {
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_po_approval.get_approval_context",
		args: {doctype: "Material Request", docname: frm.doc.name},
		callback(r) {
			if (!r || !r.message) return;
			const ctx = r.message;
			if (ctx.flow !== "transfer") return;  // safety net

			// Status indicator (color-coded pill)
			const STATUS_COLOR = {
				"Not Submitted": "gray",
				"Pending Stores Manager": "orange",
				"Approved": "green",
				"Rejected": "red",
			};
			const color = STATUS_COLOR[ctx.ts_mr_status] || "gray";
			frm.page.set_indicator(__(ctx.ts_mr_status), color);

			// Render action buttons from server-computed list
			(ctx.actions || []).forEach((action) => {
				const handler = () => _trigger_transfer_action(frm, action);
				const btn = frm.add_custom_button(
					(action.icon ? action.icon + " " : "") + action.label,
					handler,
					__("Stores Workflow")
				);
				if (action.primary) {
					btn.addClass("btn-primary");
				}
				if (action.color === "red") {
					btn.css({"background-color": "#dc2626", "color": "white",
					         "border-color": "#b91c1c"});
				} else if (action.color === "green") {
					btn.css({"background-color": "#16a34a", "color": "white",
					         "border-color": "#15803d"});
				}
			});

			// Open Stock Entry button (if SE created)
			if (ctx.stock_entry) {
				frm.add_custom_button(__("📦 Open Stock Entry"), () => {
					frappe.set_route("Form", "Stock Entry", ctx.stock_entry);
				}, __("Stores Workflow")).addClass("btn-info");
			}
		},
		error(err) {
			console.error("Transfer context load failed:", err);
		},
	});
}

function _trigger_transfer_action(frm, action) {
	if (action.prompt_reason) {
		// Reject — prompt for reason ≥10 chars
		frappe.prompt({
			fieldname: "reason",
			fieldtype: "Small Text",
			label: __("Rejection Reason"),
			reqd: 1,
			description: __("Minimum 10 characters."),
		}, (values) => {
			const reason = (values.reason || "").trim();
			if (reason.length < 10) {
				frappe.msgprint(__("Reason must be at least 10 characters."));
				return;
			}
			_call_transfer_endpoint(frm, action.key, {reason: reason});
		}, __("Reject Material Transfer"), __("Reject"));
		return;
	}

	const confirm_msg = action.confirm_msg || __("Continue with this action?");
	frappe.confirm(confirm_msg, () => {
		_call_transfer_endpoint(frm, action.key, {});
	});
}

function _call_transfer_endpoint(frm, action_key, extra_args) {
	const method_map = {
		"submit_for_stores_approval": "trustbit_ethanol.ts_gate_entry.ts_mr_transfer.submit_for_stores_approval",
		"approve_transfer": "trustbit_ethanol.ts_gate_entry.ts_mr_transfer.approve_transfer",
		"reject_transfer": "trustbit_ethanol.ts_gate_entry.ts_mr_transfer.reject_transfer",
	};
	const method = method_map[action_key];
	if (!method) {
		frappe.msgprint(__("Unknown action: {0}", [action_key]));
		return;
	}
	const args = Object.assign({mr_name: frm.doc.name}, extra_args);
	frappe.call({
		method: method,
		args: args,
		freeze: true,
		freeze_message: __("Processing..."),
		callback(r) {
			if (r && r.message) {
				if (r.message.message) {
					frappe.show_alert({message: r.message.message, indicator: "green"}, 7);
				}
				frm.reload_doc();
			}
		},
	});
}
