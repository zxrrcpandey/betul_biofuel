// v2.8.3 Vehicles In Plant — live operations board.
// Build: v2.8.3 (two-pass gate flow)
// 3-step flush pattern (Lesson 174): bench build + bench clear-website-cache + hard refresh.
frappe.pages["vehicles-in-plant"].on_page_load = function (wrapper) {
	// v2.8.3 Phase 4 Fix 5d (Lesson 190): guard against Guest session / non-/app routes.
	if (!frappe.session.user || frappe.session.user === "Guest") return;

	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Vehicles In Plant"),
		single_column: true,
	});

	// v2.8.3 Phase 4 Fix 5a: CSS variables for dark-mode parity (no hard-coded colors).
	// v2.8.3 Phase 4 Fix 5b: wrap table in an overflow-x scroller for mobile.
	$(page.body).html(`
		<div class="vip-wrapper" style="padding:15px 20px;">
			<div class="vip-header" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;gap:12px;flex-wrap:wrap;">
				<div style="display:flex;gap:10px;align-items:center;">
					<span id="vip-count-badge" style="background:var(--primary-color,#2563eb);color:var(--neutral,#fff);padding:4px 12px;border-radius:999px;font-weight:600;font-size:13px;">0 vehicles</span>
					<span id="vip-last-refresh" style="font-size:11px;color:var(--text-muted);">-</span>
				</div>
				<button id="vip-refresh-btn" class="btn btn-sm btn-primary">Refresh</button>
			</div>
			<div id="vip-table-wrap" style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:8px;overflow:hidden;">
				<div style="overflow-x:auto; -webkit-overflow-scrolling:touch;">
					<table class="table table-hover" style="margin:0;color:var(--heading-color);">
						<thead style="background:var(--fg-color);">
							<tr>
								<th>Token</th>
								<th>Vehicle</th>
								<th>Driver</th>
								<th>Supplier</th>
								<th>PO</th>
								<th>G2 Entry</th>
								<th>Time on Premises</th>
								<th>Stage</th>
							</tr>
						</thead>
						<tbody id="vip-tbody"><tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:30px;">Loading...</td></tr></tbody>
					</table>
				</div>
			</div>
			<div style="margin-top:8px;font-size:11px;color:var(--text-muted);text-align:right;">Build: v2.8.3 two-pass gate flow</div>
		</div>
	`);

	const fmtTime = (dt) => {
		if (!dt) return "-";
		const d = String(dt).split(" ");
		if (d.length >= 2) return d[1].slice(0, 5) + " " + d[0];
		return dt;
	};

	const esc = (s) => frappe.utils.escape_html(String(s == null ? "" : s));

	// v2.8.3 Phase 4 Fix 5e: per-status color mapping, CSS variables with hex fallback.
	const statusColors = {
		"G2 Entered": "var(--blue-500,#3b82f6)",
		"Gross Weighed": "var(--amber-500,#f59e0b)",
		"Quality Done": "var(--amber-500,#f59e0b)",
		"Graded": "var(--amber-500,#f59e0b)",
		"Unloading": "var(--amber-500,#f59e0b)",
		"Tare Weighed": "var(--amber-600,#d97706)",
		"GRN Created": "var(--green-500,#10b981)",
		"Plant Exited": "var(--purple-500,#8b5cf6)",
	};

	const loadData = () => {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.vehicles_in_plant_api.get_vehicles_in_plant",
			type: "GET",
			callback: (r) => {
				const rows = (r && r.message) || [];
				const tbody = $("#vip-tbody");
				tbody.empty();
				$("#vip-count-badge").text(`${rows.length} vehicle${rows.length === 1 ? "" : "s"}`);
				$("#vip-last-refresh").text("Last refresh: " + frappe.datetime.now_time());
				if (!rows.length) {
					tbody.append(`<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:30px;">No vehicles currently in plant.</td></tr>`);
					return;
				}
				rows.forEach((t) => {
					const color = statusColors[t.status] || "var(--text-muted)";
					const tr = $(`<tr>
						<td><a href="/app/ts-token/${encodeURIComponent(t.name)}" style="color:var(--primary-color,#1d4ed8);font-weight:500;">${esc(t.token_number)}</a></td>
						<td>${esc(t.vehicle_number)}</td>
						<td>${esc(t.driver_name)}</td>
						<td>${esc(t.supplier_name)}</td>
						<td>${esc(t.purchase_order)}</td>
						<td>${esc(fmtTime(t.g2_mat_entry_time))}</td>
						<td>${esc(t.time_on_premises_min != null ? t.time_on_premises_min + " min" : "-")}</td>
						<td><span style="color:${color};font-weight:600;">${esc(t.status)}</span></td>
					</tr>`);
					tbody.append(tr);
				});
			},
			// v2.8.3 Phase 4 Fix 5c: network / server errors should surface a retry UI.
			error: () => {
				$("#vip-tbody").html(
					`<tr><td colspan="8" style="text-align:center;color:var(--red-500,#dc2626);padding:30px;">Failed to load. <a href="#" onclick="location.reload();return false;">Retry</a></td></tr>`
				);
			},
		});
	};

	$(page.body).on("click", "#vip-refresh-btn", loadData);
	loadData();
	// poll every 30s
	page._vip_timer = setInterval(loadData, 30000);
	// clean up the timer when navigating away
	$(wrapper).on("remove", () => { try { clearInterval(page._vip_timer); } catch (e) {} });
};
