// Brief view: the MD's brief. "Start here", the six questions with their answers, then the findings
// under each question. Every finding row opens the shared fold (the core fills and animates it).
const owBriefQs = [
	{ kinds: ["split", "near"], word: __("Possibly"), q: __("Were orders split to stay under my line?"),
		none: __("No group of small orders passes a line, and no order sits just under it."),
		say: (c) => [c("split") && __("Groups of small orders that pass a line only when added together: {0}.", [c("split")]), c("near") && __("Single orders just under the line: {0}.", [c("near")])] },
	{ kinds: ["over"], q: __("Did anything at or over my line go ahead without me?"),
		none: __("Every order at or over the line has the MD's approval."),
		say: (c) => [__("Orders at or over the line with no MD approval: {0}.", [c("over")])] },
	{ kinds: ["self"], q: __("Did anyone approve their own order?"),
		none: __("No order was entered and approved by the same account."),
		say: (c) => [__("Orders where the account that entered the order also held an approval or review step: {0}.", [c("self")])] },
	{ kinds: ["more", "allowance"], q: __("Did we receive more than was approved?"),
		none: __("Nothing was received above the approved value."),
		say: (c) => [c("more") && __("Orders received above the approved value: {0}.", [c("more")]), c("allowance") && __("Receipt allowances raised shortly before a receipt: {0}.", [c("allowance")])] },
	{ kinds: ["early"], q: __("Did goods arrive before the order?"),
		none: __("No goods were recorded before their order."),
		say: (c) => [__("Orders with goods or a truck recorded before the order or its approval: {0}.", [c("early")])] },
	{ kinds: ["open"], open: true, q: __("Which approved orders are still lying open?"),
		none: __("No approved order is lying open."),
		say: (c, s) => [__("Approved orders with nothing received after 60 days: {0}.", [s.n]), s.n > s.Note ? __("Worth a question: {0}.", [s.n - s.Note]) + (s.Note ? " " + __("The others are notes.") : "") : __("All are notes.")] },
	{ kinds: [], extra: true, q: __("Other findings"), none: "",
		say: (c, s) => [__("Findings that fit none of the six questions: {0}.", [s.n])] },
];
const owBriefKnown = owBriefQs.reduce((all, q) => all.concat(q.kinds), []);
// the place of the level filter before this draw, so its marker can slide from there
const owBriefMem = { at: 0 };

// a finding belongs to every question one of its reasons answers (a finding can carry two reasons);
// "Other findings" takes the ones none of the six questions name
function owBriefHas(q, f) {
	return q.extra ? !f.kinds.some((k) => owBriefKnown.includes(k)) : f.kinds.some((k) => q.kinds.includes(k));
}
// counts of one question over ALL findings (not the filtered ones), each finding once, and its highest level
function owBriefStat(q, ctx) {
	const s = { n: 0, Serious: 0, "To check": 0, Note: 0 };
	ctx.all.filter((f) => owBriefHas(q, f)).forEach((f) => { s.n++; s[f.level]++; });
	s.top = OW.LEVELS.find((l) => s[l]) || "";
	return s;
}
function owBriefSay(q, ctx, s) {
	return s.n ? q.say((kind) => (ctx.counts.kinds[kind] || { n: 0 }).n, s).filter(Boolean).join(" ") : q.none;
}
function owBriefAnswer(q, s) {
	const word = !s.n ? (q.open ? __("None") : __("No")) : q.open ? __("{0} open", [s.n]) : q.extra ? __("{0} found", [s.n]) : q.word || __("Yes");
	return `<span class="ow-b-ans ${s.top ? "ow-lv-" + OW.LV[s.top] : "is-none"}">${OW.icon(s.top ? OW.LV[s.top] : "done")}<span>${OW.esc(word)}</span></span>`;
}
function owBriefCounts(s, extra) {
	const words = { Serious: __("{0} serious", [s.Serious]), "To check": __("{0} to check", [s["To check"]]), Note: s.Note === 1 ? __("1 note") : __("{0} notes", [s.Note]) };
	const chips = OW.LEVELS.filter((l) => s[l]).map((l) => `<span class="ow-b-count ow-lv-${OW.LV[l]}">${OW.icon(OW.LV[l])}${OW.esc(words[l])}</span>`).join("");
	return `<span class="ow-b-counts">${chips || `<span class="ow-b-count is-none">${OW.icon("done")}${__("Nothing found")}</span>`}${extra || ""}</span>`;
}

// ---- top: where to start, and what is in the brief ----
function owBriefStart(ctx) {
	const top = ctx.all.filter((f) => f.level !== "Note").sort((a, b) => OW.LEVELS.indexOf(a.level) - OW.LEVELS.indexOf(b.level)).slice(0, 3);
	const money = (ctx.data.money || {}).Serious || 0, lv = ctx.counts.Serious ? "serious" : top.length ? "check" : "note";
	const sum = ctx.counts.Serious ? __("{0} serious, {1} in question", [`<b>${OW.num(ctx.counts.Serious)}</b>`, `<b>${OW.num(money, "short")}</b>`])
		: top.length ? __("No serious findings. These are the first items to check.") : __("Nothing serious and nothing to check: only notes for these dates.");
	const items = top.map((f, n) => {
		const clause = [(f.orders || []).length > 1 ? __("{0} orders", [f.orders.length]) : "", OW.gap(f)].filter(Boolean).join(" · ");
		return `<li class="ow-anim"><button type="button" class="ow-b-start-item ow-lv-${f._lv}" data-ow-jump="${f._i}">
			<i class="ow-b-n">${n + 1}</i><span class="ow-b-start-lvl">${OW.pill(f.level)}</span>
			<span class="ow-b-start-text"><small>${OW.esc(OW.title(f))}</small><span><b>${OW.esc(OW.supplier(f))}</b>${clause ? " · " + OW.esc(clause) : ""}</span></span>
			<b class="ow-b-start-amt">${f.total == null ? "" : OW.num(f.total, "inr")}</b>${OW.icon("arrow")}</button></li>`;
	}).join("");
	return `<section class="ow-b-card ow-b-start ow-lv-${lv} ow-anim"><div class="ow-b-start-head"><span class="ow-b-flag">${OW.icon("flag")}</span>
		<h4 class="ow-b-h">${__("Start here")}</h4><p>${sum}</p></div>
		${items ? `<ol class="ow-b-start-list">${items}</ol><small class="ow-b-start-foot">${__("Each line opens its finding below, with the orders and the facts behind it.")}</small>`
			: `<p class="ow-b-none">${OW.icon("done")}${__("The notes are listed under the questions below.")}</p>`}</section>`;
}
function owBriefTally(ctx) {
	const d = ctx.data, m = d.money || {}, both = m.both || 0, share = both ? Math.round(((m.Serious || 0) / both) * 100) : 0;
	const rows = OW.LEVELS.map((l) => {
		const on = ctx.st.level === l;
		const side = m[l] ? `<b>${OW.num(m[l], "short")}</b><small>${__("in question")}</small>` : l === "Note" ? `<small>${__("kept for the record")}</small>` : "";
		return `<button type="button" class="ow-b-tally-row ow-lv-${OW.LV[l]} ow-anim ${on ? "is-on" : ""}" data-ow-level="${on ? "all" : l}" aria-pressed="${on}" title="${on ? __("Show all levels") : __("Show only this level")}">
			<b class="ow-b-tally-n">${OW.num(ctx.counts[l])}</b><span class="ow-b-tally-l">${OW.pill(l)}<small>${OW.esc(OW.LEVEL_HINT[l])}</small></span><span class="ow-b-tally-m">${side}</span></button>`;
	}).join("");
	const total = both ? `<div class="ow-b-money ow-anim"><span>${__("Money in question")}</span><b>${OW.num(both, "short")}</b>
		<i class="ow-b-meter" style="--s:${share}%"><i class="ow-lv-serious"></i><i class="ow-lv-check"></i></i>
		<small>${__("Orders under Serious and To check findings, each order counted once.")}</small></div>` : "";
	return `<section class="ow-b-card ow-b-tally ow-anim"><h4 class="ow-b-h">${__("In this brief")}</h4>
		<small>${OW.esc(__("{0} to {1}", [OW.day(d.from_date), OW.day(d.to_date)]))}${d.company ? " · " + OW.esc(d.company) : ""}</small>
		<div class="ow-b-tally-rows">${rows}</div>${total}</section>`;
}

// ---- the six questions, as cards that lead to their sections ----
function owBriefIndex(ctx) {
	const other = owBriefStat(owBriefQs[6], ctx).n;
	const cards = owBriefQs.slice(0, 6).map((q, i) => {
		const s = owBriefStat(q, ctx);
		return `<div class="ow-b-qwrap ow-anim"><button type="button" class="ow-b-qcard ${s.top ? "ow-lv-" + OW.LV[s.top] : "is-none"}" data-ow-b-goto="${i}">
			<span class="ow-b-qtop"><i class="ow-b-n">${i + 1}</i><span class="ow-b-qtext">${OW.esc(q.q)}</span></span>
			<span class="ow-b-say">${owBriefAnswer(q, s)}<span>${OW.esc(owBriefSay(q, ctx, s))}</span></span>
			<span class="ow-b-qfoot">${owBriefCounts(s)}<span class="ow-b-go">${OW.icon("down")}</span></span></button></div>`;
	}).join("");
	return `<section class="ow-b-index"><div class="ow-b-index-head ow-anim"><h4 class="ow-b-h">${__("Your six questions")}</h4>
		<p>${__("Each answer comes from the ERP records. Pick a question to see the orders behind it.")}${other ? ` <button type="button" class="ow-link" data-ow-b-goto="6">${__("Plus other findings: {0}", [other])}</button>` : ""}</p></div>
		<div class="ow-b-qgrid">${cards}</div></section>`;
}

// ---- the level filter: one marker that slides to the chosen level ----
function owBriefShow(ctx) {
	const opts = [["all", __("All"), ctx.all.length, ""]].concat(OW.LEVELS.map((l) => [l, l === "Note" ? __("Notes") : __(l), ctx.counts[l], OW.LV[l]]));
	const at = Math.max(0, opts.findIndex((o) => o[0] === ctx.st.level)), from = owBriefMem.at;
	owBriefMem.at = at;
	const btns = opts.map((o, i) => `<button type="button" class="${o[3] ? "ow-lv-" + o[3] : ""} ${i === at ? "is-on" : ""}" data-ow-level="${o[0]}" aria-pressed="${i === at}">${o[3] ? OW.icon(o[3]) : ""}<span>${OW.esc(o[1])}</span><b>${o[2]}</b></button>`).join("");
	const kind = ctx.st.kind === "all" ? "" : `<button type="button" class="ow-b-only" data-ow-kind="all" title="${__("Show all kinds")}">${OW.esc(__("Only: {0}", [(OW.KINDS[ctx.st.kind] || OW.KINDS.other).title]))}<b>×</b></button>`;
	return `<div class="ow-b-show"><span class="ow-b-show-l">${__("Show")}</span>
		<div class="ow-b-seg" role="group" aria-label="${__("Level")}" style="--at:${at};--from:${from}"><i class="ow-b-thumb ${opts[at][3] ? "ow-lv-" + opts[at][3] : "is-none"}"></i>${btns}</div>${kind}
		<span class="ow-b-legend"><i></i>${__("Bars: the upright tick is the line the amount is compared with.")}</span></div>`;
}

// ---- one section per question, its findings as compact rows ----
function owBriefRow(f) {
	const gap = OW.gap(f);
	return `<div class="ow-item ow-b-row ow-lv-${f._lv} ow-anim" data-ow-item="${f._i}"><button type="button" class="ow-b-row-head" data-ow-toggle="${f._i}" aria-expanded="false">
		<span class="ow-b-row-lvl">${OW.pill(f.level)}</span>
		<span class="ow-b-row-main"><span class="ow-b-kind">${OW.kindIcon(f)}${OW.esc(OW.title(f))}</span><b class="ow-b-sup">${OW.esc(OW.supplier(f))}</b><span class="ow-b-sent">${OW.esc(f.summary)}</span></span>
		<span class="ow-b-row-amt">${f.total == null ? `<small>${__("No amount for this check")}</small>` : `<b>${OW.num(f.total, "inr")}</b>`}${gap ? `<small>${OW.esc(gap)}</small>` : ""}</span>
		<span class="ow-b-row-bar">${OW.bar(f, { plain: true })}</span>${OW.icon("chevron")}</button>${OW.fold(f)}</div>`;
}
function owBriefSection(q, i, ctx, filtered) {
	const s = owBriefStat(q, ctx), rows = ctx.shown.filter((f) => owBriefHas(q, f)), routine = q.open ? OW.routine(ctx) : "";
	if (!rows.length && !routine && (filtered || q.extra)) return "";
	const part = filtered ? `<span class="ow-b-count ow-b-part">${OW.esc(__("Showing {0} of {1}", [rows.length + (q.open ? ctx.routine.length : 0), s.n]))}</span>` : "";
	const none = rows.length || routine ? "" : `<p class="ow-b-none">${OW.icon("done")}${__("Nothing found.")}</p>`;
	return `<section class="ow-b-card ow-b-sec ${s.top ? "ow-lv-" + OW.LV[s.top] : "is-none"} ow-anim" data-ow-b-sec="${i}">
		<header class="ow-b-sec-head"><i class="ow-b-n">${q.extra ? "+" : i + 1}</i>
			<div><h4>${OW.esc(q.q)}</h4><p class="ow-b-say">${owBriefAnswer(q, s)}<span>${OW.esc(owBriefSay(q, ctx, s))}</span></p></div>${owBriefCounts(s, part)}</header>
		<div class="ow-b-rows">${rows.map(owBriefRow).join("")}</div>${none}${routine}</section>`;
}

OW.views.brief = {
	label: __("Brief"),
	hint: __("The MD's brief: where to start, six questions and their answers, then the findings under each question"),
	render(ctx) {
		const filtered = ctx.st.level !== "all" || ctx.st.kind !== "all";
		const secs = owBriefQs.map((q, i) => owBriefSection(q, i, ctx, filtered)).join("");
		// one button clears both filters: the core runs its level and its kind handler on the same click
		const nothing = `<div class="ow-b-card ow-b-nothing ow-anim">${OW.icon("done")}<p>${__("Nothing at this level or of this kind.")}</p>
			<button type="button" class="ow-b-btn" data-ow-level="all" data-ow-kind="all">${__("Show everything")}</button></div>`;
		return `<div class="ow-b-root"><div class="ow-b-top">${owBriefStart(ctx)}${owBriefTally(ctx)}</div>${owBriefIndex(ctx)}${owBriefShow(ctx)}${secs || nothing}</div>`;
	},
	after(root, ctx) {
		const el = root[0] || root, still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
		const sec = (i) => el.querySelector(`[data-ow-b-sec="${i}"]`);
		const press = (sel) => { const b = el.querySelector(sel); if (b) b.click(); };
		el.querySelectorAll("[data-ow-b-goto]").forEach((b) => b.addEventListener("click", () => {
			const i = Number(b.dataset.owBGoto);
			// its section is hidden by a filter: clear the filters first (the core draws the view again)
			if (!sec(i)) { press(".ow-b-only"); if (ctx.st.level !== "all") press('.ow-b-seg [data-ow-level="all"]'); }
			const to = sec(i);
			if (!to) return;
			to.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
			to.classList.add("ow-b-hit");
			setTimeout(() => to.classList.remove("ow-b-hit"), 1600);
		}));
		if (still || !window.matchMedia("(hover: hover)").matches) return;
		// the question cards lean towards the pointer, with a soft light under it
		el.querySelectorAll(".ow-b-qcard").forEach((c) => {
			c.addEventListener("pointermove", (e) => {
				const r = c.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
				c.style.setProperty("--rx", ((0.5 - y) * 7).toFixed(2) + "deg");
				c.style.setProperty("--ry", ((x - 0.5) * 8).toFixed(2) + "deg");
				c.style.setProperty("--gx", (x * 100).toFixed(1) + "%");
				c.style.setProperty("--gy", (y * 100).toFixed(1) + "%");
			});
			c.addEventListener("pointerleave", () => ["--rx", "--ry"].forEach((p) => c.style.removeProperty(p)));
		});
	},
};
