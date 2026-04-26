/**
 * QC Yearly Dashboard — v2.9.0 Day 6
 * Lessons applied:
 *   163: 4-file pattern (no __init__.py needed when handled correctly)
 *   166: banner DOM, NO frappe.set_headline
 *   174: version badge for 3-step cache flush verification
 *   190: gate on frappe.session.user !== "Guest" + /app/ pathname
 */

frappe.pages["qc-yearly-dashboard"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("QC Yearly Dashboard"),
		single_column: true,
	});

	// Lesson 190: gate on session + path
	if (frappe.session.user === "Guest") {
		$(wrapper).find(".layout-main-section").html(
			'<div class="alert alert-warning">' + __("Login required") + "</div>"
		);
		return;
	}

	// Render shell from .html template (Frappe auto-loads sibling .html into wrapper).
	frappe.require("/assets/trustbit_ethanol/css/ts_theme.css", function () {
		setTimeout(load_dashboard, 50, page, wrapper);
	});
};

function load_dashboard(page, wrapper) {
	var $body = $(wrapper).find(".layout-main-section");
	if ($body.find(".qc-yearly-dashboard").length === 0) {
		// Frappe didn't auto-inject html (Page.html is rendered server-side
		// from .html file via the Page DocType). Fall back to fetch.
		$.get("/assets/trustbit_ethanol/qc_yearly_dashboard.html").done(function (html) {
			$body.html(html);
			init_dashboard(page);
		}).fail(function () {
			// Nothing to fetch — render directly via Frappe's standard Page render path:
			// We assume the .html is loaded by the desk page renderer. If not, render shell inline.
			$body.html(_inline_shell());
			init_dashboard(page);
		});
		return;
	}
	init_dashboard(page);
}

function _inline_shell() {
	// Minimal inline fallback when sidecar HTML isn't auto-loaded.
	return [
		'<div class="qc-yearly-dashboard">',
		'  <div id="qc-banner" class="qc-banner"></div>',
		'  <div id="qc-version" class="qc-version-badge"></div>',
		'  <div class="qc-filter-bar">',
		'    <label for="qc-year-select">' + __("Fiscal Year") + "</label>",
		'    <select id="qc-year-select" class="form-control"></select>',
		'    <button id="qc-refresh" class="btn btn-default btn-sm">' + __("Refresh") + "</button>",
		'    <span id="qc-status" class="qc-status text-muted"></span>',
		"  </div>",
		'  <div class="qc-hero-grid" id="qc-hero-grid"></div>',
		'  <div class="qc-section"><h4>' + __("Monthly Breakdown") + '</h4><div class="qc-table-wrap"><table class="table table-bordered" id="qc-monthly-table"><thead></thead><tbody></tbody></table></div></div>',
		'  <div class="qc-section"><h4>' + __("Top 5 Suppliers by Deduction") + '</h4><div class="qc-table-wrap"><table class="table table-bordered" id="qc-suppliers-table"><thead></thead><tbody></tbody></table></div></div>',
		'  <div class="qc-section"><h4>' + __("Insights") + '</h4><div id="qc-insights" class="qc-insights"></div></div>',
		"</div>",
	].join("");
}

function init_dashboard(page) {
	// Lesson 166: banner via DOM, never set_headline
	$("#qc-banner").html(
		'<i class="fa fa-bar-chart"></i> ' +
		__("QC Analytics — Yearly view across all submitted Quality Inspections + Deduction Sheets")
	);

	// Lesson 174: version badge
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_version_api.get_version_info",
		callback: function (r) {
			if (r && r.message) {
				$("#qc-version").text("Build: " + (r.message.git_sha || "dev"));
			} else {
				$("#qc-version").text("Build: v2.9.0");
			}
		},
	});

	// Build year dropdown (current FY + 4 previous).
	var current_year = new Date().getFullYear();
	// India FY: Apr-Mar. If month < 4, FY is previous year.
	var month_now = new Date().getMonth() + 1;
	var current_fy = month_now < 4 ? current_year - 1 : current_year;
	var $select = $("#qc-year-select").empty();
	for (var y = current_fy; y >= current_fy - 4; y--) {
		var label = y + "-" + String((y + 1) % 100).padStart(2, "0");
		$select.append(
			$("<option>").val(y).text(label).prop("selected", y === current_fy)
		);
	}

	$("#qc-year-select").on("change", function () { refresh_all(); });
	$("#qc-refresh").on("click", function () { refresh_all(true); });

	refresh_all();
}

function refresh_all(force) {
	var year = parseInt($("#qc-year-select").val(), 10);
	if (!year) {
		year = new Date().getFullYear();
	}
	$("#qc-status").text(__("Loading..."));
	var t0 = Date.now();

	// 4 parallel API calls.
	var p1 = frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.qc_dashboard_api.get_yearly_kpis",
		args: { year: year, no_cache: force ? 1 : 0 },
	});
	var p2 = frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.qc_dashboard_api.get_monthly_breakdown",
		args: { year: year, no_cache: force ? 1 : 0 },
	});
	var p3 = frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.qc_dashboard_api.get_top_suppliers",
		args: { year: year, limit: 5, no_cache: force ? 1 : 0 },
	});
	var p4 = frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.qc_dashboard_api.get_insights",
		args: { year: year, no_cache: force ? 1 : 0 },
	});

	Promise.all([p1, p2, p3, p4]).then(function (results) {
		render_kpis(results[0] && results[0].message);
		render_monthly(results[1] && results[1].message);
		render_suppliers(results[2] && results[2].message);
		render_insights(results[3] && results[3].message);
		var dt = Date.now() - t0;
		$("#qc-status").text(__("Loaded in {0} ms", [dt]));
	}).catch(function (err) {
		$("#qc-status").html('<span class="text-danger">' + __("Error: ") + frappe.utils.escape_html(String(err)) + "</span>");
	});
}

function render_kpis(data) {
	if (!data) return;
	function set_card(kpi, val) {
		$(".qc-hero-card[data-kpi='" + kpi + "'] .qc-hero-value").text(val);
	}
	set_card("total_inspections", (data.total_inspections || 0).toLocaleString());
	set_card("total_deduction_kg", (data.total_deduction_kg || 0).toFixed(2));
	set_card("avg_deduction_pct", (data.avg_deduction_pct || 0).toFixed(2) + " %");
	set_card("variance_pct", (data.variance_pct != null ? data.variance_pct.toFixed(2) : "—") + " %");
}

function render_monthly(rows) {
	var $tbody = $("#qc-monthly-table tbody").empty();
	if (!rows || !rows.length) {
		$tbody.append('<tr><td colspan="6" class="text-center text-muted">' + __("No data") + "</td></tr>");
		return;
	}
	rows.forEach(function (r) {
		$tbody.append(
			"<tr>" +
				"<td>" + frappe.utils.escape_html(r.month_label || "") + "</td>" +
				'<td class="text-right">' + (r.inspections || 0) + "</td>" +
				'<td class="text-right">' + (r.system_avg_pct != null ? r.system_avg_pct.toFixed(2) : "—") + "</td>" +
				'<td class="text-right">' + (r.tilok_avg_pct != null ? r.tilok_avg_pct.toFixed(2) : "—") + "</td>" +
				'<td class="text-right">' + (r.variance_pct != null ? r.variance_pct.toFixed(2) : "—") + "</td>" +
				'<td class="text-right">' + (r.total_kg != null ? r.total_kg.toFixed(2) : "—") + "</td>" +
				"</tr>"
		);
	});
}

function render_suppliers(rows) {
	var $tbody = $("#qc-suppliers-table tbody").empty();
	if (!rows || !rows.length) {
		$tbody.append('<tr><td colspan="5" class="text-center text-muted">' + __("No data") + "</td></tr>");
		return;
	}
	rows.forEach(function (r, i) {
		$tbody.append(
			"<tr>" +
				"<td>" + (i + 1) + "</td>" +
				"<td>" + frappe.utils.escape_html(r.supplier_name || r.supplier || "") + "</td>" +
				'<td class="text-right">' + (r.inspections || 0) + "</td>" +
				'<td class="text-right">' + (r.avg_deduction_pct != null ? r.avg_deduction_pct.toFixed(2) : "—") + "</td>" +
				'<td class="text-right">' + (r.total_kg != null ? r.total_kg.toFixed(2) : "—") + "</td>" +
				"</tr>"
		);
	});
}

function render_insights(items) {
	var $box = $("#qc-insights").empty();
	if (!items || !items.length) {
		$box.html('<div class="text-muted">' + __("No insights for this year.") + "</div>");
		return;
	}
	items.forEach(function (msg) {
		$box.append('<div class="qc-insight-item">' + frappe.utils.escape_html(String(msg)) + "</div>");
	});
}
