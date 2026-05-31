/* ═══════════════════════════════════════════════════════════════════
   Stores Receiving Dashboard
   One page, three sections:
     A — Tare Weighed tokens (existing Token flow)
     B — Non-RM without weighing (new, fills visibility gap)
     C — Approved Direct PO (new, no token)
   ═══════════════════════════════════════════════════════════════════ */

const SR_VERSION = "v6.1-2026-04-20-v2.8.9";
const SR_API = "trustbit_ethanol.ts_gate_entry.stores_receiving_api";
console.log("[stores-receiving]", SR_VERSION, "loaded");

frappe.pages["stores-receiving"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Stores Receiving Dashboard",
		single_column: true,
	});

	page.main.html(`<div id="sr-container" style="padding:0;">
		<div id="sr-loading" style="text-align:center;padding:60px;color:#9ca3af;font-size:14px;">Loading Stores Receiving Dashboard…</div>
	</div>`);

	if (!document.getElementById("sr-styles")) {
		const style = document.createElement("style");
		style.id = "sr-styles";
		style.textContent = SR_CSS;
		document.head.appendChild(style);
	}

	page.add_button(__("Refresh"), () => _sr_reload(), { icon: "refresh" });

	setTimeout(() => _sr_init(), 200);
};

frappe.pages["stores-receiving"].refresh = function () {
	_sr_reload();
};

let _sr_state = { d: [], a: [], b: [], busy: false };

async function _sr_init() {
	try {
		_sr_render_shell();
		await _sr_load_all();
	} catch (e) {
		$("#sr-container").html(`<div class="sr-error">Error: ${frappe.utils.escape_html(e.message || String(e))}</div>`);
		console.error(e);
	}
}

function _sr_reload() {
	_sr_load_all();
}

function _sr_render_shell() {
	$("#sr-container").html(`
		<div class="sr-header">
			<div class="sr-title">
				<div class="sr-subtitle">Pending material receipts across all flows <span class="sr-version">${SR_VERSION}</span></div>
			</div>
			<div class="sr-summary">
				<div class="sr-badge sr-b-d" id="sr-badge-d" style="display:none"><div class="sr-badge-num" id="sr-count-d">0</div><div class="sr-badge-lbl">Drafts</div></div>
				<div class="sr-badge sr-b-a"><div class="sr-badge-num" id="sr-count-a">—</div><div class="sr-badge-lbl">Weighed</div></div>
				<div class="sr-badge sr-b-b"><div class="sr-badge-num" id="sr-count-b">—</div><div class="sr-badge-lbl">Non-RM (No Weighing)</div></div>
			</div>
		</div>

		<div class="sr-section sr-section-draft" id="sr-section-d" style="display:none">
			<div class="sr-section-head sr-draft-head">
				<div class="sr-section-title">Draft PRs — Pending Review & Submit</div>
				<div class="sr-section-hint">These were created from this dashboard but not yet submitted. Edit quantities and submit to complete receiving.</div>
			</div>
			<div id="sr-d-body" class="sr-body"><div class="sr-empty">Loading…</div></div>
		</div>

		<div class="sr-section" id="sr-section-a">
			<div class="sr-section-head">
				<div class="sr-section-title">Section A — Weighbridge-Complete Tokens</div>
				<div class="sr-section-hint">Status: Tare Weighed · Net weight pre-filled from weighbridge — edit qty on PR form before submitting</div>
			</div>
			<div id="sr-a-body" class="sr-body"><div class="sr-empty">Loading…</div></div>
		</div>

		<div class="sr-section" id="sr-section-b">
			<div class="sr-section-head">
				<div class="sr-section-title">Section B — Non-RM without Weighing <span class="sr-tag-new">New</span></div>
				<div class="sr-section-hint">Uses PO ordered qty · Inspection gate enforced</div>
			</div>
			<div id="sr-b-body" class="sr-body"><div class="sr-empty">Loading…</div></div>
		</div>

	`);
}

async function _sr_load_all() {
	try {
		const r = await frappe.call({ method: `${SR_API}.get_dashboard_data` });
		const data = r.message || {};
		_sr_state.d = data.d || [];
		_sr_state.a = data.a || [];
		_sr_state.b = data.b || [];
		_sr_render_section_d();
		_sr_render_section_a();
		_sr_render_section_b();
		const counts = data.counts || {};
		// Drafts badge — only show if there are drafts
		if (_sr_state.d.length) {
			$("#sr-badge-d").show();
			$("#sr-count-d").text(_sr_state.d.length);
			$("#sr-section-d").show();
		} else {
			$("#sr-badge-d").hide();
			$("#sr-section-d").hide();
		}
		$("#sr-count-a").text(counts.section_a_count ?? _sr_state.a.length);
		$("#sr-count-b").text(counts.section_b_count ?? _sr_state.b.length);
	} catch (e) {
		frappe.msgprint({ title: "Error", message: frappe.utils.escape_html(e.message || String(e)), indicator: "red" });
		console.error(e);
	}
}

function _sr_render_section_d() {
	const rows = _sr_state.d;
	if (!rows.length) { $("#sr-d-body").html('<div class="sr-empty">No draft PRs pending.</div>'); return; }
	const html = [
		'<table class="sr-table"><thead><tr>',
		'<th>Purchase Receipt</th><th>Supplier</th><th>PO</th><th>Source</th>',
		'<th>Total</th><th>Items</th><th>Age</th><th>Action</th>',
		'</tr></thead><tbody>',
		...rows.map(r => `<tr>
			<td><strong>${_sr_esc(r.pr)}</strong></td>
			<td>${_sr_esc(r.supplier)}</td>
			<td>${r.po ? `<a href="/app/purchase-order/${encodeURIComponent(r.po)}" target="_blank">${_sr_esc(r.po)}</a>` : "—"}</td>
			<td><span class="sr-source-pill sr-source-${r.source === 'Direct PO' ? 'c' : 'b'}">${_sr_esc(r.source)}</span></td>
			<td class="sr-num">${format_currency(r.total, r.currency)}</td>
			<td class="sr-num">${r.items_count}</td>
			<td>${_sr_age(r.age_hours)}</td>
			<td><button class="btn btn-xs btn-warning sr-edit-draft" data-pr="${_sr_esc(r.pr)}">Edit & Submit</button></td>
		</tr>${_sr_items_detail(r.items, 8)}`),
		'</tbody></table>'
	].join("");
	$("#sr-d-body").html(html);
	$("#sr-d-body .sr-edit-draft").on("click", function () {
		frappe.set_route("Form", "Purchase Receipt", $(this).data("pr"));
	});
}

function _sr_render_section_a() {
	const rows = _sr_state.a;
	if (!rows.length) { $("#sr-a-body").html('<div class="sr-empty">No tokens pending GRN.</div>'); return; }
	const html = [
		'<table class="sr-table"><thead><tr>',
		'<th>Token</th><th>Vehicle</th><th>Supplier</th><th>PO</th>',
		'<th>Net Wt (Kg)</th><th>Inspection</th><th>Age</th><th>Action</th>',
		'</tr></thead><tbody>',
		...rows.map(r => `<tr>
			<td><strong>${_sr_esc(r.token)}</strong></td>
			<td>${_sr_esc(r.vehicle)}</td>
			<td>${_sr_esc(r.supplier)}</td>
			<td>${r.po ? `<a href="/app/purchase-order/${encodeURIComponent(r.po)}" target="_blank">${_sr_esc(r.po)}</a>` : ""}</td>
			<td class="sr-num">${r.net_weight || 0}</td>
			<td>${_sr_insp_badge(r.inspection_status)}</td>
			<td>${_sr_age(r.age_hours)}</td>
			<td>
				<button class="btn btn-xs btn-default sr-open-token" data-token="${_sr_esc(r.token)}">Open</button>
				<button class="btn btn-xs btn-success sr-create-a" data-token="${_sr_esc(r.token)}">Create GRN</button>
			</td>
		</tr>${_sr_items_detail(r.items, 8)}`),
		'</tbody></table>'
	].join("");
	$("#sr-a-body").html(html);
	$("#sr-a-body .sr-open-token").on("click", function () {
		frappe.set_route("Form", "TS Token", $(this).data("token"));
	});
	$("#sr-a-body .sr-create-a").on("click", function () {
		const btn = $(this);
		const token = btn.data("token");
		console.log("[stores-receiving] Section A click → token", token);
		_sr_section_a_start(btn, token);
	});
}

// v2.8.9 — UOM-aware Create GRN entry point. Fetches UOM summary first; if ALL
// items are KG, falls through to the legacy direct path. Otherwise opens the
// manual-qty dialog so the operator can enter received qty in the PO's UOM.
function _sr_is_kg_uom(uom) {
	return String(uom || "").trim().toLowerCase() === "kg";
}

async function _sr_section_a_start(btn, token) {
	btn.prop("disabled", true).text("Checking UOM…");
	let restore = true;
	try {
		const r = await frappe.call({
			method: `${SR_API}.get_token_uom_summary`,
			args: { token_name: token },
		});
		const summary = (r && r.message) || {};
		const rows = Array.isArray(summary.rows) ? summary.rows : [];
		if (!rows.length) {
			frappe.msgprint({
				title: "No items",
				message: "This token has no Gate Entry items — cannot create GRN.",
				indicator: "orange",
			});
			return;
		}
		const anyNonKg = rows.some(row => !_sr_is_kg_uom(row.uom));
		if (!anyNonKg) {
			// Legacy all-KG path — no dialog, weighbridge net weight used directly.
			restore = await _sr_confirm_and_create_grn_kg(btn, token);
			return;
		}
		// Non-KG path — dialog.
		restore = true;
		_sr_open_manual_qty_dialog(btn, token, summary);
	} catch (e) {
		console.error("[stores-receiving] UOM summary fetch failed:", e);
		const has_server_msg = e && (e._server_messages || (e.responseJSON && e.responseJSON._server_messages));
		if (!has_server_msg) {
			frappe.msgprint({
				title: "Could not fetch UOM summary",
				message: frappe.utils.escape_html((e && (e.message || e.statusText)) || "Network or script error."),
				indicator: "red",
			});
		}
	} finally {
		if (restore) btn.prop("disabled", false).text("Create GRN");
	}
}

function _sr_confirm_and_create_grn_kg(btn, token) {
	return new Promise((resolve) => {
		_sr_confirm_create(
			`Create Draft GRN from weighed token ${token}? Net weight will be pre-filled — you can edit quantities on the PR form.`,
			async () => {
				btn.text("Creating…");
				let succeeded = false;
				try {
					const r = await frappe.call({
						method: `${SR_API}.create_grn_for_weighed_token`,
						args: { token_name: token },
						freeze: true, freeze_message: "Creating GRN…",
					});
					if (r && r.message && r.message.purchase_receipt) {
						succeeded = true;
						frappe.show_alert({
							message: `Draft Purchase Receipt ${r.message.purchase_receipt} created — review quantities and submit.`,
							indicator: "blue",
						}, 8);
						frappe.set_route("Form", "Purchase Receipt", r.message.purchase_receipt);
					} else {
						frappe.msgprint({
							title: "GRN not created",
							message: "The server returned no Purchase Receipt. Check the browser console and server error log.",
							indicator: "orange",
						});
					}
				} catch (e) {
					console.error("[stores-receiving] API error:", e);
					const has_server_msg = e && (e._server_messages || (e.responseJSON && e.responseJSON._server_messages));
					if (!has_server_msg) {
						frappe.msgprint({
							title: "GRN creation failed",
							message: frappe.utils.escape_html((e && (e.message || e.statusText)) || "Network or script error — see browser console."),
							indicator: "red",
						});
					}
				} finally {
					resolve(!succeeded);
				}
			},
			() => resolve(true),  // user cancelled → restore button
		);
	});
}

function _sr_open_manual_qty_dialog(btn, token, summary) {
	const rows = summary.rows || [];
	const wbNet = Number(summary.wb_net_kg || 0);
	const safeToken = frappe.utils.escape_html(token);

	// Build table HTML. KG rows are pre-filled from wb_net_kg, read-only.
	// Non-KG rows expose an input; implied density is live-computed.
	const tableRows = rows.map((row, idx) => {
		const isKg = _sr_is_kg_uom(row.uom);
		const itemLabel = `${frappe.utils.escape_html(row.item_code)} <span style="color:#6b7280">${frappe.utils.escape_html(row.item_name || "")}</span>`;
		const ordered = `${Number(row.ordered_qty || 0)} ${frappe.utils.escape_html(row.uom || "")}`;
		const poLink = row.po_name
			? `<a href="/app/purchase-order/${encodeURIComponent(row.po_name)}" target="_blank">${frappe.utils.escape_html(row.po_name)}</a>`
			: "—";
		if (isKg) {
			return `<tr>
				<td>${poLink}</td>
				<td>${itemLabel}</td>
				<td>${ordered}</td>
				<td><input type="number" class="form-control sr-qty-input" data-idx="${idx}" value="${wbNet}" readonly style="background:#f1f5f9;"></td>
				<td><span class="text-muted">KG (direct)</span></td>
			</tr>`;
		}
		return `<tr>
			<td>${poLink}</td>
			<td>${itemLabel}</td>
			<td>${ordered}</td>
			<td><input type="number" step="0.001" min="0" class="form-control sr-qty-input" data-idx="${idx}" placeholder="received qty (blank = skip)"></td>
			<td><span class="sr-density" data-idx="${idx}" style="color:#6b7280; font-size:12px;">—</span></td>
		</tr>`;
	}).join("");

	const html = `
		<div style="margin-bottom:12px;">
			<div style="font-weight:600;">Token ${safeToken}</div>
			<div style="color:#6b7280; font-size:13px;">Weighbridge Net: <strong>${wbNet}</strong> KG <span style="color:#9ca3af;">(reference — not auto-applied to non-KG items)</span></div>
			<div style="color:#6b7280; font-size:12px; margin-top:4px;">Enter the qty received for each item on this truck. <strong>Leave a row blank to skip</strong> items not delivered — they stay open on the PO for a future delivery. (Ordered qty is shown in the Ordered column.)</div>
		</div>
		<table class="table table-bordered" style="font-size:13px; margin-bottom:0;">
			<thead><tr style="background:#f8fafc;">
				<th>PO</th><th>Item</th><th>Ordered</th><th>Received Qty</th><th>Implied Density</th>
			</tr></thead>
			<tbody>${tableRows}</tbody>
		</table>
	`;

	const dlg = new frappe.ui.Dialog({
		title: `Receive — Token ${token}`,
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: "Create GRN",
		primary_action: async () => {
			// Build manual_qty_per_po from inputs.
			const manual = {};
			let receivedCount = 0;
			dlg.$wrapper.find(".sr-qty-input").each(function () {
				const idx = Number($(this).data("idx"));
				const row = rows[idx];
				if (!row) return;
				if (_sr_is_kg_uom(row.uom)) {
					// KG rows are weighbridge-driven and always received; backend uses
					// wb_net_kg directly (not manual_qty_per_po). Count as received.
					receivedCount++;
					return;
				}
				const raw = $(this).val();
				const qty = Number(raw);
				if (!raw || !(qty > 0)) {
					// PARTIAL RECEIPT: a blank/zero non-KG row = not received on this
					// truck → skip it (no PR line); it stays open on the PO for a
					// future truck/token. Not an error.
					return;
				}
				const po = row.po_name || "";
				if (!manual[po]) manual[po] = {};
				manual[po][row.item_code] = qty;
				receivedCount++;
			});
			if (receivedCount === 0) {
				frappe.msgprint({
					title: "No quantity entered",
					message: "Enter a received quantity for at least one item. Rows left blank are skipped (not received on this truck) and stay open on the PO.",
					indicator: "orange",
				});
				return;
			}
			dlg.disable_primary_action();
			try {
				const r = await frappe.call({
					method: `${SR_API}.create_grn_for_weighed_token`,
					args: { token_name: token, manual_qty_per_po: manual },
					freeze: true, freeze_message: "Creating GRN…",
				});
				if (r && r.message && r.message.purchase_receipt) {
					dlg.hide();
					frappe.show_alert({
						message: `Draft Purchase Receipt ${r.message.purchase_receipt} created — review and submit.`,
						indicator: "blue",
					}, 8);
					frappe.set_route("Form", "Purchase Receipt", r.message.purchase_receipt);
				} else {
					dlg.enable_primary_action();
					frappe.msgprint({
						title: "GRN not created",
						message: "Server returned no Purchase Receipt.",
						indicator: "orange",
					});
				}
			} catch (e) {
				console.error("[stores-receiving] manual-qty API error:", e);
				dlg.enable_primary_action();
				const has_server_msg = e && (e._server_messages || (e.responseJSON && e.responseJSON._server_messages));
				if (!has_server_msg) {
					frappe.msgprint({
						title: "GRN creation failed",
						message: frappe.utils.escape_html((e && (e.message || e.statusText)) || "Network or script error."),
						indicator: "red",
					});
				}
			}
		},
		secondary_action_label: "Cancel",
		secondary_action: () => dlg.hide(),
	});

	dlg.fields_dict.body.$wrapper.html(html);

	// Live density hint: (wb_net / qty) kg/uom per row.
	dlg.$wrapper.on("input", ".sr-qty-input", function () {
		const idx = Number($(this).data("idx"));
		const row = rows[idx];
		if (!row || _sr_is_kg_uom(row.uom)) return;
		const qty = Number($(this).val());
		const $d = dlg.$wrapper.find(`.sr-density[data-idx="${idx}"]`);
		if (qty > 0 && wbNet > 0) {
			$d.text(`${(wbNet / qty).toFixed(3)} kg/${row.uom}`);
		} else {
			$d.text("—");
		}
	});

	dlg.onhide = () => {
		btn.prop("disabled", false).text("Create GRN");
	};
	dlg.show();
}

function _sr_render_section_b() {
	const rows = _sr_state.b;
	if (!rows.length) { $("#sr-b-body").html('<div class="sr-empty">No Non-RM no-weighing tokens pending.</div>'); return; }
	const html = [
		'<table class="sr-table"><thead><tr>',
		'<th>Token</th><th>Vehicle</th><th>Supplier</th><th>PO</th>',
		'<th>Items</th><th>Inspection</th><th>Age</th><th>Action</th>',
		'</tr></thead><tbody>',
		...rows.map(r => `<tr>
			<td><strong>${_sr_esc(r.token)}</strong></td>
			<td>${_sr_esc(r.vehicle)}</td>
			<td>${_sr_esc(r.supplier)}</td>
			<td>${r.po ? `<a href="/app/purchase-order/${encodeURIComponent(r.po)}" target="_blank">${_sr_esc(r.po)}</a>` : ""}</td>
			<td class="sr-num">${r.items_count}</td>
			<td>${_sr_insp_badge(r.inspection_status)}</td>
			<td>${_sr_age(r.age_hours)}</td>
			<td>
				<button class="btn btn-xs btn-default sr-open-token" data-token="${_sr_esc(r.token)}">Open</button>
				<button class="btn btn-xs btn-success sr-create-b" data-token="${_sr_esc(r.token)}">Create GRN</button>
			</td>
		</tr>${_sr_items_detail(r.items, 8)}`),
		'</tbody></table>'
	].join("");
	$("#sr-b-body").html(html);
	$("#sr-b-body .sr-open-token").on("click", function () {
		frappe.set_route("Form", "TS Token", $(this).data("token"));
	});
	$("#sr-b-body .sr-create-b").on("click", function () {
		const btn = $(this);
		const token = btn.data("token");
		console.log("[stores-receiving] Section B click → token", token);
		_sr_confirm_create(`Create GRN from token ${token}? This will create a Purchase Receipt using PO ordered qty.`, async () => {
			console.log("[stores-receiving] Confirmed, calling API", token);
			btn.prop("disabled", true).text("Creating…");
			let succeeded = false;
			try {
				const r = await frappe.call({
					method: `${SR_API}.create_grn_for_non_weighing_token`,
					args: { token_name: token },
					freeze: true, freeze_message: "Creating GRN…",
				});
				console.log("[stores-receiving] API response", r);
				if (r && r.message && r.message.purchase_receipt) {
					succeeded = true;
					frappe.show_alert({
						message: `Draft Purchase Receipt ${r.message.purchase_receipt} created — review quantities and submit.`,
						indicator: "blue"
					}, 8);
					frappe.set_route("Form", "Purchase Receipt", r.message.purchase_receipt);
				} else {
					console.warn("[stores-receiving] API returned without purchase_receipt:", r);
					frappe.msgprint({
						title: "GRN not created",
						message: "The server returned no Purchase Receipt. Check the browser console and server error log.",
						indicator: "orange"
					});
				}
			} catch (e) {
				console.error("[stores-receiving] API error:", e);
				// Frappe's native AJAX error handler already shows a dialog with the
				// server's frappe.throw message. Only show our fallback for non-server
				// errors (network, JS) where _server_messages is absent.
				const has_server_msg = e && (e._server_messages || (e.responseJSON && e.responseJSON._server_messages));
				if (!has_server_msg) {
					frappe.msgprint({
						title: "GRN creation failed",
						message: frappe.utils.escape_html(
							(e && (e.message || e.statusText)) || "Network or script error — see browser console."
						),
						indicator: "red"
					});
				}
			} finally {
				if (!succeeded) {
					btn.prop("disabled", false).text("Create GRN");
				}
			}
		});
	});
}

function _sr_confirm_create(msg, onYes, onNo) {
	frappe.confirm(msg, onYes, onNo);
}

function _sr_items_detail(items, colSpan) {
	if (!items || !items.length) return "";
	const pills = items.map(it =>
		`<span class="sr-item-pill">${_sr_esc(it.item_name || it.item_code)} · ${it.qty || it.ordered_qty || 0} ${_sr_esc(it.uom || "")}</span>`
	).join("");
	return `<tr class="sr-item-row"><td colspan="${colSpan}"><div class="sr-item-pills">${pills}</div></td></tr>`;
}

function _sr_esc(v) { return frappe.utils.escape_html(String(v == null ? "" : v)); }
function _sr_age(h) {
	if (!h && h !== 0) return "";
	if (h < 1) return "&lt;1h";
	if (h < 24) return `${Math.round(h)}h`;
	return `${Math.round(h/24)}d`;
}
function _sr_insp_badge(s) {
	const cls = {
		"Approved": "sr-ok",
		"Auto-Proceeded": "sr-ok",
		"Partially Approved": "sr-ok",
		"Not Required": "sr-muted",
		"Pending Inspection": "sr-warn",
		"On Hold": "sr-warn",
		"Rejected": "sr-bad",
	}[s] || "sr-muted";
	return `<span class="sr-insp ${cls}">${_sr_esc(s || "-")}</span>`;
}

const SR_CSS = `
#sr-container { padding: 16px; }
.sr-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:18px; gap:16px; flex-wrap:wrap; }
.sr-title h2 { margin:0 0 4px 0; font-size:22px; font-weight:600; color: var(--text-color); }
.sr-subtitle { color: var(--text-muted); font-size:13px; }
.sr-summary { display:flex; gap:12px; }
.sr-badge { background: var(--card-bg, #f8fafc); border:1px solid var(--border-color, #e2e8f0); border-radius:8px; padding:10px 18px; text-align:center; min-width:120px; }
.sr-badge-num { font-size:26px; font-weight:700; color: var(--text-color); }
.sr-badge-lbl { font-size:11px; color: var(--text-muted); text-transform:uppercase; letter-spacing:0.4px; margin-top:2px; }
.sr-b-a .sr-badge-num { color:#2563eb; }
.sr-b-b .sr-badge-num { color:#d97706; }
.sr-b-c .sr-badge-num { color:#16a34a; }
.sr-section { background: var(--card-bg, #ffffff); border:1px solid var(--border-color, #e2e8f0); border-radius:10px; margin-bottom:16px; overflow:hidden; }
.sr-section-head { padding:14px 18px; border-bottom:1px solid var(--border-color, #e2e8f0); background: var(--fg-color, #f1f5f9); }
.sr-section-title { font-size:15px; font-weight:600; color: var(--text-color); }
.sr-section-hint { font-size:12px; color: var(--text-muted); margin-top:2px; }
.sr-tag-new { display:inline-block; background:#16a34a; color:#fff; font-size:10px; padding:2px 7px; border-radius:4px; margin-left:6px; letter-spacing:0.3px; }
.sr-body { padding: 0; }
.sr-table { width:100%; border-collapse:collapse; font-size:13px; }
.sr-table thead th { background: var(--fg-color, #f8fafc); padding:10px 14px; text-align:left; font-weight:600; color: var(--text-muted); font-size:11px; text-transform:uppercase; letter-spacing:0.4px; border-bottom:1px solid var(--border-color, #e2e8f0); }
.sr-table tbody td { padding:10px 14px; border-bottom:1px solid var(--border-color, #f1f5f9); color: var(--text-color); }
.sr-table tbody tr:hover { background: var(--fg-hover, rgba(0,0,0,0.02)); }
.sr-num { text-align:right; font-variant-numeric: tabular-nums; }
.sr-empty { padding:24px 18px; color: var(--text-muted); text-align:center; font-size:13px; }
.sr-error { padding:30px; color:#ef4444; text-align:center; }
.sr-insp { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:500; }
.sr-insp.sr-ok { background:#dcfce7; color:#166534; }
.sr-insp.sr-warn { background:#fef3c7; color:#92400e; }
.sr-insp.sr-bad { background:#fee2e2; color:#991b1b; }
.sr-insp.sr-muted { background:#f1f5f9; color:#64748b; }
[data-theme="dark"] .sr-insp.sr-ok { background:rgba(22,163,74,0.2); color:#86efac; }
[data-theme="dark"] .sr-insp.sr-warn { background:rgba(217,119,6,0.2); color:#fcd34d; }
[data-theme="dark"] .sr-insp.sr-bad { background:rgba(239,68,68,0.2); color:#fca5a5; }
[data-theme="dark"] .sr-insp.sr-muted { background:rgba(100,116,139,0.2); color:#cbd5e1; }
[data-theme="dark"] .sr-section { background:#1f2937; border-color:#374151; }
[data-theme="dark"] .sr-section-head { background:#111827; border-color:#374151; }
[data-theme="dark"] .sr-badge { background:#1f2937; border-color:#374151; }
[data-theme="dark"] .sr-table thead th { background:#111827; border-color:#374151; }
[data-theme="dark"] .sr-table tbody td { border-color:#374151; }
[data-theme="dark"] .sr-table tbody tr:hover { background:#111827; }
.sr-version { font-size:10px; color:var(--text-light, #94a3b8); background:var(--fg-color, #f1f5f9); padding:2px 8px; border-radius:4px; margin-left:8px; font-family:monospace; }
[data-theme="dark"] .sr-version { background:#374151; color:#9ca3af; }
.sr-broken td { background:#fef2f2; }
.sr-broken-warn td { background:#fef2f2; color:#991b1b; font-size:12px; padding:8px 14px; border-left:3px solid #ef4444; }
[data-theme="dark"] .sr-broken td { background:rgba(239,68,68,0.08); }
[data-theme="dark"] .sr-broken-warn td { background:rgba(239,68,68,0.08); color:#fca5a5; }
.sr-item-row td { padding:4px 14px 10px !important; border-bottom:1px solid var(--border-color, #e2e8f0) !important; }
.sr-item-pills { display:flex; flex-wrap:wrap; gap:6px; }
.sr-item-pill { display:inline-block; background:var(--fg-color, #f1f5f9); color:var(--text-color); font-size:11px; padding:3px 10px; border-radius:12px; border:1px solid var(--border-color, #e2e8f0); white-space:nowrap; }
[data-theme="dark"] .sr-item-pill { background:#374151; border-color:#4b5563; }
.sr-b-d .sr-badge-num { color:#d97706; }
.sr-section-draft { border-color:#f59e0b !important; }
.sr-draft-head { background:#fffbeb !important; border-color:#fcd34d !important; }
[data-theme="dark"] .sr-draft-head { background:#78350f !important; border-color:#92400e !important; }
[data-theme="dark"] .sr-section-draft { border-color:#92400e !important; }
.sr-source-pill { display:inline-block; font-size:10px; padding:2px 8px; border-radius:10px; font-weight:500; }
.sr-source-b { background:#dbeafe; color:#1e40af; }
.sr-source-c { background:#dcfce7; color:#166534; }
[data-theme="dark"] .sr-source-b { background:rgba(30,64,175,0.2); color:#93c5fd; }
[data-theme="dark"] .sr-source-c { background:rgba(22,101,52,0.2); color:#86efac; }
`;
