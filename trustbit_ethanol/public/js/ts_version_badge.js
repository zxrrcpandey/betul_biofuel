/**
 * Version badge in Frappe navbar.
 *
 * Calls trustbit_ethanol.ts_gate_entry.ts_version_api.get_version_info and
 * injects a small monospace badge to the right of the page-header showing
 * the deployed git commit + date. Helps confirm that prod/demo is actually
 * running the version you think it's running.
 *
 * Hover = full commit message.
 */

(function () {
	"use strict";

	function injectBadge(info) {
		if (!info || !info.commit || info.commit === "unknown") return;

		// Remove any existing badge first (idempotent across page transitions)
		$(".ts-version-badge").remove();

		const branch_part = info.branch && info.branch !== "develop"
			? `<span style="color:#f59e0b;font-weight:bold;"> · ${info.branch}</span>`
			: "";

		const tooltip = (info.message || "").replace(/"/g, "&quot;");
		const $badge = $(`
			<div class="ts-version-badge"
			     title="${tooltip}\n${info.date}"
			     style="
			        display:inline-flex;
			        align-items:center;
			        padding:3px 10px;
			        margin-right:10px;
			        background:#f1f5f9;
			        border:1px solid #cbd5e1;
			        border-radius:12px;
			        font-family:monospace;
			        font-size:11px;
			        color:#475569;
			        cursor:help;
			        user-select:all;
			     ">
			  <span style="color:#1e40af;font-weight:bold;">${info.commit}</span>
			  <span style="margin:0 4px;color:#94a3b8;">·</span>
			  <span>${info.date}</span>${branch_part}
			</div>
		`);

		// Try to place it inside the top navbar (right side) — fall back to body top
		const $target = $(".navbar .navbar-home").first();
		if ($target.length) {
			$target.after($badge);
		} else {
			// Fallback: prepend inside navbar-collapse
			$(".navbar .navbar-collapse").first().prepend($badge);
		}
	}

	function fetchAndInject() {
		if (!window.frappe || !frappe.call) return;
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_version_api.get_version_info",
			type: "GET",
			freeze: false,
			callback: (r) => {
				if (r && r.message) {
					injectBadge(r.message);
				}
			},
			error: () => { /* silent — badge is non-critical */ },
		});
	}

	// Inject on initial load
	$(document).ready(() => {
		setTimeout(fetchAndInject, 500);
	});

	// Re-inject on route change (SPA navigation wipes navbar extras sometimes)
	if (window.frappe && frappe.router && frappe.router.on) {
		frappe.router.on("change", () => {
			setTimeout(fetchAndInject, 300);
		});
	}
})();
