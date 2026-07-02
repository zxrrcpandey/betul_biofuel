// Production Cascade Delete custom page.
// Multi-stage confirm flow + initiator dashboard + CEO/MD approval queue + recent log.
// ALWAYS full chain + full physical delete (no partial-cut). Manufacturing Manager
// initiates; CEO or MD approves (2-person).
// Lesson 174 — version pill rendered into DOM by JS; bump on every page edit.
// Frappe v15 does NOT auto-inject {page}.html — JS builds the DOM via page.main.html().

const PCD_PAGE_VERSION = "v1.0.0-2026-06-26";

const PCD_CSS = `
	#pcd-root { padding: 18px 22px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
	#pcd-root .pcd-h { font-size: 18px; font-weight: 600; margin-bottom: 6px; }
	#pcd-root .pcd-sub { font-size: 12px; color: #64748b; margin-bottom: 14px; }
	#pcd-root .pcd-version-pill { display: inline-block; padding: 1px 8px; background: #e0e7ff; color: #3730a3; border-radius: 10px; font-size: 10px; margin-left: 6px; vertical-align: middle; }
	#pcd-root .pcd-kill-banner { padding: 10px 14px; border-radius: 6px; margin-bottom: 14px; font-size: 13px; }
	#pcd-root .pcd-kill-on { background: #fef3c7; color: #92400e; border-left: 4px solid #f59e0b; }
	#pcd-root .pcd-kill-off { background: #fee2e2; color: #991b1b; border-left: 4px solid #dc2626; }
	#pcd-root .pcd-search-row { display: flex; gap: 8px; align-items: center; margin-bottom: 14px; }
	#pcd-root .pcd-search-row input { flex: 1; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 13px; font-family: monospace; }
	#pcd-root .pcd-btn { padding: 6px 14px; border-radius: 4px; border: none; font-size: 13px; cursor: pointer; }
	#pcd-root .pcd-btn-primary { background: #2563eb; color: #fff; }
	#pcd-root .pcd-btn-danger { background: #dc2626; color: #fff; }
	#pcd-root .pcd-btn-warning { background: #f59e0b; color: #fff; }
	#pcd-root .pcd-btn-secondary { background: #e2e8f0; color: #0f172a; }
	#pcd-root .pcd-btn:disabled { opacity: 0.5; cursor: not-allowed; }
	#pcd-root .pcd-section { margin-top: 24px; padding: 14px; background: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0; }
	#pcd-root .pcd-section h3 { margin: 0 0 10px 0; font-size: 14px; color: #0f172a; }
	#pcd-root .pcd-chain-table { width: 100%; border-collapse: collapse; font-size: 12px; }
	#pcd-root .pcd-chain-table th, #pcd-root .pcd-chain-table td { border: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; }
	#pcd-root .pcd-chain-table th { background: #f1f5f9; font-weight: 600; }
	#pcd-root .pcd-row-delete td { background: #fef2f2; }
	#pcd-root .pcd-list { width: 100%; border-collapse: collapse; font-size: 12px; }
	#pcd-root .pcd-list th, #pcd-root .pcd-list td { border: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; }
	#pcd-root .pcd-list th { background: #f1f5f9; }
	#pcd-root .pcd-status-pill { padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
	#pcd-root .pcd-status-Pending { background: #fef3c7; color: #92400e; }
	#pcd-root .pcd-status-Approved { background: #dbeafe; color: #1e40af; }
	#pcd-root .pcd-status-Executed { background: #d1fae5; color: #065f46; }
	#pcd-root .pcd-status-Rejected, #pcd-root .pcd-status-Cancelled { background: #e2e8f0; color: #475569; }
	#pcd-root .pcd-status-Reverted { background: #ede9fe; color: #5b21b6; }
	#pcd-root .pcd-status-Failed { background: #fee2e2; color: #991b1b; }
	#pcd-root .pcd-modal-bg { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(15,23,42,0.7); display: flex; align-items: center; justify-content: center; z-index: 9999; }
	#pcd-root .pcd-modal { background: #fff; border-radius: 8px; padding: 22px; max-width: 560px; width: 90%; }
	#pcd-root .pcd-modal h2 { font-size: 16px; margin: 0 0 12px 0; color: #b91c1c; }
	#pcd-root .pcd-modal label { display: block; font-size: 12px; margin-top: 10px; color: #475569; }
	#pcd-root .pcd-modal input { width: 100%; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 4px; font-family: monospace; box-sizing: border-box; }
	#pcd-root .pcd-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
	#pcd-root .pcd-empty { color: #94a3b8; font-style: italic; padding: 8px 0; font-size: 12px; }
	#pcd-root .pcd-legend { margin: 0 0 14px 0; padding: 10px 12px; background: #fff; border: 1px solid #e2e8f0; border-radius: 6px; }
	#pcd-root .pcd-legend-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; color: #64748b; margin-bottom: 6px; }
	#pcd-root .pcd-legend-items { display: grid; grid-template-columns: 1fr 1fr; gap: 3px 28px; }
	#pcd-root .pcd-legend-row { display: flex; align-items: center; gap: 8px; padding: 2px 0; }
	#pcd-root .pcd-legend-row .pcd-status-pill { flex: 0 0 145px; text-align: center; }
	#pcd-root .pcd-legend-text { font-size: 11px; color: #475569; }
	@media (max-width: 900px) { #pcd-root .pcd-legend-items { grid-template-columns: 1fr; } }
	#pcd-root .pcd-expand-toggle { text-align: center; cursor: pointer; vertical-align: middle; }
	#pcd-root .pcd-expand-chip { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border-radius: 6px; background: #e0e7ff; color: #2563eb; font-size: 16px; font-weight: 700; line-height: 1; transition: background 0.12s; }
	#pcd-root .pcd-expand-toggle:hover .pcd-expand-chip { background: #c7d2fe; }
	#pcd-root .pcd-expand-chip.pcd-expand-open { background: #2563eb; color: #fff; }
	[data-theme="dark"] #pcd-root .pcd-expand-chip { background: #334155; color: #93c5fd; }
	[data-theme="dark"] #pcd-root .pcd-expand-chip.pcd-expand-open { background: #2563eb; color: #fff; }
	#pcd-root .pcd-detail-row td { background: #f1f5f9; padding: 0 !important; }
	#pcd-root .pcd-detail-panel { padding: 12px 16px; }
	#pcd-root .pcd-detail-loading { padding: 12px 16px; color: #94a3b8; font-size: 12px; font-style: italic; }
	#pcd-root .pcd-detail-line { font-size: 12px; color: #334155; padding: 3px 0; }
	#pcd-root .pcd-detail-items { width: 100%; border-collapse: collapse; font-size: 12px; margin: 6px 0; }
	#pcd-root .pcd-detail-items th, #pcd-root .pcd-detail-items td { border: 1px solid #cbd5e1; padding: 4px 8px; text-align: left; }
	#pcd-root .pcd-detail-items th { background: #e2e8f0; }
	#pcd-root .pcd-detail-warn { margin-top: 6px; padding: 5px 10px; background: #fef2f2; border-left: 3px solid #dc2626; color: #991b1b; font-size: 12px; }
	#pcd-root .pcd-haz-panel { margin-top: 10px; border: 2px solid #dc2626; background: #fef2f2; border-radius: 6px; padding: 10px 12px; font-size: 12px; }
	#pcd-root .pcd-haz-panel .pcd-haz-title { color: #b91c1c; font-weight: 700; }
	#pcd-root .pcd-haz-panel .pcd-haz-body { margin-top: 4px; color: #7f1d1d; line-height: 1.7; }
	#pcd-root .pcd-haz-panel .pcd-haz-foot { margin-top: 5px; font-style: italic; color: #991b1b; }
	#pcd-root .pcd-haz-ok { margin-top: 10px; padding: 8px 12px; background: #ecfdf5; border-left: 4px solid #10b981; border-radius: 4px; font-size: 12px; color: #065f46; }
	#pcd-root .pcd-warn-note { font-size: 11px; background: #fef3c7; color: #92400e; border-left: 3px solid #f59e0b; padding: 5px 9px; margin: 0 0 8px; }
	#pcd-root .pcd-revert-warn { font-size: 12px; background: #fef2f2; color: #7f1d1d; border-left: 4px solid #dc2626; padding: 8px 11px; margin: 8px 0; line-height: 1.6; }
	#pcd-root code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; padding: 0 3px; background: rgba(15,23,42,0.06); border-radius: 3px; }
	[data-theme="dark"] #pcd-root code { background: rgba(255,255,255,0.08); color: inherit; }
	[data-theme="dark"] #pcd-root .pcd-row-delete td { background: #3f1d1d; }
	[data-theme="dark"] #pcd-root .pcd-detail-row td { background: #0f172a; }
	[data-theme="dark"] #pcd-root .pcd-detail-line { color: #cbd5e1; }
	[data-theme="dark"] #pcd-root .pcd-detail-items th { background: #1e293b; }
	[data-theme="dark"] #pcd-root .pcd-detail-items td { background: #0f172a; color: #e2e8f0; border-color: #334155; }
	[data-theme="dark"] #pcd-root .pcd-legend { background: #1e293b; border-color: #334155; }
	[data-theme="dark"] #pcd-root .pcd-legend-text { color: #cbd5e1; }
	[data-theme="dark"] #pcd-root .pcd-section { background: #1e293b; border-color: #334155; }
	[data-theme="dark"] #pcd-root .pcd-section h3 { color: #e2e8f0; }
	[data-theme="dark"] #pcd-root .pcd-sub { color: #94a3b8; }
	[data-theme="dark"] #pcd-root .pcd-chain-table th, [data-theme="dark"] #pcd-root .pcd-list th { background: #1e293b; color: #e2e8f0; }
	[data-theme="dark"] #pcd-root .pcd-chain-table td, [data-theme="dark"] #pcd-root .pcd-list td { background: #0f172a; color: #e2e8f0; border-color: #334155; }
	[data-theme="dark"] #pcd-root .pcd-modal { background: #1e293b; color: #e2e8f0; }
	[data-theme="dark"] #pcd-root .pcd-modal label { color: #94a3b8; }
	[data-theme="dark"] #pcd-root .pcd-modal input, [data-theme="dark"] #pcd-root .pcd-search-row input { background: #0f172a; color: #e2e8f0; border-color: #475569; }
	[data-theme="dark"] #pcd-root .pcd-btn-secondary { background: #334155; color: #e2e8f0; }
	[data-theme="dark"] #pcd-root .pcd-haz-panel { background: #3f1d1d; border-color: #dc2626; }
	[data-theme="dark"] #pcd-root .pcd-haz-panel .pcd-haz-title { color: #fca5a5; }
	[data-theme="dark"] #pcd-root .pcd-haz-panel .pcd-haz-body { color: #fecaca; }
	[data-theme="dark"] #pcd-root .pcd-haz-panel .pcd-haz-foot { color: #fca5a5; }
	[data-theme="dark"] #pcd-root .pcd-haz-ok { background: #052e23; color: #6ee7b7; border-color: #10b981; }
	[data-theme="dark"] #pcd-root .pcd-detail-warn { background: #3f1d1d; color: #fecaca; border-color: #dc2626; }
	[data-theme="dark"] #pcd-root .pcd-warn-note { background: #3a2e12; color: #fcd34d; border-color: #a16207; }
	[data-theme="dark"] #pcd-root .pcd-revert-warn { background: #3f1d1d; color: #fecaca; border-color: #dc2626; }
	#pcd-root .pcd-warn-note, #pcd-root .pcd-revert-warn { overflow-wrap: anywhere; }
`;

const PCD_BODY = `
<div id="pcd-root">
	<div class="pcd-h">Production Cascade Delete <span class="pcd-version-pill" id="pcd-version-pill">v1.0.0</span></div>
	<div class="pcd-sub">Authorized deletion of a whole production run &middot; Manufacturing Manager initiates &middot; CEO or MD approves (2-person) &middot; hash-chained audit &middot; full physical delete with net-zero ledger sweep.</div>
	<div id="pcd-kill-banner"></div>
	<div class="pcd-search-row" id="pcd-search-row" style="display:none;">
		<div id="pcd-prod-link" style="flex:1;"></div>
		<button class="pcd-btn pcd-btn-primary" id="pcd-preview-btn">Preview Chain</button>
	</div>
	<div id="pcd-preview-area"></div>
	<div class="pcd-section" id="pcd-pending-section">
		<h3>Pending CEO / MD Approval</h3>
		<div id="pcd-pending-list"></div>
	</div>
	<div class="pcd-section" id="pcd-recent-section">
		<h3>Recent Production Cascade Deletions</h3>
		<div id="pcd-recent-list"></div>
	</div>
</div>
`;

let _pcd_prod_link = null;  // searchable Link control on TS Production Entry

frappe.pages["production-cascade-delete"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Production Cascade Delete (BBPL)"),
		single_column: true,
	});

	if (!document.getElementById("pcd-styles")) {
		const style = document.createElement("style");
		style.id = "pcd-styles";
		style.textContent = PCD_CSS;
		document.head.appendChild(style);
	}

	page.main.html(PCD_BODY);
	setTimeout(() => _pcd_init(), 100);
};

frappe.pages["production-cascade-delete"].refresh = function () {
	_pcd_render_pending_list();
	_pcd_render_recent_list();
};

function _pcd_status_legend() {
	const items = [
		["Pending", "Pending CEO Approval", "Manufacturing Manager initiated — waiting for a CEO or MD to approve or reject."],
		["Approved", "Approved", "Approved — engine runs immediately (transient state)."],
		["Executed", "Executed", "Cascade completed — chain cancelled + physically deleted, ledger swept to zero."],
		["Failed", "Failed", "A pre-flight or step failed — see the execution result. A pre-flight failure deletes nothing."],
		["Reverted", "Reverted", "Was Executed, then restored from backup within the revert window (documents only)."],
		["Rejected", "Rejected", "CEO/MD declined — nothing was deleted."],
		["Cancelled", "Cancelled", "Cancelled before execution — nothing was deleted."],
	];
	let html = `<div class="pcd-legend"><div class="pcd-legend-title">Status guide</div><div class="pcd-legend-items">`;
	for (const [cls, label, meaning] of items) {
		html += `<div class="pcd-legend-row">`
			+ `<span class="pcd-status-pill pcd-status-${cls}">${frappe.utils.escape_html(label)}</span>`
			+ `<span class="pcd-legend-text">${frappe.utils.escape_html(meaning)}</span>`
			+ `</div>`;
	}
	html += `</div></div>`;
	return html;
}

function _pcd_fmt_dt(val) {
	if (!val) return "-";
	try { return frappe.utils.escape_html(frappe.datetime.str_to_user(val)); }
	catch (e) { return frappe.utils.escape_html(String(val).split(".")[0]); }
}

function _pcd_init() {
	const pill = document.getElementById("pcd-version-pill");
	if (pill) pill.textContent = PCD_PAGE_VERSION;
	_pcd_render_kill_banner();
	_pcd_render_pending_list();
	_pcd_render_recent_list();
	// Only Manufacturing Manager can initiate (server is the authoritative gate).
	const user_roles = new Set(frappe.user_roles || []);
	const can_initiate = user_roles.has("Manufacturing Manager");
	if (can_initiate) {
		const search_row = document.getElementById("pcd-search-row");
		if (search_row) search_row.style.display = "flex";
		// Searchable Link picker on TS Production Entry (type-ahead) instead of blind free-text.
		const link_parent = document.getElementById("pcd-prod-link");
		if (link_parent && !_pcd_prod_link) {
			_pcd_prod_link = frappe.ui.form.make_control({
				df: {
					fieldtype: "Link",
					options: "TS Production Entry",
					placeholder: __("Search a production to delete (PROD-…)"),
				},
				parent: link_parent,
				render_input: true,
				only_input: true,
			});
			_pcd_prod_link.refresh();
		}
		const preview_btn = document.getElementById("pcd-preview-btn");
		if (preview_btn) preview_btn.addEventListener("click", _pcd_on_preview_click);
	}
}

function _pcd_render_kill_banner() {
	frappe.call({
		method: "frappe.client.get_value",
		args: { doctype: "TS Settings", fieldname: "ts_prod_cascade_enabled", filters: {} },
		callback(r) {
			const enabled = r && r.message && r.message.ts_prod_cascade_enabled;
			const banner = document.getElementById("pcd-kill-banner");
			if (!banner) return;
			if (enabled) {
				banner.innerHTML = `<div class="pcd-kill-banner pcd-kill-on">⚠ Production Cascade Delete is <strong>ENABLED</strong>. Every action is logged + signed + chained.</div>`;
			} else {
				banner.innerHTML = `<div class="pcd-kill-banner pcd-kill-off">🔒 Production Cascade Delete is <strong>DISABLED</strong>. System Manager must flip <code>ts_prod_cascade_enabled</code> in TS Settings before any operation can proceed.</div>`;
			}
		},
	});
}

function _pcd_on_preview_click() {
	const production_name = (_pcd_prod_link ? (_pcd_prod_link.get_value() || "") : "").trim();
	if (!production_name) {
		frappe.msgprint({ title: __("Select a production"), message: __("Search and select a production (PROD-…) to preview."), indicator: "orange" });
		return;
	}
	if (!/^PROD-[0-9]{4}-[0-9]{5,}$/.test(production_name)) {
		frappe.msgprint({ title: __("Invalid"), message: __("Production Entry name must match PROD-YYYY-NNNNN."), indicator: "red" });
		return;
	}
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.production_cascade_api.preview_production_cascade",
		args: { production_name },
		callback(r) {
			if (!r.message) return;
			_pcd_render_preview(r.message, production_name);
		},
	});
}

// The 7-step deletion order (leaf -> root). Always all-deleted (no partial cut).
const _PCD_CHAIN = [
	{ code: "QI",          label: "Quality Inspections" },
	{ code: "SURPLUS",     label: "Surplus-Return Stock Entry" },
	{ code: "MANUFACTURE", label: "Manufacture Stock Entry" },
	{ code: "RELEASE",     label: "Release Stock Entry" },
	{ code: "JOBCARD",     label: "Job Cards" },
	{ code: "WORKORDER",   label: "Work Order" },
	{ code: "PE",          label: "TS Production Entry" },
];

function _pcd_render_preview(chain, production_name) {
	const area = document.getElementById("pcd-preview-area");
	const esc = frappe.utils.escape_html;
	if (!chain.production_record) {
		area.innerHTML = `<div class="pcd-section"><h3>Preview</h3><div class="pcd-empty">Production Entry <code>${esc(production_name)}</code> not found.</div></div>`;
		return;
	}
	const pe = chain.production_record;
	const qiNames = (chain.quality_inspections || []).map(q => q.name);
	const seNames = chain.se_names || [];
	const jcNames = (chain.job_cards || []).map(j => j.name);
	const count = {
		QI: qiNames.length,
		SURPLUS: chain.surplus_se ? 1 : 0,
		MANUFACTURE: chain.manufacture_se ? 1 : 0,
		RELEASE: chain.release_se ? 1 : 0,
		JOBCARD: jcNames.length,
		WORKORDER: chain.work_order ? 1 : 0,
		PE: 1,
	};
	const detail = {
		QI: qiNames.join(", ") || "—",
		SURPLUS: chain.surplus_se || "—",
		MANUFACTURE: chain.manufacture_se || "—",
		RELEASE: chain.release_se || "—",
		JOBCARD: jcNames.join(", ") || "—",
		WORKORDER: (chain.work_order || "—") + (chain.work_order_status ? ` (${esc(chain.work_order_status)})` : ""),
		PE: production_name + ` (${esc(pe.ts_variance_status || "-")})`,
	};

	let html = `<div class="pcd-section"><h3>Chain Preview for <code>${esc(production_name)}</code></h3>`;
	html += `<p style="font-size:12px;color:#64748b;margin:0 0 8px;">The ENTIRE chain below is cancelled then physically deleted (leaf &rarr; root), and the residual ledger is swept to zero. There is no partial option.</p>`;
	html += `<table class="pcd-chain-table"><tr><th style="width:34px">#</th><th>Step</th><th>Count</th><th>Documents</th></tr>`;
	_PCD_CHAIN.forEach((s, i) => {
		html += `<tr class="pcd-row-delete"><td>${i + 1}</td><td>${esc(s.label)}</td>`
			+ `<td>${count[s.code]}</td><td><code>${esc(String(detail[s.code]))}</code></td></tr>`;
	});
	html += `</table>`;
	html += `<p style="margin-top:8px;font-size:12px;color:#64748b;">Stock Ledger Entries: ${chain.stock_ledger_entries_count || 0} | GL Entries: ${chain.gl_entries_count || 0} | Submitted SEs: ${chain.submitted_se_count || 0}</p>`;

	// HAZARD-C blast-radius panel.
	const haz = chain.hazard_c || {};
	if (haz.blocked) {
		const findings = (haz.findings || []).map(f =>
			`${esc(f.voucher_type)} <code>${esc(f.voucher_no)}</code> (${esc(f.item_code)} @ ${esc(f.warehouse)}, out ${esc(String(f.qty_out))})`
		).join("<br>");
		html += `<div class="pcd-haz-panel">`
			+ `<span class="pcd-haz-title">⚠ HAZARD-C — produced finished-goods / by-products were already dispatched / consumed downstream</span>`
			+ `<div class="pcd-haz-body">${findings || "—"}</div>`
			+ `<div class="pcd-haz-foot">Cancelling the Manufacture SE will CORRUPT that downstream stock. Reverse the document(s) above first, or this cascade will require the <code>FORCE-DELETE-DISPATCHED</code> confirmation.</div>`
			+ `</div>`;
	} else {
		html += `<div class="pcd-haz-ok">✓ HAZARD-C clear — no downstream consumer of the produced FG / by-products detected.</div>`;
	}
	// Inline explainer — what the HAZARD-C check means (shown in both clear + blocked states).
	html += `<details class="pcd-haz-help" style="margin:4px 0 12px;font-size:12px;color:var(--text-muted,#475569);">`
		+ `<summary style="cursor:pointer;color:#2563eb;font-weight:600;list-style:revert;">ⓘ What is HAZARD-C?</summary>`
		+ `<div style="margin-top:6px;line-height:1.6;">`
		+ `<b>HAZARD-C</b> checks whether the finished goods / by-products this production created have <b>already been used downstream</b> — shipped on a Delivery Note, sold (Sales Invoice with stock update), or consumed by another Stock Entry.<br><br>`
		+ `<b>Why it matters:</b> deleting a production <b>reverses the stock it produced</b>. If those goods have already left, reversing them would drive stock negative and corrupt your stock valuation — and it cannot be undone.<br><br>`
		+ `<b>"Clear"</b> means nothing downstream has consumed them, so this production can be safely deleted. If it is <b>not</b> clear, the tool <b>blocks</b> the delete and lists the exact documents that already consumed the goods — reverse those first (or, only if genuinely intended, type <code>FORCE-DELETE-DISPATCHED</code> to override).`
		+ `</div></details>`;

	html += `<div style="margin-top:14px;"><button class="pcd-btn pcd-btn-danger" id="pcd-initiate-btn">Initiate Cascade Deletion →</button></div>`;
	html += `</div>`;
	area.innerHTML = html;

	document.getElementById("pcd-initiate-btn").addEventListener("click", () => {
		const needs_force_manufacture = (chain.submitted_se_count || 0) > 0;
		const needs_force_dispatched = !!haz.blocked;
		_pcd_show_stage1(production_name, needs_force_manufacture, needs_force_dispatched);
	});
}

function _pcd_modal(html) {
	const div = document.createElement("div");
	div.innerHTML = html;
	(document.getElementById("pcd-root") || document.body).appendChild(div);
	return div;
}

function _pcd_show_stage1(production_name, need_mfg, need_disp) {
	const div = _pcd_modal(`
		<div class="pcd-modal-bg"><div class="pcd-modal">
		<h2>Stage 1 — Confirm Production Entry Name</h2>
		<p style="font-size:12px;color:#475569;">Type the Production Entry name <strong>EXACTLY</strong> to continue.</p>
		<label for="pcd-st1">Production Entry name (case-sensitive):</label>
		<input type="text" id="pcd-st1" maxlength="24" autofocus />
		<div class="pcd-modal-actions">
			<button class="pcd-btn pcd-btn-secondary" id="pcd-st1-cancel">Cancel</button>
			<button class="pcd-btn pcd-btn-danger" id="pcd-st1-continue" disabled>Continue →</button>
		</div></div></div>`);
	const input = div.querySelector("#pcd-st1");
	const btn = div.querySelector("#pcd-st1-continue");
	input.addEventListener("input", () => { btn.disabled = input.value !== production_name; });
	div.querySelector("#pcd-st1-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => {
		div.remove();
		if (need_mfg) _pcd_show_stage_mfg(production_name, need_disp);
		else if (need_disp) _pcd_show_stage_disp(production_name, false);
		else _pcd_show_final(production_name, false, false);
	});
}

function _pcd_show_stage_mfg(production_name, need_disp) {
	const div = _pcd_modal(`
		<div class="pcd-modal-bg"><div class="pcd-modal">
		<h2>Stage 2 — Force Delete Manufacture / Stock Entries</h2>
		<p style="font-size:12px;color:#475569;">This run has <strong>submitted Stock Entries</strong> (Manufacture / Release / surplus). Type <code>FORCE-DELETE-MANUFACTURE</code> to allow cancelling + deleting them.</p>
		<label for="pcd-mfg">Type FORCE-DELETE-MANUFACTURE:</label>
		<input type="text" id="pcd-mfg" autofocus />
		<div class="pcd-modal-actions">
			<button class="pcd-btn pcd-btn-secondary" id="pcd-mfg-cancel">Cancel</button>
			<button class="pcd-btn pcd-btn-danger" id="pcd-mfg-continue" disabled>Continue →</button>
		</div></div></div>`);
	const input = div.querySelector("#pcd-mfg");
	const btn = div.querySelector("#pcd-mfg-continue");
	input.addEventListener("input", () => { btn.disabled = input.value !== "FORCE-DELETE-MANUFACTURE"; });
	div.querySelector("#pcd-mfg-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => {
		div.remove();
		if (need_disp) _pcd_show_stage_disp(production_name, true);
		else _pcd_show_final(production_name, true, false);
	});
}

function _pcd_show_stage_disp(production_name, force_mfg) {
	const div = _pcd_modal(`
		<div class="pcd-modal-bg"><div class="pcd-modal">
		<h2>Stage 2 — Force Delete With Dispatched FG (HAZARD-C)</h2>
		<p style="font-size:12px;color:#475569;">The produced finished-goods / by-products were <strong>already dispatched / consumed downstream</strong> (Delivery Note / Stock Entry / Sales Invoice). Deleting this run will <strong>corrupt that downstream stock</strong>. Type <code>FORCE-DELETE-DISPATCHED</code> only if this is genuinely intended.</p>
		<label for="pcd-disp">Type FORCE-DELETE-DISPATCHED:</label>
		<input type="text" id="pcd-disp" autofocus />
		<div class="pcd-modal-actions">
			<button class="pcd-btn pcd-btn-secondary" id="pcd-disp-cancel">Cancel</button>
			<button class="pcd-btn pcd-btn-danger" id="pcd-disp-continue" disabled>Continue →</button>
		</div></div></div>`);
	const input = div.querySelector("#pcd-disp");
	const btn = div.querySelector("#pcd-disp-continue");
	input.addEventListener("input", () => { btn.disabled = input.value !== "FORCE-DELETE-DISPATCHED"; });
	div.querySelector("#pcd-disp-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => { div.remove(); _pcd_show_final(production_name, force_mfg, true); });
}

function _pcd_show_final(production_name, force_mfg, force_disp) {
	const esc = frappe.utils.escape_html;
	const div = _pcd_modal(`
		<div class="pcd-modal-bg"><div class="pcd-modal">
		<h2>Stage 3 — FINAL CONFIRMATION</h2>
		<p style="font-size:12px;color:#475569;">You are about to <strong>queue a FULL cascade delete</strong> for <code>${esc(production_name)}</code>. A CEO or MD must approve before execution. The whole chain will be cancelled, physically deleted, and the ledger swept to zero. Type the Production Entry name ONE MORE TIME to submit.</p>
		<p class="pcd-warn-note">Scope: <span style="color:#dc2626;font-weight:600;">FULL CHAIN + PHYSICAL DELETE</span> — Quality Inspections, surplus/Manufacture/Release Stock Entries, Job Cards, Work Order, and the Production Entry are all removed.</p>
		<label for="pcd-final">Type Production Entry name to submit:</label>
		<input type="text" id="pcd-final" maxlength="24" autofocus />
		<div class="pcd-modal-actions">
			<button class="pcd-btn pcd-btn-secondary" id="pcd-final-cancel">Cancel</button>
			<button class="pcd-btn pcd-btn-danger" id="pcd-final-submit" disabled>Submit for Approval →</button>
		</div></div></div>`);
	const input = div.querySelector("#pcd-final");
	const btn = div.querySelector("#pcd-final-submit");
	input.addEventListener("input", () => { btn.disabled = input.value !== production_name; });
	div.querySelector("#pcd-final-cancel").addEventListener("click", () => div.remove());
	btn.addEventListener("click", () => {
		btn.disabled = true;
		btn.textContent = "Submitting…";
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.production_cascade_api.initiate_production_cascade",
			args: {
				production_name: production_name,
				force_manufacture: force_mfg ? 1 : 0,
				force_dispatched: force_disp ? 1 : 0,
				confirm_production_name_typed: production_name,
				force_manufacture_confirmation_typed: force_mfg ? "FORCE-DELETE-MANUFACTURE" : "",
				force_dispatched_confirmation_typed: force_disp ? "FORCE-DELETE-DISPATCHED" : "",
				client_screen: `${screen.width}x${screen.height}`,
				client_language: navigator.language,
			},
			callback(r) {
				div.remove();
				if (r.message && r.message.success) {
					frappe.show_alert({ message: __("Cascade queued. Log: ") + r.message.log_name, indicator: "green" });
					const area = document.getElementById("pcd-preview-area");
					if (area) area.innerHTML = "";
					if (_pcd_prod_link) _pcd_prod_link.set_value("");
					_pcd_render_pending_list();
					_pcd_render_recent_list();
				}
			},
			error(err) {
				div.remove();
				frappe.msgprint({ title: __("Submission Failed"), message: (err && err.message) || __("Unknown error."), indicator: "red" });
			},
		});
	});
}

function _pcd_render_pending_list() {
	frappe.db.get_list("TS Production Cascade Log", {
		fields: ["name", "target_production", "initiated_by", "initiated_at", "approval_status"],
		filters: { approval_status: "Pending CEO Approval", docstatus: 1 },
		order_by: "initiated_at desc",
		limit: 25,
	}).then(rows => {
		const el = document.getElementById("pcd-pending-list");
		if (!rows || !rows.length) { el.innerHTML = `<div class="pcd-empty">No pending requests.</div>`; return; }
		const user_roles = new Set(frappe.user_roles || []);
		const can_approve = user_roles.has("CEO") || user_roles.has("MD");
		let html = `<table class="pcd-list"><tr><th style="width:40px"></th><th>Log</th><th>Production</th><th>Initiated By</th><th>At</th><th>Actions</th></tr>`;
		for (const r of rows) {
			const sn = frappe.utils.escape_html(r.name);
			html += `<tr>`;
			html += `<td class="pcd-expand-toggle" data-toggle="${sn}" title="Show chain details"><span class="pcd-expand-chip">▸</span></td>`;
			html += `<td><a href="/app/ts-production-cascade-log/${sn}">${sn}</a></td>`;
			html += `<td><code>${frappe.utils.escape_html(r.target_production)}</code></td>`;
			html += `<td>${frappe.utils.escape_html(r.initiated_by)}</td>`;
			html += `<td>${_pcd_fmt_dt(r.initiated_at)}</td>`;
			html += `<td>`;
			if (can_approve && r.initiated_by !== frappe.session.user) {
				html += `<button class="pcd-btn pcd-btn-primary" data-action="approve" data-log="${sn}">Approve</button>`;
				html += ` <button class="pcd-btn pcd-btn-secondary" data-action="reject" data-log="${sn}">Reject</button>`;
			}
			html += `</td></tr>`;
		}
		html += `</table>`;
		el.innerHTML = html;
		el.querySelectorAll("button[data-action]").forEach(btn => {
			btn.addEventListener("click", () => _pcd_decision(btn.dataset.log, btn.dataset.action));
		});
		el.querySelectorAll(".pcd-expand-toggle").forEach(td => {
			td.addEventListener("click", () => _pcd_toggle_detail(td));
		});
	});
}

function _pcd_toggle_detail(td) {
	const log_name = td.dataset.toggle;
	const mainRow = td.closest("tr");
	const next = mainRow.nextElementSibling;
	const chip = td.querySelector(".pcd-expand-chip") || td;
	if (next && next.classList.contains("pcd-detail-row")) {
		next.remove(); chip.textContent = "▸"; chip.classList.remove("pcd-expand-open"); return;
	}
	chip.textContent = "▾"; chip.classList.add("pcd-expand-open");
	const detailRow = document.createElement("tr");
	detailRow.className = "pcd-detail-row";
	detailRow.innerHTML = `<td colspan="6"><div class="pcd-detail-loading">Loading chain detail…</div></td>`;
	mainRow.after(detailRow);
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.production_cascade_api.get_pending_production_review_detail",
		args: { log_name },
		callback(r) {
			const cell = detailRow.querySelector("td");
			cell.innerHTML = r.message ? _pcd_render_review_detail(r.message) : `<div class="pcd-empty">No detail available.</div>`;
		},
		error() {
			detailRow.querySelector("td").innerHTML = `<div class="pcd-empty" style="color:#dc2626;">Failed to load chain detail.</div>`;
		},
	});
}

function _pcd_render_review_detail(d) {
	const c = d.counts || {};
	const esc = frappe.utils.escape_html;
	let h = `<div class="pcd-detail-panel">`;
	h += `<div class="pcd-detail-line"><b>Production:</b> ${esc(d.production_status || "-")}`
		+ ` &middot; <b>Item:</b> ${esc(d.production_item || "-")}`
		+ ` &middot; <b>Produced Qty:</b> ${esc(String(d.produced_qty || 0))}</div>`;
	h += `<div class="pcd-detail-line"><b>Work Order:</b> ${esc(d.work_order || "-")} (${esc(d.work_order_status || "-")})`
		+ ` &middot; <b>Release SE:</b> ${esc(d.release_se || "-")}`
		+ ` &middot; <b>Manufacture SE:</b> ${esc(d.manufacture_se || "-")}`
		+ ` &middot; <b>Surplus SE:</b> ${esc(d.surplus_se || "-")}</div>`;
	h += `<div class="pcd-detail-line"><b>Chain:</b> ${c.job_cards || 0} Job Cards &middot; ${c.stock_entries || 0} SE (`
		+ `${c.submitted_stock_entries || 0} submitted) &middot; ${c.quality_inspections || 0} QI`
		+ ` &middot; ${c.stock_ledger_entries || 0} SLE &middot; ${c.gl_entries || 0} GL</div>`;
	if (d.produced_rows && d.produced_rows.length) {
		h += `<table class="pcd-detail-items"><tr><th>Produced Item</th><th>Warehouse</th><th>Batch</th><th>Qty</th></tr>`;
		for (const it of d.produced_rows) {
			h += `<tr><td>${esc(it.item_code || "-")}</td><td>${esc(it.warehouse || "-")}</td>`
				+ `<td>${esc(it.batch_no || "—")}</td><td>${esc(String(it.qty || 0))}</td></tr>`;
		}
		h += `</table>`;
	}
	const haz = d.hazard_c || {};
	if (haz.blocked) {
		const findings = (haz.findings || []).map(f =>
			`${esc(f.voucher_type)} ${esc(f.voucher_no)} (${esc(f.item_code)}@${esc(f.warehouse)}, ${esc(String(f.qty_out))})`
		).join("; ");
		h += `<div class="pcd-detail-warn">⚠ <b>HAZARD-C</b> — produced FG/by-products already dispatched/consumed: ${findings}. Deleting this run corrupts downstream stock.</div>`;
	}
	if (d.force_manufacture_override) {
		h += `<div class="pcd-detail-warn">⚠ Force-Manufacture override ON — submitted Stock Entries will be cancelled + deleted.</div>`;
	}
	if (d.force_dispatched_override) {
		h += `<div class="pcd-detail-warn">⚠ Force-Dispatched (HAZARD-C) override ON — the run will be deleted despite dispatched FG.</div>`;
	}
	h += `</div>`;
	return h;
}

function _pcd_decision(log_name, action) {
	if (action === "reject") {
		frappe.prompt(
			[{ fieldname: "reason", label: __("Rejection Reason (min 10 chars)"), fieldtype: "Small Text", reqd: 1 }],
			(values) => {
				if (values.reason.length < 10) {
					frappe.msgprint({ message: __("Reason must be at least 10 characters."), indicator: "red" });
					return;
				}
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.production_cascade_api.approve_production_cascade",
					args: { log_name, decision: "reject", reason: values.reason },
					callback() { _pcd_render_pending_list(); _pcd_render_recent_list(); },
				});
			},
			__("Reject Production Cascade"), __("Reject"),
		);
	} else {
		frappe.confirm(
			__("Approve this production cascade? The engine will run IMMEDIATELY — the whole chain is cancelled, physically deleted, and the ledger swept to zero. A short revert window (documents only) follows."),
			() => {
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.production_cascade_api.approve_production_cascade",
					args: { log_name, decision: "approve" },
					freeze: true,
					freeze_message: __("Executing production cascade — this may take a few seconds…"),
					callback(r) {
						_pcd_render_pending_list();
						_pcd_render_recent_list();
						if (r.message && r.message.success) {
							frappe.show_alert({ message: __("✓ Production cascade approved & executed — {0}", [frappe.utils.escape_html(log_name)]), indicator: "green" }, 7);
						} else if (r.message && !r.message.success) {
							frappe.msgprint({
								title: __("Cascade Engine Failed"),
								message: `<p>The cascade was approved but the engine reported a failure (aborted at <code>${frappe.utils.escape_html(r.message.aborted_step || "?")}</code>). A pre-flight failure deletes nothing.</p>`
									+ `<pre style="font-size:11px;max-height:300px;overflow:auto;">${frappe.utils.escape_html(JSON.stringify(r.message.steps || r.message, null, 2))}</pre>`,
								indicator: "red",
							});
						}
					},
					error() {
						_pcd_render_pending_list();
						_pcd_render_recent_list();
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

let _pcd_revert_timer = null;

function _pcd_render_recent_list() {
	frappe.db.get_list("TS Production Cascade Log", {
		fields: ["name", "target_production", "initiated_by", "approval_status", "executed_at", "revert_window_expires_at"],
		filters: { docstatus: 1 },
		order_by: "creation desc",
		limit: 25,
	}).then(rows => {
		const el = document.getElementById("pcd-recent-list");
		if (!rows || !rows.length) { el.innerHTML = _pcd_status_legend() + `<div class="pcd-empty">No history yet.</div>`; return; }
		const _roles = new Set(frappe.user_roles || []);
		const can_revert = _roles.has("Super Admin") || _roles.has("CEO") || _roles.has("MD");
		let html = _pcd_status_legend();
		html += `<table class="pcd-list"><tr><th>Log</th><th>Production</th><th>Status</th><th>Executed</th><th>Revert Until</th><th>Action</th></tr>`;
		for (const r of rows) {
			const status_class = "pcd-status-" + (r.approval_status || "").split(" ")[0];
			html += `<tr>`;
			html += `<td><a href="/app/ts-production-cascade-log/${frappe.utils.escape_html(r.name)}">${frappe.utils.escape_html(r.name)}</a></td>`;
			html += `<td><code>${frappe.utils.escape_html(r.target_production)}</code></td>`;
			html += `<td><span class="pcd-status-pill ${status_class}">${frappe.utils.escape_html(r.approval_status || "")}</span></td>`;
			html += `<td>${_pcd_fmt_dt(r.executed_at)}</td>`;
			html += `<td style="font-weight:600;font-style:italic;">${_pcd_fmt_dt(r.revert_window_expires_at)}</td>`;
			html += `<td>${_pcd_revert_cell(r, can_revert)}</td>`;
			html += `</tr>`;
		}
		html += `</table>`;
		el.innerHTML = html;
		el.querySelectorAll("button[data-revert]").forEach(btn => {
			btn.addEventListener("click", () => _pcd_do_revert(btn.dataset.revert));
		});
		if (_pcd_revert_timer) { clearTimeout(_pcd_revert_timer); _pcd_revert_timer = null; }
		let earliest = null;
		for (const r of rows) {
			if (r.approval_status !== "Executed" || !r.revert_window_expires_at) continue;
			let t = null;
			try { const obj = frappe.datetime.str_to_obj(r.revert_window_expires_at); t = obj ? obj.getTime() : null; } catch (e) { t = null; }
			if (t && t > Date.now() && (earliest === null || t < earliest)) earliest = t;
		}
		if (earliest !== null) {
			const delay = Math.min(Math.max(earliest - Date.now() + 1000, 1000), 2147483647);
			_pcd_revert_timer = setTimeout(_pcd_render_recent_list, delay);
		}
	});
}

function _pcd_revert_cell(r, can_revert) {
	if (r.approval_status !== "Executed" || !r.revert_window_expires_at) {
		return `<span style="color:#cbd5e1;">—</span>`;
	}
	let within = false;
	try { const expires = frappe.datetime.str_to_obj(r.revert_window_expires_at); within = expires && expires.getTime() > Date.now(); } catch (e) { within = false; }
	if (!within) return `<button class="pcd-btn pcd-btn-secondary" disabled title="Revert window has expired">⏪ Revert (expired)</button>`;
	if (!can_revert) return `<span style="font-size:11px;color:#94a3b8;">Revert: CEO / MD / Super Admin</span>`;
	return `<button class="pcd-btn pcd-btn-warning" data-revert="${frappe.utils.escape_html(r.name)}">⏪ Revert</button>`;
}

function _pcd_do_revert(log_name) {
	frappe.confirm(
		__("⚠️ Revert the production cascade for <strong>{0}</strong>? Backup SHA-256 is verified before restore.<br><br><span style='color:#b91c1c;font-weight:600;'>CRITICAL (read carefully): Revert restores the Work Order / Job Card / Stock Entry / Production Entry DOCUMENTS ONLY. It does NOT recreate Stock Ledger or GL entries, does NOT restore WIP/FG/Stores bin balances, and does NOT re-link the Work Order's produced quantity. The restored Manufacture Stock Entry will show submitted but moved ZERO stock — a stock-valuation hole REQUIRING a manual Stock Reconciliation. Prefer re-running production fresh over reverting.</span>", [frappe.utils.escape_html(log_name)]),
		() => {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.production_cascade_api.revert_production_cascade",
				args: { log_name },
				freeze: true,
				freeze_message: __("Restoring production cascade documents…"),
				callback(r) {
					if (r.message && r.message.success) {
						frappe.show_alert({ message: __("Revert complete (documents only — run a Stock Reconciliation)."), indicator: "orange" }, 8);
					} else {
						frappe.msgprint({ title: __("Revert Failed"), message: (r.message && r.message.error) || __("Unknown error — see Error Log."), indicator: "red" });
					}
					_pcd_render_recent_list();
				},
			});
		}
	);
}
