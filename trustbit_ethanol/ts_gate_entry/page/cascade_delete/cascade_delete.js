// Cascade Delete custom page (v2.11.0)
// 3-stage modal flow + initiator dashboard + CEO approval queue + recent log.
// Lesson 174 — version pill rendered into DOM by JS; bump on every page edit.
// Frappe v15 does NOT auto-inject {page}.html — JS builds the DOM via page.main.html().

const CD_PAGE_VERSION = "v2.13.1-2026-05-28";

const CD_CSS = `
	#cd-root { padding: 18px 22px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
	#cd-root .cd-h { font-size: 18px; font-weight: 600; margin-bottom: 6px; }
	#cd-root .cd-sub { font-size: 12px; color: #64748b; margin-bottom: 14px; }
	#cd-root .cd-version-pill { display: inline-block; padding: 1px 8px; background: #e0e7ff; color: #3730a3; border-radius: 10px; font-size: 10px; margin-left: 6px; vertical-align: middle; }
	#cd-root .cd-kill-banner { padding: 10px 14px; border-radius: 6px; margin-bottom: 14px; font-size: 13px; }
	#cd-root .cd-kill-on { background: #fef3c7; color: #92400e; border-left: 4px solid #f59e0b; }
	#cd-root .cd-kill-off { background: #fee2e2; color: #991b1b; border-left: 4px solid #dc2626; }
	#cd-root .cd-search-row { display: flex; gap: 8px; align-items: center; margin-bottom: 14px; }
	#cd-root .cd-search-row input { flex: 1; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px; font-family: monospace; }
	#cd-root .cd-btn { padding: 6px 14px; border-radius: 4px; border: none; font-size: 13px; cursor: pointer; }
	#cd-root .cd-btn-primary { background: #2563eb; color: #fff; }
	#cd-root .cd-btn-danger { background: #dc2626; color: #fff; }
	#cd-root .cd-btn-warning { background: #f59e0b; color: #fff; }
	#cd-root .cd-btn-secondary { background: #e2e8f0; color: #0f172a; }
	#cd-root .cd-btn:disabled { opacity: 0.5; cursor: not-allowed; }
	#cd-root .cd-section { margin-top: 24px; padding: 14px; background: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0; }
	#cd-root .cd-section h3 { margin: 0 0 10px 0; font-size: 14px; color: #0f172a; }
	#cd-root .cd-chain-table { width: 100%; border-collapse: collapse; font-size: 12px; }
	#cd-root .cd-chain-table th, #cd-root .cd-chain-table td { border: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; }
	#cd-root .cd-chain-table th { background: #f1f5f9; font-weight: 600; }
	#cd-root .cd-list { width: 100%; border-collapse: collapse; font-size: 12px; }
	#cd-root .cd-list th, #cd-root .cd-list td { border: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; }
	#cd-root .cd-list th { background: #f1f5f9; }
	#cd-root .cd-status-pill { padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
	#cd-root .cd-status-Pending { background: #fef3c7; color: #92400e; }
	#cd-root .cd-status-Approved { background: #dbeafe; color: #1e40af; }
	#cd-root .cd-status-Executed { background: #d1fae5; color: #065f46; }
	#cd-root .cd-status-Rejected, #cd-root .cd-status-Cancelled { background: #e2e8f0; color: #475569; }
	#cd-root .cd-status-Reverted { background: #ede9fe; color: #5b21b6; }
	#cd-root .cd-status-Failed { background: #fee2e2; color: #991b1b; }
	#cd-root .cd-modal-bg { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(15,23,42,0.7); display: flex; align-items: center; justify-content: center; z-index: 9999; }
	#cd-root .cd-modal { background: #fff; border-radius: 8px; padding: 22px; max-width: 540px; width: 90%; }
	#cd-root .cd-modal h2 { font-size: 16px; margin: 0 0 12px 0; color: #b91c1c; }
	#cd-root .cd-modal label { display: block; font-size: 12px; margin-top: 10px; color: #475569; }
	#cd-root .cd-modal input { width: 100%; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-family: monospace; box-sizing: border-box; }
	#cd-root .cd-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
	#cd-root .cd-empty { color: #94a3b8; font-style: italic; padding: 8px 0; font-size: 12px; }
	#cd-root .cd-legend { margin: 0 0 14px 0; padding: 10px 12px; background: #fff; border: 1px solid #e2e8f0; border-radius: 6px; }
	#cd-root .cd-legend-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; color: #64748b; margin-bottom: 6px; }
	#cd-root .cd-legend-items { display: grid; grid-template-columns: 1fr 1fr; gap: 3px 28px; }
	#cd-root .cd-legend-row { display: flex; align-items: center; gap: 8px; padding: 2px 0; }
	#cd-root .cd-legend-row .cd-status-pill { flex: 0 0 145px; text-align: center; }
	#cd-root .cd-legend-text { font-size: 11px; color: #475569; }
	@media (max-width: 900px) { #cd-root .cd-legend-items { grid-template-columns: 1fr; } }
	#cd-root .cd-expand-toggle { text-align: center; cursor: pointer; vertical-align: middle; }
	#cd-root .cd-expand-chip { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border-radius: 6px; background: #e0e7ff; color: #2563eb; font-size: 16px; font-weight: 700; line-height: 1; transition: background 0.12s; }
	#cd-root .cd-expand-toggle:hover .cd-expand-chip { background: #c7d2fe; }
	#cd-root .cd-expand-chip.cd-expand-open { background: #2563eb; color: #fff; }
	[data-theme="dark"] #cd-root .cd-expand-chip { background: #334155; color: #93c5fd; }
	[data-theme="dark"] #cd-root .cd-expand-chip.cd-expand-open { background: #2563eb; color: #fff; }
	#cd-root .cd-detail-row td { background: #f1f5f9; padding: 0 !important; }
	#cd-root .cd-detail-panel { padding: 12px 16px; }
	#cd-root .cd-detail-loading { padding: 12px 16px; color: #94a3b8; font-size: 12px; font-style: italic; }
	#cd-root .cd-detail-line { font-size: 12px; color: #334155; padding: 3px 0; }
	#cd-root .cd-detail-money { font-size: 13px; color: #065f46; font-weight: 600; }
	#cd-root .cd-detail-items { width: 100%; border-collapse: collapse; font-size: 12px; margin: 6px 0; }
	#cd-root .cd-detail-items th, #cd-root .cd-detail-items td { border: 1px solid #cbd5e1; padding: 4px 8px; text-align: left; }
	#cd-root .cd-detail-items th { background: #e2e8f0; }
	#cd-root .cd-detail-warn { margin-top: 6px; padding: 5px 10px; background: #fef2f2; border-left: 3px solid #dc2626; color: #991b1b; font-size: 12px; }
	#cd-root .cd-row-delete td { background: #fef2f2; }
	#cd-root .cd-row-keep td { background: #f1f5f9; color: #94a3b8; }
	#cd-root .cd-cutpoint-summary { margin-top: 10px; padding: 8px 12px; background: #eff6ff; border-left: 4px solid #2563eb; border-radius: 4px; font-size: 12px; color: #1e3a8a; }
	[data-theme="dark"] #cd-root .cd-row-delete td { background: #3f1d1d; }
	[data-theme="dark"] #cd-root .cd-row-keep td { background: #1e293b; color: #64748b; }
	[data-theme="dark"] #cd-root .cd-cutpoint-summary { background: #1e293b; color: #93c5fd; }
	[data-theme="dark"] #cd-root .cd-detail-row td { background: #0f172a; }
	[data-theme="dark"] #cd-root .cd-detail-line { color: #cbd5e1; }
	[data-theme="dark"] #cd-root .cd-detail-items th { background: #1e293b; }
	[data-theme="dark"] #cd-root .cd-detail-items td { background: #0f172a; color: #e2e8f0; border-color: #334155; }
	[data-theme="dark"] #cd-root .cd-legend { background: #1e293b; border-color: #334155; }
	[data-theme="dark"] #cd-root .cd-legend-text { color: #cbd5e1; }
	[data-theme="dark"] #cd-root .cd-section { background: #1e293b; border-color: #334155; }
	[data-theme="dark"] #cd-root .cd-section h3 { color: #e2e8f0; }
	[data-theme="dark"] #cd-root .cd-sub { color: #94a3b8; }
	[data-theme="dark"] #cd-root .cd-chain-table th, [data-theme="dark"] #cd-root .cd-list th { background: #1e293b; color: #e2e8f0; }
	[data-theme="dark"] #cd-root .cd-chain-table td, [data-theme="dark"] #cd-root .cd-list td { background: #0f172a; color: #e2e8f0; border-color: #334155; }
	[data-theme="dark"] #cd-root .cd-modal { background: #1e293b; color: #e2e8f0; }
	[data-theme="dark"] #cd-root .cd-modal label { color: #94a3b8; }
	[data-theme="dark"] #cd-root .cd-modal input, [data-theme="dark"] #cd-root .cd-search-row input { background: #0f172a; color: #e2e8f0; border-color: #475569; }
	[data-theme="dark"] #cd-root .cd-btn-secondary { background: #334155; color: #e2e8f0; }
	#cd-root .cd-warn-note { font-size: 11px; background: #fef3c7; color: #92400e; border-left: 3px solid #f59e0b; padding: 5px 9px; margin: 0 0 8px; }
	[data-theme="dark"] #cd-root .cd-warn-note { background: #3a2e12; color: #fcd34d; border-color: #a16207; }
	#cd-root .cd-fin-panel { margin-top: 10px; border: 1px solid #dc2626; background: #fef2f2; border-radius: 6px; padding: 8px 12px; font-size: 12px; }
	#cd-root .cd-fin-panel .cd-fin-title { color: #b91c1c; font-weight: 600; }
	#cd-root .cd-fin-panel .cd-fin-body { margin-top: 4px; color: #7f1d1d; line-height: 1.7; }
	#cd-root .cd-fin-panel .cd-fin-foot { margin-top: 5px; font-style: italic; color: #991b1b; }
	[data-theme="dark"] #cd-root .cd-fin-panel { background: #3f1d1d; border-color: #dc2626; }
	[data-theme="dark"] #cd-root .cd-fin-panel .cd-fin-title { color: #fca5a5; }
	[data-theme="dark"] #cd-root .cd-fin-panel .cd-fin-body { color: #fecaca; }
	[data-theme="dark"] #cd-root .cd-fin-panel .cd-fin-foot { color: #fca5a5; }
	[data-theme="dark"] #cd-root .cd-detail-warn { background: #3f1d1d; color: #fecaca; border-color: #dc2626; }
	#cd-root code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; padding: 0 3px; background: rgba(15,23,42,0.06); border-radius: 3px; }
	[data-theme="dark"] #cd-root code { background: rgba(255,255,255,0.08); color: inherit; }
	#cd-root .cd-cutpoint-summary, #cd-root .cd-warn-note { overflow-wrap: anywhere; }
`;

const CD_BODY = `
<div id="cd-root">
	<div class="cd-h">Token Cascade Delete System <span class="cd-version-pill" id="cd-version-pill">v2.11.0</span></div>
	<div class="cd-sub">Hardened cascade-delete tool for Super Admin &middot; 2-person rule &middot; hash-chained audit &middot; 5-min revert grace.</div>
	<div id="cd-kill-banner"></div>
	<div class="cd-search-row" id="cd-search-row" style="display:none;">
		<input id="cd-token-input" type="text" placeholder="BBPL-TKN-YYYY-NNNNN" maxlength="20" />
		<button class="cd-btn cd-btn-primary" id="cd-preview-btn">Preview Chain</button>
	</div>
	<div id="cd-preview-area"></div>
	<div class="cd-section" id="cd-pending-section">
		<h3>Pending CEO Approval</h3>
		<div id="cd-pending-list"></div>
	</div>
	<div class="cd-section" id="cd-recent-section">
		<h3>Recent Cascade Deletions</h3>
		<div id="cd-recent-list"></div>
	</div>
</div>
`;

frappe.pages["cascade-delete"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Cascade Delete (BBPL)"),
		single_column: true,
	});

	// Inject CSS once
	if (!document.getElementById("cd-styles")) {
		const style = document.createElement("style");
		style.id = "cd-styles";
		style.textContent = CD_CSS;
		document.head.appendChild(style);
	}

	// Build the DOM (Frappe v15 does not auto-inject {page}.html)
	page.main.html(CD_BODY);

	setTimeout(() => _init(), 100);
};

frappe.pages["cascade-delete"].refresh = function () {
	_render_pending_list();
	_render_recent_list();
};

// Status legend strip — one line per status so viewers understand the pills
// without having to ask. Rendered above the Recent Cascade Deletions table.
function _cd_status_legend() {
	const items = [
		["Pending", "Pending CEO Approval", "Super Admin initiated — waiting for a CEO to approve or reject."],
		["Approved", "Approved", "CEO approved — engine runs immediately (transient state)."],
		["Executed", "Executed", "Cascade completed — every step succeeded."],
		["Failed", "Failed", "A step failed — whole cascade rolled back, chain left intact (nothing half-deleted)."],
		["Reverted", "Reverted", "Was Executed, then restored from backup within the 5-min revert window."],
		["Rejected", "Rejected", "CEO declined the request — nothing was deleted."],
		["Cancelled", "Cancelled", "Cancelled before execution — nothing was deleted."],
	];
	let html = `<div class="cd-legend"><div class="cd-legend-title">Status guide</div><div class="cd-legend-items">`;
	for (const [cls, label, meaning] of items) {
		html += `<div class="cd-legend-row">`
			+ `<span class="cd-status-pill cd-status-${cls}">${frappe.utils.escape_html(label)}</span>`
			+ `<span class="cd-legend-text">${frappe.utils.escape_html(meaning)}</span>`
			+ `</div>`;
	}
	html += `</div></div>`;
	return html;
}

// Format a system datetime string for display — drops microseconds so
// "Executed" vs "Revert Until" (5 min apart) are visually distinct at a glance.
function _cd_fmt_dt(val) {
	if (!val) return "-";
	try {
		// frappe.datetime.str_to_user renders per the user's date/time format,
		// naturally dropping the .ffffff microsecond tail.
		return frappe.utils.escape_html(frappe.datetime.str_to_user(val));
	} catch (e) {
		// Fallback: trim the microsecond fraction manually.
		return frappe.utils.escape_html(String(val).split(".")[0]);
	}
}

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
	// v2.13.1 — mirror server-side _INITIATE_ROLES from v2.13.0
	// (cascade_delete_api.py:42). Stock User + Stock Manager can now initiate.
	// Server is still the authoritative gate; this just controls UI visibility.
	const user_roles = new Set(frappe.user_roles || []);
	const can_initiate = user_roles.has("Super Admin")
		|| user_roles.has("Stock Manager")
		|| user_roles.has("Stock User");
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

// Deletion-order codes, leaf-last in the table's visual (creation) order.
// A cut-point deletes its own row + every row BELOW it (toward Purchase Invoice).
const _CD_CHAIN = [
	{ code: "GE", doctype: "TS Gate Entry",        cut: "Full Chain" },
	{ code: "WB", doctype: "TS Weighbridge Log",   cut: "TS Weighbridge Log" },
	{ code: "QI", doctype: "TS Quality Inspection", cut: "TS Quality Inspection" },
	{ code: "DS", doctype: "TS Deduction Sheet",   cut: "TS Deduction Sheet" },
	{ code: "PR", doctype: "Purchase Receipt",     cut: "Purchase Receipt" },
	{ code: "PI", doctype: "Purchase Invoice",     cut: "Purchase Invoice" },
];

function _render_preview(chain, token_name) {
	const area = document.getElementById("cd-preview-area");
	if (!chain.token_record) {
		area.innerHTML = `<div class="cd-section"><h3>Preview</h3><div class="cd-empty">Token <code>${frappe.utils.escape_html(token_name)}</code> not found.</div></div>`;
		return;
	}
	const t = chain.token_record;
	// Per-stage data
	const stage = {
		GE: chain.gate_entries, WB: chain.weighbridge_logs, QI: chain.quality_inspections,
		DS: chain.deduction_sheets, PR: chain.purchase_receipts, PI: chain.purchase_invoices,
	};

	let html = `<div class="cd-section"><h3>Chain Preview for <code>${frappe.utils.escape_html(token_name)}</code></h3>`;
	html += `<p style="font-size:12px;color:#64748b;margin:0 0 8px;">Pick the deepest document to delete. Everything from there to the Purchase Invoice (leaf) is removed; documents above the cut-point are kept and the Token status auto-resets. Full Chain deletes the Token too.</p>`;
	html += `<table class="cd-chain-table"><tr><th style="width:78px">Delete from</th><th>DocType</th><th>Count</th><th>Submitted</th><th>Notes</th></tr>`;
	// Token row — no radio; deleted only on Full Chain
	html += `<tr id="cd-cp-row-TOKEN"><td style="text-align:center;color:#cbd5e1;">—</td>`
		+ `<td>TS Token</td><td>1</td><td>${t.docstatus === 1 ? "yes" : "no"}</td>`
		+ `<td>Status: ${frappe.utils.escape_html(t.status || "-")} | Vehicle: ${frappe.utils.escape_html(t.vehicle_number || "-")}</td></tr>`;
	for (const s of _CD_CHAIN) {
		const rows = stage[s.code] || [];
		const submitted = rows.filter(r => r.docstatus === 1).length;
		// v2.11.2 — partial cascade RE-ENABLED. All cut-points selectable;
		// server `_validate_partial_cut_feasibility` is the authoritative gate.
		const checked = s.code === "GE" ? "checked" : "";
		const has_rows = rows.length > 0;
		// Disable cut-points that point to a doctype the chain doesn't have
		// (e.g. PI cut when there's no PI). Always keep GE selectable.
		const disabled = (s.code === "GE" || has_rows) ? "" : "disabled";
		let note = "";
		if (s.code === "QI" && submitted) note = "<strong style='color:#dc2626'>submitted</strong>";
		if (s.code === "PR" && submitted) note = "<strong style='color:#dc2626'>submitted</strong>";
		if (!has_rows && s.code !== "GE") note = "<span style='color:#94a3b8'>n/a (no rows)</span>";
		html += `<tr id="cd-cp-row-${s.code}">`
			+ `<td style="text-align:center;"><input type="radio" name="cd-cutpoint" value="${s.cut}" data-code="${s.code}" ${checked} ${disabled}></td>`
			+ `<td>${frappe.utils.escape_html(s.doctype)}</td><td>${rows.length}</td><td>${submitted}</td><td>${note}</td></tr>`;
	}
	html += `</table>`;
	html += `<p style="margin-top:8px;font-size:12px;color:#64748b;">Stock Ledger Entries: ${chain.stock_ledger_entries_count} | GL Entries: ${chain.gl_entries_count}</p>`;
	// B4 (v2.11.1) — financial blast-radius panel. If the chain has downstream
	// Payment Entries / Journal Entries / Landed Cost Vouchers / billed PRs, the
	// engine BLOCKS the cascade unless the Force-Payment-Links override is typed.
	if (chain.has_financial_links) {
		const esc = frappe.utils.escape_html;
		const _list = (a) => (a && a.length) ? a.map(esc).join(", ") : "—";
		html += `<div class="cd-fin-panel">`
			+ `<span class="cd-fin-title">⚠ Financial documents linked to this chain</span>`
			+ `<div class="cd-fin-body">`
			+ `Payment Entries: <code>${_list(chain.payment_entries && chain.payment_entries.map(r => r.name))}</code><br>`
			+ `Journal Entries: <code>${_list(chain.journal_entries && chain.journal_entries.map(r => r.name))}</code><br>`
			+ `Landed Cost Vouchers: <code>${_list(chain.landed_cost_vouchers && chain.landed_cost_vouchers.map(r => r.name))}</code><br>`
			+ `Billed Purchase Receipts: <code>${_list(chain.billed_prs)}</code>`
			+ `</div>`
			+ `<div class="cd-fin-foot">Deleting this chain leaves dangling GL references. The cascade will require the <code>FORCE-DELETE-WITH-PAYMENTS</code> confirmation.</div>`
			+ `</div>`;
	}
	html += `<div id="cd-cutpoint-summary" class="cd-cutpoint-summary"></div>`;
	html += `<div style="margin-top:14px;"><button class="cd-btn cd-btn-danger" id="cd-initiate-btn">Initiate Cascade Deletion →</button></div>`;
	html += `</div>`;
	area.innerHTML = html;

	const _stage_for_cut = (cut) => {
		// returns {needs_force_pr, needs_force_mi, deleted_codes[]}
		const idx = _CD_CHAIN.findIndex(c => c.cut === cut);
		const deleted = _CD_CHAIN.slice(idx).map(c => c.code);  // this row + below
		const inRange = (code) => deleted.includes(code);
		const subQI = (stage.QI || []).some(r => r.docstatus === 1);
		const subPR = (stage.PR || []).some(r => r.docstatus === 1);
		const subPI = (stage.PI || []).some(r => r.docstatus === 1);
		return {
			needs_force_pr: (inRange("PR") && subPR) || (inRange("PI") && subPI),
			needs_force_mi: inRange("QI") && subQI,
			deleted: deleted,
		};
	};

	// v2.11.2 — client-side mirror of the server `_CD_PARTIAL_MATRIX` allowlist.
	// Used to preview the projected new_status AND to warn when the operator
	// picks an (status, cut) pair the server will reject. Server is authoritative.
	const _CD_PARTIAL_MATRIX_JS = {
		"GRN Created":   {"Purchase Invoice": "GRN Created", "Purchase Receipt": "Tare Weighed",
		                   "TS Deduction Sheet": "Tare Weighed", "TS Quality Inspection": "Tare Weighed",
		                   "TS Weighbridge Log": "PO Linked"},
		"Tare Weighed":  {"Purchase Receipt": "Tare Weighed", "TS Deduction Sheet": "Tare Weighed",
		                   "TS Quality Inspection": "Tare Weighed", "TS Weighbridge Log": "PO Linked"},
		"Gross Weighed": {"Purchase Receipt": "Gross Weighed", "TS Deduction Sheet": "Gross Weighed",
		                   "TS Quality Inspection": "Gross Weighed", "TS Weighbridge Log": "PO Linked"},
	};

	const _update = () => {
		const sel = area.querySelector("input[name='cd-cutpoint']:checked");
		const cut = sel ? sel.value : "Full Chain";
		const code = sel ? sel.dataset.code : "GE";
		const info = _stage_for_cut(cut);
		// v2.11.2 — Full Chain AND GE cut both delete the Token (engine wins).
		const deletes_token = (cut === "Full Chain" || cut === "TS Gate Entry");
		// Tint rows
		for (const s of _CD_CHAIN) {
			const row = document.getElementById("cd-cp-row-" + s.code);
			if (!row) continue;
			row.classList.remove("cd-row-delete", "cd-row-keep");
			row.classList.add(info.deleted.includes(s.code) ? "cd-row-delete" : "cd-row-keep");
		}
		const tokRow = document.getElementById("cd-cp-row-TOKEN");
		if (tokRow) {
			tokRow.classList.remove("cd-row-delete", "cd-row-keep");
			tokRow.classList.add(deletes_token ? "cd-row-delete" : "cd-row-keep");
		}
		// Summary
		const sumEl = document.getElementById("cd-cutpoint-summary");
		const delCount = info.deleted.length;
		const kept = _CD_CHAIN.filter(c => !info.deleted.includes(c.code)).map(c => c.code);
		const cur = t.status || "";
		let tokenLine;
		let serverWarn = "";
		if (deletes_token) {
			tokenLine = "<strong style='color:#dc2626'>Token will be DELETED.</strong>";
		} else {
			const projected = (_CD_PARTIAL_MATRIX_JS[cur] || {})[cut];
			if (projected) {
				tokenLine = `Token will be <strong>KEPT</strong>, status reset <code>${frappe.utils.escape_html(cur)}</code> → <code>${frappe.utils.escape_html(projected)}</code>.`;
			} else {
				tokenLine = `Token will be <strong>KEPT</strong>, status reset is <strong style='color:#dc2626'>NOT in the allowlist for this combination</strong>.`;
				serverWarn = `<div class="cd-warn-note" style="margin-top:8px;">⚠ Server will REJECT this combination: current status <code>${frappe.utils.escape_html(cur)}</code> with cut-point <code>${frappe.utils.escape_html(cut)}</code> is outside the partial-cut allowlist. Either pick Full Chain or amend the Token status first.</div>`;
			}
		}
		sumEl.innerHTML = `Will delete <strong>${delCount}</strong> document type(s) [${info.deleted.join(", ")}]`
			+ (kept.length ? ` &middot; keep [${kept.join(", ")}]` : "")
			+ `. ${tokenLine}` + serverWarn;
	};

	area.querySelectorAll("input[name='cd-cutpoint']").forEach(r => r.addEventListener("change", _update));
	_update();

	document.getElementById("cd-initiate-btn").addEventListener("click", () => {
		const sel = area.querySelector("input[name='cd-cutpoint']:checked");
		const cut = sel ? sel.value : "Full Chain";
		const info = _stage_for_cut(cut);
		const needs_force_payment = !!chain.has_financial_links;
		_show_stage1_confirm(token_name, info.needs_force_pr, info.needs_force_mi,
		                     needs_force_payment, cut);
	});
}

function _show_stage1_confirm(token_name, needs_force_pr, needs_force_mi, needs_force_payment, cut_point) {
	cut_point = cut_point || "Full Chain";
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
	(document.getElementById("cd-root") || document.body).appendChild(div);
	const input = div.querySelector("#cd-st1-token");
	const continue_btn = div.querySelector("#cd-st1-continue");
	input.addEventListener("input", () => {
		continue_btn.disabled = input.value !== token_name;
	});
	div.querySelector("#cd-st1-cancel").addEventListener("click", () => div.remove());
	continue_btn.addEventListener("click", () => {
		div.remove();
		if (needs_force_pr) {
			_show_stage2_force_pr(token_name, needs_force_mi, needs_force_payment, cut_point);
		} else if (needs_force_mi) {
			_show_stage2_force_mi(token_name, false, needs_force_payment, cut_point);
		} else if (needs_force_payment) {
			_show_stage2_force_payment(token_name, false, false, cut_point);
		} else {
			_show_stage3_final(token_name, false, false, false, cut_point);
		}
	});
}

function _show_stage2_force_pr(token_name, needs_force_mi, needs_force_payment, cut_point) {
	cut_point = cut_point || "Full Chain";
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
	(document.getElementById("cd-root") || document.body).appendChild(div);
	const input = div.querySelector("#cd-st2-pr");
	const btn = div.querySelector("#cd-st2-continue");
	input.addEventListener("input", () => { btn.disabled = input.value !== "FORCE-DELETE-PR"; });
	div.querySelector("#cd-st2-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => {
		div.remove();
		if (needs_force_mi) {
			_show_stage2_force_mi(token_name, true, needs_force_payment, cut_point);
		} else if (needs_force_payment) {
			_show_stage2_force_payment(token_name, true, false, cut_point);
		} else {
			_show_stage3_final(token_name, true, false, false, cut_point);
		}
	});
}

function _show_stage2_force_mi(token_name, force_pr, needs_force_payment, cut_point) {
	cut_point = cut_point || "Full Chain";
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
	(document.getElementById("cd-root") || document.body).appendChild(div);
	const input = div.querySelector("#cd-st2-mi");
	const btn = div.querySelector("#cd-st2-continue");
	input.addEventListener("input", () => { btn.disabled = input.value !== "FORCE-DELETE-MI"; });
	div.querySelector("#cd-st2-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => {
		div.remove();
		if (needs_force_payment) {
			_show_stage2_force_payment(token_name, force_pr, true, cut_point);
		} else {
			_show_stage3_final(token_name, force_pr, true, false, cut_point);
		}
	});
}

function _show_stage2_force_payment(token_name, force_pr, force_mi, cut_point) {
	// B4 (v2.11.1) — extra type-to-confirm when the chain has downstream
	// financial documents (Payment Entries / Journal Entries / Landed Cost
	// Vouchers / billed PRs). Without this the engine BLOCKS the cascade.
	cut_point = cut_point || "Full Chain";
	const modal_html = `
		<div class="cd-modal-bg">
		<div class="cd-modal">
		<h2>Stage 2 — Force Delete With Payment Links</h2>
		<p style="font-size:12px;color:#475569;">This chain has <strong>downstream financial documents</strong> — a Payment Entry, Journal Entry, Landed Cost Voucher, or an already-billed Purchase Receipt. Deleting it leaves <strong>dangling GL references</strong>. Type <code>FORCE-DELETE-WITH-PAYMENTS</code> only if this is genuinely intended.</p>
		<label for="cd-st2-pay">Type FORCE-DELETE-WITH-PAYMENTS:</label>
		<input type="text" id="cd-st2-pay" autofocus />
		<div class="cd-modal-actions">
			<button class="cd-btn cd-btn-secondary" id="cd-st2-cancel">Cancel</button>
			<button class="cd-btn cd-btn-danger" id="cd-st2-continue" disabled>Continue →</button>
		</div>
		</div></div>`;
	const div = document.createElement("div");
	div.innerHTML = modal_html;
	(document.getElementById("cd-root") || document.body).appendChild(div);
	const input = div.querySelector("#cd-st2-pay");
	const btn = div.querySelector("#cd-st2-continue");
	input.addEventListener("input", () => { btn.disabled = input.value !== "FORCE-DELETE-WITH-PAYMENTS"; });
	div.querySelector("#cd-st2-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => {
		div.remove();
		_show_stage3_final(token_name, force_pr, force_mi, true, cut_point);
	});
}

function _show_stage3_final(token_name, force_pr, force_mi, force_payment, cut_point) {
	cut_point = cut_point || "Full Chain";
	const scope_line = cut_point === "Full Chain"
		? `<span style="color:#dc2626;font-weight:600;">FULL CHAIN</span> — the Token and every downstream document will be deleted.`
		: `<span style="color:#dc2626;font-weight:600;">PARTIAL</span> — delete from <code>${frappe.utils.escape_html(cut_point)}</code> down to Purchase Invoice; the Token is KEPT (status will be reset).`;
	const modal_html = `
		<div class="cd-modal-bg">
		<div class="cd-modal">
		<h2>Stage 3 of 3 — FINAL CONFIRMATION</h2>
		<p style="font-size:12px;color:#475569;">You are about to <strong>queue a cascade delete</strong> for <code>${frappe.utils.escape_html(token_name)}</code>. A CEO must approve before execution. Type the token name ONE MORE TIME to submit.</p>
		<p class="cd-warn-note" style="font-size:12px;">Scope: ${scope_line}</p>
		<label for="cd-st3-final">Type token name to submit:</label>
		<input type="text" id="cd-st3-final" maxlength="20" autofocus />
		<div class="cd-modal-actions">
			<button class="cd-btn cd-btn-secondary" id="cd-st3-cancel">Cancel</button>
			<button class="cd-btn cd-btn-danger" id="cd-st3-submit" disabled>Submit for CEO Approval →</button>
		</div>
		</div></div>`;
	const div = document.createElement("div");
	div.innerHTML = modal_html;
	(document.getElementById("cd-root") || document.body).appendChild(div);
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
				force_payment_links: force_payment ? 1 : 0,
				force_payment_confirmation_typed: force_payment ? "FORCE-DELETE-WITH-PAYMENTS" : "",
				client_screen: `${screen.width}x${screen.height}`,
				client_language: navigator.language,
				cut_point: cut_point,
			},
			callback(r) {
				div.remove();
				if (r.message && r.message.success) {
					frappe.show_alert({ message: __("Cascade queued. Log: ") + r.message.log_name, indicator: "green" });
					// Clear the now-stale preview + token input so the operator can't
					// re-initiate the same token; the new entry appears in Pending CEO Approval.
					const area = document.getElementById("cd-preview-area");
					if (area) area.innerHTML = "";
					const tok_input = document.getElementById("cd-token-input");
					if (tok_input) tok_input.value = "";
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
		let html = `<table class="cd-list"><tr><th style="width:40px"></th><th>Log</th><th>Token</th><th>Initiated By</th><th>At</th><th>Actions</th></tr>`;
		for (const r of rows) {
			const sn = frappe.utils.escape_html(r.name);
			html += `<tr>`;
			html += `<td class="cd-expand-toggle" data-toggle="${sn}" title="Show token details"><span class="cd-expand-chip">▸</span></td>`;
			html += `<td><a href="/app/ts-cascade-delete-log/${sn}">${sn}</a></td>`;
			html += `<td><code>${frappe.utils.escape_html(r.target_token)}</code></td>`;
			html += `<td>${frappe.utils.escape_html(r.initiated_by)}</td>`;
			html += `<td>${_cd_fmt_dt(r.initiated_at)}</td>`;
			html += `<td>`;
			if (can_approve && r.initiated_by !== frappe.session.user) {
				html += `<button class="cd-btn cd-btn-primary" data-action="approve" data-log="${sn}">Approve</button>`;
				html += ` <button class="cd-btn cd-btn-secondary" data-action="reject" data-log="${sn}">Reject</button>`;
			}
			html += `</td></tr>`;
		}
		html += `</table>`;
		el.innerHTML = html;
		el.querySelectorAll("button[data-action]").forEach(btn => {
			btn.addEventListener("click", () => _ceo_decision(btn.dataset.log, btn.dataset.action));
		});
		el.querySelectorAll(".cd-expand-toggle").forEach(td => {
			td.addEventListener("click", () => _cd_toggle_pending_detail(td));
		});
	});
}

// Expandable lazy-loaded review panel under a Pending row.
function _cd_toggle_pending_detail(td) {
	const log_name = td.dataset.toggle;
	const mainRow = td.closest("tr");
	const next = mainRow.nextElementSibling;
	const chip = td.querySelector(".cd-expand-chip") || td;
	if (next && next.classList.contains("cd-detail-row")) {
		next.remove();
		chip.textContent = "▸";
		chip.classList.remove("cd-expand-open");
		return;
	}
	chip.textContent = "▾";
	chip.classList.add("cd-expand-open");
	const detailRow = document.createElement("tr");
	detailRow.className = "cd-detail-row";
	detailRow.innerHTML = `<td colspan="6"><div class="cd-detail-loading">Loading token detail…</div></td>`;
	mainRow.after(detailRow);
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.cascade_delete_api.get_pending_review_detail",
		args: { log_name },
		callback(r) {
			const cell = detailRow.querySelector("td");
			cell.innerHTML = r.message
				? _cd_render_review_detail(r.message)
				: `<div class="cd-empty">No detail available.</div>`;
		},
		error() {
			detailRow.querySelector("td").innerHTML =
				`<div class="cd-empty" style="color:#dc2626;">Failed to load token detail.</div>`;
		},
	});
}

function _cd_inr(n) {
	try { return format_currency(n || 0, "INR"); }
	catch (e) { return "₹" + (n || 0); }
}

function _cd_render_review_detail(d) {
	const c = d.counts || {};
	const esc = frappe.utils.escape_html;
	let h = `<div class="cd-detail-panel">`;
	h += `<div class="cd-detail-line"><b>Token:</b> ${esc(d.token_status || "-")}`
		+ ` &middot; <b>Vehicle:</b> ${esc(d.vehicle || "-")}`
		+ ` &middot; <b>Supplier:</b> ${esc(d.supplier || "-")}</div>`;
	h += `<div class="cd-detail-line"><b>Chain:</b> ${c.gate_entries || 0} GE &middot; ${c.weighbridge_logs || 0} WB`
		+ ` &middot; ${c.quality_inspections || 0} QI &middot; ${c.deduction_sheets || 0} DS`
		+ ` &middot; ${c.purchase_receipts || 0} PR &middot; ${c.purchase_invoices || 0} PI`
		+ ` &middot; ${c.gl_entries || 0} GL entries</div>`;
	h += `<div class="cd-detail-line cd-detail-money">💰 <b>PO</b> ${_cd_inr(d.po_total)}`
		+ ` &middot; <b>PR</b> ${_cd_inr(d.pr_total)}`
		+ ` &middot; <b>PI</b> ${_cd_inr(d.pi_total)}</div>`;
	if (d.items && d.items.length) {
		h += `<table class="cd-detail-items"><tr><th>Item</th><th>Ordered</th><th>Received</th></tr>`;
		for (const it of d.items) {
			const uom = esc(it.uom || "");
			h += `<tr><td>${esc(it.item_name || it.item_code || "-")}</td>`
				+ `<td>${it.ordered_qty != null ? esc(String(it.ordered_qty)) + " " + uom : "—"}</td>`
				+ `<td>${it.received_qty != null ? esc(String(it.received_qty)) + " " + uom : "—"}</td></tr>`;
		}
		h += `</table>`;
	} else {
		h += `<div class="cd-detail-line" style="color:#94a3b8;">No purchase-receipt items in this chain.</div>`;
	}
	if (d.force_pr_override) {
		h += `<div class="cd-detail-warn">⚠ Force-PR override ON — a submitted Purchase Receipt will be force-deleted.</div>`;
	}
	if (d.force_mi_override) {
		h += `<div class="cd-detail-warn">⚠ Force-MI override ON — a submitted Quality Inspection will be force-deleted.</div>`;
	}
	// B4 (v2.11.1) — surface the financial blast radius for the approving CEO.
	const fl = d.financial_links || {};
	if (fl.has_financial_links) {
		const _l = (a) => (a && a.length) ? a.map(esc).join(", ") : "—";
		h += `<div class="cd-detail-warn">⚠ <b>Downstream financial documents</b> — `
			+ `Payment Entries: ${_l(fl.payment_entries)} &middot; `
			+ `Journal Entries: ${_l(fl.journal_entries)} &middot; `
			+ `Landed Cost Vouchers: ${_l(fl.landed_cost_vouchers)} &middot; `
			+ `Billed PRs: ${_l(fl.billed_prs)}. Deleting this chain leaves dangling GL references.</div>`;
	}
	if (d.force_payment_links) {
		h += `<div class="cd-detail-warn">⚠ Force-Payment-Links override ON — the chain will be deleted despite linked financial documents.</div>`;
	}
	h += `</div>`;
	return h;
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
						if (r.message && r.message.success) {
							frappe.show_alert({
								message: __("✓ Cascade approved &amp; executed — {0}", [frappe.utils.escape_html(log_name)]),
								indicator: "green",
							}, 7);
						} else if (r.message && !r.message.success) {
							frappe.msgprint({
								title: __("Cascade Engine Failed"),
								message: `<p>The cascade was approved but the engine reported a failure. The chain was rolled back (nothing half-deleted).</p>`
									+ `<pre style="font-size:11px;max-height:300px;overflow:auto;">${frappe.utils.escape_html(JSON.stringify(r.message.steps || r.message, null, 2))}</pre>`,
								indicator: "red",
							});
						}
					},
					error(xhr) {
						// Server threw — surface it AND refresh so the page reflects true state.
						_render_pending_list();
						_render_recent_list();
						frappe.msgprint({
							title: __("Approval Failed"),
							message: __("The approval request errored on the server. The page has been refreshed — check the row's current status before retrying. Do NOT approve repeatedly."),
							indicator: "red",
						});
					},
				});
			},
		);
	}
}

// Single timer that re-renders the Recent list the instant the earliest
// 5-min revert window expires — so the Revert button flips to "expired"
// without a page refresh. Cleared + rescheduled on every render.
let _cd_revert_expiry_timer = null;

function _render_recent_list() {
	frappe.db.get_list("TS Cascade Delete Log", {
		fields: ["name", "target_token", "initiated_by", "approval_status", "executed_at", "revert_window_expires_at"],
		filters: { docstatus: 1 },
		order_by: "creation desc",
		limit: 25,
	}).then(rows => {
		const el = document.getElementById("cd-recent-list");
		if (!rows || !rows.length) {
			el.innerHTML = _cd_status_legend() + `<div class="cd-empty">No history yet.</div>`;
			return;
		}
		const _roles = new Set(frappe.user_roles || []);
		const can_revert = _roles.has("Super Admin") || _roles.has("CEO") || _roles.has("MD");
		let html = _cd_status_legend();
		html += `<table class="cd-list"><tr><th>Log</th><th>Token</th><th>Status</th><th>Executed</th><th>Revert Until</th><th>Action</th></tr>`;
		for (const r of rows) {
			const status_class = "cd-status-" + (r.approval_status || "").split(" ")[0];
			html += `<tr>`;
			html += `<td><a href="/app/ts-cascade-delete-log/${frappe.utils.escape_html(r.name)}">${frappe.utils.escape_html(r.name)}</a></td>`;
			html += `<td><code>${frappe.utils.escape_html(r.target_token)}</code></td>`;
			html += `<td><span class="cd-status-pill ${status_class}">${frappe.utils.escape_html(r.approval_status || "")}</span></td>`;
			html += `<td>${_cd_fmt_dt(r.executed_at)}</td>`;
			html += `<td style="font-weight:600;font-style:italic;">${_cd_fmt_dt(r.revert_window_expires_at)}</td>`;
			html += `<td>${_cd_revert_action_cell(r, can_revert)}</td>`;
			html += `</tr>`;
		}
		html += `</table>`;
		el.innerHTML = html;

		// Wire Revert buttons (only the enabled ones carry data-revert)
		el.querySelectorAll("button[data-revert]").forEach(btn => {
			btn.addEventListener("click", () => _cd_do_revert(btn.dataset.revert));
		});

		// Auto-flip Revert buttons to "expired" the moment the 5-min window
		// passes — schedule one re-render at the earliest still-future deadline.
		if (_cd_revert_expiry_timer) { clearTimeout(_cd_revert_expiry_timer); _cd_revert_expiry_timer = null; }
		let earliest = null;
		for (const r of rows) {
			if (r.approval_status !== "Executed" || !r.revert_window_expires_at) continue;
			let t = null;
			try {
				const obj = frappe.datetime.str_to_obj(r.revert_window_expires_at);
				t = obj ? obj.getTime() : null;
			} catch (e) { t = null; }
			if (t && t > Date.now() && (earliest === null || t < earliest)) earliest = t;
		}
		if (earliest !== null) {
			// +1s buffer so the deadline has definitely passed at re-render.
			const delay = Math.min(Math.max(earliest - Date.now() + 1000, 1000), 2147483647);
			_cd_revert_expiry_timer = setTimeout(_render_recent_list, delay);
		}
	});
}

// Build the Action-cell content for a Recent row.
// Revert button shows ENABLED only when: status=Executed AND within the 5-min
// grace window AND the viewer holds Super Admin / CEO / MD. After the window
// expires the button renders DISABLED with an "expired" label (server re-checks).
function _cd_revert_action_cell(r, can_revert) {
	if (r.approval_status !== "Executed" || !r.revert_window_expires_at) {
		return `<span style="color:#cbd5e1;">—</span>`;
	}
	let within = false;
	try {
		const expires = frappe.datetime.str_to_obj(r.revert_window_expires_at);
		within = expires && expires.getTime() > Date.now();
	} catch (e) { within = false; }

	if (!within) {
		return `<button class="cd-btn cd-btn-secondary" disabled title="Revert window (5 min) has expired">⏪ Revert (expired)</button>`;
	}
	if (!can_revert) {
		return `<span style="font-size:11px;color:#94a3b8;">Revert: CEO / MD / Super Admin</span>`;
	}
	return `<button class="cd-btn cd-btn-warning" data-revert="${frappe.utils.escape_html(r.name)}">⏪ Revert</button>`;
}

function _cd_do_revert(log_name) {
	frappe.confirm(
		__("⚠️ Restore the cascade-deleted chain for <strong>{0}</strong>? Backup SHA-256 is verified before restore.<br><br><span style='color:#b91c1c;'>Note (M2): revert restores the <b>documents</b> only — it does NOT recreate Stock Ledger or GL entries. If this chain included a Purchase Receipt or Invoice, the reverted documents will be out of sync with the ledger and need accounting review.</span>", [frappe.utils.escape_html(log_name)]),
		() => {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.cascade_delete_api.revert_cascade",
				args: { log_name },
				freeze: true,
				freeze_message: __("Restoring cascade chain…"),
				callback(r) {
					if (r.message && r.message.success) {
						frappe.show_alert({ message: __("Revert complete."), indicator: "green" });
					} else {
						frappe.msgprint({
							title: __("Revert Failed"),
							message: (r.message && r.message.error) || __("Unknown error — see Error Log."),
							indicator: "red",
						});
					}
					_render_recent_list();
				},
			});
		}
	);
}
