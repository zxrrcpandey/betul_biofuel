frappe.pages["gate-operations-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Gate Operations Dashboard",
		single_column: true,
	});

	page.main.html('<div id="gate-dash-container" style="padding: 15px;"></div>');

	page.set_primary_action(__("Wall Display"), () => _gd_enter_wall(page), "monitor");

	// Auto-refresh toggle
	page._auto_refresh = true;
	page._refresh_interval = null;
	page._wall_mode = false;
	page._wall_clock_interval = null;

	setTimeout(() => {
		_gd_refresh(page);
		_gd_start_auto_refresh(page, 30000);
	}, 500);
};

frappe.pages["gate-operations-dashboard"].refresh = function (wrapper) {
	_gd_refresh(wrapper.page);
};

/* ── Data Loading ───────────────────────────────────────────── */

let _gd_timeout = null;
function _gd_refresh(page) {
	clearTimeout(_gd_timeout);
	_gd_timeout = setTimeout(() => _gd_do_refresh(page), 200);
}

function _gd_do_refresh(page) {
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_dashboard_gate.get_gate_dashboard",
		callback(r) {
			if (!r.message) return;
			if (page._wall_mode) {
				_gd_render_wall(r.message, page);
			} else {
				_gd_render_normal(r.message);
			}
		},
		error() {
			if (!page._wall_mode) {
				$("#gate-dash-container").html('<div style="text-align:center;padding:40px;color:#ef4444;">Error loading dashboard.</div>');
			}
		},
	});
}

function _gd_start_auto_refresh(page, interval) {
	clearInterval(page._refresh_interval);
	page._refresh_interval = setInterval(() => _gd_refresh(page), interval);
}

/* ── Normal Mode Rendering ──────────────────────────────────── */

function _gd_render_normal(data) {
	const $c = $("#gate-dash-container");
	let html = "";

	// KPI Cards
	const s = data.today_summary;
	const vi = data.vehicles_inside;
	const sla = data.sla_breaches;
	const gp = data.gate_passes;

	const cards = [
		{ label: "Vehicles Inside", value: vi.total, color: "#3b82f6", bg: "#eff6ff" },
		{ label: "Entries Today", value: s.entries, color: "#10b981", bg: "#ecfdf5" },
		{ label: "Exits Today", value: s.exits, color: "#8b5cf6", bg: "#faf5ff" },
		{ label: "Avg Turnaround", value: data.avg_turnaround + "m", color: "#f59e0b", bg: "#fffbeb" },
		{ label: "Stuck Vehicles", value: sla.count, color: sla.count > 0 ? "#ef4444" : "#10b981", bg: sla.count > 0 ? "#fef2f2" : "#ecfdf5" },
		{ label: "Visitors Inside", value: gp.total, color: "#6366f1", bg: "#eef2ff" },
	];

	html += '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px;">';
	cards.forEach((c) => {
		html += `<div style="flex:1;min-width:130px;padding:15px;background:${c.bg};border-radius:10px;border:1px solid ${c.color}20;text-align:center;">`;
		html += `<div style="font-size:28px;font-weight:700;color:${c.color};">${c.value}</div>`;
		html += `<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;margin-top:4px;">${c.label}</div>`;
		html += "</div>";
	});
	html += "</div>";

	// Two columns
	html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">';

	// Left: Stage Pipeline
	html += '<div style="background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">';
	html += '<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">Vehicles by Stage</div>';
	html += _gd_stage_pipeline(vi.by_stage);
	html += "</div>";

	// Right: Hourly chart
	html += '<div style="background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">';
	html += '<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">Hourly Entry Distribution</div>';
	html += '<div id="gd-hourly-chart" style="height:220px;"></div>';
	html += "</div>";

	html += "</div>";

	// Stuck Vehicles
	if (sla.count > 0) {
		html += '<div style="margin-top:20px;background:white;border:1px solid #fca5a5;border-radius:10px;padding:16px;">';
		html += `<div style="font-weight:700;font-size:14px;color:#ef4444;margin-bottom:12px;">Stuck Vehicles (>${sla.threshold} min)</div>`;
		html += _gd_sla_table(sla.items);
		html += "</div>";
	}

	// Recent Activity
	html += '<div style="margin-top:20px;background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">';
	html += '<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">Recent Activity</div>';
	html += _gd_recent_table(data.recent_activity);
	html += "</div>";

	// Last updated
	html += `<div style="text-align:center;margin-top:12px;font-size:11px;color:#94a3b8;">Last updated: ${_gd_esc(data.last_updated)} &middot; Auto-refreshes every 30s</div>`;

	$c.html(html);

	// Render chart
	_gd_render_hourly_chart(data.hourly_distribution);
}

function _gd_stage_pipeline(stages) {
	if (!stages || !stages.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No vehicles inside</div>';

	const colors = {
		"PO Linked": "#3b82f6", "Gross Weighed": "#8b5cf6", "Quality Done": "#6366f1",
		"Graded": "#a855f7", "Unloading": "#f59e0b", "Tare Weighed": "#10b981",
		"GRN Created": "#059669", "SI Linked": "#0ea5e9", "Tare Recorded": "#06b6d4",
		"Loading Done": "#14b8a6", "Gross Recorded": "#10b981", "Dispatch Ready": "#22c55e",
	};

	let html = '<div style="display:flex;flex-direction:column;gap:8px;">';
	stages.forEach((st) => {
		const color = colors[st.stage] || "#6b7280";
		html += `<div style="display:flex;align-items:center;gap:12px;padding:10px 12px;background:${color}08;border-radius:8px;border-left:4px solid ${color};">`;
		html += `<div style="width:36px;height:36px;border-radius:50%;background:${color};color:white;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:16px;">${st.count}</div>`;
		html += `<div style="flex:1;">`;
		html += `<div style="font-weight:600;font-size:13px;color:#374151;">${_gd_esc(st.stage)}</div>`;
		if (st.tokens && st.tokens.length) {
			html += `<div style="font-size:11px;color:#9ca3af;margin-top:2px;">`;
			html += st.tokens.slice(0, 3).map(t => `<a href="/app/bbf-token/${encodeURIComponent(t.name)}" style="color:#6b7280;text-decoration:none;">${_gd_esc(t.vehicle_number || t.token_number)}</a>`).join(", ");
			if (st.count > 3) html += ` +${st.count - 3} more`;
			html += `</div>`;
		}
		html += "</div></div>";
	});
	html += "</div>";
	return html;
}

function _gd_sla_table(items) {
	let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;">';
	html += '<thead><tr style="background:#fef2f2;">';
	["Token", "Vehicle", "Stage", "Waiting"].forEach(h => {
		html += `<th style="padding:8px;text-align:left;border-bottom:2px solid #fca5a5;color:#991b1b;">${h}</th>`;
	});
	html += "</tr></thead><tbody>";

	items.forEach((b) => {
		const timeColor = b.minutes > 120 ? "#ef4444" : "#f59e0b";
		html += "<tr>";
		html += `<td style="padding:8px;border-bottom:1px solid #fee2e2;"><a href="/app/bbf-token/${encodeURIComponent(b.token_name)}" style="color:#1d4ed8;text-decoration:none;font-weight:500;">${_gd_esc(b.token)}</a></td>`;
		html += `<td style="padding:8px;border-bottom:1px solid #fee2e2;">${_gd_esc(b.vehicle)}</td>`;
		html += `<td style="padding:8px;border-bottom:1px solid #fee2e2;">${_gd_esc(b.status)}</td>`;
		html += `<td style="padding:8px;border-bottom:1px solid #fee2e2;font-weight:700;color:${timeColor};">${b.minutes}m</td>`;
		html += "</tr>";
	});

	html += "</tbody></table>";
	return html;
}

function _gd_recent_table(items) {
	if (!items || !items.length) return '<div style="color:#9ca3af;padding:10px;text-align:center;">No activity today</div>';

	let html = '<div style="display:flex;flex-direction:column;gap:4px;max-height:300px;overflow-y:auto;">';
	items.forEach((t) => {
		const sc = t.status === "Exited" ? "#10b981" : t.status === "Token Generated" ? "#6b7280" : "#f59e0b";
		const time = t.g1_entry_time ? t.g1_entry_time.split(" ")[1]?.substring(0, 5) : "";
		html += `<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 10px;border-radius:6px;background:#f8fafc;">`;
		html += `<div style="display:flex;align-items:center;gap:8px;">`;
		html += `<a href="/app/bbf-token/${encodeURIComponent(t.name)}" style="font-weight:500;font-size:12px;color:#1d4ed8;text-decoration:none;">${_gd_esc(t.token_number)}</a>`;
		html += `<span style="font-size:11px;color:#64748b;">${_gd_esc(t.vehicle_number || "")}</span>`;
		html += "</div>";
		html += `<div style="display:flex;align-items:center;gap:8px;">`;
		html += `<span style="font-size:11px;padding:2px 8px;border-radius:10px;background:${sc}15;color:${sc};font-weight:600;">${_gd_esc(t.status)}</span>`;
		html += `<span style="font-size:10px;color:#94a3b8;min-width:40px;text-align:right;">${time}</span>`;
		html += "</div></div>";
	});
	html += "</div>";
	return html;
}

function _gd_render_hourly_chart(hours) {
	const $el = document.getElementById("gd-hourly-chart");
	if (!$el || !hours || !hours.length) return;

	const labels = hours.map(h => (h.hour < 10 ? "0" : "") + h.hour);
	const values = hours.map(h => h.count);

	new frappe.Chart($el, {
		data: { labels, datasets: [{ name: "Entries", values }] },
		type: "bar",
		height: 200,
		colors: ["#3b82f6"],
		barOptions: { spaceRatio: 0.3 },
		tooltipOptions: { formatTooltipX: (d) => d + ":00" },
	});
}

/* ── Wall Display Mode ──────────────────────────────────────── */

function _gd_enter_wall(page) {
	page._wall_mode = true;

	// Create overlay
	const $overlay = $('<div class="gd-wall-overlay" id="gd-wall-overlay"></div>');
	$("body").append($overlay);

	// Try native fullscreen
	const el = document.documentElement;
	if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
	else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();

	// ESC key listener
	page._wall_esc_handler = (e) => {
		if (e.key === "Escape") _gd_exit_wall(page);
	};
	document.addEventListener("keydown", page._wall_esc_handler);

	// Fullscreen change listener
	page._wall_fs_handler = () => {
		if (!document.fullscreenElement && !document.webkitFullscreenElement) {
			_gd_exit_wall(page);
		}
	};
	document.addEventListener("fullscreenchange", page._wall_fs_handler);
	document.addEventListener("webkitfullscreenchange", page._wall_fs_handler);

	// Start clock
	page._wall_clock_interval = setInterval(() => {
		const $clock = $(".gd-wall-clock");
		if ($clock.length) {
			const now = new Date();
			$clock.text(now.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
		}
	}, 1000);

	// Faster refresh in wall mode (15s)
	_gd_start_auto_refresh(page, 15000);

	// Initial render
	_gd_refresh(page);
}

function _gd_exit_wall(page) {
	if (!page._wall_mode) return;
	page._wall_mode = false;

	// Remove overlay
	$("#gd-wall-overlay").remove();

	// Exit fullscreen
	if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
	else if (document.webkitFullscreenElement) document.webkitExitFullscreen();

	// Remove listeners
	if (page._wall_esc_handler) document.removeEventListener("keydown", page._wall_esc_handler);
	if (page._wall_fs_handler) {
		document.removeEventListener("fullscreenchange", page._wall_fs_handler);
		document.removeEventListener("webkitfullscreenchange", page._wall_fs_handler);
	}

	// Clear clock
	clearInterval(page._wall_clock_interval);

	// Normal refresh rate
	_gd_start_auto_refresh(page, 30000);
	_gd_refresh(page);
}

function _gd_render_wall(data, page) {
	const $overlay = $("#gd-wall-overlay");
	if (!$overlay.length) return;

	const s = data.today_summary;
	const vi = data.vehicles_inside;
	const sla = data.sla_breaches;
	const gp = data.gate_passes;

	let html = "";

	// Header: title + live dot + clock + exit
	html += '<div class="gd-wall-header">';
	html += '<div class="gd-wall-title"><span class="gd-live-dot"></span>Gate Operations — Live</div>';
	html += '<div style="display:flex;align-items:center;gap:20px;">';
	html += '<div class="gd-wall-clock"></div>';
	html += `<button class="gd-wall-exit" onclick="frappe.pages['gate-operations-dashboard'].on_page_load && _gd_exit_wall(cur_page.page)">EXIT</button>`;
	html += "</div></div>";

	// Body
	html += '<div class="gd-wall-body">';

	// KPI Cards
	html += '<div class="gd-wall-kpis">';
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
		html += '<div class="gd-wall-kpi">';
		html += `<div class="gd-wall-kpi-value" style="color:${k.color};">${k.value}</div>`;
		html += `<div class="gd-wall-kpi-label">${k.label}</div>`;
		html += "</div>";
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

		html += '<div class="gd-wall-pipeline">';
		vi.by_stage.forEach((st, i) => {
			if (i > 0) html += '<div class="gd-wall-stage-arrow">&#10132;</div>';
			const c = stageColors[st.stage] || "#64748b";
			html += '<div class="gd-wall-stage">';
			html += `<div class="gd-wall-stage-count" style="background:${c};">${st.count}</div>`;
			html += `<div class="gd-wall-stage-label">${_gd_esc(st.stage)}</div>`;
			html += "</div>";
		});
		html += "</div>";
	}

	// Two columns: Stuck Vehicles + Recent Activity
	html += '<div class="gd-wall-grid">';

	// Stuck Vehicles
	html += '<div class="gd-wall-section">';
	html += `<div class="gd-wall-section-title" style="color:${sla.count > 0 ? '#ef4444' : '#22c55e'};">Stuck Vehicles (>${sla.threshold}m) — ${sla.count}</div>`;
	if (sla.items && sla.items.length) {
		html += '<div class="gd-wall-breaches">';
		sla.items.slice(0, 8).forEach((b) => {
			const cls = b.minutes > 120 ? "critical" : "warning";
			html += '<div class="gd-breach-row">';
			html += `<div><span class="gd-breach-token">${_gd_esc(b.token)}</span> <span class="gd-breach-status">${_gd_esc(b.vehicle)}</span></div>`;
			html += `<div><span class="gd-breach-status">${_gd_esc(b.status)}</span> <span class="gd-breach-time ${cls}">${b.minutes}m</span></div>`;
			html += "</div>";
		});
		html += "</div>";
	} else {
		html += '<div style="padding:30px;text-align:center;color:#22c55e;font-size:18px;">All Clear</div>';
	}
	html += "</div>";

	// Recent Activity
	html += '<div class="gd-wall-section">';
	html += '<div class="gd-wall-section-title">Recent Activity</div>';
	if (data.recent_activity && data.recent_activity.length) {
		data.recent_activity.slice(0, 8).forEach((t) => {
			const sc = t.status === "Exited" ? "#22c55e" : t.status === "Token Generated" ? "#64748b" : "#f59e0b";
			const time = t.g1_entry_time ? t.g1_entry_time.split(" ")[1]?.substring(0, 5) : "";
			html += '<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #334155;">';
			html += `<div><span style="color:#f8fafc;font-weight:500;">${_gd_esc(t.token_number)}</span> <span style="color:#64748b;">${_gd_esc(t.vehicle_number || "")}</span></div>`;
			html += `<div><span style="color:${sc};font-weight:600;">${_gd_esc(t.status)}</span> <span style="color:#64748b;margin-left:8px;">${time}</span></div>`;
			html += "</div>";
		});
	}
	html += "</div>";

	html += "</div>"; // grid
	html += "</div>"; // body

	$overlay.html(html);
}

/* ── Helpers ────────────────────────────────────────────────── */

function _gd_esc(v) {
	return frappe.utils.escape_html(v || "");
}
