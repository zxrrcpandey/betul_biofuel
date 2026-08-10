// TS Login Banner — "Almost Done!" announcement above the login card.
//
// Loaded via web_include_js, which renders on EVERY website/portal page
// (base.html), so this MUST no-op unless the login form is actually present.
// `section.for-login` is an exact class token — it does not match the sibling
// `section.for-login-with-email-link`. Styles live in ts_login.css.
// All markup below is static; no user input is interpolated.
(function () {
	function mount() {
		var host = document.querySelector("section.for-login");
		if (!host || document.querySelector(".ts-login-banner")) return;

		var banner = document.createElement("div");
		banner.className = "ts-login-banner";
		banner.setAttribute("role", "status");
		banner.innerHTML =
			'<p class="ts-lb-title">🚀 Almost Done!</p>' +
			'<p class="ts-lb-body">Trustbit ERP development is nearly complete. ' +
			"We are currently completing the final testing and deployment.</p>" +
			'<p class="ts-lb-foot">Have feedback, suggestions or issues? Write to ' +
			'<a href="mailto:support@trustbit.in">support@trustbit.in</a></p>';

		host.parentNode.insertBefore(banner, host);
	}

	// base.html injects web_include_js after the content block, so the DOM is
	// already parsed; the listener is only a guard for a future defer/async.
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", mount);
	} else {
		mount();
	}
})();
