/* ═══════════════════════════════════════════════════════════════════
   Stores Receiving Dashboard
   One page, three sections:
     A — Tare Weighed tokens (existing Token flow)
     B — Non-RM without weighing (new, fills visibility gap)
     C — Approved Direct PO (new, no token)
   ═══════════════════════════════════════════════════════════════════ */

const SR_VERSION = "v6.11-2026-07-31-grain-po-items-rate";
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

let _sr_state = { d: [], a: [], b: [], g: [], busy: false };
const GRAIN_API = "trustbit_ethanol.ts_gate_entry.ts_grain_defer";
const GE_PO_API = "trustbit_ethanol.ts_gate_entry.api.get_purchase_orders";

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
				<div class="sr-badge sr-b-g" id="sr-badge-g" style="display:none"><div class="sr-badge-num" id="sr-count-g" style="color:#d97706">0</div><div class="sr-badge-lbl">Grain · PO Pending</div></div>
					<div class="sr-badge sr-b-d" id="sr-badge-d" style="display:none"><div class="sr-badge-num" id="sr-count-d">0</div><div class="sr-badge-lbl">Drafts</div></div>
				<div class="sr-badge sr-b-a"><div class="sr-badge-num" id="sr-count-a">—</div><div class="sr-badge-lbl">Weighed</div></div>
				<div class="sr-badge sr-b-b"><div class="sr-badge-num" id="sr-count-b">—</div><div class="sr-badge-lbl">Non-RM (No Weighing)</div></div>
			</div>
		</div>

		<div class="sr-search" id="sr-search">
			<div class="sr-search-bar">
				<svg class="sr-search-icon" viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12.9 14.32a8 8 0 1 1 1.41-1.41l5.35 5.33-1.42 1.42-5.33-5.34zM8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12z"/></svg>
				<input type="text" id="sr-search-input" class="sr-search-input" placeholder="Search token #, vehicle, supplier or PO…" autocomplete="off" spellcheck="false">
				<button class="sr-search-clear" id="sr-search-clear" title="Clear" aria-label="Clear search" style="display:none">&times;</button>
			</div>
			<div class="sr-search-meta" id="sr-search-meta" style="display:none"><span class="sr-search-count" id="sr-search-count"></span></div>
			<div class="sr-result" id="sr-result" style="display:none"></div>
		</div>

		<div class="sr-section sr-section-grain" id="sr-section-g" style="display:none">
			<div class="sr-section-head sr-grain-head">
				<div class="sr-section-title">🔒 Grain — Awaiting PO Link</div>
				<div class="sr-section-hint">Weighed grain vehicles held at G2 Exit until you link the grain PO and create the GRN</div>
			</div>
			<div id="sr-g-body" class="sr-body"><div class="sr-empty">Loading…</div></div>
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
	_sr_bind_search();
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
		_sr_reapply_filter();
	} catch (e) {
		frappe.msgprint({ title: "Error", message: frappe.utils.escape_html(e.message || String(e)), indicator: "red" });
		console.error(e);
	}
	_sr_load_grain();
}

async function _sr_load_grain() {
	try {
		const r = await frappe.call({ method: `${GRAIN_API}.get_section_g` });
		_sr_state.g = r.message || [];
		_sr_render_section_g();
		if (_sr_state.g.length) {
			$("#sr-badge-g").show();
			$("#sr-count-g").text(_sr_state.g.length);
			$("#sr-section-g").show();
		} else {
			$("#sr-badge-g").hide();
			$("#sr-section-g").hide();
		}
		_sr_reapply_filter();
	} catch (e) {
		// Non-allow-listed users / errors: hide the section silently.
		$("#sr-badge-g").hide();
		$("#sr-section-g").hide();
	}
}

function _sr_render_section_g() {
	const rows = _sr_state.g;
	if (!rows.length) { $("#sr-g-body").html('<div class="sr-empty">No grain vehicles awaiting PO link.</div>'); return; }
	const html = [
		'<table class="sr-table"><thead><tr>',
		'<th>Token</th><th>Vehicle</th><th>Material</th><th>Net Wt (Kg)</th><th>Waiting</th><th>Action</th>',
		'</tr></thead><tbody>',
		...rows.map(r => `<tr data-search="${_sr_esc((r.token + " " + r.vehicle + " " + (r.material || "")).toLowerCase())}">
			<td><strong>${_sr_esc(r.token)}</strong></td>
			<td>${_sr_esc(r.vehicle)}</td>
			<td><span class="sr-status sr-warn">${_sr_esc(r.material)}</span></td>
			<td class="sr-num">${format_number(r.net_weight || 0, null, 0)}</td>
			<td>${_sr_age(r.age_hours)}</td>
			<td><button class="btn btn-xs btn-warning sr-link-grain" data-token="${_sr_esc(r.token)}" data-vehicle="${_sr_esc(r.vehicle)}" data-material="${_sr_esc(r.material)}" data-net="${_sr_esc(r.net_weight || 0)}">Link PO</button></td>
		</tr>`),
		'</tbody></table>'
	].join("");
	$("#sr-g-body").html(html);
	$("#sr-g-body .sr-link-grain").on("click", function () {
		const b = $(this);
		_sr_open_link_grain_dialog(b.data("token"), b.data("vehicle"), b.data("material"), b.data("net"));
	});
}

function _sr_open_link_grain_dialog(token, vehicle, material, net) {
	let selected_po = null;
	const dlg = new frappe.ui.Dialog({
		title: __("Link Grain PO — {0}", [token]),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Link PO & Create GRN"),
		primary_action: async () => {
			if (!selected_po) { frappe.show_alert({ message: __("Select a PO first"), indicator: "orange" }); return; }
			if (!dlg.$wrapper.find("#grain-confirm").is(":checked")) {
				frappe.show_alert({ message: __("Confirm the supplier match first"), indicator: "orange" }); return;
			}
			let received_qty = null;
			const $qtys = dlg.$wrapper.find(".grain-qty");
			if ($qtys.length) {
				received_qty = {};
				$qtys.each(function () { received_qty[$(this).data("item")] = Number($(this).val()) || 0; });
				if (!Object.values(received_qty).some(v => v > 0)) {
					frappe.show_alert({ message: __("Enter a received quantity greater than zero."), indicator: "orange" }); return;
				}
			}
			dlg.disable_primary_action();
			try {
				const r = await frappe.call({
					method: `${GRAIN_API}.link_grain_po_and_create_grn`,
					args: { token_name: token, po_name: selected_po, received_qty: received_qty ? JSON.stringify(received_qty) : undefined },
					freeze: true, freeze_message: __("Linking PO and creating GRN…")
				});
				frappe.show_alert({ message: __("GRN {0} created — vehicle released.", [r.message.purchase_receipt]), indicator: "green" }, 6);
				dlg.hide();
				if (r && r.message && r.message.purchase_receipt) {
					frappe.set_route("Form", "Purchase Receipt", r.message.purchase_receipt);
				} else {
					_sr_reload();
				}
			} catch (e) {
				dlg.enable_primary_action();
				// frappe.call already surfaces the server's own error message
				// (_server_messages). Only add a fallback for genuine network/script
				// errors — NEVER String(e), which renders an object as "[object Object]".
				const has_server_msg = e && (e._server_messages || (e.responseJSON && e.responseJSON._server_messages));
				if (!has_server_msg) {
					frappe.msgprint({ title: __("Could not link PO"), message: frappe.utils.escape_html((e && (e.message || e.statusText)) || __("Network or script error — check the browser console.")), indicator: "red" });
				}
			}
		}
	});

	const $b = $(dlg.fields_dict.body.$wrapper);
	$b.html(`
		<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:12px 14px;background:var(--fg-color,#f8fafc);border:1px solid var(--border-color,#e2e8f0);border-radius:8px;margin-bottom:14px">
			<div><div style="font-size:10px;text-transform:uppercase;letter-spacing:.3px;color:var(--text-muted);font-weight:600">Truck at exit gate</div>
			<div style="font-size:19px;font-weight:700;letter-spacing:.03em">${_sr_esc(vehicle)}</div></div>
			<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--text-muted)">
				<span>Token <b style="color:inherit">${_sr_esc(token)}</b></span>
				<span>Material <b>${_sr_esc(material)}</b></span>
				<span>Net <b>${format_number(net || 0, null, 0)} Kg</b></span>
			</div>
		</div>
		<div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px">
			<div><label style="font-size:11px;color:var(--text-muted);font-weight:600">PO Number (optional)</label><br>
				<input type="text" id="grain-po-id" class="form-control" style="min-width:170px" placeholder="e.g. 00505"></div>
			<div><label style="font-size:11px;color:var(--text-muted);font-weight:600">PO Date (optional)</label><br>
				<input type="date" id="grain-po-date" class="form-control" style="min-width:150px"></div>
			<button class="btn btn-default" id="grain-search-btn">${__("Search grain POs")}</button>
		</div>
		<div id="grain-results" style="overflow-x:auto"></div>
		<div id="grain-confirm-wrap" style="display:none;margin-top:12px;border:1px solid #f59e0b;border-radius:8px;overflow:hidden">
			<div id="grain-compare" class="grain-compare-panel" style="padding:12px 14px"></div>
				<div id="grain-qty-area" style="padding:0 14px 12px;border-top:1px solid #f59e0b"></div>
			<label style="display:flex;gap:9px;align-items:flex-start;padding:11px 14px;border-top:1px solid #f59e0b;font-size:12.5px;cursor:pointer">
				<input type="checkbox" id="grain-confirm" style="margin-top:2px">
				<span id="grain-confirm-label"></span>
			</label>
		</div>
	`);
	dlg.disable_primary_action();

	async function doSearch() {
		const po_id = $b.find("#grain-po-id").val();
		const po_date = $b.find("#grain-po-date").val();
		$b.find("#grain-results").html('<div class="sr-empty">Searching…</div>');
		try {
			const r = await frappe.call({ method: `${GRAIN_API}.get_grain_purchase_orders`, args: { po_id: po_id, po_date: po_date } });
			const pos = r.message || [];
			if (!pos.length) { $b.find("#grain-results").html('<div class="sr-empty">No matching grain Purchase Orders found.</div>'); return; }
			const rows = pos.map(p => {
				// Never allow a fully-received PO to be selected (the server also blocks
				// it; this keeps it un-clickable in the list as a safeguard).
				const fully_received = (Number(p.per_received) || 0) >= 100;
				const action = fully_received
					? `<span class="sr-status sr-muted" title="This PO is already 100% received">Fully received</span>`
					: `<button class="btn btn-xs btn-default grain-pick" data-po="${_sr_esc(p.name)}" data-supplier="${_sr_esc(p.supplier_name || p.supplier)}" data-total="${_sr_esc(p.grand_total || 0)}">Select</button>`;
				const items_html = (p.items || []).map(it =>
					`<div style="white-space:nowrap">${_sr_esc(it.item_name || "")} <span style="color:var(--text-muted)">· ${format_number(it.qty || 0)} ${_sr_esc(it.uom || "")} @</span> ${format_currency(it.rate || 0)}</div>`
				).join("") || `<span class="sr-muted">—</span>`;
				return `<tr>
					<td class="sr-mono">${_sr_esc(p.name)}</td>
					<td>${_sr_esc(p.supplier_name || p.supplier)}</td>
					<td>${items_html}</td>
					<td>${_sr_esc(p.transaction_date || "")}</td>
					<td class="sr-num">${format_number(p.total_qty || 0)}</td>
					<td class="sr-num">${format_currency(p.grand_total || 0)}</td>
					<td class="sr-num">${format_number(p.per_received || 0, null, 0)}%</td>
					<td>${action}</td>
				</tr>`;
			}).join("");
			$b.find("#grain-results").html(`<table class="sr-table" style="font-size:12px"><thead><tr>
				<th>PO</th><th>Supplier</th><th>Items (Rate)</th><th>Date</th><th>Ordered</th><th>Grand Total</th><th>Recd %</th><th></th></tr></thead><tbody>${rows}</tbody></table>`);
			$b.find(".grain-pick").on("click", async function () {
				const el = $(this);
				selected_po = el.data("po");
				$b.find(".grain-pick").removeClass("btn-primary").addClass("btn-default").text("Select");
				el.removeClass("btn-default").addClass("btn-primary").text("Selected");
				const supplier = el.data("supplier"), total = el.data("total");
				$b.find("#grain-compare").html(
					`<div style="display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:center">
						<div><div style="font-size:10px;text-transform:uppercase;color:var(--text-muted);font-weight:600">Truck / papers</div>
							<div style="font-weight:600">${_sr_esc(vehicle)}</div><div style="font-size:12px;color:var(--text-muted)">${_sr_esc(material)} · ${format_number(net || 0, null, 0)} Kg</div></div>
						<div style="font-weight:700;color:var(--text-color)">MATCH?</div>
						<div><div style="font-size:10px;text-transform:uppercase;color:var(--text-muted);font-weight:600">Selected PO</div>
							<div style="font-weight:600">${_sr_esc(supplier)}</div><div style="font-size:12px;color:var(--text-muted)">${_sr_esc(selected_po)} · ${format_currency(total)}</div></div>
					</div>`);
				$b.find("#grain-confirm-label").html(
					__("I confirm PO {0}'s supplier <b>{1}</b> matches the delivery papers for vehicle <b>{2}</b>.", [_sr_esc(selected_po), _sr_esc(supplier), _sr_esc(vehicle)]));
				$b.find("#grain-confirm").prop("checked", false);
				$b.find("#grain-confirm-wrap").show();
				dlg.disable_primary_action();

				// Received qty per item — pre-filled from the weighbridge weight,
				// editable for a partial delivery.
				$b.find("#grain-qty-area").html('<div style="font-size:12px;color:var(--text-muted);padding-top:10px">Loading received quantity…</div>');
				try {
					const pv = await frappe.call({ method: `${GRAIN_API}.get_grain_po_receipt_preview`, args: { token_name: token, po_name: selected_po } });
					const items = (pv.message && pv.message.items) || [];
					const rows = items.map(it => `<div style="display:flex;gap:10px;align-items:center;margin:6px 0">
							<div style="flex:1;font-size:12.5px">${_sr_esc(it.item_name || it.item_code)} <span style="color:var(--text-muted)">· ordered ${format_number(it.ordered_qty || 0)} ${_sr_esc(it.uom)}</span></div>
							<input type="number" min="0" step="any" class="form-control grain-qty" data-item="${_sr_esc(it.item_code)}" value="${it.suggested_qty || 0}" style="width:130px;text-align:right">
							<div style="width:56px;font-size:12px;color:var(--text-muted)">${_sr_esc(it.uom)}</div>
						</div>`).join("");
					$b.find("#grain-qty-area").html(`<div style="font-size:10px;text-transform:uppercase;letter-spacing:.3px;color:var(--text-muted);font-weight:600;padding-top:10px;margin-bottom:2px">Received Qty (this truck) — edit for a partial delivery</div>${rows}`);
				} catch (e) {
					$b.find("#grain-qty-area").html('<div style="font-size:12px;color:#b7791f;padding-top:10px">Could not load a suggested quantity — you can still link; it will default to the weighbridge weight.</div>');
				}
			});
		} catch (e) {
			$b.find("#grain-results").html(`<div class="sr-empty">${frappe.utils.escape_html(e.message || String(e))}</div>`);
		}
	}
	$b.find("#grain-search-btn").on("click", doSearch);
	$b.find("#grain-confirm").on("change", function () {
		if ($(this).is(":checked") && selected_po) dlg.enable_primary_action(); else dlg.disable_primary_action();
	});
	dlg.show();
	doSearch();
}

function _sr_render_section_d() {
	const rows = _sr_state.d;
	if (!rows.length) { $("#sr-d-body").html('<div class="sr-empty">No draft PRs pending.</div>'); return; }
	const html = [
		'<table class="sr-table"><thead><tr>',
		'<th>Purchase Receipt</th><th>Supplier</th><th>PO</th><th>Source</th>',
		'<th>Total</th><th>Items</th><th>Age</th><th>Action</th>',
		'</tr></thead><tbody>',
		...rows.map(r => `<tr data-search="${_sr_search_key(r)}">
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
		...rows.map(r => `<tr data-search="${_sr_search_key(r)}">
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
		...rows.map(r => `<tr data-search="${_sr_search_key(r)}">
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
		_sr_section_b_start($(this), $(this).data("token"));
	});
}

// Section B (Non-RM no-weighing) GRN creation — extracted so both the Section B
// table button and the Token Search result card can invoke the same flow.
function _sr_section_b_start(btn, token) {
	console.log("[stores-receiving] Section B start → token", token);
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

// ═══════════════════════════════════════════════════════════════════
//  TOKEN SEARCH (v6.2) — live in-page filter + server fallback lookup
// ═══════════════════════════════════════════════════════════════════

let _sr_search_q = "";

// Lowercased, attribute-safe key stashed on each row's data-search attribute.
function _sr_search_key(r) {
	const parts = [r.token, r.pr, r.vehicle, r.supplier, r.po];
	return frappe.utils.escape_html(parts.filter(Boolean).join(" ").toLowerCase());
}

function _sr_bind_search() {
	const $input = $("#sr-search-input");
	if (!$input.length) return;
	$input.off("input.sr").on("input.sr", function () {
		const q = String($(this).val() || "");
		_sr_search_q = q;
		$("#sr-search-clear").toggle(!!q);
		_sr_apply_filter(q);
	});
	$input.off("keydown.sr").on("keydown.sr", function (e) {
		if (e.key === "Enter") { e.preventDefault(); _sr_server_search(); }
	});
	$("#sr-search-clear").off("click.sr").on("click.sr", function () {
		$("#sr-search-input").val("");
		_sr_search_q = "";
		$(this).hide();
		_sr_apply_filter("");
		$("#sr-search-input").focus();
	});
}

// Pure client-side filter over already-rendered rows. No backend call.
function _sr_apply_filter(q) {
	const query = String(q || "").trim().toLowerCase();
	const $result = $("#sr-result");
	const $meta = $("#sr-search-meta");

	if (!query) {
		$("#sr-container tr[data-search]").removeClass("sr-row-hidden");
		$("#sr-container tr.sr-item-row").removeClass("sr-row-hidden");
		$("#sr-container .sr-section").removeClass("sr-section-filtered-empty");
		$meta.hide();
		$result.hide().empty();
		return;
	}

	let totalShown = 0, totalRows = 0;
	$("#sr-container .sr-section").each(function () {
		const $sec = $(this);
		let shown = 0;
		$sec.find("tr[data-search]").each(function () {
			const $row = $(this);
			totalRows++;
			const hit = ($row.attr("data-search") || "").indexOf(query) !== -1;
			$row.toggleClass("sr-row-hidden", !hit);
			const $next = $row.next("tr.sr-item-row");
			if ($next.length) $next.toggleClass("sr-row-hidden", !hit);
			if (hit) { shown++; totalShown++; }
		});
		const hasRows = $sec.find("tr[data-search]").length > 0;
		$sec.toggleClass("sr-section-filtered-empty", hasRows && shown === 0);
	});

	$("#sr-search-count").text(`Showing ${totalShown} of ${totalRows}`);
	$meta.show();

	if (totalShown === 0) {
		$result.html(
			`<div class="sr-result-fallback">
				<div class="sr-result-fallback-text">No pending token matches <strong class="sr-result-q">${_sr_esc(q)}</strong>. It may already be received, exited, or still upstream.</div>
				<button class="btn btn-sm btn-primary sr-result-search-all" id="sr-search-all">Search all tokens</button>
			</div>`
		).show();
		$("#sr-search-all").off("click").on("click", () => _sr_server_search());
	} else {
		$result.hide().empty();
	}
}

function _sr_reapply_filter() {
	if (_sr_search_q && String(_sr_search_q).trim()) {
		_sr_apply_filter(_sr_search_q);
	}
}

// Server fallback — look up ANY token in any state.
async function _sr_server_search() {
	const q = String($("#sr-search-input").val() || "").trim();
	const $result = $("#sr-result");
	if (!q) {
		frappe.show_alert({ message: "Type a token number, vehicle, supplier or PO first.", indicator: "orange" }, 4);
		return;
	}
	$result.html(`<div class="sr-result-loading"><span class="sr-spinner"></span> Searching all tokens for <strong class="sr-result-q">${_sr_esc(q)}</strong>…</div>`).show();
	try {
		const r = await frappe.call({ method: `${SR_API}.search_token`, args: { query: q } });
		_sr_render_search_result(r && r.message ? r.message : { count: 0, results: [], query: q });
	} catch (e) {
		console.error("[stores-receiving] search_token error:", e);
		const has_server_msg = e && (e._server_messages || (e.responseJSON && e.responseJSON._server_messages));
		if (has_server_msg) {
			$result.hide().empty();  // Frappe's native dialog already shows the server message
		} else {
			$result.html(`<div class="sr-result-error">Could not search tokens. <button class="btn btn-xs btn-default sr-result-retry">Retry</button></div>`).show();
			$result.find(".sr-result-retry").on("click", () => _sr_server_search());
		}
	}
}

function _sr_render_search_result(res) {
	const $result = $("#sr-result");
	const q = res.query || "";
	const results = res.results || [];

	if (!results.length) {
		$result.html(
			`<div class="sr-result-empty">No token found matching <strong class="sr-result-q">${_sr_esc(q)}</strong>. Check the token number, or open the <a href="/app/ts-token" target="_blank">Token list</a>.</div>`
		).show();
		return;
	}

	const cards = results.map(r => {
		const poLink = r.po ? `<a href="/app/purchase-order/${encodeURIComponent(r.po)}" target="_blank">${_sr_esc(r.po)}</a>` : "—";
		const createBtn = r.receivable
			? `<button class="btn btn-xs btn-success sr-result-create" data-token="${_sr_esc(r.token)}" data-path="${_sr_esc(r.grn_path || "")}">Create GRN</button>`
			: "";
		const note = (!r.receivable && r.reason)
			? `<div class="sr-result-note">${_sr_esc(r.reason)}</div>` : "";
		return `<div class="sr-result-card ${r.receivable ? "" : "sr-result-warn"}">
			<div class="sr-result-card-head">
				<div class="sr-result-token">
					<span class="sr-result-token-no">${_sr_esc(r.token)}</span>
					${_sr_status_badge(r.status)}
				</div>
				<span class="sr-result-found-tag">Found via full search</span>
			</div>
			<div class="sr-result-grid">
				<div class="sr-result-field"><div class="sr-result-lbl">Vehicle</div><div class="sr-result-val">${_sr_esc(r.vehicle) || "—"}</div></div>
				<div class="sr-result-field"><div class="sr-result-lbl">Supplier</div><div class="sr-result-val">${_sr_esc(r.supplier) || "—"}</div></div>
				<div class="sr-result-field"><div class="sr-result-lbl">PO</div><div class="sr-result-val">${poLink}</div></div>
				<div class="sr-result-field"><div class="sr-result-lbl">Inspection</div><div class="sr-result-val">${_sr_insp_badge(r.inspection_status)}</div></div>
				<div class="sr-result-field"><div class="sr-result-lbl">Age</div><div class="sr-result-val">${_sr_age(r.age_hours)}</div></div>
			</div>
			${note}
			<div class="sr-result-actions">
				<button class="btn btn-xs btn-default sr-open-token" data-token="${_sr_esc(r.token)}">Open Token</button>
				${createBtn}
			</div>
		</div>`;
	}).join("");

	const capNote = res.capped
		? `<div class="sr-search-meta" style="display:block">Showing first ${results.length} matches — refine your search to narrow down.</div>`
		: "";
	$result.html(cards + capNote).show();

	$result.find(".sr-open-token").on("click", function () {
		frappe.set_route("Form", "TS Token", $(this).data("token"));
	});
	$result.find(".sr-result-create").on("click", function () {
		const btn = $(this);
		const token = btn.data("token");
		const path = String(btn.data("path") || "");
		if (path === "A") {
			_sr_section_a_start(btn, token);
		} else if (path === "B") {
			_sr_section_b_start(btn, token);
		} else {
			frappe.msgprint({ title: "Cannot receive", message: "This token is not in a receivable state.", indicator: "orange" });
		}
	});
}

function _sr_status_badge(s) {
	const cls = {
		"Tare Weighed": "sr-ok", "Gross Weighed": "sr-warn",
		"Inside": "sr-warn", "Generated": "sr-muted", "Token Generated": "sr-muted",
		"GRN Created": "sr-muted", "Received": "sr-muted",
		"Exited": "sr-muted", "Plant Exited": "sr-muted", "Campus Exited": "sr-muted",
		"Rejected": "sr-bad", "Cancelled": "sr-bad",
	}[s] || "sr-muted";
	return `<span class="sr-status ${cls}">${_sr_esc(s || "-")}</span>`;
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
/* ── Grain Section G + Link-PO dialog (amber theme, dark-mode aware) ── */
.sr-section-grain { border-color:#f59e0b !important; box-shadow:0 0 0 1px #f59e0b inset; }
.sr-grain-head { background:#fffbeb !important; }
[data-theme="dark"] .sr-section-grain { border-color:#92400e !important; box-shadow:0 0 0 1px #92400e inset; }
[data-theme="dark"] .sr-grain-head { background:#78350f !important; }
.grain-compare-panel { background:#fffbeb; }
[data-theme="dark"] .grain-compare-panel { background:#78350f; }
@media (max-width: 768px) { .grain-compare-panel > div { grid-template-columns:1fr !important; text-align:center; } }
.sr-source-pill { display:inline-block; font-size:10px; padding:2px 8px; border-radius:10px; font-weight:500; }
.sr-source-b { background:#dbeafe; color:#1e40af; }
.sr-source-c { background:#dcfce7; color:#166534; }
[data-theme="dark"] .sr-source-b { background:rgba(30,64,175,0.2); color:#93c5fd; }
[data-theme="dark"] .sr-source-c { background:rgba(22,101,52,0.2); color:#86efac; }

/* ── Token Search (v6.2) ─────────────────────────────────────────── */
.sr-search { margin: -4px 0 18px; }
.sr-search-bar { position:relative; display:flex; align-items:center; background: var(--card-bg, #ffffff); border:1px solid var(--border-color, #e2e8f0); border-radius:10px; padding:0 12px; transition:border-color .12s, box-shadow .12s; }
.sr-search-bar:focus-within { border-color:#2563eb; box-shadow:0 0 0 3px rgba(37,99,235,0.12); }
.sr-search-icon { color: var(--text-muted, #94a3b8); flex:0 0 auto; }
.sr-search-input { flex:1 1 auto; border:0; outline:0; background:transparent; padding:12px 10px; font-size:14px; color: var(--text-color); }
.sr-search-input::placeholder { color: var(--text-muted, #94a3b8); }
.sr-search-clear { flex:0 0 auto; border:0; background:transparent; color: var(--text-muted, #94a3b8); font-size:22px; line-height:1; cursor:pointer; padding:0 4px; border-radius:6px; }
.sr-search-clear:hover { color: var(--text-color); background: var(--fg-color, #f1f5f9); }
.sr-search-meta { margin:8px 2px 0; font-size:12px; color: var(--text-muted); }
.sr-search-count { font-variant-numeric: tabular-nums; }
[data-theme="dark"] .sr-search-bar { background:#1f2937; border-color:#374151; }
[data-theme="dark"] .sr-search-bar:focus-within { border-color:#60a5fa; box-shadow:0 0 0 3px rgba(96,165,250,0.18); }
[data-theme="dark"] .sr-search-clear:hover { background:#111827; }
.sr-row-hidden { display:none !important; }
.sr-section-filtered-empty { display:none !important; }
.sr-result { margin-top:14px; }
.sr-result-fallback { display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap; background: var(--fg-color, #f8fafc); border:1px dashed var(--border-color, #cbd5e1); border-radius:10px; padding:14px 16px; }
.sr-result-fallback-text { font-size:13px; color: var(--text-muted); }
.sr-result-q { color: var(--text-color); }
[data-theme="dark"] .sr-result-fallback { background:#111827; border-color:#374151; }
.sr-result-loading { display:flex; align-items:center; gap:10px; font-size:13px; color: var(--text-muted); background: var(--card-bg, #ffffff); border:1px solid var(--border-color, #e2e8f0); border-radius:10px; padding:16px 18px; }
.sr-spinner { width:15px; height:15px; border:2px solid var(--border-color, #e2e8f0); border-top-color:#2563eb; border-radius:50%; display:inline-block; animation:sr-spin .7s linear infinite; }
@keyframes sr-spin { to { transform:rotate(360deg); } }
[data-theme="dark"] .sr-result-loading { background:#1f2937; border-color:#374151; }
[data-theme="dark"] .sr-spinner { border-color:#374151; border-top-color:#60a5fa; }
.sr-result-card { background: var(--card-bg, #ffffff); border:1px solid var(--border-color, #e2e8f0); border-left:4px solid #2563eb; border-radius:10px; padding:16px 18px; margin-bottom:10px; }
.sr-result-card.sr-result-warn { border-left-color:#f59e0b; }
.sr-result-card-head { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
.sr-result-token { display:flex; align-items:center; gap:10px; }
.sr-result-token-no { font-size:16px; font-weight:700; color: var(--text-color); }
.sr-result-found-tag { font-size:10px; text-transform:uppercase; letter-spacing:0.4px; color: var(--text-muted); background: var(--fg-color, #f1f5f9); border-radius:10px; padding:3px 9px; }
.sr-status { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:500; }
.sr-result-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px 18px; margin-bottom:12px; }
.sr-result-lbl { font-size:10px; text-transform:uppercase; letter-spacing:0.4px; color: var(--text-muted); margin-bottom:3px; }
.sr-result-val { font-size:13px; color: var(--text-color); }
.sr-result-note { font-size:12px; color: var(--text-muted); background: var(--fg-color, #f8fafc); border-radius:8px; padding:8px 12px; margin-bottom:12px; }
.sr-result-actions { display:flex; gap:8px; flex-wrap:wrap; }
[data-theme="dark"] .sr-result-card { background:#1f2937; border-color:#374151; border-left-color:#60a5fa; }
[data-theme="dark"] .sr-result-card.sr-result-warn { border-left-color:#fbbf24; }
[data-theme="dark"] .sr-result-found-tag { background:#374151; color:#9ca3af; }
[data-theme="dark"] .sr-result-note { background:#111827; }
.sr-status.sr-ok { background:#dcfce7; color:#166534; }
.sr-status.sr-warn { background:#fef3c7; color:#92400e; }
.sr-status.sr-bad { background:#fee2e2; color:#991b1b; }
.sr-status.sr-muted { background:#f1f5f9; color:#64748b; }
[data-theme="dark"] .sr-status.sr-ok { background:rgba(22,163,74,0.2); color:#86efac; }
[data-theme="dark"] .sr-status.sr-warn { background:rgba(217,119,6,0.2); color:#fcd34d; }
[data-theme="dark"] .sr-status.sr-bad { background:rgba(239,68,68,0.2); color:#fca5a5; }
[data-theme="dark"] .sr-status.sr-muted { background:rgba(100,116,139,0.2); color:#cbd5e1; }
.sr-result-empty { font-size:13px; color: var(--text-muted); background: var(--card-bg, #ffffff); border:1px solid var(--border-color, #e2e8f0); border-radius:10px; padding:16px 18px; text-align:center; }
.sr-result-error { display:flex; align-items:center; justify-content:center; gap:10px; font-size:13px; color:#991b1b; background:#fee2e2; border:1px solid #fecaca; border-radius:10px; padding:14px 18px; }
[data-theme="dark"] .sr-result-empty { background:#1f2937; border-color:#374151; }
[data-theme="dark"] .sr-result-error { background:rgba(239,68,68,0.12); border-color:rgba(239,68,68,0.3); color:#fca5a5; }
@media (max-width: 768px) {
  .sr-search { margin-top:0; }
  .sr-search-input { font-size:16px; padding:11px 8px; }
  .sr-result-fallback { flex-direction:column; align-items:stretch; }
  .sr-result-fallback .sr-result-search-all { width:100%; }
  .sr-result-grid { grid-template-columns:repeat(2,1fr); gap:10px 12px; }
  .sr-result-card-head { align-items:flex-start; }
  .sr-result-actions .btn { flex:1 1 auto; }
  /* v2.18.0: Section B action cell now holds 3 buttons (Open / Create GRN /
     Exit Approved). .sr-section is overflow:hidden, so let the inner table
     scroll horizontally on phones instead of wrapping/cramping the buttons. */
  .sr-body { overflow-x:auto; -webkit-overflow-scrolling:touch; }
  .sr-table { min-width:720px; }
  .sr-table td:last-child { white-space:nowrap; }
  .sr-table td:last-child .btn { margin-bottom:2px; }
}
`;
