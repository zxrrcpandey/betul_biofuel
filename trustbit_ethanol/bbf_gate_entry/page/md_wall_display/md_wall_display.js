/**
 * MD Wall Display — premium fullscreen dark-theme executive dashboard
 * Auto-enters fullscreen. Refreshes every 30 seconds.
 * ESC or EXIT button returns to MD Dashboard.
 */

frappe.pages["md-wall-display"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "MD Wall Display",
		single_column: true,
	});

	$(".navbar, .page-head").hide();
	$("[data-page-container] > .layout-side-section").hide();

	page.main.html('<div id="mwd-container"></div>');
	page._refresh_interval = null;
	page._clock_interval = null;

	_mwd_init(page);
};

frappe.pages["md-wall-display"].on_page_show = function () {
	$(".navbar, .page-head").hide();
	$("[data-page-container] > .layout-side-section").hide();
};

frappe.pages["md-wall-display"].on_page_hide = function () {
	$(".navbar, .page-head").show();
	$("[data-page-container] > .layout-side-section").show();
};

function _mwd_init(page) {
	_mwd_page_ref = page;
	const el = document.documentElement;
	if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
	else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();

	page._clock_interval = setInterval(() => {
		const $c = $(".mwd-clock");
		if ($c.length) {
			$c.text(new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
		}
	}, 1000);

	page._esc_handler = (e) => { if (e.key === "Escape") _mwd_exit(page); };
	document.addEventListener("keydown", page._esc_handler);

	// Check PIN
	if (sessionStorage.getItem("md_dash_unlocked") === "1") {
		_mwd_start(page);
	} else {
		frappe.call({
			method: "trustbit_ethanol.bbf_gate_entry.bbf_dashboard_md.check_md_pin_required",
			callback(r) {
				if (r.message && r.message.pin_required) {
					_mwd_show_pin(page);
				} else {
					sessionStorage.setItem("md_dash_unlocked", "1");
					_mwd_start(page);
				}
			},
		});
	}
}

function _mwd_start(page) {
	_mwd_refresh(page);
	page._refresh_interval = setInterval(() => _mwd_refresh(page), 30000);
}

function _mwd_show_pin(page) {
	$("#mwd-container").html('<div style="display:flex;align-items:center;justify-content:center;min-height:100vh;background:#0f172a;color:#64748b;font-size:16px;">Wall Display locked. Enter PIN to continue.</div>');
	frappe.prompt(
		[{ label: "Enter PIN", fieldname: "pin", fieldtype: "Password", reqd: 1 }],
		function(values) {
			frappe.call({
				method: "trustbit_ethanol.bbf_gate_entry.bbf_dashboard_md.verify_md_pin",
				args: { pin: values.pin },
				callback(r) {
					if (r.message && r.message.valid) {
						sessionStorage.setItem("md_dash_unlocked", "1");
						_mwd_start(page);
					} else {
						frappe.msgprint({ title: "Access Denied", message: "Incorrect PIN.", indicator: "red" });
						setTimeout(() => _mwd_show_pin(page), 500);
					}
				},
			});
		},
		"MD Wall Display — PIN Required",
		"Unlock"
	);
}

function _mwd_exit(page) {
	clearInterval(page._refresh_interval);
	clearInterval(page._clock_interval);
	if (page._esc_handler) document.removeEventListener("keydown", page._esc_handler);
	if (document.exitFullscreen) document.exitFullscreen().catch(() => {});
	$(".navbar, .page-head").show();
	$("[data-page-container] > .layout-side-section").show();
	frappe.set_route("md-dashboard");
}

function _mwd_refresh(page) {
	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_dashboard_md.get_md_dashboard",
		args: { date_range: "Today" },
		callback(r) {
			if (r.message) _mwd_render(r.message, page);
		},
	});
}

function _mwd_render(data, page) {
	const co = data.company_overview;
	const ops = data.gate_operations;
	const q = data.quality;

	let html = '<button class="mwd-exit" onclick="_mwd_exit(cur_page.page)">EXIT</button>';

	// Header
	html += `<div class="mwd-header">
		<div>
			<div class="mwd-company">Betul Bio Fuel Pvt Ltd</div>
			<div class="mwd-subtitle">Managing Director — Live Operations Monitor</div>
		</div>
		<div style="text-align:right;">
			<div class="mwd-live"><span class="mwd-live-dot"></span> LIVE</div>
			<div class="mwd-clock"></div>
		</div>
	</div>`;

	// KPI Cards
	html += '<div class="mwd-kpi-grid">';
	html += _mwd_kpi(co.md_pending, "MD Approvals", co.md_pending > 0 ? co.md_pending + " pending" : "Clear", co.md_pending > 0 ? "#ef4444" : "#22c55e", co.md_pending > 3);
	html += _mwd_kpi(co.vehicles_inside, "Vehicles Inside", co.today_inward + " today", "#8b5cf6", false);
	html += _mwd_kpi(co.today_weight_mt + " MT", "Weight Today", "net received", "#3b82f6", false);
	html += _mwd_kpi(_mwd_fmt_time(co.avg_turnaround_min), "Avg Turnaround", "today", "#0ea5e9", false);
	html += _mwd_kpi(co.stuck_vehicles, "Stuck Vehicles", "CTL breach", co.stuck_vehicles > 0 ? "#ef4444" : "#22c55e", co.stuck_vehicles > 3);
	html += _mwd_kpi(ops.stock_out, "Stock OUT", ops.weight_out_mt + " MT", "#f59e0b", false);
	html += "</div>";

	// Two column: Stage + Gate flow
	html += '<div class="mwd-grid">';

	// Stage distribution
	html += '<div class="mwd-card">';
	html += '<div class="mwd-card-title">Vehicle Distribution by Stage</div>';
	html += _mwd_stage_bar(ops.stages);
	html += _mwd_stage_legend(ops.stages);
	html += "</div>";

	// Gate flow today
	html += '<div class="mwd-card">';
	html += '<div class="mwd-card-title">Today\'s Gate Flow</div>';
	html += `<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;">`;
	html += _mwd_mini("Entries", ops.entries, "#3b82f6");
	html += _mwd_mini("Exits", ops.exits, "#22c55e");
	html += _mwd_mini("Stock IN", ops.stock_in, "#8b5cf6");
	html += _mwd_mini("Stock OUT", ops.stock_out, "#f59e0b");
	html += _mwd_mini("Visitors", ops.visitors, "#ec4899");
	html += "</div>";
	html += `<div style="display:flex;gap:12px;margin-top:16px;">
		<div style="flex:1;padding:14px;background:#0a1628;border-radius:10px;text-align:center;border:1px solid #1e3a5f;">
			<div style="font-size:10px;color:#64748b;text-transform:uppercase;">Weight IN</div>
			<div style="font-size:28px;font-weight:800;color:#3b82f6;">${ops.weight_in_mt} MT</div>
		</div>
		<div style="flex:1;padding:14px;background:#0a1628;border-radius:10px;text-align:center;border:1px solid #1e3a5f;">
			<div style="font-size:10px;color:#64748b;text-transform:uppercase;">Weight OUT</div>
			<div style="font-size:28px;font-weight:800;color:#f59e0b;">${ops.weight_out_mt} MT</div>
		</div>
	</div>`;
	html += "</div>";

	html += "</div>";

	// Non-Branded Items Alert
	const nbi = data.non_branded_items;
	if (nbi && nbi.count > 0) {
		html += `<div class="mwd-card mwd-critical" style="border:2px solid #f59e0b;margin-bottom:16px;">
			<div class="mwd-card-title" style="color:#fbbf24;">&#9888; Non-Branded Items &mdash; <span style="color:#ef4444;font-size:24px;font-weight:800;">${nbi.count}</span> items without brand</div>
			<div style="display:flex;flex-wrap:wrap;gap:6px;max-height:80px;overflow:hidden;">`;
		nbi.items.slice(0, 15).forEach((item) => {
			html += `<span style="padding:4px 10px;background:#451a03;border:1px solid #92400e;border-radius:6px;font-size:11px;color:#fcd34d;">${frappe.utils.escape_html(item.name)}</span>`;
		});
		if (nbi.count > 15) html += `<span style="padding:4px 10px;font-size:11px;color:#f59e0b;">+${nbi.count - 15} more</span>`;
		html += '</div></div>';
	}

	// Three column: Company cards + Quality
	html += '<div class="mwd-grid-3">';

	// Company 1
	if (co.companies && co.companies.length > 0) {
		html += _mwd_company_card(co.companies[0]);
	}
	// Company 2
	if (co.companies && co.companies.length > 1) {
		html += _mwd_company_card(co.companies[1]);
	}

	// Quality
	html += '<div class="mwd-card">';
	html += '<div class="mwd-card-title">Quality</div>';
	html += `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px;">`;
	html += _mwd_mini("Total", q.total, "#94a3b8");
	html += _mwd_mini("Accepted", q.accepted, "#22c55e");
	html += _mwd_mini("Rejected", q.rejected, "#ef4444");
	html += "</div>";
	html += `<div style="padding:14px;background:#0a1628;border-radius:10px;text-align:center;border:1px solid ${q.reject_rate > 5 ? '#7f1d1d' : '#064e3b'};">
		<div style="font-size:10px;color:#64748b;text-transform:uppercase;">Rejection Rate</div>
		<div style="font-size:36px;font-weight:800;color:${q.reject_rate > 5 ? '#ef4444' : '#22c55e'};">${q.reject_rate}%</div>
	</div>`;
	html += "</div>";

	html += "</div>";

	// Footer
	html += `<div class="mwd-footer">Auto-refreshes every 30 seconds &middot; Press ESC to exit &middot; ${frappe.datetime.prettyDate(data.last_updated)}</div>`;

	$("#mwd-container").html(html);
}

/* ── Helpers ── */

function _mwd_kpi(value, label, sub, color, critical) {
	return `<div class="mwd-kpi ${critical ? 'mwd-critical' : ''}">
		<div class="mwd-kpi-val" style="color:${color};">${value}</div>
		<div class="mwd-kpi-label">${label}</div>
		<div class="mwd-kpi-sub">${sub}</div>
	</div>`;
}

function _mwd_mini(label, value, color) {
	return `<div style="padding:10px;background:#0a1628;border-radius:8px;text-align:center;border:1px solid #1e3a5f;">
		<div style="font-size:24px;font-weight:700;color:${color};">${value}</div>
		<div style="font-size:9px;color:#64748b;text-transform:uppercase;margin-top:2px;">${label}</div>
	</div>`;
}

function _mwd_stage_bar(stages) {
	if (!stages || !stages.length) return '<div style="color:#475569;text-align:center;padding:20px;">No vehicles</div>';
	const total = stages.reduce((s, r) => s + r.count, 0);
	const colors = {
		"PO Linked": "#3b82f6", "Gross Weighed": "#8b5cf6", "Quality Done": "#6366f1",
		"Graded": "#a855f7", "Unloading": "#f59e0b", "Tare Weighed": "#10b981",
		"GRN Created": "#059669", "SI Linked": "#0ea5e9", "Tare Recorded": "#06b6d4",
		"Loading Done": "#14b8a6", "Gross Recorded": "#22c55e", "Dispatch Ready": "#84cc16",
	};

	let html = '<div class="mwd-stage-bar">';
	stages.forEach((s) => {
		const pct = (s.count / total) * 100;
		const c = colors[s.stage] || "#6b7280";
		html += `<div class="mwd-stage-segment" style="width:${pct}%;background:${c};" title="${s.stage}: ${s.count}">${s.count > 0 ? s.count : ""}</div>`;
	});
	html += "</div>";
	html += `<div style="text-align:center;font-size:14px;font-weight:700;color:#e2e8f0;">${total} vehicles inside</div>`;
	return html;
}

function _mwd_stage_legend(stages) {
	if (!stages || !stages.length) return "";
	const colors = {
		"PO Linked": "#3b82f6", "Gross Weighed": "#8b5cf6", "Quality Done": "#6366f1",
		"Graded": "#a855f7", "Unloading": "#f59e0b", "Tare Weighed": "#10b981",
		"GRN Created": "#059669", "SI Linked": "#0ea5e9", "Tare Recorded": "#06b6d4",
		"Loading Done": "#14b8a6", "Gross Recorded": "#22c55e", "Dispatch Ready": "#84cc16",
	};
	let html = '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;">';
	stages.forEach((s) => {
		const c = colors[s.stage] || "#6b7280";
		html += `<span style="display:inline-flex;align-items:center;gap:4px;font-size:10px;color:${c};padding:3px 8px;background:${c}15;border-radius:6px;">
			<span style="width:6px;height:6px;border-radius:50%;background:${c};"></span>${s.stage}: ${s.count}
		</span>`;
	});
	html += "</div>";
	return html;
}

function _mwd_company_card(c) {
	const bc = c.budget_pct > 80 ? "#ef4444" : c.budget_pct > 60 ? "#f59e0b" : "#22c55e";
	return `<div class="mwd-card">
		<div class="mwd-card-title">${frappe.utils.escape_html(c.short_name)}</div>
		<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
			<div style="padding:10px;background:#0a1628;border-radius:8px;text-align:center;border:1px solid #1e3a5f;">
				<div style="font-size:10px;color:#64748b;">PO Value</div>
				<div style="font-size:16px;font-weight:700;color:#22c55e;">${format_currency(c.po_value)}</div>
			</div>
			<div style="padding:10px;background:#0a1628;border-radius:8px;text-align:center;border:1px solid #1e3a5f;">
				<div style="font-size:10px;color:#64748b;">Invoiced</div>
				<div style="font-size:16px;font-weight:700;color:#3b82f6;">${format_currency(c.pi_value)}</div>
			</div>
		</div>
		<div class="mwd-budget-row">
			<div class="mwd-budget-label">
				<span style="color:#94a3b8;">Budget</span>
				<span style="color:${bc};font-weight:700;">${c.budget_pct}%</span>
			</div>
			<div class="mwd-budget-track"><div class="mwd-budget-fill" style="width:${Math.min(c.budget_pct, 100)}%;background:${bc};"></div></div>
		</div>
		${c.pending_pos > 0 ? `<div style="padding:6px;background:#7f1d1d30;border-radius:6px;text-align:center;font-size:11px;color:#fca5a5;font-weight:600;margin-top:8px;">${c.pending_pos} POs pending</div>` : ""}
	</div>`;
}

function _mwd_fmt_time(min) {
	if (!min || min <= 0) return "—";
	const h = Math.floor(min / 60);
	const m = Math.round(min % 60);
	return h > 0 ? h + "h " + m + "m" : m + "m";
}
