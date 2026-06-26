/**
 * OMC Ethanol Supply Tracker — Wall Display (boardroom / meter-room TV).
 *
 * Mirrors the house ceo_wall_display pattern:
 *   - hides the ERPNext navbar + page-head + side section, goes fullscreen
 *   - auto-refreshes every 30s (interval stored on page._refresh_interval,
 *     CLEARED on page hide / ESC exit)
 *   - live clock (page._clock_interval, cleared on exit)
 *   - ESC (or EXIT button) -> frappe.set_route("omc-supply-tracker")
 *
 * It reuses the SAME read API and renders the hero + pace gauges + quarter
 * chart. Daily / ops / trend sections are hidden by the wall CSS
 * (.omc-tracker.wall). PENDING + targets_set=false states render gracefully.
 *
 * Self-contained: this bundle does not assume the main page's JS is loaded,
 * so the gauge + quarter-bar SVG helpers are included here under OMCW.*.
 */

frappe.pages["omc-supply-tracker-wall"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("OMC Ethanol Supply Tracker — Wall Display"),
		single_column: true,
	});

	$(".navbar, .page-head").hide();
	$("[data-page-container] > .layout-side-section").hide();

	page.main.html('<div id="omcw-host"></div>');
	page._refresh_interval = null;
	page._clock_interval = null;
	page._esc_handler = null;
	page._data = null;
	page._call_token = 0;

	// Page-local theme: white (light) by default, isolated from Frappe's global
	// data-theme. State lives on the .omc-tracker.wall root only (set in _render).
	page._theme = "light";

	OMCW._init(page);
};

frappe.pages["omc-supply-tracker-wall"].on_page_show = function (wrapper) {
	$(".navbar, .page-head").hide();
	$("[data-page-container] > .layout-side-section").hide();
	// I1: re-assert the page-local theme + re-render when routing main->wall,
	// so a toggle on the main page never leaves the wall mis-themed.
	const page = wrapper && wrapper.page;
	if (page) {
		OMCW._applyTheme(page);
		page._gaugesAnimated = false; // re-sweep the gauges once on each visit
		if (page._data) OMCW._render(page);
	}
};

frappe.pages["omc-supply-tracker-wall"].on_page_hide = function (wrapper) {
	OMCW._teardown(wrapper && wrapper.page);
	$(".navbar, .page-head").show();
	$("[data-page-container] > .layout-side-section").show();
};

window.OMCW = window.OMCW || {};

/* =====================================================================
   PAGE-LOCAL THEME — isolated from Frappe's global data-theme.
   The theme attribute lives on the .omc-tracker.wall root only, so a dark
   desk never re-themes this wall display.
   ===================================================================== */
OMCW._root = function () { return document.querySelector("#omcw-host .omc-tracker"); };
/* write the page-local theme attr onto the wall root (default light/white) */
OMCW._applyTheme = function (page) {
	const t = (page && page._theme === "dark") ? "dark" : "light";
	if (page) page._theme = t;
	const root = OMCW._root();
	if (root) root.setAttribute("data-omc-theme", t);
	return t;
};
/* page-local "is this page in light mode?" — reads page state, never the desk */
OMCW._isLight = function (page) {
	if (page && page._theme) return page._theme === "light";
	const root = OMCW._root();
	return !root || root.getAttribute("data-omc-theme") !== "dark";
};

OMCW._init = function (page) {
	const el = document.documentElement;
	if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
	else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();

	// live clock
	page._clock_interval = setInterval(() => {
		const $clock = $(".wall-clock");
		if ($clock.length) {
			$clock.text(new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
		}
	}, 1000);

	// ESC -> exit
	page._esc_handler = (e) => { if (e.key === "Escape") OMCW._exit(page); };
	document.addEventListener("keydown", page._esc_handler);

	// EXIT button (delegated)
	page.main.on("click", "#omcwExit", () => OMCW._exit(page));

	OMCW._fetch(page);
	page._refresh_interval = setInterval(() => OMCW._fetch(page), 30000);
};

OMCW._teardown = function (page) {
	if (!page) return;
	if (page._refresh_interval) { clearInterval(page._refresh_interval); page._refresh_interval = null; }
	if (page._clock_interval) { clearInterval(page._clock_interval); page._clock_interval = null; }
	if (page._esc_handler) { document.removeEventListener("keydown", page._esc_handler); page._esc_handler = null; }
};

OMCW._exit = function (page) {
	OMCW._teardown(page);
	if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
	else if (document.webkitFullscreenElement) document.webkitExitFullscreen();
	$(".navbar, .page-head").show();
	$("[data-page-container] > .layout-side-section").show();
	frappe.set_route("omc-supply-tracker");
};

/* ── helpers (mirrors the main page) ─────────────────────────── */
OMCW.esc = function (v) { return frappe.utils.escape_html(v == null ? "" : String(v)); };
OMCW.fmt = function (n) { return (n == null || isNaN(n)) ? "—" : Math.round(n).toLocaleString("en-IN"); };
OMCW.paceCls = function (s) { return s === "green" ? "p-green" : s === "red" ? "p-red" : "p-amber"; };
OMCW.logoCls = function (key) {
	const k = (key || "").toString().toLowerCase();
	if (k === "total") return "l-total";
	if (k === "iocl") return "l-iocl";
	if (k === "hpcl") return "l-hpcl";
	if (k === "bpcl") return "l-bpcl";
	return "l-generic";
};
OMCW.isTotal = function (o) { return o && (o.key === "total" || o.logo_cls === "total"); };
OMCW.periodLabel = function (code) {
	return { Q1: "Quarter-1 · Nov–Jan", Q2: "Quarter-2 · Feb–Apr", Q3: "Quarter-3 · May–Jul",
		Q4: "Quarter-4 · Aug–Oct", FY: "Full Year" }[code] || code || "—";
};

/* ── data ────────────────────────────────────────────────────── */
OMCW._fetch = function (page) {
	const token = ++page._call_token;
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_dashboard_omc_supply.get_omc_supply_tracker",
		args: { esy: null, period: null }, // wall = server default ESY + current quarter
		callback(r) {
			if (token !== page._call_token) return;
			if (r.message) { page._data = r.message; OMCW._render(page); }
		},
	});
};

/* ── render ──────────────────────────────────────────────────── */
OMCW._render = function (page) {
	const d = page._data;
	const esc = OMCW.esc;
	const esy = d.esy || {};
	const pcode = d.period_selected || "FY";

	// verdict chip class
	const vmap = { at_risk: "v-risk", action: "v-action", on_track: "v-ok", none: "v-none" };
	const v = d.verdict || { level: "none", label: "—" };
	const vcls = vmap[v.level] || "v-none";

	const daysLabel = esy.total_days
		? `${esc(__("Day"))} <b>${esy.day_index}</b> ${esc(__("of"))} ${esy.total_days} · <b>${esy.days_left} ${esc(__("days left"))}</b>`
		: esc(__("Window not set"));
	const prorata = (esy.prorata_pct != null) ? `${esy.prorata_pct}%` : "—";

	const themeAttr = (page._theme === "dark") ? "dark" : "light";
	let html = `<div class="omc-tracker wall" data-omc-theme="${themeAttr}">
    <div class="bg-stage" aria-hidden="true">
      <div class="orb a"></div><div class="orb b"></div><div class="orb c"></div><div class="orb d"></div><div class="orb e"></div>
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
          <div class="brand-sub">${esc(__("OMC Ethanol Supply Tracker"))} · ${esc(__("Wall Display"))} · ${esc(__("Live"))}</div>
        </div>
        <div class="hero-spacer"></div>
        <span class="period-tag">${esc(OMCW.periodLabel(pcode))}</span>
        <div class="verdict-chip ${vcls}"><span class="pulse-dot"></span> ${esc((v.label || "—")).toUpperCase()}</div>
        <div class="wall-clock"></div>
        <button class="exit-btn" id="omcwExit">${esc(__("Exit"))}</button>
      </div>
      <div class="meta-row">
        <span>${esc(__("Ethanol Supply Year"))}: <b>${esc(esy.code || "—")}</b></span>
        <span class="sep"></span>
        <span>${daysLabel}</span>
        <span class="sep"></span>
        <span>${esc(__("Pro-rata pace target"))}: <b>${esc(prorata)}</b></span>
        <span class="sep"></span>
        <span class="live-tag"><span class="pulse-dot" style="background:var(--green);box-shadow:0 0 9px var(--green)"></span>${esc(__("Live"))}${d.last_updated ? " · " + esc(frappe.datetime.str_to_user(d.last_updated)) : ""}</span>
      </div>
    </div>

    <div class="wrap">
      <div class="sec-title">
        <span class="bar"></span> ${esc(__("OMC Fulfilment — Pace Gauges"))}
        <span class="feas-tag tag-live">${esc(__("Live · dispatch-accepted KL"))}</span>
      </div>
      <div class="gauge-grid" id="omcwGauges"></div>

      <div class="sec-title">
        <span class="bar" style="background:var(--green)"></span> ${esc(__("Quarter-wise Supply by OMC"))}
        <span class="feas-tag tag-live">${esc(__("All four quarters"))}</span>
      </div>
      <div class="trend-grid" style="grid-template-columns:1fr;">
        <div class="panel glass">
          <div class="panel-h"><span class="t">${esc(__("Per-OMC Quarterly Fulfilment (KL)"))}</span></div>
          <div id="omcwQuarter"></div>
        </div>
      </div>
    </div>
  </div>`;

	$("#omcw-host").html(html);
	OMCW._applyTheme(page); // re-assert page-local theme on the freshly built root
	// Animate the gauge draw only on the FIRST paint per visit; the 30s auto-refresh
	// polls then update values quietly (no re-sweep on a wall display).
	const animGauges = !page._gaugesAnimated;
	page._gaugesAnimated = true;
	OMCW._renderGauges(page, animGauges);
	OMCW._renderQuarter(page);
};

/* ── pace gauge SVG (ported verbatim) ────────────────────────── */
OMCW._polar = function (cx, cy, r, deg) { const a = (deg - 180) * Math.PI / 180; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; };
OMCW._arc = function (cx, cy, r, a0, a1, color, w, extra, cap) {
	const [x0, y0] = OMCW._polar(cx, cy, r, a0), [x1, y1] = OMCW._polar(cx, cy, r, a1);
	const large = (a1 - a0) > 180 ? 1 : 0;
	return `<path d="M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}" stroke="${color}" stroke-width="${w}" fill="none" stroke-linecap="${cap || "round"}" ${extra || ""}/>`;
};
OMCW._gid = 0;
OMCW._gauge = function (cfg, page) {
	const cx = 115, cy = 115, r = 84, id = "omcwg" + (OMCW._gid++);
	const wBand = 8, wProg = 15;
	const deg = (p) => Math.max(0, Math.min(100, p)) / 100 * 180;
	// read the page-scoped CSS vars off the .omc-tracker root (theme is local)
	const cs = getComputedStyle(OMCW._root() || document.documentElement);
	const AR = (cs.getPropertyValue("--arc-red") || "#ff5a5a").trim();
	const AA = (cs.getPropertyValue("--arc-amber") || "#ffb43c").trim();
	const AG = (cs.getPropertyValue("--arc-green") || "#2ee29b").trim();
	const TXT = (cs.getPropertyValue("--text") || "#fff").trim();
	const isLight = OMCW._isLight(page);
	const HALO = isLight ? "#ffffff" : "rgba(255,255,255,0.9)";
	const PROG = cfg.status === "p-green" ? AG : cfg.status === "p-red" ? AR : AA;

	let s = `<svg viewBox="0 0 230 140" style="width:100%;display:block;overflow:visible;"><defs>
    <filter id="${id}prog" x="-40%" y="-40%" width="180%" height="180%"><feDropShadow dx="0" dy="0" stdDeviation="${isLight ? 1.8 : 3}" flood-color="${PROG}" flood-opacity="${isLight ? 0.5 : 0.8}"/></filter>
    <filter id="${id}soft" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="1.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>`;
	s += OMCW._arc(cx, cy, r, 0, 180, "var(--gauge-track)", wBand + 4, "", "round");
	const rE = deg(cfg.redEnd), aE = deg(cfg.amberEnd), bandOp = isLight ? 0.50 : 0.46;
	s += OMCW._arc(cx, cy, r, 0, rE, AR, wBand, `opacity="${bandOp}"`, "butt");
	s += OMCW._arc(cx, cy, r, rE, aE, AA, wBand, `opacity="${bandOp}"`, "butt");
	s += OMCW._arc(cx, cy, r, aE, 180, AG, wBand, `opacity="${bandOp}"`, "butt");
	s += OMCW._arc(cx, cy, r + wBand / 2 + 0.5, 0, 180, "var(--gauge-rim)", 1, "", "round");
	const aPct = deg(cfg.pct);
	if (aPct > 0.4) {
		s += OMCW._arc(cx, cy, r, 0, aPct, PROG, wProg, `opacity="0.28" class="omc-prog"`, "round");
		s += OMCW._arc(cx, cy, r, 0, aPct, PROG, wProg, `filter="url(#${id}prog)" class="omc-prog"`, "round");
	}
	const [lx, ly] = OMCW._polar(cx, cy, r, aPct);
	if (aPct > 0.4) {
		s += `<g class="omc-knob">`;
		s += `<circle cx="${lx}" cy="${ly}" r="${wProg / 2 + 2.0}" fill="${HALO}" opacity="0.95" filter="url(#${id}soft)"/>`;
		s += `<circle cx="${lx}" cy="${ly}" r="${wProg / 2 - 0.4}" fill="${PROG}" filter="url(#${id}prog)"/>`;
		s += `<circle cx="${lx}" cy="${ly}" r="${wProg / 2 - 3.4}" fill="${HALO}" opacity="0.9"/>`;
		s += `</g>`;
	}
	const tDeg = deg(cfg.prorata);
	const [tn0x, tn0y] = OMCW._polar(cx, cy, r - wProg / 2 - 2.5, tDeg);
	const [tn1x, tn1y] = OMCW._polar(cx, cy, r + wBand / 2 + 2.5, tDeg);
	s += `<line x1="${tn0x}" y1="${tn0y}" x2="${tn1x}" y2="${tn1y}" stroke="${HALO}" stroke-width="4.6" stroke-linecap="round"/>`;
	s += `<line x1="${tn0x}" y1="${tn0y}" x2="${tn1x}" y2="${tn1y}" stroke="${TXT}" stroke-width="2.1" stroke-linecap="round"/>`;
	const [pjx, pjy] = OMCW._polar(cx, cy, r, deg(cfg.projected));
	s += `<circle cx="${pjx}" cy="${pjy}" r="4.6" fill="${isLight ? "#fff" : "rgba(10,15,29,0.65)"}" stroke="${AA}" stroke-width="2.3" opacity="${isLight ? 0.96 : 0.92}" filter="url(#${id}soft)"/>`;
	s += `</svg>`;
	return s;
};

OMCW._renderGauges = function (page, animate) {
	OMCW._gid = 0;
	const d = page._data;
	const grid = document.getElementById("omcwGauges");
	if (!grid) return;
	if (!d.targets_set) {
		grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
      <div class="es-title">${OMCW.esc(__("OMC allocation targets not set"))}</div>
      <div class="es-body">${OMCW.esc(__("Set per-OMC quarterly allocations to populate the wall display."))}</div></div>`;
		return;
	}
	let html = "";
	(d.omcs || []).forEach((o) => {
		const total = OMCW.isTotal(o);
		const statusCls = OMCW.paceCls(o.status);
		const pctTxt = (o.pct != null) ? Math.round(o.pct) : "—";
		html += `<div class="gcard glass ${total ? "total" : ""}">
      <div class="gcard-head">
        <span class="omc-logo ${OMCW.logoCls(o.logo_cls || o.key)}">${OMCW.esc(o.code || (o.name || "").slice(0, 2))}</span>
        <div><div class="gcard-name">${OMCW.esc(o.name)}</div><div class="gcard-fullname">${OMCW.esc(o.full || o.name)}</div></div>
      </div>
      <div class="gauge-wrap">
        ${OMCW._gauge({ pct: o.pct || 0, prorata: o.prorata_pct || 0, projected: o.projected_pct || 0, redEnd: o.redEnd || 0, amberEnd: o.amberEnd || 0, status: statusCls }, page)}
        <div class="gauge-center">
          <div class="gauge-pct"><span class="gpct-n" data-to="${(typeof pctTxt === "number") ? pctTxt : ""}">${pctTxt}</span><small>%</small></div>
          <div class="gauge-sub">${OMCW.fmt(o.supplied_kl)} / ${OMCW.fmt(o.allocation_kl)} KL</div>
        </div>
      </div>
      <span class="status-pill ${statusCls}">${OMCW.esc(o.status_txt || "—")}</span>
      <div class="gstats">
        <div class="gstat"><div class="lbl">${OMCW.esc(__("Allocation"))}</div><div class="val">${OMCW.fmt(o.allocation_kl)} <span style="font-size:10px;color:var(--faint)">KL</span></div></div>
        <div class="gstat"><div class="lbl">${OMCW.esc(__("Supplied"))}</div><div class="val">${OMCW.fmt(o.supplied_kl)} <span style="font-size:10px;color:var(--faint)">KL</span></div></div>
        <div class="gstat wide"><div class="lbl">${OMCW.esc(__("Remaining"))}</div><div class="val sm">${OMCW.fmt(o.remaining_kl)} KL</div></div>
      </div>
    </div>`;
	});
	grid.innerHTML = html;
	if (animate) requestAnimationFrame(() => OMCW._animateGauges(grid));
};

/* ── gauge animation — arc draw + %-count-up + knob pop (first paint only) ── */
OMCW._animateGauges = function (grid) {
	if (!grid) return;
	const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
	if (reduce) return;
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
	grid.querySelectorAll(".omc-knob").forEach((k) => k.classList.add("omc-knob-go"));
	grid.querySelectorAll(".gpct-n").forEach((el) => {
		const to = parseFloat(el.getAttribute("data-to"));
		if (isNaN(to)) return;
		OMCW._countUp(el, to, 1000);
	});
};

OMCW._countUp = function (el, to, dur) {
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

/* ── quarter bars (from per_quarter[]) ───────────────────────── */
OMCW._qbarStatus = function (pct) { return pct == null ? "amber" : pct >= 95 ? "green" : pct >= 80 ? "amber" : "red"; };
OMCW._renderQuarter = function (page) {
	const d = page._data;
	const host = document.getElementById("omcwQuarter");
	if (!host) return;
	if (!d.targets_set) {
		host.innerHTML = `<div class="pending-note"><div class="pn-title">${OMCW.esc(__("Quarter chart unavailable"))}</div></div>`;
		return;
	}
	const groups = (d.omcs || []).slice();
	if (!groups.length) { host.innerHTML = ""; return; }

	const cs = getComputedStyle(OMCW._root() || document.documentElement);
	const C = {
		green: (cs.getPropertyValue("--arc-green") || "#2ee29b").trim(),
		amber: (cs.getPropertyValue("--arc-amber") || "#ffb43c").trim(),
		red: (cs.getPropertyValue("--arc-red") || "#ff5a5a").trim(),
	};
	const TXT = (cs.getPropertyValue("--text") || "#fff").trim();
	const FAINT = (cs.getPropertyValue("--faint") || "#8b99b4").trim();
	const isLight = OMCW._isLight(page);

	const W = 820, H = 300, padL = 46, padR = 14, padT = 24, padB = 46;
	const plot = W - padL - padR, plotH = H - padT - padB;
	let maxAlloc = 0;
	groups.forEach((g) => (g.per_quarter || []).forEach((c) => { if ((c.allocation_kl || 0) > maxAlloc) maxAlloc = c.allocation_kl; }));
	const max = Math.max(1000, Math.ceil(maxAlloc / 1000) * 1000);
	const nG = groups.length, nC = 4;
	const groupW = plot / nG, groupPad = groupW * 0.14, innerW = groupW - groupPad;
	const colGap = innerW * 0.10, colW = (innerW - colGap * (nC - 1)) / nC;
	const x0 = (i) => padL + i * groupW + groupPad / 2;
	const y = (v) => padT + (1 - v / max) * plotH;
	const baseY = padT + plotH;

	let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;display:block;overflow:visible;" role="img" aria-label="${OMCW.esc(__("Quarter-wise supply by OMC"))}">`;
	s += `<defs><filter id="omcwQbarGlow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="0" stdDeviation="${isLight ? 1.2 : 2.2}" flood-color="${C.green}" flood-opacity="${isLight ? 0.30 : 0.55}"/></filter></defs>`;
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
			const alloc = c.allocation_kl || 0, sup = c.supplied_kl || 0, pct = c.pct;
			const st = OMCW._qbarStatus(pct), allocTop = y(alloc), supTop = y(sup);
			const supH = Math.max(0, baseY - supTop), fill = C[st], cur = !!c.is_current;
			s += `<rect x="${cx.toFixed(1)}" y="${allocTop.toFixed(1)}" width="${colW.toFixed(1)}" height="${(baseY - allocTop).toFixed(1)}" rx="3" fill="${cur ? (isLight ? "rgba(24,36,66,0.04)" : "rgba(255,255,255,0.05)") : "none"}" stroke="${cur ? TXT : FAINT}" stroke-width="${cur ? 1.8 : 1.2}" stroke-opacity="${cur ? 0.9 : 0.6}"/>`;
			if (supH > 0.5) {
				s += `<rect x="${cx.toFixed(1)}" y="${supTop.toFixed(1)}" width="${colW.toFixed(1)}" height="${supH.toFixed(1)}" rx="3" fill="${fill}" opacity="0.30"/>`;
				s += `<rect x="${cx.toFixed(1)}" y="${supTop.toFixed(1)}" width="${colW.toFixed(1)}" height="${supH.toFixed(1)}" rx="3" fill="${fill}" ${cur ? `filter="url(#omcwQbarGlow)"` : ""}/>`;
			}
			const pctCol = st === "green" ? (isLight ? "#066b42" : "#74f3b6") : st === "amber" ? (isLight ? "#875407" : "#ffd784") : (isLight ? "#b41f1f" : "#ffa3a3");
			const pctLbl = (pct != null) ? Math.round(pct) + "%" : "—";
			s += `<text x="${(cx + colW / 2).toFixed(1)}" y="${(allocTop - 8).toFixed(1)}" text-anchor="middle" font-size="${cur ? 9.5 : 8.5}" font-weight="${cur ? 800 : 700}" fill="${pctCol}">${pctLbl}</text>`;
			s += `<text x="${(cx + colW / 2).toFixed(1)}" y="${(baseY + 14).toFixed(1)}" text-anchor="middle" font-size="8.5" font-weight="${cur ? 800 : 600}" fill="${cur ? TXT : FAINT}">${OMCW.esc(c.q)}</text>`;
		});
		s += `<text x="${(gx + innerW / 2).toFixed(1)}" y="${(baseY + 33).toFixed(1)}" text-anchor="middle" font-size="11" font-weight="800" fill="var(--text)">${OMCW.esc(g.name)}</text>`;
	});
	s += `</svg>`;
	host.innerHTML = s;
};
