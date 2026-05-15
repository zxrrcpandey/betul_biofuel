// TS Budget Override Approval — v2.10.0 form script
// Renders breach snapshot banner + Approve / Reject / Cancel buttons for
// CEO/MD/System Manager. Per plan Q4: Reject requires typed source-doc-name + comment ≥10 chars.
// Per plan Q9: status pill drives the list-view indicator (configured server-side).

frappe.ui.form.on("TS Budget Override Approval", {
	refresh(frm) {
		_bo_render_breach_summary(frm);
		_bo_add_open_source_button(frm);
		_bo_add_action_buttons(frm);
	},
});

function _bo_render_breach_summary(frm) {
	if (frm.is_new()) return;
	$(frm.page.wrapper).find('.ts-banner[data-key="bo-breach"]').remove();
	const d = frm.doc;
	if (!d.breach_type) return;

	const fmtCurrency = (v) => format_currency(v, frappe.defaults.get_default("currency"));
	const annualOver = d.annual_breach_delta > 0;
	const monthlyOver = d.monthly_breach_delta > 0;

	let bgColor, borderColor, icon, label;
	if (d.status === "Pending CEO") {
		bgColor = "#fffbeb"; borderColor = "#f59e0b"; icon = "⏸";
		label = __("Pending CEO budget approval");
	} else if (d.status === "Approved") {
		bgColor = "#ecfdf5"; borderColor = "#10b981"; icon = "✓";
		label = __("Approved — source doc resumed normal route");
	} else if (d.status === "Rejected") {
		bgColor = "#fef2f2"; borderColor = "#ef4444"; icon = "✗";
		label = __("Rejected — source doc sent to Rejected state");
	} else {
		bgColor = "#f3f4f6"; borderColor = "#6b7280"; icon = "○";
		label = __("Cancelled");
	}

	const annualBlock = annualOver
		? `<div><b>${__("Annual")}:</b> ${fmtCurrency(d.annual_available)} ${__("available")} · <span style="color:#dc2626;">${__("breach")}: ${fmtCurrency(d.annual_breach_delta)}</span> (${(d.annual_utilization_pct || 0).toFixed(1)}%)</div>`
		: "";
	const monthlyBlock = monthlyOver
		? `<div><b>${__("Monthly")} (${frappe.utils.escape_html(d.period_month || "")}):</b> ${fmtCurrency(d.monthly_available)} ${__("available")} · <span style="color:#dc2626;">${__("breach")}: ${fmtCurrency(d.monthly_breach_delta)}</span> (${(d.monthly_utilization_pct || 0).toFixed(1)}%)</div>`
		: "";

	const html = `
		<div>
			<div style="color:${borderColor};font-weight:600;margin-bottom:4px;">${icon} ${frappe.utils.escape_html(label)}</div>
			<div><b>${__("Source")}:</b> ${frappe.utils.escape_html(d.reference_doctype)} ${frappe.utils.escape_html(d.reference_name)} · <b>${__("Amount")}:</b> ${fmtCurrency(d.source_amount)} · <b>${__("Cost Center")}:</b> ${frappe.utils.escape_html(d.cost_center)}</div>
			${annualBlock}
			${monthlyBlock}
		</div>`;

	const banner = `<div class="ts-banner" data-key="bo-breach" style="
		padding: 10px 15px;
		margin: 0 15px 8px;
		background: ${bgColor};
		border-left: 3px solid ${borderColor};
		border-radius: 4px;
		font-size: 12px;
	">${html}</div>`;
	const $dashboard = $(frm.page.wrapper).find(".form-dashboard-section");
	if ($dashboard.length) $dashboard.after(banner);
}

function _bo_add_open_source_button(frm) {
	if (frm.is_new()) return;
	if (!frm.doc.reference_doctype || !frm.doc.reference_name) return;
	frm.add_custom_button(
		__("Open Source {0}", [__(frm.doc.reference_doctype)]),
		() => {
			frappe.set_route("Form", frm.doc.reference_doctype, frm.doc.reference_name);
		}
	);
}

function _bo_add_action_buttons(frm) {
	if (frm.is_new()) return;
	if (frm.doc.docstatus !== 1) return;
	if (frm.doc.status !== "Pending CEO") return;

	const roles = frappe.user_roles || [];
	const isApprover = roles.includes("CEO") || roles.includes("MD") || roles.includes("System Manager");
	const isSubmitter = (frm.doc.submitted_by === frappe.session.user);

	if (isApprover) {
		// Green "Approve Budget" — primary.
		const $approve = frm.add_custom_button(__("Approve Budget"), () => _bo_show_approve_dialog(frm));
		$approve && $approve.addClass("btn-success").css({ "background": "#10b981", "border-color": "#10b981", "color": "#fff" });
		// Red "Reject" — secondary; needs typed source-name + comment ≥10.
		const $reject = frm.add_custom_button(__("Reject"), () => _bo_show_reject_dialog(frm));
		$reject && $reject.addClass("btn-danger").css({ "background": "#ef4444", "border-color": "#ef4444", "color": "#fff" });
	}

	if (isSubmitter || roles.includes("System Manager")) {
		frm.add_custom_button(__("Cancel Request"), () => _bo_show_cancel_dialog(frm));
	}
}

function _bo_show_approve_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Approve Budget Override"),
		fields: [
			{
				fieldname: "comment", fieldtype: "Small Text", label: __("Comment (optional)"),
				description: __("Visible to submitter + next approver in the chain."),
			},
		],
		primary_action_label: __("Approve"),
		primary_action(values) {
			d.disable_primary_action();
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.ts_budget_override.approve_budget_override",
				args: { name: frm.doc.name, comment: values.comment || "" },
				callback(r) {
					d.hide();
					if (r && r.message && r.message.ok) {
						frappe.show_alert({ message: __("Approved. Source doc resuming normal route."), indicator: "green" }, 5);
						frm.reload_doc();
					}
				},
				error() {
					d.enable_primary_action();
				},
			});
		},
	});
	d.show();
}

function _bo_show_reject_dialog(frm) {
	const expectedName = frm.doc.reference_name || "";
	const d = new frappe.ui.Dialog({
		title: __("Reject Budget Override"),
		fields: [
			{
				fieldname: "intro", fieldtype: "HTML",
				options: `<div style="background:#fef2f2;border-left:3px solid #ef4444;padding:8px 12px;border-radius:4px;margin-bottom:10px;font-size:12px;">
					${__("Rejecting will move {0} <b>{1}</b> to <b>Rejected</b> status. The user must Amend or create a new doc.",
					  [__(frm.doc.reference_doctype), frappe.utils.escape_html(expectedName)])}
				</div>`,
			},
			{
				fieldname: "confirm_name", fieldtype: "Data", reqd: 1,
				label: __("Type source name to confirm: {0}", [expectedName]),
				description: __("Anti-fat-finger gate. Must match exactly."),
			},
			{
				fieldname: "comment", fieldtype: "Small Text", reqd: 1,
				label: __("Rejection reason (≥10 chars)"),
				description: __("Comment is sent to the submitter and stored on the override audit trail."),
			},
		],
		primary_action_label: __("Reject"),
		primary_action(values) {
			if ((values.confirm_name || "").trim() !== expectedName) {
				frappe.msgprint({ message: __("Typed name does not match. Aborting."), indicator: "red" });
				return;
			}
			if (((values.comment || "").trim()).length < 10) {
				frappe.msgprint({ message: __("Rejection comment must be at least 10 characters."), indicator: "red" });
				return;
			}
			d.disable_primary_action();
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.ts_budget_override.reject_budget_override",
				args: { name: frm.doc.name, comment: values.comment.trim() },
				callback(r) {
					d.hide();
					if (r && r.message && r.message.ok) {
						frappe.show_alert({ message: __("Rejected. Source doc moved to Rejected status."), indicator: "red" }, 5);
						frm.reload_doc();
					}
				},
				error() {
					d.enable_primary_action();
				},
			});
		},
	});
	d.show();
}

function _bo_show_cancel_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Cancel Budget Override Request"),
		fields: [
			{
				fieldname: "reason", fieldtype: "Small Text", reqd: 1,
				label: __("Cancellation reason"),
				description: __("Source doc will stay in 'Pending Budget Override' until re-submitted. Re-submit will create a fresh override request."),
			},
		],
		primary_action_label: __("Cancel Request"),
		primary_action(values) {
			if (!((values.reason || "").trim())) {
				frappe.msgprint({ message: __("Reason is required."), indicator: "red" });
				return;
			}
			d.disable_primary_action();
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.ts_budget_override.cancel_budget_override",
				args: { name: frm.doc.name, reason: values.reason.trim() },
				callback(r) {
					d.hide();
					if (r && r.message && r.message.ok) {
						frappe.show_alert({ message: __("Override cancelled."), indicator: "gray" }, 5);
						frm.reload_doc();
					}
				},
				error() {
					d.enable_primary_action();
				},
			});
		},
	});
	d.show();
}
