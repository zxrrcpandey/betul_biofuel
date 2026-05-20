// Cascade Delete custom page (v2.11.0)
// 3-stage modal flow + initiator dashboard + CEO approval queue + recent log.
// Lesson 174 — version pill rendered in HTML; bump on every page edit.

frappe.pages["cascade-delete"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Cascade Delete (BBPL)"),
		single_column: true,
	});

	_init();
};

const CD_PAGE_VERSION = "v2.11.0-2026-05-20";

function _init() {
	// 0. Render version pill (Lesson 174 — cache-flush verification)
	const pill = document.getElementById("cd-version-pill");
	if (pill) pill.textContent = CD_PAGE_VERSION;
	// 1. Kill-switch banner
	_render_kill_banner();
	// 2. Pending + recent lists
	_render_pending_list();
	_render_recent_list();
	// 3. Wire search box (only for initiate-capable users)
	const user_roles = new Set(frappe.user_roles || []);
	const can_initiate = user_roles.has("Super Admin");
	if (can_initiate) {
		const search_row = document.getElementById("cd-search-row");
		if (search_row) search_row.style.display = "flex";
		const preview_btn = document.getElementById("cd-preview-btn");
		if (preview_btn) {
			preview_btn.addEventListener("click", _on_preview_click);
		}
	}
}

function _render_kill_banner() {
	frappe.call({
		method: "frappe.client.get_value",
		args: {
			doctype: "TS Settings",
			fieldname: "ts_cascade_delete_enabled",
			filters: {},
		},
		callback(r) {
			const enabled = r && r.message && r.message.ts_cascade_delete_enabled;
			const banner = document.getElementById("cd-kill-banner");
			if (!banner) return;
			if (enabled) {
				banner.innerHTML = `<div class="cd-kill-banner cd-kill-on">⚠ Cascade Delete tool is <strong>ENABLED</strong>. Every action is logged + signed + chained.</div>`;
			} else {
				banner.innerHTML = `<div class="cd-kill-banner cd-kill-off">🔒 Cascade Delete tool is <strong>DISABLED</strong>. System Manager must flip the kill switch in TS Settings before any operation can proceed.</div>`;
			}
		},
	});
}

function _on_preview_click() {
	const input = document.getElementById("cd-token-input");
	const token_name = (input.value || "").trim();
	if (!/^BBPL-TKN-[0-9]{4}-[0-9]{5}$/.test(token_name)) {
		frappe.msgprint({ title: __("Invalid"), message: __("Token name must match BBPL-TKN-YYYY-NNNNN."), indicator: "red" });
		return;
	}
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.cascade_delete_api.preview_cascade_chain",
		args: { token_name },
		callback(r) {
			if (!r.message) return;
			_render_preview(r.message, token_name);
		},
	});
}

function _render_preview(chain, token_name) {
	const area = document.getElementById("cd-preview-area");
	if (!chain.token_record) {
		area.innerHTML = `<div class="cd-section"><h3>Preview</h3><div class="cd-empty">Token <code>${frappe.utils.escape_html(token_name)}</code> not found.</div></div>`;
		return;
	}
	const t = chain.token_record;
	const has_pr = chain.purchase_receipts.length > 0;
	const has_pi = chain.purchase_invoices.length > 0;
	const has_qi = chain.quality_inspections.length > 0;
	const submitted_pr = chain.purchase_receipts.some(r => r.docstatus === 1);
	const submitted_pi = chain.purchase_invoices.some(r => r.docstatus === 1);
	const submitted_qi = chain.quality_inspections.some(r => r.docstatus === 1);
	const needs_force_pr = submitted_pr || submitted_pi;
	const needs_force_mi = submitted_qi;

	let html = `<div class="cd-section"><h3>Chain Preview for <code>${frappe.utils.escape_html(token_name)}</code></h3>`;
	html += `<table class="cd-chain-table">`;
	html += `<tr><th>DocType</th><th>Count</th><th>Submitted</th><th>Notes</th></tr>`;
	html += `<tr><td>TS Token</td><td>1</td><td>${t.docstatus === 1 ? "yes" : "no"}</td><td>Status: ${frappe.utils.escape_html(t.status || "-")} | Vehicle: ${frappe.utils.escape_html(t.vehicle_number || "-")}</td></tr>`;
	html += `<tr><td>TS Gate Entry</td><td>${chain.gate_entries.length}</td><td>${chain.gate_entries.filter(r => r.docstatus === 1).length}</td><td></td></tr>`;
	html += `<tr><td>TS Weighbridge Log</td><td>${chain.weighbridge_logs.length}</td><td>${chain.weighbridge_logs.filter(r => r.docstatus === 1).length}</td><td></td></tr>`;
	html += `<tr><td>TS Quality Inspection</td><td>${chain.quality_inspections.length}</td><td>${chain.quality_inspections.filter(r => r.docstatus === 1).length}</td><td>${needs_force_mi ? "<strong style='color:#dc2626'>Requires FORCE-DELETE-MI</strong>" : ""}</td></tr>`;
	html += `<tr><td>TS Deduction Sheet</td><td>${chain.deduction_sheets.length}</td><td>${chain.deduction_sheets.filter(r => r.docstatus === 1).length}</td><td></td></tr>`;
	html += `<tr><td>Purchase Receipt</td><td>${chain.purchase_receipts.length}</td><td>${chain.purchase_receipts.filter(r => r.docstatus === 1).length}</td><td>${needs_force_pr ? "<strong style='color:#dc2626'>Requires FORCE-DELETE-PR</strong>" : ""}</td></tr>`;
	html += `<tr><td>Purchase Invoice</td><td>${chain.purchase_invoices.length}</td><td>${chain.purchase_invoices.filter(r => r.docstatus === 1).length}</td><td></td></tr>`;
	html += `</table>`;
	html += `<p style="margin-top:10px;font-size:12px;color:#64748b;">Stock Ledger Entries: ${chain.stock_ledger_entries_count} | GL Entries: ${chain.gl_entries_count}</p>`;
	html += `<div style="margin-top:14px;"><button class="cd-btn cd-btn-danger" id="cd-initiate-btn">Initiate Cascade Deletion →</button></div>`;
	html += `</div>`;
	area.innerHTML = html;

	document.getElementById("cd-initiate-btn").addEventListener("click", () => {
		_show_stage1_confirm(token_name, needs_force_pr, needs_force_mi);
	});
}

function _show_stage1_confirm(token_name, needs_force_pr, needs_force_mi) {
	const modal_html = `
		<div class="cd-modal-bg">
		<div class="cd-modal">
		<h2>Stage 1 of 3 — Confirm Token Name</h2>
		<p style="font-size:12px;color:#475569;">Type the token name <strong>EXACTLY</strong> to continue.</p>
		<label for="cd-st1-token">Token name (case-sensitive):</label>
		<input type="text" id="cd-st1-token" maxlength="20" autofocus />
		<div class="cd-modal-actions">
			<button class="cd-btn cd-btn-secondary" id="cd-st1-cancel">Cancel</button>
			<button class="cd-btn cd-btn-danger" id="cd-st1-continue" disabled>Continue →</button>
		</div>
		</div></div>`;
	const div = document.createElement("div");
	div.innerHTML = modal_html;
	document.body.appendChild(div);
	const input = div.querySelector("#cd-st1-token");
	const continue_btn = div.querySelector("#cd-st1-continue");
	input.addEventListener("input", () => {
		continue_btn.disabled = input.value !== token_name;
	});
	div.querySelector("#cd-st1-cancel").addEventListener("click", () => div.remove());
	continue_btn.addEventListener("click", () => {
		div.remove();
		if (needs_force_pr) {
			_show_stage2_force_pr(token_name, needs_force_mi);
		} else if (needs_force_mi) {
			_show_stage2_force_mi(token_name, false);
		} else {
			_show_stage3_final(token_name, false, false);
		}
	});
}

function _show_stage2_force_pr(token_name, needs_force_mi) {
	const modal_html = `
		<div class="cd-modal-bg">
		<div class="cd-modal">
		<h2>Stage 2 — Force Delete PR</h2>
		<p style="font-size:12px;color:#475569;">This chain has a <strong>submitted Purchase Receipt or Invoice</strong>. Type <code>FORCE-DELETE-PR</code> to continue.</p>
		<label for="cd-st2-pr">Type FORCE-DELETE-PR:</label>
		<input type="text" id="cd-st2-pr" autofocus />
		<div class="cd-modal-actions">
			<button class="cd-btn cd-btn-secondary" id="cd-st2-cancel">Cancel</button>
			<button class="cd-btn cd-btn-danger" id="cd-st2-continue" disabled>Continue →</button>
		</div>
		</div></div>`;
	const div = document.createElement("div");
	div.innerHTML = modal_html;
	document.body.appendChild(div);
	const input = div.querySelector("#cd-st2-pr");
	const btn = div.querySelector("#cd-st2-continue");
	input.addEventListener("input", () => { btn.disabled = input.value !== "FORCE-DELETE-PR"; });
	div.querySelector("#cd-st2-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => {
		div.remove();
		if (needs_force_mi) {
			_show_stage2_force_mi(token_name, true);
		} else {
			_show_stage3_final(token_name, true, false);
		}
	});
}

function _show_stage2_force_mi(token_name, force_pr) {
	const modal_html = `
		<div class="cd-modal-bg">
		<div class="cd-modal">
		<h2>Stage 2 — Force Delete Material Inspection</h2>
		<p style="font-size:12px;color:#475569;">This chain has a <strong>submitted Quality Inspection</strong>. Type <code>FORCE-DELETE-MI</code> to continue.</p>
		<label for="cd-st2-mi">Type FORCE-DELETE-MI:</label>
		<input type="text" id="cd-st2-mi" autofocus />
		<div class="cd-modal-actions">
			<button class="cd-btn cd-btn-secondary" id="cd-st2-cancel">Cancel</button>
			<button class="cd-btn cd-btn-danger" id="cd-st2-continue" disabled>Continue →</button>
		</div>
		</div></div>`;
	const div = document.createElement("div");
	div.innerHTML = modal_html;
	document.body.appendChild(div);
	const input = div.querySelector("#cd-st2-mi");
	const btn = div.querySelector("#cd-st2-continue");
	input.addEventListener("input", () => { btn.disabled = input.value !== "FORCE-DELETE-MI"; });
	div.querySelector("#cd-st2-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => {
		div.remove();
		_show_stage3_final(token_name, force_pr, true);
	});
}

function _show_stage3_final(token_name, force_pr, force_mi) {
	const modal_html = `
		<div class="cd-modal-bg">
		<div class="cd-modal">
		<h2>Stage 3 of 3 — FINAL CONFIRMATION</h2>
		<p style="font-size:12px;color:#475569;">You are about to <strong>queue a cascade delete</strong> for <code>${frappe.utils.escape_html(token_name)}</code>. A CEO must approve before execution. Type the token name ONE MORE TIME to submit.</p>
		<label for="cd-st3-final">Type token name to submit:</label>
		<input type="text" id="cd-st3-final" maxlength="20" autofocus />
		<div class="cd-modal-actions">
			<button class="cd-btn cd-btn-secondary" id="cd-st3-cancel">Cancel</button>
			<button class="cd-btn cd-btn-danger" id="cd-st3-submit" disabled>Submit for CEO Approval →</button>
		</div>
		</div></div>`;
	const div = document.createElement("div");
	div.innerHTML = modal_html;
	document.body.appendChild(div);
	const input = div.querySelector("#cd-st3-final");
	const btn = div.querySelector("#cd-st3-submit");
	input.addEventListener("input", () => { btn.disabled = input.value !== token_name; });
	div.querySelector("#cd-st3-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => {
		btn.disabled = true;
		btn.textContent = "Submitting…";
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.cascade_delete_api.initiate_cascade",
			args: {
				token_name: token_name,
				force_pr: force_pr ? 1 : 0,
				force_mi: force_mi ? 1 : 0,
				confirm_token_name_typed: token_name,
				force_pr_confirmation_typed: force_pr ? "FORCE-DELETE-PR" : "",
				force_mi_confirmation_typed: force_mi ? "FORCE-DELETE-MI" : "",
				client_screen: `${screen.width}x${screen.height}`,
				client_language: navigator.language,
			},
			callback(r) {
				div.remove();
				if (r.message && r.message.success) {
					frappe.show_alert({ message: __("Cascade queued. Log: ") + r.message.log_name, indicator: "green" });
					_render_pending_list();
					_render_recent_list();
				}
			},
			error(err) {
				div.remove();
				frappe.msgprint({ title: __("Submission Failed"), message: (err && err.message) || __("Unknown error."), indicator: "red" });
			},
		});
	});
}

function _render_pending_list() {
	frappe.db.get_list("TS Cascade Delete Log", {
		fields: ["name", "target_token", "initiated_by", "initiated_at", "approval_status"],
		filters: { approval_status: "Pending CEO Approval", docstatus: 1 },
		order_by: "initiated_at desc",
		limit: 25,
	}).then(rows => {
		const el = document.getElementById("cd-pending-list");
		if (!rows || !rows.length) {
			el.innerHTML = `<div class="cd-empty">No pending requests.</div>`;
			return;
		}
		const user_roles = new Set(frappe.user_roles || []);
		const can_approve = user_roles.has("CEO");
		let html = `<table class="cd-list"><tr><th>Log</th><th>Token</th><th>Initiated By</th><th>At</th><th>Actions</th></tr>`;
		for (const r of rows) {
			html += `<tr>`;
			html += `<td><a href="/app/ts-cascade-delete-log/${frappe.utils.escape_html(r.name)}">${frappe.utils.escape_html(r.name)}</a></td>`;
			html += `<td><code>${frappe.utils.escape_html(r.target_token)}</code></td>`;
			html += `<td>${frappe.utils.escape_html(r.initiated_by)}</td>`;
			html += `<td>${frappe.utils.escape_html(r.initiated_at || "")}</td>`;
			html += `<td>`;
			if (can_approve && r.initiated_by !== frappe.session.user) {
				html += `<button class="cd-btn cd-btn-primary" data-action="approve" data-log="${frappe.utils.escape_html(r.name)}">Approve</button>`;
				html += ` <button class="cd-btn cd-btn-secondary" data-action="reject" data-log="${frappe.utils.escape_html(r.name)}">Reject</button>`;
			}
			html += `</td></tr>`;
		}
		html += `</table>`;
		el.innerHTML = html;
		el.querySelectorAll("button[data-action]").forEach(btn => {
			btn.addEventListener("click", () => _ceo_decision(btn.dataset.log, btn.dataset.action));
		});
	});
}

function _ceo_decision(log_name, action) {
	if (action === "reject") {
		frappe.prompt(
			[{ fieldname: "reason", label: __("Rejection Reason (min 10 chars)"), fieldtype: "Small Text", reqd: 1 }],
			(values) => {
				if (values.reason.length < 10) {
					frappe.msgprint({ message: __("Reason must be at least 10 characters."), indicator: "red" });
					return;
				}
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.cascade_delete_api.ceo_approve_cascade",
					args: { log_name, decision: "reject", reason: values.reason },
					callback() { _render_pending_list(); _render_recent_list(); },
				});
			},
			__("Reject Cascade"),
			__("Reject"),
		);
	} else {
		frappe.confirm(
			__("Approve cascade delete? The engine will execute IMMEDIATELY. A 5-minute revert window will follow."),
			() => {
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.cascade_delete_api.ceo_approve_cascade",
					args: { log_name, decision: "approve" },
					freeze: true,
					freeze_message: __("Executing cascade — this may take a few seconds…"),
					callback(r) {
						_render_pending_list();
						_render_recent_list();
						if (r.message && !r.message.success) {
							frappe.msgprint({ title: __("Engine Failed"), message: JSON.stringify(r.message.steps, null, 2), indicator: "red" });
						}
					},
				});
			},
		);
	}
}

function _render_recent_list() {
	frappe.db.get_list("TS Cascade Delete Log", {
		fields: ["name", "target_token", "initiated_by", "approval_status", "executed_at", "revert_window_expires_at"],
		filters: { docstatus: 1 },
		order_by: "creation desc",
		limit: 25,
	}).then(rows => {
		const el = document.getElementById("cd-recent-list");
		if (!rows || !rows.length) {
			el.innerHTML = `<div class="cd-empty">No history yet.</div>`;
			return;
		}
		let html = `<table class="cd-list"><tr><th>Log</th><th>Token</th><th>Status</th><th>Executed</th><th>Revert Until</th></tr>`;
		for (const r of rows) {
			const status_class = "cd-status-" + (r.approval_status || "").split(" ")[0];
			html += `<tr>`;
			html += `<td><a href="/app/ts-cascade-delete-log/${frappe.utils.escape_html(r.name)}">${frappe.utils.escape_html(r.name)}</a></td>`;
			html += `<td><code>${frappe.utils.escape_html(r.target_token)}</code></td>`;
			html += `<td><span class="cd-status-pill ${status_class}">${frappe.utils.escape_html(r.approval_status || "")}</span></td>`;
			html += `<td>${frappe.utils.escape_html(r.executed_at || "-")}</td>`;
			html += `<td>${frappe.utils.escape_html(r.revert_window_expires_at || "-")}</td>`;
			html += `</tr>`;
		}
		html += `</table>`;
		el.innerHTML = html;
	});
}
