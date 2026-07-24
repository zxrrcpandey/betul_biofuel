/* ═══════════════════════════════════════════════════════════════════
   Usage Report — who is using the system, how much, and when.
   Spec: PLAN_user_activity_usage_report.md v2 (approved 24 Jul 2026).
   NC page idiom MINUS its realtime machinery: data loads on page load,
   filter change, and the manual Refresh button ONLY (no realtime.on,
   no setInterval). Every section renders fail-soft with console.error
   (L311). Failed sources render "n/a" — never a fabricated 0.
   ═══════════════════════════════════════════════════════════════════ */

const UR_VERSION = "v2.1-2026-07-24";
const UR_ACT_METHOD = "trustbit_ethanol.ts_gate_entry.ts_usage_report_api.get_user_activity";
const UR_METHOD = "trustbit_ethanol.ts_gate_entry.ts_usage_report_api.get_usage_report";
const UR_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
console.log("[usage-report]", UR_VERSION, "loaded");

let _ur = { data: null, loaded_at: 0, fields: {}, applying: false, sort: { key: null, dir: -1 }, sessions_shown: 5,
	activity: { user: null, from: null, to: null, rows: [], cursor: null, first_page: null, shown: 5, loading: false, facet: null } };

frappe.pages["usage-report"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "Usage Report", single_column: true });
	if (!document.getElementById("ur-styles")) {
		const s = document.createElement("style");
		s.id = "ur-styles"; s.textContent = UR_CSS;
		document.head.appendChild(s);
	} else {
		document.getElementById("ur-styles").textContent = UR_CSS;
	}
	const f = _ur.fields;
	f.preset = page.add_field({ fieldname: "preset", label: __("Range"), fieldtype: "Select",
		options: ["Today", "This Week", "This Month", "Last 7 days", "Last 30 days", "Last 90 days", "Custom"].join("\n"),
		default: "Last 30 days", change: () => { if (!_ur.applying) _ur_apply_preset().then(() => _ur_load()); } });
	f.from = page.add_field({ fieldname: "from_date", label: __("From"), fieldtype: "Date",
		change: () => { if (_ur.applying) return; f.preset.set_value("Custom"); _ur_load(); } });
	f.to = page.add_field({ fieldname: "to_date", label: __("To"), fieldtype: "Date",
		change: () => { if (_ur.applying) return; f.preset.set_value("Custom"); _ur_load(); } });
	f.user = page.add_field({ fieldname: "user", label: __("User"), fieldtype: "Link", options: "User",
		change: () => { if (!_ur.applying) _ur_load(); } });
	f.department = page.add_field({ fieldname: "department", label: __("Department"), fieldtype: "Select",
		options: "", change: () => { if (!_ur.applying) _ur_load(); } });
	f.flow = page.add_field({ fieldname: "flow", label: __("Flow"), fieldtype: "Select",
		options: "", change: () => { if (!_ur.applying) _ur_load(); } });
	page.add_button(__("Refresh"), () => _ur_load(), { icon: "refresh" });
	// APPEND, never page.main.html(): page.add_field renders the filter strip
	// into page.page_form, which frappe PREPENDS INSIDE page.main — .html()
	// wipes the just-created filters out of the DOM (Lesson 325). Static
	// header + #ur-filters slot render once; only #ur-body re-renders, so
	// the relocated page_form node survives every reload.
	page.main.find("#ur-container").remove();
	page.main.append(`<div id="ur-container">
		<div class="ur-head"><span class="ur-chip" aria-hidden="true">📊</span>
			<div><div class="ur-title">${__("System Usage & User Activity")}</div>
			<div class="ur-sub"><span id="ur-range-sub">${__("Loading…")}</span> · <span class="ur-ver">${UR_VERSION}</span></div></div></div>
		<div id="ur-filters"></div>
		<div id="ur-body"><div class="ur-loading">${__("Loading Usage Report…")}</div></div>
	</div>`);
	$("#ur-filters").append(page.page_form); // physically move the toolbar below the header
	$(`<button class="ur-resetbtn" title="${__("Reset all filters and the date range")}">↺ ${__("Clear Filters")}</button>`)
		.appendTo($("#ur-filters .page-form")).on("click", _ur_reset);
	_ur_apply_preset().then(() => _ur_load());
};
/* route revisit: re-render from the last response if fresh (<60s), else re-query */
frappe.pages["usage-report"].refresh = () => {
	if (_ur.data && Date.now() - _ur.loaded_at < 60000) _ur_render();
	else if (document.getElementById("ur-container")) _ur_load();
};

function _esc(v) { return frappe.utils.escape_html(String(v == null ? "" : v)); }
function _num(v) { return v == null ? `<span class="ur-na" title="${__("source unavailable")}">n/a</span>` : format_number(v, null, 0); }

function _ur_apply_preset() {
	// programmatic set_value fires the date fields' change handlers — the
	// `applying` flag (cleared only after the returned promises settle, since
	// frappe.run_serially defers the callbacks) stops the preset snapping to
	// "Custom" and the duplicate loads that caused (ui-designer F1)
	const f = _ur.fields, t = frappe.datetime.get_today(), p = f.preset.get_value();
	let from = null;
	if (p === "This Week") {
		// Monday-start week, matching the house _resolve_dates idiom
		from = frappe.datetime.add_days(t, -((new Date(t + "T00:00:00").getDay() + 6) % 7));
	} else if (p === "This Month") {
		from = t.substring(0, 8) + "01";
	} else {
		const days = { "Today": 0, "Last 7 days": 6, "Last 30 days": 29, "Last 90 days": 89 }[p];
		if (days != null) from = frappe.datetime.add_days(t, -days);
	}
	if (from == null) return Promise.resolve(); // "Custom" — user drives the date pickers
	_ur.applying = true;
	return Promise.all([
		f.from.set_value(from),
		f.to.set_value(t),
	]).finally(() => { _ur.applying = false; });
}

async function _ur_load() {
	const f = _ur.fields;
	if (!f.from.get_value() || !f.to.get_value()) return;
	$("#ur-body").addClass("ur-busy");
	try {
		const r = await frappe.call({ method: UR_METHOD, args: {
			from_date: f.from.get_value(), to_date: f.to.get_value(),
			user: f.user.get_value() || null,
			department: f.department.get_value() || null,
			flow: f.flow.get_value() || null,
		} });
		_ur.data = r.message; _ur.loaded_at = Date.now(); _ur.sessions_shown = 5;
		_ur_fill_selects();
		_ur_render();
	} catch (e) {
		console.error(e);
		if (!(e && e._server_messages)) {
			$("#ur-body").html(`<div class="ur-error">${__("Couldn't load the Usage Report.")}</div>`);
		}
	} finally {
		$("#ur-body").removeClass("ur-busy");
	}
}
function _ur_fill_selects() {
	const m = _ur.data.meta, f = _ur.fields;
	for (const [fld, vals] of [[f.department, m.dept_buckets], [f.flow, m.flows]]) {
		if (fld.df.options) continue;
		const cur = fld.get_value();
		fld.df.options = [""].concat(vals).join("\n");
		fld.refresh(); fld.set_value(cur || "");
	}
}

function _ur_render() {
	const d = _ur.data;
	$("#ur-range-sub").text(`${d.meta.from_date} → ${d.meta.to_date}`);
	$("#ur-body").html(`
		<div id="ur-banner"></div>
		<div id="ur-chips"></div>
		<section class="ur-section"><ul class="ur-cards" id="ur-cards"></ul></section>
		<section class="ur-section"><h3>${__("By Department")}</h3><div class="ur-scroll" id="ur-depts"></div></section>
		<section class="ur-section"><h3>${__("Per User")}</h3><div class="ur-scroll" id="ur-users"></div></section>
		<section class="ur-section"><h3>${__("Activity Heatmap")}</h3><div class="ur-scroll" id="ur-heat"></div>
			<div class="ur-hint">${__("Recorded actions (entries + approval decisions + logins) per hour, IST. Login stream limited to the last 90 days; a small number of date-only events are not shown.")}</div></section>
		<section class="ur-section"><h3>${__("Login / Logout Sessions")}</h3><div class="ur-scroll" id="ur-sessions"></div>
			<div class="ur-hint">${__("One row per session. \"auto\" = closed by the midnight session-expiry sweep (the user stopped earlier, so no duration is claimed). Logout \"—\" = browser closed without logging out. History limited to the last 90 days.")}</div></section>
		<section class="ur-section" id="ur-activity-sec"><h3>${__("Changes & Creations")}</h3><div id="ur-activity"></div>
			<details class="ur-hint"><summary>${__("What this shows / doesn't show")}</summary>${__("Edits, creations and approval decisions only — views are never recorded. Only tracked doctypes appear; anything else shows nothing here regardless of real activity. Material Request edits are not audit-tracked. Saves are grouped per document per day (see save counts). System-marked writes are badged separately, but hook-driven saves without markers still attribute to the acting user. Units differ from the counts above and will not reconcile. Edit counts are not effort — this is an audit trail, not a productivity score.")}</details></section>
		<section class="ur-section"><h3>${__("By Flow")}</h3><div class="ur-scroll" id="ur-flows"></div></section>
		<section class="ur-section"><h3>${__("Inactive Users")}</h3><div id="ur-inactive"></div></section>
		<div class="ur-method">${__("Methodology: this page counts recorded actions — it cannot measure hours worked, off-system work, or presence, and must not be read as a performance score. Failed logins are not attributable to the named account holder. Bulk imports count one entry per row.")}</div>`);
	for (const fn of [_ur_banner, _ur_chips, _ur_cards, _ur_depts, _ur_users, _ur_heat, _ur_sessions, _ur_activity_shell, _ur_flows, _ur_inactive]) {
		try { fn(d); } catch (e) { console.error(e); }
	}
}

/* full reset: clear user/dept/flow + range back to the default preset, ONE reload */
function _ur_reset() {
	const f = _ur.fields;
	_ur.applying = true;
	Promise.all([f.user.set_value(""), f.department.set_value(""), f.flow.set_value(""), f.preset.set_value("Last 30 days")])
		.catch(() => {})
		.finally(() => {
			_ur.applying = false;
			_ur_apply_preset().then(() => _ur_load());
		});
}
/* set one filter field programmatically — its change handler triggers the reload */
function _ur_setfilter(which, val) {
	const f = { user: _ur.fields.user, department: _ur.fields.department, flow: _ur.fields.flow }[which];
	if (f) f.set_value(val || "");
}
function _ur_chips() {
	const f = _ur.fields;
	const active = [["user", f.user.get_value()], ["department", f.department.get_value()], ["flow", f.flow.get_value()]]
		.filter(x => x[1]);
	if (!active.length) return $("#ur-chips").empty();
	$("#ur-chips").html(`<span class="ur-chiplbl">${__("Filters")}:</span>` +
		active.map(([k, v]) => `<button class="ur-chipbtn" data-k="${k}" title="${__("Clear this filter")}">${_esc(v)} ✕</button>`).join("") +
		`<button class="ur-chipbtn ur-chipclear" data-k="all">${__("Clear all")}</button>`);
	$("#ur-chips .ur-chipbtn").on("click", function () {
		const k = $(this).data("k");
		if (k !== "all") return _ur_setfilter(k, "");
		_ur.applying = true;
		Promise.all([f.user.set_value(""), f.department.set_value(""), f.flow.set_value("")])
			.catch(() => {})
			.finally(() => { _ur.applying = false; _ur_load(); });
	});
}

function _ur_banner(d) {
	if (!d.meta.failed_sources.length) return;
	$("#ur-banner").html(`<div class="ur-warn">⚠ ${__("Some data sources were unavailable — affected figures show n/a or are partial:")} ${d.meta.failed_sources.map(_esc).join(", ")}</div>`);
}
function _ur_cards(d) {
	const c = d.cards, partial = d.meta.failed_sources.length ? `<span class="ur-partial">±</span>` : "";
	const cards = [
		[__("Active users in range"), _num(c.active_users), ""],
		[__("Entries created"), _num(c.total_entries) + partial, "ur-c-blue"],
		[__("Approval decisions"), _num(c.total_decisions) + partial, "ur-c-purple"],
		[__("Logins"), _num(c.total_logins), "ur-c-green"],
		[__("Failed login attempts (any source, incl. non-existent usernames)"), _num(c.failed_logins), "ur-c-amber"],
		[__("Inactive > 30 days"), _num(c.inactive_over_30d), "ur-c-red"],
	];
	$("#ur-cards").html(cards.map(([l, v, cls]) =>
		`<li class="ur-card ${cls}"><span class="ur-card-count">${v}</span><span class="ur-card-label">${l}</span></li>`).join(""));
	$("#ur-cards .ur-card").eq(5).addClass("ur-click").attr("title", __("Jump to the inactive users panel"))
		.on("click", () => document.getElementById("ur-inactive").scrollIntoView({ behavior: "smooth", block: "start" }));
}
function _ur_depts(d) {
	const agg = {};
	for (const u of d.users) {
		const a = agg[u.dept] = agg[u.dept] || { entries: 0, decisions: 0, logins: 0, active: 0, logins_na: false };
		a.entries += u.entries; a.decisions += u.decisions;
		if (u.logins == null) a.logins_na = true; else a.logins += u.logins;
		if (u.entries + u.amendments + u.decisions + u.flow_actions + (u.logins || 0) > 0) a.active++;
	}
	const rows = Object.entries(agg).sort((a, b) => (b[1].entries + b[1].decisions) - (a[1].entries + a[1].decisions));
	if (!rows.length) return $("#ur-depts").html(`<div class="ur-empty">${__("No activity in the selected range")}</div>`);
	$("#ur-depts").html(`<table class="ur-table"><thead><tr>
		<th>${__("Department")}</th><th>${__("Entries")}</th><th>${__("Decisions")}</th><th>${__("Logins")}</th><th>${__("Active users")}</th></tr></thead><tbody>` +
		rows.map(([k, a]) => `<tr class="ur-click" data-dept="${_esc(k)}" title="${__("Filter to this department")}"><td>${_esc(k)}</td><td>${_num(a.entries)}</td><td>${_num(a.decisions)}</td><td>${a.logins_na ? _num(null) : _num(a.logins)}</td><td>${_num(a.active)}</td></tr>`).join("") +
		`</tbody></table>`);
	$("#ur-depts tr.ur-click").on("click", function () { _ur_setfilter("department", this.dataset.dept); });
}
function _ur_users(d) {
	if (!d.users.length) return $("#ur-users").html(`<div class="ur-empty">${__("No matching users")}</div>`);
	const s = _ur.sort, STR_KEYS = ["user", "dept", "last_active"];
	const rows = d.users.slice();
	if (s.key) {
		const val = u => s.key === "user" ? (u.full_name || "") : u[s.key];
		rows.sort((a, b) => {
			const av = val(a), bv = val(b);
			if (STR_KEYS.includes(s.key)) return String(av || "").localeCompare(String(bv || "")) * s.dir;
			return ((av == null ? -1 : av) - (bv == null ? -1 : bv)) * s.dir;
		});
	}
	const arrow = k => s.key === k ? (s.dir === 1 ? " ▲" : " ▼") : "";
	const th = (k, label, title) =>
		`<th class="ur-sort" data-key="${k}"${title ? ` title="${title}"` : ""}>${label}${arrow(k)}</th>`;
	$("#ur-users").html(`<table class="ur-table"><thead><tr>
		${th("user", __("User"))}${th("dept", __("Department"))}${th("entries", __("Entries"))}${th("amendments", __("Amendments"))}
		${th("since_cancelled", __("Since cancelled"), __("created by this user, since cancelled (by anyone)"))}
		${th("decisions", __("Decisions"))}${th("flow_actions", __("Other approval actions"), __("forwarding / revising / holding — routing, not deciding"))}
		${th("logins", __("Logins"))}${th("last_active", __("Last active"))}</tr></thead><tbody>` +
		rows.map(u => `<tr>
			<td class="ur-click ur-userlink" data-user="${_esc(u.user)}" title="${__("Filter to this user")}"><div class="ur-uname">${_esc(u.full_name)}</div><div class="ur-uemail">${_esc(u.user)}</div></td>
			<td>${_esc(u.dept)}${u.multi_department ? ` <span class="ur-multi" title="${__("holds roles in multiple departments")}">multi</span>` : ""}</td>
			<td>${_num(u.entries)}</td><td>${_num(u.amendments)}</td><td>${_num(u.since_cancelled)}</td>
			<td>${_num(u.decisions)}</td><td>${_num(u.flow_actions)}</td><td>${_num(u.logins)}</td>
			<td class="ur-last">${_esc(u.last_active ? u.last_active.substring(0, 16) : "—")}</td></tr>`).join("") +
		`</tbody></table>`);
	$("#ur-users th.ur-sort").on("click", function () {
		const k = $(this).data("key");
		_ur.sort = { key: k, dir: _ur.sort.key === k ? -_ur.sort.dir : (STR_KEYS.includes(k) ? 1 : -1) };
		try { _ur_users(_ur.data); } catch (e) { console.error(e); }
	});
	$("#ur-users td.ur-userlink").on("click", function () { _ur_setfilter("user", this.dataset.user); });
}
function _ur_heat(d) {
	const max = Math.max(1, ...d.heatmap.flat());
	let html = `<div class="ur-heatgrid"><div></div>` +
		Array.from({ length: 24 }, (_, h) => `<div class="ur-hh">${h}</div>`).join("");
	d.heatmap.forEach((row, di) => {
		html += `<div class="ur-hd">${UR_DAYS[di]}</div>` + row.map((n, h) =>
			`<div class="ur-cell" style="background:rgba(37,99,235,${n ? (0.12 + 0.78 * n / max).toFixed(2) : 0})"
				title="${UR_DAYS[di]} ${h}:00 — ${n} ${__("actions")}">${n ? "" : ""}</div>`).join("");
	});
	$("#ur-heat").html(html + `</div>`);
}
function _ur_dur(mins) {
	if (mins == null) return `<span class="ur-na">—</span>`;
	return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m`;
}
function _ur_sessions(d) {
	const s = d.sessions;
	if (!s) return $("#ur-sessions").html(`<div class="ur-error">${__("Timeline unavailable — source failed.")}</div>`);
	if (!s.sessions.length) return $("#ur-sessions").html(`<div class="ur-empty">${__("No login/logout activity in the selected range")}</div>`);
	const shown = s.sessions.slice(0, _ur.sessions_shown);
	$("#ur-sessions").html(`<table class="ur-table"><thead><tr>
		<th>${__("User")}</th><th>${__("Login")}</th><th>${__("Logout")}</th><th>${__("Duration")}</th></tr></thead><tbody>` +
		shown.map(e => `<tr>
			<td class="ur-click ur-userlink" data-user="${_esc(e.user)}" title="${__("Filter to this user")}"><span class="ur-uname">${_esc(e.full_name)}</span> <span class="ur-uemail">${_esc(e.user)}</span></td>
			<td>${e.login ? `<span class="ur-ev ur-ev-in">→ ${_esc(e.login)}</span>` : `<span class="ur-na" title="${__("logged in before the selected range")}">—</span>`}</td>
			<td>${e.logout ? (e.expired
				? `<span class="ur-ev ur-ev-exp" title="${__("closed by the automatic midnight session-expiry sweep — the user stopped earlier")}">${_esc(e.logout)} · ${__("auto")}</span>`
				: `<span class="ur-ev ur-ev-out">← ${_esc(e.logout)}</span>`)
				: `<span class="ur-na" title="${__("no logout recorded — browser closed")}">—</span>`}</td>
			<td>${_ur_dur(e.minutes)}</td></tr>`).join("") +
		`</tbody></table>
		<div class="ur-sessfoot"><span class="ur-showing">${__("Showing {0} of {1} sessions ({2} events)", [format_number(shown.length, null, 0), format_number(s.total_sessions, null, 0), format_number(s.total_events, null, 0)])}</span>
		${shown.length < Math.min(s.sessions.length, s.total_sessions) ? `<button id="ur-sess-more" class="ur-chipbtn">${__("Show 5 more")}</button>` : ""}</div>`);
	$("#ur-sessions td.ur-userlink").on("click", function () { _ur_setfilter("user", this.dataset.user); });
	$("#ur-sess-more").on("click", () => { _ur.sessions_shown += 5; try { _ur_sessions(_ur.data); } catch (e) { console.error(e); } });
}
/* ── Activity Detail (Changes & Creations) — plan §5: explicit Load trigger,
      keyset paging, batch-of-5, coarse hidden line, facet chips ──────────── */
function _ur_activity_shell() {
	const a = _ur.activity, u = _ur.fields.user.get_value();
	const fromD = _ur.fields.from.get_value(), toD = _ur.fields.to.get_value();
	$("#ur-activity-sec").toggle(!!u);
	if (!u) { a.user = null; a.rows = []; a.cursor = null; a.first_page = null; a.facet = null; return; }
	// reset on user OR range change — stale-range rows must never mix with a
	// new-range cursor (ui-designer F2)
	if (a.user !== u || a.from !== fromD || a.to !== toD) {
		a.user = u; a.from = fromD; a.to = toD;
		a.rows = []; a.cursor = null; a.first_page = null; a.shown = 5; a.facet = null;
	}
	if (!a.rows.length && !a.loading) {
		$("#ur-activity").html(`<button id="ur-act-load" class="ur-chipbtn">${__("Load activity for {0}", [_esc(u)])}</button>`);
		$("#ur-act-load").on("click", () => _ur_activity_fetch(true));
	} else {
		_ur_activity_render();
	}
}
async function _ur_activity_fetch(reset) {
	const a = _ur.activity, f = _ur.fields;
	if (a.loading) return;
	const forUser = a.user; // discard the response if the drill moved on (ui-designer F1)
	a.loading = true;
	if (reset) { a.rows = []; a.cursor = null; a.first_page = null; a.shown = 5; }
	$("#ur-activity").append(`<div class="ur-loading">${__("Loading…")}</div>`);
	try {
		const r = await frappe.call({ method: UR_ACT_METHOD, args: {
			user: a.user, from_date: f.from.get_value(), to_date: f.to.get_value(),
			cursor: a.cursor || null, doctype: a.facet || null,
		} });
		if (_ur.activity.user !== forUser) return;
		const m = r.message || {};
		a.rows = a.rows.concat(m.rows || []);
		a.cursor = m.cursor || null;
		if (m.first_page) a.first_page = m.first_page;
		_ur_activity_render();
	} catch (e) {
		console.error(e);
		const status = e && e.xhr && e.xhr.status;
		$("#ur-activity").html(`<div class="ur-error">${status === 429
			? __("Too many requests — try again in a minute.")
			: __("Couldn't load activity.")}</div>`);
	} finally {
		a.loading = false;
	}
}
function _ur_activity_render() {
	const a = _ur.activity, fp = a.first_page || {};
	let html = "";
	if (fp.withheld_unavailable) html += `<div class="ur-warn">${__("Hidden-count unavailable — completeness cannot be confirmed for this range.")}</div>`;
	else if (fp.withheld_confidential_total > 0) html += `<div class="ur-hiddenline">${__("{0} confidential document changes hidden in this range", [format_number(fp.withheld_confidential_total, null, 0)])}</div>`;
	if (fp.not_shown_other > 0) html += `<div class="ur-hiddenline">${__("{0} changes not shown", [format_number(fp.not_shown_other, null, 0)])}</div>`;
	if (fp.facets && Object.keys(fp.facets).length > 1) {
		html += `<div class="ur-chipstrip">` + Object.entries(fp.facets).map(([dt, n]) =>
			`<button class="ur-chipbtn ur-facet ${a.facet === dt ? "ur-facet-on" : ""}" data-dt="${_esc(dt)}">${_esc(dt)} (${format_number(n, null, 0)})</button>`).join("") +
			(a.facet ? `<button class="ur-chipbtn ur-chipclear" id="ur-facet-clear">${__("All doctypes")}</button>` : "") + `</div>`;
	}
	if (!a.rows.length) {
		html += `<div class="ur-empty">${__("No recorded changes in this range.")}</div>`;
	} else {
		const shown = a.rows.slice(0, a.shown);
		let day = null;
		html += `<div class="ur-actlist">` + shown.map(r => {
			const rday = (r.ts || r.day || "").substring(0, 10);
			const hdr = rday && rday !== day ? `<div class="ur-dayhdr">${_esc(rday)}</div>` : "";
			day = rday || day;
			if (r.since_deleted) {
				return `${hdr}<div class="ur-actrow ur-actmuted">${_esc((r.ts || "").substring(11) || "—")} · ${__("edited a since-deleted {0}", [_esc(r.doctype)])}</div>`;
			}
			if (r.system) {
				return `${hdr}<div class="ur-actrow ur-actmuted">${_esc((r.ts || "").substring(11) || "")} <span class="ur-ev ur-ev-exp">${__("System")}</span> ${_esc(r.action)}${r.system_ref ? " (" + __("via") + " " + _esc(r.system_ref) + ")" : ""} ${r.docname ? `· ${_esc(r.docname)}` : ""}</div>`;
			}
			const more = (r.more_fields || []).length;
			return `${hdr}<div class="ur-actrow">
				<span class="ur-acttime">${_esc((r.ts || "").substring(11))}</span>
				<span class="ur-ev ${r.decision ? "ur-ev-dec" : r.action === "Created" ? "ur-ev-in" : "ur-ev-chg"}">${_esc(__(r.action))}</span>
				<span class="ur-actdt">${_esc(r.doctype)}</span>
				<a href="/app/${_esc(frappe.router.slug(r.doctype))}/${encodeURIComponent(r.docname)}" class="ur-doclink">${_esc(r.docname)}</a>
				${(r.fields || []).length ? `<span class="ur-actfields">${__("Changed")}: ${r.fields.map(_esc).join(", ")}${more ? ` <span class="ur-more" title="${_esc((r.more_fields || []).join(", "))}">+${more} ${__("more")}</span>` : ""}</span>` : ""}
				${r.save_count > 1 ? `<span class="ur-uemail">· ${r.save_count} ${__("saves")}</span>` : ""}
			</div>`;
		}).join("") + `</div>`;
		const canLocal = a.shown < a.rows.length;
		html += `<div class="ur-sessfoot"><span class="ur-showing">${__("Showing {0} changes{1}", [format_number(Math.min(a.shown, a.rows.length), null, 0), a.cursor || canLocal ? " · " + __("more available") : ""])}</span>
			${canLocal || a.cursor ? `<button id="ur-act-more" class="ur-chipbtn">${__("Show 5 more")}</button>` : ""}</div>`;
	}
	if (fp.history_floor) html += `<div class="ur-hint">${__("History available from {0}.", [_esc(fp.history_floor)])}</div>`;
	$("#ur-activity").html(html);
	$("#ur-act-more").on("click", () => {
		const aa = _ur.activity;
		if (aa.shown < aa.rows.length) { aa.shown += 5; try { _ur_activity_render(); } catch (e) { console.error(e); } }
		else if (aa.cursor) { aa.shown += 5; _ur_activity_fetch(false); }
	});
	$("#ur-activity .ur-facet").on("click", function () {
		_ur.activity.facet = this.dataset.dt === _ur.activity.facet ? null : this.dataset.dt;
		_ur_activity_fetch(true);
	});
	$("#ur-facet-clear").on("click", () => { _ur.activity.facet = null; _ur_activity_fetch(true); });
}
function _ur_flows(d) {
	if (!d.flows.length) return $("#ur-flows").html(`<div class="ur-empty">${__("No activity in the selected range")}</div>`);
	$("#ur-flows").html(`<table class="ur-table"><thead><tr>
		<th>${__("Flow")}</th><th>${__("Entries")}</th><th>${__("Decisions")}</th><th>${__("Top users")}</th></tr></thead><tbody>` +
		d.flows.map(fl => `<tr class="ur-click" data-flow="${_esc(fl.flow)}" title="${__("Filter to this flow")}"><td>${_esc(fl.flow)}</td><td>${_num(fl.entries)}</td><td>${_num(fl.decisions)}</td>
			<td class="ur-top">${fl.top_users.map(t => `${_esc(t.full_name)} (${_num(t.n)})`).join(" · ") || "—"}</td></tr>`).join("") +
		`</tbody></table>`);
	$("#ur-flows tr.ur-click").on("click", function () { _ur_setfilter("flow", this.dataset.flow); });
}
function _ur_inactive(d) {
	const b = d.inactive.buckets;
	const strip = [["≤ 1 day", b.d1, "ur-c-green"], ["≤ 7 days", b.d7, ""], ["≤ 30 days", b.d30, ""], ["> 30 days", b.over30, "ur-c-amber"], [__("never"), b.never, "ur-c-red"]];
	let html = `<ul class="ur-cards ur-cards-5">` + strip.map(([l, arr, cls]) =>
		`<li class="ur-card ${cls}"><span class="ur-card-count">${arr.length}</span><span class="ur-card-label">${__("last seen")} ${l}</span></li>`).join("") + `</ul>`;
	const stale = b.over30.concat(b.never);
	if (stale.length) html += `<div class="ur-stale"><b>${__("Needs attention")}:</b> ${stale.map(_esc).join(", ")}</div>`;
	if (d.inactive.disabled.length) {
		html += `<details class="ur-disabled"><summary>${__("Disabled accounts")} (${d.inactive.disabled.length})</summary>${d.inactive.disabled.map(_esc).join(", ")}</details>`;
	}
	$("#ur-inactive").html(html);
}

const UR_CSS = `
#ur-container{padding:4px 0 24px;}
.ur-busy{opacity:.55;pointer-events:none;}
#ur-filters .page-form{background:var(--card-bg,#fff);border:1px solid var(--border-color,#e2e8f0);border-radius:12px;padding:10px 12px 4px;margin:0 0 14px;}
[data-theme="dark"] #ur-filters .page-form{background:#1f2937;border-color:#374151;}
.ur-resetbtn{border:1px solid var(--border-color,#e2e8f0);background:var(--fg-color,#f8fafc);color:var(--text-color);border-radius:8px;font-size:12px;font-weight:600;padding:4px 12px;cursor:pointer;margin:0 0 6px 8px;align-self:center;white-space:nowrap;}
.ur-resetbtn:hover{background:#fee2e2;border-color:#fecaca;color:#b91c1c;}
[data-theme="dark"] .ur-resetbtn{background:#111827;border-color:#374151;color:#e5e7eb;}
[data-theme="dark"] .ur-resetbtn:hover{background:rgba(239,68,68,.15);border-color:rgba(239,68,68,.4);color:#fca5a5;}
.ur-head{display:flex;align-items:center;gap:12px;padding:10px 4px 16px;}
.ur-chip{font-size:20px;background:var(--fg-color,#f1f5f9);border:1px solid var(--border-color,#e2e8f0);border-radius:12px;padding:8px 11px;line-height:1;}
.ur-title{font-size:17px;font-weight:600;color:var(--text-color);}
.ur-sub{font-size:12px;color:var(--text-muted);}
.ur-ver{opacity:.7;}
.ur-section{background:var(--card-bg,#fff);border:1px solid var(--border-color,#e2e8f0);border-radius:12px;padding:16px 18px;margin-bottom:14px;}
.ur-section h3{font-size:14px;font-weight:600;margin:0 0 12px;color:var(--text-color);}
.ur-cards{list-style:none;display:grid;grid-template-columns:repeat(6,1fr);gap:10px;padding:0;margin:0;}
.ur-cards-5{grid-template-columns:repeat(5,1fr);margin-bottom:12px;}
.ur-card{border:1px solid var(--border-color,#e2e8f0);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:2px;background:var(--fg-color,#f8fafc);}
.ur-card-count{font-size:20px;font-weight:700;color:var(--text-color);line-height:1.1;}
.ur-card-label{font-size:11px;color:var(--text-muted);line-height:1.35;}
.ur-c-blue .ur-card-count{color:#2563eb;} .ur-c-purple .ur-card-count{color:#7c3aed;}
.ur-c-green .ur-card-count{color:#047857;} .ur-c-amber .ur-card-count{color:#b45309;} .ur-c-red .ur-card-count{color:#dc2626;}
.ur-partial{color:#b45309;font-size:13px;vertical-align:super;}
.ur-na{color:var(--text-muted);font-style:italic;font-size:12px;}
.ur-scroll{overflow-x:auto;}
.ur-table{width:100%;border-collapse:collapse;font-size:12.5px;}
.ur-table th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);padding:6px 10px;border-bottom:1px solid var(--border-color,#e2e8f0);white-space:nowrap;}
.ur-table td{padding:7px 10px;border-bottom:1px solid var(--border-color,#e2e8f0);color:var(--text-color);white-space:nowrap;}
.ur-table tbody tr:hover{background:var(--fg-color,#f8fafc);}
.ur-uname{font-weight:600;} .ur-uemail{font-size:11px;color:var(--text-muted);}
.ur-multi{background:#ede9fe;color:#5b21b6;border-radius:8px;font-size:10px;font-weight:600;padding:1px 6px;}
.ur-last{color:var(--text-muted);font-size:11.5px;}
.ur-top{white-space:normal;min-width:220px;font-size:12px;}
.ur-heatgrid{display:grid;grid-template-columns:44px repeat(24,minmax(20px,1fr));gap:2px;min-width:620px;}
.ur-hh{font-size:9.5px;color:var(--text-muted);text-align:center;}
.ur-hd{font-size:10.5px;color:var(--text-muted);display:flex;align-items:center;}
.ur-cell{height:20px;border-radius:4px;border:1px solid var(--border-color,#eef2f7);}
.ur-hint,.ur-method{font-size:11px;color:var(--text-muted);padding-top:10px;line-height:1.5;}
.ur-showing{font-size:12px;color:var(--text-muted);padding-top:8px;}
.ur-ev{border-radius:10px;font-size:11px;font-weight:600;padding:2px 9px;white-space:nowrap;}
.ur-ev-in{background:#dcfce7;color:#166534;}
.ur-ev-out{background:#fef3c7;color:#92400e;}
.ur-ev-exp{background:var(--fg-color,#f1f5f9);color:var(--text-muted);}
.ur-sessfoot{display:flex;align-items:center;gap:12px;padding-top:8px;}
.ur-ev-dec{background:#ede9fe;color:#5b21b6;}
.ur-ev-chg{background:#e0f2fe;color:#075985;}
.ur-hiddenline{font-size:12px;color:var(--text-muted);font-style:italic;padding:4px 0;}
.ur-dayhdr{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);padding:10px 0 4px;}
.ur-actlist{display:flex;flex-direction:column;gap:4px;}
.ur-actrow{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;padding:5px 8px;border-radius:8px;font-size:12.5px;}
.ur-actrow:hover{background:var(--fg-color,#f8fafc);}
.ur-actmuted{color:var(--text-muted);}
.ur-acttime{font-size:11.5px;color:var(--text-muted);min-width:38px;}
.ur-actdt{font-weight:600;}
.ur-actfields{color:var(--text-muted);font-size:12px;}
.ur-more{color:#2563eb;cursor:help;}
.ur-facet-on{background:#334155;color:#fff;border-color:transparent;}
.ur-chipstrip{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:0 0 10px;}
.ur-doclink{color:#2563eb;font-weight:600;text-decoration:none;}
.ur-doclink:hover{text-decoration:underline;}
[data-theme="dark"] .ur-doclink{color:#93c5fd;}
[data-theme="dark"] .ur-actrow:hover{background:#111827;}
[data-theme="dark"] .ur-ev-dec{background:rgba(139,92,246,.2);color:#c4b5fd;}
[data-theme="dark"] .ur-ev-chg{background:rgba(14,165,233,.2);color:#7dd3fc;}
[data-theme="dark"] .ur-more{color:#93c5fd;}
[data-theme="dark"] .ur-ev-in{background:rgba(16,185,129,.2);color:#86efac;}
[data-theme="dark"] .ur-ev-out{background:rgba(245,158,11,.2);color:#fcd34d;}
[data-theme="dark"] .ur-ev-exp{background:#111827;color:#9ca3af;}
.ur-method{padding:6px 4px 0;}
.ur-warn{background:#fffbeb;border:1px solid #fde68a;color:#92400e;border-radius:10px;padding:9px 12px;font-size:12.5px;margin-bottom:12px;}
#ur-chips{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:0 0 12px;}
.ur-chiplbl{font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;}
.ur-chipbtn{border:1px solid #bfdbfe;background:#dbeafe;color:#1e40af;border-radius:14px;font-size:12px;font-weight:600;padding:3px 11px;cursor:pointer;}
.ur-chipbtn:hover{background:#bfdbfe;}
.ur-chipclear{background:transparent;border-color:var(--border-color,#e2e8f0);color:var(--text-muted);font-weight:500;}
.ur-chipclear:hover{background:var(--fg-color,#f1f5f9);}
.ur-click{cursor:pointer;}
tr.ur-click:hover td{background:var(--fg-color,#f1f5f9);}
.ur-userlink:hover .ur-uname{text-decoration:underline;color:#2563eb;}
th.ur-sort{cursor:pointer;user-select:none;}
th.ur-sort:hover{color:var(--text-color);}
.ur-error{background:rgba(239,68,68,.08);color:#b91c1c;border:1px solid rgba(239,68,68,.3);border-radius:10px;padding:10px 12px;font-size:13px;}
.ur-empty{text-align:center;padding:22px;color:var(--text-muted);font-size:13px;}
.ur-loading{text-align:center;padding:60px;color:var(--text-muted);}
.ur-stale{font-size:12px;color:var(--text-color);line-height:1.6;}
.ur-disabled{font-size:12px;color:var(--text-muted);margin-top:8px;}
.ur-disabled summary{cursor:pointer;font-weight:600;}
[data-theme="dark"] .ur-section{background:#1f2937;border-color:#374151;}
[data-theme="dark"] .ur-chip{background:#111827;border-color:#374151;}
[data-theme="dark"] .ur-card{background:#111827;border-color:#374151;}
[data-theme="dark"] .ur-table th,[data-theme="dark"] .ur-table td{border-color:#374151;}
[data-theme="dark"] .ur-table tbody tr:hover{background:#111827;}
[data-theme="dark"] .ur-cell{border-color:#374151;}
[data-theme="dark"] .ur-warn{background:rgba(245,158,11,.12);border-color:rgba(245,158,11,.3);color:#fcd34d;}
[data-theme="dark"] .ur-error{color:#fca5a5;}
[data-theme="dark"] .ur-partial{color:#fcd34d;}
[data-theme="dark"] .ur-chipbtn{background:rgba(59,130,246,.2);border-color:rgba(59,130,246,.35);color:#93c5fd;}
[data-theme="dark"] .ur-chipbtn:hover{background:rgba(59,130,246,.32);}
[data-theme="dark"] .ur-chipclear{background:transparent;border-color:#374151;color:#9ca3af;}
[data-theme="dark"] .ur-chipclear:hover{background:#0b1220;}
[data-theme="dark"] tr.ur-click:hover td{background:#111827;}
[data-theme="dark"] .ur-userlink:hover .ur-uname{color:#93c5fd;}
[data-theme="dark"] .ur-multi{background:rgba(139,92,246,.2);color:#c4b5fd;}
[data-theme="dark"] .ur-c-blue .ur-card-count{color:#93c5fd;} [data-theme="dark"] .ur-c-purple .ur-card-count{color:#c4b5fd;}
[data-theme="dark"] .ur-c-green .ur-card-count{color:#86efac;} [data-theme="dark"] .ur-c-amber .ur-card-count{color:#fcd34d;}
[data-theme="dark"] .ur-c-red .ur-card-count{color:#fca5a5;}
@media (max-width:768px){
	.ur-cards,.ur-cards-5{grid-template-columns:repeat(2,1fr);}
	.ur-head{flex-wrap:wrap;}
}
`;
