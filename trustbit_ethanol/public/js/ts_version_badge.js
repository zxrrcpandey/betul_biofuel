/**
 * Version badge — positioned BELOW the Trustbit logo in the sidebar.
 *
 * Calls trustbit_ethanol.ts_gate_entry.ts_version_api.get_version_info and
 * injects a small monospace label just under the sidebar Trustbit logo so
 * users can see what commit is deployed on the server they're using.
 *
 * Hover = full commit message. Click-to-select-all for easy copy.
 */

(function () {
	"use strict";

	function injectBadge(info) {
		if (!info || !info.commit || info.commit === "unknown") return;

		// Remove any existing badge first (idempotent across SPA route changes)
		$(".ts-version-badge").remove();

		const branch_part = (info.branch && info.branch !== "develop")
			? `<div style="font-size:9px;color:#f59e0b;font-weight:bold;margin-top:2px;">${info.branch}</div>`
			: "";

		const tooltip = (info.message || "").replace(/"/g, "&quot;");

		const $badge = $(`
			<div class="ts-version-badge"
			     title="${tooltip}\n${info.date}"
			     style="
			        display:block;
			        margin:4px 14px 8px 14px;
			        padding:4px 8px;
			        background:rgba(241, 245, 249, 0.6);
			        border:1px solid rgba(203, 213, 225, 0.5);
			        border-radius:6px;
			        font-family:monospace;
			        font-size:10px;
			        color:#64748b;
			        cursor:help;
			        user-select:all;
			        text-align:center;
			        line-height:1.3;
			     ">
			  <div><span style="color:#1e40af;font-weight:bold;">${info.commit}</span></div>
			  <div style="font-size:9px;color:#94a3b8;">${info.date}</div>
			  ${branch_part}
			</div>
		`);

		// Try a few known selectors for the sidebar — place the badge after the
		// first-of-its-kind logo block. Fall back through multiple candidates.
		const candidates = [
			".body-sidebar .app-logo",           // Frappe v15 sidebar app logo
			".desk-sidebar .app-logo",
			".body-sidebar-container .app-logo",
			".body-sidebar .sidebar-item-container:first",
			".desk-sidebar",
			".standard-sidebar",
		];

		for (const sel of candidates) {
			const $el = $(sel).first();
			if ($el.length) {
				$el.after($badge);
				return;
			}
		}

		// Final fallback: prepend to sidebar
		const $sidebar = $(".body-sidebar, .desk-sidebar, .standard-sidebar").first();
		if ($sidebar.length) {
			$sidebar.prepend($badge);
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

	// Inject on initial load — wait for sidebar to render
	$(document).ready(() => {
		setTimeout(fetchAndInject, 800);
	});

	// Re-inject on route change (SPA nav sometimes rebuilds sidebar)
	if (window.frappe && frappe.router && frappe.router.on) {
		frappe.router.on("change", () => {
			setTimeout(fetchAndInject, 500);
		});
	}
})();
