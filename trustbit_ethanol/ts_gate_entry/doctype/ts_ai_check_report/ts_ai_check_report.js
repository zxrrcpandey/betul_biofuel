// AI Checker report: the MD's view of one checked payment list or MST report.
// An MST report's line is one row of a cost sheet: it has no supplier and no trail of ERP documents, so the
// row shows what the sheet says about it (quantity, rate, cost centre, place).
// Everything shown comes from the saved report. Each value is escaped before it goes into HTML, because
// supplier names and remarks are text from the submitted file and the AI's points are text from the AI.

frappe.ui.form.on("TS AI Check Report", {
	refresh(frm) {
		frm.disable_save();
		frm.set_df_property("list_date", "label", frm.doc.check_type === "MST Report" ? __("Month of the report") : __("List date"));
		// one Form object serves every report: the filter and the open lines belong to one report only
		if (!frm._aichk || frm._aichk.name !== frm.doc.name) frm._aichk = { name: frm.doc.name, filter: "all", open: {} };
		aichk_draw(frm);
	},
});

const AICHK_LEVELS = [
	["Serious", "serious", "red"],
	["To check", "check", "orange"],
	["No issue found", "ok", "green"],
];
const AICHK_MARK = { ok: "✓", bad: "✕", warn: "!", na: "–" };

function aichk_draw(frm) {
	const doc = frm.doc;
	const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
	const money = (v) => esc(format_currency(v || 0, frappe.defaults.get_default("currency"), 2));
	// a report made before the checks existed has lines with no verdict: they are shown as not checked
	const key = (verdict) => (AICHK_LEVELS.find((l) => l[0] === verdict) || [0, "none"])[1];
	const colour = (level) => (AICHK_LEVELS.find((l) => l[0] === level) || [0, 0, "blue"])[2];
	const pill = (level) => `<span class="indicator-pill no-indicator-dot ${colour(level)}">${esc(level)}</span>`;
	const state = frm._aichk;
	const mst = doc.check_type === "MST Report";
	// entry motion plays once per report, not again on every filter or line click
	const first = !state.drawn;
	state.drawn = true;
	const lines = doc.lines || [];
	const points = {};
	(doc.findings || []).forEach((f) => (points[cint(f.line_no)] = points[cint(f.line_no)] || []).push(f));
	const total = flt(doc.amt_ok) + flt(doc.amt_check) + flt(doc.amt_serious);
	const share = (v) => (total > 0 ? ((flt(v) / total) * 100).toFixed(2) : 0);
	const count = { serious: cint(doc.n_serious), check: cint(doc.n_check), ok: cint(doc.n_ok) };
	const amount = { serious: doc.amt_serious, check: doc.amt_check, ok: doc.amt_ok };

	const point_html = (f) =>
		`<div class="aichk-pt">${pill(f.level)}<div><b>${esc(f.title)}</b>` +
		(f.source === "AI" ? ` <span class="aichk-ai">${__("AI")}</span>` : "") +
		`<p>${esc(f.detail)}${f.refs ? ` <span class="aichk-refs">${esc(f.refs)}</span>` : ""}</p></div></div>`;

	const detail_html = (line) => {
		let chain = [];
		try {
			chain = JSON.parse(line.chain || "[]");
		} catch (e) {
			chain = [];
		}
		const nodes = chain
			.map(
				(n, i) =>
					`<div class="aichk-node ${esc(n.s)}" style="--i:${i}"><div class="aichk-dot">${AICHK_MARK[n.s] || "–"}</div>` +
					`<b>${esc(n.k)}</b><span>${esc(n.v)}</span></div>`
			)
			.join("");
		const mine = points[cint(line.line_no)] || [];
		const body = mine.length
			? mine.map(point_html).join("")
			: line.verdict
			? `<div class="aichk-pt">${pill("No issue found")}<div><p>${
					mst ? __("The figures of this line agree with each other.") : __("Every link in the trail is present and the amount agrees.")
			  }</p></div></div>`
			: `<p class="aichk-muted">${__("This list was read before the checks existed. Nothing on this line was checked.")}</p>`;
		return (
			`<div class="aichk-det${String(state.just) === String(line.line_no) ? " aichk-pop" : ""}"><div class="aichk-chain">${nodes}</div>` +
			`<div class="aichk-says"><div><b>${mst ? __("The sheet says") : __("The list says")}</b>${esc(line.list_says)}</div>` +
			(mst && !line.erp_says ? "" : `<div><b>${__("The ERP says")}</b>${esc(line.erp_says)}</div>`) +
			`</div>` +
			(mst && line.read_note ? `<p class="aichk-muted">${esc(line.read_note)}</p>` : "") +
			(line.unclear ? `<p class="aichk-muted">${__("Unclear in the file")}: ${esc(line.unclear)}</p>` : "") +
			`<div class="aichk-pts">${body}</div></div>`
		);
	};

	let rows = "";
	let last = null;
	let shown = 0;
	lines.forEach((line) => {
		const k = key(line.verdict);
		if (state.filter !== "all" && state.filter !== k) return;
		shown += 1;
		const section = line.section_text || "";
		if (section !== last && section) {
			const sum = lines.filter((x) => (x.section_text || "") === section).reduce((a, x) => a + flt(x.amount), 0);
			rows += `<div class="aichk-blk"><span>${esc(section)}</span><span>${money(sum)}</span></div>`;
		}
		last = section;
		const open = !!state.open[line.line_no];
		const found = mst
			? esc(line.details_text || "")
			: !line.verdict
			? __("Not checked")
			: line.supplier_title
			? line.supplier_title.toLowerCase() === (line.supplier_text || "").toLowerCase()
				? __("Supplier found")
				: __("ERP") + ": " + esc(line.supplier_title)
			: __("Not in the ERP");
		rows +=
			`<div class="aichk-line ${k}${open ? " open" : ""}" style="--i:${Math.min(shown, 24)}">` +
			`<button type="button" class="aichk-row" data-line="${cint(line.line_no)}" aria-expanded="${open}">` +
			`<span class="aichk-no">${cint(line.line_no)}</span>` +
			`<span class="aichk-name"><b>${esc(line.supplier_text)}</b><span>${found}` +
			`${line.remarks_text ? (found ? " · " : "") + esc(line.remarks_text) : ""}</span></span>` +
			`<span class="aichk-how">${mst ? "" : esc(line.match_how)}</span>` +
			`<span class="aichk-amt">${money(line.amount)}</span>${pill(line.verdict || "Not checked")}` +
			`</button>${open ? detail_html(line) : ""}</div>`;
	});

	const chips = [["all", __("All"), lines.length]]
		.concat(AICHK_LEVELS.map((l) => [l[1], __(l[0]), count[l[1]]]))
		.map(
			(c) =>
				`<button type="button" class="btn btn-xs ${state.filter === c[0] ? "btn-primary" : "btn-default"}" ` +
				`data-filter="${c[0]}">${esc(c[1])} ${cint(c[2])}</button>`
		)
		.join(" ");
	const whole = (points[0] || []).map(point_html).join("");
	// an MST report set against last month's: the heads of both months side by side, as the checker stored them
	let figures = {};
	try {
		figures = JSON.parse(doc.figures || "{}") || {};
	} catch (e) {
		figures = {};
	}
	const trend = figures.trend;
	// what the check cannot tell, as the checker stored it: in bold, straight under the summary
	const limits = Array.isArray(figures.limits) ? figures.limits : [];
	const limits_html = limits.length
		? `<div class="aichk-aisum" style="border-left-color: var(--orange-500)"><b>${__("Limits of this check")}</b>` +
		  limits.map((t) => `<p><strong>${esc(t)}</strong></p>`).join("") +
		  `</div>`
		: "";
	const litre = (v) => (v == null ? "–" : esc(`₹ ${Math.abs(flt(v)) >= 1 ? flt(v).toFixed(2) : flt(v).toFixed(4)}`));
	const change = (r) =>
		r.flag === "gone"
			? __("not in this report")
			: r.flag === "new"
			? __("new")
			: r.change == null
			? r.flag === "moved"
				? __("from nothing")
				: "–"
			: `${r.change > 0 ? "+" : ""}${(r.change * 100).toFixed(0)}%`;
	const flagged = (r) => trend_row(r, ["moved", "gone", "new"].includes(r.flag) ? r.flag : "");
	const trend_row = (r, cls) =>
		`<tr class="${cls}"><td>${esc(r.head)}</td><td>${r.was == null ? "–" : money(r.was)}</td><td>${r.now == null ? "–" : money(r.now)}</td>` +
		(trend.litres ? `<td>${litre(r.was_litre)}</td><td>${litre(r.now_litre)}</td>` : "") +
		`<td>${esc(change(r))}</td></tr>`;
	const per = (i) => (trend.litres && trend.total ? trend.total[i] / trend.litres[i] : null);
	const trend_html =
		trend && (trend.rows || []).length
			? `<div class="aichk-trend"><b>${__("Against the MST report of {0}", [esc(trend.before)])} · ${esc(trend.report)}</b>` +
			  `<table><thead><tr><th>${trend.by === "head" ? __("Head") : __("Sheet")}</th><th>${esc(trend.before)}</th><th>${__("This report")}</th>` +
			  (trend.litres ? `<th>${__("A litre, then")}</th><th>${__("A litre, now")}</th>` : "") +
			  `<th>${__("Change")}</th></tr></thead><tbody>` +
			  trend.rows.filter((r) => !r.outside).map(flagged).join("") +
			  (trend.total_row
					? trend_row(trend.total_row, "total")
					: trend.total
					? trend_row(
							{
								head: __("Total"),
								was: trend.total[0],
								now: trend.total[1],
								was_litre: per(0),
								now_litre: per(1),
								change: trend.litres ? (per(0) ? (per(1) - per(0)) / Math.abs(per(0)) : null) : trend.total[0] ? (trend.total[1] - trend.total[0]) / Math.abs(trend.total[0]) : null,
							},
							"total"
					  )
					: "") +
			  // heads the sheet writes under its total are no part of it
			  trend.rows.filter((r) => r.outside).map(flagged).join("") +
			  `</tbody></table>` +
			  (trend.litres
					? `<p class="aichk-muted">${__("Production: {0} litres then, {1} litres now.", [esc(format_number(trend.litres[0], null, 2)), esc(format_number(trend.litres[1], null, 2))])}</p>`
					: "") +
			  `</div>`
			: "";
	const html =
		`<div class="aichk${first ? " aichk-enter" : ""}">` +
		`<div class="aichk-tiles"><div class="aichk-tile"><div class="v">${lines.length}</div>` +
		`<div class="l">${__("lines")} · ${money(doc.list_total)}</div></div>` +
		AICHK_LEVELS.map(
			(l) => `<div class="aichk-tile ${l[1]}"><div class="v">${count[l[1]]}</div><div class="l">${__(l[0])}</div></div>`
		).join("") +
		`</div>` +
		`<div class="aichk-bar">${["ok", "check", "serious"]
			.map((k) => `<i class="${k}" style="width:${share(amount[k])}%"></i>`)
			.join("")}</div>` +
		`<div class="aichk-legend">${[...AICHK_LEVELS]
			.reverse()
			.map((l) => `<span class="${l[1]}">${__(l[0])} ${money(amount[l[1]])}</span>`)
			.join("")}</div>` +
		`<p class="aichk-sum">${esc(doc.summary)}</p>` +
		limits_html +
		(doc.ai_summary
			? `<div class="aichk-aisum"><b>${__("AI summary")}</b>${esc(doc.ai_summary)}` +
			  `<p class="aichk-muted">${__("Written by the AI. Code makes the verdicts and counts. Code also leaves out an AI point or sentence whose amount or document number is not in the checked facts; the wording is the AI's own.")}</p></div>`
			: "") +
		(doc.ai_note ? `<p class="aichk-muted">${esc(doc.ai_note)}</p>` : "") +
		trend_html +
		(whole ? `<div class="aichk-pts aichk-whole">${whole}</div>` : "") +
		`<div class="aichk-tools">${chips}</div>` +
		`<div class="aichk-lines">${shown ? rows : `<p class="aichk-muted">${__("No line on this list has that verdict.")}</p>`}</div>` +
		`<div class="aichk-cover"><b>${__("What was checked")}</b>${esc([doc.coverage, doc.read_notes].filter(Boolean).join("\n"))
			.split("\n")
			.map((t) => `<p>${t}</p>`)
			.join("")}</div></div>`;

	const $wrap = frm.fields_dict.view.$wrapper;
	$wrap.html(html);
	state.just = null;
	if (first && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
		// the four counts run up to their value; the value itself is already in the page
		$wrap.find(".aichk-tile .v").each(function () {
			const el = this;
			const to = cint(el.textContent);
			const from = performance.now();
			const step = (now) => {
				const k = Math.min((now - from) / 700, 1);
				el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3)));
				if (k < 1 && el.isConnected) requestAnimationFrame(step);
			};
			requestAnimationFrame(step);
		});
	}
	$wrap.off("click.aichk");
	$wrap.on("click.aichk", "[data-filter]", function () {
		state.filter = $(this).attr("data-filter");
		aichk_draw(frm);
	});
	$wrap.on("click.aichk", "[data-line]", function () {
		const no = $(this).attr("data-line");
		state.open[no] = !state.open[no];
		state.just = state.open[no] ? no : null;
		aichk_draw(frm);
		$wrap.find(`[data-line="${cint(no)}"]`).trigger("focus");
	});
}
