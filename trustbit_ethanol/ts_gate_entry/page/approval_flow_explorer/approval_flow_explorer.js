/* ═══════════════════════════════════════════════════════════════════
   TS Approval Flow Explorer — live-data version
   Debug tool for understanding who can do what in MR/PO/CC approvals.
   All data fetched from approval_flow_explorer_api.py (read-only).
   ═══════════════════════════════════════════════════════════════════ */

frappe.pages["approval-flow-explorer"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Approval Flow Explorer",
		single_column: true,
	});

	page.main.html(`<div id="afe-container" style="padding: 0;">
		<div id="afe-loading" style="text-align:center;padding:60px;color:#9ca3af;font-size:14px;">Loading Approval Flow Explorer…</div>
	</div>`);

	// Inject CSS
	if (!document.getElementById("afe-styles")) {
		const style = document.createElement("style");
		style.id = "afe-styles";
		style.textContent = AFE_CSS;
		document.head.appendChild(style);
	}

	page.add_button(__("Refresh"), () => _afe_reload(), { icon: "refresh" });

	setTimeout(() => _afe_init(), 300);
};

frappe.pages["approval-flow-explorer"].refresh = function () {
	_afe_reload();
};

let _afe_state = {
	users: [],
	currentEmail: null,
	poRules: null,
	mrRoutes: null,
	traceableDocs: null,
};

async function _afe_init() {
	try {
		const r = await frappe.call({ method: "trustbit_ethanol.ts_gate_entry.approval_flow_explorer_api.get_users_list" });
		_afe_state.users = r.message || [];
		if (!_afe_state.users.length) {
			$("#afe-container").html('<div style="padding:60px;text-align:center;color:#ef4444;">No users found.</div>');
			return;
		}
		_afe_render_shell();
		_afe_state.currentEmail = _afe_state.users[0].email;
		await _afe_load_user(_afe_state.currentEmail);
		await _afe_load_flows();
	} catch (e) {
		$("#afe-container").html(`<div style="padding:60px;text-align:center;color:#ef4444;">Error: ${frappe.utils.escape_html(e.message || String(e))}</div>`);
		console.error(e);
	}
}

function _afe_reload() {
	_afe_state.poRules = null;
	_afe_state.mrRoutes = null;
	_afe_init();
}

function _afe_render_shell() {
	const userOpts = _afe_state.users.map(u => `<option value="${frappe.utils.escape_html(u.email)}">${frappe.utils.escape_html(u.display)}</option>`).join("");
	$("#afe-container").html(`
		<div class="afe-selector">
			<label>Select User:</label>
			<select id="afe-user-select">${userOpts}</select>
			<button class="btn btn-primary btn-sm" id="afe-load-btn">Load Flow</button>
		</div>

		<div class="afe-user-card" id="afe-user-card">
			<div style="padding:30px;text-align:center;color:var(--text-muted);">Loading user flow…</div>
		</div>

		<div class="afe-grid-2">
			<div class="afe-card">
				<div class="afe-card-header">
					<div class="afe-card-title">MR Creation Authority</div>
					<div class="afe-card-badge" id="afe-create-count">—</div>
				</div>
				<div id="afe-create-table"></div>
			</div>
			<div class="afe-card">
				<div class="afe-card-header">
					<div class="afe-card-title">MR Review / Approval Authority</div>
					<div class="afe-card-badge" id="afe-approve-count">—</div>
				</div>
				<div id="afe-approve-table"></div>
			</div>
		</div>

		<div class="afe-card">
			<div class="afe-card-header">
				<div class="afe-card-title">PO Approval Authority (via System Roles)</div>
				<div class="afe-card-badge" id="afe-po-count">—</div>
			</div>
			<div id="afe-po-table"></div>
		</div>

		<div class="afe-grid-2">
			<div class="afe-card">
				<div class="afe-card-header">
					<div class="afe-card-title">🔴 Live Pending With You</div>
					<div class="afe-card-badge" id="afe-pending-count">—</div>
				</div>
				<div id="afe-pending-list"></div>
			</div>
			<div class="afe-card">
				<div class="afe-card-header">
					<div class="afe-card-title">Why You Didn't Get Notified</div>
					<div class="afe-card-badge">Debug helper</div>
				</div>
				<div class="afe-explain-grid">
					<div class="afe-explain-card"><strong>CC Config Gap</strong><p>You have the role but aren't in the CC Approval Config user list for that cost center — system skips you.</p></div>
					<div class="afe-explain-card"><strong>Self-Submit Skip</strong><p>You submitted the doc yourself, so your own step is auto-skipped (prevents self-approval).</p></div>
					<div class="afe-explain-card"><strong>Higher-Level Override</strong><p>You CAN act because you hold a higher step's role, but you aren't auto-notified unless it's your current step.</p></div>
				</div>
			</div>
		</div>

		<div class="afe-grid-2">
			<div class="afe-card">
				<div class="afe-card-header">
					<div class="afe-card-title">Recent Activity (Last 14 days)</div>
					<div class="afe-card-badge">Audit log</div>
				</div>
				<div id="afe-timeline" class="afe-timeline"></div>
			</div>
			<div class="afe-card">
				<div class="afe-card-header">
					<div class="afe-card-title">Notification History</div>
					<div class="afe-card-badge">Bell feed</div>
				</div>
				<div id="afe-notifications"></div>
			</div>
		</div>

		<div class="afe-card">
			<div class="afe-card-header">
				<div class="afe-card-title">Path Tracer — Interactive Flow Viewer</div>
				<div class="afe-card-badge">Pick a doc to trace</div>
			</div>
			<div class="afe-tracer-input">
				<select id="afe-tracer-doc"><option value="">-- Select a doc --</option></select>
				<button class="btn btn-primary btn-sm" id="afe-trace-btn">Trace</button>
			</div>
			<div id="afe-flow-diagram"></div>
			<div id="afe-tracer-summary" class="afe-tracer-summary"></div>
		</div>

		<div class="afe-card">
			<div class="afe-card-header">
				<div class="afe-card-title">📐 Configured Flows — How the System Is Set Up</div>
				<div class="afe-card-badge" id="afe-flow-count">— rules / routes</div>
			</div>
			<div class="afe-tabs-bar">
				<button class="afe-tab active" id="afe-tab-po" data-tab="po">PO Approval Rules</button>
				<button class="afe-tab" id="afe-tab-mr" data-tab="mr">MR Approval Routes</button>
			</div>
			<div id="afe-flow-gallery"></div>
		</div>

		<div class="afe-card">
			<div class="afe-card-header">
				<div class="afe-card-title">🧠 How It Works — Visual Explainers</div>
				<div class="afe-card-badge">Tricky logic made simple</div>
			</div>
			<div class="afe-tabs-bar" id="afe-explainer-tabs"></div>
			<div id="afe-explainer-content"></div>
		</div>

		<div class="afe-card">
			<div class="afe-card-header">
				<div class="afe-card-title">🎬 Action Playback — What Each Button Does</div>
				<div class="afe-card-badge">9 approval actions</div>
			</div>
			<div class="afe-action-chip-bar" id="afe-action-tabs"></div>
			<div id="afe-action-content"></div>
		</div>
	`);

	$("#afe-user-select").on("change", () => {
		_afe_state.currentEmail = $("#afe-user-select").val();
	});
	$("#afe-load-btn").on("click", () => _afe_load_user(_afe_state.currentEmail));
	$("#afe-trace-btn").on("click", () => {
		const v = $("#afe-tracer-doc").val();
		if (v) _afe_trace(v);
	});
	$("#afe-container").on("click", ".afe-tab", function () {
		$("#afe-container .afe-tab").removeClass("active");
		$(this).addClass("active");
		_afe_show_flow_tab($(this).data("tab"));
	});

	// Explainer tabs
	const ex_keys = Object.keys(AFE_EXPLAINERS);
	$("#afe-explainer-tabs").html(ex_keys.map((k, i) =>
		`<button class="afe-tab ${i === 0 ? 'active' : ''}" data-ex="${k}">${AFE_EXPLAINERS[k].tabLabel}</button>`
	).join(""));
	$("#afe-explainer-tabs").on("click", ".afe-tab", function () {
		$("#afe-explainer-tabs .afe-tab").removeClass("active");
		$(this).addClass("active");
		_afe_show_explainer($(this).data("ex"));
	});
	_afe_show_explainer(ex_keys[0]);

	// Action tabs
	const act_keys = Object.keys(AFE_ACTIONS);
	$("#afe-action-tabs").html(act_keys.map((k, i) =>
		`<button class="afe-action-chip ${i === 0 ? 'active' : ''}" data-act="${k}">${AFE_ACTIONS[k].title}</button>`
	).join(""));
	$("#afe-action-tabs").on("click", ".afe-action-chip", function () {
		$("#afe-action-tabs .afe-action-chip").removeClass("active");
		$(this).addClass("active");
		_afe_play_action($(this).data("act"));
	});
	_afe_play_action(act_keys[0]);
}

async function _afe_load_user(email) {
	if (!email) return;
	$("#afe-user-card").html('<div style="padding:30px;text-align:center;color:var(--text-muted);">Loading…</div>');
	try {
		const [flowR, pendingR, activityR, notifR, docsR] = await Promise.all([
			frappe.call({ method: "trustbit_ethanol.ts_gate_entry.approval_flow_explorer_api.get_user_flow", args: { email } }),
			frappe.call({ method: "trustbit_ethanol.ts_gate_entry.approval_flow_explorer_api.get_user_pending", args: { email } }),
			frappe.call({ method: "trustbit_ethanol.ts_gate_entry.approval_flow_explorer_api.get_user_activity", args: { email, days: 14 } }),
			frappe.call({ method: "trustbit_ethanol.ts_gate_entry.approval_flow_explorer_api.get_user_notifications", args: { email, days: 14 } }),
			frappe.call({ method: "trustbit_ethanol.ts_gate_entry.approval_flow_explorer_api.get_traceable_docs" }),
		]);
		_afe_render_user_card(flowR.message);
		_afe_render_create_table(flowR.message.create);
		_afe_render_approve_table(flowR.message.approve);
		_afe_render_po_table(flowR.message.po);
		_afe_render_pending(pendingR.message);
		_afe_render_timeline(activityR.message);
		_afe_render_notifications(notifR.message);
		_afe_populate_tracer(docsR.message, flowR.message.stats);
	} catch (e) {
		$("#afe-user-card").html(`<div style="padding:20px;color:#ef4444;">Error loading user: ${frappe.utils.escape_html(e.message || String(e))}</div>`);
		console.error(e);
	}
}

async function _afe_load_flows() {
	try {
		const [poR, mrR] = await Promise.all([
			frappe.call({ method: "trustbit_ethanol.ts_gate_entry.approval_flow_explorer_api.get_po_rules" }),
			frappe.call({ method: "trustbit_ethanol.ts_gate_entry.approval_flow_explorer_api.get_mr_routes" }),
		]);
		_afe_state.poRules = poR.message || [];
		_afe_state.mrRoutes = mrR.message || [];
		$("#afe-flow-count").text(`${_afe_state.poRules.length} PO rules + ${_afe_state.mrRoutes.length} MR routes`);
		_afe_show_flow_tab("po");
	} catch (e) {
		$("#afe-flow-gallery").html(`<div style="color:#ef4444;padding:20px;">${frappe.utils.escape_html(e.message || String(e))}</div>`);
	}
}

function _afe_render_user_card(u) {
	const roleChips = u.roles.map(r => {
		const cls = ["CEO", "MD", "AVP"].includes(r) ? "executive" :
		            ["Purchase Manager", "Department Head", "IT Head", "Accounts Manager", "General Manager", "Grain Purchase Manager"].includes(r) ? "manager" : "operational";
		return `<span class="afe-role-chip ${cls}">${frappe.utils.escape_html(r)}</span>`;
	}).join("");
	$("#afe-user-card").html(`
		<div class="afe-avatar">${frappe.utils.escape_html(u.initials)}</div>
		<div class="afe-user-info">
			<div class="afe-user-name">${frappe.utils.escape_html(u.full_name || u.email)}</div>
			<div class="afe-user-email">${frappe.utils.escape_html(u.email)}</div>
			<div class="afe-user-roles">${roleChips || '<span class="afe-role-chip operational">(no roles)</span>'}</div>
		</div>
		<div class="afe-user-stats">
			<div class="afe-stat"><div class="afe-stat-value">${u.stats.creator}</div><div class="afe-stat-label">Creator in</div></div>
			<div class="afe-stat"><div class="afe-stat-value">${u.stats.reviewer}</div><div class="afe-stat-label">Reviewer in</div></div>
			<div class="afe-stat"><div class="afe-stat-value">${u.stats.final_approver}</div><div class="afe-stat-label">Final Approver</div></div>
			<div class="afe-stat"><div class="afe-stat-value">${u.stats.notify_only}</div><div class="afe-stat-label">Notify Only</div></div>
		</div>
	`);
}

function _afe_render_create_table(rows) {
	$("#afe-create-count").text(`${rows.length} CCs`);
	if (!rows.length) {
		$("#afe-create-table").html(`<div class="afe-empty">This user cannot create Material Requests.</div>`);
		return;
	}
	const html = rows.map(r => `
		<tr>
			<td><div class="afe-cc-name">${frappe.utils.escape_html(r.cost_center)}</div><div class="afe-cc-route">Route: ${frappe.utils.escape_html(r.mr_approval_route || "-")}</div></td>
			<td><span class="afe-flow-badge ${r.flow_type === 'Direct PO' ? 'direct' : 'standard'}">${frappe.utils.escape_html(r.flow_type)}</span></td>
		</tr>
	`).join("");
	$("#afe-create-table").html(`<table class="afe-table"><thead><tr><th>Cost Center</th><th>Flow</th></tr></thead><tbody>${html}</tbody></table>`);
}

function _afe_render_approve_table(rows) {
	$("#afe-approve-count").text(`${rows.length} CCs`);
	if (!rows.length) {
		$("#afe-approve-table").html(`<div class="afe-empty">No MR approval authority for any cost center.</div>`);
		return;
	}
	const html = rows.slice(0, 12).map(r => {
		const actionCls = r.action_type === "Final Approver" ? "final" : "review";
		return `
			<tr>
				<td><div class="afe-cc-name">${frappe.utils.escape_html(r.cost_center)}</div><div class="afe-cc-route">${frappe.utils.escape_html(r.mr_approval_route || "-")}</div></td>
				<td>Step ${r.step_order || "-"}</td>
				<td><span class="afe-action-badge ${actionCls}">${frappe.utils.escape_html(r.action_type)}</span></td>
				<td>${frappe.utils.escape_html(r.role || "-")}</td>
			</tr>
		`;
	}).join("");
	const more = rows.length > 12 ? `<tr><td colspan="4" style="text-align:center;color:var(--text-muted);font-style:italic;">+ ${rows.length - 12} more CCs</td></tr>` : "";
	$("#afe-approve-table").html(`<table class="afe-table"><thead><tr><th>Cost Center</th><th>Step</th><th>Action</th><th>Role</th></tr></thead><tbody>${html}${more}</tbody></table>`);
}

function _afe_render_po_table(rows) {
	if (!rows.length) {
		$("#afe-po-count").text("Not applicable");
		$("#afe-po-table").html(`<div class="afe-note afe-note-warn">None of this user's roles are mapped to any active PO Approval Rule step.</div>`);
		return;
	}
	$("#afe-po-count").text(`${rows.length} rule-step matches`);
	const fmt = n => n === null || n === undefined ? "∞" : n >= 100000 ? `₹${(n/100000).toFixed(0)}L` : `₹${Number(n).toLocaleString("en-IN")}`;
	const html = rows.map(r => {
		const actionCls = r.action_type === "Final Approve" ? "final" : r.action_type === "Review" ? "review" : "approve";
		return `
			<tr>
				<td><strong>${frappe.utils.escape_html(r.purchase_category || "-")}</strong></td>
				<td>${frappe.utils.escape_html(r.rule_name || "-")}</td>
				<td>${fmt(r.min_amount)} – ${fmt(r.max_amount)}</td>
				<td>Step ${r.step_order}</td>
				<td><span class="afe-action-badge ${actionCls}">${frappe.utils.escape_html(r.action_type)}</span></td>
				<td style="color:var(--text-muted);font-size:11px;">${frappe.utils.escape_html(r.role)}${r.is_manual_trigger ? " • manual trigger" : ""}</td>
			</tr>
		`;
	}).join("");
	$("#afe-po-table").html(`<table class="afe-table"><thead><tr><th>Category</th><th>Rule</th><th>Amount Band</th><th>Step</th><th>Action</th><th>Role</th></tr></thead><tbody>${html}</tbody></table>`);
}

function _afe_render_pending(data) {
	const all = [...(data.mr || []).map(m => ({...m, dt: "Material Request"})), ...(data.po || []).map(p => ({...p, dt: "Purchase Order"}))];
	$("#afe-pending-count").text(`${all.length} items`);
	if (!all.length) {
		$("#afe-pending-list").html(`<div class="afe-empty">✨ Nothing pending with you right now.</div>`);
		return;
	}
	const html = all.map(d => {
		const days = d.days_pending || 0;
		const cls = days <= 1 ? "fresh" : days <= 5 ? "aging" : "stuck";
		const status = d.ts_approval_status || d.ts_mr_status || "";
		const amt = frappe.format(d.grand_total || 0, { fieldtype: "Currency" });
		const meta = d.dt === "Purchase Order"
			? `${frappe.utils.escape_html(d.supplier_name || d.supplier || "")} • ${frappe.utils.escape_html(status)}`
			: `${frappe.utils.escape_html(d.cost_center || "")} • ${frappe.utils.escape_html(status)}`;
		const url = `/app/${d.dt.toLowerCase().replace(" ", "-")}/${encodeURIComponent(d.name)}`;
		return `
			<div class="afe-pending-row" onclick="window.open('${url}', '_blank')">
				<div class="afe-pending-doc">${frappe.utils.escape_html(d.name)}</div>
				<div class="afe-pending-meta">${meta}</div>
				<div class="afe-pending-amount">${amt}</div>
				<div class="afe-pending-days ${cls}">${days}d</div>
			</div>
		`;
	}).join("");
	$("#afe-pending-list").html(html);
}

function _afe_render_timeline(rows) {
	if (!rows.length) {
		$("#afe-timeline").html(`<div class="afe-empty">No activity in the last 14 days.</div>`);
		return;
	}
	const cls_for = action => {
		const a = (action || "").toLowerCase();
		if (a.includes("approv")) return "approved";
		if (a.includes("reject")) return "rejected";
		if (a.includes("revis")) return "revised";
		if (a.includes("hold")) return "held";
		return "";
	};
	$("#afe-timeline").html(rows.map(r => {
		const when = frappe.datetime.prettyDate(r.action_date);
		return `
			<div class="afe-timeline-item ${cls_for(r.action)}">
				<div class="afe-timeline-time">${when}</div>
				<div class="afe-timeline-text"><strong>${frappe.utils.escape_html(r.action)}</strong> ${frappe.utils.escape_html(r.parent)} (${frappe.utils.escape_html(r.parenttype)})${r.comment ? ` — ${frappe.utils.escape_html(r.comment.slice(0, 80))}` : ""}</div>
			</div>
		`;
	}).join(""));
}

function _afe_render_notifications(rows) {
	if (!rows.length) {
		$("#afe-notifications").html(`<div class="afe-empty">No notifications in the last 14 days.</div>`);
		return;
	}
	$("#afe-notifications").html(rows.map(r => {
		const when = frappe.datetime.prettyDate(r.creation);
		return `<div style="padding:8px 4px;border-bottom:1px solid #f1f5f9;font-size:12px;">🔔 ${frappe.utils.escape_html(r.subject || "")} <span style="color:#9ca3af;">· ${when}</span></div>`;
	}).join(""));
}

function _afe_populate_tracer(docs, stats) {
	const po = (docs.po || []).map(d =>
		`<option value="po:${frappe.utils.escape_html(d.name)}">[PO] ${frappe.utils.escape_html(d.name)} — ${frappe.utils.escape_html(d.ts_purchase_category || "")} — ${frappe.format(d.grand_total, {fieldtype: "Currency"})} — ${frappe.utils.escape_html(d.ts_approval_status || "")}</option>`);
	const mr = (docs.mr || []).map(d =>
		`<option value="mr:${frappe.utils.escape_html(d.name)}">[MR] ${frappe.utils.escape_html(d.name)} — ${frappe.utils.escape_html(d.cost_center || "")} — ${frappe.format(d.grand_total, {fieldtype: "Currency"})} — ${frappe.utils.escape_html(d.ts_mr_status || "")}</option>`);
	$("#afe-tracer-doc").html(`<option value="">-- Select a doc to trace --</option>${po.join("")}${mr.join("")}`);
}

async function _afe_trace(val) {
	const [type, ...nameParts] = val.split(":");
	const name = nameParts.join(":");
	const doctype = type === "po" ? "Purchase Order" : "Material Request";
	try {
		const r = await frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.approval_flow_explorer_api.trace_doc",
			args: { doctype, name },
		});
		_afe_render_trace(r.message);
	} catch (e) {
		$("#afe-flow-diagram").html(`<div style="color:#ef4444;padding:20px;">${frappe.utils.escape_html(e.message || String(e))}</div>`);
	}
}

function _afe_render_trace(data) {
	const nodesHtml = (data.nodes || []).map((n, i, arr) => {
		const arrow = i < arr.length - 1 ? `<div class="afe-flow-arrow">→</div>` : "";
		return `
			<div class="afe-flow-node ${n.state || ''}">
				<div class="afe-flow-node-step">${frappe.utils.escape_html(n.step)}</div>
				<div class="afe-flow-node-role">${frappe.utils.escape_html(n.role)}</div>
				<div class="afe-flow-node-user">${frappe.utils.escape_html(n.user || "")}</div>
			</div>${arrow}
		`;
	}).join("");
	$("#afe-flow-diagram").html(`<div class="afe-flow-diagram">${nodesHtml}</div>`);
	const s = data.summary || {};
	$("#afe-tracer-summary").html(`
		<div><span class="afe-label">Current state</span> ${frappe.utils.escape_html(s.current || "-")}</div>
		<div><span class="afe-label">Route / Rule</span> ${frappe.utils.escape_html(s.route || "-")}</div>
		<div><span class="afe-label">Cost center</span> ${frappe.utils.escape_html(s.cost_center || "-")}</div>
		<div><span class="afe-label">Amount</span> ${frappe.utils.escape_html(s.amount || "-")}</div>
		<div><span class="afe-label">Submitted by</span> ${frappe.utils.escape_html(s.submitted_by || "-")}</div>
		<div><span class="afe-label">Pending for</span> ${s.days_pending || 0} days</div>
	`);
}

function _afe_show_flow_tab(tab) {
	const fmt = n => n === null || n === undefined ? "∞" : n >= 100000 ? `₹${(n/100000).toFixed(0)}L` : `₹${Number(n).toLocaleString("en-IN")}`;
	const nodeHtml = (s, isCreator, isSubmit) => {
		if (isCreator) return `<div class="afe-mini-node"><div class="afe-mini-node-label">Creator</div><div class="afe-mini-node-type">Purchase User</div></div>`;
		if (isSubmit) return `<div class="afe-mini-node" style="border-color:#86efac;background:#f0fdf4;"><div class="afe-mini-node-label">✓ Submitted</div><div class="afe-mini-node-type">docstatus = 1</div></div>`;
		const cls = (s.action_type || "").toLowerCase().includes("final") ? "final" : (s.action_type || "").toLowerCase().includes("review") ? "review" : "approve";
		const manual = s.is_manual_trigger ? " • manual trigger" : "";
		return `<div class="afe-mini-node ${cls}"><div class="afe-mini-node-label">${frappe.utils.escape_html(s.role_label || s.role)}</div><div class="afe-mini-node-type">${frappe.utils.escape_html(s.action_type || "")}${manual}</div></div>`;
	};
	const chain = steps => {
		if (!steps || !steps.length) return `<div style="padding:8px;color:var(--text-muted);font-style:italic;font-size:11px;">No steps configured</div>`;
		const all = [nodeHtml(null, true, false), ...steps.map(s => nodeHtml(s, false, false)), nodeHtml(null, false, true)];
		return `<div class="afe-flow-mini-diagram">${all.join('<div class="afe-mini-arrow">→</div>')}</div>`;
	};
	if (tab === "po") {
		$("#afe-flow-gallery").html((_afe_state.poRules || []).map(r => `
			<div class="afe-flow-card">
				<div class="afe-flow-card-header">
					<div class="afe-flow-card-title">${frappe.utils.escape_html(r.rule_name || r.name)}</div>
					<div class="afe-flow-card-tags">
						<span class="afe-flow-tag category">${frappe.utils.escape_html(r.purchase_category)}</span>
						<span class="afe-flow-tag amount">${fmt(r.min_amount)} – ${fmt(r.max_amount)}</span>
						<span class="afe-flow-tag steps">${r.steps.length} step${r.steps.length !== 1 ? 's' : ''}</span>
					</div>
				</div>
				${chain(r.steps)}
			</div>
		`).join(""));
	} else {
		$("#afe-flow-gallery").html((_afe_state.mrRoutes || []).map(r => `
			<div class="afe-flow-card" style="${!r.is_active ? 'opacity: 0.5;' : ''}">
				<div class="afe-flow-card-header">
					<div class="afe-flow-card-title">${frappe.utils.escape_html(r.route_name || r.name)} ${r.is_active ? "" : '<span style="color:#9ca3af;font-weight:400;">(inactive)</span>'}</div>
					<div class="afe-flow-card-tags">
						<span class="afe-flow-tag route">${r.ccs.length} CCs</span>
						<span class="afe-flow-tag steps">${r.steps.length} step${r.steps.length !== 1 ? 's' : ''}</span>
					</div>
				</div>
				${chain(r.steps)}
				${r.ccs.length ? `<div class="afe-cc-list"><strong>CCs mapped:</strong> ${r.ccs.map(c => frappe.utils.escape_html(c)).join(", ")}</div>` : ""}
			</div>
		`).join(""));
	}
}

function _afe_show_explainer(key) {
	const e = AFE_EXPLAINERS[key];
	if (!e) return;
	const renderCol = (col, which) => `
		<div class="afe-ba-col">
			<h4 class="${which}">${frappe.utils.escape_html(col.label)}</h4>
			${col.nodes.map(n => `
				<div class="afe-ba-node ${n.state || ''}">
					<div>${frappe.utils.escape_html(n.label)}</div>
					${n.note ? `<div class="afe-ba-note">${frappe.utils.escape_html(n.note)}</div>` : ""}
				</div>
			`).join("")}
		</div>
	`;
	$("#afe-explainer-content").html(`
		<div class="afe-explainer-body">
			<div class="afe-explainer-title">${frappe.utils.escape_html(e.title)}</div>
			<div class="afe-explainer-desc">${frappe.utils.escape_html(e.desc)}</div>
			<div class="afe-scenario-box">${e.scenario}</div>
			<div class="afe-before-after">${renderCol(e.before, "before")}${renderCol(e.after, "after")}</div>
			<div class="afe-code-ref">${frappe.utils.escape_html(e.code)}</div>
		</div>
	`);
}

function _afe_play_action(key) {
	const a = AFE_ACTIONS[key];
	if (!a) return;
	$("#afe-action-content").html(`
		<div class="afe-action-body">
			<div class="afe-action-title">${frappe.utils.escape_html(a.title)}</div>
			<div class="afe-action-meta">
				<div class="afe-action-meta-cell"><div class="afe-action-meta-label">Who can trigger</div><div class="afe-action-meta-value">${frappe.utils.escape_html(a.who)}</div></div>
				<div class="afe-action-meta-cell"><div class="afe-action-meta-label">From state</div><div class="afe-action-meta-value">${frappe.utils.escape_html(a.from)}</div></div>
				<div class="afe-action-meta-cell"><div class="afe-action-meta-label">To state</div><div class="afe-action-meta-value">${frappe.utils.escape_html(a.to)}</div></div>
			</div>
			<div class="afe-action-changes"><h5>Fields changed in DB</h5><ul>${a.changes.map(c => `<li>${frappe.utils.escape_html(c)}</li>`).join("")}</ul></div>
			<div class="afe-action-notifs"><h5>Who gets notified</h5><ul>${a.notifications.map(n => `<li>${frappe.utils.escape_html(n)}</li>`).join("")}</ul></div>
			<div class="afe-action-changes" style="background:#fef9e7;border-color:#fbbf24;"><h5 style="color:#78350f;">Validation guards</h5><ul>${a.guards.map(g => `<li>${frappe.utils.escape_html(g)}</li>`).join("")}</ul></div>
		</div>
	`);
}

/* ═══════════════════════════════════════════════════════════════════
   STATIC DATA — Explainers + Actions (concepts, not live data)
   ═══════════════════════════════════════════════════════════════════ */

const AFE_EXPLAINERS = {
	"self-skip": {
		tabLabel: "Self-Skip",
		title: "Self-Skip — prevents self-approval",
		desc: "When the submitter has the role of an upcoming step, that step is auto-skipped. This prevents self-approval and also skips redundant sign-offs.",
		scenario: "<strong>Scenario:</strong> S. N. Shukla (Department Head for Process CCs) creates and submits an MR for Process WTP. The MR route is AVP MR App which has 2 steps: Step 1 Dept Head Review → Step 2 AVP Final Approve.",
		before: {
			label: "Without Self-Skip (hypothetical)",
			nodes: [
				{ label: "Step 1: Department Head", state: "current", note: "would wait for submitter to review his own doc" },
				{ label: "Step 2: AVP Final", state: "queued" },
			],
		},
		after: {
			label: "With Self-Skip (actual)",
			nodes: [
				{ label: "Step 1: Department Head", state: "skipped", note: "auto-skipped (submitter has Dept Head role)" },
				{ label: "Step 2: AVP Final", state: "current", note: "goes straight to AVP" },
			],
		},
		code: "ts_po_approval.py:945  _get_target_step_with_skip()\n  for step in steps:\n      if step.role in user_roles:\n          continue  # skip\n      return step",
	},
	"higher-level": {
		tabLabel: "Higher-Level Override",
		title: "Higher-Level Override — seniors can act at any step",
		desc: "A user whose role appears at a HIGHER step can also act at the CURRENT step. Lets CEO/MD jump in and approve without waiting for PM review.",
		scenario: "<strong>Scenario:</strong> PO is at Step 1 (PM Review) for a ₹4L Store purchase. CEO opens it and wants to approve directly.",
		before: {
			label: "Only exact-step role can act",
			nodes: [
				{ label: "Step 1: Purchase Manager", state: "current", note: "only PM can act" },
				{ label: "Step 2: CEO Final", state: "queued", note: "CEO waiting" },
			],
		},
		after: {
			label: "With Higher-Level Override",
			nodes: [
				{ label: "Step 1: Purchase Manager", state: "done", note: "CEO acted here (step_order >= current + has role)" },
				{ label: "Step 2: CEO Final", state: "current", note: "now at CEO's own step" },
			],
		},
		code: "ts_po_approval.py:1466 / 1594\n  for step in steps:\n      if step.step_order >= current and step.role in user_roles:\n          can_act = True",
	},
	"cc-skip": {
		tabLabel: "CC Config Skip (MR)",
		title: "CC Config Skip — MR-only dynamic skipping",
		desc: "For MR only: if the Cost Center's Approval Config has no Reviewer mapped for a step, that step auto-skips. Lets small CCs bypass the HOD tier without changing the route.",
		scenario: "<strong>Scenario:</strong> Civil CC has no Department Head in its CC Approval Config (only Creator + Final Approver). An MR is created for Civil.",
		before: {
			label: "Route has 2 steps",
			nodes: [
				{ label: "Step 1: Dept Head Review", state: "queued", note: "route says this step exists" },
				{ label: "Step 2: AVP Final Approve", state: "queued" },
			],
		},
		after: {
			label: "Runtime — CC config takes over",
			nodes: [
				{ label: "Step 1: Dept Head Review", state: "skipped", note: "CC has no Reviewer mapped → skipped" },
				{ label: "Step 2: AVP Final Approve", state: "current", note: "MR goes directly to AVP" },
			],
		},
		code: "ts_po_approval.py:963  _get_target_step_for_mr()\n  if step.action_type == 'Review' and not cc_approvers:\n      continue",
	},
	"amount-tamper": {
		tabLabel: "Amount Tamper Check",
		title: "Amount Tamper Detection — can't sneak amount changes",
		desc: "At submission, grand_total is captured to ts_amount_at_submission. Before every approval action, the validator checks they still match. New gate-field guard blocks direct REST-API mutation.",
		scenario: "<strong>Scenario:</strong> PO submitted at ₹4,50,000. Someone calls frappe.client.set_value to change grand_total to ₹4,00,000.",
		before: {
			label: "At Submission",
			nodes: [
				{ label: "ts_amount_at_submission: ₹4,50,000", state: "done" },
				{ label: "grand_total: ₹4,50,000", state: "done" },
				{ label: "Status: Pending PM Review", state: "current" },
			],
		},
		after: {
			label: "After Tamper Attempt",
			nodes: [
				{ label: "ts_amount_at_submission: ₹4,50,000 (locked snapshot)", state: "done" },
				{ label: "grand_total: ₹4,00,000 (tampered)", state: "blocked" },
				{ label: "Next Approve action → BLOCKED", state: "blocked", note: "PO amount has changed during approval" },
			],
		},
		code: "ts_po_approval.py:1141  _validate_amount_unchanged()\nts_po_approval.py:1744  po_before_save() — _block_gate_field_tampering()",
	},
	"budget-block": {
		tabLabel: "Budget Block (PO)",
		title: "Budget Block — PO hard-stops on overspend",
		desc: "When a PO is submitted, ts_budget.validate_budget_on_po_submit() checks the CC's monthly budget. If utilization would exceed 100%, the PO is blocked. CEO or MD can override via ceo_budget_override() (audited).",
		scenario: "<strong>Scenario:</strong> Process WTP monthly budget ₹5,00,000. Already utilized ₹4,80,000. New PO ₹50,000 would push to ₹5,30,000 (106%).",
		before: {
			label: "Without budget check",
			nodes: [
				{ label: "PO ₹50,000 submitted", state: "current" },
				{ label: "Pending PM Review", state: "queued" },
			],
		},
		after: {
			label: "With budget check (Stop)",
			nodes: [
				{ label: "PO ₹50,000 submitted", state: "current" },
				{ label: "Budget check", state: "blocked", note: "106% > 100% → Stop" },
				{ label: "Error: Budget exceeded", state: "blocked", note: "revise PO or request CEO/MD override" },
			],
		},
		code: "ts_budget.py:396  validate_budget_on_po_submit()\nts_budget.py:464  ceo_budget_override()  # allows CEO, MD, System Manager",
	},
};

const AFE_ACTIONS = {
	"submit": {
		title: "Submit for Approval",
		who: "Document Creator (owner)",
		from: "Draft / Not Submitted",
		to: "Pending [Step 1] (or later via self-skip)",
		changes: ["ts_approval_status = Pending [Role]", "ts_submitted_by = current user", "ts_amount_at_submission = grand_total (PO snapshot)", "ts_current_step = target step", "Audit log row: Submitted"],
		notifications: ["Target step role users", "+ CC config users for step (MR)", "Channel: bell + email"],
		guards: ["docstatus = 0", "Has items", "Cost Center required (MR)", "Budget check (PO)"],
	},
	"approve": {
		title: "Approve / Forward",
		who: "Current step role user (+ higher-step override)",
		from: "Pending [Step N]",
		to: "Pending [Step N+1]",
		changes: ["ts_approval_status = Pending [Next Role]", "ts_current_step += 1", "Audit log: Reviewed & Forwarded"],
		notifications: ["Next step users (deduped)", "Role-based for PO, CC-aware for MR"],
		guards: ["Must be Pending*", "Not self-submitted", "Amount unchanged (PO)", "User has step role or higher"],
	},
	"final-approve": {
		title: "Final Approve",
		who: "Final Approver (step.action_type = Final Approve)",
		from: "Pending [Final Step]",
		to: "Approved (docstatus = 1)",
		changes: ["ts_approval_status = Approved", "ts_approved_by = current user", "ts_approved_date = now()", "docstatus = 1", "Audit log: Final Approved"],
		notifications: ["Owner + Submitter", "CC Notify Only users (MR)", "Purchase Manager + Purchase User (MR)", "Full chain (PO)", "TS Settings post_approval_notify table"],
		guards: ["Must be Pending [Final Step]", "Amount unchanged", "Not self-submitted"],
	},
	"revise": {
		title: "Revise",
		who: "Current step role user",
		from: "Pending [Step N]",
		to: "Revised (fields unlocked for creator)",
		changes: ["ts_approval_status = Revised", "ts_revision_count += 1 (PO)", "ts_revision_reason = [reason]", "ts_revised_by = [user]", "ts_can_send_to_md = 0", "Audit log: Revised"],
		notifications: ["Owner + Original Submitter (deduped)"],
		guards: ["Reason required (max 2000 chars)", "step.can_revise = 1", "Must be Pending*"],
	},
	"reject": {
		title: "Reject",
		who: "Current step role user",
		from: "Pending [Step N]",
		to: "Rejected (stays Draft, fields locked, NO recovery)",
		changes: ["ts_approval_status = Rejected", "ts_last_action = Rejected by [Role]", "Fields locked server-side + JS", "docstatus stays 0", "Audit log: Rejected"],
		notifications: ["Owner + Submitter", "Full chain (PO)", "post_approval_notify if notify_on_rejection = 1"],
		guards: ["Reason required", "step.can_reject = 1", "Must be Pending*", "⚠ IRREVERSIBLE"],
	},
	"hold": {
		title: "Hold (MR)",
		who: "Reviewer (HOD) at current Review step — NOT Final Approve",
		from: "Pending [Review Step N]",
		to: "On Hold [Step N]",
		changes: ["ts_mr_status = On Hold - [Role]", "ts_mr_hold_reason = [reason]", "ts_mr_held_by = [user]", "ts_mr_held_at_step = [step_order]", "Audit log: Held"],
		notifications: ["Creator + Submitter"],
		guards: ["Only Review action_type", "Reason required", "Must be Pending*"],
	},
	"resume": {
		title: "Resume (MR)",
		who: "Any user with held step's role",
		from: "On Hold [Step N]",
		to: "Pending [Step N] (same step)",
		changes: ["ts_mr_status = Pending [Role]", "ts_mr_current_step = ts_mr_held_at_step (restored)", "Hold fields cleared", "Audit log: Resumed"],
		notifications: ["Step's CC config users (fallback to role users)"],
		guards: ["Must be 'On Hold*'", "User has role of held step"],
	},
	"resubmit": {
		title: "Resubmit",
		who: "Owner / Submitter / Administrator only",
		from: "Revised",
		to: "Pending [Step 1] (with fresh self-skip)",
		changes: ["ts_approval_status = Pending [Role]", "ts_current_step = new target", "ts_approval_rule = re-resolved", "ts_amount_at_submission = grand_total (PO RE-SNAPSHOT)", "Audit log: Resubmitted"],
		notifications: ["New target step users (may differ from original)"],
		guards: ["Only owner/submitter/Admin", "Status must be Revised", "Rule must exist"],
	},
	"send-to-md": {
		title: "Send to MD (PO)",
		who: "CEO at step before MD (manual trigger)",
		from: "Pending [CEO Review]",
		to: "Pending [MD Final Approve]",
		changes: ["ts_approval_status = Pending [MD]", "ts_current_step += 1", "ts_can_send_to_md = 0", "Audit log: Sent to MD"],
		notifications: ["MD role users"],
		guards: ["Next step is_manual_trigger = 1", "User at current step", "Amount unchanged"],
	},
};

/* ═══════════════════════════════════════════════════════════════════
   STYLES — injected into document head
   ═══════════════════════════════════════════════════════════════════ */
const AFE_CSS = `
#afe-container { --brand:#1e4d8c; --brand-dark:#16355e; --border:#e5e9f0; --text-muted:#6b7280; font-size:13px; }
#afe-container .afe-selector { background:#fff; border:1px solid var(--border); border-radius:8px; padding:14px 18px; display:flex; align-items:center; gap:12px; margin-bottom:14px; flex-wrap:wrap; }
#afe-container .afe-selector label { font-weight:600; color:#6b7280; }
#afe-container .afe-selector select { padding:7px 10px; border:1px solid var(--border); border-radius:5px; min-width:320px; font-size:12px; }
#afe-container .afe-card { background:#fff; border:1px solid var(--border); border-radius:8px; padding:16px 18px; margin-bottom:14px; }
#afe-container .afe-card-header { display:flex; justify-content:space-between; align-items:center; padding-bottom:10px; margin-bottom:12px; border-bottom:1px solid var(--border); }
#afe-container .afe-card-title { font-size:13px; font-weight:700; color:var(--brand-dark); text-transform:uppercase; letter-spacing:0.4px; }
#afe-container .afe-card-badge { font-size:11px; padding:3px 8px; border-radius:10px; background:#f0f4f8; color:var(--brand); font-weight:600; }
#afe-container .afe-grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
@media (max-width:900px) { #afe-container .afe-grid-2 { grid-template-columns:1fr; } }
#afe-container .afe-user-card { background:linear-gradient(135deg,#eef2f9,#fbfcfe); border:1px solid #d8e1ee; border-radius:10px; padding:18px 22px; display:flex; gap:18px; align-items:center; margin-bottom:14px; flex-wrap:wrap; }
#afe-container .afe-avatar { width:60px; height:60px; border-radius:50%; background:var(--brand); color:#fff; display:flex; align-items:center; justify-content:center; font-size:22px; font-weight:700; flex-shrink:0; }
#afe-container .afe-user-info { flex:1; min-width:200px; }
#afe-container .afe-user-name { font-size:17px; font-weight:700; }
#afe-container .afe-user-email { font-size:12px; color:#6b7280; }
#afe-container .afe-user-roles { margin-top:8px; display:flex; gap:5px; flex-wrap:wrap; }
#afe-container .afe-role-chip { padding:3px 9px; background:#fff; border:1px solid #cbd5e1; border-radius:12px; font-size:11px; font-weight:500; color:#334155; }
#afe-container .afe-role-chip.executive { background:#fef3c7; border-color:#fbbf24; color:#92400e; }
#afe-container .afe-role-chip.manager { background:#dbeafe; border-color:#60a5fa; color:#1e40af; }
#afe-container .afe-role-chip.operational { background:#d1fae5; border-color:#34d399; color:#065f46; }
#afe-container .afe-user-stats { display:flex; gap:16px; padding-left:18px; border-left:1px solid #cbd5e1; flex-wrap:wrap; }
#afe-container .afe-stat { text-align:center; min-width:70px; }
#afe-container .afe-stat-value { font-size:22px; font-weight:700; color:var(--brand-dark); line-height:1; }
#afe-container .afe-stat-label { font-size:10px; color:#6b7280; text-transform:uppercase; margin-top:3px; letter-spacing:0.3px; }
#afe-container .afe-table { width:100%; border-collapse:collapse; font-size:12px; }
#afe-container .afe-table th { text-align:left; padding:7px 9px; background:#f8fafc; border-bottom:1px solid var(--border); font-size:10px; text-transform:uppercase; color:#6b7280; letter-spacing:0.4px; font-weight:600; }
#afe-container .afe-table td { padding:8px 9px; border-bottom:1px solid #f1f5f9; vertical-align:middle; }
#afe-container .afe-table tr:last-child td { border-bottom:none; }
#afe-container .afe-cc-name { font-weight:600; }
#afe-container .afe-cc-route { color:#6b7280; font-size:11px; }
#afe-container .afe-flow-badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:600; text-transform:uppercase; }
#afe-container .afe-flow-badge.standard { background:#e0e7ff; color:#3730a3; }
#afe-container .afe-flow-badge.direct { background:#fce7f3; color:#9d174d; }
#afe-container .afe-action-badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:600; }
#afe-container .afe-action-badge.review { background:#fef3c7; color:#92400e; }
#afe-container .afe-action-badge.final { background:#d1fae5; color:#065f46; }
#afe-container .afe-action-badge.approve { background:#dbeafe; color:#1e40af; }
#afe-container .afe-empty { padding:20px; text-align:center; color:#9ca3af; font-style:italic; font-size:12px; }
#afe-container .afe-pending-row { display:flex; align-items:center; padding:9px 11px; border-bottom:1px solid #f1f5f9; gap:10px; cursor:pointer; }
#afe-container .afe-pending-row:hover { background:#fef9e7; }
#afe-container .afe-pending-doc { font-family:monospace; font-size:12px; color:var(--brand); font-weight:600; min-width:150px; }
#afe-container .afe-pending-meta { flex:1; font-size:11px; color:#6b7280; }
#afe-container .afe-pending-amount { font-weight:700; min-width:80px; text-align:right; font-size:12px; }
#afe-container .afe-pending-days { min-width:55px; text-align:right; padding:2px 7px; border-radius:10px; font-size:11px; font-weight:600; }
#afe-container .afe-pending-days.fresh { background:#d1fae5; color:#065f46; }
#afe-container .afe-pending-days.aging { background:#fef3c7; color:#92400e; }
#afe-container .afe-pending-days.stuck { background:#fee2e2; color:#991b1b; }
#afe-container .afe-explain-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
@media (max-width:800px) { #afe-container .afe-explain-grid { grid-template-columns:1fr; } }
#afe-container .afe-explain-card { padding:11px 13px; background:#fafafa; border-radius:6px; border:1px solid var(--border); }
#afe-container .afe-explain-card strong { display:block; font-size:11px; color:var(--brand-dark); margin-bottom:4px; text-transform:uppercase; }
#afe-container .afe-explain-card p { font-size:11px; color:#6b7280; margin:0; }
#afe-container .afe-timeline { padding-left:20px; position:relative; }
#afe-container .afe-timeline::before { content:""; position:absolute; left:6px; top:4px; bottom:4px; width:2px; background:var(--border); }
#afe-container .afe-timeline-item { position:relative; padding:5px 0 8px 8px; border-bottom:1px solid #f1f5f9; }
#afe-container .afe-timeline-item::before { content:""; position:absolute; left:-18px; top:9px; width:10px; height:10px; border-radius:50%; background:var(--brand); border:2px solid #fff; }
#afe-container .afe-timeline-item.approved::before { background:#10b981; }
#afe-container .afe-timeline-item.rejected::before { background:#ef4444; }
#afe-container .afe-timeline-item.revised::before { background:#f59e0b; }
#afe-container .afe-timeline-item.held::before { background:#8b5cf6; }
#afe-container .afe-timeline-time { font-size:10px; color:#6b7280; font-weight:600; text-transform:uppercase; }
#afe-container .afe-timeline-text { font-size:12px; margin-top:2px; }
#afe-container .afe-tracer-input { display:flex; gap:10px; margin-bottom:13px; }
#afe-container .afe-tracer-input select { flex:1; padding:7px 10px; border:1px solid var(--border); border-radius:5px; }
#afe-container .afe-flow-diagram { display:flex; align-items:center; gap:6px; padding:18px 0; overflow-x:auto; }
#afe-container .afe-flow-node { background:#fff; border:2px solid var(--border); border-radius:8px; padding:9px 14px; text-align:center; min-width:130px; flex-shrink:0; }
#afe-container .afe-flow-node.done { border-color:#10b981; background:#ecfdf5; }
#afe-container .afe-flow-node.current { border-color:#f59e0b; background:#fef3c7; }
#afe-container .afe-flow-node.pending { border-color:var(--border); background:#f9fafb; color:#9ca3af; }
#afe-container .afe-flow-node-step { font-size:9px; text-transform:uppercase; color:#6b7280; font-weight:600; }
#afe-container .afe-flow-node-role { font-size:12px; font-weight:700; margin-top:3px; }
#afe-container .afe-flow-node-user { font-size:10px; margin-top:2px; color:#6b7280; }
#afe-container .afe-flow-arrow { color:#6b7280; font-size:18px; flex-shrink:0; }
#afe-container .afe-tracer-summary { margin-top:13px; padding:11px 13px; background:#f8fafc; border-radius:6px; font-size:12px; }
#afe-container .afe-tracer-summary div { margin:3px 0; }
#afe-container .afe-label { color:#6b7280; display:inline-block; min-width:110px; font-weight:600; text-transform:uppercase; font-size:10px; }
#afe-container .afe-tabs-bar { display:flex; gap:4px; border-bottom:1px solid var(--border); margin-bottom:14px; flex-wrap:wrap; }
#afe-container .afe-tab { padding:8px 15px; background:transparent; border:none; border-bottom:2px solid transparent; color:#6b7280; font-size:12px; font-weight:600; cursor:pointer; }
#afe-container .afe-tab.active { color:var(--brand); border-bottom-color:var(--brand); }
#afe-container .afe-flow-card { border:1px solid var(--border); border-radius:8px; padding:13px 15px; margin-bottom:11px; background:#fafbfc; }
#afe-container .afe-flow-card-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:9px; flex-wrap:wrap; gap:8px; }
#afe-container .afe-flow-card-title { font-weight:700; font-size:13px; color:var(--brand-dark); }
#afe-container .afe-flow-card-tags { display:flex; gap:5px; flex-wrap:wrap; }
#afe-container .afe-flow-tag { padding:2px 8px; border-radius:4px; font-size:10px; font-weight:600; }
#afe-container .afe-flow-tag.category { background:#e0e7ff; color:#3730a3; }
#afe-container .afe-flow-tag.amount { background:#f3f4f6; color:#374151; }
#afe-container .afe-flow-tag.steps { background:#fef3c7; color:#92400e; }
#afe-container .afe-flow-tag.route { background:#d1fae5; color:#065f46; }
#afe-container .afe-flow-mini-diagram { display:flex; align-items:center; gap:5px; padding:7px 0; flex-wrap:wrap; }
#afe-container .afe-mini-node { padding:5px 11px; background:#fff; border:2px solid var(--border); border-radius:5px; font-size:11px; text-align:center; min-width:105px; }
#afe-container .afe-mini-node.review { border-color:#fbbf24; background:#fffbeb; }
#afe-container .afe-mini-node.approve { border-color:#60a5fa; background:#eff6ff; }
#afe-container .afe-mini-node.final { border-color:#34d399; background:#ecfdf5; }
#afe-container .afe-mini-node-label { font-weight:700; }
#afe-container .afe-mini-node-type { font-size:9px; text-transform:uppercase; color:#6b7280; margin-top:2px; letter-spacing:0.3px; }
#afe-container .afe-mini-arrow { color:#6b7280; font-size:13px; }
#afe-container .afe-cc-list { margin-top:7px; padding:7px 9px; background:#fff; border-radius:4px; font-size:11px; color:#6b7280; border:1px dashed var(--border); }
#afe-container .afe-explainer-body { padding:15px; background:#fafbfc; border-radius:8px; }
#afe-container .afe-explainer-title { font-size:15px; font-weight:700; color:var(--brand-dark); margin-bottom:5px; }
#afe-container .afe-explainer-desc { font-size:12px; color:#475569; margin-bottom:14px; line-height:1.6; }
#afe-container .afe-scenario-box { padding:9px 13px; background:#fef3c7; border-left:3px solid #f59e0b; border-radius:4px; font-size:12px; color:#78350f; margin-bottom:13px; }
#afe-container .afe-before-after { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:14px 0; }
@media (max-width:800px) { #afe-container .afe-before-after { grid-template-columns:1fr; } }
#afe-container .afe-ba-col { padding:11px 13px; background:#fff; border:1px solid var(--border); border-radius:6px; }
#afe-container .afe-ba-col h4 { font-size:11px; text-transform:uppercase; color:#6b7280; margin:0 0 9px 0; letter-spacing:0.4px; font-weight:700; }
#afe-container .afe-ba-col h4.after { color:var(--brand); }
#afe-container .afe-ba-node { padding:7px 10px; border-radius:4px; margin-bottom:5px; font-size:11px; border:2px solid var(--border); background:#f9fafb; }
#afe-container .afe-ba-node.current { border-color:#f59e0b; background:#fef3c7; font-weight:700; }
#afe-container .afe-ba-node.done { border-color:#10b981; background:#ecfdf5; }
#afe-container .afe-ba-node.skipped { border-color:#a78bfa; background:#f5f3ff; color:#5b21b6; }
#afe-container .afe-ba-node.queued { color:#6b7280; }
#afe-container .afe-ba-node.blocked { border-color:#ef4444; background:#fee2e2; color:#991b1b; }
#afe-container .afe-ba-note { font-size:9px; font-style:italic; margin-top:2px; text-transform:uppercase; letter-spacing:0.3px; }
#afe-container .afe-code-ref { margin-top:11px; padding:8px 12px; background:#1e293b; color:#e2e8f0; border-radius:4px; font-family:monospace; font-size:11px; overflow-x:auto; white-space:pre; }
#afe-container .afe-action-chip-bar { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:14px; padding-bottom:11px; border-bottom:1px solid var(--border); }
#afe-container .afe-action-chip { padding:6px 13px; background:#f1f5f9; border:1px solid var(--border); border-radius:14px; font-size:11px; font-weight:600; color:#475569; cursor:pointer; }
#afe-container .afe-action-chip.active { background:var(--brand); color:#fff; border-color:var(--brand-dark); }
#afe-container .afe-action-body { padding:15px; background:#fafbfc; border-radius:8px; }
#afe-container .afe-action-title { font-size:15px; font-weight:700; color:var(--brand-dark); margin-bottom:9px; }
#afe-container .afe-action-meta { display:grid; grid-template-columns:repeat(3,1fr); gap:11px; margin-bottom:13px; }
@media (max-width:800px) { #afe-container .afe-action-meta { grid-template-columns:1fr; } }
#afe-container .afe-action-meta-cell { padding:9px 11px; background:#fff; border-radius:4px; border:1px solid var(--border); }
#afe-container .afe-action-meta-label { font-size:9px; text-transform:uppercase; color:#6b7280; font-weight:700; margin-bottom:3px; }
#afe-container .afe-action-meta-value { font-size:11px; }
#afe-container .afe-action-changes, #afe-container .afe-action-notifs { background:#fff; border-radius:4px; padding:11px 13px; border:1px solid var(--border); margin-top:9px; }
#afe-container .afe-action-changes h5, #afe-container .afe-action-notifs h5 { font-size:10px; text-transform:uppercase; color:#6b7280; margin:0 0 7px 0; font-weight:700; }
#afe-container .afe-action-changes ul, #afe-container .afe-action-notifs ul { list-style:none; padding:0; margin:0; }
#afe-container .afe-action-changes li { padding:4px 0; font-size:11px; font-family:monospace; color:#334155; border-bottom:1px dashed #f1f5f9; }
#afe-container .afe-action-changes li::before { content:"▸ "; color:var(--brand); font-weight:700; }
#afe-container .afe-action-notifs li { padding:4px 0; font-size:11px; color:#334155; border-bottom:1px dashed #f1f5f9; }
#afe-container .afe-action-notifs li::before { content:"🔔 "; }
#afe-container .afe-note { padding:11px 13px; background:#eff6ff; border-left:3px solid var(--brand); border-radius:4px; font-size:12px; }
#afe-container .afe-note-warn { background:#fef3c7; border-left-color:#f59e0b; color:#78350f; }
`;
