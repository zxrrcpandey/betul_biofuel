/* ═══════════════════════════════════════════════════════════════════
   Assign People (v2.61.0) — IT Head / System Manager.
     1. Department Heads, read from the approval configs (never edited here:
        each cost centre links to its config).
     2. Notify-only people per cost centre, edited here and saved to
        TS Notification Audience — never written to an approval config.
   ═══════════════════════════════════════════════════════════════════ */

const AP_API = "trustbit_ethanol.ts_gate_entry.assign_people_api";
const AP_USER_SEARCH = "trustbit_ethanol.ts_gate_entry.ts_notification_routing.user_search";
let ap_state = { page: null, data: null, people: {}, dirty: false, search: "", used_only: true, all_no_head: false };

frappe.pages["assign-people"].on_page_load = function (wrapper) {
	console.log("[assign-people]", "v2.61.0", "loaded");
	const page = ap_state.page = frappe.ui.make_app_page({ parent: wrapper, title: __("Assign People"), single_column: true });
	if (!document.getElementById("ap-styles")) {
		const s = document.createElement("style");
		s.id = "ap-styles";
		document.head.appendChild(s);
	}
	document.getElementById("ap-styles").textContent = AP_CSS;
	page.set_primary_action(__("Save notify-only people"), () => ap_save());
	page.add_button(__("Who gets each notification"), () => frappe.set_route("Form", "TS Notification Audience"));
	page.main.html(`<div id="ap-root"><div class="ap-muted" style="padding:40px;text-align:center;">${__("Loading…")}</div></div>`);
};
// Runs on every show, the first one included (pageview.js), so the first load happens here.
frappe.pages["assign-people"].refresh = () => { if (!ap_state.dirty) ap_load(); };

const ap_esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
const ap_cfg_url = (cc) => `/app/ts-cc-approval-config/${encodeURIComponent(ap_state.data.configs[cc] || cc)}`;

async function ap_load() {
	try {
		const r = await frappe.call({ method: `${AP_API}.get_assign_people` });
		ap_state.data = r.message;
		ap_state.people = {};
		for (const row of ap_state.data.cost_centers) ap_state.people[row.cost_center] = row.people.slice();
		ap_state.dirty = false;
		ap_state.page.clear_indicator();
		ap_render();
	} catch (e) {
		console.error(e);
		$("#ap-root").html(`<div class="ap-error">${__("Couldn't load the page.")}
			<button type="button" class="ap-add ap-retry">${__("Try again")}</button></div>`);
		$("#ap-root .ap-retry").on("click", () => ap_load());
	}
}

function ap_render() {
	try {
		const d = ap_state.data;
		$("#ap-root").html(`
			<p class="ap-lead">${__("The people behind the \"Dept Head\" and \"CC Notify\" ticks on")}
				<a href="/app/ts-notification-audience">${__("Who gets each notification")}</a>.</p>
			<section class="ap-section">
				<h3>${__("Department Heads")} <span class="ap-muted ap-small">${__("from the approval configs · {0} people · {1} configs",
					[d.heads.length, Object.keys(d.configs).length])}</span></h3>
				<p class="ap-text">${__("A Department Head is a person on a cost centre's approval config with the role Department Head who can approve. They are told about that cost centre's inspections and can inspect them. To change who heads a cost centre, open its approval config: this page never changes a config.")}</p>
				${ap_heads_html(d)}
				${ap_no_head_html(d)}
			</section>
			<section class="ap-section">
				<h3>${__("Notify-only people, by cost centre")} <span class="ap-muted ap-small">${__("{0} cost centres used on Material Requests in the last 90 days",
					[d.cost_centers.filter(r => r.used).length + d.unconfigured.length])}</span></h3>
				<p class="ap-text">${__("People told about a cost centre's events without approving anything. They are kept on this page, not on the approval config, so adding someone never changes which Material Requests they can see. \"Notify Only\" rows already on a config still count and show as grey chips.")}</p>
				<div class="ap-tools">
					<input type="search" class="ap-search" placeholder="${__("Find a cost centre")}" aria-label="${__("Find a cost centre")}" value="${ap_esc(ap_state.search)}">
					<label class="ap-check"><input type="checkbox" class="ap-used" ${ap_state.used_only ? "checked" : ""}> ${__("Only cost centres used in the last 90 days")}</label>
					<span class="ap-dirty" ${ap_state.dirty ? "" : "hidden"}>${__("Unsaved changes")}</span>
				</div>
				<div id="ap-cc-table"></div>
				<p class="ap-muted ap-small">${__("\"Told about\" lists the notifications whose \"CC Notify\" tick is on.")}</p>
			</section>
			<section class="ap-section ap-info">
				<div class="ap-info-title">${__("What this changes")}</div>
				<p>${__("With \"CC Notify\" ticked and \"Team\" unticked on MR approved, an approved MR reaches its requester and the notify-only people of its cost centre instead of every purchase-role holder.")}</p>
				<p>${__("An inspection reaches its requester and the Department Heads on its cost centre's approval config. They can inspect it, and it appears under \"Assigned to me\" for them.")}</p>
				<p class="ap-small">${__("Approval steps are not affected: each cost centre's approvers stay on its approval config, exactly as today.")}</p>
			</section>`);
		ap_render_cc_table();
		$("#ap-root .ap-search").on("input", function () { ap_state.search = $(this).val(); ap_render_cc_table(); });
		$("#ap-root .ap-used").on("change", function () { ap_state.used_only = $(this).is(":checked"); ap_render_cc_table(); });
		$("#ap-root .ap-show-all").on("click", () => { ap_state.all_no_head = true; ap_render(); });
	} catch (e) {
		console.error(e);
		$("#ap-root").html(`<div class="ap-error">${__("Couldn't draw the page.")}</div>`);
	}
}

function ap_cc_link(cc) {
	// Plain text for someone who can't open approval configs (a link would only error).
	return ap_state.data.can_open_configs
		? `<a class="ap-cc" href="${ap_cfg_url(cc)}">${ap_esc(cc)}</a>`
		: `<span class="ap-cc">${ap_esc(cc)}</span>`;
}

function ap_heads_html(d) {
	if (!d.heads.length) return `<p class="ap-muted">${__("No approval config names a Department Head yet.")}</p>`;
	const rows = d.heads.map(h => `
		<tr><th scope="row">${ap_esc(h.full_name)}<div class="ap-muted ap-small">${h.cost_centers.length === 1
			? __("1 cost centre") : __("{0} cost centres", [h.cost_centers.length])}</div></th>
		<td><div class="ap-chips">${h.cost_centers.map(ap_cc_link).join("")}</div></td></tr>`).join("");
	return `<div class="ap-table-wrap"><table class="ap-table">
		<thead><tr><th scope="col" style="width:240px">${__("Person")}</th><th scope="col">${__("Heads these cost centres (opens the approval config)")}</th></tr></thead>
		<tbody>${rows}</tbody></table></div>`;
}

function ap_no_head_html(d) {
	if (!d.no_head.length) return "";
	const shown = ap_state.all_no_head ? d.no_head : d.no_head.slice(0, 12);
	const more = d.no_head.length > shown.length
		? `<button type="button" class="ap-add ap-show-all">${__("Show all {0}", [d.no_head.length])}</button>` : "";
	return `<div class="ap-warn">
		<div class="ap-warn-title">${d.no_head.length === 1
			? __("1 cost centre has no Department Head who can approve on its config")
			: __("{0} cost centres have no Department Head who can approve on their config", [d.no_head.length])}</div>
		<div class="ap-small">${__("Inspections there go only to the head on the requester's Employee record, if one is set.")}</div>
		<div class="ap-chips">${shown.map(ap_cc_link).join("")}${more}</div></div>`;
}

function ap_render_cc_table() {
	const d = ap_state.data;
	const q = (ap_state.search || "").toLowerCase();
	const match = (cc) => !q || cc.toLowerCase().includes(q);
	const rows = d.cost_centers.filter(r => match(r.cost_center) && (!ap_state.used_only || r.used
		|| (ap_state.people[r.cost_center] || []).length || r.fixed.length));
	const told = d.told_about.length ? d.told_about.join(", ") : __("nothing: the tick is off everywhere");
	let html = rows.map(r => {
		const mine = ap_state.people[r.cost_center] || [];
		const chips = mine.map((p, i) => `<span class="ap-chip ${p.enabled === 0 ? "ap-chip-off" : ""}">${ap_esc(p.full_name || p.user)}${p.enabled === 0 ? ` · ${__("disabled")}` : ""}
				<button type="button" class="ap-remove" data-cc="${ap_esc(r.cost_center)}" data-i="${i}" aria-label="${__("Remove {0}", [ap_esc(p.full_name || p.user)])}">×</button></span>`).join("")
			+ r.fixed.map(p => `<span class="ap-chip ap-chip-fixed" title="${__("Set on the approval config")}">${ap_esc(p.full_name)} · ${__("on the config")}</span>`).join("");
		const anyone = mine.length || r.fixed.length;
		return `<tr><th scope="row">${ap_esc(r.cost_center)}</th>
			<td><div class="ap-chips">${chips}<button type="button" class="ap-add ap-add-person" data-cc="${ap_esc(r.cost_center)}">+ ${__("Person")}</button></div></td>
			<td class="${anyone ? "" : "ap-muted"}">${anyone ? ap_esc(told) : __("nobody yet")}</td></tr>`;
	}).join("");
	html += d.unconfigured.filter(match).map(cc => `<tr><th scope="row" class="ap-muted">${ap_esc(cc)}</th>
		<td colspan="2" class="ap-muted ap-small">${__("No approval config yet.")} <a href="/app/ts-cc-approval-config/new">${__("Set one up first")}</a>:
		${__("a config also controls who approves and who sees this cost centre's MRs, so this page never creates one by itself.")}</td></tr>`).join("");
	$("#ap-cc-table").html(html
		? `<div class="ap-table-wrap"><table class="ap-table"><thead><tr>
			<th scope="col" style="width:240px">${__("Cost centre")}</th><th scope="col">${__("Notify-only people")}</th>
			<th scope="col" style="width:200px">${__("Told about")}</th></tr></thead><tbody>${html}</tbody></table></div>`
		: `<p class="ap-muted">${q || !ap_state.used_only ? __("No cost centre matches.")
			: __("No cost centre was used in the last 90 days. Untick the box to see all.")}</p>`);
	$("#ap-cc-table .ap-remove").on("click", function () {
		const cc = $(this).attr("data-cc"), i = cint($(this).attr("data-i"));
		(ap_state.people[cc] || []).splice(i, 1);
		ap_mark_dirty(cc);
	});
	$("#ap-cc-table .ap-add-person").on("click", function () { ap_add_person($(this).attr("data-cc")); });
}

function ap_add_person(cc) {
	const dlg = new frappe.ui.Dialog({
		title: __("Add a notify-only person for {0}", [ap_esc(cc)]),
		fields: [{ fieldname: "user", fieldtype: "Link", options: "User", label: __("Person"), reqd: 1,
			get_query: () => ({ query: AP_USER_SEARCH }) }],
		primary_action_label: __("Add"),
		primary_action(v) {
			const list = ap_state.people[cc] = ap_state.people[cc] || [];
			if (!list.some(p => p.user === v.user)) list.push({ user: v.user, full_name: v.user, enabled: 1 });
			dlg.hide();
			ap_mark_dirty(cc);
		},
	});
	// IT Head usually cannot read User: search through the admin-only query and
	// skip the client link check (the server checks each user on save).
	dlg.fields_dict.user.df.ignore_link_validation = 1;
	dlg.show();
}

function ap_mark_dirty(cc) {
	ap_state.dirty = true;
	ap_state.page.set_indicator(__("Not Saved"), "orange");
	$("#ap-root .ap-dirty").prop("hidden", false);
	ap_render_cc_table();
	// The table was redrawn: keep keyboard focus on this cost centre's row.
	$("#ap-cc-table .ap-add-person").filter((_, el) => el.dataset.cc === cc).trigger("focus");
}

async function ap_save() {
	const rows = [];
	for (const [cc, list] of Object.entries(ap_state.people)) for (const p of list) rows.push({ cost_center: cc, user: p.user });
	try {
		const r = await frappe.call({ method: `${AP_API}.save_notify_people`, type: "POST", args: { rows: JSON.stringify(rows) }, freeze: true });
		frappe.show_alert({ message: __("Saved {0} notify-only people", [r.message.saved]), indicator: "green" });
		await ap_load();
	} catch (e) {
		console.error(e);  // the server's message is already on screen
	}
}

const AP_CSS = `
#ap-root{max-width:1296px;margin:0 auto;padding:4px 0 32px;display:flex;flex-direction:column;gap:16px;}
.ap-lead{font-size:14px;color:var(--text-color);margin:0;}
.ap-section{background:var(--card-bg,#fff);border:1px solid var(--border-color,#e2e8f0);border-radius:12px;padding:18px;display:flex;flex-direction:column;gap:12px;}
.ap-section h3{margin:0;font-size:17px;display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;}
.ap-text{font-size:13px;color:var(--text-color);max-width:1040px;line-height:1.5;margin:0;}
.ap-muted{color:var(--text-muted);}
.ap-small{font-size:12px;font-weight:400;}
.ap-table-wrap{border:1px solid var(--border-color,#e2e8f0);border-radius:10px;overflow-x:auto;}
.ap-table{width:100%;min-width:640px;border-collapse:collapse;font-size:13px;}
.ap-table th,.ap-table td{padding:10px 14px;text-align:left;vertical-align:top;border-bottom:1px solid var(--border-color,#eef0f2);}
.ap-table thead th{font-size:12px;font-weight:600;color:var(--text-muted);background:var(--subtle-fg,#fafbfc);}
.ap-table tbody tr:last-child th,.ap-table tbody tr:last-child td{border-bottom:0;}
.ap-chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;}
.ap-cc{display:inline-flex;align-items:center;border-radius:999px;padding:4px 10px;font-size:12px;background:var(--subtle-fg,#eef0f2);color:var(--text-color);text-decoration:none;border:1px solid transparent;white-space:nowrap;}
.ap-cc:hover{border-color:var(--border-color,#b9c2ca);color:var(--text-color);text-decoration:none;}
.ap-chip{display:inline-flex;align-items:center;gap:2px;border-radius:999px;padding:2px 2px 2px 10px;font-size:12px;background:#dbeafe;color:#1e3a8a;white-space:nowrap;}
.ap-chip-fixed{padding:4px 10px;background:var(--subtle-fg,#eef0f2);color:var(--text-color);}
.ap-chip-off{background:#fee2e2;color:#7f1d1d;}
.ap-remove{width:28px;height:28px;border:0;background:transparent;border-radius:999px;color:inherit;font-size:15px;line-height:1;cursor:pointer;}
.ap-remove:hover{background:rgba(15,23,42,.08);}
.ap-add{min-height:32px;border:1px dashed var(--border-color,#b9c2ca);background:transparent;border-radius:999px;padding:4px 12px;font:inherit;font-size:12px;color:#1d4ed8;cursor:pointer;}
.ap-warn{display:flex;flex-direction:column;gap:6px;background:#fff8f1;border:1px solid #f3c99a;border-radius:10px;padding:12px 14px;color:#7c2d12;}
.ap-warn-title{font-size:13px;font-weight:600;}
.ap-tools{display:flex;flex-wrap:wrap;gap:10px;align-items:center;}
.ap-search{min-height:36px;min-width:240px;border:1px solid var(--border-color,#d5dbe0);border-radius:8px;padding:6px 10px;font:inherit;font-size:13px;background:var(--control-bg,#fff);color:var(--text-color);}
.ap-check{display:inline-flex;align-items:center;gap:8px;font-size:13px;margin:0;}
.ap-dirty{font-size:12px;font-weight:600;color:#b45309;}
.ap-error{padding:24px;text-align:center;color:#b91c1c;display:flex;flex-direction:column;align-items:center;gap:12px;}
.ap-info{background:#f1f6fd;border-color:#b9d3f2;gap:6px;}
.ap-info p{margin:0;font-size:14px;line-height:1.5;color:var(--text-color);}
.ap-info-title{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#164f96;}
.ap-cc:focus-visible,.ap-remove:focus-visible,.ap-add:focus-visible,.ap-search:focus-visible{outline:2px solid #3b82f6;outline-offset:1px;}
[data-theme="dark"] .ap-section{background:#1f2937;border-color:#374151;}
[data-theme="dark"] .ap-table thead th{background:#111827;}
[data-theme="dark"] .ap-cc,[data-theme="dark"] .ap-chip.ap-chip-fixed{background:#111827;color:#e5e7eb;}
[data-theme="dark"] .ap-table-wrap,[data-theme="dark"] .ap-table th,[data-theme="dark"] .ap-table td,[data-theme="dark"] .ap-add{border-color:#374151;}
[data-theme="dark"] .ap-search{background:#111827;border-color:#4b5563;}
[data-theme="dark"] .ap-cc:hover{border-color:#6b7280;}
[data-theme="dark"] .ap-remove:hover{background:rgba(255,255,255,.12);}
[data-theme="dark"] .ap-error{color:#fca5a5;}
[data-theme="dark"] .ap-info{background:rgba(59,130,246,.10);border-color:rgba(59,130,246,.35);}
[data-theme="dark"] .ap-info-title{color:#93c5fd;}
[data-theme="dark"] .ap-chip{background:rgba(59,130,246,.18);color:#bfdbfe;}
[data-theme="dark"] .ap-chip-off{background:rgba(239,68,68,.18);color:#fecaca;}
[data-theme="dark"] .ap-add{color:#93c5fd;}
[data-theme="dark"] .ap-warn{background:rgba(245,158,11,.10);border-color:rgba(245,158,11,.35);color:#fcd34d;}
[data-theme="dark"] .ap-dirty{color:#fbbf24;}
@media (max-width:768px){
	.ap-section{padding:14px;}
	.ap-search{width:100%;min-width:0;font-size:16px;min-height:44px;}
	.ap-remove{width:44px;height:44px;}
	.ap-add{min-height:44px;}
	.ap-cc{min-height:40px;white-space:normal;}
	.ap-check{min-height:44px;}
}
`;
