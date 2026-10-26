// Clearer "please wait" state for every query report (all apps).
// Frappe's own loading state is the still empty-list picture + "Loading...",
// which looks like an empty report. This shows a spinner and a running
// seconds count instead. Fail-soft: if Frappe's report view changes shape,
// its original loading screen stays.
(function () {
	const QR = frappe.views && frappe.views.QueryReport;
	if (!QR || QR.prototype.__ts_loading_patched) return;
	const show = QR.prototype.show_loading_screen;
	const hide = QR.prototype.hide_loading_screen;
	if (!show || !hide) return;

	QR.prototype.show_loading_screen = function () {
		show.apply(this, arguments);
		clearInterval(this.__ts_loading_timer);
		const $box = this.$loading && this.$loading.children("div").first();
		if (!$box || !$box.length) return;
		const last = Math.round(this.report_settings?.execution_time || 0);
		const hint = last >= 3 ? " · " + __("last run took about {0} s", [last]) : "";
		$box.html(`<div class="text-center">
			<div class="spinner-border text-muted" role="status" style="width: 2.5rem; height: 2.5rem;"></div>
			<p class="mt-3 mb-1">${__("Loading report, please wait…")}</p>
			<p class="small text-muted"><span class="ts-loading-seconds">0</span> s${hint}</p>
		</div>`);
		const $sec = $box.find(".ts-loading-seconds");
		const started = Date.now();
		this.__ts_loading_timer = setInterval(() => {
			// A failed run never reaches hide_loading_screen — stop the spinner
			// so nobody waits on a request that has already failed.
			const req = this.last_ajax;
			if (req && req.state && req.state() === "rejected") {
				clearInterval(this.__ts_loading_timer);
				$box.html(`<p class="text-muted">${__("The report could not load. Please press Refresh to try again.")}</p>`);
				return;
			}
			$sec.text(Math.floor((Date.now() - started) / 1000));
		}, 1000);
	};

	QR.prototype.hide_loading_screen = function () {
		clearInterval(this.__ts_loading_timer);
		return hide.apply(this, arguments);
	};

	QR.prototype.__ts_loading_patched = true;
})();
