frappe.pages["ceo-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "CEO Dashboard",
		single_column: true,
	});

	page.main.html('<div id="ceo-dash-container" style="padding: 15px;"></div>');

	page.add_field({
		fieldname: "date_range",
		label: __("Period"),
		fieldtype: "Select",
		options: "Today\nThis Week\nThis Month\nThis Quarter\nThis Year",
		default: "This Month",
		change() { _cd_refresh(page); },
	});

	page.add_field({
		fieldname: "company",
		label: __("Company"),
		fieldtype: "Link",
		options: "Company",
		default: frappe.defaults.get_global_default("company"),
		change() { _cd_refresh(page); },
	});

	setTimeout(() => _cd_refresh(page), 500);
};

frappe.pages["ceo-dashboard"].refresh = function (wrapper) {
	_cd_refresh(wrapper.page);
};

let _cd_timeout = null;
function _cd_refresh(page) {
	clearTimeout(_cd_timeout);
	_cd_timeout = setTimeout(() => _cd_do_refresh(page), 300);
}

function _cd_do_refresh(page) {
	const date_range = page.fields_dict.date_range?.get_value() || "This Month";
	const company = page.fields_dict.company?.get_value();
	const $c = $("#ceo-dash-container");
	$c.html('<div style="text-align:center;padding:60px;color:#9ca3af;font-size:14px;">Loading CEO Dashboard...</div>');

	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_dashboard_ceo.get_ceo_dashboard",
		args: { date_range, company },
		callback(r) {
			if (!r.message) {
				$c.html('<div style="text-align:center;padding:60px;color:#9ca3af;">No data available.</div>');
				return;
			}
			_cd_render(r.message, $c);
		},
		error() {
			$c.html('<div style="text-align:center;padding:60px;color:#ef4444;">Error loading dashboard. Please refresh.</div>');
		},
	});
}

/* ══════════════════════════════════════════════════════════════════════
   MAIN RENDER
   ══════════════════════════════════════════════════════════════════════ */

function _cd_render(data, $c) {
	let html = "";

	// KPI Cards (8)
	html += _cd_kpi_cards(data.kpis);

	// Section 1: Action Items
	html += _cd_section("My Action Items", _cd_action_items(data.action_items), "#ef4444");

	// Section 2: Daily Operations (two columns)
	html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px;">';
	html += _cd_card("Today's Operations", _cd_daily_ops(data.daily_ops));
	html += _cd_card("Vehicle Distribution by Stage", _cd_stage_bars(data.daily_ops.stages));
	html += "</div>";

	// Section 3: Procurement Health (two columns)
	html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px;">';
	html += _cd_card("PO Pipeline by Category", _cd_po_pipeline(data.procurement.po_pipeline));
	html += _cd_card("Monthly PO Trend (6 months)", '<div id="cd-monthly-chart" style="height:250px;"></div>');
	html += "</div>";

	// Section 4: Budget + Top Suppliers
	html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px;">';
	html += _cd_card("Budget Utilization by Cost Center", _cd_budget_bars(data.budget_overview));
	html += _cd_card("Top Suppliers by Value", _cd_suppliers(data.procurement.top_suppliers));
	html += "</div>";

	// Section 5: Quality + Material Inspection
	html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px;">';
	html += _cd_card("Quality Inspection Summary", _cd_quality(data.quality));
	html += _cd_card("Quality Metrics by Category", _cd_quality_metrics(data.quality));
	html += "</div>";

	// Budget Overrides
	if (data.budget_overview.overrides && data.budget_overview.overrides.length) {
		html += _cd_section("Recent Budget Overrides", _cd_overrides(data.budget_overview.overrides), "#f59e0b");
	}

	// Footer
	html += `<div style="text-align:center;padding:15px;color:#9ca3af;font-size:11px;margin-top:20px;">
		Last updated: ${frappe.datetime.prettyDate(data.last_updated)} &nbsp;|&nbsp;
		Period: ${_cd_esc(data.filters_applied.start)} to ${_cd_esc(data.filters_applied.end)}
	</div>`;

	$c.html(html);

	// Render chart after DOM insertion
	_cd_render_chart(data.procurement.monthly_trend);
}

/* ══════════════════════════════════════════════════════════════════════
   KPI CARDS
   ══════════════════════════════════════════════════════════════════════ */

function _cd_kpi_cards(k) {
	const cards = [
		{ label: "My Pending Approvals", value: k.pending_approvals,
		  sub: `${k.pending_pos} POs · ${k.pending_mrs} MRs`,
		  color: "#ef4444", bg: "#fef2f2", icon: "⚠" },
		{ label: "Today's Inward", value: k.today_inward + " vehicles",
		  sub: k.today_weight_mt + " MT received",
		  color: "#3b82f6", bg: "#eff6ff", icon: "🚛" },
		{ label: "Vehicles Inside", value: k.vehicles_inside,
		  color: "#8b5cf6", bg: "#faf5ff", icon: "📍" },
		{ label: "Avg Turnaround", value: _cd_format_time(k.avg_turnaround_min),
		  sub: "today's average",
		  color: "#0ea5e9", bg: "#f0f9ff", icon: "⏱" },
		{ label: "PO Value (Period)", value: format_currency(k.po_value),
		  sub: "approved POs",
		  color: "#10b981", bg: "#ecfdf5", icon: "₹" },
		{ label: "Budget Utilization", value: k.budget_pct + "%",
		  color: k.budget_pct > 80 ? "#ef4444" : k.budget_pct > 60 ? "#f59e0b" : "#10b981",
		  bg: k.budget_pct > 80 ? "#fef2f2" : k.budget_pct > 60 ? "#fffbeb" : "#ecfdf5", icon: "📊" },
		{ label: "Stuck Vehicles", value: k.stuck_vehicles,
		  sub: "CTL breaches",
		  color: k.stuck_vehicles > 0 ? "#ef4444" : "#10b981",
		  bg: k.stuck_vehicles > 0 ? "#fef2f2" : "#ecfdf5", icon: "🔴" },
		{ label: "Pending GRN", value: k.pending_grn,
		  sub: "at Tare Weighed",
		  color: "#f59e0b", bg: "#fffbeb", icon: "📦" },
	];

	let html = '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:10px;">';
	cards.forEach((c) => {
		html += `<div style="padding:16px;background:${c.bg};border-radius:12px;border:1px solid ${c.color}20;position:relative;">`;
		html += `<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">${c.icon} ${c.label}</div>`;
		html += `<div style="font-size:24px;font-weight:800;color:${c.color};margin-top:6px;">${c.value}</div>`;
		if (c.sub) html += `<div style="font-size:11px;color:#9ca3af;margin-top:2px;">${c.sub}</div>`;
		html += "</div>";
	});
	html += "</div>";
	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   SECTION 1: ACTION ITEMS
   ══════════════════════════════════════════════════════════════════════ */

function _cd_action_items(items) {
	const pos = items.pos || [];
	const mrs = items.mrs || [];

	if (!pos.length && !mrs.length) {
		return '<div style="padding:20px;text-align:center;color:#10b981;font-weight:600;">✓ No pending approvals — all clear!</div>';
	}

	let html = "";

	if (pos.length) {
		html += '<div style="font-weight:600;font-size:13px;color:#374151;margin-bottom:8px;">Purchase Orders Pending Approval</div>';
		html += '<table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:16px;">';
		html += '<thead><tr style="background:#fef2f2;">';
		html += '<th style="padding:8px;text-align:left;border-bottom:2px solid #fecaca;">PO</th>';
		html += '<th style="padding:8px;text-align:left;border-bottom:2px solid #fecaca;">Supplier</th>';
		html += '<th style="padding:8px;text-align:right;border-bottom:2px solid #fecaca;">Amount</th>';
		html += '<th style="padding:8px;text-align:center;border-bottom:2px solid #fecaca;">Category</th>';
		html += '<th style="padding:8px;text-align:center;border-bottom:2px solid #fecaca;">Status</th>';
		html += '<th style="padding:8px;text-align:right;border-bottom:2px solid #fecaca;">Waiting</th>';
		html += "</tr></thead><tbody>";

		pos.forEach((po, i) => {
			const bg = i % 2 === 0 ? "#fff" : "#fff7ed";
			const wait_color = po.waiting_hours > 24 ? "#ef4444" : po.waiting_hours > 8 ? "#f59e0b" : "#6b7280";
			html += `<tr style="background:${bg};cursor:pointer;" onclick="frappe.set_route('Form','Purchase Order','${_cd_esc(po.name)}')">`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;"><a style="color:#3b82f6;font-weight:600;">${_cd_esc(po.name)}</a></td>`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;">${_cd_esc(po.supplier_name)}</td>`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:right;font-weight:600;">${format_currency(po.grand_total)}</td>`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:center;"><span style="background:#e0e7ff;color:#3730a3;padding:2px 8px;border-radius:10px;font-size:11px;">${_cd_esc(po.category || "—")}</span></td>`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:center;"><span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:10px;font-size:11px;">${_cd_esc(po.status)}</span></td>`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:right;color:${wait_color};font-weight:600;">${_cd_format_wait(po.waiting_hours)}</td>`;
			html += "</tr>";
		});
		html += "</tbody></table>";
	}

	if (mrs.length) {
		html += '<div style="font-weight:600;font-size:13px;color:#374151;margin-bottom:8px;">Material Requests Pending Approval</div>';
		html += '<table style="width:100%;border-collapse:collapse;font-size:12px;">';
		html += '<thead><tr style="background:#fef2f2;">';
		html += '<th style="padding:8px;text-align:left;border-bottom:2px solid #fecaca;">MR</th>';
		html += '<th style="padding:8px;text-align:left;border-bottom:2px solid #fecaca;">Cost Center</th>';
		html += '<th style="padding:8px;text-align:center;border-bottom:2px solid #fecaca;">Status</th>';
		html += '<th style="padding:8px;text-align:right;border-bottom:2px solid #fecaca;">Waiting</th>';
		html += "</tr></thead><tbody>";

		mrs.forEach((mr, i) => {
			const bg = i % 2 === 0 ? "#fff" : "#fff7ed";
			const wait_color = mr.waiting_hours > 24 ? "#ef4444" : mr.waiting_hours > 8 ? "#f59e0b" : "#6b7280";
			html += `<tr style="background:${bg};cursor:pointer;" onclick="frappe.set_route('Form','Material Request','${_cd_esc(mr.name)}')">`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;"><a style="color:#3b82f6;font-weight:600;">${_cd_esc(mr.name)}</a></td>`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;">${_cd_esc(mr.cost_center || "—")}</td>`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:center;"><span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:10px;font-size:11px;">${_cd_esc(mr.status)}</span></td>`;
			html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:right;color:${wait_color};font-weight:600;">${_cd_format_wait(mr.waiting_hours)}</td>`;
			html += "</tr>";
		});
		html += "</tbody></table>";
	}

	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   SECTION 2: DAILY OPERATIONS
   ══════════════════════════════════════════════════════════════════════ */

function _cd_daily_ops(ops) {
	let html = "";

	// Today's summary cards
	const t = ops.today;
	html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px;">';
	html += _cd_mini_card("Entries", t.entries, "#3b82f6");
	html += _cd_mini_card("Exits", t.exits, "#10b981");
	html += _cd_mini_card("Inside", t.entries - t.exits, "#f59e0b");
	html += "</div>";

	// Breakdown
	html += '<div style="display:flex;gap:10px;margin-bottom:16px;">';
	html += `<div style="flex:1;padding:8px;background:#eff6ff;border-radius:6px;text-align:center;font-size:11px;">Stock IN<br><b style="font-size:16px;color:#3b82f6;">${t.stock_in}</b></div>`;
	html += `<div style="flex:1;padding:8px;background:#faf5ff;border-radius:6px;text-align:center;font-size:11px;">Stock OUT<br><b style="font-size:16px;color:#8b5cf6;">${t.stock_out}</b></div>`;
	html += `<div style="flex:1;padding:8px;background:#ecfdf5;border-radius:6px;text-align:center;font-size:11px;">Gate Pass<br><b style="font-size:16px;color:#10b981;">${t.gate_pass}</b></div>`;
	html += "</div>";

	// This week
	html += `<div style="font-size:11px;color:#6b7280;margin-bottom:10px;">This Week: <b>${ops.week.entries}</b> entries, <b>${ops.week.exits}</b> exits</div>`;

	// Weight by type
	if (ops.weight_by_type && ops.weight_by_type.length) {
		html += '<div style="font-weight:600;font-size:12px;color:#374151;margin-bottom:6px;">Weight Received Today</div>';
		html += '<table style="width:100%;border-collapse:collapse;font-size:11px;">';
		html += '<tr style="background:#f1f5f9;"><th style="padding:6px;text-align:left;">Type</th><th style="padding:6px;text-align:right;">Gross (MT)</th><th style="padding:6px;text-align:right;">Net (MT)</th><th style="padding:6px;text-align:center;">Count</th></tr>';
		ops.weight_by_type.forEach((w) => {
			html += `<tr><td style="padding:6px;">${_cd_esc(w.material_flow || "—")}</td>`;
			html += `<td style="padding:6px;text-align:right;">${w.gross_mt || 0}</td>`;
			html += `<td style="padding:6px;text-align:right;font-weight:600;">${w.net_mt || 0}</td>`;
			html += `<td style="padding:6px;text-align:center;">${w.cnt}</td></tr>`;
		});
		html += "</table>";
	}

	return html;
}

function _cd_stage_bars(stages) {
	if (!stages || !stages.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No vehicles inside</div>';

	const total = stages.reduce((s, r) => s + r.count, 0);
	const colors = {
		"PO Linked": "#3b82f6", "Gross Weighed": "#8b5cf6", "Quality Done": "#0ea5e9",
		"Graded": "#06b6d4", "Unloading": "#f59e0b", "Tare Weighed": "#10b981",
		"GRN Created": "#22c55e", "SI Linked": "#6366f1", "Tare Recorded": "#a855f7",
		"Loading Done": "#ec4899", "Gross Recorded": "#f43f5e", "Dispatch Ready": "#14b8a6",
	};

	let html = '<div style="display:flex;flex-direction:column;gap:8px;">';
	stages.forEach((s) => {
		const pct = total > 0 ? ((s.count / total) * 100) : 0;
		const color = colors[s.stage] || "#6b7280";
		html += '<div style="display:flex;align-items:center;gap:10px;">';
		html += `<div style="width:100px;font-size:11px;color:#374151;text-align:right;font-weight:500;">${_cd_esc(s.stage)}</div>`;
		html += `<div style="flex:1;height:20px;background:#f1f5f9;border-radius:4px;overflow:hidden;">`;
		html += `<div style="height:100%;width:${Math.max(pct, 2)}%;background:${color};border-radius:4px;display:flex;align-items:center;justify-content:center;">`;
		if (s.count > 0) html += `<span style="font-size:10px;color:white;font-weight:700;">${s.count}</span>`;
		html += `</div></div>`;
		html += "</div>";
	});
	html += "</div>";
	html += `<div style="text-align:center;margin-top:10px;font-size:12px;color:#374151;font-weight:600;">Total: ${total} vehicles</div>`;
	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   SECTION 3: PROCUREMENT
   ══════════════════════════════════════════════════════════════════════ */

function _cd_po_pipeline(pipeline) {
	if (!pipeline || !pipeline.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No PO data</div>';

	const statuses = ["Draft", "Pending", "Awaiting", "Approved", "Rejected"];
	const colors = { Draft: "#6b7280", Pending: "#f59e0b", Awaiting: "#3b82f6", Approved: "#10b981", Rejected: "#ef4444" };

	let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;">';
	html += "<thead><tr>";
	html += '<th style="padding:8px;text-align:left;border-bottom:2px solid #e5e7eb;color:#374151;">Category</th>';
	statuses.forEach((s) => {
		html += `<th style="padding:8px;text-align:center;border-bottom:2px solid #e5e7eb;color:${colors[s]};font-weight:600;">${s}</th>`;
	});
	html += '<th style="padding:8px;text-align:center;border-bottom:2px solid #e5e7eb;font-weight:700;">Total</th>';
	html += "</tr></thead><tbody>";

	pipeline.forEach((row, i) => {
		const bg = i % 2 === 0 ? "#fff" : "#f9fafb";
		let total = 0;
		html += `<tr style="background:${bg};">`;
		html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;font-weight:500;">${_cd_esc(row.category)}</td>`;
		statuses.forEach((s) => {
			const v = row[s] || 0;
			total += v;
			const badge = v > 0
				? `<span style="background:${colors[s]}15;color:${colors[s]};padding:2px 8px;border-radius:10px;font-weight:600;">${v}</span>`
				: '<span style="color:#d1d5db;">0</span>';
			html += `<td style="padding:8px;text-align:center;border-bottom:1px solid #f1f5f9;">${badge}</td>`;
		});
		html += `<td style="padding:8px;text-align:center;border-bottom:1px solid #f1f5f9;font-weight:700;">${total}</td>`;
		html += "</tr>";
	});
	html += "</tbody></table>";
	return html;
}

function _cd_suppliers(suppliers) {
	if (!suppliers || !suppliers.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No supplier data</div>';

	const max_val = suppliers[0]?.total_value || 1;
	let html = '<div style="display:flex;flex-direction:column;gap:8px;">';
	suppliers.forEach((s, i) => {
		const pct = ((s.total_value || 0) / max_val) * 100;
		html += '<div style="padding:6px 0;">';
		html += `<div style="display:flex;justify-content:space-between;margin-bottom:3px;">`;
		html += `<span style="font-size:12px;font-weight:500;color:#374151;"><span style="color:#9ca3af;margin-right:4px;">${i + 1}.</span> ${_cd_esc(s.supplier_name)}</span>`;
		html += `<span style="font-size:11px;color:#6b7280;">${s.po_count} POs</span>`;
		html += `</div>`;
		html += `<div style="height:8px;background:#e5e7eb;border-radius:4px;"><div style="height:100%;width:${pct}%;background:#3b82f6;border-radius:4px;"></div></div>`;
		html += `<div style="font-size:11px;color:#3b82f6;font-weight:600;margin-top:2px;">${format_currency(s.total_value)}</div>`;
		html += "</div>";
	});
	html += "</div>";
	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   SECTION 4: BUDGET
   ══════════════════════════════════════════════════════════════════════ */

function _cd_budget_bars(budget) {
	if (!budget.cc_data || !budget.cc_data.length) {
		return '<div style="color:#9ca3af;padding:20px;text-align:center;">No budget data for this fiscal year</div>';
	}

	let html = "";

	// Totals summary
	const t = budget.totals;
	html += '<div style="display:flex;gap:10px;margin-bottom:16px;">';
	html += `<div style="flex:1;padding:10px;background:#f8fafc;border-radius:8px;text-align:center;font-size:11px;">Total Budget<br><b style="font-size:14px;color:#374151;">${format_currency(t.budget)}</b></div>`;
	html += `<div style="flex:1;padding:10px;background:#fffbeb;border-radius:8px;text-align:center;font-size:11px;">Committed<br><b style="font-size:14px;color:#f59e0b;">${format_currency(t.committed)}</b></div>`;
	html += `<div style="flex:1;padding:10px;background:#fef2f2;border-radius:8px;text-align:center;font-size:11px;">Actual Spent<br><b style="font-size:14px;color:#ef4444;">${format_currency(t.actual)}</b></div>`;
	html += `<div style="flex:1;padding:10px;background:${t.pct > 80 ? '#fef2f2' : t.pct > 60 ? '#fffbeb' : '#ecfdf5'};border-radius:8px;text-align:center;font-size:11px;">Utilization<br><b style="font-size:14px;color:${t.pct > 80 ? '#ef4444' : t.pct > 60 ? '#f59e0b' : '#10b981'};">${t.pct}%</b></div>`;
	html += "</div>";

	// Per-CC bars
	html += '<div style="display:flex;flex-direction:column;gap:6px;max-height:300px;overflow-y:auto;">';
	budget.cc_data.forEach((cc) => {
		const color = cc.pct > 80 ? "#ef4444" : cc.pct > 60 ? "#f59e0b" : "#10b981";
		const bar_pct = Math.min(cc.pct, 100);
		const cc_short = (cc.cost_center || "").replace(/ - (BBF|BBPL)$/, "");
		html += '<div style="padding:6px 8px;background:#f8fafc;border-radius:6px;">';
		html += `<div style="display:flex;justify-content:space-between;margin-bottom:3px;">`;
		html += `<span style="font-size:11px;font-weight:500;color:#374151;">${_cd_esc(cc_short)}</span>`;
		html += `<span style="font-size:11px;font-weight:700;color:${color};">${cc.pct}%</span>`;
		html += `</div>`;
		html += `<div style="height:6px;background:#e5e7eb;border-radius:3px;"><div style="height:100%;width:${bar_pct}%;background:${color};border-radius:3px;"></div></div>`;
		html += `<div style="font-size:10px;color:#9ca3af;margin-top:2px;">Budget: ${format_currency(cc.budget)} · Used: ${format_currency(cc.used)}</div>`;
		html += "</div>";
	});
	html += "</div>";
	return html;
}

function _cd_overrides(overrides) {
	let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;">';
	html += '<thead><tr style="background:#fffbeb;">';
	html += '<th style="padding:8px;text-align:left;border-bottom:2px solid #fcd34d;">PO</th>';
	html += '<th style="padding:8px;text-align:left;border-bottom:2px solid #fcd34d;">Override By</th>';
	html += '<th style="padding:8px;text-align:right;border-bottom:2px solid #fcd34d;">PO Amount</th>';
	html += '<th style="padding:8px;text-align:right;border-bottom:2px solid #fcd34d;">Shortfall</th>';
	html += '<th style="padding:8px;text-align:left;border-bottom:2px solid #fcd34d;">Reason</th>';
	html += "</tr></thead><tbody>";

	overrides.forEach((o) => {
		html += `<tr style="cursor:pointer;" onclick="frappe.set_route('Form','Purchase Order','${_cd_esc(o.po_name)}')">`;
		html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;"><a style="color:#3b82f6;font-weight:600;">${_cd_esc(o.po_name)}</a></td>`;
		html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;">${_cd_esc(o.override_by)}</td>`;
		html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:right;">${format_currency(o.po_amount)}</td>`;
		html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;text-align:right;color:#ef4444;font-weight:600;">${format_currency(o.shortfall)}</td>`;
		html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;font-size:11px;color:#6b7280;">${_cd_esc(o.reason || "—")}</td>`;
		html += "</tr>";
	});
	html += "</tbody></table>";
	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   SECTION 5: QUALITY
   ══════════════════════════════════════════════════════════════════════ */

function _cd_quality(q) {
	const qi = q.qi || {};
	const total = qi.total || 0;
	const accepted = qi.accepted || 0;
	const rejected = qi.rejected || 0;
	const on_hold = qi.on_hold || 0;
	const pending = total - accepted - rejected - on_hold;
	const reject_rate = total > 0 ? ((rejected / total) * 100).toFixed(1) : "0.0";

	let html = "";

	// QI summary cards
	html += '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px;">';
	html += _cd_mini_card("Total QI", total, "#374151");
	html += _cd_mini_card("Accepted", accepted, "#10b981");
	html += _cd_mini_card("Rejected", rejected, "#ef4444");
	html += _cd_mini_card("On Hold", on_hold, "#f59e0b");
	html += "</div>";

	// Rejection rate
	html += `<div style="padding:12px;background:${rejected > 0 ? '#fef2f2' : '#ecfdf5'};border-radius:8px;text-align:center;margin-bottom:12px;">`;
	html += `<div style="font-size:11px;color:#6b7280;text-transform:uppercase;">Rejection Rate</div>`;
	html += `<div style="font-size:28px;font-weight:800;color:${rejected > 0 ? '#ef4444' : '#10b981'};">${reject_rate}%</div>`;
	html += "</div>";

	// Material Inspection (Non-RM)
	const mi = q.material_inspection || {};
	const mi_total = Object.values(mi).reduce((s, v) => s + v, 0);
	if (mi_total > 0) {
		html += '<div style="font-weight:600;font-size:12px;color:#374151;margin-bottom:6px;">Material Inspection (Non-RM)</div>';
		html += '<div style="display:flex;gap:6px;flex-wrap:wrap;">';
		const mi_colors = { Pending: "#f59e0b", Approved: "#10b981", "Partially Approved": "#3b82f6", Rejected: "#ef4444", "On Hold": "#6b7280", "Auto-Proceeded": "#9ca3af" };
		Object.entries(mi).forEach(([status, cnt]) => {
			const color = mi_colors[status] || "#6b7280";
			html += `<span style="padding:4px 10px;background:${color}15;color:${color};border-radius:10px;font-size:11px;font-weight:600;">${status}: ${cnt}</span>`;
		});
		html += "</div>";
	}

	return html;
}

function _cd_quality_metrics(q) {
	const cats = q.by_category || [];

	if (!cats.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No quality data in this period</div>';

	let html = '<div style="display:flex;flex-direction:column;gap:12px;">';
	cats.forEach((c) => {
		html += '<div style="padding:12px;background:#f8fafc;border-radius:8px;">';
		html += `<div style="font-weight:600;font-size:13px;color:#374151;margin-bottom:8px;">${_cd_esc(c.item_category)} (${c.cnt} inspections)</div>`;
		html += '<div style="display:flex;gap:16px;">';

		if (c.avg_gcv) {
			html += '<div style="flex:1;text-align:center;">';
			html += '<div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Avg GCV</div>';
			html += `<div style="font-size:20px;font-weight:700;color:#3b82f6;">${c.avg_gcv}</div>`;
			html += "</div>";
		}

		if (c.avg_moisture !== null && c.avg_moisture !== undefined) {
			const m_color = c.avg_moisture > 12 ? "#ef4444" : c.avg_moisture > 8 ? "#f59e0b" : "#10b981";
			html += '<div style="flex:1;text-align:center;">';
			html += '<div style="font-size:10px;color:#6b7280;text-transform:uppercase;">Avg Moisture</div>';
			html += `<div style="font-size:20px;font-weight:700;color:${m_color};">${c.avg_moisture}%</div>`;
			html += "</div>";
		}

		html += "</div></div>";
	});
	html += "</div>";
	return html;
}

/* ══════════════════════════════════════════════════════════════════════
   CHART
   ══════════════════════════════════════════════════════════════════════ */

function _cd_render_chart(trend) {
	const $el = document.getElementById("cd-monthly-chart");
	if (!$el || !trend || !trend.length) {
		if ($el) $el.innerHTML = '<div style="color:#9ca3af;padding:40px;text-align:center;">No trend data</div>';
		return;
	}

	new frappe.Chart($el, {
		data: {
			labels: trend.map((t) => t.month),
			datasets: [{ name: "PO Value", values: trend.map((t) => t.value || 0) }],
		},
		type: "bar",
		height: 220,
		colors: ["#3b82f6"],
		barOptions: { spaceRatio: 0.4 },
		tooltipOptions: { formatTooltipY: (d) => format_currency(d) },
	});
}

/* ══════════════════════════════════════════════════════════════════════
   HELPERS
   ══════════════════════════════════════════════════════════════════════ */

function _cd_card(title, content) {
	return `<div style="background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">
		<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">${title}</div>
		${content}
	</div>`;
}

function _cd_section(title, content, color) {
	return `<div style="margin-top:20px;background:white;border:1px solid ${color}30;border-left:4px solid ${color};border-radius:10px;padding:16px;">
		<div style="font-weight:700;font-size:15px;color:${color};margin-bottom:12px;">${title}</div>
		${content}
	</div>`;
}

function _cd_mini_card(label, value, color) {
	return `<div style="padding:10px;background:${color}10;border-radius:8px;text-align:center;">
		<div style="font-size:20px;font-weight:700;color:${color};">${value}</div>
		<div style="font-size:10px;color:#6b7280;margin-top:2px;">${label}</div>
	</div>`;
}

function _cd_format_time(min) {
	if (!min || min <= 0) return "—";
	const h = Math.floor(min / 60);
	const m = Math.round(min % 60);
	return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function _cd_format_wait(hours) {
	if (!hours || hours <= 0) return "Just now";
	if (hours < 1) return Math.round(hours * 60) + "m";
	if (hours < 24) return Math.round(hours) + "h";
	return Math.round(hours / 24) + "d " + Math.round(hours % 24) + "h";
}

function _cd_esc(v) {
	return frappe.utils.escape_html(v || "");
}
