frappe.pages["md-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "MD Dashboard",
		single_column: true,
	});

	page.main.html('<div id="md-app" style="padding: 15px;"></div>');

	page.add_button(__("Wall Display"), () => {
		frappe.set_route("md-wall-display");
	}, { icon: "monitor" }).addClass("btn-dark");

	page.add_field({
		fieldname: "date_range",
		label: __("Period"),
		fieldtype: "Select",
		options: "Today\nThis Week\nThis Month\nThis Quarter\nThis Year",
		default: "This Month",
		change() { _md_refresh(page); },
	});

	setTimeout(() => _md_refresh(page), 500);
};

frappe.pages["md-dashboard"].refresh = function (wrapper) {
	_md_refresh(wrapper.page);
};

let _md_timeout = null;
function _md_refresh(page) {
	clearTimeout(_md_timeout);
	_md_timeout = setTimeout(() => _md_do_refresh(page), 300);
}

function _md_do_refresh(page) {
	const date_range = page.fields_dict.date_range?.get_value() || "This Month";
	const $c = $("#md-app");
	$c.html('<div style="text-align:center;padding:80px;color:#94a3b8;font-size:14px;">Loading MD Dashboard...</div>');

	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_dashboard_md.get_md_dashboard",
		args: { date_range },
		callback(r) {
			if (!r.message) {
				$c.html('<div style="text-align:center;padding:80px;color:#94a3b8;">No data available.</div>');
				return;
			}
			_md_render(r.message, $c);
		},
		error() {
			$c.html('<div style="text-align:center;padding:80px;color:#ef4444;">Error loading dashboard. Please refresh.</div>');
		},
	});
}

/* ══════════════════════════════════════════════════════════════════════
   MAIN RENDER
   ══════════════════════════════════════════════════════════════════════ */

function _md_render(data, $c) {
	let html = "";
	const co = data.company_overview;

	// ── Hero Header ──
	html += `<div class="md-hero md-animate">
		<div>
			<div class="md-hero-title">Betul Bio Fuel Pvt Ltd</div>
			<div class="md-hero-sub">Managing Director's Executive Dashboard</div>
		</div>
		<div class="md-hero-right">
			<div class="md-hero-date">${_md_esc(data.filters_applied.date_range)} &middot; ${_md_esc(data.filters_applied.start)} to ${_md_esc(data.filters_applied.end)}</div>
			<div class="md-hero-time md-clock"></div>
		</div>
	</div>`;

	// ── KPI Cards (5) ──
	html += '<div class="md-kpi-grid md-animate md-animate-d1">';
	html += _md_kpi("My Approvals", co.md_pending, co.md_pending > 0 ? "Needs attention" : "All clear", "#ef4444", co.md_pending > 0 ? "#fef2f2" : "#ecfdf5");
	html += _md_kpi("Vehicles Inside", co.vehicles_inside, co.today_inward + " entered today", "#8b5cf6", "#faf5ff");
	html += _md_kpi("Today's Weight", co.today_weight_mt + " MT", co.today_inward + " vehicles weighed", "#3b82f6", "#eff6ff");
	html += _md_kpi("Avg Turnaround", _md_fmt_time(co.avg_turnaround_min), "today's average", "#0ea5e9", "#f0f9ff");
	html += _md_kpi("Stuck Vehicles", co.stuck_vehicles, "CTL breaches", co.stuck_vehicles > 0 ? "#ef4444" : "#10b981", co.stuck_vehicles > 0 ? "#fef2f2" : "#ecfdf5");
	html += "</div>";

	// ── Company Comparison ──
	if (co.companies && co.companies.length > 1) {
		html += '<div class="md-company-grid md-animate md-animate-d2">';
		co.companies.forEach((c) => {
			html += _md_company_card(c);
		});
		html += "</div>";
	} else if (co.companies && co.companies.length === 1) {
		html += '<div class="md-animate md-animate-d2">' + _md_company_card(co.companies[0]) + '</div>';
	}

	// ── Action Items ──
	const actions = data.action_items;
	if (actions.count > 0) {
		html += '<div class="md-section md-animate md-animate-d2" style="border-left:4px solid #ef4444;">';
		html += `<div class="md-section-head">
			<div class="md-section-title">Pending MD Approvals</div>
			<span class="md-section-badge" style="background:#fef2f2;color:#ef4444;">${actions.count} pending</span>
		</div>`;
		html += _md_action_table(actions.pos);
		html += "</div>";
	}

	// ── Non-Branded Items Alert ──
	const nbi = data.non_branded_items;
	if (nbi && nbi.count > 0) {
		html += '<div class="md-section md-animate md-animate-d2 md-flash-alert" style="border-left:4px solid #f59e0b;background:linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);">';
		html += `<div class="md-section-head">
			<div class="md-section-title" style="color:#92400e;">&#9888; Non-Branded Items</div>
			<span class="md-section-badge md-flash-badge" style="background:#fef2f2;color:#dc2626;font-weight:700;">${nbi.count} items without brand</span>
		</div>`;
		html += '<div style="font-size:12px;color:#92400e;margin-bottom:10px;">These items have no Brand assigned. Sorted by most purchased.</div>';
		html += '<table class="md-action-table"><thead><tr>';
		html += '<th>Item Code</th><th>Item Name</th><th>Item Group</th><th style="text-align:right;">POs</th>';
		html += '</tr></thead><tbody>';
		nbi.items.forEach((item, idx) => {
			const hidden = idx >= 5 ? ' class="md-nbi-extra" style="display:none;cursor:pointer;"' : ' style="cursor:pointer;"';
			html += `<tr${hidden} onclick="frappe.set_route('item', '${_md_esc(item.name)}')">
				<td style="font-weight:600;color:#1e40af;">${_md_esc(item.name)}</td>
				<td>${_md_esc(item.item_name)}</td>
				<td>${_md_esc(item.item_group)}</td>
				<td style="text-align:right;font-weight:600;">${item.po_count || 0}</td>
			</tr>`;
		});
		html += '</tbody></table>';
		if (nbi.count > 5) {
			html += `<div style="text-align:center;margin-top:8px;">
				<button class="btn btn-xs btn-default" id="md-nbi-toggle" onclick="_md_toggle_nbi()" style="color:#92400e;border-color:#f59e0b;">
					Show ${nbi.count - 5} More Items ▼
				</button>
			</div>`;
		}
		html += '</div>';
	}

	// ── Two Column: Gate Operations + Stage Distribution ──
	html += '<div class="md-grid-2 md-animate md-animate-d3">';

	// Left: Gate operations
	html += '<div class="md-section" style="margin-bottom:0;">';
	html += '<div class="md-section-head"><div class="md-section-title">Today\'s Gate Activity</div></div>';
	html += _md_gate_ops(data.gate_operations);
	html += "</div>";

	// Right: Stage distribution
	html += '<div class="md-section" style="margin-bottom:0;">';
	html += '<div class="md-section-head"><div class="md-section-title">Live Vehicle Distribution</div></div>';
	html += _md_stage_pills(data.gate_operations.stages);
	html += "</div>";

	html += "</div>";

	// ── Procurement + Budget ──
	html += '<div class="md-grid-2 md-animate md-animate-d3">';

	html += '<div class="md-section" style="margin-bottom:0;">';
	html += '<div class="md-section-head"><div class="md-section-title">Procurement Pipeline</div>';
	html += `<span class="md-section-badge" style="background:#ecfdf5;color:#10b981;">${format_currency(data.procurement.total_approved_value)} approved</span>`;
	html += '</div>';
	html += _md_po_pipeline(data.procurement.pipeline);
	html += "</div>";

	html += '<div class="md-section" style="margin-bottom:0;">';
	html += '<div class="md-section-head"><div class="md-section-title">Budget Health</div></div>';
	html += _md_budget(data.budget);
	html += "</div>";

	html += "</div>";

	// ── Charts Row ──
	html += '<div class="md-grid-2 md-animate md-animate-d4">';

	html += '<div class="md-section" style="margin-bottom:0;">';
	html += '<div class="md-section-head"><div class="md-section-title">Monthly PO Trend (6 months)</div></div>';
	html += '<div id="md-po-chart" style="height:250px;"></div>';
	html += "</div>";

	html += '<div class="md-section" style="margin-bottom:0;">';
	html += '<div class="md-section-head"><div class="md-section-title">Monthly Material Inward (MT)</div></div>';
	html += '<div id="md-weight-chart" style="height:250px;"></div>';
	html += "</div>";

	html += "</div>";

	// ── Quality + Suppliers ──
	html += '<div class="md-grid-2 md-animate md-animate-d4">';

	html += '<div class="md-section" style="margin-bottom:0;">';
	html += '<div class="md-section-head"><div class="md-section-title">Quality Overview</div></div>';
	html += _md_quality(data.quality);
	html += "</div>";

	html += '<div class="md-section" style="margin-bottom:0;">';
	html += '<div class="md-section-head"><div class="md-section-title">Top 10 Suppliers</div></div>';
	html += _md_suppliers(data.top_suppliers);
	html += "</div>";

	html += "</div>";

	// ── Footer ──
	html += `<div class="md-footer">Last updated: ${frappe.datetime.prettyDate(data.last_updated)}</div>`;

	$c.html(html);

	// Charts
	_md_render_po_chart(data.monthly_trend.po);
	_md_render_weight_chart(data.monthly_trend.weight);

	// Clock
	_md_start_clock();
}

/* ══════════════════════════════════════════════════════════════════════
   KPI CARD
   ══════════════════════════════════════════════════════════════════════ */

function _md_kpi(label, value, sub, color, bg) {
	return `<div class="md-kpi">
		<div class="md-kpi-accent" style="background:${color};"></div>
		<div class="md-kpi-icon" style="background:${color}15;color:${color};">${_md_kpi_icon(label)}</div>
		<div class="md-kpi-label">${label}</div>
		<div class="md-kpi-value" style="color:${color};">${value}</div>
		<div class="md-kpi-sub">${sub}</div>
	</div>`;
}

function _md_kpi_icon(label) {
	const icons = {
		"My Approvals": "&#9888;", "Vehicles Inside": "&#9679;", "Today's Weight": "&#9878;",
		"Avg Turnaround": "&#9202;", "Stuck Vehicles": "&#9888;",
	};
	return icons[label] || "&#9632;";
}

/* ══════════════════════════════════════════════════════════════════════
   COMPANY COMPARISON CARDS
   ══════════════════════════════════════════════════════════════════════ */

function _md_company_card(c) {
	const bcolor = c.budget_pct > 80 ? "#ef4444" : c.budget_pct > 60 ? "#f59e0b" : "#10b981";
	return `<div class="md-company-card">
		<div class="md-company-name">${_md_esc(c.short_name)} <span style="font-size:11px;color:#9ca3af;font-weight:400;">${_md_esc(c.company)}</span></div>
		<div class="md-co-stats">
			<div class="md-co-stat">
				<div class="md-co-stat-val" style="color:#10b981;">${format_currency(c.po_value)}</div>
				<div class="md-co-stat-label">PO Value</div>
			</div>
			<div class="md-co-stat">
				<div class="md-co-stat-val" style="color:#3b82f6;">${format_currency(c.pi_value)}</div>
				<div class="md-co-stat-label">Invoice Value</div>
			</div>
			<div class="md-co-stat">
				<div class="md-co-stat-val" style="color:#8b5cf6;">${c.pr_count}</div>
				<div class="md-co-stat-label">GRNs</div>
			</div>
		</div>
		<div style="margin-top:12px;">
			<div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;">
				<span style="color:#6b7280;">Budget</span>
				<span style="color:${bcolor};font-weight:700;">${c.budget_pct}% used</span>
			</div>
			<div class="md-budget-bar"><div class="md-budget-fill" style="width:${Math.min(c.budget_pct, 100)}%;background:${bcolor};"></div></div>
			<div style="font-size:10px;color:#9ca3af;margin-top:4px;">${format_currency(c.budget_used)} of ${format_currency(c.budget_total)}</div>
		</div>
		${c.pending_pos > 0 ? `<div style="margin-top:10px;padding:6px 10px;background:#fef2f2;border-radius:8px;font-size:11px;color:#ef4444;font-weight:600;">${c.pending_pos} POs pending approval</div>` : ""}
	</div>`;
}

/* ══════════════════════════════════════════════════════════════════════
   ACTION TABLE
   ══════════════════════════════════════════════════════════════════════ */

function _md_action_table(pos) {
	if (!pos || !pos.length) return '<div style="padding:16px;text-align:center;color:#10b981;">No pending approvals</div>';

	let html = '<table class="md-action-table"><thead><tr>';
	html += '<th>Purchase Order</th><th>Supplier</th><th>Company</th><th style="text-align:right;">Amount</th><th style="text-align:center;">Category</th><th style="text-align:right;">Waiting</th>';
	html += "</tr></thead><tbody>";

	pos.forEach((po) => {
		const wc = po.waiting_hours > 24 ? "#ef4444" : po.waiting_hours > 8 ? "#f59e0b" : "#6b7280";
		html += `<tr onclick="frappe.set_route('Form','Purchase Order','${_md_esc(po.name)}')">`;
		html += `<td><a style="color:#3b82f6;font-weight:600;">${_md_esc(po.name)}</a></td>`;
		html += `<td>${_md_esc(po.supplier_name)}</td>`;
		html += `<td style="font-size:11px;color:#6b7280;">${_md_esc(po.company || "")}</td>`;
		html += `<td style="text-align:right;font-weight:700;">${format_currency(po.grand_total)}</td>`;
		html += `<td style="text-align:center;"><span style="background:#e0e7ff;color:#3730a3;padding:2px 8px;border-radius:10px;font-size:11px;">${_md_esc(po.category || "—")}</span></td>`;
		html += `<td style="text-align:right;color:${wc};font-weight:600;">${_md_fmt_wait(po.waiting_hours)}</td>`;
		html += "</tr>";
	});
	html += "</tbody></table>";
	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   GATE OPERATIONS
   ══════════════════════════════════════════════════════════════════════ */

function _md_gate_ops(ops) {
	let html = '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:16px;">';
	const items = [
		{ l: "Entries", v: ops.entries, c: "#3b82f6" },
		{ l: "Exits", v: ops.exits, c: "#10b981" },
		{ l: "Stock IN", v: ops.stock_in, c: "#8b5cf6" },
		{ l: "Stock OUT", v: ops.stock_out, c: "#f59e0b" },
		{ l: "Visitors", v: ops.visitors, c: "#ec4899" },
	];
	items.forEach((i) => {
		html += `<div class="md-trend-card"><div class="md-trend-val" style="color:${i.c};">${i.v}</div><div class="md-trend-label">${i.l}</div></div>`;
	});
	html += "</div>";

	// Weight summary
	html += `<div style="display:flex;gap:12px;">
		<div style="flex:1;padding:12px;background:#eff6ff;border-radius:10px;text-align:center;">
			<div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Weight IN</div>
			<div style="font-size:22px;font-weight:700;color:#3b82f6;">${ops.weight_in_mt} MT</div>
		</div>
		<div style="flex:1;padding:12px;background:#faf5ff;border-radius:10px;text-align:center;">
			<div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Weight OUT</div>
			<div style="font-size:22px;font-weight:700;color:#8b5cf6;">${ops.weight_out_mt} MT</div>
		</div>
	</div>`;

	return html;
}

function _md_stage_pills(stages) {
	if (!stages || !stages.length) return '<div style="padding:20px;text-align:center;color:#9ca3af;">No vehicles inside campus</div>';

	const colors = {
		"PO Linked": "#3b82f6", "Gross Weighed": "#8b5cf6", "Quality Done": "#6366f1",
		"Graded": "#a855f7", "Unloading": "#f59e0b", "Tare Weighed": "#10b981",
		"GRN Created": "#059669", "SI Linked": "#0ea5e9", "Tare Recorded": "#06b6d4",
		"Loading Done": "#14b8a6", "Gross Recorded": "#10b981", "Dispatch Ready": "#22c55e",
	};

	const total = stages.reduce((s, r) => s + r.count, 0);
	let html = '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px;">';
	stages.forEach((s) => {
		const c = colors[s.stage] || "#6b7280";
		html += `<span class="md-stage-pill" style="background:${c}12;color:${c};border:1px solid ${c}30;">
			<span class="md-stage-dot" style="background:${c};"></span>
			${_md_esc(s.stage)} <b>${s.count}</b>
		</span>`;
	});
	html += "</div>";

	// Visual bar
	html += '<div style="display:flex;height:28px;border-radius:8px;overflow:hidden;background:#f1f5f9;">';
	stages.forEach((s) => {
		const pct = (s.count / total) * 100;
		const c = colors[s.stage] || "#6b7280";
		if (pct > 0) {
			html += `<div style="width:${pct}%;background:${c};display:flex;align-items:center;justify-content:center;font-size:10px;color:white;font-weight:700;" title="${_md_esc(s.stage)}: ${s.count}">${s.count > 0 ? s.count : ""}</div>`;
		}
	});
	html += "</div>";
	html += `<div style="text-align:center;margin-top:10px;font-size:13px;font-weight:700;color:#0f172a;">${total} vehicles inside campus</div>`;
	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   PROCUREMENT PIPELINE
   ══════════════════════════════════════════════════════════════════════ */

function _md_po_pipeline(pipeline) {
	if (!pipeline || !pipeline.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No PO data</div>';

	const sts = ["Draft", "Pending", "Awaiting", "Approved", "Rejected"];
	const cols = { Draft: "#6b7280", Pending: "#f59e0b", Awaiting: "#3b82f6", Approved: "#10b981", Rejected: "#ef4444" };

	let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>';
	html += '<th style="padding:8px;text-align:left;border-bottom:2px solid #e5e7eb;font-size:10px;text-transform:uppercase;color:#6b7280;">Category</th>';
	sts.forEach((s) => {
		html += `<th style="padding:8px;text-align:center;border-bottom:2px solid #e5e7eb;font-size:10px;text-transform:uppercase;color:${cols[s]};">${s}</th>`;
	});
	html += "</tr></thead><tbody>";

	pipeline.forEach((row) => {
		let total = 0;
		html += "<tr>";
		html += `<td style="padding:8px;font-weight:600;border-bottom:1px solid #f1f5f9;">${_md_esc(row.category)}</td>`;
		sts.forEach((s) => {
			const v = row[s] || 0;
			total += v;
			const badge = v > 0
				? `<span style="display:inline-block;min-width:24px;text-align:center;background:${cols[s]}15;color:${cols[s]};padding:3px 8px;border-radius:12px;font-weight:700;">${v}</span>`
				: '<span style="color:#d1d5db;">—</span>';
			html += `<td style="padding:8px;text-align:center;border-bottom:1px solid #f1f5f9;">${badge}</td>`;
		});
		html += "</tr>";
	});
	html += "</tbody></table>";
	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   BUDGET
   ══════════════════════════════════════════════════════════════════════ */

function _md_budget(budget) {
	if (!budget.companies || !budget.companies.length) {
		return '<div style="color:#9ca3af;padding:20px;text-align:center;">No budget data</div>';
	}

	let html = "";

	// Overall totals
	const t = budget.totals;
	const tc = t.pct > 80 ? "#ef4444" : t.pct > 60 ? "#f59e0b" : "#10b981";
	html += `<div style="display:flex;gap:10px;margin-bottom:16px;">
		<div style="flex:1;padding:10px;background:#f8fafc;border-radius:8px;text-align:center;">
			<div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Total Budget</div>
			<div style="font-size:16px;font-weight:700;color:#374151;">${format_currency(t.budget)}</div>
		</div>
		<div style="flex:1;padding:10px;background:${tc}08;border-radius:8px;text-align:center;">
			<div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Utilization</div>
			<div style="font-size:16px;font-weight:700;color:${tc};">${t.pct}%</div>
		</div>
	</div>`;

	// Per company
	budget.companies.forEach((c) => {
		const color = c.pct > 80 ? "#ef4444" : c.pct > 60 ? "#f59e0b" : "#10b981";
		html += `<div style="padding:10px;background:#f8fafc;border-radius:8px;margin-bottom:8px;">
			<div style="display:flex;justify-content:space-between;margin-bottom:6px;">
				<span style="font-size:12px;font-weight:600;color:#0f172a;">${_md_esc(c.short_name)}</span>
				<span style="font-size:12px;font-weight:700;color:${color};">${c.pct}%</span>
			</div>
			<div class="md-budget-bar"><div class="md-budget-fill" style="width:${Math.min(c.pct, 100)}%;background:${color};"></div></div>
			<div style="display:flex;justify-content:space-between;font-size:10px;color:#9ca3af;margin-top:4px;">
				<span>Committed: ${format_currency(c.committed)}</span>
				<span>Actual: ${format_currency(c.actual)}</span>
			</div>
		</div>`;
	});

	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   QUALITY
   ══════════════════════════════════════════════════════════════════════ */

function _md_quality(q) {
	let html = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px;">';
	html += `<div class="md-trend-card"><div class="md-trend-val" style="color:#374151;">${q.total}</div><div class="md-trend-label">Total QI</div></div>`;
	html += `<div class="md-trend-card"><div class="md-trend-val" style="color:#10b981;">${q.accepted}</div><div class="md-trend-label">Accepted</div></div>`;
	html += `<div class="md-trend-card"><div class="md-trend-val" style="color:#ef4444;">${q.rejected}</div><div class="md-trend-label">Rejected</div></div>`;
	html += "</div>";

	// Reject rate
	html += `<div style="padding:14px;background:${q.reject_rate > 5 ? '#fef2f2' : '#ecfdf5'};border-radius:10px;text-align:center;margin-bottom:14px;">
		<div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Rejection Rate</div>
		<div style="font-size:32px;font-weight:800;color:${q.reject_rate > 5 ? '#ef4444' : '#10b981'};">${q.reject_rate}%</div>
	</div>`;

	// By category
	const cats = q.by_category || [];
	if (cats.length) {
		cats.forEach((c) => {
			html += `<div style="padding:10px;background:#f8fafc;border-radius:8px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;">
				<span style="font-weight:600;font-size:12px;">${_md_esc(c.item_category)} (${c.cnt})</span>
				<div style="display:flex;gap:16px;">
					${c.avg_gcv ? `<span style="font-size:11px;">GCV: <b style="color:#3b82f6;">${c.avg_gcv}</b></span>` : ""}
					<span style="font-size:11px;">Moisture: <b style="color:${(c.avg_moisture || 0) > 12 ? '#ef4444' : '#10b981'};">${c.avg_moisture || 0}%</b></span>
				</div>
			</div>`;
		});
	}

	// Material Inspection
	const mi = q.material_inspection || {};
	const mi_total = Object.values(mi).reduce((s, v) => s + v, 0);
	if (mi_total > 0) {
		html += '<div style="margin-top:12px;font-size:11px;font-weight:600;color:#374151;margin-bottom:6px;">Material Inspection (Non-RM)</div>';
		html += '<div style="display:flex;gap:4px;flex-wrap:wrap;">';
		const mc = { "Pending Inspection": "#f59e0b", Approved: "#10b981", "Partially Approved": "#3b82f6", Rejected: "#ef4444", "On Hold": "#6b7280", "Auto-Proceeded": "#9ca3af" };
		Object.entries(mi).forEach(([status, cnt]) => {
			const color = mc[status] || "#6b7280";
			html += `<span style="padding:3px 8px;background:${color}12;color:${color};border-radius:8px;font-size:10px;font-weight:600;">${status}: ${cnt}</span>`;
		});
		html += "</div>";
	}

	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   SUPPLIERS
   ══════════════════════════════════════════════════════════════════════ */

function _md_suppliers(suppliers) {
	if (!suppliers || !suppliers.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No supplier data</div>';

	const max_val = suppliers[0]?.total_value || 1;
	const rank_colors = ["#f59e0b", "#94a3b8", "#b45309", "#6b7280", "#6b7280", "#6b7280", "#6b7280", "#6b7280", "#6b7280", "#6b7280"];

	let html = "";
	suppliers.forEach((s, i) => {
		const pct = ((s.total_value || 0) / max_val) * 100;
		const rc = rank_colors[i] || "#6b7280";
		html += `<div class="md-supplier-row">
			<div class="md-supplier-rank" style="background:${rc};">${i + 1}</div>
			<div style="flex:1;min-width:0;">
				<div style="display:flex;justify-content:space-between;margin-bottom:3px;">
					<span style="font-size:12px;font-weight:500;color:#374151;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_md_esc(s.supplier_name)}</span>
					<span style="font-size:11px;color:#6b7280;flex-shrink:0;margin-left:8px;">${s.po_count} POs</span>
				</div>
				<div class="md-supplier-bar"><div class="md-supplier-fill" style="width:${pct}%;background:#3b82f6;"></div></div>
				<div style="font-size:11px;color:#3b82f6;font-weight:600;margin-top:2px;">${format_currency(s.total_value)}</div>
			</div>
		</div>`;
	});
	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   CHARTS
   ══════════════════════════════════════════════════════════════════════ */

function _md_render_po_chart(trend) {
	const $el = document.getElementById("md-po-chart");
	if (!$el || !trend || !trend.length) {
		if ($el) $el.innerHTML = '<div style="color:#9ca3af;padding:40px;text-align:center;">No trend data</div>';
		return;
	}

	const values = trend.map((t) => Math.round((t.value || 0) / 100000));
	new frappe.Chart($el, {
		data: { labels: trend.map((t) => t.month), datasets: [{ name: "PO Value (Lakhs)", values }] },
		type: "bar",
		height: 220,
		colors: ["#3b82f6"],
		barOptions: { spaceRatio: 0.4 },
		tooltipOptions: { formatTooltipY: (d) => "₹ " + d.toLocaleString("en-IN") + " L" },
	});
}

function _md_render_weight_chart(trend) {
	const $el = document.getElementById("md-weight-chart");
	if (!$el || !trend || !trend.length) {
		if ($el) $el.innerHTML = '<div style="color:#9ca3af;padding:40px;text-align:center;">No weight data</div>';
		return;
	}

	new frappe.Chart($el, {
		data: { labels: trend.map((t) => t.month), datasets: [{ name: "Weight (MT)", values: trend.map((t) => t.mt || 0) }] },
		type: "bar",
		height: 220,
		colors: ["#10b981"],
		barOptions: { spaceRatio: 0.4 },
		tooltipOptions: { formatTooltipY: (d) => d + " MT" },
	});
}

/* ══════════════════════════════════════════════════════════════════════
   HELPERS
   ══════════════════════════════════════════════════════════════════════ */

function _md_fmt_time(min) {
	if (!min || min <= 0) return "—";
	const h = Math.floor(min / 60);
	const m = Math.round(min % 60);
	return h > 0 ? h + "h " + m + "m" : m + "m";
}

function _md_fmt_wait(hours) {
	if (!hours || hours <= 0) return "Just now";
	if (hours < 1) return Math.round(hours * 60) + "m";
	if (hours < 24) return Math.round(hours) + "h";
	return Math.round(hours / 24) + "d " + Math.round(hours % 24) + "h";
}

function _md_esc(v) { return frappe.utils.escape_html(v || ""); }

function _md_toggle_nbi() {
	const rows = document.querySelectorAll(".md-nbi-extra");
	const btn = document.getElementById("md-nbi-toggle");
	const showing = rows[0] && rows[0].style.display !== "none";
	rows.forEach((r) => { r.style.display = showing ? "none" : ""; r.style.cursor = "pointer"; });
	btn.innerHTML = showing ? `Show ${rows.length} More Items &#9660;` : "Show Less &#9650;";
}

let _md_clock_interval = null;
function _md_start_clock() {
	clearInterval(_md_clock_interval);
	const tick = () => {
		const $cl = $(".md-clock");
		if ($cl.length) {
			$cl.text(new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
		}
	};
	tick();
	_md_clock_interval = setInterval(tick, 1000);
}
