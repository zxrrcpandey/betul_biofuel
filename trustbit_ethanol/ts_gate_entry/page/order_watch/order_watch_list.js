// Order Watch, "List" view: a slim summary bar, the findings as a grouped list on the left and the selected
// finding in a sticky panel on the right. On a phone the panel is a full-screen sheet over the list.
// Selecting a row redraws only the panel, never the list, so the reader keeps their place.

const owListPhone = () => window.matchMedia("(max-width: 991.98px)").matches;
const owListCalm = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const owListGlass = '<svg class="ow-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>';
const owListLevel = (el, lv) => {
	el.classList.remove("ow-lv-serious", "ow-lv-check", "ow-lv-note");
	el.classList.add("ow-lv-" + lv);
};

// the groups in the order they are listed: every Serious finding first, then the rest kind by kind
function owListGroups(ctx) {
	const groups = [], serious = ctx.shown.filter((f) => f.level === "Serious");
	if (serious.length) groups.push({ lv: "serious", title: __("Serious: start here"), icon: "serious", rows: serious });
	OW.KIND_ORDER.forEach((k) => {
		const rows = ctx.shown.filter((f) => f.level !== "Serious" && (OW.KINDS[f.kind] ? f.kind : "other") === k);
		if (rows.length) groups.push({ lv: "", title: OW.KINDS[k].title, icon: OW.KINDS[k].icon, rows });
	});
	return groups;
}
const owListFlat = (groups) => [].concat(...groups.map((g) => g.rows));

function owListRow(f, pinned) {
	const gap = OW.gap(f);
	return `<button type="button" class="ow-l-row ow-lv-${f._lv} ow-anim" data-ow-l-row="${f._i}" aria-current="false">
		<span class="ow-l-row-top"><i class="ow-l-kind" title="${OW.esc(OW.title(f))}">${OW.kindIcon(f)}</i>${OW.pill(f.level)}<b>${OW.esc(OW.supplier(f))}</b><span class="ow-l-amt">${OW.esc(OW.short(f.total))}</span></span>
		${pinned ? `<span class="ow-l-row-kind">${OW.esc(OW.title(f))}</span>` : ""}<span class="ow-l-row-text">${OW.esc(f.summary)}</span>
		${gap ? `<span class="ow-l-row-gap">${OW.esc(gap)}</span>` : ""}</button>`;
}

function owListSummary(ctx, flat) {
	const money = ctx.data.money || {}, names = new Set();
	ctx.all.forEach((f) => f.level !== "Note" && (f.orders || []).forEach((o) => names.add(o.name)));
	const start = ctx.all.find((f) => f.level === "Serious") || ctx.all.find((f) => f.level === "To check" && flat.includes(f)) || flat[0];
	// a filter may hide the first serious finding: the button then clears the filters, which selects it
	const target = start && flat.includes(start) ? `data-ow-l-go="${start._i}"` : 'data-ow-level="all" data-ow-kind="all"';
	const go = !start ? "" : `<div class="ow-l-start ow-lv-${start._lv} ow-anim"><span class="ow-l-cap"><i></i>${__("Start here")}</span>
		<button type="button" class="ow-l-go" ${target}><span><b>${OW.esc(OW.supplier(start))}</b>
		<small>${OW.esc(OW.title(start))}${OW.gap(start) ? " · " + OW.esc(OW.gap(start)) : ""}</small></span>${OW.icon("arrow")}</button></div>`;
	return `<section class="ow-l-sum ow-anim"><div class="ow-l-levels" role="group" aria-label="${__("Show one level only")}">${OW.LEVELS.map((l) => OW.counter(l, ctx)).join("")}</div>
		<div class="ow-l-money ow-anim"><b>${OW.num(money.both, "short")}</b><span>${__("in {0} orders marked Serious or To check", [names.size])}</span>
		<small>${__("Serious alone: {0}", [OW.short(money.Serious || 0)])}</small></div>${go}</section>`;
}

// up to four tiles: [label, value as HTML, small line, extra class]; only what this finding has
function owListTiles(f) {
	const os = f.orders || [], one = os[0], t = [], more = f.kind === "more", diff = f.total - f.line;
	const lakh = (v) => (Math.abs(v) >= 1e5 ? OW.short(v) : "");
	if (f.total != null) t.push([f.kind === "split" ? __("Together") : more ? __("Received") : __("Order value"), OW.num(f.total, "inr"), lakh(f.total)]);
	if (f.line != null) t.push([__("The {0}", [OW.lineName(f)]), OW.num(f.line, "inr"), lakh(f.line)]);
	if (f.total != null && f.line > 0 && diff) {
		t.push([diff < 0 ? __("Under by") : more ? __("More than approved") : __("Over by"), OW.num(Math.abs(diff), "inr"),
			__("{0}% of the {1}", [Math.floor((f.total / f.line) * 1000) / 10, OW.lineName(f)]), "is-hi"]);
	}
	if (os.length > 1) {
		const sup = new Set(os.map((o) => o.supplier).filter(Boolean)).size;
		t.push([__("Orders"), OW.num(os.length), sup > 1 ? __("{0} suppliers", [sup]) : __("one supplier")]);
	} else if (one) {
		t.push([__("Entered"), OW.esc(OW.day(one.entered_at)), one.entered_by ? __("by {0}", [one.entered_by]) : ""]);
		t.push([__("Approved"), OW.esc(one.approved_at ? OW.day(one.approved_at) : one.status || __("Not approved yet")), one.approved_at && one.approved_by ? __("by {0}", [one.approved_by]) : ""]);
	}
	return t.slice(0, 4).map(([label, value, sub, cls]) =>
		`<div class="ow-l-tile ow-anim ${cls || ""}"><span>${OW.esc(label)}</span><b>${value}</b>${sub ? `<small>${OW.esc(sub)}</small>` : ""}</div>`).join("");
}

// the selected finding, drawn straight into the panel (the core's fold is for the rows of the routine block)
function owListDetail(f) {
	const chart = f.kind === "split" ? OW.timeline(f) : "", bar = chart ? "" : OW.bar(f);
	const what = chart ? `<h5 class="ow-l-h ow-anim">${__("What happened")}</h5>${chart}`
		: bar ? `<h5 class="ow-l-h ow-anim">${__("How it compares")}</h5><div class="ow-l-cmp ow-anim">${bar}<p>${OW.esc(OW.gap(f))}</p></div>` : "";
	return `<div class="ow-detail ow-l-d ow-lv-${f._lv}">
		<p class="ow-l-tags ow-anim">${OW.pill(f.level)}<span>${OW.kindIcon(f)}${OW.esc(OW.title(f))}</span></p>
		<h4 class="ow-l-say ow-anim">${OW.esc(f.summary)}</h4>
		<p class="ow-l-meta ow-anim">${[OW.supplier(f), f.company].filter(Boolean).map((s) => OW.esc(s)).join(" · ")}</p>
		${OW.also(f)}<div class="ow-l-tiles">${owListTiles(f)}</div>${what}${OW.orders(f)}${OW.facts(f)}</div>`;
}

OW.views.list = {
	label: __("List"),
	hint: __("The findings as a list, with the selected one beside it"),

	render(ctx) {
		const groups = owListGroups(ctx), flat = owListFlat(groups), st = ctx.st;
		const clear = `<button type="button" class="ow-link" data-ow-level="all" data-ow-kind="all">${__("Show all findings")}</button>`;
		if (!flat.length && !ctx.routine.length) {
			const none = st.level !== "all" && st.kind !== "all" ? __("Nothing at this level of this kind.") : st.level !== "all" ? __("Nothing at this level.") : __("Nothing of this kind.");
			return `<div class="ow-l">${owListSummary(ctx, flat)}<div class="ow-l-none ow-anim">${OW.icon("done")}<p>${none}</p>${clear}</div></div>`;
		}
		const only = [st.level !== "all" ? __(st.level) : "", st.kind !== "all" ? (OW.KINDS[st.kind] || OW.KINDS.other).title : ""].filter(Boolean).join(" · ");
		const find = __("Find a supplier, order or item");
		const head = `<div class="ow-l-head"><div class="ow-l-titlebar"><h4>${__("Findings")}</h4><i class="ow-l-count">${flat.length}</i></div>
			${flat.length ? `<label class="ow-l-search">${owListGlass}<input type="search" data-ow-l-q placeholder="${find}" aria-label="${find}" autocomplete="off"></label>` : ""}
			${only ? `<p class="ow-l-note">${OW.esc(__("Showing only: {0}", [only]))} ${clear}</p>` : ""}<p class="ow-l-hits" role="status" aria-live="polite" hidden></p></div>`;
		const list = groups.map((g) => `<div class="ow-l-group ${g.lv ? "ow-lv-" + g.lv : ""} ow-anim"><h5 class="ow-l-gh">${OW.icon(g.icon)}<span>${OW.esc(g.title)}</span><i class="ow-l-count">${g.rows.length}</i></h5>
			${g.rows.map((f) => owListRow(f, !!g.lv)).join("")}</div>`).join("");
		const pane = !flat.length ? "" : `<aside class="ow-l-pane is-new" aria-label="${__("The selected finding")}"><div class="ow-l-bar">
			<button type="button" class="ow-l-back" data-ow-l-back>${OW.icon("arrow")}<span>${__("Back")}</span></button><span class="ow-l-pos" aria-live="polite"></span>
			<span class="ow-l-nav"><button type="button" data-ow-l-step="-1" aria-label="${__("Previous finding")}">${OW.icon("chevron")}</button>
			<button type="button" data-ow-l-step="1" aria-label="${__("Next finding")}">${OW.icon("chevron")}</button></span></div><div class="ow-l-body"></div></aside>`;
		return `<div class="ow-l">${owListSummary(ctx, flat)}<div class="ow-l-split ${flat.length ? "" : "is-solo"}">
			<section class="ow-l-list ${flat.length ? "is-slim" : ""} ow-anim" aria-label="${__("Findings")}">${head}
			<div class="ow-l-groups"><i class="ow-l-mark" aria-hidden="true"></i>${list || `<p class="ow-l-void">${st.level === "all" && st.kind === "all" ? __("No other findings. The old open orders are below.") : __("No other finding passes this filter. The old open orders are below.")}</p>`}</div>
			${OW.routine(ctx)}</section>${pane}</div></div>`;
	},

	after(root, ctx) {
		const el = root[0].querySelector(".ow-l"), pane = el && el.querySelector(".ow-l-pane");
		if (!pane) return;
		const st = ctx.st, flat = owListFlat(owListGroups(ctx));
		const box = el.querySelector(".ow-l-groups"), mark = box.querySelector(".ow-l-mark"), body = pane.querySelector(".ow-l-body");
		const pos = pane.querySelector(".ow-l-pos"), steps = pane.querySelectorAll("[data-ow-l-step]"), hits = el.querySelector(".ow-l-hits");
		const rowOf = (i) => box.querySelector(`[data-ow-l-row="${i}"]`);
		const smooth = () => (owListCalm() ? "auto" : "smooth");
		let timer = 0, drawn = -1;

		// previous / next walk the rows the search leaves visible (all rows when the selected one is hidden)
		const lane = () => {
			const seen = flat.filter((f) => !rowOf(f._i).hidden);
			return seen.some((f) => f._i === st.sel) ? seen : flat;
		};
		// glide the selection marker to the selected row
		const place = () => {
			const row = rowOf(st.sel);
			if (!row || row.hidden) return mark.classList.remove("is-on");
			let y = 0;
			for (let n = row; n && n !== box; n = n.offsetParent) y += n.offsetTop;
			owListLevel(mark, flat.find((f) => f._i === st.sel)._lv);
			mark.classList.add("is-on");
			mark.style.height = row.offsetHeight + "px";
			mark.style.transform = `translateY(${y}px)`;
		};
		const sync = () => {
			const l = lane(), at = l.findIndex((f) => f._i === st.sel);
			pos.textContent = __("Finding {0} of {1}", [at + 1, l.length]);
			steps[0].disabled = at <= 0;
			steps[1].disabled = at >= l.length - 1;
			place();
		};
		// select finding i: mark its row, then swap the panel content (dir: -1 came from below, 1 from above)
		const show = (i, dir, now) => {
			const f = flat.find((x) => x._i === i);
			if (!f) return;
			st.sel = i;
			box.querySelectorAll(".ow-l-row").forEach((r) => {
				const on = +r.dataset.owLRow === i;
				r.classList.toggle("is-sel", on);
				r.setAttribute("aria-current", on);
			});
			sync();
			// on a phone the panel is drawn when its sheet opens
			if (drawn === i || (owListPhone() && !pane.classList.contains("is-open"))) return;
			drawn = i;
			clearTimeout(timer);
			const fill = () => {
				pane.classList.remove("is-swap");
				owListLevel(pane, f._lv);
				body.innerHTML = owListDetail(f);
				body.scrollTop = 0;
				OW.animate(pane);
				// blocks below the panel's own scroll edge never count as "in view" for the core: show them once the rest has settled
				timer = setTimeout(() => body.querySelectorAll(".ow-anim:not(.ow-in)").forEach((n) => n.classList.add("ow-in")), 1000);
			};
			if (now || owListCalm()) return fill();
			pane.style.setProperty("--ow-l-dir", dir || 1);
			pane.classList.add("is-swap");
			timer = setTimeout(fill, 170);
		};
		// the phone sheet. The view's entrance animation (core, fill "both") keeps pinning fixed children to the view
		// after it has ended, so the sheet could not fill the screen: the class is dropped once the animation is over.
		root.off("animationend.owList").on("animationend.owList", (e) => { if (e.target === root[0]) root.removeClass("ow-swap"); });
		const open = (on) => {
			if (on === pane.classList.contains("is-open")) return;
			if (on) root.removeClass("ow-swap");
			pane.classList.toggle("is-open", on);
			const to = on ? pane.querySelector(".ow-l-back") : rowOf(st.sel);
			if (to) to.focus({ preventScroll: true });
		};
		const pick = (i, bring) => {
			const dir = flat.findIndex((f) => f._i === i) < flat.findIndex((f) => f._i === st.sel) ? -1 : 1;
			const phone = owListPhone(), closed = phone && !pane.classList.contains("is-open");
			if (phone) open(true);
			show(i, dir, closed);
			if (!bring || phone) return;
			const row = rowOf(i);
			row.scrollIntoView({ block: "center", behavior: smooth() });
			row.classList.add("ow-flash");
			setTimeout(() => row.classList.remove("ow-flash"), 1600);
		};
		const step = (d, focus) => {
			const l = lane(), to = l[l.findIndex((f) => f._i === st.sel) + d];
			if (!to) return;
			show(to._i, d);
			const row = rowOf(to._i);
			if (focus) row.focus();
			else if (!owListPhone()) row.scrollIntoView({ block: "nearest", behavior: smooth() });
		};

		$(el).on("click", "[data-ow-l-row]", (e) => pick(+e.currentTarget.dataset.owLRow))
			.on("click", "[data-ow-l-go]", (e) => pick(+e.currentTarget.dataset.owLGo, true))
			.on("click", "[data-ow-l-step]", (e) => step(+e.currentTarget.dataset.owLStep))
			.on("click", "[data-ow-l-back]", () => open(false));
		pane.addEventListener("keydown", (e) => { if (e.key === "Escape") open(false); });
		box.addEventListener("keydown", (e) => {
			const d = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
			if (!d || owListPhone() || !e.target.closest(".ow-l-row")) return;
			e.preventDefault();
			step(d, true);
		});

		// search as you type: hide what does not match, in the list and in the routine block; nothing is drawn again
		const hay = {};
		flat.concat(ctx.routine).forEach((f) => {
			hay[f._i] = [OW.supplier(f), f.summary, OW.title(f)].concat((f.orders || []).map((o) => [o.name, o.supplier, o.items, o.request].join(" "))).join(" ").toLowerCase();
		});
		el.querySelector("[data-ow-l-q]").addEventListener("input", (e) => {
			const q = e.target.value.trim().toLowerCase(), routine = el.querySelector(".ow-routine");
			let n = 0, old = 0;
			box.querySelectorAll(".ow-l-row").forEach((r) => { r.hidden = !!q && !hay[r.dataset.owLRow].includes(q); n += !r.hidden; });
			box.querySelectorAll(".ow-l-group").forEach((g) => { g.hidden = !g.querySelector(".ow-l-row:not([hidden])"); });
			el.querySelectorAll(".ow-rrow").forEach((r) => { r.hidden = !!q && !hay[r.dataset.owItem].includes(q); old += !r.hidden; });
			if (routine) routine.hidden = !!q && !old;
			el.classList.toggle("is-q", !!q);
			hits.hidden = !q;
			hits.textContent = !q ? "" : !n && !old ? __("Nothing matches. Try a supplier name or an order number.")
				: __("Findings found: {0}", [n]) + (old ? " · " + __("Old open orders found: {0}, in the block below", [old]) : "");
			// the search hid the selected finding: the first match takes its place, so the panel and the counter follow the list
			const sel = rowOf(st.sel), first = box.querySelector(".ow-l-row:not([hidden])");
			if (sel && sel.hidden && first) return show(+first.dataset.owLRow, 1);
			sync();
		});

		if (!flat.some((f) => f._i === st.sel)) st.sel = flat[0]._i;
		show(st.sel, 1, true);
		requestAnimationFrame(() => mark.classList.add("is-live"));
		// the panel's entrance plays once: left on, it would play again each time the window crosses the phone width
		setTimeout(() => pane.classList.remove("is-new"), 1200);
		// rows change height when the window does, and a phone turned sideways may become two panes
		if (window.ResizeObserver) new ResizeObserver(() => (owListPhone() ? place() : show(st.sel, 1, true))).observe(box);
	},
};
