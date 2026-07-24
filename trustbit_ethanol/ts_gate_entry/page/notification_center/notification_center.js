/* ═══════════════════════════════════════════════════════════════════
   Notification Center — everyone's inbox.
     S1 — My Pending Actions (things needing MY decision)
     S2 — My Notifications (uncapped bell inbox, grouped Unread / Read)
     S3 — My WhatsApp Messages (collapsed, lazy, honest "Sent"-only)
   House idiom: stores_receiving. No moment() — ages come from the server.
   Every section renders fail-soft WITH console.error (Lesson 311).
   ═══════════════════════════════════════════════════════════════════ */

const NC_VERSION = "v2.8-2026-07-25";
const NC_API = "trustbit_ethanol.ts_gate_entry.notification_center_api";
console.log("[notification-center]", NC_VERSION, "loaded");

frappe.pages["notification-center"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper, title: "Notification Center", single_column: true,
	});
	page.main.html(`<div id="nc-container">
		<div id="nc-loading" style="text-align:center;padding:60px;color:var(--text-muted);">Loading Notification Center…</div>
	</div>`);
	if (!document.getElementById("nc-styles")) {
		const s = document.createElement("style");
		s.id = "nc-styles"; s.textContent = NC_CSS;
		document.head.appendChild(s);
	} else {
		document.getElementById("nc-styles").textContent = NC_CSS;
	}
	page.add_button(__("Refresh"), () => _nc_reload(), { icon: "refresh" });
	page.add_button(__("Mark all read"), () => _nc_mark_all(), { icon: "small-message" });
	_nc_subscribe_realtime();
	setTimeout(() => _nc_init(), 200);
};
frappe.pages["notification-center"].refresh = () => _nc_reload();

let _nc_state = {
	pending: { cards: [], items: [], total: 0 },
	notifs: { rows: [], total: 0, unread_total: 0, category_counts: {} },
	wa: { rows: [], total: 0, opted_in: false, loaded: false },
	filters: { category: "all", unread: 0, days: "30", start: 0, page_length: 20, search: "" },
	pfilters: { category: "all", start: 0, page_length: 50 },
};
let _nc_search_t = null;

function _nc_esc(v) { return frappe.utils.escape_html(String(v == null ? "" : v)); }
function _nc_call(method, args) {
	return frappe.call({ method: `${NC_API}.${method}`, args: args || {} }).then(r => r.message);
}

async function _nc_init() {
	try {
		const on = await _nc_call("notification_center_enabled");
		if (!cint(on)) {
			$("#nc-container").html(`<div class="nc-empty">${__("The Notification Center is temporarily unavailable.")}</div>`);
			return;
		}
		_nc_render_shell();
		await _nc_load_all();
	} catch (e) {
		console.error(e);
		$("#nc-container").html(`<div class="nc-error">${__("Couldn't load the Notification Center.")}</div>`);
	}
}
function _nc_reload() {
	_nc_state.filters.start = 0;
	if (document.getElementById("nc-s1-body")) _nc_load_all();
}
async function _nc_load_all() {
	_nc_state.pfilters.start = 0;
	_nc_state.filters.start = 0;
	await Promise.allSettled([_nc_load_pending(false), _nc_load_notifs(false)]);
	if (_nc_state.wa.loaded) _nc_load_wa();
}

/* Live updates — core fires a realtime "notification" event to the user on
   every new Notification Log row. Debounced; only refreshes while the Center
   is the active route (elsewhere the navbar badge/toast handle it). */
let _nc_rt_timer = null;
let _nc_rt_bound = false;
function _nc_subscribe_realtime() {
	try {
		if (_nc_rt_bound || !frappe.realtime || !frappe.realtime.on) return;
		_nc_rt_bound = true;
		frappe.realtime.on("notification", () => {
			try {
				if ((frappe.get_route() || [])[0] !== "notification-center") return;
				if (!document.getElementById("nc-s1-body")) return;
				clearTimeout(_nc_rt_timer);
				_nc_rt_timer = setTimeout(() => {
					_nc_state.filters.start = 0;
					_nc_load_all();
				}, 1200);
			} catch (e) { console.error(e); }
		});
	} catch (e) { console.error(e); }
}

function _nc_render_shell() {
	$("#nc-container").html(`
		<div class="nc-header">
			<span class="nc-bell-chip" aria-hidden="true">🔔</span>
			<div class="nc-head-text">
				<div class="nc-title">${__("Notification Center")}</div>
				<div class="nc-sub">${__("Your approvals, alerts & messages")} · <span class="nc-ver">${NC_VERSION}</span></div>
			</div>
			<div class="nc-head-badges">
				<span class="nc-badge nc-badge-pending" id="nc-head-pending" aria-live="polite" style="display:none;"></span>
				<span class="nc-badge nc-badge-unread" id="nc-head-unread" aria-live="polite" style="display:none;"></span>
			</div>
		</div>
		<div class="nc-cols">
			<section class="nc-section">
				<h3>${__("My Pending Actions")}</h3>
				<div id="nc-s1-body"><div class="nc-skel"></div><div class="nc-skel"></div></div>
			</section>
			<section class="nc-section">
				<h3>${__("My Notifications")} <span class="nc-badge nc-badge-unread" id="nc-s2-unread" aria-live="polite" style="display:none;"></span><span id="nc-s2-controls"></span></h3>
				<div id="nc-filterbar"></div>
				<div id="nc-s2-body"><div class="nc-skel"></div><div class="nc-skel"></div><div class="nc-skel"></div></div>
				<div id="nc-s2-footer"></div>
			</section>
		</div>
		<section class="nc-section">
			<h3><button id="nc-wa-toggle" class="nc-collapse" aria-expanded="false">▸ ${__("My WhatsApp Messages")}</button></h3>
			<div id="nc-s3-body" style="display:none;"></div>
		</section>`);
	$("#nc-wa-toggle").on("click", _nc_toggle_wa);
}

/* ── Section 1 ──────────────────────────────────────────────────── */
async function _nc_load_pending(append) {
	try {
		const data = await _nc_call("get_pending_actions", _nc_state.pfilters);
		if (append) data.items = _nc_state.pending.items.concat(data.items);
		_nc_state.pending = data;
		_nc_render_pending();
	} catch (e) {
		console.error(e);
		$("#nc-s1-body").html(`<div class="nc-error">${__("Couldn't load your pending actions.")}</div>`);
	}
}
function _nc_pending_filter(cat) {
	const cur = _nc_state.pfilters.category;
	// click the already-active card → reset to all
	_nc_state.pfilters.category = (cur === cat) ? "all" : cat;
	_nc_state.pfilters.start = 0;
	_nc_load_pending(false);
}
function _nc_render_pending() {
	try {
		const p = _nc_state.pending;
		const grand = (p.cards || []).reduce((s, c) => s + (c.count || 0), 0);
		_nc_paint_pending_badge(grand);
		if (!grand) {
			$("#nc-s1-body").html(`
				<div class="nc-empty nc-empty-ok">
					<div class="nc-empty-glyph" aria-hidden="true">✓</div>
					<div class="nc-empty-title">${__("You're all caught up")}</div>
					<div class="nc-empty-sub">${__("Nothing is waiting on you right now.")}</div>
				</div>`);
			return;
		}
		const pcat = _nc_state.pfilters.category;
		const cards = p.cards.map(c => `
			<li class="nc-card nc-cat-${_nc_esc(c.key)} ${pcat === c.key ? "nc-card-on" : ""}" role="button" tabindex="0" aria-pressed="${pcat === c.key}" data-pcat="${_nc_esc(c.key)}">
				<span class="nc-card-count">${format_number(c.count, null, 0)}</span>
				<span class="nc-card-label">${_nc_esc(__(c.label))}</span>
			</li>`).join("");
		const rows = p.items.map(it => `
			<div class="nc-pending-row nc-cat-${_nc_esc(it.category)}">
				${_nc_pill(it.category)}
				<a href="${_nc_esc(it.route)}" class="nc-doclink">${_nc_esc(it.doc_name)}</a>
				<span class="nc-age ${it.overdue ? "nc-overdue" : ""}">${__("waiting")} ${_nc_esc(it.age_display)}${it.since_display ? ` · <span class="nc-since-label">${__("since")}</span> <span class="nc-since">${_nc_esc(it.since_display)}</span>` : ""}${it.overdue ? ` <span class="nc-tag-overdue">${__("overdue")}</span>` : ""}</span>
				<a href="${_nc_esc(it.route)}" class="nc-open">${__("Open")}</a>
			</div>`).join("");
		const foot = `<div class="nc-s1-foot">
			<span class="nc-showing">${__("Showing {0} of {1}", [format_number(p.items.length, null, 0), format_number(p.total || 0, null, 0)])}${pcat !== "all" ? " · " + _nc_esc(pcat) : ""}</span>
			${p.items.length < (p.total || 0) ? `<button class="nc-loadmore" id="nc-pend-more">${__("Load more")}</button>` : ""}
			${pcat !== "all" ? `<button class="nc-loadmore" id="nc-pend-all">${__("Show all")}</button>` : ""}
		</div>`;
		$("#nc-s1-body").html(`<ul class="nc-cards">${cards}</ul><div class="nc-pending-list">${rows}</div>${foot}`);
		$("#nc-s1-body .nc-card").on("click", function () { _nc_pending_filter($(this).data("pcat")); });
		$("#nc-s1-body .nc-card").on("keydown", function (e) {
			if (e.key === "Enter" || e.key === " ") { e.preventDefault(); _nc_pending_filter($(this).data("pcat")); }
		});
		$("#nc-pend-more").on("click", function () {
			$(this).prop("disabled", true).html(`<span class="nc-spin" aria-hidden="true"></span> ${__("Loading…")}`);
			_nc_state.pfilters.start += _nc_state.pfilters.page_length;
			_nc_load_pending(true);
		});
		$("#nc-pend-all").on("click", () => _nc_pending_filter("all"));
	} catch (e) {
		console.error(e);
		$("#nc-s1-body").html(`<div class="nc-error">${__("Couldn't render pending actions.")}</div>`);
	}
}
function _nc_paint_pending_badge(count) {
	$("#nc-head-pending").toggle(count > 0).text(`${format_number(count, null, 0)} ${__("pending")}`);
}

/* ── Section 2 ──────────────────────────────────────────────────── */
function _nc_show_list_loading(on) {
	$("#nc-s2-body .nc-list-loading").remove();
	if (on) $("#nc-s2-body").append(`<div class="nc-list-loading"><span class="nc-spin nc-spin-lg" aria-hidden="true"></span><span class="nc-srtext">${__("Loading…")}</span></div>`);
}
async function _nc_load_notifs(append) {
	try {
		if (!append) _nc_show_list_loading(true);
		const f = _nc_state.filters;
		const data = await _nc_call("get_notifications", f);
		if (append) data.rows = _nc_state.notifs.rows.concat(data.rows);
		_nc_state.notifs = data;
		_nc_render_filterbar();
		_nc_render_notifs();
		_nc_paint_badges();
	} catch (e) {
		console.error(e);
		$("#nc-s2-body").html(`<div class="nc-error">${__("Couldn't load your notifications.")} <button class="btn btn-xs btn-default nc-retry">${__("Retry")}</button></div>`);
		$("#nc-s2-body .nc-retry").on("click", () => _nc_load_notifs(false));
	} finally {
		_nc_show_list_loading(false);
	}
}
function _nc_render_filterbar() {
	try {
		const f = _nc_state.filters, cc = _nc_state.notifs.category_counts || {};
		const cats = ["all", "PO", "MR", "Inspection", "Production", "Gate", "Budget", "Mention", "Other"];
		const chips = cats.map(c => {
			const n = cc[c] ? (f.unread ? cc[c].unread : cc[c].total) : 0;
			return `<button class="nc-chip ${f.category === c ? "nc-chip-on nc-cat-" + c : ""}" aria-pressed="${f.category === c}" data-cat="${c}">${c === "all" ? __("All") : _nc_esc(c)} (${format_number(n, null, 0)})</button>`;
		}).join("");
		$("#nc-filterbar").html(`<div class="nc-chipstrip">${chips}</div>`);
		$("#nc-filterbar .nc-chip").on("click", function () { f.category = $(this).data("cat"); f.start = 0; _nc_load_notifs(false); });
		// Search + toggle + range live in the section title row. Rendered ONCE
		// (not on every list reload) so typing into search is never interrupted.
		if (!document.getElementById("nc-search")) {
			$("#nc-s2-controls").html(`
				<input type="search" id="nc-search" class="form-control nc-search" placeholder="${__("Search…")}" aria-label="${__("Search notifications")}">
				<label class="nc-toggle"><input type="checkbox" id="nc-unread-only" aria-label="${__("Show unread only")}"> ${__("Unread only")}</label>
				<select id="nc-days" class="form-control nc-days">
					<option value="7">${__("Last 7 days")}</option>
					<option value="30">${__("Last 30 days")}</option>
					<option value="90">${__("Last 90 days")}</option>
					<option value="all">${__("All")}</option>
				</select>`);
			$("#nc-search").val(f.search || "");
			$("#nc-unread-only").prop("checked", !!f.unread);
			$("#nc-days").val(f.days);
			$("#nc-search").on("input", function () {
				const v = this.value;
				clearTimeout(_nc_search_t);
				_nc_search_t = setTimeout(() => { f.search = v; f.start = 0; _nc_load_notifs(false); }, 350);
			});
			$("#nc-unread-only").on("change", function () { f.unread = this.checked ? 1 : 0; f.start = 0; _nc_load_notifs(false); });
			$("#nc-days").on("change", function () { f.days = this.value; f.start = 0; _nc_load_notifs(false); });
		}
	} catch (e) {
		console.error(e);
		$("#nc-filterbar").html(`<div class="nc-error">${__("Couldn't render filters.")}</div>`);
	}
}
function _nc_notif_row(r) {
	return `
		<div class="nc-notif ${r.read ? "nc-read" : "nc-unread nc-cat-" + _nc_esc(r.category)}" data-name="${_nc_esc(r.name)}">
			<span class="nc-dot" ${r.read ? 'style="visibility:hidden;"' : ""} aria-hidden="true"></span>${r.read ? "" : `<span class="nc-srtext">${__("Unread")}</span>`}
			${_nc_pill(r.category)}
			<a class="nc-notif-main" href="${_nc_esc(r.route)}">
				<span class="nc-subject">${_nc_esc(r.subject)}</span>
				<span class="nc-meta">${_nc_esc(r.doc_type || "")}${r.doc_type ? " · " : ""}${_nc_esc(r.age_display)} ${__("ago")} · <span class="nc-since">${_nc_esc(r.created_display || "")}</span></span>
			</a>
			${r.read ? "" : `<button class="nc-markread" aria-label="${__("Mark as read")}" data-name="${_nc_esc(r.name)}">✓</button>`}
		</div>`;
}
function _nc_render_notifs() {
	try {
		const n = _nc_state.notifs, f = _nc_state.filters;
		if (!n.rows.length) {
			const msg = f.unread ? __("No unread notifications — you're clear.")
				: (f.category !== "all" ? __("No {0} notifications in this range.", [f.category]) : __("No notifications in this range."));
			// Range-aware hint: the badge counts ALL-TIME unread, so an empty
			// filtered view with a non-zero badge needs an explanation + way out.
			const hidden = n.unread_total || 0;
			const filtered = f.days !== "all" || f.category !== "all";
			const older = (hidden > 0 && filtered) ? `
				<div class="nc-empty-sub">${__("You have {0} unread outside this view.", [format_number(hidden, null, 0)])}
					<button class="nc-loadmore" id="nc-show-all">${__("Show all")}</button></div>` : "";
			$("#nc-s2-body").html(`
				<div class="nc-empty">
					<div class="nc-empty-glyph" aria-hidden="true">📭</div>
					<div class="nc-empty-title">${_nc_esc(msg)}</div>${older}
				</div>`);
			$("#nc-show-all").on("click", () => { f.days = "all"; f.category = "all"; f.start = 0; _nc_load_notifs(false); });
			$("#nc-s2-footer").empty();
			return;
		}
		const unread = n.rows.filter(r => !r.read);
		const read = n.rows.filter(r => r.read);
		let html = "";
		if (unread.length) {
			html += `
				<div class="nc-group nc-group-unread">
					<div class="nc-group-head" role="heading" aria-level="4"><span class="nc-group-title">${__("Unread")}</span><span class="nc-group-count">${format_number(unread.length, null, 0)}</span></div>
					<div class="nc-group-body">${unread.map(_nc_notif_row).join("")}</div>
				</div>`;
		}
		if (!f.unread && read.length) {
			html += `
				<div class="nc-group nc-group-read">
					<div class="nc-group-head" role="heading" aria-level="4"><span class="nc-group-title">${__("Read")}</span><span class="nc-group-count">${format_number(read.length, null, 0)}</span></div>
					<div class="nc-group-body">${read.map(_nc_notif_row).join("")}</div>
				</div>`;
		}
		$("#nc-s2-body").html(html);
		$("#nc-s2-footer").html(`
			<span class="nc-showing">${__("Showing {0} of {1}", [format_number(n.rows.length, null, 0), format_number(n.total, null, 0)])}</span>
			${n.rows.length < n.total ? `<button id="nc-loadmore" class="nc-loadmore">${__("Load more")}</button>` : ""}`);
		$("#nc-s2-body .nc-markread").on("click", function (ev) { ev.preventDefault(); _nc_mark_read($(this).data("name"), $(this)); });
		$("#nc-loadmore").on("click", function () {
			$(this).prop("disabled", true).html(`<span class="nc-spin" aria-hidden="true"></span> ${__("Loading…")}`);
			_nc_state.filters.start += _nc_state.filters.page_length;
			_nc_load_notifs(true);
		});
	} catch (e) {
		console.error(e);
		$("#nc-s2-body").html(`<div class="nc-error">${__("Couldn't render notifications.")}</div>`);
	}
}
function _nc_paint_badges() {
	const u = _nc_state.notifs.unread_total || 0;
	for (const id of ["#nc-head-unread", "#nc-s2-unread"]) {
		$(id).toggle(u > 0).text(`${format_number(u, null, 0)} ${__("unread")}`);
	}
}
async function _nc_mark_read(name, $btn) {
	// Phase 1: the network mutation. Only a genuine failure here is a real
	// error — a post-success render hiccup must NOT report "couldn't mark".
	try {
		$btn.prop("disabled", true).html(`<span class="nc-spin" aria-hidden="true"></span>`);
		await _nc_call("mark_read", { log_name: name });
	} catch (e) {
		console.error(e);
		$btn.prop("disabled", false).text("✓");
		if (!(e && e._server_messages)) frappe.msgprint(__("Couldn't mark as read."));
		return;
	}
	// Phase 2: refresh from server truth (same clean path as Mark-all-read) —
	// re-buckets the now-read row into Read and repaints badges. Any glitch
	// here is a display-only issue, never surfaced as a mutation failure.
	try {
		await _nc_load_notifs(false);
	} catch (e) {
		console.error(e);
	}
}
async function _nc_mark_all() {
	try {
		const f = _nc_state.filters;
		await frappe.call({
			method: `${NC_API}.mark_all_read`,
			args: { category: f.category, days: f.days },
			freeze: true, freeze_message: __("Marking notifications read…"),
		});
		f.start = 0;
		await _nc_load_notifs(false);
		frappe.show_alert({ message: __("Notifications marked read."), indicator: "green" });
	} catch (e) {
		console.error(e);
		if (!(e && e._server_messages)) frappe.msgprint(__("Couldn't mark all read."));
	}
}

/* ── Section 3 ──────────────────────────────────────────────────── */
function _nc_toggle_wa() {
	const $b = $("#nc-wa-toggle"), open = $b.attr("aria-expanded") === "true";
	$b.attr("aria-expanded", String(!open)).text(`${open ? "▸" : "▾"} ${__("My WhatsApp Messages")}`);
	$("#nc-s3-body").toggle(!open);
	if (!open && !_nc_state.wa.loaded) _nc_load_wa();
}
async function _nc_load_wa() {
	try {
		$("#nc-s3-body").html(`<div class="nc-skel"></div>`);
		_nc_state.wa = await _nc_call("get_whatsapp_log");
		_nc_state.wa.loaded = true;
		const w = _nc_state.wa;
		if (!w.rows.length) {
			$("#nc-s3-body").html(`<div class="nc-empty">${w.opted_in ? __("No WhatsApp messages yet.") : __("WhatsApp notifications are off for your account. Ask IT to enable them.")}</div>`);
			return;
		}
		$("#nc-s3-body").html(`<div class="nc-group-body">` + w.rows.map(r => `
			<div class="nc-notif nc-read">
				<span class="nc-pill nc-pill-wa">${_nc_esc(r.status_display)}</span>
				<a class="nc-notif-main" href="${_nc_esc(r.route)}">
					<span class="nc-subject">${_nc_esc(r.template_key)}${r.is_sandbox ? ` <span class="nc-tag-overdue">${__("test")}</span>` : ""}</span>
					<span class="nc-meta">${_nc_esc(r.ref_name || "")} · ${_nc_esc(r.age_display)} ${__("ago")} · <span class="nc-since">${_nc_esc(r.created_display || "")}</span></span>
				</a>
			</div>`).join("") + `</div>` +
			`<div class="nc-hint">${__("'Sent' means handed to the gateway — delivery confirmation isn't configured, so it doesn't guarantee the message was received.")}</div>`);
	} catch (e) {
		console.error(e);
		$("#nc-s3-body").html(`<div class="nc-error">${__("Couldn't load WhatsApp messages.")}</div>`);
	}
}

/* ── helpers + CSS ──────────────────────────────────────────────── */
function _nc_pill(category) {
	return `<span class="nc-pill nc-pill-${_nc_esc(category)}">${_nc_esc(category)}</span>`;
}

const NC_CSS = `
/* ── container + header ─────────────────────────────────────────── */
#nc-container{padding:4px 0 16px;}
/* two-column layout: Pending | Notifications (WhatsApp stays full-width below) */
.nc-cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start;margin-bottom:10px;}
.nc-cols>.nc-section{margin-bottom:0;}
.nc-header{display:flex;align-items:center;gap:12px;padding:8px 4px 12px;}
.nc-bell-chip{font-size:20px;background:var(--fg-color,#f1f5f9);border:1px solid var(--border-color,#e2e8f0);border-radius:12px;padding:8px 11px;line-height:1;}
.nc-head-text{min-width:0;}
.nc-title{font-size:17px;font-weight:600;color:var(--text-color);line-height:1.25;}
.nc-sub{font-size:12px;color:var(--text-muted);}
.nc-ver{opacity:.7;}
.nc-head-badges{margin-left:auto;display:flex;align-items:center;gap:6px;flex:none;}
.nc-badge{border-radius:11px;font-size:11px;font-weight:600;padding:3px 10px;white-space:nowrap;line-height:1.4;}
.nc-badge-unread{background:#ef4444;color:#fff;}
.nc-badge-pending{background:#2563eb;color:#fff;}

/* ── sections ───────────────────────────────────────────────────── */
.nc-section{background:var(--card-bg,#fff);border:1px solid var(--border-color,#e2e8f0);border-radius:12px;padding:12px 16px;margin-bottom:10px;}
.nc-section h3{font-size:14px;font-weight:600;margin:0 0 10px;color:var(--text-color);display:flex;align-items:center;gap:8px;}

/* ── S1 count cards (tinted per category) ───────────────────────── */
.nc-cards{list-style:none;display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;padding:0;margin:0 0 10px;}
.nc-card{border:1px solid var(--border-color,#e2e8f0);border-radius:10px;padding:7px 11px;display:flex;flex-direction:column;gap:2px;background:var(--fg-color,#f8fafc);}
.nc-card{cursor:pointer;transition:box-shadow .12s;}
.nc-card:hover{box-shadow:0 2px 8px rgba(15,23,42,.08);}
.nc-card-on{outline:2px solid var(--primary,#3b82f6);outline-offset:-1px;}
.nc-card:focus-visible{outline:2px solid var(--primary,#3b82f6);outline-offset:2px;}
.nc-card-count{font-size:18px;font-weight:700;color:var(--text-color);line-height:1.1;}
.nc-s1-foot{display:flex;align-items:center;gap:10px;padding-top:8px;}
.nc-search{width:150px;font-size:12px;height:28px;padding:2px 10px;}
.nc-card-label{font-size:12px;color:var(--text-muted);}
.nc-card.nc-cat-PO{background:#eff6ff;border-color:#bfdbfe;}          .nc-card.nc-cat-PO .nc-card-count{color:#2563eb;}
.nc-card.nc-cat-MR{background:#ecfeff;border-color:#a5f3fc;}          .nc-card.nc-cat-MR .nc-card-count{color:#0e7490;}
.nc-card.nc-cat-Inspection{background:#fdf2f8;border-color:#fbcfe8;}  .nc-card.nc-cat-Inspection .nc-card-count{color:#db2777;}
.nc-card.nc-cat-Production{background:#f5f3ff;border-color:#ddd6fe;}  .nc-card.nc-cat-Production .nc-card-count{color:#7c3aed;}
.nc-card.nc-cat-Gate{background:#f0fdf4;border-color:#bbf7d0;}        .nc-card.nc-cat-Gate .nc-card-count{color:#047857;}
.nc-card.nc-cat-Budget{background:#fffbeb;border-color:#fde68a;}      .nc-card.nc-cat-Budget .nc-card-count{color:#b45309;}
.nc-card.nc-cat-Other{background:#f8fafc;border-color:#e2e8f0;}       .nc-card.nc-cat-Other .nc-card-count{color:#475569;}

/* ── S1 pending rows (card-like) ────────────────────────────────── */
.nc-pending-list{display:flex;flex-direction:column;gap:4px;}
.nc-pending-row{display:flex;align-items:center;gap:10px;padding:6px 12px;border:1px solid var(--border-color,#e2e8f0);border-left-width:3px;border-left-color:var(--border-color,#e2e8f0);border-radius:10px;background:var(--card-bg,#fff);transition:box-shadow .12s;}
.nc-pending-row:hover{box-shadow:0 2px 8px rgba(15,23,42,.07);}
.nc-doclink{font-weight:600;color:var(--text-color);text-decoration:none;}
.nc-doclink:hover{text-decoration:underline;}
.nc-age{font-size:12px;color:var(--text-muted);}
.nc-overdue{color:#b45309;}
.nc-tag-overdue{background:#fef3c7;color:#92400e;border-radius:8px;font-size:10px;font-weight:600;padding:1px 6px;}
.nc-since{color:var(--text-color);}
.nc-since-label{color:#2563eb;}
.nc-open{white-space:nowrap;font-size:12px;font-weight:600;padding:5px 12px;border-radius:8px;background:#dbeafe;color:#1e40af;border:1px solid #bfdbfe;text-decoration:none;flex:none;margin-left:auto;}
.nc-open:hover{background:#bfdbfe;color:#1e3a8a;text-decoration:none;}

/* ── S2 filter chips (filled active in category hue) ────────────── */
.nc-chipstrip{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;}
.nc-chip{border:1px solid var(--border-color,#e2e8f0);background:var(--card-bg,#fff);color:var(--text-color);border-radius:15px;font-size:12px;font-weight:500;padding:4px 12px;cursor:pointer;line-height:1.3;}
.nc-chip:hover{background:var(--fg-color,#f1f5f9);}
.nc-chip-on{font-weight:600;color:#fff;border-color:transparent;background:#334155;}
.nc-chip-on.nc-cat-PO{background:#2563eb;}
.nc-chip-on.nc-cat-MR{background:#0e7490;}
.nc-chip-on.nc-cat-Inspection{background:#db2777;}
.nc-chip-on.nc-cat-Production{background:#7c3aed;}
.nc-chip-on.nc-cat-Gate{background:#047857;}
.nc-chip-on.nc-cat-Budget{background:#b45309;}
.nc-chip-on.nc-cat-Mention{background:#0f766e;}
.nc-chip-on.nc-cat-Other{background:#475569;}

/* ── S2 filter controls (in the section title row) ──────────────── */
#nc-s2-controls{margin-left:auto;display:flex;align-items:center;gap:12px;font-weight:400;}
.nc-toggle{font-size:12px;color:var(--text-muted);margin:0;display:flex;align-items:center;gap:6px;cursor:pointer;}
.nc-days{width:auto;font-size:12px;height:28px;padding:2px 8px;}

/* ── S2 groups (unread tray + read) ─────────────────────────────── */
.nc-group{margin-bottom:10px;}
.nc-group:last-child{margin-bottom:0;}
.nc-group-head{display:flex;align-items:center;gap:8px;margin:0 2px 6px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);}
.nc-group-count{border-radius:10px;padding:1px 8px;font-size:11px;font-weight:700;background:var(--border-color,#e2e8f0);color:var(--text-color);}
.nc-group-unread{background:#f0f7ff;border:1px solid #dbeafe;border-radius:12px;padding:7px 8px 4px;}
.nc-group-unread .nc-group-count{background:#ef4444;color:#fff;}
.nc-group-body{display:flex;flex-direction:column;gap:4px;}

/* ── S2 notif rows (card-like + dim read) ───────────────────────── */
.nc-notif{display:flex;align-items:flex-start;gap:10px;padding:7px 12px;border-radius:10px;background:var(--card-bg,#fff);border:1px solid var(--border-color,#e2e8f0);transition:box-shadow .12s,opacity .12s;}
.nc-notif:hover{box-shadow:0 2px 8px rgba(15,23,42,.07);}
.nc-unread{border-left:3px solid var(--border-color,#e2e8f0);}
.nc-unread .nc-subject{font-weight:600;}
.nc-group-read .nc-notif{opacity:.72;background:transparent;border-color:transparent;}
.nc-group-read .nc-notif:hover,.nc-group-read .nc-notif:focus-within{opacity:1;background:var(--card-bg,#fff);border-color:var(--border-color,#e2e8f0);}
.nc-dot{width:8px;height:8px;border-radius:50%;background:#3b82f6;flex:none;margin-top:5px;}
.nc-notif-main{display:flex;flex-direction:column;min-width:0;flex:1;color:var(--text-color);text-decoration:none;gap:2px;}
.nc-subject{overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;font-size:13px;line-height:1.35;}
.nc-meta{font-size:11px;color:var(--text-muted);}
.nc-markread{border:1px solid var(--border-color,#e2e8f0);background:var(--card-bg,#fff);color:var(--text-muted);border-radius:8px;min-width:32px;min-height:32px;cursor:pointer;flex:none;display:inline-flex;align-items:center;justify-content:center;font-size:13px;}
.nc-markread:hover{color:#16a34a;border-color:#16a34a;background:#f0fdf4;}
.nc-markread:disabled{cursor:default;opacity:.7;}

/* ── S2 footer ──────────────────────────────────────────────────── */
#nc-s2-body{position:relative;min-height:32px;}
#nc-s2-footer{display:flex;align-items:center;gap:10px;padding-top:10px;}
.nc-showing{font-size:12px;color:var(--text-muted);}
.nc-loadmore{background:transparent;border:1px solid var(--border-color,#e2e8f0);color:var(--text-color);border-radius:8px;padding:5px 14px;font-size:12px;font-weight:600;cursor:pointer;}
.nc-loadmore:hover{background:var(--fg-color,#f1f5f9);}
.nc-loadmore:disabled{opacity:.6;cursor:default;}

/* ── S3 whatsapp ────────────────────────────────────────────────── */
.nc-collapse{background:none;border:none;padding:0;font-size:14px;font-weight:600;color:var(--text-color);cursor:pointer;}
.nc-hint{font-size:11px;color:var(--text-muted);padding-top:10px;line-height:1.4;}

/* ── empty / error / skeleton ───────────────────────────────────── */
.nc-empty{text-align:center;padding:18px 16px;color:var(--text-muted);}
.nc-empty-glyph{font-size:26px;line-height:1;margin-bottom:6px;opacity:.85;}
.nc-empty-title{font-size:14px;font-weight:600;color:var(--text-color);}
.nc-empty-sub{font-size:12px;color:var(--text-muted);margin-top:3px;}
.nc-empty-ok .nc-empty-glyph{color:#16a34a;}
.nc-empty-ok .nc-empty-title{color:#166534;}
.nc-error{background:rgba(239,68,68,.08);color:#b91c1c;border:1px solid rgba(239,68,68,.3);border-radius:10px;padding:10px 12px;font-size:13px;}
.nc-skel{height:40px;border-radius:10px;background:var(--fg-color,#f1f5f9);margin:5px 0;animation:ncpulse 1.2s infinite;}
@keyframes ncpulse{0%,100%{opacity:.5}50%{opacity:1}}

/* ── spinners + list overlay ────────────────────────────────────── */
.nc-spin{display:inline-block;width:13px;height:13px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:ncspin .6s linear infinite;vertical-align:-2px;}
.nc-spin-lg{width:22px;height:22px;border-width:3px;color:var(--primary,#3b82f6);}
@keyframes ncspin{to{transform:rotate(360deg)}}
.nc-list-loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,.55);border-radius:10px;z-index:3;}

/* ── sr-only ────────────────────────────────────────────────────── */
.nc-srtext{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}

/* ── pills + category left-accents ──────────────────────────────── */
.nc-pill{border-radius:10px;font-size:10px;font-weight:600;padding:2px 8px;flex:none;margin-top:1px;}
.nc-pill-PO{background:#dbeafe;color:#1e40af;}          .nc-cat-PO{border-left-color:#3b82f6;}
.nc-pill-MR{background:#cffafe;color:#155e75;}          .nc-cat-MR{border-left-color:#06b6d4;}
.nc-pill-Inspection{background:#fce7f3;color:#9d174d;}  .nc-cat-Inspection{border-left-color:#ec4899;}
.nc-pill-Production{background:#ede9fe;color:#5b21b6;}  .nc-cat-Production{border-left-color:#8b5cf6;}
.nc-pill-Gate{background:#dcfce7;color:#166534;}        .nc-cat-Gate{border-left-color:#10b981;}
.nc-pill-Budget{background:#fef3c7;color:#92400e;}      .nc-cat-Budget{border-left-color:#f59e0b;}
.nc-pill-Mention{background:#ccfbf1;color:#0f766e;}     .nc-cat-Mention{border-left-color:#14b8a6;}
.nc-pill-Other{background:#f1f5f9;color:#475569;}       .nc-cat-Other{border-left-color:#64748b;}
.nc-pill-wa{background:#e0e7ff;color:#3730a3;}

/* ── focus-visible (keyboard) ───────────────────────────────────── */
.nc-chip:focus-visible,.nc-markread:focus-visible,.nc-open:focus-visible,.nc-loadmore:focus-visible,.nc-notif-main:focus-visible,.nc-doclink:focus-visible,.nc-collapse:focus-visible,.nc-retry:focus-visible{outline:2px solid var(--primary,#3b82f6);outline-offset:2px;border-radius:8px;}

/* ═══ DARK MODE ═══════════════════════════════════════════════════ */
[data-theme="dark"] .nc-section{background:#1f2937;border-color:#374151;}
[data-theme="dark"] .nc-bell-chip{background:#111827;border-color:#374151;}
[data-theme="dark"] .nc-card{background:#111827;border-color:#374151;}
[data-theme="dark"] .nc-chip{background:#111827;border-color:#374151;}
[data-theme="dark"] .nc-chip:hover{background:#0b1220;}
[data-theme="dark"] .nc-markread{background:#111827;border-color:#374151;}
[data-theme="dark"] .nc-markread:hover{background:rgba(16,185,129,.12);border-color:#34d399;color:#34d399;}
[data-theme="dark"] .nc-notif{background:#111827;border-color:#374151;}
[data-theme="dark"] .nc-group-read .nc-notif:hover,[data-theme="dark"] .nc-group-read .nc-notif:focus-within{background:#111827;border-color:#374151;}
[data-theme="dark"] .nc-pending-row{background:#111827;border-color:#374151;}
[data-theme="dark"] .nc-skel{background:#374151;}
[data-theme="dark"] .nc-loadmore{color:#e5e7eb;border-color:#374151;}
[data-theme="dark"] .nc-loadmore:hover{background:#0b1220;}
[data-theme="dark"] .nc-list-loading{background:rgba(17,24,39,.6);}
[data-theme="dark"] .nc-group-count{background:#374151;color:#e5e7eb;}
[data-theme="dark"] .nc-group-unread{background:rgba(59,130,246,.08);border-color:rgba(59,130,246,.25);}
[data-theme="dark"] .nc-group-unread .nc-group-count{background:#ef4444;color:#fff;}
[data-theme="dark"] .nc-card.nc-cat-PO{background:rgba(59,130,246,.12);border-color:rgba(59,130,246,.32);}          [data-theme="dark"] .nc-card.nc-cat-PO .nc-card-count{color:#93c5fd;}
[data-theme="dark"] .nc-card.nc-cat-MR{background:rgba(6,182,212,.12);border-color:rgba(6,182,212,.32);}            [data-theme="dark"] .nc-card.nc-cat-MR .nc-card-count{color:#67e8f9;}
[data-theme="dark"] .nc-card.nc-cat-Inspection{background:rgba(236,72,153,.12);border-color:rgba(236,72,153,.32);}  [data-theme="dark"] .nc-card.nc-cat-Inspection .nc-card-count{color:#f9a8d4;}
[data-theme="dark"] .nc-card.nc-cat-Production{background:rgba(139,92,246,.12);border-color:rgba(139,92,246,.32);}  [data-theme="dark"] .nc-card.nc-cat-Production .nc-card-count{color:#c4b5fd;}
[data-theme="dark"] .nc-card.nc-cat-Gate{background:rgba(16,185,129,.12);border-color:rgba(16,185,129,.32);}        [data-theme="dark"] .nc-card.nc-cat-Gate .nc-card-count{color:#86efac;}
[data-theme="dark"] .nc-card.nc-cat-Budget{background:rgba(245,158,11,.12);border-color:rgba(245,158,11,.32);}      [data-theme="dark"] .nc-card.nc-cat-Budget .nc-card-count{color:#fcd34d;}
[data-theme="dark"] .nc-card.nc-cat-Other{background:rgba(100,116,139,.12);border-color:rgba(100,116,139,.32);}     [data-theme="dark"] .nc-card.nc-cat-Other .nc-card-count{color:#cbd5e1;}
[data-theme="dark"] .nc-pill-PO{background:rgba(59,130,246,.2);color:#93c5fd;}
[data-theme="dark"] .nc-pill-MR{background:rgba(6,182,212,.2);color:#67e8f9;}
[data-theme="dark"] .nc-pill-Inspection{background:rgba(236,72,153,.2);color:#f9a8d4;}
[data-theme="dark"] .nc-pill-Production{background:rgba(139,92,246,.2);color:#c4b5fd;}
[data-theme="dark"] .nc-pill-Gate{background:rgba(16,185,129,.2);color:#86efac;}
[data-theme="dark"] .nc-pill-Budget{background:rgba(245,158,11,.2);color:#fcd34d;}
[data-theme="dark"] .nc-pill-Mention{background:rgba(20,184,166,.2);color:#5eead4;}
[data-theme="dark"] .nc-pill-Other{background:rgba(100,116,139,.2);color:#cbd5e1;}
[data-theme="dark"] .nc-pill-wa{background:rgba(99,102,241,.2);color:#c7d2fe;}
[data-theme="dark"] .nc-open{background:rgba(59,130,246,.2);color:#93c5fd;border-color:rgba(59,130,246,.35);}
[data-theme="dark"] .nc-open:hover{background:rgba(59,130,246,.32);color:#bfdbfe;}
[data-theme="dark"] .nc-tag-overdue{background:rgba(245,158,11,.2);color:#fcd34d;}
[data-theme="dark"] .nc-error{color:#fca5a5;}
[data-theme="dark"] .nc-overdue{color:#fcd34d;}
[data-theme="dark"] .nc-empty-ok .nc-empty-glyph{color:#34d399;}
[data-theme="dark"] .nc-empty-ok .nc-empty-title{color:#34d399;}
[data-theme="dark"] .nc-since-label{color:#93c5fd;}
[data-theme="dark"] .nc-doclink,[data-theme="dark"] .nc-since{color:var(--text-color);}

/* ── reduced motion ─────────────────────────────────────────────── */
@media (prefers-reduced-motion:reduce){
	.nc-skel{animation:none;}
	.nc-spin,.nc-spin-lg{animation:ncpulse 1.2s infinite;border-right-color:currentColor;}
	.nc-notif,.nc-pending-row{transition:none;}
}

/* ── mobile (≤768px) ────────────────────────────────────────────── */
@media (max-width:1100px){
	.nc-cols{grid-template-columns:1fr;}
}
@media (max-width:768px){
	.nc-cols{grid-template-columns:1fr;}
	.nc-header{flex-wrap:wrap;}
	.nc-head-badges{margin-left:0;width:100%;}
	.nc-cards{grid-template-columns:repeat(2,1fr);}
	.nc-chipstrip{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px;}
	.nc-chip{flex:none;}
	.nc-section h3{flex-wrap:wrap;}
	#nc-s2-controls{margin-left:0;width:100%;flex-wrap:wrap;}
	.nc-days{font-size:16px;height:36px;}
	.nc-search{font-size:16px;height:36px;width:100%;}
	.nc-markread{min-width:44px;min-height:44px;}
	.nc-open{min-height:40px;display:inline-flex;align-items:center;}
	.nc-pending-row{flex-wrap:wrap;}
}
`;
