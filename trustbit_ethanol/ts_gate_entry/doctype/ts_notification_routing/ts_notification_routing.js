// Copyright (c) 2026, Trustbit Software and contributors
// v2.58.0 — Notification Routing: a role-holder checklist that edits the hidden
// `users` table. The server re-validates every tick on save.

const TS_NR_SELECTED = "Selected users only";

frappe.ui.form.on("TS Notification Routing", {
	setup(frm) {
		// IT Head often has no read on Role: search through our admin-only query
		// and skip the client link check (the server still validates the role).
		frm.set_query("role", () => ({
			query: "trustbit_ethanol.ts_gate_entry.ts_notification_routing.role_search",
		}));
		frm.fields_dict.role.df.ignore_link_validation = 1;
	},
	refresh(frm) {
		// The form swaps in a per-document copy of each field on refresh, which
		// drops the flag set in setup — set it on the copy the control now uses.
		frm.set_df_property("role", "ignore_link_validation", 1);
		ts_nr_load(frm);
	},
	role(frm) {
		ts_nr_load(frm);
	},
	routing_mode(frm) {
		if (frm.__ts_nr_holders) ts_nr_draw(frm, frm.__ts_nr_holders);
	},
});

function ts_nr_load(frm) {
	const field = frm.get_field("holders_html");
	if (!field) return;
	frm.__ts_nr_holders = null;
	if (!frm.doc.role) {
		field.$wrapper.html(`<p class="text-muted">${__("Pick a role to see who holds it.")}</p>`);
		return;
	}
	const token = (frm.__ts_nr_token = (frm.__ts_nr_token || 0) + 1);
	field.$wrapper.html(`<p class="text-muted">${__("Loading role holders…")}</p>`);
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_notification_routing.get_role_holders",
		args: { role: frm.doc.role },
		callback(r) {
			if (token !== frm.__ts_nr_token) return; // a newer load won
			frm.__ts_nr_holders = (r.message && r.message.holders) || [];
			ts_nr_draw(frm, frm.__ts_nr_holders);
		},
		error() {
			if (token !== frm.__ts_nr_token) return;
			field.$wrapper.html(`<p class="text-danger">${__("Could not load the role holders. Reload the page to try again.")}</p>`);
		},
	});
}

function ts_nr_draw(frm, holders) {
	const esc = frappe.utils.escape_html;
	const $w = frm.get_field("holders_html").$wrapper;
	const selected = frm.doc.routing_mode === TS_NR_SELECTED;
	const ticked = new Set((frm.doc.users || []).map((r) => r.user));
	const holder_set = new Set(holders.map((h) => h.user));
	const stale = (frm.doc.users || []).filter((r) => !holder_set.has(r.user));
	const n_ticked = holders.filter((h) => ticked.has(h.user)).length;

	let summary, tone;
	if (!holders.length) {
		summary = __("Nobody holds this role right now.");
		tone = "alert-secondary";
	} else if (!selected) {
		summary = __("All {0} holders are notified. Switch Routing to Selected users only to choose who receives these notifications.", [holders.length]);
		tone = "alert-secondary";
	} else if (!n_ticked) {
		summary = __("Tick at least one user. A role cannot be muted.");
		tone = "alert-warning";
	} else {
		summary = __("{0} of {1} holders will be notified. {2} will stop receiving this role's notifications; anything that names them personally still reaches them.", [n_ticked, holders.length, holders.length - n_ticked]);
		tone = "alert-info";
	}

	const rows = holders.map((h) => `
		<tr>
			<td style="width: 36px">
				<input type="checkbox" class="ts-nr-tick" data-user="${esc(h.user)}"
					${!selected || ticked.has(h.user) ? "checked" : ""} ${selected ? "" : "disabled"}
					aria-label="${esc(__("Notify {0}", [h.full_name]))}">
			</td>
			<td><div class="bold">${esc(h.full_name)}</div><div class="text-muted small">${esc(h.user)}</div></td>
			<td class="text-right">${cint(h.other_roles)}</td>
			<td class="text-right">${cint(h.bells_30d)}</td>
			<td class="text-right">${h.read_pct === null || h.read_pct === undefined ? "—" : cint(h.read_pct) + "%"}</td>
			<td class="text-muted">${esc(h.last_active || "")}</td>
		</tr>`).join("");

	const stale_html = stale.length ? `
		<div class="alert alert-warning">
			<div class="bold">${__("These ticked users no longer hold the role or are disabled. Untick them before saving:")}</div>
			${stale.map((r) => `
				<label style="display: block; margin: 6px 0 0">
					<input type="checkbox" class="ts-nr-tick" data-user="${esc(r.user)}" checked> ${esc(r.full_name || r.user)}
				</label>`).join("")}
		</div>` : "";

	$w.html(`
		<div class="alert ${tone}">${summary}</div>
		${stale_html}
		${selected && holders.length ? `
			<div style="display: flex; gap: 8px; margin-bottom: 8px">
				<button type="button" class="btn btn-xs btn-default ts-nr-all">${__("Tick all")}</button>
				<button type="button" class="btn btn-xs btn-default ts-nr-none">${__("Clear")}</button>
			</div>` : ""}
		${holders.length ? `
			<div class="table-responsive">
				<table class="table table-sm" style="margin-bottom: 4px">
					<thead><tr>
						<th></th>
						<th>${__("Holder of {0} ({1})", [esc(frm.doc.role), holders.length])}</th>
						<th class="text-right">${__("Other Roles")}</th>
						<th class="text-right">${__("Notifications, Last 30 Days")}</th>
						<th class="text-right">${__("Read")}</th>
						<th>${__("Last Active")}</th>
					</tr></thead>
					<tbody>${rows}</tbody>
				</table>
			</div>
			<p class="text-muted small">${__("Notification counts cover every kind of notification, not only this role's.")}</p>` : ""}
	`);

	$w.find(".ts-nr-tick").on("change", function () {
		ts_nr_set_users(frm, holders, (users) => {
			const user = $(this).attr("data-user");
			return this.checked ? users.concat([user]) : users.filter((u) => u !== user);
		});
	});
	$w.find(".ts-nr-all").on("click", () => ts_nr_set_users(frm, holders, () => holders.map((h) => h.user)));
	$w.find(".ts-nr-none").on("click", () => ts_nr_set_users(frm, holders, () => []));
}

function ts_nr_set_users(frm, holders, change) {
	const names = Object.fromEntries(holders.map((h) => [h.user, h.full_name]));
	const users = [...new Set(change((frm.doc.users || []).map((r) => r.user)))];
	frm.clear_table("users");
	users.forEach((user) => frm.add_child("users", { user, full_name: names[user] || "" }));
	frm.refresh_field("users");
	frm.dirty();
	ts_nr_draw(frm, holders);
}
