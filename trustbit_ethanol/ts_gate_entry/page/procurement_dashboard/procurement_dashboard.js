frappe.pages["procurement-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Purchase & Procurement Dashboard",
		single_column: true,
	});

	page.main.html('<div id="proc-dash-container" style="padding: 15px;"></div>');

	page.add_field({
		fieldname: "date_range",
		label: __("Period"),
		fieldtype: "Select",
		options: "Today\nThis Week\nThis Month\nThis Quarter\nThis Year",
		default: "This Month",
		change() { _pd_refresh(page); },
	});

	page.add_field({
		fieldname: "company",
		label: __("Company"),
		fieldtype: "Link",
		options: "Company",
		default: frappe.defaults.get_global_default("company"),
		change() { _pd_refresh(page); },
	});

	page.add_field({
		fieldname: "category",
		label: __("Category"),
		fieldtype: "Link",
		options: "TS Purchase Category",
		change() { _pd_refresh(page); },
	});

	setTimeout(() => _pd_refresh(page), 500);
};

frappe.pages["procurement-dashboard"].refresh = function (wrapper) {
	_pd_refresh(wrapper.page);
};

let _pd_timeout = null;
function _pd_refresh(page) {
	clearTimeout(_pd_timeout);
	_pd_timeout = setTimeout(() => _pd_do_refresh(page), 300);
}

function _pd_do_refresh(page) {
	const date_range = page.fields_dict.date_range?.get_value() || "This Month";
	const company = page.fields_dict.company?.get_value();
	const category = page.fields_dict.category?.get_value();

	const $c = $("#proc-dash-container");
	$c.html('<div style="text-align:center;padding:40px;color:#9ca3af;">Loading...</div>');

	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_dashboard_procurement.get_procurement_dashboard",
		args: { date_range, company, category },
		callback(r) {
			if (!r.message) {
				$c.html('<div style="text-align:center;padding:40px;color:#9ca3af;">No data found.</div>');
				return;
			}
			_pd_render(r.message, $c);
		},
		error() {
			$c.html('<div style="text-align:center;padding:40px;color:#ef4444;">Error loading dashboard.</div>');
		},
	});
}

function _pd_render(data, $c) {
	let html = "";

	// KPI Cards
	html += _pd_kpi_cards(data.summary, data.avg_cycle_time);

	// Two-column layout
	html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px;">';

	// Left: PO Pipeline by Category
	html += '<div style="background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">';
	html += '<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">PO Pipeline by Category</div>';
	html += _pd_pipeline_table(data.po_pipeline);
	html += "</div>";

	// Right: Pending Approval Steps
	html += '<div style="background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">';
	html += '<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">Pending at Approval Steps</div>';
	html += _pd_pending_steps(data.pending_steps);
	html += "</div>";

	html += "</div>";

	// Second row
	html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px;">';

	// Left: MR Pipeline
	html += '<div style="background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">';
	html += '<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">Material Request Pipeline</div>';
	html += _pd_mr_pipeline(data.mr_pipeline);
	html += "</div>";

	// Right: Avg Cycle Time by Category
	html += '<div style="background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">';
	html += '<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">Avg Approval Cycle Time</div>';
	html += _pd_cycle_time(data.avg_cycle_time);
	html += "</div>";

	html += "</div>";

	// Third row
	html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px;">';

	// Left: Top Suppliers
	html += '<div style="background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">';
	html += '<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">Top Suppliers by Value</div>';
	html += _pd_suppliers(data.top_suppliers);
	html += "</div>";

	// Right: Monthly Trend
	html += '<div style="background:white;border:1px solid #e2e8f0;border-radius:10px;padding:16px;">';
	html += '<div style="font-weight:700;font-size:14px;color:#0f172a;margin-bottom:12px;">Monthly PO Trend (6 months)</div>';
	html += '<div id="pd-monthly-chart" style="height:250px;"></div>';
	html += "</div>";

	html += "</div>";

	$c.html(html);

	// Render chart after DOM insertion
	_pd_render_monthly_chart(data.monthly_trend);
}

/* ── KPI Cards ──────────────────────────────────────────────── */

function _pd_kpi_cards(s, cycle) {
	const cards = [
		{ label: "Total POs", value: s.total_pos, color: "#3b82f6", bg: "#eff6ff",
		  click: "List/Purchase Order" },
		{ label: "Approved Value", value: format_currency(s.approved_value), color: "#10b981", bg: "#ecfdf5",
		  click: "List/Purchase Order/docstatus=1" },
		{ label: "Pending Approval", value: s.pending_count, color: "#f59e0b", bg: "#fffbeb",
		  click: "List/Purchase Order/ts_approval_status=%5B%22like%22%2C%22Pending%25%22%5D" },
		{ label: "Rejected", value: s.rejected_count, color: "#ef4444", bg: "#fef2f2",
		  click: "List/Purchase Order/ts_approval_status=Rejected" },
		{ label: "Avg Cycle Time", value: cycle.overall_hours + "h", color: "#8b5cf6", bg: "#faf5ff" },
	];

	let html = '<div style="display:flex;gap:12px;flex-wrap:wrap;">';
	cards.forEach((c) => {
		const onclick = c.click ? `onclick="frappe.set_route('${c.click}')" style="cursor:pointer;"` : "";
		html += `<div ${onclick} style="flex:1;min-width:150px;padding:15px;background:${c.bg};border-radius:10px;border:1px solid ${c.color}20;">`;
		html += `<div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">${c.label}</div>`;
		html += `<div style="font-size:22px;font-weight:700;color:${c.color};margin-top:4px;">${c.value}</div>`;
		html += "</div>";
	});
	html += "</div>";
	return html;
}

/* ── PO Pipeline Table ──────────────────────────────────────── */

function _pd_pipeline_table(pipeline) {
	if (!pipeline || !pipeline.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No PO data</div>';

	const statuses = ["Draft", "Pending", "Awaiting", "Approved", "Rejected", "Revised"];
	const colors = { Draft: "#6b7280", Pending: "#f59e0b", Awaiting: "#3b82f6", Approved: "#10b981", Rejected: "#ef4444", Revised: "#f97316" };

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
		html += `<td style="padding:8px;border-bottom:1px solid #f1f5f9;font-weight:500;">${_pd_esc(row.category)}</td>`;
		statuses.forEach((s) => {
			const v = row[s] || 0;
			total += v;
			const badge = v > 0 ? `<span style="background:${colors[s]}15;color:${colors[s]};padding:2px 8px;border-radius:10px;font-weight:600;">${v}</span>` : '<span style="color:#d1d5db;">0</span>';
			html += `<td style="padding:8px;text-align:center;border-bottom:1px solid #f1f5f9;">${badge}</td>`;
		});
		html += `<td style="padding:8px;text-align:center;border-bottom:1px solid #f1f5f9;font-weight:700;">${total}</td>`;
		html += "</tr>";
	});

	html += "</tbody></table>";
	return html;
}

/* ── Pending Steps ──────────────────────────────────────────── */

function _pd_pending_steps(steps) {
	if (!steps || !steps.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No pending POs</div>';

	let html = '<div style="display:flex;flex-direction:column;gap:8px;">';
	steps.forEach((s) => {
		const step_label = _pd_esc(s.status);
		html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 12px;background:#fffbeb;border-radius:8px;border:1px solid #fcd34d30;">';
		html += `<div>`;
		html += `<div style="font-weight:600;font-size:13px;color:#92400e;">${step_label}</div>`;
		html += `<div style="font-size:11px;color:#b45309;">Step ${s.current_step || "?"} of ${s.total_steps || "?"}</div>`;
		html += `</div>`;
		html += `<div style="text-align:right;">`;
		html += `<div style="font-size:20px;font-weight:700;color:#f59e0b;">${s.cnt}</div>`;
		html += `<div style="font-size:10px;color:#b45309;">${format_currency(s.total_value || 0)}</div>`;
		html += `</div>`;
		html += "</div>";
	});
	html += "</div>";
	return html;
}

/* ── MR Pipeline ────────────────────────────────────────────── */

function _pd_mr_pipeline(mr) {
	if (!mr) return "";

	const items = [
		{ label: "Draft", count: mr.Draft, color: "#6b7280" },
		{ label: "Pending", count: mr.Pending, color: "#f59e0b" },
		{ label: "Approved", count: mr.Approved, color: "#10b981" },
		{ label: "Rejected", count: mr.Rejected, color: "#ef4444" },
		{ label: "Revised", count: mr.Revised, color: "#f97316" },
	];

	let html = '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;">';
	items.forEach((item) => {
		html += `<div style="flex:1;min-width:80px;text-align:center;padding:12px 8px;background:${item.color}10;border-radius:8px;border:1px solid ${item.color}20;">`;
		html += `<div style="font-size:20px;font-weight:700;color:${item.color};">${item.count}</div>`;
		html += `<div style="font-size:10px;color:#6b7280;margin-top:2px;">${item.label}</div>`;
		html += "</div>";
	});
	html += "</div>";

	// Total bar
	const total = mr.total || 1;
	html += '<div style="height:8px;background:#e5e7eb;border-radius:4px;overflow:hidden;display:flex;">';
	items.forEach((item) => {
		const pct = ((item.count || 0) / total) * 100;
		if (pct > 0) {
			html += `<div style="width:${pct}%;background:${item.color};"></div>`;
		}
	});
	html += "</div>";

	return html;
}

/* ── Cycle Time ─────────────────────────────────────────────── */

function _pd_cycle_time(cycle) {
	if (!cycle || !cycle.by_category || !cycle.by_category.length) {
		return '<div style="color:#9ca3af;padding:20px;text-align:center;">No approved POs in this period</div>';
	}

	let html = '<div style="display:flex;flex-direction:column;gap:8px;">';
	cycle.by_category.forEach((c) => {
		const hrs = Math.round(c.avg_hours || 0);
		const color = hrs > 48 ? "#ef4444" : hrs > 24 ? "#f59e0b" : "#10b981";
		const bar_pct = Math.min((hrs / 72) * 100, 100);

		html += '<div style="padding:8px 12px;background:#f8fafc;border-radius:8px;">';
		html += `<div style="display:flex;justify-content:space-between;margin-bottom:4px;">`;
		html += `<span style="font-size:12px;font-weight:500;color:#374151;">${_pd_esc(c.category)}</span>`;
		html += `<span style="font-size:12px;font-weight:700;color:${color};">${hrs}h <span style="font-weight:400;color:#9ca3af;">(${c.cnt} POs)</span></span>`;
		html += `</div>`;
		html += `<div style="height:6px;background:#e5e7eb;border-radius:3px;">`;
		html += `<div style="height:100%;width:${bar_pct}%;background:${color};border-radius:3px;"></div>`;
		html += `</div>`;
		html += "</div>";
	});
	html += "</div>";
	return html;
}

/* ── Top Suppliers ──────────────────────────────────────────── */

function _pd_suppliers(suppliers) {
	if (!suppliers || !suppliers.length) return '<div style="color:#9ca3af;padding:20px;text-align:center;">No supplier data</div>';

	const max_val = suppliers[0]?.total_value || 1;

	let html = '<div style="display:flex;flex-direction:column;gap:6px;">';
	suppliers.forEach((s, i) => {
		const pct = ((s.total_value || 0) / max_val) * 100;
		html += '<div style="display:flex;align-items:center;gap:10px;padding:6px 0;">';
		html += `<div style="width:20px;text-align:right;font-size:11px;color:#9ca3af;font-weight:600;">${i + 1}</div>`;
		html += `<div style="flex:1;">`;
		html += `<div style="display:flex;justify-content:space-between;margin-bottom:2px;">`;
		html += `<span style="font-size:12px;font-weight:500;color:#374151;">${_pd_esc(s.supplier_name || s.supplier)}</span>`;
		html += `<span style="font-size:11px;color:#6b7280;">${s.po_count} POs</span>`;
		html += `</div>`;
		html += `<div style="height:6px;background:#e5e7eb;border-radius:3px;">`;
		html += `<div style="height:100%;width:${pct}%;background:#3b82f6;border-radius:3px;"></div>`;
		html += `</div>`;
		html += `<div style="font-size:11px;color:#3b82f6;font-weight:600;margin-top:2px;">${format_currency(s.total_value)}</div>`;
		html += `</div>`;
		html += "</div>";
	});
	html += "</div>";
	return html;
}

/* ── Monthly Chart ──────────────────────────────────────────── */

function _pd_render_monthly_chart(trend) {
	const $el = document.getElementById("pd-monthly-chart");
	if (!$el || !trend || !trend.length) {
		if ($el) $el.innerHTML = '<div style="color:#9ca3af;padding:40px;text-align:center;">No trend data</div>';
		return;
	}

	const labels = trend.map((t) => t.month);
	const values = trend.map((t) => Math.round((t.value || 0) / 100000));

	new frappe.Chart($el, {
		data: {
			labels: labels,
			datasets: [{ name: "PO Value (₹ Lakhs)", values: values }],
		},
		type: "bar",
		height: 220,
		colors: ["#3b82f6"],
		barOptions: { spaceRatio: 0.4 },
		tooltipOptions: {
			formatTooltipY: (d) => "₹ " + d.toLocaleString("en-IN") + " L",
		},
	});
}

/* ── Helpers ────────────────────────────────────────────────── */

function _pd_esc(v) {
	return frappe.utils.escape_html(v || "");
}
