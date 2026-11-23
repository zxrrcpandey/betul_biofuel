/**
 * Version tag in the top bar, e.g. "v2.85.6 · 23 Nov"; with `ts_server_label` in site_config the label leads
 * and the tag is orange, e.g. "DEMO · v2.85.7+demo · 23 Nov". A phone shows the version number only. A click
 * opens Help > About. Values: ts_version_api.get_version_info (read from the running code). Fail-soft: if the
 * top bar's markup changes with a Frappe upgrade, no tag shows. A change here needs a new ?v= in hooks.py.
 */
(function () {
	"use strict";
	var TRIES = 20, info = null;

	function part(text, wide) {
		var el = document.createElement("span");
		el.textContent = text;
		if (wide) el.className = "d-none d-md-inline"; // not on a phone
		return el;
	}

	function inject() {
		try {
			if (document.getElementById("ts-version-tag")) return true;
			var bell = document.querySelector("header.navbar li.dropdown-notifications") ||
				document.querySelector(".dropdown-notifications");
			if (!bell || !bell.parentElement) return false;
			var li = document.createElement("li");
			li.id = "ts-version-tag";
			li.className = "nav-item";
			li.style.cssText = "display:flex;align-items:center;padding:0 6px;";
			var tag = document.createElement("span");
			tag.className = "indicator-pill no-indicator-dot " + (info.label ? "orange" : "gray");
			tag.style.cssText = "cursor:pointer;white-space:nowrap;font-size:11px;";
			tag.setAttribute("role", "button");
			tag.title = (info.label ? info.label + " server. " : "") + "Version " + info.version +
				". Code last changed " + info.updated_full + "." +
				(!info.label && info.commit && info.commit !== "unknown" ? " Build " + info.commit + "." : "");
			// "2.85.7+demo": a phone shows "v2.85.7" only. \u00a0 = a space the pill's side-by-side layout keeps
			var ver = String(info.version).split("+");
			if (info.label) tag.appendChild(part(info.label + "\u00a0·\u00a0", true));
			tag.appendChild(part("v" + ver[0], false));
			if (ver[1]) tag.appendChild(part("+" + ver[1], true));
			if (info.updated) tag.appendChild(part("\u00a0·\u00a0" + info.updated, true));
			tag.addEventListener("click", function () {
				if (frappe.ui && frappe.ui.misc && frappe.ui.misc.about) frappe.ui.misc.about();
			});
			li.appendChild(tag);
			bell.parentElement.insertBefore(li, bell.parentElement.firstChild);
			return true;
		} catch (e) {
			console.error("[version tag]", e);
			return true; // stop retrying on a hard error
		}
	}

	function start() {
		if (!window.frappe || !frappe.call || !frappe.session || frappe.session.user === "Guest") return;
		if (window.location.pathname.indexOf("/app") !== 0) return; // desk only
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_version_api.get_version_info",
			type: "GET",
			freeze: false,
			callback: function (r) {
				if (!r || !r.message || !r.message.version) return;
				info = r.message;
				var timer = setInterval(function () {
					if (inject() || --TRIES <= 0) clearInterval(timer);
				}, 500);
			},
			silent: true, // the tag is not critical: no message box if the call fails
		});
	}

	$(document).ready(function () {
		setTimeout(start, 800);
	});
})();
