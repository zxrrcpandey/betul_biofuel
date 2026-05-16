/* ═══════════════════════════════════════════════════════════════════
   Accounts Pending Billing Dashboard
   Single section: submitted Purchase Receipts where per_billed < 100.
   Mirrors the Stores Receiving Dashboard structure.
   ═══════════════════════════════════════════════════════════════════ */

const APB_VERSION = "v1.0.0-2026-05-16";
const APB_API = "trustbit_ethanol.ts_gate_entry.accounts_billing_api";
console.log("[accounts-pending-billing]", APB_VERSION, "loaded");

frappe.pages["accounts-pending-billing"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Accounts Pending Billing",
		single_column: true,
	});

	page.main.html(`<div id="apb-container" style="padding:0;">
		<div id="apb-loading" style="text-align:center;padding:60px;color:#9ca3af;font-size:14px;">Loading Accounts Pending Billing…</div>
	</div>`);

	if (!document.getElementById("apb-styles")) {
		const style = document.createElement("style");
		style.id = "apb-styles";
		style.textContent = APB_CSS;
		document.head.appendChild(style);
	}

	page.add_button(__("Refresh"), () => _apb_reload(), { icon: "refresh" });

	setTimeout(() => _apb_init(), 200);
};

frappe.pages["accounts-pending-billing"].refresh = function () {
	_apb_reload();
};

let _apb_state = { rows: [], auditor_readonly: false, busy: false };

async function _apb_init() {
	try {
		_apb_render_shell();
		await _apb_load();
	} catch (e) {
		$("#apb-container").html(`<div class="apb-error">Error: ${frappe.utils.escape_html(e.message || String(e))}</div>`);
		console.error(e);
	}
}

function _apb_reload() {
	if (_apb_state.busy) return;
	_apb_load();
}

function _apb_render_shell() {
	$("#apb-container").html(`
		<div class="apb-header">
			<div class="apb-title">
				<div class="apb-subtitle">Submitted Purchase Receipts awaiting Purchase Invoice <span class="apb-version">${APB_VERSION}</span></div>
			</div>
			<div class="apb-summary">
				<div class="apb-badge apb-b-pending"><div class="apb-badge-num" id="apb-count-pending">—</div><div class="apb-badge-lbl">Pending</div></div>
				<div class="apb-badge apb-b-partial"><div class="apb-badge-num" id="apb-count-partial">—</div><div class="apb-badge-lbl">Partial</div></div>
				<div class="apb-badge apb-b-aged"><div class="apb-badge-num" id="apb-count-aged">—</div><div class="apb-badge-lbl">&gt;30 Days</div></div>
			</div>
		</div>

		<div class="apb-section" id="apb-section-main">
			<div class="apb-section-head">
				<div class="apb-section-title">Pending Purchase Receipts — Awaiting Invoice</div>
				<div class="apb-section-hint">docstatus=1 · per_billed &lt; 100 · oldest posting_date first · max 500 rows</div>
			</div>
			<div id="apb-body" class="apb-body"><div class="apb-empty">Loading…</div></div>
		</div>
	`);
}

async function _apb_load() {
	if (_apb_state.busy) return;
	_apb_state.busy = true;
	try {
		const r = await frappe.call({ method: `${APB_API}.get_dashboard_data` });
		const data = r.message || {};
		_apb_state.rows = data.rows || [];
		_apb_state.auditor_readonly = !!data.auditor_readonly;
		const counts = data.counts || {};
		$("#apb-count-pending").text(counts.pending ?? _apb_state.rows.length);
		$("#apb-count-partial").text(counts.partial ?? 0);
		$("#apb-count-aged").text(counts.aged ?? 0);
		_apb_render_table();
	} catch (e) {
		console.error("[accounts-pending-billing] load error", e);
		frappe.msgprint({
			title: "Error",
			message: frappe.utils.escape_html(e.message || String(e)),
			indicator: "red",
		});
	} finally {
		_apb_state.busy = false;
	}
}

function _apb_render_table() {
	const rows = _apb_state.rows;
	if (!rows.length) {
		$("#apb-body").html('<div class="apb-empty">All Purchase Receipts billed — nothing pending.</div>');
		return;
	}
	const auditor = _apb_state.auditor_readonly;
	const html = [
		'<table class="apb-table"><thead><tr>',
		'<th>PR#</th><th>Posting</th><th>Supplier</th><th>PO</th>',
		'<th class="apb-num">Total</th><th class="apb-num">Billed</th>',
		'<th>% Bill</th><th>Age</th><th>Action</th>',
		'</tr></thead><tbody>',
		...rows.map(r => {
			const aged = (r.age_days || 0) > 30;
			const draft_pill = r.has_draft_pi
				? ` <a class="apb-draft-pi-pill" href="/app/purchase-invoice/${encodeURIComponent(r.draft_pi)}" title="Open existing Draft PI">Has draft PI: ${_apb_esc(r.draft_pi)}</a>`
				: "";
			const po_cell = r.po_count > 1
				? `<a href="/app/purchase-order/${encodeURIComponent(r.po)}" target="_blank">${_apb_esc(r.po)}</a> <span class="apb-po-more" title="${_apb_esc((r.po_list || []).join(', '))}">(+${r.po_count - 1} more)</span>`
				: (r.po
					? `<a href="/app/purchase-order/${encodeURIComponent(r.po)}" target="_blank">${_apb_esc(r.po)}</a>`
					: '—');
			const action_cell = auditor
				? `<button class="btn btn-xs btn-default apb-open-pr" data-pr="${_apb_esc(r.pr)}">Open</button>`
				: `<button class="btn btn-xs btn-default apb-open-pr" data-pr="${_apb_esc(r.pr)}">Open</button> <button class="btn btn-xs btn-primary apb-create-pi" data-pr="${_apb_esc(r.pr)}">Create PI</button>`;
			return `<tr class="${aged ? 'apb-aged-row' : ''}">
				<td><strong>${_apb_esc(r.pr)}</strong>${draft_pill}</td>
				<td>${_apb_esc(r.posting_date)}</td>
				<td>${_apb_esc(r.supplier_name || r.supplier)}</td>
				<td>${po_cell}</td>
				<td class="apb-num">${format_currency(r.grand_total, r.currency)}</td>
				<td class="apb-num">${format_currency(r.billed_amount, r.currency)}</td>
				<td>${_apb_pb_badge(r.per_billed)}</td>
				<td>${_apb_age_days(r.age_days)}</td>
				<td>${action_cell}</td>
			</tr>${_apb_items_detail(r.items, 9, aged)}`;
		}),
		'</tbody></table>'
	].join("");
	$("#apb-body").html(html);

	$("#apb-body .apb-open-pr").on("click", function () {
		frappe.set_route("Form", "Purchase Receipt", $(this).data("pr"));
	});

	$("#apb-body .apb-create-pi").on("click", function () {
		const btn = $(this);
		const pr = btn.data("pr");
		_apb_confirm(`Create a Draft Purchase Invoice from ${pr}? You will be redirected to the PI to review + submit.`, async () => {
			btn.prop("disabled", true).text("Creating…");
			let succeeded = false;
			try {
				const r = await frappe.call({
					method: `${APB_API}.create_pi_from_pr`,
					type: "POST",
					args: { pr_name: pr },
					freeze: true, freeze_message: "Creating Draft Purchase Invoice…",
				});
				if (r && r.message && r.message.purchase_invoice) {
					succeeded = true;
					frappe.show_alert({
						message: `Draft Purchase Invoice ${frappe.utils.escape_html(r.message.purchase_invoice)} created — review and submit.`,
						indicator: "blue",
					}, 8);
					frappe.set_route("Form", "Purchase Invoice", r.message.purchase_invoice);
				} else {
					console.warn("[accounts-pending-billing] no purchase_invoice in response", r);
					frappe.msgprint({
						title: "Invoice not created",
						message: "The server returned no Purchase Invoice. Check the browser console and the Frappe Error Log.",
						indicator: "orange",
					});
				}
			} catch (e) {
				console.error("[accounts-pending-billing] create_pi error", e);
				const has_server_msg = e && (e._server_messages || (e.responseJSON && e.responseJSON._server_messages));
				if (!has_server_msg) {
					frappe.msgprint({
						title: "Could not create invoice",
						message: frappe.utils.escape_html((e && (e.message || e.statusText)) || "Network or script error — see browser console."),
						indicator: "red",
					});
				}
			} finally {
				if (!succeeded) {
					btn.prop("disabled", false).text("Create PI");
				}
			}
		});
	});
}

function _apb_confirm(msg, onYes, onNo) {
	frappe.confirm(msg, onYes, onNo);
}

function _apb_items_detail(items, colSpan, aged) {
	const note = aged ? '<div class="apb-aged-warn">⚠ &gt;30 days unbilled</div>' : '';
	if (!items || !items.length) {
		return note ? `<tr class="apb-item-row"><td colspan="${colSpan}">${note}</td></tr>` : '';
	}
	const pills = items.map(it =>
		`<span class="apb-item-pill">${_apb_esc(it.item_name || it.item_code)} · ${it.qty || 0} ${_apb_esc(it.uom || "")}</span>`
	).join("");
	return `<tr class="apb-item-row"><td colspan="${colSpan}">${note}<div class="apb-item-pills">${pills}</div></td></tr>`;
}

function _apb_esc(v) { return frappe.utils.escape_html(String(v == null ? "" : v)); }

function _apb_age_days(d) {
	if (d == null) return "";
	if (d === 0) return "today";
	return `${d}d`;
}

function _apb_pb_badge(pb) {
	pb = Number(pb) || 0;
	let cls = "apb-pb-zero";
	if (pb > 0 && pb <= 50) cls = "apb-pb-partial";
	else if (pb > 50 && pb < 100) cls = "apb-pb-near";
	const pct = pb < 1 && pb > 0 ? pb.toFixed(2) : Math.round(pb);
	return `<span class="apb-pb ${cls}">${pct}%</span>`;
}

const APB_CSS = `
#apb-container { padding: 16px; }
.apb-header { display:grid; grid-template-columns: 1fr auto; align-items:center; margin-bottom:18px; gap:16px; }
.apb-title h2 { margin:0 0 4px 0; font-size:22px; font-weight:600; color: var(--text-color); }
.apb-subtitle { color: var(--text-muted); font-size:13px; }
.apb-summary { display:flex; gap:12px; flex-wrap:wrap; }
.apb-badge { background: var(--card-bg, #f8fafc); border:1px solid var(--border-color, #e2e8f0); border-radius:8px; padding:10px 18px; text-align:center; min-width:110px; }
.apb-badge-num { font-size:26px; font-weight:700; color: var(--text-color); }
.apb-badge-lbl { font-size:11px; color: var(--text-muted); text-transform:uppercase; letter-spacing:0.4px; margin-top:2px; }
.apb-b-pending .apb-badge-num { color:#2563eb; }
.apb-b-partial .apb-badge-num { color:#d97706; }
.apb-b-aged .apb-badge-num { color:#dc2626; }
.apb-section { background: var(--card-bg, #ffffff); border:1px solid var(--border-color, #e2e8f0); border-radius:10px; margin-bottom:16px; overflow:hidden; }
.apb-section-head { padding:14px 18px; border-bottom:1px solid var(--border-color, #e2e8f0); background: var(--fg-color, #f1f5f9); }
.apb-section-title { font-size:15px; font-weight:600; color: var(--text-color); }
.apb-section-hint { font-size:12px; color: var(--text-muted); margin-top:2px; }
.apb-body { padding: 0; }
.apb-table { width:100%; border-collapse:collapse; font-size:13px; }
.apb-table thead th { background: var(--fg-color, #f8fafc); padding:10px 14px; text-align:left; font-weight:600; color: var(--text-muted); font-size:11px; text-transform:uppercase; letter-spacing:0.4px; border-bottom:1px solid var(--border-color, #e2e8f0); }
.apb-table thead th.apb-num { text-align:right; }
.apb-table tbody td { padding:10px 14px; border-bottom:1px solid var(--border-color, #f1f5f9); color: var(--text-color); vertical-align: top; }
.apb-table tbody tr:hover { background: var(--fg-hover, rgba(0,0,0,0.02)); }
.apb-num { text-align:right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.apb-empty { padding:24px 18px; color: var(--text-muted); text-align:center; font-size:13px; }
.apb-error { padding:30px; color:#ef4444; text-align:center; }
.apb-pb { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }
.apb-pb.apb-pb-zero { background:#fee2e2; color:#991b1b; }
.apb-pb.apb-pb-partial { background:#fef3c7; color:#92400e; }
.apb-pb.apb-pb-near { background:#dcfce7; color:#166534; }
[data-theme="dark"] .apb-pb.apb-pb-zero { background:rgba(239,68,68,0.2); color:#fca5a5; }
[data-theme="dark"] .apb-pb.apb-pb-partial { background:rgba(217,119,6,0.2); color:#fcd34d; }
[data-theme="dark"] .apb-pb.apb-pb-near { background:rgba(22,163,74,0.2); color:#86efac; }
[data-theme="dark"] .apb-section { background:#1f2937; border-color:#374151; }
[data-theme="dark"] .apb-section-head { background:#111827; border-color:#374151; }
[data-theme="dark"] .apb-badge { background:#1f2937; border-color:#374151; }
[data-theme="dark"] .apb-table thead th { background:#111827; border-color:#374151; }
[data-theme="dark"] .apb-table tbody td { border-color:#374151; }
[data-theme="dark"] .apb-table tbody tr:hover { background:#111827; }
.apb-version { font-size:10px; color:var(--text-light, #94a3b8); background:var(--fg-color, #f1f5f9); padding:2px 8px; border-radius:4px; margin-left:8px; font-family:monospace; }
[data-theme="dark"] .apb-version { background:#374151; color:#9ca3af; }
.apb-aged-row td { background:#fef2f2; }
.apb-aged-warn { background:#fef2f2; color:#991b1b; font-size:12px; padding:6px 10px; border-left:3px solid #ef4444; margin-bottom:6px; }
[data-theme="dark"] .apb-aged-row td { background:rgba(239,68,68,0.08); }
[data-theme="dark"] .apb-aged-warn { background:rgba(239,68,68,0.08); color:#fca5a5; border-left-color:#ef4444; }
.apb-item-row td { padding:4px 14px 10px !important; border-bottom:1px solid var(--border-color, #e2e8f0) !important; }
.apb-item-pills { display:flex; flex-wrap:wrap; gap:6px; }
.apb-item-pill { display:inline-block; background:var(--fg-color, #f1f5f9); color:var(--text-color); font-size:11px; padding:3px 10px; border-radius:12px; border:1px solid var(--border-color, #e2e8f0); white-space:nowrap; }
[data-theme="dark"] .apb-item-pill { background:#374151; border-color:#4b5563; }
.apb-draft-pi-pill { display:inline-block; margin-left:8px; padding:2px 8px; border-radius:10px; background:#fef3c7; color:#92400e; font-size:10px; font-weight:600; text-decoration:none; }
.apb-draft-pi-pill:hover { background:#fde68a; text-decoration:none; }
[data-theme="dark"] .apb-draft-pi-pill { background:rgba(217,119,6,0.25); color:#fcd34d; }
.apb-po-more { font-size:10px; color: var(--text-muted); margin-left:4px; }
@media (max-width: 768px) {
	.apb-header { grid-template-columns: 1fr; }
	.apb-summary { justify-content: flex-start; }
	.apb-table thead { display:none; }
	.apb-table, .apb-table tbody, .apb-table tr, .apb-table td { display:block; width:100%; box-sizing:border-box; }
	.apb-table tbody tr { border:1px solid var(--border-color, #e2e8f0); border-radius:8px; margin-bottom:10px; padding:8px 12px; }
	.apb-table tbody td { border:none; padding:4px 0; }
	.apb-table tbody td.apb-num { text-align:left; }
	.apb-item-row td { padding-top:6px !important; }
}
`;
