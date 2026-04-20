/**
 * Gate Wall Display — standalone fullscreen dark-theme dashboard
 * Auto-enters wall mode on load. Refreshes every 15 seconds.
 * Press ESC or click EXIT to return to normal Frappe view.
 */

frappe.pages["gate-wall-display"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Gate Wall Display",
		single_column: true,
	});

	// Hide Frappe chrome immediately
	$(".navbar, .page-head").hide();
	$("[data-page-container] > .layout-side-section").hide();

	page.main.html('<div id="gwd-container"></div>');

	page._refresh_interval = null;
	page._clock_interval = null;

	// Enter wall mode immediately
	_gwd_init(page);
};

frappe.pages["gate-wall-display"].on_page_show = function (wrapper) {
	// Re-hide chrome on page show (e.g., back navigation)
	$(".navbar, .page-head").hide();
	$("[data-page-container] > .layout-side-section").hide();
};

frappe.pages["gate-wall-display"].on_page_hide = function () {
	// Restore Frappe chrome when leaving
	$(".navbar, .page-head").show();
	$("[data-page-container] > .layout-side-section").show();
};

/* ── Init ───────────────────────────────────────────────────── */

function _gwd_init(page) {
	// Try fullscreen
	const el = document.documentElement;
	if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
	else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();

	// Start clock
	page._clock_interval = setInterval(() => {
		const $clock = $(".gwd-clock");
		if ($clock.length) {
			$clock.text(new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
		}
	}, 1000);

	// ESC to exit
	page._esc_handler = (e) => {
		if (e.key === "Escape") _gwd_exit(page);
	};
	document.addEventListener("keydown", page._esc_handler);

	// Load data and start auto-refresh
	_gwd_refresh(page);
	page._refresh_interval = setInterval(() => _gwd_refresh(page), 15000);
}

function _gwd_exit(page) {
	clearInterval(page._refresh_interval);
	clearInterval(page._clock_interval);
	if (page._esc_handler) document.removeEventListener("keydown", page._esc_handler);

	if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
	else if (document.webkitFullscreenElement) document.webkitExitFullscreen();

	$(".navbar, .page-head").show();
	$("[data-page-container] > .layout-side-section").show();
	frappe.set_route("gate-operations-dashboard");
}

/* ── Data ───────────────────────────────────────────────────── */

function _gwd_refresh(page) {
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_dashboard_gate.get_gate_dashboard",
		callback(r) {
			if (r.message) _gwd_render(r.message, page);
		},
	});
}

/* ── Render ──────────────────────────────────────────────────── */

function _gwd_render(data, page) {
	const $c = $("#gwd-container");
	const s = data.today_summary;
	const vi = data.vehicles_inside;
	const sla = data.sla_breaches;
	const gp = data.gate_passes;
	const esc = frappe.utils.escape_html;

	let html = '<div class="gwd-wall">';

	// Header
	html += '<div class="gwd-header">';
	html += '<div class="gwd-title"><span class="gwd-live-dot"></span>Gate Operations — Live</div>';
	html += '<div class="gwd-header-right">';
	html += '<div class="gwd-clock"></div>';
	html += '<button class="gwd-exit-btn" onclick="_gwd_exit(cur_page.page)">EXIT</button>';
	html += "</div></div>";

	// KPI Cards
	html += '<div class="gwd-kpis">';
	const kpis = [
		{ label: "Vehicles Inside", value: vi.total, color: "#3b82f6" },
		{ label: "Entries Today", value: s.entries, color: "#10b981" },
		{ label: "Exits Today", value: s.exits, color: "#8b5cf6" },
		{ label: "Stock IN", value: s.stock_in, color: "#0ea5e9" },
		{ label: "Stock OUT", value: s.stock_out, color: "#f97316" },
		{ label: "Avg Turnaround", value: data.avg_turnaround + "m", color: "#f59e0b" },
		{ label: "Stuck Vehicles", value: sla.count, color: sla.count > 0 ? "#ef4444" : "#22c55e" },
		{ label: "Visitors Inside", value: gp.total, color: "#6366f1" },
	];
	kpis.forEach((k) => {
		html += `<div class="gwd-kpi"><div class="gwd-kpi-value" style="color:${k.color};">${k.value}</div>`;
		html += `<div class="gwd-kpi-label">${k.label}</div></div>`;
	});
	html += "</div>";

	// Stage Pipeline
	if (vi.by_stage && vi.by_stage.length) {
		const stageColors = {
			"PO Linked": "#3b82f6", "Gross Weighed": "#8b5cf6", "Quality Done": "#6366f1",
			"Graded": "#a855f7", "Unloading": "#f59e0b", "Tare Weighed": "#10b981",
			"GRN Created": "#059669", "SI Linked": "#0ea5e9", "Tare Recorded": "#06b6d4",
			"Loading Done": "#14b8a6", "Gross Recorded": "#10b981", "Dispatch Ready": "#22c55e",
		};

		html += '<div class="gwd-pipeline">';
		vi.by_stage.forEach((st, i) => {
			if (i > 0) html += '<div class="gwd-pipe-arrow">&#10132;</div>';
			const c = stageColors[st.stage] || "#64748b";
			html += `<div class="gwd-pipe-stage">`;
			html += `<div class="gwd-pipe-count" style="background:${c};">${st.count}</div>`;
			html += `<div class="gwd-pipe-label">${esc(st.stage)}</div>`;
			html += "</div>";
		});
		html += "</div>";
	}

	// Two columns: SLA + Recent
	html += '<div class="gwd-grid">';

	// Stuck Vehicles
	html += '<div class="gwd-section">';
	html += `<div class="gwd-section-title" style="color:${sla.count > 0 ? "#ef4444" : "#22c55e"};">Stuck Vehicles (>${sla.threshold}m) — ${sla.count}</div>`;
	if (sla.items && sla.items.length) {
		sla.items.slice(0, 10).forEach((b) => {
			const cls = b.minutes > 120 ? "critical" : "warning";
			html += '<div class="gwd-breach-row">';
			html += `<div><span class="gwd-breach-token">${esc(b.token)}</span> <span class="gwd-breach-sub">${esc(b.vehicle)}</span></div>`;
			html += `<div><span class="gwd-breach-sub">${esc(b.status)}</span> <span class="gwd-breach-time ${cls}">${b.minutes}m</span></div>`;
			html += "</div>";
		});
	} else {
		html += '<div class="gwd-all-clear">All Clear</div>';
	}
	html += "</div>";

	// Recent Activity
	html += '<div class="gwd-section">';
	html += '<div class="gwd-section-title">Recent Activity</div>';
	if (data.recent_activity && data.recent_activity.length) {
		data.recent_activity.slice(0, 10).forEach((t) => {
			const sc = ["Exited", "Campus Exited"].includes(t.status) ? "#22c55e" : t.status === "Plant Exited" ? "#fbbf24" : t.status === "Token Generated" ? "#64748b" : "#f59e0b";
			const time = t.g1_entry_time ? t.g1_entry_time.split(" ")[1]?.substring(0, 5) : "";
			html += '<div class="gwd-activity-row">';
			html += `<div><span style="color:#f8fafc;font-weight:500;">${esc(t.token_number)}</span> <span style="color:#64748b;">${esc(t.vehicle_number || "")}</span></div>`;
			html += `<div><span style="color:${sc};font-weight:600;">${esc(t.status)}</span> <span style="color:#64748b;margin-left:8px;">${time}</span></div>`;
			html += "</div>";
		});
	}
	html += "</div>";

	html += "</div>"; // grid
	html += "</div>"; // wall

	$c.html(html);
}
