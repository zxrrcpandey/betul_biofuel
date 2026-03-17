frappe.pages["budget-control-matrix"].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Budget Control Matrix",
		single_column: true,
	});

	page.main.html('<div id="budget-matrix-container" style="padding: 15px;"></div>');

	// Filters
	const fiscal_year_field = page.add_field({
		fieldname: "fiscal_year",
		label: __("Fiscal Year"),
		fieldtype: "Link",
		options: "Fiscal Year",
		default: frappe.defaults.get_global_default("fiscal_year"),
		change() { _refresh_matrix(page); }
	});

	const company_field = page.add_field({
		fieldname: "company",
		label: __("Company"),
		fieldtype: "Link",
		options: "Company",
		default: frappe.defaults.get_global_default("company"),
		change() { _refresh_matrix(page); }
	});

	const view_field = page.add_field({
		fieldname: "view_mode",
		label: __("View"),
		fieldtype: "Select",
		options: "Summary\nDetailed (by Account)",
		default: "Summary",
		change() { _refresh_matrix(page); }
	});

	// Initial load
	setTimeout(() => _refresh_matrix(page), 500);
};

frappe.pages["budget-control-matrix"].refresh = function(wrapper) {
	const page = wrapper.page;
	_refresh_matrix(page);
};

let _matrix_timeout = null;
function _refresh_matrix(page) {
	clearTimeout(_matrix_timeout);
	_matrix_timeout = setTimeout(() => _do_refresh_matrix(page), 300);
}

function _do_refresh_matrix(page) {
	const fiscal_year = page.fields_dict.fiscal_year?.get_value();
	const company = page.fields_dict.company?.get_value();
	const view_mode = page.fields_dict.view_mode?.get_value() || "Summary";

	if (!fiscal_year || !company) return;

	const $container = $("#budget-matrix-container");
	$container.html('<div style="text-align: center; padding: 40px; color: #9ca3af;">Loading...</div>');

	frappe.call({
		method: "trustbit_ethanol.bbf_gate_entry.bbf_budget.get_budget_dashboard",
		args: { fiscal_year, company },
		callback(r) {
			if (!r.message || !r.message.data.length) {
				$container.html('<div style="text-align: center; padding: 40px; color: #9ca3af;">No budget data found for this fiscal year.</div>');
				return;
			}

			const { data, summary } = r.message;

			let html = "";

			// Summary cards
			html += _render_summary_cards(summary);

			// Matrix table
			if (view_mode === "Summary") {
				html += _render_summary_table(data);
			} else {
				html += '<div style="color: #6b7280; padding: 15px; text-align: center;">Detailed account-wise view coming soon. Use the BBF Budget Dashboard report for account-level breakdown.</div>';
			}

			$container.html(html);
		},
		error() {
			$container.html('<div style="text-align: center; padding: 40px; color: #ef4444;">Error loading budget data.</div>');
		}
	});
}

function _render_summary_cards(summary) {
	const fmt = (v) => format_currency(v);

	const cards = [
		{ label: "Total Budget", value: fmt(summary.total_budget), color: "#3b82f6", bg: "#eff6ff" },
		{ label: "Committed (POs)", value: fmt(summary.total_committed), color: "#f59e0b", bg: "#fffbeb" },
		{ label: "Actual Spent", value: fmt(summary.total_actual), color: "#10b981", bg: "#ecfdf5" },
		{ label: "Available", value: fmt(summary.total_available), color: summary.total_available < 0 ? "#ef4444" : "#10b981", bg: summary.total_available < 0 ? "#fef2f2" : "#ecfdf5" },
		{ label: "Utilization", value: summary.overall_utilization + "%", color: summary.overall_utilization > 100 ? "#ef4444" : (summary.overall_utilization >= 80 ? "#f59e0b" : "#10b981"), bg: "#f9fafb" },
	];

	let html = '<div style="display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap;">';
	cards.forEach(card => {
		html += `<div style="flex: 1; min-width: 150px; padding: 15px; background: ${card.bg}; border-radius: 10px; border: 1px solid ${card.color}20;">`;
		html += `<div style="font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px;">${card.label}</div>`;
		html += `<div style="font-size: 20px; font-weight: 700; color: ${card.color}; margin-top: 4px;">${card.value}</div>`;
		html += `</div>`;
	});
	html += '</div>';

	return html;
}

function _render_summary_table(data) {
	let html = '<div style="overflow-x: auto;">';
	html += '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">';

	// Header
	html += '<thead><tr style="background: #f3f4f6;">';
	["Cost Center", "Annual Budget", "Committed (POs)", "Actual Spent", "Available", "Utilization", "Status"].forEach(h => {
		html += `<th style="padding: 10px 14px; text-align: left; border-bottom: 2px solid #e5e7eb; font-weight: 600; color: #374151;">${h}</th>`;
	});
	html += '</tr></thead>';

	// Body
	html += '<tbody>';
	data.forEach((row, i) => {
		const bg = i % 2 === 0 ? "#ffffff" : "#f9fafb";
		const statusColor = row.status === "Exceeded" ? "#ef4444" : (row.status === "Warning" ? "#f59e0b" : "#10b981");
		const statusIcon = row.status === "Exceeded" ? "⚠" : (row.status === "Warning" ? "⚡" : "✓");
		const availColor = row.available < 0 ? "#ef4444" : "#111827";

		// Utilization bar
		const pct = Math.min(row.utilization_pct, 100);
		const barColor = row.utilization_pct > 100 ? "#ef4444" : (row.utilization_pct >= 80 ? "#f59e0b" : "#10b981");

		html += `<tr style="background: ${bg};">`;
		html += `<td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb; font-weight: 500;">${frappe.utils.escape_html(row.cost_center)}</td>`;
		html += `<td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;">${format_currency(row.annual_budget)}</td>`;
		html += `<td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;">${format_currency(row.committed)}</td>`;
		html += `<td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;">${format_currency(row.actual_spent)}</td>`;
		html += `<td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb; color: ${availColor}; font-weight: 600;">${format_currency(row.available)}</td>`;

		// Utilization bar cell
		html += `<td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;">`;
		html += `<div style="display: flex; align-items: center; gap: 8px;">`;
		html += `<div style="flex: 1; height: 6px; background: #e5e7eb; border-radius: 3px; min-width: 60px;">`;
		html += `<div style="height: 100%; width: ${pct}%; background: ${barColor}; border-radius: 3px;"></div>`;
		html += `</div>`;
		html += `<span style="font-size: 11px; font-weight: 600; color: ${barColor}; min-width: 35px;">${row.utilization_pct}%</span>`;
		html += `</div></td>`;

		// Status
		html += `<td style="padding: 10px 14px; border-bottom: 1px solid #e5e7eb;">`;
		html += `<span style="color: ${statusColor}; font-weight: 600;">${statusIcon} ${row.status}</span>`;
		html += `</td>`;

		html += '</tr>';
	});
	html += '</tbody></table></div>';

	return html;
}
