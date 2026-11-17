// ---- Cards view: an inbox of findings. Four tiles, a "start here" strip, the kind chips, then one card per
// finding under its level, the routine old orders folded at the end. Every data value goes through OW.esc(). ----
const owCardsDates = (f) => {
	const at = (f.orders || []).map((o) => o.entered_at).filter(Boolean).sort();
	if (!at.length) return "";
	const a = moment(at[0]), b = moment(at[at.length - 1]);
	if (a.isSame(b, "day")) return OW.day(a);
	return __("{0} to {1}", [a.format(a.isSame(b, "year") ? "D MMM" : "D MMM YYYY"), OW.day(b)]);
};
const owCardsLabel = (f, open) => {
	const n = (f.orders || []).length, m = (f.facts || []).length;
	if (open) return n > 1 ? __("Hide the orders and facts") : __("Hide the order and facts");
	if (n > 1) return m > 1 ? __("Show the {0} orders and {1} facts", [n, m]) : m ? __("Show the {0} orders and 1 fact", [n]) : __("Show the {0} orders", [n]);
	return m > 1 ? __("Show the order and {0} facts", [m]) : m ? __("Show the order and 1 fact") : __("Show the order");
};
// right side of a card: the amount, how far it is from its line, the bar; without a line, one fact instead
const owCardsFigure = (f) => {
	const os = f.orders || [], split = f.kind === "split" && os.length > 1, bar = OW.bar(f), gap = OW.gap(f);
	const name = split ? __("{0} orders together", [os.length]) : f.kind === "more" ? __("received") : ["near", "over", "self", "open"].includes(f.kind) ? __("order value") : __("amount");
	const amt = f.total == null ? "" : `<div class="ow-c-amt"><b>${OW.num(f.total, "inr")}</b><span>${OW.esc(name)}</span></div>`;
	const fact = bar ? "" : (f.facts || [])[0] || (os[0] && os[0].received ? __("Received: {0}", [os[0].received]) : "");
	let under = "";
	if (bar && split) under = `<ul class="ow-c-parts" aria-label="${OW.esc(__("Value of each order"))}">${os.map((o, i) => `<li style="--i:${i}">${OW.esc(OW.short(o.value))}</li>`).join("")}</ul>`;
	else if (fact) under = `<div class="ow-c-fact">${OW.kindIcon(f)}<span>${OW.esc(fact)}</span></div>`;
	if (!amt && !under) return "";
	return `<div class="ow-c-fig">${amt}${gap ? `<div class="ow-c-gap">${OW.esc(gap)}</div>` : ""}${bar}${under}</div>`;
};
const owCardsCard = (f, here) => {
	const n = (f.orders || []).length, fig = owCardsFigure(f), when = owCardsDates(f), sup = OW.supplier(f);
	return `<article class="ow-item ow-c-card ow-lv-${f._lv} ow-anim" data-ow-item="${f._i}">
		${here ? `<span class="ow-c-here">${OW.icon("down")}${__("Start here")}</span>` : ""}
		<div class="ow-c-shell"><div class="ow-c-main ${fig ? "" : "is-plain"}"><div class="ow-c-text">
			<div class="ow-c-top">${OW.pill(f.level)}<span class="ow-c-kic">${OW.kindIcon(f)}</span><h4>${OW.esc(OW.title(f))}</h4></div>
			<div class="ow-c-say">${OW.esc(f.summary)}</div>
			<div class="ow-c-meta">${sup ? `<b>${OW.esc(sup)}</b>` : ""}${f.company ? `<span>${OW.esc(f.company)}</span>` : ""}
				<span>${OW.icon("table")}${OW.esc(n === 1 ? __("1 order") : __("{0} orders", [n]))}</span>${when ? `<span>${OW.icon("clock")}${OW.esc(when)}</span>` : ""}</div>
		</div>${fig}</div>${OW.toggle(f, owCardsLabel(f))}${OW.fold(f)}</div></article>`;
};
const owCardsMoney = (money) => {
	const s = money.Serious || 0, c = money["To check"] || 0, sum = s + c;
	const share = sum > 0 ? `<div class="ow-c-share" aria-hidden="true"><i class="ow-lv-serious" style="--w:${((s / sum) * 100).toFixed(1)}%"></i><i class="ow-lv-check" style="--w:${((c / sum) * 100).toFixed(1)}%"></i></div>` : "";
	return `<div class="ow-c-money ow-anim"><span class="ow-c-money-k">${OW.icon("flag")}${__("Money in question")}</span><b>${OW.num(money.both == null ? sum : money.both, "short")}</b>${share}
		<span class="ow-c-money-h">${__("Serious and To check orders, each counted once.")}</span></div>`;
};
// the living strip under the tiles: where to begin (first is the first Serious finding, else the first To check)
const owCardsStart = (ctx, first) => {
	if (!first) return `<div class="ow-c-start is-calm ow-lv-note ow-anim"><span class="ow-c-beacon">${OW.icon("done")}</span>
		<div class="ow-c-start-text"><b>${__("Nothing serious in this period.")}</b> ${__("The notes below are mostly routine.")}</div></div>`;
	const serious = first.level === "Serious", n = ctx.counts[first.level], worth = (ctx.data.money || {})[first.level];
	const text = serious ? (n === 1 ? __("1 serious finding is at the top of the list.") : __("{0} serious findings are at the top of the list.", [n]))
		: n === 1 ? __("1 finding is worth a look.") : __("{0} findings are worth a look.", [n]);
	return `<div class="ow-c-start ow-lv-${first._lv} ow-anim"><span class="ow-c-beacon">${OW.icon("flag")}</span>
		<div class="ow-c-start-text"><b>${serious ? __("Start here.") : __("Nothing serious in this period.")}</b> ${OW.esc(text)} ${worth ? OW.esc(__("The orders involved come to {0}.", [OW.short(worth)])) : ""} ${__("Each one is a question to ask, not a conclusion.")}</div>
		<button type="button" class="ow-c-go" data-ow-jump="${first._i}"><span>${serious ? __("Open the first serious finding") : __("Open the first finding to check")}</span>${OW.icon("arrow")}</button></div>`;
};
const owCardsResults = (ctx) => {
	const st = ctx.st, n = ctx.shown.length + ctx.routine.length, on = [];
	if (st.level !== "all") on.push(__(st.level));
	if (st.kind !== "all") on.push((OW.KINDS[st.kind] || OW.KINDS.other).title);
	if (!on.length) return `<div class="ow-c-results ow-anim"><span>${__("All {0} findings. Serious first, then To check, then Note.", [OW.num(n)])}</span></div>`;
	return `<div class="ow-c-results ow-anim"><span>${__("Showing {0} of {1} findings: {2}.", [OW.num(n), ctx.all.length, OW.esc(on.join(", "))])}</span>
		<button type="button" class="ow-c-clear" data-ow-level="all" data-ow-kind="all">${__("Show everything")}</button></div>`;
};
const owCardsEmpty = (st) => {
	const text = st.level !== "all" && st.kind !== "all" ? __("Nothing at this level of this kind.") : st.kind !== "all" ? __("Nothing of this kind.") : __("Nothing at this level.");
	return `<div class="ow-c-empty ow-anim">${OW.icon("done", "ow-state-ic")}<b>${text}</b>
		<button type="button" class="ow-c-clear" data-ow-level="all" data-ow-kind="all">${__("Show everything")}</button></div>`;
};

OW.views.cards = {
	label: __("Cards"),
	hint: __("One card for each finding, the most serious first"),
	render(ctx) {
		const money = ctx.data.money || {};
		const first = ctx.all.find((f) => f.level === "Serious") || ctx.all.find((f) => f.level === "To check");
		const tiles = OW.LEVELS.map((l) => OW.counter(l, ctx, money[l] ? __("{0} in these orders", [OW.short(money[l])]) : "")).join("");
		const feed = OW.LEVELS.map((level) => {
			const list = ctx.shown.filter((f) => f.level === level), routine = level === "Note" ? OW.routine(ctx) : "";
			if (!list.length && !routine) return "";
			return `<section class="ow-c-sec ow-lv-${OW.LV[level]}"><header class="ow-c-sec-h ow-anim"><span class="ow-c-sec-ic">${OW.icon(OW.LV[level])}</span>
				<h4>${OW.esc(__(level))}</h4><i class="ow-c-sec-h-n">${OW.num(list.length + (routine ? ctx.routine.length : 0))}</i><span>${OW.esc(OW.LEVEL_HINT[level])}</span></header>
				<div class="ow-c-list">${list.map((f) => owCardsCard(f, first && f._i === first._i)).join("")}${routine}</div></section>`;
		}).join("");
		return `<div class="ow-c-tiles">${tiles}${owCardsMoney(money)}</div>${owCardsStart(ctx, first)}
			<div class="ow-c-kinds"><span class="ow-c-kinds-l">${__("Kind of finding")}</span>${OW.kindChips(ctx)}</div>
			${feed ? owCardsResults(ctx) + feed : owCardsEmpty(ctx.st)}`;
	},
	after(root, ctx) {
		// the core opens and closes the fold; here only the words on the card's button follow it
		const sync = () => root.find(".ow-c-shell > [data-ow-toggle]").each((n, b) => {
			const f = ctx.all[+b.dataset.owToggle];
			if (f) b.firstElementChild.textContent = owCardsLabel(f, b.getAttribute("aria-expanded") === "true");
		});
		root.find(".ow-chip").each((n, el) => el.style.setProperty("--i", n));
		let frame = 0;
		// .ow-view outlives every repaint and view switch: one set of handlers, bound again with the new ctx
		root.off(".owCards").on("click.owCards", "[data-ow-toggle], [data-ow-jump]", () => setTimeout(sync, 0))
			.on("pointermove.owCards", ".ow-c-shell, .ow-counter, .ow-c-money", (e) => {
				if (frame || (e.originalEvent || e).pointerType !== "mouse" || !root.hasClass("ow-view-cards")) return;
				const el = e.currentTarget, x = e.clientX, y = e.clientY;
				frame = requestAnimationFrame(() => {
					frame = 0;
					const r = el.getBoundingClientRect();
					if (el.classList.contains("ow-c-shell")) {
						// a soft light follows the pointer over a card
						el.style.setProperty("--mx", (x - r.left).toFixed(0) + "px");
						el.style.setProperty("--my", (y - r.top).toFixed(0) + "px");
					} else {
						// the tiles lean towards the pointer
						el.style.setProperty("--ry", (((x - r.left) / r.width - 0.5) * 9).toFixed(2) + "deg");
						el.style.setProperty("--rx", ((0.5 - (y - r.top) / r.height) * 9).toFixed(2) + "deg");
					}
				});
			});
	},
};
