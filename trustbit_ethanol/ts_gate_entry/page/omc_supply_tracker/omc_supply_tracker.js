/**
 * OMC Ethanol Supply Tracker — Frappe custom page.
 *
 * Ported from the approved mockup BBPL_OMC_Supply_Tracker_Dashboard.html.
 * The mockup's dummy PERIODS object is REPLACED by the live read API
 *   trustbit_ethanol.ts_gate_entry.ts_dashboard_omc_supply.get_omc_supply_tracker(esy, period)
 *
 * Data wiring:
 *   - PERIOD pills + ESY selector  -> RE-CALL the API (server computes the
 *     period window). The server is the source of truth for all gauge math.
 *   - OMC filter (All/IOCL/HPCL/BPCL...) -> CLIENT-SIDE focus on the returned
 *     omcs[] (payload always carries every OMC + Plant Total).
 *   - PENDING sections (risk:null, trend.available:false, daily fields null,
 *     ops:null for CEO/MD) render in a graceful "data pending" state — no fake
 *     numbers, layout shells preserved.
 *   - targets_set === false -> a clear "Set OMC allocations" empty state
 *     (selectors + chart frame still render).
 *
 * Visuals (CSS + SVG gauge/quarter-bar/trend) are kept identical to the mockup;
 * all CSS is scoped under `.omc-tracker` (see omc_supply_tracker.css).
 */

frappe.pages["omc-supply-tracker"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("OMC Ethanol Supply Tracker"),
		single_column: true,
	});

	// Full-bleed dashboard: hide Frappe's page-head (redundant title + default
	// actions) and the navbar breadcrumb — the dashboard's own hero carries the
	// branding + a relocated Wall Display button. Breadcrumb restored on leave.
	$(wrapper).find(".page-head").hide();
	OMC._hideChrome();

	// page state
	page._esy = null;          // selected ESY code (null = server default)
	page._period = null;       // selected period code (null = server default)
	page._omc = "ALL";         // client-side OMC focus
	page._expanded = null;     // which OMC's depot drill-down is open
	page._data = null;         // last API payload
	page._call_token = 0;      // guards against out-of-order responses

	page.main.html(OMC._shell());

	// Page-local theme: white (light) by default, isolated from Frappe's global
	// data-theme so a dark desk never re-themes this page (and toggling here
	// never re-themes the desk). State lives on the .omc-tracker root only.
	OMC._setTheme(page, "light");

	OMC._bind(page);
	OMC._fetch(page);
};

frappe.pages["omc-supply-tracker"].refresh = function (wrapper) {
	// Live dashboard — always re-fetch on the refresh hook (matches ceo_dashboard.js).
	// The _call_token guard inside _fetch drops any out-of-order responses.
	if (wrapper.page) OMC._fetch(wrapper.page);
};

// Re-hide the global navbar breadcrumb on re-entry; restore it when leaving so
// other pages keep theirs (the per-page page-head is hidden in on_page_load).
frappe.pages["omc-supply-tracker"].on_page_show = function () { OMC._hideChrome(); };
frappe.pages["omc-supply-tracker"].on_page_hide = function () { OMC._showChrome(); };

/* =====================================================================
   Namespace — all helpers live on OMC to avoid global collisions.
   ===================================================================== */
window.OMC = window.OMC || {};

/* =====================================================================
   PAGE-LOCAL THEME — isolated from Frappe's global data-theme.
   The theme attribute lives on the .omc-tracker root only, so a dark desk
   never re-themes this page and toggling here never re-themes the desk.
   ===================================================================== */
OMC._root = function () { return document.querySelector(".omc-tracker"); };
OMC._setTheme = function (page, theme) {
	page._theme = (theme === "dark") ? "dark" : "light";
	const root = OMC._root();
	if (root) root.setAttribute("data-omc-theme", page._theme);
	return page._theme;
};
/* Hide the GLOBAL navbar breadcrumb while on this page (full-bleed dashboard);
   restored on leave so other pages keep theirs. */
OMC._hideChrome = function () { $("#navbar-breadcrumbs").hide(); };
OMC._showChrome = function () { $("#navbar-breadcrumbs").show(); };
/* page-local "is this page in light mode?" — reads page state, never the desk */
OMC._isLight = function (page) {
	if (page && page._theme) return page._theme === "light";
	const root = OMC._root();
	return !root || root.getAttribute("data-omc-theme") !== "dark";
};

OMC.esc = function (v) { return frappe.utils.escape_html(v == null ? "" : String(v)); };

/* format an integer with a thousands separator; "—" when absent */
OMC.fmt = function (n) {
	if (n === null || n === undefined || isNaN(n)) return "—";
	return Math.round(n).toLocaleString("en-IN");
};
OMC.fmt1 = function (n) {
	if (n === null || n === undefined || isNaN(n)) return "—";
	return Number(n).toFixed(1);
};
/* map API status (green/amber/red) -> mockup pace class (p-green/...) */
OMC.paceCls = function (s) {
	return s === "green" ? "p-green" : s === "red" ? "p-red" : s === "amber" ? "p-amber" : "p-amber";
};
/* map API logo_cls/key -> mockup logo class; fall back to a generic blue chip */
OMC.logoCls = function (key) {
	const k = (key || "").toString().toLowerCase();
	if (k === "total") return "l-total";
	if (k === "iocl") return "l-iocl";
	if (k === "hpcl") return "l-hpcl";
	if (k === "bpcl") return "l-bpcl";
	return "l-generic";
};
OMC.isTotal = function (o) { return o && (o.key === "total" || o.logo_cls === "total"); };

OMC.omcFocused = function (page) { return page._omc && page._omc !== "ALL"; };
/* match an API omc to the focus key (by code or upper-cased name/key) */
OMC.matchFocus = function (o, focus) {
	if (!focus || focus === "ALL") return true;
	const f = focus.toUpperCase();
	return (o.code || "").toUpperCase() === f
		|| (o.key || "").toUpperCase() === f
		|| (o.name || "").toUpperCase() === f;
};

/* =====================================================================
   STATIC DOM SHELL (mirrors the mockup body; ids reused 1:1).
   Everything lives inside <div class="omc-tracker"> for CSS scoping.
   ===================================================================== */
OMC._shell = function () {
	const esc = OMC.esc;
	return `<div class="omc-tracker">
  <div class="bg-stage" aria-hidden="true">
    <div class="orb a"></div><div class="orb b"></div><div class="orb c"></div>
    <div class="orb d"></div><div class="orb e"></div>
  </div>
  <div class="bg-sheen" aria-hidden="true"></div>
  <div class="bg-grain" aria-hidden="true"></div>

  <div class="hero glass">
    <div class="hero-inner">
      <div class="logo">
        <svg viewBox="0 0 24 24" fill="none"><path d="M12 2c-2.5 4-5 6.5-5 10a5 5 0 0010 0c0-3.5-2.5-6-5-10z" fill="#fff" opacity=".95"/><circle cx="12" cy="13" r="2" fill="#16a34a"/></svg>
      </div>
      <div class="brand-block">
        <div class="brand-name">${esc("Betul Bio Fuel Pvt Ltd")} <span class="ver-badge">v1.0.0</span></div>
        <div class="brand-sub">${esc(__("OMC Ethanol Supply Tracker"))} · ${esc(__("Meter Room"))}</div>
      </div>
      <div class="hero-spacer"></div>
      <div class="esy-sel">
        <label>${esc(__("Ethanol Supply Year"))}</label>
        <select class="esy" id="omcEsySel"></select>
      </div>
      <div class="period-sel">
        <label>${esc(__("Period"))}</label>
        <div class="seg" id="omcPeriodSeg" role="group" aria-label="${esc(__("Reporting period"))}">
          <button type="button" data-period="Q1"><span class="pq">Quarter-1</span><span class="pm">Nov–Jan</span></button>
          <button type="button" data-period="Q2"><span class="pq">Quarter-2</span><span class="pm">Feb–Apr</span></button>
          <button type="button" data-period="Q3"><span class="pq">Quarter-3</span><span class="pm">May–Jul</span></button>
          <button type="button" data-period="Q4"><span class="pq">Quarter-4</span><span class="pm">Aug–Oct</span></button>
          <button type="button" data-period="FY"><span class="pq">Full Year</span><span class="pm">Nov–Oct</span></button>
        </div>
      </div>
      <div class="period-sel">
        <label>${esc(__("OMC"))}</label>
        <div class="seg" id="omcOmcSeg" role="group" aria-label="${esc(__("Oil marketing company filter"))}"></div>
      </div>
      <div class="verdict-chip v-none" id="omcVerdict"><span class="pulse-dot"></span> —</div>
      <button class="toggle-btn" id="omcWallBtn" title="${esc(__("Wall Display"))}" aria-label="${esc(__("Open wall display"))}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
      </button>
      <button class="toggle-btn" id="omcThemeBtn" title="${esc(__("Light / Dark"))}" aria-label="${esc(__("Toggle theme"))}">
        <svg id="omcThemeIco" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
      </button>
    </div>
    <div class="meta-row" id="omcMetaRow"></div>
  </div>

  <div class="wrap">
    <div class="sec-title">
      <span class="bar"></span> <span id="omcGaugeSecTitle">${esc(__("OMC Annual Fulfilment — Pace Gauges"))}</span>
      <span class="feas-tag tag-live">${esc(__("Live · dispatch-accepted KL"))}</span>
      <span class="feas-tag tag-config">${esc(__("Allocation targets being finalised"))}</span>
    </div>
    <div class="gauge-grid" id="omcGaugeGrid"></div>

    <div class="sec-title">
      <span class="bar" style="background:var(--green)"></span> ${esc(__("Quarter-wise Supply by OMC — Supplied vs Allocation"))}
      <span class="feas-tag tag-live">${esc(__("All four quarters · Alcoholic Year"))}</span>
    </div>
    <div class="trend-grid" style="grid-template-columns:1fr;">
      <div class="panel glass">
        <div class="panel-h">
          <span class="t" id="omcQbarTitle">${esc(__("Per-OMC Quarterly Fulfilment (KL)"))}</span>
          <div class="legend">
            <span><i style="background:var(--series-actual)"></i>${esc(__("Filled = Supplied"))}</span>
            <span><i style="background:transparent;border:1.5px solid var(--faint)"></i>${esc(__("Outline = Allocation"))}</span>
            <span><i style="background:var(--green)"></i>≥95%</span>
            <span><i style="background:var(--amber)"></i>≥80%</span>
            <span><i style="background:var(--red)"></i>&lt;80%</span>
          </div>
        </div>
        <div id="omcQuarterBars"></div>
      </div>
    </div>

    <div class="risk-banner" id="omcRiskBanner"></div>

    <div class="sec-title">
      <span class="bar" style="background:var(--amber)"></span> ${esc(__("Daily Target & Catch-Up Run-Rate — Today"))}
      <span class="feas-tag tag-pending">${esc(__("Daily targets · coming soon"))}</span>
    </div>
    <div class="daily-grid">
      <div class="panel glass">
        <div class="panel-h">
          <span class="t" id="omcDailyTitle">${esc(__("Per-OMC Daily Target vs Actual"))}</span>
        </div>
        <div id="omcDailyBody"></div>
      </div>
      <div class="panel glass">
        <div class="panel-h"><span class="t" id="omcAggTitle">${esc(__("Plant Total — Today"))}</span></div>
        <div id="omcAggBody"></div>
      </div>
    </div>

    <div class="ops-toggle glass" id="omcOpsToggle">
      <div class="lft">
        <span class="badge">${esc(__("PM view"))}</span>
        <span class="ttl">${esc(__("Plant Operations Strip"))}</span>
        <span style="font-size:11px;color:var(--muted);font-weight:600;">${esc(__("run-rates · yield · capacity · stock cover · config"))}</span>
      </div>
      <svg class="chev" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
    </div>
    <div class="ops-body" id="omcOpsBody"></div>

    <div class="sec-title">
      <span class="bar" style="background:var(--purple)"></span> ${esc(__("Cumulative Supply Trend — Target vs Actual"))}
      <span class="feas-tag tag-pending">${esc(__("Trend curve · coming soon"))}</span>
    </div>
    <div id="omcTrendSection"></div>

    <div class="footer">
      <b>${esc("Betul Bio Fuel Pvt Ltd")}</b> · ${esc(__("OMC Ethanol Supply Tracker"))} <span id="omcFooterPeriod"></span><br>
      ${esc(__("Powered by"))} <b>${esc("Trustbit Technologies Pvt. Ltd.")}</b> · <span id="omcFooterUpdated"></span>
    </div>
  </div>
</div>`;
};

/* =====================================================================
   EVENT BINDING (no inline onclick — CSP-clean)
   ===================================================================== */
OMC._bind = function (page) {
	const $root = page.main;

	// ESY selector -> RE-CALL the API
	$root.on("change", "#omcEsySel", function () {
		page._esy = this.value || null;
		page._expanded = OMC.omcFocused(page) ? page._omc : null;
		OMC._fetch(page);
	});

	// PERIOD pills -> RE-CALL the API
	$root.on("click", "#omcPeriodSeg button", function () {
		page._period = this.dataset.period;
		page._expanded = OMC.omcFocused(page) ? page._omc : null;
		OMC._fetch(page);
	});

	// OMC pills -> CLIENT-SIDE focus (no re-call)
	$root.on("click", "#omcOmcSeg button", function () {
		page._omc = this.dataset.omc;
		page._expanded = OMC.omcFocused(page) ? page._omc : null;
		OMC._renderAll(page);
	});

	// "Back to All OMCs" link inside section titles
	$root.on("click", ".focus-clear", function () {
		page._omc = "ALL";
		page._expanded = null;
		OMC._renderAll(page);
	});

	// depot drill-down toggle (delegated; cards have data-omckey)
	$root.on("click", "[data-omc-toggle]", function (e) {
		e.stopPropagation();
		const key = this.getAttribute("data-omc-toggle");
		page._expanded = (page._expanded === key) ? null : key;
		OMC._renderGauges(page);
	});
	$root.on("keydown", "[data-omc-toggle]", function (e) {
		if (e.key === "Enter" || e.key === " ") {
			e.preventDefault();
			const key = this.getAttribute("data-omc-toggle");
			page._expanded = (page._expanded === key) ? null : key;
			OMC._renderGauges(page);
		}
	});

	// ops collapse
	$root.on("click", "#omcOpsToggle", function () {
		$("#omcOpsToggle").toggleClass("open");
		$("#omcOpsBody").toggleClass("open");
	});

	// theme toggle — flips the PAGE-LOCAL data-omc-theme on the .omc-tracker
	// root only (never the global data-theme), then re-renders SVGs so var()
	// strokes recompute against the new page theme.
	$root.on("click", "#omcWallBtn", function () {
		frappe.set_route("omc-supply-tracker-wall");
	});

	$root.on("click", "#omcThemeBtn", function () {
		const next = OMC._isLight(page) ? "dark" : "light";
		OMC._setTheme(page, next);
		document.getElementById("omcThemeIco").innerHTML = next === "dark"
			? '<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>'
			: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';
		if (page._data) OMC._renderAll(page);
	});
};

/* =====================================================================
   DATA FETCH — period / ESY changes re-call the server
   ===================================================================== */
OMC._fetch = function (page) {
	const token = ++page._call_token;
	const grid = document.getElementById("omcGaugeGrid");
	if (grid) grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">${OMC.esc(__("Loading OMC Supply Tracker..."))}</div>`;

	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_dashboard_omc_supply.get_omc_supply_tracker",
		args: { esy: page._esy, period: page._period },
		callback(r) {
			if (token !== page._call_token) return; // stale response — ignore
			if (!r.message) {
				if (grid) grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">${OMC.esc(__("No data available."))}</div>`;
				return;
			}
			page._data = r.message;
			// adopt the server-resolved esy/period so the selectors reflect reality
			page._esy = (r.message.esy && r.message.esy.code) || page._esy;
			page._period = r.message.period_selected || page._period;
			OMC._renderAll(page);
		},
		error() {
			if (token !== page._call_token) return;
			if (grid) grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;color:var(--red-txt);">${OMC.esc(__("Error loading dashboard. Please refresh."))}</div>`;
		},
	});
};

/* =====================================================================
   MASTER RE-RENDER
   ===================================================================== */
OMC._renderAll = function (page) {
	const d = page._data;
	if (!d) return;
	OMC._syncSelectors(page);
	OMC._renderMeta(page);
	OMC._renderVerdict(page);
	OMC._renderGauges(page, true);
	OMC._renderQuarterBars(page);
	OMC._renderRisk(page);
	OMC._renderDaily(page);
	OMC._renderOps(page);
	OMC._renderTrend(page);
	OMC._renderFooter(page);
};

/* shared "Focused: X · Back to All" badge for focus-aware section titles */
OMC._focusBadge = function (page) {
	if (!OMC.omcFocused(page)) return "";
	const f = OMC.esc(page._omc);
	return ` <span class="focus-tag">${OMC.esc(__("Focused"))}: ${f}</span>` +
		` <button type="button" class="focus-clear" aria-label="${OMC.esc(__("Clear OMC filter"))}">↩ ${OMC.esc(__("All OMCs"))}</button>`;
};

/* the non-total OMC cards from the payload (Plant Total excluded) */
OMC._omcCards = function (page) {
	return (page._data.omcs || []).filter((o) => !OMC.isTotal(o));
};
OMC._plantTotal = function (page) {
	return (page._data.omcs || []).find((o) => OMC.isTotal(o)) || null;
};

/* =====================================================================
   SELECTOR SYNC (ESY <select>, period pills, OMC pills)
   ===================================================================== */
OMC._syncSelectors = function (page) {
	const d = page._data;

	// ESY <select> from esy_options[]
	const sel = document.getElementById("omcEsySel");
	if (sel) {
		const opts = d.esy_options && d.esy_options.length ? d.esy_options
			: (d.esy && d.esy.code ? [d.esy.code] : []);
		const curCode = (d.esy && d.esy.code) || page._esy || (opts[0] || "");
		sel.innerHTML = opts.length
			? opts.map((c) => `<option value="${OMC.esc(c)}"${c === curCode ? " selected" : ""}>${OMC.esc(__("Alcoholic Year"))} ${OMC.esc(c)}</option>`).join("")
			: `<option value="">${OMC.esc(__("No supply year configured"))}</option>`;
	}

	// period pills active state
	const psel = d.period_selected || page._period || "FY";
	document.querySelectorAll("#omcPeriodSeg button").forEach((b) => {
		const on = b.dataset.period === psel;
		b.classList.toggle("active", on);
		b.setAttribute("aria-pressed", on ? "true" : "false");
	});

	// OMC pills built from the payload OMC list (codes), + "All"
	const omcSeg = document.getElementById("omcOmcSeg");
	if (omcSeg) {
		const cards = OMC._omcCards(page);
		let html = `<button type="button" data-omc="ALL"><span class="pq">${OMC.esc(__("All"))}</span><span class="pm">${cards.length} ${OMC.esc(__("OMCs"))}</span></button>`;
		cards.forEach((o) => {
			const code = OMC.esc(o.code || o.key || "");
			html += `<button type="button" data-omc="${code}"><span class="pq">${OMC.esc(o.code || o.name)}</span><span class="pm">${OMC.esc(o.name)}</span></button>`;
		});
		omcSeg.innerHTML = html;
		document.querySelectorAll("#omcOmcSeg button").forEach((b) => {
			const on = (b.dataset.omc || "").toUpperCase() === (page._omc || "ALL").toUpperCase();
			b.classList.toggle("active", on);
			b.setAttribute("aria-pressed", on ? "true" : "false");
		});
	}
};

/* =====================================================================
   META ROW
   ===================================================================== */
OMC._periodLabel = function (code) {
	return { Q1: "Quarter-1 · Nov–Jan", Q2: "Quarter-2 · Feb–Apr", Q3: "Quarter-3 · May–Jul",
		Q4: "Quarter-4 · Aug–Oct", FY: "Full Year" }[code] || code || "—";
};
OMC._renderMeta = function (page) {
	const d = page._data, esy = d.esy || {};
	const pcode = d.period_selected || "FY";
	const winStart = esy.start || "—", winEnd = esy.end || "—";
	// find the selected period's window from esy.quarters when not FY
	let window = `${winStart} → ${winEnd}`;
	if (pcode !== "FY" && esy.quarters) {
		const q = esy.quarters.find((x) => x.code === pcode);
		if (q) window = `${q.start} → ${q.end}`;
	}
	const daysLabel = (esy.total_days)
		? `${OMC.esc(__("Day"))} <b>${esy.day_index}</b> ${OMC.esc(__("of"))} ${esy.total_days} · <b>${esy.days_left} ${OMC.esc(__("days left"))}</b>`
		: OMC.esc(__("Window not set"));
	const prorata = (esy.prorata_pct != null) ? `${esy.prorata_pct}%` : "—";
	const lu = d.last_updated ? frappe.datetime.str_to_user(d.last_updated) : "";

	document.getElementById("omcMetaRow").innerHTML =
		`<span class="period-tag">${OMC.esc(OMC._periodLabel(pcode))}</span>` +
		`<span class="sep"></span>` +
		`<span>${OMC.esc(__("Window"))}: <b>${OMC.esc(window)}</b></span>` +
		`<span class="sep"></span>` +
		`<span>${daysLabel}</span>` +
		`<span class="sep"></span>` +
		`<span>${OMC.esc(__("Pro-rata pace target"))}: <b>${OMC.esc(prorata)}</b></span>` +
		`<span class="sep"></span>` +
		`<span class="live-tag"><span class="pulse-dot" style="background:var(--green);box-shadow:0 0 9px var(--green)"></span>${OMC.esc(__("Live"))}${lu ? " · " + OMC.esc(lu) : ""}</span>`;

	const baseTitle = pcode === "FY" ? __("OMC Annual Fulfilment — Pace Gauges")
		: `${__("OMC")} ${OMC._periodLabel(pcode)} ${__("Fulfilment — Pace Gauges")}`;
	document.getElementById("omcGaugeSecTitle").innerHTML =
		(OMC.omcFocused(page) ? `${OMC.esc(page._omc)} ${OMC.esc(__("Fulfilment"))}` : OMC.esc(baseTitle)) + OMC._focusBadge(page);
};

/* =====================================================================
   VERDICT CHIP (server-computed; focus narrows to the one OMC)
   ===================================================================== */
OMC._renderVerdict = function (page) {
	const d = page._data;
	const chip = document.getElementById("omcVerdict");
	if (!d.targets_set) {
		chip.className = "verdict-chip v-none";
		chip.innerHTML = `<span class="pulse-dot"></span> ${OMC.esc(__("Targets not set"))}`;
		return;
	}
	if (OMC.omcFocused(page)) {
		const card = OMC._omcCards(page).find((o) => OMC.matchFocus(o, page._omc));
		let cls = "v-ok", txt = __("On Track");
		if (card && card.status === "red") { cls = "v-risk"; txt = __("At Risk"); }
		else if (card && card.status === "amber") { cls = "v-action"; txt = __("Action Needed"); }
		chip.className = "verdict-chip " + cls;
		chip.innerHTML = `<span class="pulse-dot"></span> ${OMC.esc(page._omc)} · ${OMC.esc(txt).toUpperCase()}`;
		return;
	}
	const v = d.verdict || { level: "none", label: "—" };
	const map = { at_risk: "v-risk", action: "v-action", on_track: "v-ok", none: "v-none" };
	chip.className = "verdict-chip " + (map[v.level] || "v-none");
	chip.innerHTML = `<span class="pulse-dot"></span> ${OMC.esc(v.label).toUpperCase()}`;
};

/* =====================================================================
   SVG PACE GAUGE — VARIANT B "ARC FILL" (ported verbatim from mockup)
   cfg: pct, prorata, projected, redEnd, amberEnd, status (p-green/amber/red)
   ===================================================================== */
OMC._polar = function (cx, cy, r, deg) {
	const a = (deg - 180) * Math.PI / 180;
	return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
};
OMC._arc = function (cx, cy, r, a0, a1, color, w, extra, cap) {
	const [x0, y0] = OMC._polar(cx, cy, r, a0), [x1, y1] = OMC._polar(cx, cy, r, a1);
	const large = (a1 - a0) > 180 ? 1 : 0;
	return `<path d="M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}" stroke="${color}" stroke-width="${w}" fill="none" stroke-linecap="${cap || "round"}" ${extra || ""}/>`;
};
OMC._gid = 0;
OMC._gauge = function (cfg, page) {
	const cx = 115, cy = 115, r = 84, id = "omcg" + (OMC._gid++);
	const wBand = 8, wProg = 15;
	const deg = (p) => Math.max(0, Math.min(100, p)) / 100 * 180;
	// read the page-scoped CSS vars off the .omc-tracker root (theme is local)
	const cs = getComputedStyle(OMC._root() || document.documentElement);
	const AR = (cs.getPropertyValue("--arc-red") || "#ff5a5a").trim();
	const AA = (cs.getPropertyValue("--arc-amber") || "#ffb43c").trim();
	const AG = (cs.getPropertyValue("--arc-green") || "#2ee29b").trim();
	const TXT = (cs.getPropertyValue("--text") || "#fff").trim();
	const isLight = OMC._isLight(page);
	const HALO = isLight ? "#ffffff" : "rgba(255,255,255,0.9)";
	const PROG = cfg.status === "p-green" ? AG : cfg.status === "p-red" ? AR : AA;

	let s = `<svg viewBox="0 0 230 140" style="width:100%;display:block;overflow:visible;">`;
	s += `<defs>
    <filter id="${id}prog" x="-40%" y="-40%" width="180%" height="180%">
      <feDropShadow dx="0" dy="0" stdDeviation="${isLight ? 1.8 : 3}" flood-color="${PROG}" flood-opacity="${isLight ? 0.5 : 0.8}"/>
    </filter>
    <filter id="${id}soft" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="1.2" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>`;

	s += OMC._arc(cx, cy, r, 0, 180, "var(--gauge-track)", wBand + 4, "", "round");
	const rE = deg(cfg.redEnd), aE = deg(cfg.amberEnd), bandOp = isLight ? 0.50 : 0.46;
	s += OMC._arc(cx, cy, r, 0, rE, AR, wBand, `opacity="${bandOp}"`, "butt");
	s += OMC._arc(cx, cy, r, rE, aE, AA, wBand, `opacity="${bandOp}"`, "butt");
	s += OMC._arc(cx, cy, r, aE, 180, AG, wBand, `opacity="${bandOp}"`, "butt");
	s += OMC._arc(cx, cy, r + wBand / 2 + 0.5, 0, 180, "var(--gauge-rim)", 1, "", "round");

	const aPct = deg(cfg.pct);
	if (aPct > 0.4) {
		s += OMC._arc(cx, cy, r, 0, aPct, PROG, wProg, `opacity="0.28" class="omc-prog"`, "round");
		s += OMC._arc(cx, cy, r, 0, aPct, PROG, wProg, `filter="url(#${id}prog)" class="omc-prog"`, "round");
	}
	const [lx, ly] = OMC._polar(cx, cy, r, aPct);
	if (aPct > 0.4) {
		s += `<g class="omc-knob">`;
		s += `<circle cx="${lx}" cy="${ly}" r="${wProg / 2 + 2.0}" fill="${HALO}" opacity="0.95" filter="url(#${id}soft)"/>`;
		s += `<circle cx="${lx}" cy="${ly}" r="${wProg / 2 - 0.4}" fill="${PROG}" filter="url(#${id}prog)"/>`;
		s += `<circle cx="${lx}" cy="${ly}" r="${wProg / 2 - 3.4}" fill="${HALO}" opacity="0.9"/>`;
		s += `</g>`;
	}

	const tDeg = deg(cfg.prorata);
	const [tn0x, tn0y] = OMC._polar(cx, cy, r - wProg / 2 - 2.5, tDeg);
	const [tn1x, tn1y] = OMC._polar(cx, cy, r + wBand / 2 + 2.5, tDeg);
	s += `<line x1="${tn0x}" y1="${tn0y}" x2="${tn1x}" y2="${tn1y}" stroke="${HALO}" stroke-width="4.6" stroke-linecap="round"/>`;
	s += `<line x1="${tn0x}" y1="${tn0y}" x2="${tn1x}" y2="${tn1y}" stroke="${TXT}" stroke-width="2.1" stroke-linecap="round"/>`;
	const [cax, cay] = OMC._polar(cx, cy, r + wBand / 2 + 9, tDeg);
	const ca = (tDeg - 180) * Math.PI / 180, ux = Math.cos(ca), uy = Math.sin(ca);
	const px = -uy, py = ux, ch = 4.2;
	s += `<path d="M ${cax + px * ch - ux * ch} ${cay + py * ch - uy * ch} L ${cax} ${cay} L ${cax - px * ch - ux * ch} ${cay - py * ch - uy * ch}" fill="none" stroke="${TXT}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>`;
	const [tlx, tly] = OMC._polar(cx, cy, r + wBand / 2 + 19, tDeg);
	s += `<text x="${tlx}" y="${tly + 2.5}" text-anchor="middle" font-size="7.5" font-weight="700" fill="var(--faint)" style="letter-spacing:.3px">${OMC.esc(__("today"))}</text>`;

	const [pjx, pjy] = OMC._polar(cx, cy, r, deg(cfg.projected));
	s += `<circle cx="${pjx}" cy="${pjy}" r="4.6" fill="${isLight ? "#fff" : "rgba(10,15,29,0.65)"}" stroke="${AA}" stroke-width="2.3" opacity="${isLight ? 0.96 : 0.92}" filter="url(#${id}soft)"/>`;

	s += `</svg>`;
	return s;
};

/* =====================================================================
   GAUGE CARDS — consumes the API omcs[] directly
   ===================================================================== */
OMC._renderGauges = function (page, animate) {
	OMC._gid = 0;
	const d = page._data;
	const grid = document.getElementById("omcGaugeGrid");
	grid.classList.toggle("focused", OMC.omcFocused(page));

	if (!d.targets_set) {
		grid.innerHTML = OMC._emptyState();
		return;
	}

	// All OMC cards + Plant Total (total last). Focus -> only that OMC.
	let cards = (d.omcs || []).slice();
	if (OMC.omcFocused(page)) cards = cards.filter((o) => OMC.matchFocus(o, page._omc));

	let html = "";
	cards.forEach((o) => {
		const total = OMC.isTotal(o);
		const clickable = !total && (o.depots && (o.depots.length || (o.depots_unplanned && o.depots_unplanned.supplied_kl)));
		const focusKey = o.code || o.key || "";
		const isExp = clickable && page._expanded === focusKey;
		const statusCls = OMC.paceCls(o.status);
		const pctTxt = (o.pct != null) ? Math.round(o.pct) : "—";
		const proTxt = (o.prorata_pct != null) ? Math.round(o.prorata_pct) : "—";
		const projTxt = (o.projected_pct != null) ? Math.round(o.projected_pct) : "—";
		const aff = clickable
			? `<span class="depot-aff" aria-hidden="true"><span class="chv">▾</span> ${OMC.esc(__("depots"))}</span>`
			: "";
		const allocLbl = (d.period_selected === "FY") ? __("Annual Alloc.") : __("Period Alloc.");
		const ghost = (o.projected_pct != null)
			? `${OMC.esc(__("Projected"))} ${projTxt}% ${OMC.esc(__("period-end"))}`
			: OMC.esc(__("Projection pending"));

		html += `<div class="gcard glass ${total ? "total" : ""} ${clickable ? "clickable" : ""} ${isExp ? "expanded" : ""}"
        ${clickable ? `role="button" tabindex="0" aria-expanded="${isExp}" aria-label="${OMC.esc(o.name)} — ${OMC.esc(__("open per-depot drill-down"))}" data-omc-toggle="${OMC.esc(focusKey)}"` : ""}>
      <div class="gcard-head">
        <span class="omc-logo ${OMC.logoCls(o.logo_cls || o.key)}">${OMC.esc(o.code || (o.name || "").slice(0, 2))}</span>
        <div><div class="gcard-name">${OMC.esc(o.name)}</div><div class="gcard-fullname">${OMC.esc(o.full || o.name)}</div></div>
        ${aff}
      </div>
      <div class="gauge-wrap">
        ${OMC._gauge({ pct: o.pct || 0, prorata: o.prorata_pct || 0, projected: o.projected_pct || 0, redEnd: o.redEnd || 0, amberEnd: o.amberEnd || 0, status: statusCls }, page)}
        <div class="gauge-center">
          <div class="gauge-pct"><span class="gpct-n" data-to="${(typeof pctTxt === "number") ? pctTxt : ""}">${pctTxt}</span><small>%</small></div>
          <div class="gauge-sub">${OMC.fmt(o.supplied_kl)} / ${OMC.fmt(o.allocation_kl)} KL</div>
        </div>
      </div>
      <div class="gauge-legend">
        <span><span class="gl-act ${statusCls === "p-green" ? "s-green" : statusCls === "p-red" ? "s-red" : "s-amber"}">●</span> ${OMC.esc(__("Actual"))} ${pctTxt}%</span>
        <span><span class="gl-tgt">‹</span> ${OMC.esc(__("Target today"))} ${proTxt}%</span>
        <span><span class="gl-proj">◌</span> ${OMC.esc(__("Projected"))} ${projTxt}%</span>
      </div>
      <span class="status-pill ${statusCls}">${OMC.esc(o.status_txt || "—")}</span>
      <div class="gstats">
        <div class="gstat"><div class="lbl">${OMC.esc(allocLbl)}</div><div class="val">${OMC.fmt(o.allocation_kl)} <span style="font-size:10px;color:var(--faint)">KL</span></div></div>
        <div class="gstat"><div class="lbl">${OMC.esc(__("Supplied"))}</div><div class="val">${OMC.fmt(o.supplied_kl)} <span style="font-size:10px;color:var(--faint)">KL</span></div></div>
        <div class="gstat"><div class="lbl">${OMC.esc(__("Remaining"))}</div><div class="val sm">${OMC.fmt(o.remaining_kl)} KL</div></div>
        <div class="gstat"><div class="lbl">${OMC.esc(__("Catch-up / day"))}</div><div class="val sm">${OMC.fmt1(o.catchup_kl_day)} KL/d</div></div>
      </div>
      <div class="ghost-note"><span class="ghost-swatch"></span>${ghost}</div>
    </div>`;

		if (isExp) html += OMC._depotPanel(page, o);
	});
	grid.innerHTML = html || OMC._emptyState();
	if (animate) requestAnimationFrame(() => OMC._animateGauges(grid));
};

/* =====================================================================
   GAUGE ANIMATION — sweep the progress arc, count up the %, pop the knob.
   Runs only on a data-load/view-change render (not depot expand toggles).
   ===================================================================== */
OMC._animateGauges = function (grid) {
	if (!grid) return;
	const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
	if (reduce) return; // everything is already rendered at its final value

	// 1) Progress arcs "draw" from 0 -> value via stroke-dashoffset.
	grid.querySelectorAll(".omc-prog").forEach((p) => {
		let len = 0;
		try { len = p.getTotalLength(); } catch (e) { return; }
		if (!len) return;
		p.style.transition = "none";
		p.style.strokeDasharray = len + " " + len;
		p.style.strokeDashoffset = String(len);
		p.getBoundingClientRect(); // force reflow so the hidden start state sticks
		p.style.transition = "stroke-dashoffset 1.05s cubic-bezier(.22,.85,.28,1)";
		p.style.strokeDashoffset = "0";
	});

	// 2) End-knob pops in as the fill lands (CSS keyframe, gated by this class).
	grid.querySelectorAll(".omc-knob").forEach((k) => k.classList.add("omc-knob-go"));

	// 3) Count the % numbers up from 0.
	grid.querySelectorAll(".gpct-n").forEach((el) => {
		const to = parseFloat(el.getAttribute("data-to"));
		if (isNaN(to)) return;
		OMC._countUp(el, to, 1000);
	});
};

OMC._countUp = function (el, to, dur) {
	const start = performance.now();
	const ease = (t) => 1 - Math.pow(1 - t, 3); // easeOutCubic
	function step(now) {
		const t = Math.min(1, (now - start) / (dur || 1000));
		el.textContent = String(Math.round(to * ease(t)));
		if (t < 1) requestAnimationFrame(step);
		else el.textContent = String(Math.round(to));
	}
	requestAnimationFrame(step);
};

OMC._emptyState = function () {
	return `<div class="empty-state" style="grid-column:1/-1;">
    <svg class="es-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0z"/></svg>
    <div class="es-title">${OMC.esc(__("OMC allocation targets not set"))}</div>
    <div class="es-body">${OMC.esc(__("This Ethanol Supply Year has no active OMC supply targets yet. Set per-OMC quarterly allocations (TS OMC Supply Target) to populate the pace gauges, quarter chart and verdict."))}<br><br><b>${OMC.esc(__("Set OMC allocations to begin tracking."))}</b></div>
  </div>`;
};

/* =====================================================================
   DEPOT DRILL-DOWN (consumes o.depots[] + o.depots_unplanned)
   ===================================================================== */
OMC._dpctClass = function (p) { return p == null ? "" : p >= 98 ? "dpct-good" : p >= 90 ? "dpct-warn" : "dpct-bad"; };
OMC._depotBarColor = function (p) { return p == null ? "var(--faint)" : p >= 98 ? "var(--green)" : p >= 90 ? "var(--amber)" : "var(--red)"; };

OMC._depotPanel = function (page, o) {
	const esc = OMC.esc;
	const depots = o.depots || [];
	const unplanned = o.depots_unplanned || { supplied_kl: 0, count: 0 };
	const plannedTot = depots.reduce((a, d) => a + (d.planned_kl || 0), 0);
	const supPlanned = depots.reduce((a, d) => a + (d.supplied_kl || 0), 0);
	const supTotal = supPlanned + (unplanned.supplied_kl || 0);
	const periodLbl = OMC._periodLabel(page._data.period_selected || "FY");

	let rows = "";
	depots.forEach((d) => {
		const pct = d.pct;
		const barW = (pct != null) ? Math.min(100, pct) : 0;
		rows += `<tr>
      <td><span class="depot-name">
        <svg class="depot-pin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s-7-5.7-7-11a7 7 0 0114 0c0 5.3-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>
        ${esc(d.depot_label || d.customer)}</span></td>
      <td class="r">${OMC.fmt(d.planned_kl)}</td>
      <td class="r">${OMC.fmt(d.supplied_kl)}<span class="depot-mini-bar"><i style="width:${barW}%;background:${OMC._depotBarColor(pct)};color:${OMC._depotBarColor(pct)}"></i></span></td>
      <td class="r ${OMC._dpctClass(pct)}">${pct != null ? Math.round(pct) + "%" : "—"}</td>
    </tr>`;
	});
	if (unplanned.supplied_kl || unplanned.count) {
		rows += `<tr class="unplanned">
      <td><span class="depot-name">
        <svg class="depot-pin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="3 2"><path d="M12 21s-7-5.7-7-11a7 7 0 0114 0c0 5.3-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>
        ${esc(__("Unplanned depots"))} (${unplanned.count || 0})</span></td>
      <td class="r">—</td>
      <td class="r">${OMC.fmt(unplanned.supplied_kl)}</td>
      <td class="r">—</td>
    </tr>`;
	}
	if (!rows) {
		rows = `<tr><td colspan="4" class="depot-empty">${esc(__("No depot plan or supply recorded for this period."))}</td></tr>`;
	} else {
		const totPct = (o.allocation_kl > 0) ? Math.round(supTotal / o.allocation_kl * 100) : null;
		rows += `<tr class="dtot">
      <td>${esc(o.name)} · ${esc(__("all depots"))}</td>
      <td class="r">${OMC.fmt(plannedTot)}</td>
      <td class="r">${OMC.fmt(supTotal)}</td>
      <td class="r ${OMC._dpctClass(totPct)}">${totPct != null ? totPct + "%" : "—"}</td>
    </tr>`;
	}

	return `<div class="depot-panel glass" role="region" aria-label="${esc(o.name)} ${esc(__("per-depot breakdown"))}">
    <div class="depot-panel-h">
      <div class="dh-left">
        <span class="omc-logo ${OMC.logoCls(o.logo_cls || o.key)}">${esc(o.code || "")}</span>
        <div>
          <div class="dh-title">${esc(o.name)} — ${esc(__("Per-Depot Breakdown"))}</div>
          <div class="dh-sub">${esc(periodLbl)} · ${esc(__("planned-depot supplied + unplanned = card total"))}</div>
        </div>
      </div>
      <button class="depot-close" data-omc-toggle="${esc(o.code || o.key || "")}" aria-label="${esc(__("Close"))}" title="${esc(__("Close"))}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M6 6l12 12M18 6L6 18"/></svg>
      </button>
    </div>
    <table class="depot-table">
      <thead><tr><th>${esc(__("Depot"))}</th><th class="r">${esc(__("Planned KL"))}</th><th class="r">${esc(__("Supplied KL"))}</th><th class="r">%</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="depot-recon">
      ${esc(__("Reconciliation"))}: <b>${OMC.fmt(supPlanned)} KL</b> ${esc(__("across"))} ${depots.length} ${esc(__("planned depots"))}
      + <b>${OMC.fmt(unplanned.supplied_kl)} KL</b> ${esc(__("unplanned"))} = <b>${OMC.fmt(supTotal)} KL</b> ${esc(o.name)} ${esc(__("supplied"))} (${esc(periodLbl)}) — ${esc(__("matches the gauge card."))}
    </div>
  </div>`;
};

/* =====================================================================
   QUARTER-WISE SUPPLY BY OMC — from omcs[].per_quarter[] (always 4 Qs)
   ===================================================================== */
OMC._qbarStatus = function (pct) { return pct == null ? "amber" : pct >= 95 ? "green" : pct >= 80 ? "amber" : "red"; };
OMC._renderQuarterBars = function (page) {
	const d = page._data;
	const host = document.getElementById("omcQuarterBars");
	const titleEl = document.getElementById("omcQbarTitle");

	if (!d.targets_set) {
		host.innerHTML = `<div class="pending-note"><div class="pn-title">${OMC.esc(__("Quarter chart unavailable"))}</div><div class="pn-sub">${OMC.esc(__("Set OMC quarterly allocations to populate this chart."))}</div></div>`;
		return;
	}

	// groups = OMC cards (+ Plant Total), each with per_quarter[]; focus -> 1 group
	let groups = (d.omcs || []).slice();
	if (OMC.omcFocused(page)) groups = groups.filter((o) => OMC.matchFocus(o, page._omc));
	if (!groups.length) { host.innerHTML = ""; return; }

	const cs = getComputedStyle(OMC._root() || document.documentElement);
	const C = {
		green: (cs.getPropertyValue("--arc-green") || "#2ee29b").trim(),
		amber: (cs.getPropertyValue("--arc-amber") || "#ffb43c").trim(),
		red: (cs.getPropertyValue("--arc-red") || "#ff5a5a").trim(),
	};
	const TXT = (cs.getPropertyValue("--text") || "#fff").trim();
	const FAINT = (cs.getPropertyValue("--faint") || "#8b99b4").trim();
	const isLight = OMC._isLight(page);

	const W = 820, H = 300, padL = 46, padR = 14, padT = 24, padB = 46;
	const plot = W - padL - padR, plotH = H - padT - padB;
	let maxAlloc = 0;
	groups.forEach((g) => (g.per_quarter || []).forEach((c) => { if ((c.allocation_kl || 0) > maxAlloc) maxAlloc = c.allocation_kl; }));
	const max = Math.max(1000, Math.ceil(maxAlloc / 1000) * 1000);
	const nG = groups.length, nC = 4;
	const groupW = plot / nG;
	const groupPad = groupW * 0.14;
	const innerW = groupW - groupPad;
	const colGap = innerW * 0.10;
	const colW = (innerW - colGap * (nC - 1)) / nC;
	const x0 = (i) => padL + i * groupW + groupPad / 2;
	const y = (v) => padT + (1 - v / max) * plotH;
	const baseY = padT + plotH;

	let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;display:block;overflow:visible;" role="img" aria-label="${OMC.esc(__("Quarter-wise supply by OMC grouped column chart"))}">`;
	s += `<defs><filter id="omcQbarGlow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="0" stdDeviation="${isLight ? 1.2 : 2.2}" flood-color="${C.green}" flood-opacity="${isLight ? 0.30 : 0.55}"/></filter></defs>`;

	for (let k = 0; k <= 4; k++) {
		const gv = max * k / 4, gy = y(gv);
		s += `<line x1="${padL}" y1="${gy}" x2="${W - padR}" y2="${gy}" stroke="var(--track)" stroke-width="1"/>`;
		s += `<text x="${padL - 7}" y="${gy + 3}" text-anchor="end" font-size="9" fill="var(--faint)">${(gv / 1000).toFixed(gv % 1000 === 0 ? 0 : 1)}k</text>`;
	}
	s += `<line x1="${padL}" y1="${baseY}" x2="${W - padR}" y2="${baseY}" stroke="var(--g-border)" stroke-width="1"/>`;

	groups.forEach((g, gi) => {
		const gx = x0(gi);
		(g.per_quarter || []).forEach((c, ci) => {
			const cx = gx + ci * (colW + colGap);
			const alloc = c.allocation_kl || 0, sup = c.supplied_kl || 0;
			const pct = c.pct;
			const st = OMC._qbarStatus(pct);
			const allocTop = y(alloc);
			const supTop = y(sup);
			const supH = Math.max(0, baseY - supTop);
			const fill = C[st];
			const cur = !!c.is_current;
			const outlineStroke = cur ? TXT : FAINT;
			const outlineW = cur ? 1.8 : 1.2;
			const outlineOp = cur ? 0.9 : 0.6;
			s += `<rect x="${cx.toFixed(1)}" y="${allocTop.toFixed(1)}" width="${colW.toFixed(1)}" height="${(baseY - allocTop).toFixed(1)}" rx="3"
        fill="${cur ? (isLight ? "rgba(24,36,66,0.04)" : "rgba(255,255,255,0.05)") : "none"}"
        stroke="${outlineStroke}" stroke-width="${outlineW}" stroke-opacity="${outlineOp}"/>`;
			if (supH > 0.5) {
				s += `<rect x="${cx.toFixed(1)}" y="${supTop.toFixed(1)}" width="${colW.toFixed(1)}" height="${supH.toFixed(1)}" rx="3" fill="${fill}" opacity="0.30"/>`;
				s += `<rect x="${cx.toFixed(1)}" y="${supTop.toFixed(1)}" width="${colW.toFixed(1)}" height="${supH.toFixed(1)}" rx="3" fill="${fill}" ${cur ? `filter="url(#omcQbarGlow)"` : ""}/>`;
			}
			if (cur) {
				s += `<line x1="${(cx - 1.5).toFixed(1)}" y1="${(allocTop - 3).toFixed(1)}" x2="${(cx + colW + 1.5).toFixed(1)}" y2="${(allocTop - 3).toFixed(1)}" stroke="${TXT}" stroke-width="2" stroke-linecap="round" opacity="0.85"/>`;
			}
			const pctCol = st === "green" ? (isLight ? "#066b42" : "#74f3b6") : st === "amber" ? (isLight ? "#875407" : "#ffd784") : (isLight ? "#b41f1f" : "#ffa3a3");
			const pctLbl = (pct != null) ? Math.round(pct) + "%" : "—";
			s += `<text x="${(cx + colW / 2).toFixed(1)}" y="${(allocTop - 8).toFixed(1)}" text-anchor="middle" font-size="${cur ? 9.5 : 8.5}" font-weight="${cur ? 800 : 700}" fill="${pctCol}">${pctLbl}</text>`;
			s += `<text x="${(cx + colW / 2).toFixed(1)}" y="${(baseY + 14).toFixed(1)}" text-anchor="middle" font-size="8.5" font-weight="${cur ? 800 : 600}" fill="${cur ? TXT : FAINT}">${OMC.esc(c.q)}</text>`;
		});
		s += `<text x="${(gx + innerW / 2).toFixed(1)}" y="${(baseY + 33).toFixed(1)}" text-anchor="middle" font-size="11" font-weight="800" fill="var(--text)">${OMC.esc(g.name)}</text>`;
	});
	s += `</svg>`;
	host.innerHTML = s;
	if (titleEl) titleEl.textContent = OMC.omcFocused(page)
		? `${page._omc} ${__("Quarterly Fulfilment (KL)")}`
		: __("Per-OMC Quarterly Fulfilment (KL)");
};

/* =====================================================================
   RISK BANNER — server returns risk:null (PENDING). Show a derived
   per-OMC remaining/at-risk note from gauge data (no fake exposure ₹).
   ===================================================================== */
OMC._renderRisk = function (page) {
	const d = page._data;
	const banner = document.getElementById("omcRiskBanner");

	if (!d.targets_set) { banner.style.display = "none"; banner.innerHTML = ""; return; }

	// OMC focus: per-OMC banner from the card's status (only if behind / marginal)
	if (OMC.omcFocused(page)) {
		const o = OMC._omcCards(page).find((c) => OMC.matchFocus(c, page._omc));
		if (!o || OMC.paceCls(o.status) === "p-green") { banner.style.display = "none"; banner.innerHTML = ""; return; }
		banner.style.display = "";
		const cls = OMC.paceCls(o.status) === "p-red" ? "" : "b-amber";
		const perLbl = OMC._periodLabel(d.period_selected || "FY");
		banner.className = "risk-banner " + cls;
		banner.innerHTML =
			`<svg class="risk-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0z"/></svg>
      <div class="risk-text">
        <div class="risk-title">${OMC.esc(o.name)} ${OMC.esc(perLbl)} — ${o.pct != null ? Math.round(o.pct) : "—"}% ${OMC.esc(__("lifted vs"))} ${o.prorata_pct != null ? Math.round(o.prorata_pct) : "—"}% ${OMC.esc(__("pace"))} ${OMC.paceCls(o.status) === "p-red" ? "· " + OMC.esc(__("behind / at risk")) : "· " + OMC.esc(__("watch · marginal"))}</div>
        <div class="risk-body">${OMC.esc(o.name)} ${OMC.esc(__("has supplied"))} <b>${OMC.fmt(o.supplied_kl)} KL</b> ${OMC.esc(__("of its"))} <b>${OMC.fmt(o.allocation_kl)} KL</b> ${OMC.esc(perLbl)} ${OMC.esc(__("allocation"))} — <b>${OMC.fmt(o.remaining_kl)} KL ${OMC.esc(__("remaining"))}</b>. ${OMC.esc(__("Projected"))} ${o.projected_pct != null ? Math.round(o.projected_pct) : "—"}% ${OMC.esc(__("at period-end. Each LOI is a separate commitment; a surplus on another OMC does not forgive this gap."))}</div>
      </div>
      <div class="risk-exposure">
        <div class="big">${OMC.fmt(o.remaining_kl)} KL</div>
        <div class="lbl">${OMC.esc(o.name)} ${OMC.esc(perLbl)} ${OMC.esc(__("remaining"))}</div>
      </div>`;
		return;
	}

	// ALL: risk:null from server => PENDING plant-level commercial exposure.
	banner.style.display = "";
	banner.className = "risk-banner b-pending";
	const v = d.verdict || {};
	const behind = OMC._omcCards(page).filter((c) => OMC.paceCls(c.status) !== "p-green");
	const summary = behind.length
		? `${behind.length} ${OMC.esc(__("OMC(s) below pace"))}: ${behind.map((c) => OMC.esc(c.name) + " (" + (c.pct != null ? Math.round(c.pct) : "—") + "%)").join(", ")}.`
		: OMC.esc(__("All OMCs at or above pro-rata pace."));
	banner.innerHTML =
		`<svg class="risk-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0z"/></svg>
    <div class="risk-text">
      <div class="risk-title">${OMC.esc(__("Risk & Commercial Exposure"))} — ${OMC.esc(v.label || "—")}</div>
      <div class="risk-body">${summary}<br><span style="color:var(--faint);">${OMC.esc(__("Commercial exposure (PRC / BG / next-year allocation) — coming soon."))}</span></div>
    </div>
    <div class="risk-exposure">
      <div class="big" style="color:var(--muted);text-shadow:none;">—</div>
      <div class="lbl">${OMC.esc(__("Exposure · pending"))}</div>
    </div>`;
};

/* =====================================================================
   DAILY TARGET PANEL — daily fields are null (PENDING). Graceful note.
   ===================================================================== */
OMC._renderDaily = function (page) {
	const d = page._data;
	document.getElementById("omcDailyTitle").innerHTML =
		(OMC.omcFocused(page) ? `${OMC.esc(page._omc)} — ${OMC.esc(__("Daily Target vs Actual"))}` : OMC.esc(__("Per-OMC Daily Target vs Actual"))) + OMC._focusBadge(page);
	document.getElementById("omcAggTitle").textContent =
		OMC.omcFocused(page) ? `${page._omc} — ${__("Today")}` : __("Plant Total — Today");

	const pendingMsg = `<div class="pending-note">
    <div class="pn-title">${OMC.esc(__("Daily targets & catch-up run-rate — coming soon"))}</div>
    <div class="pn-sub">${OMC.esc(__("Per-OMC daily target = remaining KL ÷ days left. Being finalised; the period gauges and quarter chart above are live."))}</div>
  </div>`;
	document.getElementById("omcDailyBody").innerHTML = pendingMsg;
	document.getElementById("omcAggBody").innerHTML = `<div class="pending-note"><div class="pn-title">${OMC.esc(__("Today's dispatch — coming soon"))}</div><div class="pn-sub">${OMC.esc(__("Plant total dispatched / accepted KL for today will appear here."))}</div></div>`;
};

/* =====================================================================
   OPS STRIP — ops is null for CEO/MD; for ops roles it carries config
   only (most metrics PENDING). Render config transparency + pending note.
   ===================================================================== */
OMC._renderOps = function (page) {
	const d = page._data;
	const toggle = document.getElementById("omcOpsToggle");
	const body = document.getElementById("omcOpsBody");

	if (!d.ops) {
		// CEO/MD (or no ops role) -> hide the whole strip
		toggle.style.display = "none";
		body.innerHTML = "";
		body.classList.remove("open");
		return;
	}
	toggle.style.display = "";
	const ops = d.ops;

	// config cards (live) + a clear PENDING note for the operational metrics
	const cards = [
		{ h: __("Ethanol Items Resolved"), col: "blue", big: String(ops.ethanol_item_count || 0), small: `${OMC.esc(__("source"))}: ${OMC.esc(ops.ethanol_item_source || "—")}`, bar: ops.ethanol_item_count ? 100 : 0 },
		{ h: __("Plant KLPD"), col: d.klpd_set ? "green" : "amber", big: d.klpd_set ? OMC.fmt(d.plant_klpd) : "—", small: d.klpd_set ? __("nameplate capacity") : __("not set"), bar: d.klpd_set ? 100 : 0 },
		{ h: __("Unit Consistency"), col: ops.unit_uncertain ? "red" : "green", big: ops.unit_uncertain ? "⚠" : "✓", small: ops.unit_uncertain ? __("some DN lines unit-uncertain (skipped)") : __("all DN lines in L / KL"), bar: ops.unit_uncertain ? 40 : 100 },
		{ h: __("Capacity / Yield / Stock"), col: "amber", big: "—", small: __("operational metrics — coming soon"), bar: 0 },
	];
	let ohtml = "";
	cards.forEach((c) => {
		ohtml += `<div class="opc glass">
      <div class="h"><span class="ico-dot" style="background:var(--${c.col});color:var(--${c.col})"></span> ${OMC.esc(c.h)}</div>
      <div class="big">${c.big}</div>
      <div class="small">${OMC.esc(c.small)}</div>
      <div class="util-bar"><i style="width:${c.bar}%;background:var(--${c.col});color:var(--${c.col})"></i></div>
    </div>`;
	});

	body.innerHTML =
		`<div class="ops-cards">${ohtml}</div>
    <div class="panel glass" style="margin-top:16px;">
      <div class="panel-h"><span class="t">${OMC.esc(__("Per-OMC Required vs Actual Run-Rate & Catch-Up"))}</span>
        <span class="feas-tag tag-pending" style="margin:0;">${OMC.esc(__("run-rate detail · coming soon"))}</span></div>
      ${OMC._opsRunRate(page)}
      <div style="font-size:10px;color:var(--faint);margin-top:10px;line-height:1.5;">
        ${OMC.esc(__("Allocation & supplied are live from OMC targets and submitted dispatch DNs. Required / actual / catch-up run-rates are being finalised."))}
      </div>
    </div>`;
};

/* run-rate table — allocation/supplied/catch-up are live from the gauge
   payload; per-day required/actual are PENDING (rendered as "—"). */
OMC._opsRunRate = function (page) {
	const d = page._data;
	let list = OMC._omcCards(page).slice();
	if (OMC.omcFocused(page)) list = list.filter((o) => OMC.matchFocus(o, page._omc));
	const total = OMC.omcFocused(page) ? null : OMC._plantTotal(page);

	const row = (o, isTot) => {
		const cumCol = (o.cum_variance_kl >= 0) ? "var(--green-txt)" : "var(--red-txt)";
		const cumSign = (o.cum_variance_kl >= 0) ? "+" : "−";
		const b = isTot ? "b" : "span";
		return `<tr ${isTot ? 'style="border-top:2px solid var(--g-border-bright);"' : ""}>
      <td><span class="omc-cell"><span class="omc-logo ${OMC.logoCls(o.logo_cls || o.key)}" style="width:22px;height:22px;font-size:9px;">${OMC.esc(o.code || "")}</span><${b}>${OMC.esc(o.name)}</${b}></span></td>
      <td class="r"><${b}>${OMC.fmt(o.allocation_kl)}</${b}></td>
      <td class="r"><${b}>${OMC.fmt(o.supplied_kl)}</${b}></td>
      <td class="r">—</td>
      <td class="r">—</td>
      <td class="r" style="color:${cumCol}"><${b}>${cumSign}${OMC.fmt(Math.abs(o.cum_variance_kl || 0))}</${b}></td>
      <td class="r"><${b}>${OMC.fmt1(o.catchup_kl_day)}</${b}></td>
      <td><span class="chip-mini" style="background:var(--${o.status}-chip);color:var(--${o.status}-txt);">${OMC.esc((o.status_txt || "").toUpperCase())}</span></td>
    </tr>`;
	};
	let rhtml = list.map((o) => row(o, false)).join("");
	if (total) rhtml += row(total, true);

	return `<table class="rr-table">
    <thead><tr>
      <th>${OMC.esc(__("OMC"))}</th>
      <th class="r">${OMC.esc(__("Allocation KL"))}</th>
      <th class="r">${OMC.esc(__("Supplied KL"))}</th>
      <th class="r">${OMC.esc(__("Required KL/day"))}</th>
      <th class="r">${OMC.esc(__("Actual KL/day"))}</th>
      <th class="r">${OMC.esc(__("Cum. ±KL"))}</th>
      <th class="r">${OMC.esc(__("Catch-up KL/day"))}</th>
      <th>${OMC.esc(__("Status"))}</th>
    </tr></thead>
    <tbody>${rhtml}</tbody>
  </table>`;
};

/* =====================================================================
   TREND — trend.available === false (PENDING). Render the section shell
   with a graceful note (no fabricated curve).
   ===================================================================== */
OMC._renderTrend = function (page) {
	const d = page._data;
	const host = document.getElementById("omcTrendSection");
	const available = d.trend && d.trend.available;
	if (available) {
		// (Future) when the server ships trend data, render the SVG line chart here.
		host.innerHTML = `<div class="pending-note"><div class="pn-title">${OMC.esc(__("Trend data received"))}</div></div>`;
		return;
	}
	host.innerHTML = `<div class="trend-grid">
    <div class="panel glass">
      <div class="panel-h"><span class="t">${OMC.esc(__("Cumulative Plant Supply (KL)"))}</span>
        <div class="legend">
          <span><i class="line" style="background:var(--green)"></i>${OMC.esc(__("Actual"))}</span>
          <span><i class="line" style="background:var(--amber);opacity:.85"></i>${OMC.esc(__("Pro-rata target"))}</span>
        </div>
      </div>
      <div class="pending-note"><div class="pn-title">${OMC.esc(__("Cumulative supply trend — coming soon"))}</div><div class="pn-sub">${OMC.esc(__("The month-wise target-vs-actual curve is being finalised. Quarter-wise supply (above) is live."))}</div></div>
    </div>
    <div class="panel glass">
      <div class="panel-h"><span class="t">${OMC.esc(__("Month-wise Supply (KL)"))}</span></div>
      <div class="pending-note"><div class="pn-title">${OMC.esc(__("Month-wise table — coming soon"))}</div></div>
    </div>
  </div>`;
};

/* =====================================================================
   FOOTER
   ===================================================================== */
OMC._renderFooter = function (page) {
	const d = page._data;
	const esy = (d.esy && d.esy.code) ? `· ${__("Alcoholic Year")} ${d.esy.code}` : "";
	const fp = document.getElementById("omcFooterPeriod");
	if (fp) fp.textContent = esy;
	const fu = document.getElementById("omcFooterUpdated");
	if (fu) fu.textContent = d.last_updated ? `${__("Last updated")}: ${frappe.datetime.str_to_user(d.last_updated)}` : "";
};
