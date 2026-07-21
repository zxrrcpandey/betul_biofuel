/* Notification Center — navbar icon beside the native bell, with a live
   numeric unread badge (fed by the same realtime "notification" event core
   fires on every new Notification Log row).
   Fail-soft by design: if a future Frappe upgrade changes the navbar markup,
   the icon simply doesn't render (the page stays reachable from the BBPL
   Ethanol workspace card). Never throws into the desk boot. */
(function () {
	var TRIES = 20;
	var API = "trustbit_ethanol.ts_gate_entry.notification_center_api.unread_count";

	function paint_badge(n) {
		try {
			var el = document.getElementById("ts-nc-navbar-badge");
			if (!el) return;
			n = parseInt(n, 10) || 0;
			el.style.display = n > 0 ? "flex" : "none";
			el.textContent = n > 99 ? "99+" : String(n);
		} catch (e) { console.error("[notification-center badge]", e); }
	}

	var _last_toast = 0;
	function show_toast() {
		try {
			// suppress on the Center page itself — it updates live there
			if (((window.frappe && frappe.get_route && frappe.get_route()) || [])[0] === "notification-center") return;
			var now = Date.now();
			if (now - _last_toast < 3000) return; // burst guard
			_last_toast = now;
			frappe.xcall("trustbit_ethanol.ts_gate_entry.notification_center_api.latest_unread")
				.then(function (r) {
					if (!r || !r.subject) return;
					var esc = frappe.utils.escape_html;
					frappe.show_alert({
						message: '<a href="/app/notification-center" style="color:inherit;">🔔 ' +
							esc(r.subject) + "</a>",
						indicator: "blue",
					}, 7);
				})
				.catch(function (e) { console.error("[notification-center toast]", e); });
		} catch (e) { console.error("[notification-center toast]", e); }
	}

	var _fetching = false;
	function refresh_badge() {
		if (_fetching || !window.frappe || !frappe.xcall) return;
		_fetching = true;
		frappe.xcall(API)
			.then(function (n) { paint_badge(n); })
			.catch(function (e) { console.error("[notification-center badge]", e); })
			.finally(function () { _fetching = false; });
	}

	function inject() {
		try {
			if (document.getElementById("ts-nc-navbar")) return true;
			// v15: the <header> element IS the navbar (header.navbar) — no descendant .navbar exists.
			var bell = document.querySelector("header.navbar li.dropdown-notifications") ||
				document.querySelector(".navbar li.dropdown-notifications") ||
				document.querySelector(".dropdown-notifications");
			if (!bell || !bell.parentElement) return false;
			var li = document.createElement("li");
			li.id = "ts-nc-navbar";
			li.className = "nav-item";
			li.innerHTML = '<a class="nav-link" href="/app/notification-center" ' +
				'title="Notification Center" aria-label="Notification Center" ' +
				'style="display:flex;align-items:center;padding:0 8px;color:var(--text-muted);position:relative;">' +
				'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
				'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
				'<path d="M22 12h-6l-2 3h-4l-2-3H2"></path>' +
				'<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"></path>' +
				'</svg>' +
				'<span id="ts-nc-navbar-badge" aria-live="polite" style="display:none;position:absolute;' +
				'top:2px;right:-2px;min-width:15px;height:15px;padding:0 4px;border-radius:8px;' +
				'background:#ef4444;color:#fff;font-size:9px;font-weight:700;line-height:15px;' +
				'align-items:center;justify-content:center;pointer-events:none;"></span></a>';
			bell.parentElement.insertBefore(li, bell);

			// initial count + live updates + toast on new notifications
			refresh_badge();
			try {
				if (window.frappe && frappe.realtime && frappe.realtime.on) {
					frappe.realtime.on("notification", function () {
						setTimeout(refresh_badge, 500);
						setTimeout(show_toast, 700);
					});
				}
			} catch (e) { console.error("[notification-center badge]", e); }
			// re-count when leaving the Notification Center page (things were likely marked read there)
			try {
				if (window.frappe && frappe.router && frappe.router.on) {
					var was_nc = false;
					frappe.router.on("change", function () {
						var on_nc = (frappe.get_route() || [])[0] === "notification-center";
						if (was_nc && !on_nc) refresh_badge();
						was_nc = on_nc;
					});
				}
			} catch (e) { console.error("[notification-center badge]", e); }
			return true;
		} catch (e) {
			console.error("[notification-center navbar]", e);
			return true; // stop retrying on a hard error
		}
	}
	var timer = setInterval(function () {
		if (inject() || --TRIES <= 0) clearInterval(timer);
	}, 500);
})();
