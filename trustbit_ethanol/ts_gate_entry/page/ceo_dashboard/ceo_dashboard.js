// CEO Dashboard (v2.64.0 redesign) — data from ts_dashboard_ceo.get_ceo_overview.
// The older get_ceo_dashboard method still feeds the CEO Wall Display; leave it.
// Colours are Frappe CSS variables only, so the page follows light/dark theme.

const CEO_D_METHOD = "trustbit_ethanol.ts_gate_entry.ts_dashboard_ceo.get_ceo_overview";
const CEO_D_CATS = { Grain: "--blue-600", Coal: "--gray-600", Store: "--orange-500", Capex: "--purple-500", Other: "--gray-400" };

const CEO_D_CSS = `
.ceo-d{--ceo-gap:32px;color:var(--text-color);padding:8px 0 40px;display:flex;flex-direction:column;gap:var(--ceo-gap)}
.ceo-d *{box-sizing:border-box}
.ceo-d a{color:inherit;text-decoration:none}
.ceo-d a:focus-visible,.ceo-d button:focus-visible{outline:2px solid var(--blue-600);outline-offset:2px}
.ceo-d-busy .ceo-d-body{opacity:.6;transition:opacity .2s}
.ceo-d-body{display:flex;flex-direction:column;gap:var(--ceo-gap)}
.ceo-d-ctx{font-size:12px;color:var(--text-muted)}
.ceo-d-banner{padding:10px 14px;border-radius:var(--border-radius-md);font-size:13px}
.ceo-d-banner.err{background:var(--alert-bg-danger);color:var(--alert-text-danger)}
.ceo-d-banner.info{background:var(--alert-bg-info);color:var(--alert-text-info)}
.ceo-d-banners:empty{display:none}
.ceo-d-retry{min-height:44px}
.ceo-d-sec{display:flex;flex-direction:column;gap:12px;min-width:0}
.ceo-d-sh{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.ceo-d-sh h2{margin:0;font-size:16px;font-weight:600;color:var(--heading-color)}
.ceo-d-cap{font-size:12px;color:var(--text-muted)}
.ceo-d-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:var(--border-radius-md);padding:16px;min-width:0}
.ceo-d-card.dash{border-style:dashed;background:transparent}
a.ceo-d-card:hover,.ceo-d-row:hover{background:var(--fg-hover-color)}
.ceo-d-g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.ceo-d-tile{display:flex;flex-direction:column;gap:6px;min-height:96px}
.ceo-d-lbl{font-size:13px;font-weight:500;color:var(--text-muted)}
.ceo-d-n{font-size:28px;font-weight:700;color:var(--heading-color);font-variant-numeric:tabular-nums;line-height:1.1}
.ceo-d-n.sm{font-size:22px}
.ceo-d-amt{font-size:16px;font-weight:600;color:var(--heading-color);font-variant-numeric:tabular-nums}
.ceo-d-meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:13px;color:var(--text-muted)}
.ceo-d-chip{display:inline-block;font-size:12px;font-weight:500;padding:2px 8px;border-radius:999px;white-space:nowrap}
.ceo-d-chip.d{background:var(--alert-bg-danger);color:var(--alert-text-danger)}
.ceo-d-chip.w{background:var(--bg-orange);color:var(--text-on-orange)}
.ceo-d-chip.n{background:var(--subtle-fg);color:var(--text-muted)}
.ceo-d-stuck{background:var(--alert-bg-danger);border-radius:var(--border-radius-md);padding:12px 16px;display:flex;flex-direction:column}
.ceo-d-stuck-h{font-weight:600;color:var(--alert-text-danger);padding-bottom:6px}
.ceo-d-stuck .ceo-d-chip{background:var(--card-bg)}
.ceo-d-stuck a{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;align-items:center;min-height:44px;padding:6px 0;border-top:1px solid var(--border-color)}
.ceo-d-row{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr) minmax(0,1.6fr) minmax(0,.8fr) minmax(0,.7fr);gap:12px;align-items:center;min-height:44px;padding:8px 4px;border-top:1px solid var(--border-color)}
.ceo-d-r{text-align:right;font-variant-numeric:tabular-nums}
.ceo-d-b{font-weight:600;color:var(--heading-color)}
.ceo-d-purch{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:16px}
.ceo-d-big{font-size:32px;font-weight:700;color:var(--heading-color);font-variant-numeric:tabular-nums;line-height:1.1}
.ceo-d-cat{display:grid;grid-template-columns:12px minmax(0,1fr) auto;gap:4px 10px;align-items:center;padding:8px 0;border-top:1px solid var(--border-color)}
.ceo-d-sw{width:12px;height:12px;border-radius:3px;display:inline-block}
.ceo-d-track{grid-column:2 / 4;height:8px;border-radius:4px;background:var(--subtle-fg);overflow:hidden;display:block}
.ceo-d-fill{display:block;height:8px;border-radius:4px;background:var(--text-muted)}
.ceo-d-fill.over{background:var(--red-500)}
.ceo-d-chart{display:flex;align-items:flex-end;gap:14px;height:230px;border-bottom:1px solid var(--border-color)}
.ceo-d-col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%;gap:4px;min-width:0}
.ceo-d-colv{font-size:12px;color:var(--text-muted);text-align:center;font-variant-numeric:tabular-nums;white-space:nowrap}
.ceo-d-stack{display:flex;flex-direction:column-reverse;border-radius:4px 4px 0 0;overflow:hidden}
.ceo-d-xl{display:flex;gap:14px;margin-top:6px}
.ceo-d-xl span{flex:1;text-align:center;font-size:12px;color:var(--text-muted)}
.ceo-d-legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--text-muted);margin-top:10px}
.ceo-d-legend span{display:inline-flex;gap:6px;align-items:center}
.ceo-d-srow{display:grid;grid-template-columns:24px minmax(0,1fr) auto auto;gap:12px;align-items:center;min-height:44px;padding:8px 4px;border-top:1px solid var(--border-color)}
.ceo-d-sum{display:flex;flex-wrap:wrap;gap:24px}
.ceo-d-bud{columns:2;column-gap:24px}
.ceo-d-brow{break-inside:avoid;display:flex;flex-direction:column;gap:6px;padding:10px 0;border-top:1px solid var(--border-color)}
.ceo-d-brow-h{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.ceo-d-two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:32px}
.ceo-d-stock{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.ceo-d-skel{background:var(--skeleton-bg);border-radius:var(--border-radius-md);min-height:96px}
.ceo-d-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
@media (prefers-reduced-motion:no-preference){.ceo-d-skel{animation:ceo-d-pulse 1.4s ease-in-out infinite}}
@keyframes ceo-d-pulse{50%{opacity:.55}}
@media (min-width:768px){#page-ceo-dashboard .page-form .frappe-control[data-fieldname="company"]{flex:0 0 18rem;max-width:18rem}}
@media (max-width:1199.98px){.ceo-d-purch,.ceo-d-two{grid-template-columns:minmax(0,1fr)}.ceo-d-bud{columns:1}}
@media (max-width:767.98px){.ceo-d{--ceo-gap:24px}.ceo-d-g3{grid-template-columns:minmax(0,1fr)}.ceo-d-n{font-size:24px}.ceo-d-big{font-size:26px}
 .ceo-d-row{grid-template-columns:minmax(0,1fr) auto}.ceo-d-hs{display:none}.ceo-d-chart{gap:8px}.ceo-d-xl{gap:8px}
 .ceo-d-colv{visibility:hidden}
 .ceo-d-stock{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}#page-ceo-dashboard .page-form .form-control{min-height:44px}}
`;

frappe.pages["ceo-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("CEO Dashboard"), single_column: true });
	if (!document.getElementById("ceo-d-styles")) {
		$("<style id='ceo-d-styles'>").text(CEO_D_CSS).appendTo("head");
	}
	const st = (wrapper.ceo_d = { page, data: null, links: [], timer: null });
	// append, not html: page.main also holds the Period / Company filter bar.
	page.main.append('<div class="ceo-d"><div class="ceo-d-ctx"></div><div class="ceo-d-banners"></div><div class="ceo-d-body"></div></div>');
	page.add_button(__("Wall Display"), () => frappe.set_route("ceo-wall-display"));
	page.add_button(__("Refresh"), () => ceo_d_refresh(wrapper), { icon: "refresh" });
	page.add_field({
		fieldname: "date_range", label: __("Period"), fieldtype: "Select",
		options: "Today\nThis Week\nThis Month\nThis Quarter\nThis Year", default: "This Month",
		change: () => ceo_d_refresh(wrapper, true),
	});
	page.add_field({
		fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
		default: frappe.defaults.get_global_default("company"),
		change: () => ceo_d_refresh(wrapper, true),
	});
	page.main.on("click", "[data-ceo-go]", (e) => {
		e.preventDefault();
		ceo_d_go(st.links[+e.currentTarget.dataset.ceoGo]);
	});
	page.main.on("click", ".ceo-d-retry", () => ceo_d_refresh(wrapper));
	ceo_d_skeleton(page.main.find(".ceo-d-body"));
	ceo_d_refresh(wrapper);
};

frappe.pages["ceo-dashboard"].refresh = function (wrapper) {
	if (wrapper.ceo_d) ceo_d_refresh(wrapper);
};

// if_changed: a Link field fires change on select AND on blur — load once.
function ceo_d_refresh(wrapper, if_changed) {
	const st = wrapper.ceo_d;
	clearTimeout(st.timer);
	st.timer = setTimeout(() => {
		const key = `${st.page.fields_dict.date_range.get_value()}|${st.page.fields_dict.company.get_value()}`;
		if (if_changed && key === st.loaded_key) return;
		st.loaded_key = key;
		ceo_d_load(st);
	}, 300);
}

function ceo_d_load(st) {
	const $root = st.page.main.find(".ceo-d");
	const range = st.page.fields_dict.date_range.get_value() || "This Month";
	const company = st.page.fields_dict.company.get_value();
	$root.addClass("ceo-d-busy").attr("aria-busy", "true");
	frappe.call({
		method: CEO_D_METHOD,
		args: { date_range: range, company },
		callback: (r) => {
			st.data = r.message;
			ceo_d_render(st, $root);
		},
		error: () => {
			const when = st.data ? frappe.datetime.str_to_user(st.data.as_of).split(" ").pop() : null;
			// Nothing loaded yet: drop the pulsing placeholders so it doesn't look like loading.
			if (!st.data) $root.find(".ceo-d-body").empty();
			$root.find(".ceo-d-banners").html(`<div class="ceo-d-banner err" role="alert">${
				when ? __("Could not refresh — showing {0} data.", [when]) : __("The dashboard could not load.")
			} <button class="btn btn-sm btn-default ceo-d-retry">${__("Try again")}</button></div>`);
		},
		always: () => $root.removeClass("ceo-d-busy").attr("aria-busy", "false"),
	});
}

function ceo_d_go(t) {
	if (!t) return;
	if (t.filters) frappe.route_options = t.filters;
	if (t.form) return frappe.set_route("Form", t.dt, t.form);
	if (t.report) return frappe.set_route("query-report", t.report);
	if (t.page) return frappe.set_route(t.page);
	frappe.set_route("List", t.dt);
}

/* ── formatting ─────────────────────────────────────────────────────── */

const ceo_d_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

function ceo_d_inr(v) {
	if (v === null || v === undefined) return "—";
	const a = Math.abs(v), sign = v < 0 ? "-" : "";
	const fmt = (x, u) => `${sign}₹ ${x.toFixed(x < 10 ? 2 : x < 100 ? 1 : 0)} ${u}`;
	if (a >= 99.95e5) return fmt(a / 1e7, "Cr");
	if (a >= 1e5) return fmt(a / 1e5, "L");
	return `${sign}₹ ${format_number(a, null, 0)}`;
}

function ceo_d_wait(h) {
	if (h === null || h === undefined) return "—";
	const mins = Math.round(h * 60), d = Math.floor(mins / 1440), hr = Math.floor((mins % 1440) / 60);
	if (d >= 3) return `${d} d`;
	if (d >= 1) return `${d} d ${hr} h`;
	return hr >= 1 ? `${hr} h` : "< 1 h";
}

const ceo_d_wait_chip = (h) => (h > 24 ? "d" : h > 8 ? "w" : "n");
const ceo_d_num = (v) => (v === null || v === undefined ? "—" : format_number(v, null, 0));
const ceo_d_pos = (n) => (n === 1 ? __("1 PO") : __("{0} POs", [n]));

function ceo_d_dates(s, e) {
	const o = (x) => frappe.datetime.str_to_obj(x);
	const f = (x, y) => x.toLocaleDateString("en-IN", y ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" });
	return s === e ? f(o(s), true) : `${f(o(s))} – ${f(o(e), true)}`;
}

/* ── render ─────────────────────────────────────────────────────────── */

function ceo_d_skeleton($body) {
	const heads = [__("Waiting for me"), __("Purchases"), __("Budget vs actual"), __("Stock"), __("Gate & receiving")];
	$body.html(heads.map((h) => `<section class="ceo-d-sec"><div class="ceo-d-sh"><h2>${h}</h2></div>
		<div class="ceo-d-g3"><div class="ceo-d-skel"></div><div class="ceo-d-skel"></div><div class="ceo-d-skel"></div></div></section>`).join(""));
}

function ceo_d_render(st, $root) {
	const d = st.data;
	st.links = [];
	const link = (t) => `href="#" data-ceo-go="${st.links.push(t) - 1}"`;
	const f = d.filters;
	$root.find(".ceo-d-ctx").text(`${__(f.date_range)} · ${ceo_d_dates(f.start, f.end)} · ${__("Updated")} ${frappe.datetime.str_to_user(d.as_of).split(" ").pop()} · v${d.version}`);
	$root.find(".ceo-d-banners").html(d.confidential_scope === "partial"
		? `<div class="ceo-d-banner info" role="status">${__("Some purchase orders are hidden from your role, so purchase totals are partial.")}</div>` : "");
	// One bad payload must not leave the whole page on placeholders.
	const safe = (id, title, fn) => {
		try {
			return fn();
		} catch (e) {
			console.error(e);
			return ceo_d_section(id, title, "", ceo_d_state(null));
		}
	};
	$root.find(".ceo-d-body").html([
		safe("w", __("Waiting for me"), () => ceo_d_waiting(d.waiting, d.stuck, link)),
		safe("p", __("Purchases"), () => ceo_d_purchases(d.purchases, f, link)),
		safe("b", __("Budget vs actual"), () => ceo_d_budget(d.budget, link)),
		`<div class="ceo-d-two">${safe("s", __("Stock"), () => ceo_d_stock(d.stock, f, link))}${
			safe("t", __("Gate & receiving"), () => ceo_d_plant(d.plant, link))}</div>`,
	].join(""));
}

function ceo_d_section(id, title, caption, body) {
	return `<section class="ceo-d-sec" aria-labelledby="ceo-d-${id}"><div class="ceo-d-sh"><h2 id="ceo-d-${id}">${title}</h2>
		<span class="ceo-d-cap">${caption}</span></div>${body}</section>`;
}

function ceo_d_state(s, unconfigured) {
	if (!s || s.state === "error") {
		return `<div class="ceo-d-card" role="alert">${__("This section could not load.")} <button class="btn btn-sm btn-default ceo-d-retry">${__("Try again")}</button></div>`;
	}
	if (s.state === "restricted") return `<div class="ceo-d-card ceo-d-cap">${__("Not shown for your role.")}</div>`;
	if (s.state === "unconfigured") return `<div class="ceo-d-card dash ceo-d-cap">${unconfigured}</div>`;
	return null;
}

function ceo_d_waiting(w, stuck, link) {
	const cap = __("Live, all companies · age = time since the document last changed");
	const bad = ceo_d_state(w);
	if (bad) return ceo_d_section("w", __("Waiting for me"), cap, bad);
	const tile = (q, label, has_value) => `<a class="ceo-d-card ceo-d-tile" ${link({ dt: q.doctype, filters: q.filters })}
		aria-label="${ceo_d_esc(`${label}: ${q.count}${has_value ? ", " + ceo_d_inr(q.value) : ""}${q.count ? ", oldest " + ceo_d_wait(q.oldest_hours) : ""}`)}">
		<span class="ceo-d-lbl">${label}</span><span class="ceo-d-n">${q.count}</span>
		${has_value ? `<span class="ceo-d-amt">${q.count ? `${ceo_d_inr(q.value)} <span class="ceo-d-cap">${__("order value")}</span>` : "—"}</span>` : ""}
		<span class="ceo-d-meta">${q.count ? `${__("Oldest")} ${ceo_d_wait(q.oldest_hours)} <span class="ceo-d-chip ${ceo_d_wait_chip(q.oldest_hours)}">${
			q.oldest_hours > 24 ? __("Over 24 h") : q.oldest_hours > 8 ? __("Over 8 h") : __("Recent")}</span>` : __("Nothing waiting")}</span></a>`;
	let html = `<div class="ceo-d-g3">${tile(w.overrides, __("Budget overrides"), true)}${tile(w.pos, __("Purchase orders"), true)}${tile(w.mrs, __("Material requests"), false)}</div>`;

	if (stuck && stuck.state === "error") {
		html += `<div class="ceo-d-card ceo-d-cap" role="alert">${__("Could not check for stuck documents.")}</div>`;
	} else if (stuck && stuck.items.length) {
		html += `<div class="ceo-d-stuck" role="status"><span class="ceo-d-stuck-h">${__("Stuck with nobody — these are in no one's approval queue")}${stuck.partial ? " · " + __("partial") : ""}</span>
			${stuck.items.map((s) => `<a ${link({ dt: s.doctype, filters: s.filters })}><span>${ceo_d_esc(s.text)}</span>
				<span class="ceo-d-chip d">${__("oldest {0} d", [Math.round(s.days)])}</span></a>`).join("")}</div>`;
	} else if (stuck) {
		html += `<div class="ceo-d-cap">${__("Nothing is stuck outside an approval queue.")}</div>`;
	}

	if (w.oldest.length) {
		html += `<div class="ceo-d-card" style="padding:8px 16px 4px"><div class="ceo-d-sh" style="padding:8px 4px"><span class="ceo-d-b">${__("Waiting longest")}</span>
			<span class="ceo-d-cap">${__("{0} oldest of {1}", [w.oldest.length, w.total])}</span></div>
			${w.oldest.map((r) => `<a class="ceo-d-row" ${link({ dt: r.doctype, form: r.name })}>
				<span class="ceo-d-b">${ceo_d_esc(r.name)}</span><span class="ceo-d-hs ceo-d-cap">${__(r.kind)}</span>
				<span class="ceo-d-hs">${ceo_d_esc(r.party)}</span><span class="ceo-d-r ceo-d-hs">${r.value === null ? "—" : ceo_d_inr(r.value)}</span>
				<span class="ceo-d-r"><span class="ceo-d-chip ${ceo_d_wait_chip(r.hours)}">${ceo_d_wait(r.hours)}</span></span></a>`).join("")}</div>`;
	}
	return ceo_d_section("w", __("Waiting for me"), cap, html);
}

function ceo_d_purchases(p, f, link) {
	const cap = __("{0} vs the same days of the previous period · approved POs", [__(f.date_range)]);
	const bad = ceo_d_state(p);
	if (bad) return ceo_d_section("p", __("Purchases"), cap, bad);
	const delta = p.previous ? Math.round(((p.current - p.previous) / p.previous) * 100) : null;
	const po_list = (extra) => ({ dt: "Purchase Order", filters: Object.assign({ docstatus: 1, company: f.company, transaction_date: ["between", [f.start, f.end]] }, extra) });
	const cats = p.by_category.map((c) => {
		const pct = p.current ? Math.round((c.value / p.current) * 100) : 0;
		const cpct = p.count ? Math.round((c.count / p.count) * 100) : 0;
		const extra = c.category === "Other" ? { ts_purchase_category: ["is", "not set"] } : { ts_purchase_category: c.category };
		return `<a class="ceo-d-cat" ${link(po_list(extra))}>
			<span class="ceo-d-sw" style="background:var(${CEO_D_CATS[c.category] || "--gray-400"})"></span>
			<span><span class="ceo-d-b">${ceo_d_esc(__(c.category))}</span> <span class="ceo-d-cap">· ${ceo_d_pos(c.count)} ${__("({0}% of count)", [cpct])}</span></span>
			<span class="ceo-d-r ceo-d-b">${ceo_d_inr(c.value)}</span>
			<span class="ceo-d-track"><span class="ceo-d-fill" style="width:${pct}%;background:var(${CEO_D_CATS[c.category] || "--gray-400"})"></span></span></a>`;
	}).join("");
	const max = Math.max(...p.trend.map((m) => m.total), 1);
	const order = ["Grain", "Coal", "Store", "Capex", "Other"];
	const chart = `<div class="ceo-d-chart" role="img" aria-label="${__("Monthly approved PO value, last 6 months, stacked by category")}">
		${p.trend.map((m) => `<div class="ceo-d-col"><span class="ceo-d-colv">${m.total ? ceo_d_inr(m.total) : "—"}</span>
			<div class="ceo-d-stack" style="height:${m.total > 0 ? Math.max(3, Math.round((m.total / max) * 190)) : 0}px">${order.filter((k) => m.by[k]).map((k) =>
				`<span style="display:block;height:${Math.max(2, Math.round((m.by[k] / max) * 190))}px;background:var(${CEO_D_CATS[k]})"></span>`).join("")}</div></div>`).join("")}</div>
		<div class="ceo-d-xl">${p.trend.map((m) => `<span>${ceo_d_esc(m.label)}</span>`).join("")}</div>
		<table class="ceo-d-sr"><tbody>${p.trend.map((m) => `<tr><th>${ceo_d_esc(m.label)}</th><td>${ceo_d_inr(m.total)}</td></tr>`).join("")}</tbody></table>
		<div class="ceo-d-legend">${order.map((k) => `<span><span class="ceo-d-sw" style="background:var(${CEO_D_CATS[k]})"></span>${__(k)}</span>`).join("")}</div>`;
	const sup = p.top_suppliers.length ? p.top_suppliers.map((s, i) => `<a class="ceo-d-srow" ${link(po_list({ supplier: s.supplier }))}>
		<span class="ceo-d-cap">${i + 1}</span><span><span class="ceo-d-b">${ceo_d_esc(s.supplier_name)}</span> <span class="ceo-d-cap">· ${ceo_d_esc(__(s.category || "Other"))}</span></span>
		<span class="ceo-d-cap ceo-d-hs">${ceo_d_pos(s.count)}</span><span class="ceo-d-r ceo-d-b">${ceo_d_inr(s.value)}</span></a>`).join("")
		: `<div class="ceo-d-cap" style="padding:8px 4px">${__("No approved POs in this period.")}</div>`;
	return ceo_d_section("p", __("Purchases"), cap, `<div class="ceo-d-purch">
		<div class="ceo-d-card" style="display:flex;flex-direction:column;gap:12px">
			<div style="display:flex;flex-direction:column;gap:4px"><span class="ceo-d-lbl">${__("PO value")}</span>
			<span class="ceo-d-big">${ceo_d_inr(p.current)}</span>
			<span class="ceo-d-cap" style="font-size:13px">${delta === null ? __("Nothing in the previous period to compare") :
				`${delta >= 0 ? "▲" : "▼"} ${Math.abs(delta) > 999 ? ">999" : Math.abs(delta)}% ${__("vs {0} ({1})", [ceo_d_dates(f.prev_start, f.prev_end), ceo_d_inr(p.previous)])}`} · ${ceo_d_pos(p.count)}</span></div>
			<div>${cats || `<div class="ceo-d-cap">${__("No approved POs in this period.")}</div>`}</div></div>
		<div class="ceo-d-card"><div class="ceo-d-sh" style="margin-bottom:8px"><span class="ceo-d-b">${__("Last 6 months")}</span>
			<span class="ceo-d-cap">${__("this month to date")}</span></div>${chart}</div></div>
		<div class="ceo-d-card" style="padding:8px 16px 4px"><div class="ceo-d-sh" style="padding:8px 4px"><span class="ceo-d-b">${__("Top suppliers")}</span>
			<span class="ceo-d-cap">${__("by approved PO value in this period")}</span></div>${sup}</div>`);
}

function ceo_d_budget(b, link) {
	const cap = b && b.fiscal_year ? __("Fiscal year {0} · expense accounts only · same figures as the TS Budget Dashboard report", [ceo_d_esc(b.fiscal_year)]) : "";
	const bad = ceo_d_state(b);
	if (bad) return ceo_d_section("b", __("Budget vs actual"), cap, bad);
	if (!b.rows.length) return ceo_d_section("b", __("Budget vs actual"), cap, `<div class="ceo-d-card ceo-d-cap">${__("No submitted budgets for this fiscal year.")}</div>`);
	const s = b.summary;
	const rows = b.rows.map((r) => `<a class="ceo-d-brow" ${link({ report: "TS Budget Dashboard", filters: Object.assign({ fiscal_year: b.fiscal_year, company: b.company }, r.cost_center ? { cost_center: r.cost_center } : {}) })}>
		<span class="ceo-d-brow-h"><span class="ceo-d-b">${ceo_d_esc(r.label)}</span><span class="ceo-d-r">${r.pct}%</span></span>
		<span class="ceo-d-track"><span class="ceo-d-fill ${r.pct > 100 ? "over" : ""}" style="width:${Math.min(Math.max(r.pct, 0), 100)}%"></span></span>
		<span class="ceo-d-meta">${__("{0} of {1}", [ceo_d_inr(r.used), ceo_d_inr(r.budget)])}${r.pct > 100 ? ` <span class="ceo-d-chip d">${__("Over budget · {0} over", [ceo_d_inr(r.used - r.budget)])}</span>` : ""}</span></a>`).join("");
	return ceo_d_section("b", __("Budget vs actual"), cap, `<div class="ceo-d-card" style="display:flex;flex-direction:column;gap:12px">
		<div class="ceo-d-sum">
			<div><div class="ceo-d-lbl">${__("Budget")}</div><div class="ceo-d-amt" style="font-size:20px">${ceo_d_inr(s.total_budget)}</div></div>
			<div><div class="ceo-d-lbl">${__("Spent + committed")}</div><div class="ceo-d-amt" style="font-size:20px">${ceo_d_inr(s.total_committed + s.total_actual)} · ${s.overall_utilization}%</div></div>
			<div><div class="ceo-d-lbl">${__("Over budget")}</div><div class="ceo-d-amt" style="font-size:20px${b.over_count ? ";color:var(--alert-text-danger)" : ""}">${
				b.over_count ? __("{0} cost centres", [b.over_count]) : __("None")}</div></div></div>
		<div class="ceo-d-bud">${rows}</div>${b.total_rows > b.rows.length ? `<div class="ceo-d-cap">${__("Showing {0} of {1} cost centres — open the TS Budget Dashboard report for all", [b.rows.length, b.total_rows])}</div>` : ""}</div>`);
}

function ceo_d_stock(s, f, link) {
	const cap = __("As of now · each item in its own unit");
	if (!s || s.state !== "ok") return ceo_d_section("s", __("Stock"), cap, ceo_d_state(s, __("Not configured.")) || ceo_d_state(null));
	const p = s.products;
	const prod = p.state !== "ok"
		? `<div class="ceo-d-card dash" style="min-height:88px"><div class="ceo-d-lbl">${__("Products")}</div><div class="ceo-d-cap" style="font-size:13px">${__("Product item list is not configured (TS Settings → Ethanol items).")}</div></div>`
		: p.items.map((i) => `<a class="ceo-d-card ceo-d-tile" style="min-height:88px" ${link({ report: "Stock Balance", filters: { company: f.company, item_code: i.item_code } })}>
			<span class="ceo-d-lbl">${ceo_d_esc(i.item_name)}</span><span class="ceo-d-n sm">${format_number(i.qty, null, i.qty % 1 ? 2 : 0)}</span>
			<span class="ceo-d-cap">${ceo_d_esc(i.uom)}</span></a>`).join("");
	return ceo_d_section("s", __("Stock"), cap, `<div class="ceo-d-stock">${prod}
		<div class="ceo-d-card dash" style="min-height:88px"><div class="ceo-d-lbl">${__("Grain stock")}</div><div class="ceo-d-cap" style="font-size:13px">${__("Grain item list is not configured yet.")}</div></div></div>`);
}

function ceo_d_plant(p, link) {
	const cap = __("Live · open gate records — a vehicle may have left without an exit entry");
	const bad = ceo_d_state(p);
	if (bad) return ceo_d_section("t", __("Gate & receiving"), cap, bad);
	const tile = (label, n, sub, t) => `<a class="ceo-d-card ceo-d-tile" style="min-height:88px" ${link(t)}>
		<span class="ceo-d-lbl">${label}</span><span class="ceo-d-n sm">${ceo_d_num(n)}</span><span class="ceo-d-meta">${sub}</span></a>`;
	return ceo_d_section("t", __("Gate & receiving"), cap, `<div class="ceo-d-g3" style="gap:12px">
		${tile(__("Arrived in last 24 h"), p.arrived_24h, __("trucks not yet exited"), { dt: "TS Token", filters: {
			entry_type: "Material", g2_link_time: [">=", p.arrived_since], status: ["not in", p.arrived_exclude] } })}
		${tile(__("Grain awaiting PO link"), p.grain_waiting, p.grain_over_6h ? `<span class="ceo-d-chip w">${__("{0} over 6 h", [p.grain_over_6h])}</span>` : __("none over 6 h"), { page: "stores-receiving" })}
		${tile(__("Left owing a receipt"), p.receipt_owed, p.receipt_owed === null ? "" : __("Grain {0} · Non-RM {1}", [p.receipt_owed_grain, p.receipt_owed_non_rm]), { page: "stores-receiving" })}
	</div>`);
}
