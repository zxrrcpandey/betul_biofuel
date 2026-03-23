/**
 * CEO Wall Display — standalone fullscreen dark-theme executive dashboard
 * Auto-enters wall mode on load. Refreshes every 30 seconds.
 * Press ESC or click EXIT to return to CEO Dashboard.
 */

frappe.pages["ceo-wall-display"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "CEO Wall Display",
		single_column: true,
	});

	$(".navbar, .page-head").hide();
	$("[data-page-container] > .layout-side-section").hide();

	page.main.html('<div id="cwd-container"></div>');
	page._refresh_interval = null;
	page._clock_interval = null;

	_cwd_init(page);
};

frappe.pages["ceo-wall-display"].on_page_show = function (wrapper) {
	$(".navbar, .page-head").hide();
	$("[data-page-container] > .layout-side-section").hide();
};

frappe.pages["ceo-wall-display"].on_page_hide = function () {
	$(".navbar, .page-head").show();
	$("[data-page-container] > .layout-side-section").show();
};

/* ── Init ───────────────────────────────────────────────────── */

function _cwd_init(page) {
	const el = document.documentElement;
	if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
	else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();

	page._clock_interval = setInterval(() => {
		const $clock = $(".cwd-clock");
		if ($clock.length) {
			$clock.text(new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
		}
	}, 1000);

	page._esc_handler = (e) => {
		if (e.key === "Escape") _cwd_exit(page);
	};
	document.addEventListener("keydown", page._esc_handler);

	_cwd_refresh(page);
	page._refresh_interval = setInterval(() => _cwd_refresh(page), 30000);
}

function _cwd_exit(page) {
	clearInterval(page._refresh_interval);
	clearInterval(page._clock_interval);
	if (page._esc_handler) document.removeEventListener("keydown", page._esc_handler);

	if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
	else if (document.webkitFullscreenElement) document.webkitExitFullscreen();

	$(".navbar, .page-head").show();
	$("[data-page-container] > .layout-side-section").show();
	frappe.set_route("ceo-dashboard");
}

/* ── Data ───────────────────────────────────────────────────── */

function _cwd_refresh(page) {
	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_dashboard_ceo.get_ceo_dashboard",
		args: { date_range: "This Month" },
		callback(r) {
			if (r.message) _cwd_render(r.message, page);
		},
	});
}

/* ── Render ──────────────────────────────────────────────────── */

function _cwd_render(data, page) {
	const $c = $("#cwd-container");
	const k = data.kpis;
	const esc = frappe.utils.escape_html;

	let html = '<div class="cwd-wall">';

	// Header
	html += '<div class="cwd-header">';
	html += '<div class="cwd-title"><span class="cwd-live-dot"></span>CEO Dashboard — Live</div>';
	html += '<div class="cwd-header-right">';
	html += '<div class="cwd-clock"></div>';
	html += '<button class="cwd-exit-btn" onclick="_cwd_exit(cur_page.page)">EXIT</button>';
	html += "</div></div>";

	// KPI Cards — Row 1 (4 cards)
	html += '<div class="cwd-kpis">';
	const kpis1 = [
		{ label: "Pending Approvals", value: k.pending_approvals,
		  sub: k.pending_pos + " POs · " + k.pending_mrs + " MRs",
		  color: k.pending_approvals > 0 ? "#ef4444" : "#22c55e" },
		{ label: "Vehicles Inside", value: k.vehicles_inside, color: "#3b82f6" },
		{ label: "Today's Inward", value: k.today_inward,
		  sub: k.today_weight_mt + " MT", color: "#10b981" },
		{ label: "Avg Turnaround", value: _cwd_fmt_time(k.avg_turnaround_min),
		  sub: "today", color: "#0ea5e9" },
	];
	kpis1.forEach((c) => {
		html += `<div class="cwd-kpi"><div class="cwd-kpi-value" style="color:${c.color};">${c.value}</div>`;
		html += `<div class="cwd-kpi-label">${c.label}</div>`;
		if (c.sub) html += `<div class="cwd-kpi-sub">${c.sub}</div>`;
		html += "</div>";
	});
	html += "</div>";

	// KPI Cards — Row 2 (4 cards)
	html += '<div class="cwd-kpis" style="padding-top:0;">';
	const kpis2 = [
		{ label: "PO Value (Period)", value: _cwd_fmt_currency(k.po_value), color: "#f59e0b" },
		{ label: "Budget Utilization", value: k.budget_pct + "%",
		  color: k.budget_pct > 80 ? "#ef4444" : k.budget_pct > 60 ? "#f59e0b" : "#22c55e" },
		{ label: "Stuck Vehicles", value: k.stuck_vehicles,
		  sub: "CTL breaches",
		  color: k.stuck_vehicles > 0 ? "#ef4444" : "#22c55e" },
		{ label: "Pending GRN", value: k.pending_grn,
		  sub: "at Tare Weighed", color: "#a855f7" },
	];
	kpis2.forEach((c) => {
		html += `<div class="cwd-kpi"><div class="cwd-kpi-value" style="color:${c.color};">${c.value}</div>`;
		html += `<div class="cwd-kpi-label">${c.label}</div>`;
		if (c.sub) html += `<div class="cwd-kpi-sub">${c.sub}</div>`;
		html += "</div>";
	});
	html += "</div>";

	// Stage Pipeline
	const stages = data.daily_ops.stages;
	if (stages && stages.length) {
		const stageColors = {
			"PO Linked": "#3b82f6", "Gross Weighed": "#8b5cf6", "Quality Done": "#6366f1",
			"Graded": "#a855f7", "Unloading": "#f59e0b", "Tare Weighed": "#10b981",
			"GRN Created": "#059669", "SI Linked": "#0ea5e9", "Tare Recorded": "#06b6d4",
			"Loading Done": "#14b8a6", "Gross Recorded": "#10b981", "Dispatch Ready": "#22c55e",
		};

		html += '<div class="cwd-pipeline">';
		stages.forEach((st, i) => {
			if (i > 0) html += '<div class="cwd-pipe-arrow">&#10132;</div>';
			const c = stageColors[st.stage] || "#64748b";
			html += `<div class="cwd-pipe-stage">`;
			html += `<div class="cwd-pipe-count" style="background:${c};">${st.count}</div>`;
			html += `<div class="cwd-pipe-label">${esc(st.stage)}</div>`;
			html += "</div>";
		});
		html += "</div>";
	}

	// Two columns: Action Items + Budget
	html += '<div class="cwd-grid">';

	// Left: Pending PO Approvals
	html += '<div class="cwd-section">';
	const pos = data.action_items.pos || [];
	html += `<div class="cwd-section-title" style="color:${pos.length > 0 ? '#ef4444' : '#22c55e'};">Pending PO Approvals — ${pos.length}</div>`;
	if (pos.length) {
		pos.slice(0, 8).forEach((po) => {
			const wait_cls = po.waiting_hours > 24 ? "urgent" : po.waiting_hours > 8 ? "warning" : "normal";
			html += '<div class="cwd-action-row">';
			html += `<div><span class="cwd-action-name">${esc(po.name)}</span><span class="cwd-action-sub">${esc(po.supplier_name || "")}</span></div>`;
			html += `<div><span class="cwd-action-amount">${format_currency(po.grand_total)}</span> <span class="cwd-action-wait ${wait_cls}">${_cwd_fmt_wait(po.waiting_hours)}</span></div>`;
			html += "</div>";
		});
	} else {
		html += '<div class="cwd-all-clear">All Clear — No Pending POs</div>';
	}
	html += "</div>";

	// Right: Budget Utilization (top CCs)
	html += '<div class="cwd-section">';
	const budget = data.budget_overview;
	const bt = budget.totals || {};
	html += `<div class="cwd-section-title">Budget — ${bt.pct || 0}% Used</div>`;

	if (budget.cc_data && budget.cc_data.length) {
		budget.cc_data.slice(0, 8).forEach((cc) => {
			const color = cc.pct > 80 ? "#ef4444" : cc.pct > 60 ? "#f59e0b" : "#22c55e";
			const cc_short = (cc.cost_center || "").replace(/ - (BBF|BBPL)$/, "");
			html += '<div class="cwd-budget-row">';
			html += `<div class="cwd-budget-label"><span style="color:#cbd5e1;">${esc(cc_short)}</span><span style="color:${color};font-weight:700;">${cc.pct}%</span></div>`;
			html += `<div class="cwd-budget-bar"><div class="cwd-budget-fill" style="width:${Math.min(cc.pct, 100)}%;background:${color};"></div></div>`;
			html += "</div>";
		});
	} else {
		html += '<div style="padding:20px;text-align:center;color:#64748b;">No budget data</div>';
	}
	html += "</div>";

	html += "</div>"; // grid

	// Second row: Quality + Top Suppliers
	html += '<div class="cwd-grid">';

	// Left: Quality Summary
	html += '<div class="cwd-section">';
	html += '<div class="cwd-section-title">Quality Inspection</div>';
	const qi = data.quality.qi || {};
	html += '<div class="cwd-quality-grid">';
	const qi_cards = [
		{ label: "Total", value: qi.total || 0, color: "#e2e8f0" },
		{ label: "Accepted", value: qi.accepted || 0, color: "#22c55e" },
		{ label: "Rejected", value: qi.rejected || 0, color: "#ef4444" },
		{ label: "On Hold", value: qi.on_hold || 0, color: "#f59e0b" },
	];
	qi_cards.forEach((c) => {
		html += `<div class="cwd-quality-card"><div class="cwd-quality-val" style="color:${c.color};">${c.value}</div><div class="cwd-quality-lbl">${c.label}</div></div>`;
	});
	html += "</div>";

	// Quality metrics by category
	const cats = data.quality.by_category || [];
	if (cats.length) {
		html += '<div style="margin-top:12px;">';
		cats.forEach((c) => {
			html += `<div style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #334155;">`;
			html += `<span style="color:#cbd5e1;font-weight:500;">${esc(c.item_category)}</span>`;
			html += '<span>';
			if (c.avg_gcv) html += `<span style="color:#3b82f6;margin-right:12px;">GCV: <b>${c.avg_gcv}</b></span>`;
			if (c.avg_moisture !== null) {
				const mc = c.avg_moisture > 12 ? "#ef4444" : "#f59e0b";
				html += `<span style="color:${mc};">Moisture: <b>${c.avg_moisture}%</b></span>`;
			}
			html += "</span></div>";
		});
		html += "</div>";
	}
	html += "</div>";

	// Right: Top Suppliers
	html += '<div class="cwd-section">';
	html += '<div class="cwd-section-title">Top Suppliers by Value</div>';
	const suppliers = data.procurement.top_suppliers || [];
	if (suppliers.length) {
		const max_val = suppliers[0]?.total_value || 1;
		suppliers.slice(0, 5).forEach((s, i) => {
			const pct = ((s.total_value || 0) / max_val) * 100;
			html += '<div class="cwd-supplier-row">';
			html += `<div class="cwd-supplier-rank">${i + 1}</div>`;
			html += '<div class="cwd-supplier-bar-wrap">';
			html += `<div class="cwd-supplier-name">${esc(s.supplier_name)}</div>`;
			html += `<div class="cwd-supplier-bar"><div class="cwd-supplier-fill" style="width:${pct}%;"></div></div>`;
			html += `<div class="cwd-supplier-val">${format_currency(s.total_value)}</div>`;
			html += "</div></div>";
		});
	} else {
		html += '<div style="padding:20px;text-align:center;color:#64748b;">No supplier data</div>';
	}
	html += "</div>";

	html += "</div>"; // grid
	html += "</div>"; // wall

	$c.html(html);
}

/* ── Helpers ─────────────────────────────────────────────────── */

function _cwd_fmt_time(min) {
	if (!min || min <= 0) return "—";
	const h = Math.floor(min / 60);
	const m = Math.round(min % 60);
	return h > 0 ? h + "h " + m + "m" : m + "m";
}

function _cwd_fmt_wait(hours) {
	if (!hours || hours <= 0) return "now";
	if (hours < 1) return Math.round(hours * 60) + "m";
	if (hours < 24) return Math.round(hours) + "h";
	return Math.round(hours / 24) + "d";
}

function _cwd_fmt_currency(val) {
	if (!val) return "₹0";
	if (val >= 10000000) return "₹" + (val / 10000000).toFixed(1) + "Cr";
	if (val >= 100000) return "₹" + (val / 100000).toFixed(1) + "L";
	if (val >= 1000) return "₹" + (val / 1000).toFixed(0) + "K";
	return "₹" + val;
}
