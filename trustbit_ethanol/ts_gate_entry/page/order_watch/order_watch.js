// Copyright (c) 2026, Trustbit Software and contributors
// Order Watch page: the findings of the TS Order Watch report in three view styles the reader picks
// (Brief, Cards, List). Data: report/ts_order_watch/ts_order_watch.get_page_data (MD only, read-only).
//
// EVERY text value from the server is plain text typed by people: build HTML only through OW.esc().
//
// A view is one file, included below, that registers:
//   OW.views.<key> = { label, hint, render(ctx) -> html string, after(root, ctx) (optional) }
// ctx = { data, all, shown, routine, counts, st }
//   all     every finding, each with _i (its id), _lv ("serious" | "check" | "note")
//   shown   findings after the level / kind filters, without the routine ones
//   routine "Note" findings of kind "open" after the filters (the many old open orders): show them folded
//   counts  { Serious, "To check", Note, kinds: { <kind>: { n, Serious, "To check", Note } } } over `all`
// The core handles these attributes in any view:
//   data-ow-toggle="<i>"  open / close .ow-fold[data-ow-fold="<i>"] (filled with OW.detail on first open)
//   data-ow-level, data-ow-kind  set a filter ("all" clears it)      data-ow-po="<order>"  open the order
//   data-ow-jump="<i>"    scroll to [data-ow-item="<i>"] and open it  data-ow-more  show 20 more routine rows
// Animation: give an element class "ow-anim" (it rises in when it scrolls into view, staggered among its
// siblings), use OW.num() for numbers that count up, OW.bar() and OW.timeline() for the moving charts.

(function () {
	const OW = { views: {}, order: ["brief", "cards", "list"] };
	const METHOD = "trustbit_ethanol.ts_gate_entry.report.ts_order_watch.ts_order_watch.get_page_data";
	const LEVELS = ["Serious", "To check", "Note"];
	const STORE = "ts_order_watch_view";

	OW.LEVELS = LEVELS;
	OW.LV = { Serious: "serious", "To check": "check", Note: "note" };
	OW.LEVEL_HINT = { Serious: __("Look at these first"), "To check": __("Worth a look"), Note: __("Mostly routine") };
	OW.KINDS = {
		split: { title: __("Possible split order"), icon: "split" },
		near: { title: __("Just under the line"), icon: "near" },
		over: { title: __("Over the line without the MD"), icon: "over" },
		self: { title: __("Entered and approved by the same account"), icon: "user" },
		more: { title: __("Received more than approved"), icon: "box" },
		allowance: { title: __("Receipt allowance raised just before a receipt"), icon: "swap" },
		early: { title: __("Goods came before the order"), icon: "truck" },
		open: { title: __("Approved long ago, nothing received"), icon: "clock" },
		other: { title: __("Other finding"), icon: "info" },
	};
	OW.KIND_ORDER = ["split", "near", "over", "self", "more", "allowance", "early", "open", "other"];

	const PATHS = {
		serious: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.2v.3"/>',
		check: '<path d="M12 4 2.8 19.5h18.4L12 4Z"/><path d="M12 10v4.5M12 17v.3"/>',
		note: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.3"/>',
		info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.3"/>',
		chevron: '<path d="m6 9 6 6 6-6"/>',
		arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
		down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
		split: '<path d="M4 20h16M7 20V9M12 20V5M17 20v-8"/>',
		near: '<path d="M4 8h16M8 20v-8M16 20v-8"/>',
		over: '<path d="M4 14h16M12 20V4M8 8l4-4 4 4"/>',
		user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c.8-3.6 3.5-5.5 7-5.5s6.200 1.900 7 5.500"/>',
		box: '<path d="M3.500 8 12 3.500 20.500 8v8L12 20.500 3.500 16V8Z"/><path d="M3.500 8 12 12.500 20.500 8M12 12.500v8"/>',
		swap: '<path d="M7 4v16M3.500 7.500 7 4l3.500 3.500M17 20V4M13.500 16.500 17 20l3.500-3.500"/>',
		truck: '<path d="M2.500 6.500h11v9.500h-11zM13.500 10h4l3 3v3h-7z"/><circle cx="7" cy="17.500" r="1.800"/><circle cx="17" cy="17.500" r="1.800"/>',
		clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5.200l3.300 2"/>',
		brief: '<path d="M6 3.500h12v17H6zM9 8h6M9 12h6M9 16h3.500"/>',
		cards: '<rect x="3.500" y="4" width="17" height="6.500" rx="1.500"/><rect x="3.500" y="13.500" width="17" height="6.500" rx="1.500"/>',
		list: '<rect x="3.500" y="4" width="6.500" height="16" rx="1.500"/><path d="M13.500 6.500h7M13.500 12h7M13.500 17.500h7"/>',
		table: '<rect x="3.500" y="4.500" width="17" height="15" rx="1.500"/><path d="M3.500 10h17M9.500 4.500v15"/>',
		flag: '<path d="M5 21V4M5 4.500h12l-2.200 4 2.200 4H5"/>',
		done: '<circle cx="12" cy="12" r="9"/><path d="m8 12.300 2.800 2.800L16.200 9.500"/>',
	};
	OW.icon = (name, cls) =>
		`<svg class="ow-ic ${cls || ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name] || PATHS.info}</svg>`;

	// ---- text and numbers ----
	OW.esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
	OW.group = (n) => {
		const s = String(Math.round(Math.abs(n || 0)));
		return s.length <= 3 ? s : s.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + s.slice(-3);
	};
	OW.inr = (v) => (v == null ? "" : (v < 0 ? "-" : "") + "₹" + OW.group(v));
	OW.short = (v) => {
		if (v == null) return "";
		// cut off, never rounded up: an amount just under a line must not print as the line itself
		const a = Math.abs(v), cut = (unit) => (Math.floor(a / (unit / 100)) / 100).toFixed(2).replace(/\.?0+$/, "");
		if (a >= 1e7) return "₹" + cut(1e7) + " Cr";
		if (a >= 1e5) return "₹" + cut(1e5) + " L";
		return OW.inr(v);
	};
	const FMT = { int: (v) => OW.group(v), inr: OW.inr, short: OW.short };
	// a number that counts up when it comes into view
	OW.num = (v, fmt) =>
		`<span class="ow-num" data-ow-count="${Number(v) || 0}" data-ow-fmt="${fmt || "int"}">${OW.esc(FMT[fmt || "int"](Number(v) || 0))}</span>`;
	OW.when = (dt) => (dt ? moment(dt).format("D MMM YYYY, h:mm a") : "");
	OW.day = (dt) => (dt ? moment(dt).format("D MMM YYYY") : "");

	// ---- pieces every view shares ----
	OW.title = (f) => (OW.KINDS[f.kind] || OW.KINDS.other).title;
	OW.kindIcon = (f) => OW.icon((OW.KINDS[f.kind] || OW.KINDS.other).icon);
	OW.pill = (level) =>
		`<span class="ow-pill ow-lv-${OW.LV[level] || "note"}">${OW.icon(OW.LV[level] || "note")}<span>${OW.esc(__(level))}</span></span>`;
	OW.lineName = (f) => (f.kind === "more" ? __("approved amount") : f.review_line ? __("review line") : __("MD line"));
	OW.supplier = (f) => {
		if (f.supplier) return f.supplier;
		const names = [...new Set((f.orders || []).map((o) => o.supplier).filter(Boolean))];
		return names.length > 1 ? __("{0} suppliers: {1}", [names.length, names.join(", ")]) : names[0] || "";
	};
	// "₹8,85,000 over the MD line", "₹2,000 under the MD line", "₹1,25,000 more than approved"
	OW.gap = (f) => {
		if (f.line == null || f.total == null) return "";
		if (f.kind === "more") return __("{0} more than approved", [OW.inr(f.total - f.line)]);
		if (f.total < f.line) return __("{0} under the {1}", [OW.inr(f.line - f.total), OW.lineName(f)]);
		return f.total > f.line ? __("{0} over the {1}", [OW.inr(f.total - f.line), OW.lineName(f)]) : __("at the {0}", [OW.lineName(f)]);
	};
	// the amount against its line: one segment per order of a split group, the part past the line striped
	OW.bar = (f, opts) => {
		if (f.line == null || f.total == null || !(f.line > 0)) return "";
		const o = opts || {}, scale = Math.max(f.total, f.line) * 1.14, pct = (v) => Math.max(0, Math.min(100, (v / scale) * 100));
		const parts = f.kind === "split" && (f.orders || []).length > 1 ? f.orders.map((x) => x.value) : [f.total];
		const segs = parts.map((v, i) => `<span class="ow-seg" style="--w:${pct(v).toFixed(2)}%;--i:${i}"></span>`).join("");
		const over = f.total > f.line ? `<span class="ow-over" style="--from:${pct(f.line).toFixed(2)}%;--to:${pct(f.total).toFixed(2)}%"></span>` : "";
		const label = o.plain ? "" : `<span class="ow-tick-label">${OW.esc(OW.lineName(f))} ${OW.esc(OW.short(f.line))}</span>`;
		return `<div class="ow-bar ow-lv-${f._lv} ${o.plain ? "ow-bar-plain" : ""} ow-anim" role="img" aria-label="${OW.esc(OW.gap(f))}">
			<div class="ow-track"><div class="ow-segs">${segs}</div>${over}<i class="ow-tick ${pct(f.line) > 70 ? "ow-tick-end" : ""}" style="--at:${pct(f.line).toFixed(2)}%">${label}</i></div></div>`;
	};
	// the orders of a split group in the order they were entered, with the running total crossing the line
	OW.timeline = (f) => {
		const os = (f.orders || []).slice().sort((a, b) => String(a.entered_at).localeCompare(String(b.entered_at)));
		if (os.length < 2 || !(f.line > 0)) return "";
		const W = 640, H = 236, L = 16, R = 16, T = 34, B = 62, slot = (W - L - R) / os.length;
		let run = 0, crossed = -1;
		const sums = os.map((o, i) => { run += o.value; if (crossed < 0 && run >= f.line) crossed = i; return run; });
		const top = Math.max(run, f.line) * 1.12, y = (v) => H - B - (v / top) * (H - B - T), x = (i) => L + slot * (i + 0.5);
		// a long group: thinner bars, text only on every few orders (always the first, the crossing one and the last),
		// and the whole chart still finishes moving in about the same time
		const n = os.length, bw = Math.max(4, Math.min(34, slot - 6)), every = Math.ceil(n / 8), d = (i) => (n > 8 ? (i * 8) / n : i).toFixed(2);
		const keep = [];
		[crossed, n - 1, 0].concat(os.map((o, i) => i).filter((i) => i % every === 0)).forEach((i) => { if (i >= 0 && !keep.some((k) => Math.abs(k - i) < every)) keep.push(i); });
		const named = (i) => keep.includes(i);
		const bars = os.map((o, i) => `<rect class="ow-tl-bar" style="--i:${d(i)}" x="${(x(i) - bw / 2).toFixed(1)}" y="${y(o.value).toFixed(1)}" width="${bw.toFixed(1)}" height="${(H - B - y(o.value)).toFixed(1)}" rx="3"/>`).join("");
		const path = sums.map((s, i) => (i ? `H${x(i).toFixed(1)}V${y(s).toFixed(1)}` : `M${x(0).toFixed(1)} ${y(s).toFixed(1)}`)).join("");
		const dots = sums.map((s, i) => `<g class="ow-tl-dot ${i === crossed ? "is-cross" : ""}" style="--i:${d(i)}"><circle class="ow-tl-ring" cx="${x(i).toFixed(1)}" cy="${y(s).toFixed(1)}" r="9"/><circle cx="${x(i).toFixed(1)}" cy="${y(s).toFixed(1)}" r="4"/>
			${!named(i) ? "" : `<text x="${x(i).toFixed(1)}" y="${(y(s) - 12).toFixed(1)}" text-anchor="middle" class="ow-tl-sum">${OW.esc(OW.short(s))}</text>`}</g>`).join("");
		const same = moment(os[0].entered_at).isSame(os[os.length - 1].entered_at, "day");
		const labels = os.map((o, i) => !named(i) ? "" : `<g class="ow-tl-label" style="--i:${d(i)}"><text x="${x(i).toFixed(1)}" y="${H - B + 20}" text-anchor="middle" class="ow-tl-val">${OW.esc(OW.short(o.value))}</text>
			<text x="${x(i).toFixed(1)}" y="${H - B + 38}" text-anchor="middle" class="ow-tl-time">${OW.esc(moment(o.entered_at).format(same ? "h:mm a" : "D MMM"))}</text></g>`).join("");
		const note = crossed >= 0 ? __("Each bar is one order. The stepped line adds them up in the order they were entered: it passed the {0} with order {1}.", [OW.lineName(f), crossed + 1]) : "";
		return `<div class="ow-tl ow-lv-${f._lv} ow-anim"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${OW.esc(note)}">
			<line class="ow-tl-base" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>${bars}
			<line class="ow-tl-line" x1="${L}" x2="${W - R}" y1="${y(f.line).toFixed(1)}" y2="${y(f.line).toFixed(1)}"/>
			<text class="ow-tl-linename" x="${W - R - 2}" y="${(y(f.line) + 15).toFixed(1)}" text-anchor="end">${OW.esc(OW.lineName(f))} ${OW.esc(OW.short(f.line))}</text>
			<path class="ow-tl-run" pathLength="1" d="${path}"/>${dots}${labels}</svg><p class="ow-tl-note">${OW.esc(note)}</p></div>`;
	};
	OW.orders = (f) => {
		const os = f.orders || [];
		if (!os.length) return "";
		const rows = os.map((o) => {
			const approved = o.approved_at ? `${OW.esc(o.approved_by || "")}<small>${OW.esc(OW.when(o.approved_at))}</small>` : OW.esc(o.status || __("Not approved yet"));
			return `<tr><td data-l="${__("Order")}"><button type="button" class="ow-link" data-ow-po="${OW.esc(o.name)}">${OW.esc(o.name)}</button><small>${__("PO date")} ${OW.esc(o.po_date)}</small></td>
				<td data-l="${__("Entered")}">${OW.esc(OW.when(o.entered_at))}<small>${__("by")} ${OW.esc(o.entered_by || "")}</small></td>
				<td data-l="${__("Approved")}">${approved}</td>
				<td data-l="${__("Main items")}">${OW.esc(o.items)}${o.request ? `<small>${__("Request")} ${OW.esc(o.request)}</small>` : ""}</td>
				<td data-l="${__("Received")}">${OW.esc(o.received)}</td><td data-l="${__("Value")}" class="ow-r"><b>${OW.esc(OW.inr(o.value))}</b></td></tr>`;
		}).join("");
		const sum = os.length > 1 ? `<tfoot><tr><td colspan="5">${__("Together: {0} orders", [os.length])}</td><td class="ow-r"><b>${OW.esc(OW.inr(os.reduce((t, o) => t + o.value, 0)))}</b></td></tr></tfoot>` : "";
		return `<div class="ow-orders ow-anim"><h5>${os.length > 1 ? __("The {0} orders", [os.length]) : __("The order")}</h5><table><thead><tr><th>${__("Order")}</th><th>${__("Entered")}</th><th>${__("Approved")}</th>
			<th>${__("Main items")}</th><th>${__("Received")}</th><th class="ow-r">${__("Value")}</th></tr></thead><tbody>${rows}</tbody>${sum}</table></div>`;
	};
	OW.facts = (f) => {
		const li = (f.facts || []).map((t, i) => `<li style="--i:${i}">${OW.esc(t)}</li>`).join("");
		return `<div class="ow-facts ow-anim">${li ? `<h5>${__("What the ERP shows")}</h5><ul>${li}</ul>` : ""}<p class="ow-last">${OW.icon("info")}${OW.esc(OW.data.last_line)}</p></div>`;
	};
	// everything behind one finding: the moving chart (split groups), its orders, its facts
	// a finding that carries more than one reason names the others, so none is only a line among the facts
	OW.also = (f) => {
		const more = (f.kinds || []).filter((k) => k !== f.kind && OW.KINDS[k]);
		return more.length ? `<p class="ow-also ow-anim"><b>${__("Also")}</b>${more.map((k) => `<span>${OW.icon(OW.KINDS[k].icon)}${OW.esc(OW.KINDS[k].title)}</span>`).join("")}</p>` : "";
	};
	OW.detail = (f) => `<div class="ow-detail ow-lv-${f._lv}">${OW.also(f)}${f.kind === "split" ? OW.timeline(f) : ""}${OW.orders(f)}${OW.facts(f)}</div>`;
	OW.fold = (f) => `<div class="ow-fold" data-ow-fold="${f._i}"><div class="ow-fold-in"></div></div>`;
	OW.toggle = (f, label) =>
		`<button type="button" class="ow-toggle" data-ow-toggle="${f._i}" aria-expanded="false"><span>${OW.esc(label || __("Show the orders and facts"))}</span>${OW.icon("chevron")}</button>`;
	OW.counter = (level, ctx, extra) => {
		const on = ctx.st.level === level;
		return `<button type="button" class="ow-counter ow-lv-${OW.LV[level]} ow-anim ${on ? "is-on" : ""}" data-ow-level="${on ? "all" : level}" aria-pressed="${on}">
			<span class="ow-counter-top">${OW.icon(OW.LV[level])}${OW.esc(__(level))}</span><b>${OW.num(ctx.counts[level])}</b>
			<span class="ow-counter-sub">${OW.esc(extra || OW.LEVEL_HINT[level])}</span></button>`;
	};
	OW.kindChips = (ctx) => {
		const chip = (key, label, n) => `<button type="button" class="ow-chip ${ctx.st.kind === key ? "is-on" : ""}" data-ow-kind="${key}" aria-pressed="${ctx.st.kind === key}">${OW.esc(label)}<i>${n}</i></button>`;
		return `<div class="ow-chips ow-anim">${chip("all", __("All kinds"), ctx.all.length)}${OW.KIND_ORDER.filter((k) => ctx.counts.kinds[k]).map((k) => chip(k, OW.KINDS[k].title, ctx.counts.kinds[k].n)).join("")}</div>`;
	};
	// the many old open orders, folded: 20 rows at a time, each row opens like any finding
	OW.routine = (ctx, heading) => {
		const list = ctx.routine;
		if (!list.length) return "";
		const worth = list.reduce((t, f) => t + (f.total || 0), 0);
		const rows = list.map((f, n) => `<div class="ow-item ow-rrow ${n >= 20 ? "is-later" : ""}" data-ow-item="${f._i}"><button type="button" class="ow-rrow-head" data-ow-toggle="${f._i}" aria-expanded="false">
			<span class="ow-rrow-name">${OW.esc(OW.supplier(f))}</span><span class="ow-rrow-text">${OW.esc(f.summary)}</span><b>${OW.esc(OW.short(f.total))}</b>${OW.icon("chevron")}</button>${OW.fold(f)}</div>`).join("");
		return `<section class="ow-routine ow-anim"><button type="button" class="ow-routine-head" data-ow-routine aria-expanded="false">${OW.icon("clock")}
			<span><b>${OW.esc(heading || (list.length === 1 ? __("1 old open order, kept as a note") : __("{0} old open orders, kept as notes", [list.length])))}</b><small>${__("Approved over 60 days ago and nothing received yet. Usually routine. Together {0}.", [OW.short(worth)])}</small></span>${OW.icon("chevron")}</button>
			<div class="ow-fold ow-routine-fold"><div class="ow-fold-in">${rows}${list.length > 20 ? `<button type="button" class="ow-more" data-ow-more>${__("Show 20 more")}</button>` : ""}</div></div></section>`;
	};
	OW.ranked = (data) => {
		if (!(data.ranked || []).length) return "";
		const what = { supplier: __("Supplier"), item: __("Item"), request: __("Request") };
		const rows = data.ranked.map((r) => `<li><b>${what[r.kind] ? `<i class="ow-ranked-kind">${OW.esc(what[r.kind])}</i>` : ""}${OW.esc(r.label)}</b><span>${r.d90 == null ? "" : OW.esc(__("90 days {0} · year {1} · ", [OW.short(r.d90), OW.short(r.fy)]))}${OW.esc(__("{0} orders · largest {1}", [r.orders, OW.short(r.largest)]))}${(r.marks || []).map((m) => " · " + OW.esc(m)).join("")}</span></li>`).join("");
		return `<section class="ow-routine ow-ranked ow-anim"><button type="button" class="ow-routine-head" data-ow-routine aria-expanded="false">${OW.icon("split")}
			<span><b>${__("Standing small orders")}</b><small>${__("Suppliers, then items, then requests with many small orders that never need the MD. Each group has its largest first; the same orders can show under a supplier and under an item.")}</small></span>${OW.icon("chevron")}</button>
			<div class="ow-fold ow-routine-fold"><div class="ow-fold-in"><ul class="ow-ranked-list">${rows}</ul></div></div></section>`;
	};
	OW.mdLine = (data) => {
		const l = data.md_lines || [];
		if (!l.length) return "";
		return l.length === 1 ? __("Orders of {0} or more need the MD's approval.", [OW.inr(l[0])]) : __("Orders need the MD's approval from {0} to {1}, by kind of purchase.", [OW.inr(l[0]), OW.inr(l[l.length - 1])]);
	};

	{% include "trustbit_ethanol/ts_gate_entry/page/order_watch/order_watch_brief.js" %}
	{% include "trustbit_ethanol/ts_gate_entry/page/order_watch/order_watch_cards.js" %}
	{% include "trustbit_ethanol/ts_gate_entry/page/order_watch/order_watch_list.js" %}

	// ---- animation ----
	const calm = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
	function countUp(el) {
		const end = Number(el.dataset.owCount) || 0, fmt = FMT[el.dataset.owFmt] || FMT.int, t0 = performance.now(), ms = 900;
		const step = (t) => {
			const k = Math.min(1, (t - t0) / ms);
			el.textContent = fmt(end * (1 - Math.pow(1 - k, 3)));
			if (k < 1) requestAnimationFrame(step);
		};
		requestAnimationFrame(step);
	}
	OW.animate = (root) => {
		const el = root[0] || root;
		const fresh = [...el.querySelectorAll(".ow-anim:not(.ow-seen)")];
		fresh.forEach((n) => {
			n.classList.add("ow-seen");
			const sibs = n.parentElement ? [...n.parentElement.children].filter((c) => c.classList.contains("ow-anim")) : [n];
			n.style.setProperty("--n", Math.min(sibs.indexOf(n), 10));
		});
		const show = (n) => {
			n.classList.add("ow-in");
			n.querySelectorAll("[data-ow-count]:not(.ow-counted)").forEach((c) => { c.classList.add("ow-counted"); if (!calm()) countUp(c); });
		};
		if (calm() || !("IntersectionObserver" in window)) return fresh.forEach(show);
		OW._io = OW._io || new IntersectionObserver((hits) => hits.forEach((h) => { if (h.isIntersecting) { show(h.target); OW._io.unobserve(h.target); } }), { threshold: 0 });
		fresh.forEach((n) => OW._io.observe(n));
	};

	// ---- the page ----
	function context(st) {
		const all = st.data.findings;
		const counts = { Serious: 0, "To check": 0, Note: 0, kinds: {} };
		all.forEach((f) => {
			counts[f.level] = (counts[f.level] || 0) + 1;
			// a finding with two reasons counts under both kinds (so the kind counts can add up to more than `all`)
			f.kinds.forEach((kind) => {
				const k = (counts.kinds[kind] = counts.kinds[kind] || { n: 0, Serious: 0, "To check": 0, Note: 0 });
				k.n++; k[f.level]++;
			});
		});
		const pass = all.filter((f) => (st.level === "all" || f.level === st.level) && (st.kind === "all" || f.kinds.includes(st.kind)));
		const isRoutine = (f) => f.kind === "open" && f.level === "Note";
		return { data: st.data, all, counts, st, shown: pass.filter((f) => !isRoutine(f)), routine: pass.filter(isRoutine) };
	}
	function switcher(st) {
		const at = OW.order.indexOf(st.view);
		return `<div class="ow-switch" role="tablist" aria-label="${__("View style")}" style="--at:${at};--of:${OW.order.length}"><i class="ow-switch-thumb"></i>${OW.order.map((k) =>
			`<button type="button" role="tab" aria-selected="${k === st.view}" class="${k === st.view ? "is-on" : ""}" data-ow-view="${k}" title="${OW.esc(OW.views[k].hint || "")}">${OW.icon(k)}<span>${OW.esc(OW.views[k].label)}</span></button>`).join("")}</div>`;
	}
	function head(st) {
		const d = st.data;
		return `<div class="ow-head ow-anim"><div class="ow-head-text"><p>${__("Purchase orders that may have gone around the MD's approval, and other approval facts worth a look.")} ${OW.esc(OW.mdLine(d))}</p>
			<small>${OW.esc(__("{0} to {1}", [OW.day(d.from_date), OW.day(d.to_date)]))}${d.company ? " · " + OW.esc(d.company) : ""}</small></div>
			<div class="ow-head-side"><span class="ow-switch-label">${__("View style")}</span>${switcher(st)}</div></div>`;
	}
	function body(st) {
		const ctx = context(st);
		if (!ctx.all.length) {
			const ranked = OW.ranked(st.data);
			return `<div class="ow-state ow-anim">${OW.icon("done", "ow-state-ic")}<h4>${__("Nothing found for these dates.")}</h4><p>${ranked ? __("No order in this period matches a check. The standing small orders are listed below.") : __("No order in this period matches any of the checks.")}</p></div>${ranked}`;
		}
		return OW.views[st.view].render(ctx) + OW.ranked(st.data) +
			`<p class="ow-foot ow-anim">${__("Order Watch lists facts and links found in the ERP. It does not say why anything happened. People are shown by role only.")}</p>`;
	}
	function paint(page, how) {
		const st = page._ow, root = page.main.find(".ow-root");
		if (!st.data) return;
		OW.data = st.data;
		if (how === "view") {
			// the filters changed: only the view is drawn again
			root.find(".ow-view").attr("class", `ow-view ow-view-${st.view} ow-swap`).html(body(st));
		} else {
			root.html(head(st) + `<div class="ow-view ow-view-${st.view} ow-swap">${body(st)}</div>`);
		}
		const view = OW.views[st.view];
		if (view.after && st.data.findings.length) view.after(root.find(".ow-view"), context(st));
		OW.animate(root);
	}
	function skeleton() {
		const card = '<div class="ow-skel-card"><i></i><i></i><i></i></div>';
		return `<div class="ow-skel" aria-busy="true" aria-label="${__("Reading the orders")}"><div class="ow-skel-row">${card}${card}${card}</div>${card}${card}<p>${__("Reading the orders…")}</p></div>`;
	}
	function load(page, soft) {
		const st = page._ow, root = page.main.find(".ow-root");
		const args = { from_date: page.fields_dict.from_date.get_value(), to_date: page.fields_dict.to_date.get_value(), company: page.fields_dict.company.get_value() || "" };
		// an emptied date: wait for it, and let a load that is already running still land
		if (!args.from_date || !args.to_date) return;
		// a filter box that reports a change without a new value (the date boxes do, right after the page opens) asks nothing
		const key = JSON.stringify(args);
		if (soft && key === st.asked) return;
		st.asked = key;
		const seq = ++st.seq;
		const stop = (title, text) => { st.data = null; root.html(`<div class="ow-state ow-anim ow-in">${OW.icon("check", "ow-state-ic")}<h4>${title}</h4><p>${text}</p><button type="button" class="btn btn-default btn-sm" data-ow-retry>${__("Try again")}</button></div>`); };
		if (args.from_date > args.to_date) return stop(__("The dates are the wrong way round."), __("Pick a From Date on or before the To Date."));
		root.html(skeleton());
		frappe.call({ method: METHOD, type: "GET", args }).then((r) => {
			if (seq !== st.seq) return;
			st.data = r.message || { findings: [] };
			st.data.findings.forEach((f, i) => { f._i = i; f._lv = OW.LV[f.level] || "note"; f.kinds = (f.kinds || []).length ? f.kinds : [f.kind]; });
			st.level = st.kind = "all"; st.sel = 0;
			// a login that cannot read Company picks from the names the server sends
			const co = page.fields_dict.company;
			if (co.df.fieldtype === "Select" && !st.companies && (st.data.companies || []).length) {
				st.companies = true;
				co.df.options = [""].concat(st.data.companies);
				co.refresh();
			}
			paint(page);
		}, (xhr) => {
			if (seq !== st.seq) return;
			if (xhr && xhr.status === 403) return stop(__("Order Watch could not be opened."), __("It is for the MD. If that is you, press Try again."));
			stop(__("Order Watch could not read the orders."), __("Check the dates and the company, then press Try again."));
		});
	}
	function openFold(root, st, i, open) {
		const fold = root.find(`.ow-fold[data-ow-fold="${i}"]`), f = st.data.findings[i];
		if (!fold.length || !f) return;
		const want = open == null ? !fold.hasClass("is-open") : open;
		if (want && !fold.data("filled")) { fold.data("filled", 1).children(".ow-fold-in").html(OW.detail(f)); }
		fold.toggleClass("is-open", want);
		root.find(`[data-ow-toggle="${i}"]`).attr("aria-expanded", want).closest(".ow-item").toggleClass("is-open", want);
		if (want) setTimeout(() => OW.animate(fold), 30);
	}
	OW.open = (root, i, open) => openFold(root.closest(".ow-root"), OW.page._ow, i, open);

	frappe.pages["order-watch"].on_page_load = function (wrapper) {
		const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Order Watch"), single_column: true });
		let view = "brief";
		try { view = localStorage.getItem(STORE) || view; } catch (e) { /* no storage: the brief view */ }
		const st = (page._ow = { data: null, view: OW.views[view] ? view : "brief", level: "all", kind: "all", sel: 0, seq: 0, timer: null });
		OW.page = page;
		const again = () => { if (!st.ready) return; clearTimeout(st.timer); st.timer = setTimeout(() => load(page, true), 350); };
		page.add_field({ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -90), change: again });
		page.add_field({ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), change: again });
		// an MD-only login cannot read Company: a Link box would refuse every value, so it gets a pick list
		// that the first answer fills with the company names
		const link = frappe.model.can_read("Company");
		page.add_field({ fieldname: "company", label: __("Company"), fieldtype: link ? "Link" : "Select", options: link ? "Company" : [""], change: again });
		const filters = () => ({ from_date: page.fields_dict.from_date.get_value(), to_date: page.fields_dict.to_date.get_value(), company: page.fields_dict.company.get_value() || "" });
		page.set_secondary_action(__("Table / Export"), () => frappe.set_route("query-report", "TS Order Watch", filters()));
		page.add_action_icon("refresh", () => load(page), "", __("Read the orders again"));
		// after the menu has closed, or it is printed on top of the page
		page.add_menu_item(__("Print"), () => setTimeout(() => window.print(), 350));
		page.main.append('<div class="ow-root"></div>');
		const root = page.main.find(".ow-root");
		// a filter pressed from the keyboard: the same control keeps the focus after the view is drawn again
		const refilter = (e, attr, set) => {
			const at = root.find(`[${attr}]`).index(e.currentTarget);
			set(e.currentTarget.dataset);
			st.sel = 0;
			paint(page, "view");
			const same = root.find(`[${attr}]`)[at];
			if (same) same.focus({ preventScroll: true });
		};

		root.on("click", "[data-ow-view]", (e) => {
			const next = e.currentTarget.dataset.owView;
			if (next === st.view || !OW.views[next]) return;
			st.view = next;
			try { localStorage.setItem(STORE, next); } catch (err) { /* not remembered, nothing else changes */ }
			root.find(".ow-switch").css("--at", OW.order.indexOf(next)).find("button").each((i, b) => $(b).toggleClass("is-on", b.dataset.owView === next).attr("aria-selected", b.dataset.owView === next));
			root.find(".ow-view").addClass("ow-leave");
			setTimeout(() => paint(page, "view"), calm() ? 0 : 190);
		});
		root.on("click", "[data-ow-level]", (e) => refilter(e, "data-ow-level", (d) => { st.level = d.owLevel; }));
		root.on("click", "[data-ow-kind]", (e) => refilter(e, "data-ow-kind", (d) => { st.kind = d.owKind; }));
		root.on("click", "[data-ow-toggle]", (e) => openFold(root, st, +e.currentTarget.dataset.owToggle));
		// remember the place: coming back from the order lands where the reader was
		root.on("click", "[data-ow-po]", (e) => { e.stopPropagation(); st.back = window.scrollY; frappe.set_route("Form", "Purchase Order", e.currentTarget.dataset.owPo); });
		root.on("click", "[data-ow-routine]", (e) => {
			const b = $(e.currentTarget), open = b.attr("aria-expanded") !== "true";
			b.attr("aria-expanded", open).next(".ow-fold").toggleClass("is-open", open);
		});
		root.on("click", "[data-ow-more]", (e) => {
			const later = $(e.currentTarget).siblings(".is-later");
			later.slice(0, 20).removeClass("is-later");
			if (later.length <= 20) $(e.currentTarget).remove();
		});
		root.on("click", "[data-ow-jump]", (e) => {
			const i = +e.currentTarget.dataset.owJump;
			if (!root.find(`[data-ow-item="${i}"]`).length && (st.level !== "all" || st.kind !== "all")) { st.level = st.kind = "all"; paint(page, "view"); }
			const item = root.find(`[data-ow-item="${i}"]`);
			if (!item.length) return;
			item.closest(".ow-fold").addClass("is-open");
			openFold(root, st, i, true);
			item[0].scrollIntoView({ behavior: calm() ? "auto" : "smooth", block: "center" });
			item.addClass("ow-flash");
			setTimeout(() => item.removeClass("ow-flash"), 1600);
		});
		root.on("click", "[data-ow-retry]", () => load(page));
		st.ready = true;
		load(page);
	};
	frappe.pages["order-watch"].on_page_show = function () {
		const st = OW.page && OW.page._ow;
		if (!st || st.back == null) return;
		const y = st.back;
		st.back = null;
		setTimeout(() => window.scrollTo(0, y), 80);
	};
})();
