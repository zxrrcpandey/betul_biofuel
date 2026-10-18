// MD Dashboard (v2.65.0) — data from ts_dashboard_md.get_md_overview. The look
// (md_dashboard.css) is unchanged; the content is the MD's own: the approval
// queue, what the MD signed off, budget, grain, companies and purchases.
// The older get_md_dashboard method still feeds the MD Wall Display; leave it.

const MD_METHOD = "trustbit_ethanol.ts_gate_entry.ts_dashboard_md.get_md_overview";
const MD_CATS = { Grain: "#3b82f6", Coal: "#64748b", Store: "#8b5cf6", Capex: "#ec4899", Other: "#94a3b8" };
const MD_CAT_ORDER = ["Grain", "Coal", "Store", "Capex", "Other"];

frappe.pages["md-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "MD Dashboard",
		single_column: true,
	});

	// append, not html: page.main also holds the Period / Company filter bar.
	page.main.append('<div id="md-app" style="padding: 15px;"></div>');

	page.add_button(__("Wall Display"), () => {
		frappe.set_route("md-wall-display");
	}, { icon: "monitor" }).addClass("btn-dark");

	page.add_field({
		fieldname: "date_range",
		label: __("Period"),
		fieldtype: "Select",
		options: "Today\nThis Week\nThis Month\nThis Quarter\nThis Year",
		default: "This Month",
		change: () => _md_refresh(page, true),
	});
	page.add_field({
		fieldname: "company",
		label: __("Company"),
		fieldtype: "Link",
		options: "Company",
		default: frappe.defaults.get_global_default("company"),
		change: () => _md_refresh(page, true),
	});

	page._md = { data: null, links: [], timer: null, loaded_key: null };
	page.main.on("click keydown", "[data-md-go]", (e) => {
		if (e.type === "keydown" && e.key !== "Enter") return;
		e.preventDefault();
		_md_go(page._md.links[+e.currentTarget.dataset.mdGo]);
	});
	page.main.on("click", ".md-retry", () => _md_refresh(page));
	page.main.on("click", ".md-nbi-btn", (e) => {
		const $card = $(e.currentTarget).closest(".md-nbi");
		const open = !$card.hasClass("is-open");
		$card.toggleClass("is-open", open).find(".md-nbi-body").prop("hidden", !open);
		$(e.currentTarget).html(open ? `${__("Hide")} &#9650;` : `${__("Show")} &#9660;`);
	});

	page._unlocked = sessionStorage.getItem("md_dash_unlocked") === "1";
	setTimeout(() => _md_check_pin(page), 300);
};

frappe.pages["md-dashboard"].refresh = function (wrapper) {
	if (wrapper.page && wrapper.page._unlocked) _md_refresh(wrapper.page);
};

function _md_check_pin(page) {
	if (page._unlocked) { _md_refresh(page); return; }
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_dashboard_md.check_md_pin_required",
		callback(r) {
			if (r.message && r.message.pin_required) {
				_md_show_pin_screen(page);
			} else {
				page._unlocked = true;
				sessionStorage.setItem("md_dash_unlocked", "1");
				_md_refresh(page);
			}
		},
	});
}

function _md_show_pin_screen(page) {
	$("#md-app").html(`<div class="md-msg" style="color:#64748b;">${__("Dashboard locked. Enter PIN to continue.")}</div>`);
	frappe.prompt(
		[{ label: "Enter PIN", fieldname: "pin", fieldtype: "Password", reqd: 1 }],
		function(values) {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.ts_dashboard_md.verify_md_pin",
				args: { pin: values.pin },
				callback(r) {
					if (r.message && r.message.valid) {
						sessionStorage.setItem("md_dash_unlocked", "1");
						page._unlocked = true;
						_md_refresh(page);
					} else {
						frappe.msgprint({ title: "Access Denied", message: "Incorrect PIN. Please try again.", indicator: "red" });
						setTimeout(() => _md_show_pin_screen(page), 500);
					}
				},
			});
		},
		"MD Dashboard — PIN Required",
		"Unlock"
	);
}

// if_changed: a Link field fires change on select AND on blur — load once.
function _md_refresh(page, if_changed) {
	const st = page._md;
	clearTimeout(st.timer);
	st.timer = setTimeout(() => {
		if (!page._unlocked) return;
		const key = `${page.fields_dict.date_range.get_value()}|${page.fields_dict.company.get_value()}`;
		if (if_changed && key === st.loaded_key) return;
		st.loaded_key = key;
		_md_load(page);
	}, 300);
}

function _md_load(page) {
	const st = page._md;
	const $c = page.main.find("#md-app");
	if (!st.data) $c.html(`<div class="md-msg">${__("Loading MD Dashboard...")}</div>`);
	const seq = (st.seq = (st.seq || 0) + 1); // only the latest request may draw
	$c.addClass("md-busy");
	frappe.call({
		method: MD_METHOD,
		args: {
			date_range: page.fields_dict.date_range.get_value() || "This Month",
			company: page.fields_dict.company.get_value(),
		},
		always() {
			if (seq === st.seq) $c.removeClass("md-busy");
		},
		callback(r) {
			if (seq !== st.seq) return;
			st.data = r.message;
			_md_render(page, $c);
		},
		error() {
			if (seq !== st.seq) return;
			const msg = st.data ? __("Could not refresh — showing the last loaded data.") : __("The dashboard could not load.");
			const html = `<div class="md-section md-load-err" role="alert" style="color:#ef4444;">${msg}
				<button class="btn btn-xs btn-default md-retry">${__("Try again")}</button></div>`;
			$c.find(".md-load-err").remove();
			if (st.data) $c.prepend(html);
			else $c.html(html);
		},
	});
}

function _md_go(t) {
	if (!t) return;
	if (t.filters) frappe.route_options = t.filters;
	if (t.form) return frappe.set_route("Form", t.dt, t.form);
	if (t.report) return frappe.set_route("query-report", t.report);
	if (t.page) return frappe.set_route(t.page);
	frappe.set_route("List", t.dt);
}

/* ══════════════════════════════════════════════════════════════════════
   MAIN RENDER
   ══════════════════════════════════════════════════════════════════════ */

function _md_render(page, $c) {
	const d = page._md.data, f = d.filters;
	page._md.links = [];
	const link = (t) => `data-md-go="${page._md.links.push(t) - 1}" role="link" tabindex="0"`;
	// One bad payload must not blank the whole page.
	const safe = (title, fn) => {
		try {
			return fn();
		} catch (e) {
			console.error(e);
			return _md_section(title, "", _md_state(null));
		}
	};
	const is_md = frappe.user_roles.includes("MD");

	let html = `<div class="md-hero md-animate">
		<div>
			<div class="md-hero-title">Betul Bio Fuel Pvt Ltd</div>
			<div class="md-hero-sub">${__("Managing Director's Executive Dashboard")}</div>
		</div>
		<div class="md-hero-right">
			<div class="md-hero-date">${_md_esc(__(f.date_range))} &middot; ${_md_dates(f.start, f.end)}</div>
			<div class="md-hero-time md-clock"></div>
		</div>
	</div>`;
	if (d.confidential_scope === "partial") {
		html += `<div class="md-section" role="status" style="padding:10px 16px;color:#1e40af;background:#eff6ff;font-size:12px;">
			${__("Some purchase orders are hidden from your role, so purchase totals are partial.")}</div>`;
	}
	html += safe(__("Summary"), () => _md_kpis(d, link, is_md));
	html += safe(__("Waiting for my approval"), () => _md_waiting(d.waiting, link, is_md));
	html += safe(__("Companies"), () => _md_companies(d.companies, f, link));
	html += safe(__("Purchases by category"), () => _md_purchases(d.purchases, f, link));
	html += `<div class="md-grid-2 md-animate md-animate-d3">${safe(__("Budget Health"), () => _md_budget(d.budget, link))}${
		safe(__("Top 5 Suppliers"), () => _md_suppliers(d.purchases, f, link))}</div>`;
	html += safe(__("Gate & receiving"), () => _md_gate(d.plant, link));
	html += safe(__("Non-Branded Items"), () => _md_brandless(d.brandless, link));
	html += `<div class="md-footer">${__("Updated")} ${frappe.datetime.str_to_user(d.as_of).split(" ").pop()} &middot; v${_md_esc(d.version)}</div>`;

	$c.html(html);
	_md_trend_chart(d.purchases);
	_md_start_clock();
}

function _md_section(title, badge, body, style) {
	return `<div class="md-section md-animate md-animate-d3"${style ? ` style="${style}"` : ""}>
		<div class="md-section-head"><div class="md-section-title">${title}</div>${badge || ""}</div>${body}</div>`;
}

function _md_state(s) {
	if (s && s.state === "restricted") {
		return `<div class="md-t-faint" style="padding:16px;text-align:center;">${__("Not shown for your role.")}</div>`;
	}
	return `<div style="padding:16px;text-align:center;color:#ef4444;">${__("This section could not load.")}
		<button class="btn btn-xs btn-default md-retry">${__("Try again")}</button></div>`;
}

/* ══════════════════════════════════════════════════════════════════════
   KPI CARDS
   ══════════════════════════════════════════════════════════════════════ */

function _md_kpi(o) {
	return `<div class="md-kpi"${o.go ? " " + o.go : ""}>
		<div class="md-kpi-accent" style="background:${o.color};"></div>
		<div class="md-kpi-icon" style="background:${o.color}15;color:${o.color};">${o.icon}</div>
		<div class="md-kpi-label">${o.label}</div>
		<div class="md-kpi-value" style="color:${o.color};">${o.value}${o.unit ? `<span class="md-kpi-unit">${o.unit}</span>` : ""}</div>
		<div class="md-kpi-sub">${o.sub}</div>
	</div>`;
}

function _md_kpis(d, link, is_md) {
	const ok = (s) => s && s.state === "ok";
	const failed = (s) => (s && s.state === "restricted" ? __("Not shown for your role") : __("Could not load"));
	const w = d.waiting, a = d.approved, b = d.budget, g = d.grain, p = d.plant;
	const cards = [];

	if (ok(w)) {
		const q = [w.pos, w.mrs, w.post_dated].find((x) => x && x.count) || w.pos;
		const only_pos = w.total === w.pos.count;
		cards.push(_md_kpi({
			label: is_md ? __("Waiting for my approval") : __("Waiting for MD approval"), icon: "&#9888;",
			value: w.total, unit: only_pos && w.total ? (w.total === 1 ? __("PO") : __("POs")) : "",
			sub: w.total ? `${w.pos.count ? _md_inr(w.pos.value) + " &middot; " : ""}${__("oldest")} <b style="color:#ef4444;">${_md_wait(w.oldest[0].hours)}</b>`
				: __("Nothing waiting"),
			color: w.total ? "#ef4444" : "#10b981", go: link({ dt: q.doctype, filters: q.filters }),
		}));
	} else {
		cards.push(_md_kpi({ label: __("Waiting for my approval"), icon: "&#9888;", value: "—", sub: failed(w), color: "#9ca3af" }));
	}

	cards.push(_md_kpi(ok(a)
		? { label: __("Approved by MD"), icon: "&#10003;", value: a.count, unit: a.count === 1 ? __("PO") : __("POs"),
			sub: `${a.count ? `${_md_inr(a.value)} &middot; ${_md_esc(__(d.filters.date_range).toLowerCase())}` : __("None in this period")} &middot; ${__("all companies")}`, color: "#10b981" }
		: { label: __("Approved by MD"), icon: "&#10003;", value: "—", sub: failed(a), color: "#9ca3af" }));

	if (ok(b) && b.rows.length) {
		const pct = b.summary.overall_utilization || 0;
		cards.push(_md_kpi({
			label: __("Budget used"), icon: "&#8377;", value: `${_md_pct(pct)}%`,
			sub: b.over_count ? `<b style="color:#ef4444;">${b.over_count}</b> ${b.over_count === 1 ? __("cost centre over budget") : __("cost centres over budget")}`
				: __("All cost centres within budget"),
			color: _md_pct_color(pct), go: link({ report: "TS Budget Dashboard", filters: { fiscal_year: b.fiscal_year, company: b.company } }),
		}));
	} else {
		cards.push(_md_kpi({ label: __("Budget used"), icon: "&#8377;", value: "—",
			sub: ok(b) ? __("No budget for this fiscal year") : failed(b), color: "#9ca3af" }));
	}

	if (ok(g)) {
		const delta = g.prev_mt ? Math.round(((g.mt - g.prev_mt) / g.prev_mt) * 100) : null;
		cards.push(_md_kpi({
			label: __("Grain received"), icon: "&#9878;", value: format_number(g.mt, null, g.mt % 1 ? 1 : 0), unit: "MT",
			sub: `${g.loads === 1 ? __("1 load") : __("{0} loads", [g.loads])}${delta === null ? "" : ` &middot; ${_md_delta(delta)} ${__("vs last period")}`} &middot; ${__("all companies")}`,
			color: "#3b82f6",
		}));
	} else {
		cards.push(_md_kpi({ label: __("Grain received"), icon: "&#9878;", value: "—", sub: failed(g), color: "#9ca3af" }));
	}

	if (ok(p) && p.grain_waiting !== null) {
		cards.push(_md_kpi({
			label: __("Grain awaiting PO link"), icon: "&#9202;", value: p.grain_waiting, unit: __("loads"),
			sub: p.grain_over_6h ? `<b style="color:#ef4444;">${p.grain_over_6h}</b> ${__("waiting over 6 h")}` : __("None waiting over 6 h"),
			color: p.grain_waiting ? "#f59e0b" : "#10b981", go: link({ page: "stores-receiving" }),
		}));
	} else {
		cards.push(_md_kpi({ label: __("Grain awaiting PO link"), icon: "&#9202;", value: "—", sub: failed(p), color: "#9ca3af" }));
	}
	return `<div class="md-kpi-grid md-animate md-animate-d1">${cards.join("")}</div>`;
}

/* ══════════════════════════════════════════════════════════════════════
   WAITING FOR MD APPROVAL
   ══════════════════════════════════════════════════════════════════════ */

function _md_waiting(w, link, is_md) {
	const title = is_md ? __("Waiting for my approval") : __("Waiting for MD approval");
	if (!w || w.state !== "ok") return _md_section(title, "", _md_state(w));
	if (!w.total) {
		return `<div class="md-section md-animate md-animate-d2" style="border-left:4px solid #10b981;">
			<div class="md-section-head"><div class="md-section-title">${title}</div>
			<span class="md-section-badge" style="background:#ecfdf5;color:#10b981;">${__("All clear")}</span></div>
			<div style="padding:8px;text-align:center;color:#10b981;">${__("Nothing is waiting for MD approval.")}</div></div>`;
	}
	const badge = `${__("{0} pending", [w.total])}${w.pos.count ? " &middot; " + _md_inr(w.pos.value) : ""}`;
	let html = `<table class="md-action-table"><thead><tr>
		<th class="md-hide-phone">${__("Document")}</th><th>${__("Supplier / details")}</th>
		<th style="text-align:right;">${__("Amount")}</th><th style="text-align:right;">${__("Waiting")}</th></tr></thead><tbody>`;
	w.oldest.forEach((r) => {
		const wc = r.hours > 24 ? "#ef4444" : r.hours > 8 ? "#f59e0b" : "#6b7280";
		const kind = r.doctype === "Purchase Order" ? "" : `<span class="md-chip">${_md_esc(__(r.kind))}</span>`;
		html += `<tr ${link({ dt: r.doctype, form: r.name })}>
			<td class="md-hide-phone"><span style="color:#3b82f6;font-weight:600;">${_md_esc(r.name)}</span></td>
			<td>${_md_esc(r.party)}${kind}<div class="md-only-phone md-row-meta">${_md_esc(r.name)}</div></td>
			<td style="text-align:right;font-weight:700;">${r.value === null ? "—" : _md_inr(r.value)}</td>
			<td style="text-align:right;color:${wc};font-weight:600;">${_md_wait(r.hours)}</td></tr>`;
	});
	html += "</tbody></table>";
	if (w.total > w.oldest.length) {
		html += `<div class="md-more-row">${[w.pos, w.mrs, w.post_dated].filter((q) => q && q.count).map((q) =>
			`<a class="md-link" ${link({ dt: q.doctype, filters: q.filters })}>${__("Open all {0} in {1}", [q.count, __(q.doctype)])} &rarr;</a>`).join(" &middot; ")}</div>`;
	}
	html += `<div class="md-fnote">${__("Live, all companies · oldest first · waiting = time since the document last changed")}</div>`;
	return `<div class="md-section md-animate md-animate-d2" style="border-left:4px solid #ef4444;">
		<div class="md-section-head"><div class="md-section-title">${title}</div>
		<span class="md-section-badge" style="background:#fef2f2;color:#ef4444;">${badge}</span></div>${html}</div>`;
}

/* ══════════════════════════════════════════════════════════════════════
   COMPANY CARDS
   ══════════════════════════════════════════════════════════════════════ */

function _md_companies(c, f, link) {
	if (!c || c.state !== "ok") return _md_section(__("Companies"), "", _md_state(c));
	if (!c.rows.length) return "";
	const one = c.rows.length === 1 ? ' style="grid-template-columns:1fr;"' : "";
	return `<div class="md-company-grid md-animate md-animate-d2"${one}>${c.rows.map((r) => _md_company_card(r, f, link)).join("")}</div>`;
}

function _md_company_card(c, f, link) {
	const bc = _md_pct_color(c.budget_pct);
	let budget;
	if (c.budget_state !== "ok") {
		budget = `<div class="md-fnote">${c.budget_state === "restricted" ? __("Budget not shown for your role.") : __("Budget could not load.")}</div>`;
	} else if (!c.budget_total) {
		budget = `<div class="md-fnote">${__("No budget for this fiscal year.")}</div>`;
	} else {
		budget = `<div style="margin-top:12px;" ${link({ report: "TS Budget Dashboard", filters: { fiscal_year: c.fiscal_year, company: c.company } })}>
			<div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;">
				<span style="color:#6b7280;">${__("Budget")}</span>
				<span style="color:${bc};font-weight:700;">${__("{0}% used", [_md_pct(c.budget_pct)])}</span>
			</div>
			<div class="md-budget-bar"><div class="md-budget-fill" style="width:${Math.min(Math.max(c.budget_pct, 0), 100)}%;background:${bc};"></div></div>
			<div style="font-size:10px;color:#6b7280;margin-top:4px;">${__("{0} of {1}", [_md_inr(c.budget_used), _md_inr(c.budget_total)])}</div>
		</div>`;
	}
	const pos = link({ dt: "Purchase Order", filters: { docstatus: 1, company: c.company, transaction_date: ["between", [f.start, f.end]] } });
	return `<div class="md-company-card">
		<div class="md-company-name">${_md_esc(c.abbr)} <span class="md-co-full">${_md_esc(c.company)}</span></div>
		<div class="md-co-stats">
			<div class="md-co-stat" ${pos}>
				<div class="md-co-stat-val" style="color:#10b981;">${_md_inr(c.po_value)}</div>
				<div class="md-co-stat-label">${__("PO Value")}</div>
			</div>
			<div class="md-co-stat">
				<div class="md-co-stat-val" style="color:#3b82f6;">${_md_inr(c.pi_value)}</div>
				<div class="md-co-stat-label">${__("Invoice Value")}</div>
			</div>
			<div class="md-co-stat">
				<div class="md-co-stat-val" style="color:#8b5cf6;">${c.pr_count}</div>
				<div class="md-co-stat-label">${__("GRNs")}</div>
			</div>
		</div>${budget}
	</div>`;
}

/* ══════════════════════════════════════════════════════════════════════
   PURCHASES BY CATEGORY + 6-MONTH TREND
   ══════════════════════════════════════════════════════════════════════ */

function _md_purchases(p, f, link) {
	const title = __("Purchases by category");
	if (!p || p.state !== "ok") return _md_section(title, "", _md_state(p));
	const delta = p.previous ? Math.round(((p.current - p.previous) / p.previous) * 100) : null;
	const po_list = (extra) => ({ dt: "Purchase Order", filters: Object.assign({ docstatus: 1, company: f.company, transaction_date: ["between", [f.start, f.end]] }, extra) });
	const cats = p.by_category.map((c) => {
		const share = p.current ? (c.value / p.current) * 100 : 0;
		const prev = (p.previous_by_category || {})[c.category] || 0;
		const col = MD_CATS[c.category] || MD_CATS.Other;
		const extra = c.category === "Other" ? { ts_purchase_category: ["is", "not set"] } : { ts_purchase_category: c.category };
		return `<div class="md-cat-row" ${link(po_list(extra))}>
			<div class="md-cat-top"><span class="md-cat-dot" style="background:${col};"></span><span class="md-cat-name">${_md_esc(__(c.category))}</span>
				<span class="md-cat-meta md-t-faint">${_md_pos(c.count)} &middot; ${Math.round(share)}%${prev ? " &middot; " + _md_delta(Math.round(((c.value - prev) / prev) * 100)) : ""}</span>
				<span class="md-cat-val">${_md_inr(c.value)}</span></div>
			<div class="md-budget-bar"><div class="md-budget-fill" style="width:${share}%;background:${col};"></div></div></div>`;
	}).join("");
	return `<div class="md-section md-animate md-animate-d3">
		<div class="md-section-head"><div class="md-section-title">${title}</div>
			<span class="md-section-badge" style="background:#eff6ff;color:#3b82f6;">${_md_dates(f.start, f.end)} ${__("vs")} ${_md_dates(f.prev_start, f.prev_end)}</span></div>
		<div class="md-grid-2" style="margin-bottom:0;">
			<div>
				<div class="md-pc-head">
					<span class="md-pc-val">${_md_inr(p.current)}</span>
					<span class="md-t-muted">${__("from {0}", [_md_pos(p.count)])}</span>
					${delta === null ? "" : `<span class="md-delta">${_md_delta(delta)}</span>`}
					<span class="md-t-muted">${p.previous ? __("vs {0} before", [_md_inr(p.previous)]) : __("nothing in the previous period")}</span>
				</div>
				${cats || `<div class="md-t-faint" style="padding:12px 0;">${__("No approved POs in this period.")}</div>`}
			</div>
			<div>
				<div class="md-sub-head md-t-muted">${__("Last 6 months · approved PO value, ₹ lakh")}</div>
				<div id="md-trend-chart"></div>
			</div>
		</div>
	</div>`;
}

function _md_trend_chart(p) {
	const el = document.getElementById("md-trend-chart");
	if (!el || !p || p.state !== "ok") return;
	const rank = (k) => (MD_CAT_ORDER.includes(k) ? MD_CAT_ORDER.indexOf(k) : 99);
	const keys = [...new Set(p.trend.flatMap((m) => Object.keys(m.by)))].sort((x, y) => rank(x) - rank(y) || x.localeCompare(y));
	if (!keys.length) {
		el.innerHTML = `<div class="md-t-faint" style="padding:40px;text-align:center;">${__("No approved POs in the last 6 months.")}</div>`;
		return;
	}
	new frappe.Chart(el, {
		data: {
			labels: p.trend.map((m) => m.label),
			datasets: keys.map((k) => ({ name: _md_esc(__(k)), values: p.trend.map((m) => Math.round((m.by[k] || 0) / 1e4) / 10) })),
		},
		type: "bar",
		height: 240,
		colors: keys.map((k) => MD_CATS[k] || MD_CATS.Other),
		barOptions: { stacked: 1, spaceRatio: 0.4 },
		showLegend: 0, // the chart's own legend clips its third row on a phone
		tooltipOptions: { formatTooltipY: (v) => "₹ " + format_number(v, null, 1) + " L" },
	});
	el.insertAdjacentHTML("afterend", `<div class="md-legend">${keys.map((k) =>
		`<span><i style="background:${MD_CATS[k] || MD_CATS.Other};"></i>${_md_esc(__(k))}</span>`).join("")}</div>`);
}

/* ══════════════════════════════════════════════════════════════════════
   BUDGET HEALTH
   ══════════════════════════════════════════════════════════════════════ */

function _md_budget(b, link) {
	const title = __("Budget Health");
	const badge = b && b.fiscal_year ? `<span class="md-section-badge" style="background:#f1f5f9;color:#475569;">FY ${_md_esc(b.fiscal_year)}</span>` : "";
	if (!b || b.state !== "ok") return _md_section(title, badge, _md_state(b), "margin-bottom:0;");
	if (!b.rows.length) {
		return _md_section(title, badge, `<div class="md-t-faint" style="padding:20px;text-align:center;">${__("No submitted budgets for this fiscal year.")}</div>`, "margin-bottom:0;");
	}
	const s = b.summary, pct = s.overall_utilization || 0, tc = _md_pct_color(pct);
	const w = (v) => (s.total_budget ? Math.max(0, Math.min(100, (v / s.total_budget) * 100)) : 0);
	const report = (cc) => link({ report: "TS Budget Dashboard", filters: Object.assign({ fiscal_year: b.fiscal_year, company: b.company }, cc ? { cost_center: cc } : {}) });
	const over = b.rows.filter((r) => r.pct > 100);
	const list = (over.length ? over : b.rows).slice(0, 6);
	let html = `<div style="display:flex;gap:10px;margin-bottom:12px;">
		<div class="md-tile" style="flex:1;padding:10px;text-align:center;">
			<div style="font-size:10px;color:#6b7280;text-transform:uppercase;">${__("Total Budget")}</div>
			<div style="font-size:16px;font-weight:700;color:#374151;">${_md_inr(s.total_budget)}</div>
		</div>
		<div style="flex:1;padding:10px;background:${tc}08;border-radius:8px;text-align:center;">
			<div style="font-size:10px;color:#6b7280;text-transform:uppercase;">${__("Utilization")}</div>
			<div style="font-size:16px;font-weight:700;color:${tc};">${_md_pct(pct)}%</div>
		</div>
	</div>
	<div class="md-budget-bar md-stackbar"><div style="width:${w(s.total_actual)}%;background:${tc};"></div><div style="width:${w(s.total_committed)}%;background:${tc}66;"></div></div>
	<div class="md-fnote" style="margin-bottom:14px;">${__("Spent {0} · committed {1} · {2}", [_md_inr(s.total_actual), _md_inr(s.total_committed),
		s.total_available >= 0 ? __("{0} left", [_md_inr(s.total_available)]) : __("{0} over", [_md_inr(-s.total_available)])])}</div>
	<div class="md-sub-head md-t-muted">${over.length ? __("Over budget · {0} cost centres", [over.length]) : __("Most used cost centres")}</div>`;
	list.forEach((r) => {
		const rc = _md_pct_color(r.pct);
		html += `<div class="md-over-row md-tile" ${report(r.cost_center)}>
			<div class="md-over-top"><span class="md-t-strong">${_md_esc(r.label)}</span><span style="color:${rc};">${_md_pct(r.pct)}%</span></div>
			<div class="md-budget-bar"><div class="md-budget-fill" style="width:${Math.min(Math.max(r.pct, 0), 100)}%;background:${rc};"></div></div>
			<div class="md-fnote" style="margin-top:4px;">${__("{0} of {1}", [_md_inr(r.used), _md_inr(r.budget)])}</div></div>`;
	});
	html += `<div class="md-more-row" style="text-align:left;padding-left:0;"><a class="md-link" ${report(null)}>${
		over.length > list.length ? __("{0} more over budget — open the TS Budget Dashboard", [over.length - list.length]) : __("Open the TS Budget Dashboard")} &rarr;</a></div>`;
	return _md_section(title, badge, html, "margin-bottom:0;");
}

/* ══════════════════════════════════════════════════════════════════════
   TOP SUPPLIERS
   ══════════════════════════════════════════════════════════════════════ */

function _md_suppliers(p, f, link) {
	const title = __("Top 5 Suppliers");
	if (!p || p.state !== "ok") return _md_section(title, "", _md_state(p), "margin-bottom:0;");
	const sup = p.top_suppliers;
	if (!sup.length) {
		return _md_section(title, "", `<div class="md-t-faint" style="padding:20px;text-align:center;">${__("No approved POs in this period.")}</div>`, "margin-bottom:0;");
	}
	const top = sup.reduce((t, s) => t + s.value, 0);
	const badge = p.current ? `<span class="md-section-badge" style="background:#eff6ff;color:#3b82f6;">${__("{0}% of all buying", [Math.round((top / p.current) * 100)])}</span>` : "";
	const max_val = sup[0].value || 1;
	const rank_colors = ["#f59e0b", "#94a3b8", "#b45309", "#6b7280", "#6b7280"];
	const html = sup.map((s, i) => `<div class="md-supplier-row" ${link({ dt: "Purchase Order", filters: { docstatus: 1, company: f.company, supplier: s.supplier, transaction_date: ["between", [f.start, f.end]] } })}>
		<div class="md-supplier-rank" style="background:${rank_colors[i] || "#6b7280"};">${i + 1}</div>
		<div style="flex:1;min-width:0;">
			<div style="display:flex;justify-content:space-between;margin-bottom:3px;">
				<span style="font-size:12px;font-weight:500;color:#374151;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_md_esc(s.supplier_name)}</span>
				<span style="font-size:11px;color:#6b7280;flex-shrink:0;margin-left:8px;">${p.current ? __("{0}% of buying", [Math.round((s.value / p.current) * 100)]) : _md_pos(s.count)}</span>
			</div>
			<div class="md-supplier-bar"><div class="md-supplier-fill" style="width:${(s.value / max_val) * 100}%;background:#3b82f6;"></div></div>
			<div style="font-size:11px;color:#3b82f6;font-weight:600;margin-top:2px;">${_md_inr(s.value)} &middot; ${_md_pos(s.count)}</div>
		</div>
	</div>`).join("");
	return _md_section(title, badge, html, "margin-bottom:0;");
}

/* ══════════════════════════════════════════════════════════════════════
   GATE & RECEIVING
   ══════════════════════════════════════════════════════════════════════ */

function _md_gate(p, link) {
	const title = __("Gate & receiving");
	const badge = `<span class="md-section-badge" style="background:#f1f5f9;color:#475569;">${__("live")}</span>`;
	if (!p || p.state !== "ok") return _md_section(title, badge, _md_state(p));
	const card = (v, label, sub, color, t) => `<div class="md-trend-card" ${link(t)}>
		<div class="md-trend-val" style="color:${color};">${v === null || v === undefined ? "—" : v}</div>
		<div class="md-trend-label">${label}</div><div class="md-fnote" style="margin-top:2px;">${sub}</div></div>`;
	return _md_section(title, badge, `<div class="md-gate-grid">
		${card(p.arrived_24h, __("Arrived · last 24 h"), __("trucks not yet exited"), "#3b82f6", { dt: "TS Token", filters: {
			entry_type: "Material", g2_link_time: [">=", p.arrived_since], status: ["not in", p.arrived_exclude] } })}
		${card(p.grain_waiting, __("Grain awaiting PO link"), p.grain_over_6h ? __("{0} over 6 h", [p.grain_over_6h]) : __("none over 6 h"), "#f59e0b", { page: "stores-receiving" })}
		${card(p.receipt_owed, __("Left owing a receipt"), p.receipt_owed === null ? "" : __("Grain {0} · Non-RM {1}", [p.receipt_owed_grain, p.receipt_owed_non_rm]),
			p.receipt_owed ? "#ef4444" : "#10b981", { page: "stores-receiving" })}
	</div><div class="md-fnote">${__("Open gate records — a vehicle may have left without an exit entry.")}</div>`);
}

/* ══════════════════════════════════════════════════════════════════════
   NON-BRANDED ITEMS — collapsed, no flashing
   ══════════════════════════════════════════════════════════════════════ */

function _md_brandless(n, link) {
	if (!n || n.state !== "ok" || !n.count) return "";
	let rows = "";
	n.items.forEach((item) => {
		rows += `<tr ${link({ dt: "Item", form: item.name })}>
			<td style="font-weight:600;color:#1e40af;">${_md_esc(item.name)}</td><td>${_md_esc(item.item_name)}</td>
			<td class="md-hide-phone">${_md_esc(item.item_group)}</td><td style="text-align:right;font-weight:600;">${item.po_count || 0}</td></tr>`;
	});
	return `<div class="md-section md-nbi md-animate md-animate-d4">
		<div class="md-section-head">
			<div class="md-section-title" style="color:#92400e;">&#9888; ${__("Non-Branded Items")}</div>
			<div style="display:flex;align-items:center;gap:6px;">
				<span class="md-section-badge" style="background:#fef2f2;color:#dc2626;font-weight:700;">${__("{0} items without brand", [n.count])}</span>
				<button type="button" class="md-nbi-btn">${__("Show")} &#9660;</button>
			</div>
		</div>
		<div class="md-nbi-body" hidden>
			<div style="font-size:12px;color:#92400e;margin-bottom:10px;">${__("Enabled items with no Brand. The 5 bought on the most POs:")}</div>
			<table class="md-action-table"><thead><tr><th>${__("Item Code")}</th><th>${__("Item Name")}</th>
				<th class="md-hide-phone">${__("Item Group")}</th><th style="text-align:right;">${__("POs")}</th></tr></thead><tbody>${rows}</tbody></table>
			<div class="md-more-row"><a class="md-link" ${link({ dt: "Item", filters: { disabled: 0, brand: ["is", "not set"] } })}>${__("Open all {0} items", [n.count])} &rarr;</a></div>
		</div>
	</div>`;
}

/* ══════════════════════════════════════════════════════════════════════
   HELPERS
   ══════════════════════════════════════════════════════════════════════ */

function _md_inr(v) {
	if (v === null || v === undefined) return "—";
	const a = Math.abs(v), sign = v < 0 ? "-" : "";
	const fmt = (x, u) => `${sign}₹ ${x.toFixed(x < 10 ? 2 : x < 100 ? 1 : 0)} ${u}`;
	if (a >= 99.95e5) return fmt(a / 1e7, "Cr");
	if (a >= 1e5) return fmt(a / 1e5, "L");
	return `${sign}₹ ${format_number(a, null, 0)}`;
}

function _md_wait(h) {
	if (h === null || h === undefined) return "—";
	const mins = Math.round(h * 60), d = Math.floor(mins / 1440), hr = Math.floor((mins % 1440) / 60);
	if (d >= 3) return `${d} d`;
	if (d >= 1) return `${d} d ${hr} h`;
	return hr >= 1 ? `${hr} h` : "< 1 h";
}

function _md_dates(s, e) {
	const o = (x) => frappe.datetime.str_to_obj(x);
	const f = (x, y) => x.toLocaleDateString("en-IN", y ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" });
	return s === e ? f(o(s), true) : `${f(o(s), s.slice(0, 4) !== e.slice(0, 4))} – ${f(o(e), true)}`;
}

const _md_pos = (n) => (n === 1 ? __("1 PO") : __("{0} POs", [n]));
const _md_delta = (n) => `${n >= 0 ? "&#9650;" : "&#9660;"} ${Math.abs(n) > 999 ? ">999" : Math.abs(n)}%`;
const _md_pct_color = (pct) => (pct > 100 ? "#ef4444" : pct > 80 ? "#f59e0b" : "#10b981");
const _md_pct = (pct) => (pct > 999 ? ">999" : pct);

function _md_esc(v) { return frappe.utils.escape_html(v == null ? "" : String(v)); }

let _md_clock_interval = null;
function _md_start_clock() {
	clearInterval(_md_clock_interval);
	const tick = () => {
		const $cl = $(".md-clock");
		if ($cl.length) {
			$cl.text(new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
		}
	};
	tick();
	_md_clock_interval = setInterval(tick, 1000);
}
