frappe.pages["user-management"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "User Management",
		single_column: true,
	});

	// NOTE: do NOT call page.main.html(...) here. `page.page_form` — the bar that
	// page.add_field() renders into — is prepended INTO page.main by Frappe
	// (ui/page.js:143). Replacing main's contents detached it, so the Status
	// filter and Search box below were built into an orphaned node and never
	// appeared on screen. Append a child instead.
	page._um_$container = $('<div class="um-wrap"></div>').appendTo(page.main);

	page._um_users = [];
	page._um_allowed_roles = [];
	page._um_filter = "all";
	page._um_search = "";
	page._um_loaded_at = 0;

	page.add_field({
		fieldname: "status_filter",
		label: __("Status"),
		fieldtype: "Select",
		options: "All\nActive\nDisabled",
		default: "All",
		change() { page._um_filter = (this.get_value() || "all").toLowerCase(); _um_render_table(page); },
	});

	page.add_field({
		fieldname: "search",
		label: __("Search"),
		fieldtype: "Data",
		change() { page._um_search = (this.get_value() || "").toLowerCase(); _um_render_table(page); },
	});

	page.set_primary_action(__("+ New User"), () => _um_show_create_dialog(page), "es-line-add");
	page.add_inner_button(__("Change My Password"), () => _um_change_own_password());

	_um_load(page);
};

frappe.pages["user-management"].refresh = function (wrapper) {
	_um_load(wrapper.page);
};

// ── DATA LOADING ────────────────────────────────────────────────────

function _um_load(page) {
	if (!page || !page._um_$container) return;

	// on_page_load and the show-triggered refresh both fire on first open, which
	// used to fetch everything twice. Collapse near-simultaneous calls.
	const now = Date.now();
	if (now - (page._um_loaded_at || 0) < 500) return;
	page._um_loaded_at = now;

	const $c = page._um_$container;
	$c.html('<div class="um-loading">' + __("Loading users...") + "</div>");

	Promise.all([
		frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.get_allowed_roles"),
		frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.get_users"),
	]).then(([roles, users]) => {
		page._um_allowed_roles = roles || [];
		page._um_users = users || [];
		_um_render_table(page);
	}).catch((err) => {
		// This used to claim "Access Denied — Only IT Head" for every failure,
		// including timeouts and 500s — the one cause it essentially cannot be,
		// since the page only routes for IT Head in the first place. Report what
		// actually happened.
		const detail = (err && (err.message || err)) || __("Unknown error");
		$c.html(
			'<div class="um-error">' +
			'<div class="um-error__title">' + __("Could not load users") + "</div>" +
			'<div class="um-error__detail">' + frappe.utils.escape_html(String(detail)) + "</div>" +
			"</div>"
		);
	});
}

// ── TABLE RENDERING ─────────────────────────────────────────────────

function _um_render_table(page) {
	const $c = page._um_$container;
	if (!$c) return;

	const users = _um_filtered_users(page);
	const total = page._um_users.length;
	const active = page._um_users.filter((u) => u.enabled).length;
	const disabled = total - active;

	let html = `
		<div class="um-rolecount">${total ? page._um_allowed_roles.length : 0} ${__("roles available")}</div>
		<div class="um-stats">
			<div class="um-stat um-stat--total">
				<div class="um-stat__value">${total}</div>
				<div class="um-stat__label">${__("Total Users")}</div>
			</div>
			<div class="um-stat um-stat--active">
				<div class="um-stat__value">${active}</div>
				<div class="um-stat__label">${__("Active")}</div>
			</div>
			<div class="um-stat um-stat--disabled">
				<div class="um-stat__value">${disabled}</div>
				<div class="um-stat__label">${__("Disabled")}</div>
			</div>
		</div>

		<div class="um-tablecard">
			<table class="table table-hover um-table">
				<thead>
					<tr>
						<th>${__("User")}</th>
						<th>${__("Email")}</th>
						<th>${__("Roles")}</th>
						<th>${__("Last Active")}</th>
						<th>${__("Status")}</th>
						<th class="um-cell-actions">${__("Actions")}</th>
					</tr>
				</thead>
				<tbody>`;

	if (users.length === 0) {
		html += `<tr><td colspan="6" class="um-empty">${__("No users found")}</td></tr>`;
	}

	users.forEach((u) => {
		const status_badge = u.enabled
			? `<span class="um-badge um-badge--active">${__("Active")}</span>`
			: `<span class="um-badge um-badge--disabled">${__("Disabled")}</span>`;

		const roles = u.roles || [];
		const role_pills = roles.slice(0, 3).map(
			(r) => '<span class="um-role">' + frappe.utils.escape_html(r) + "</span>"
		).join("");
		const more = roles.length > 3
			? `<span class="um-role-more">+${roles.length - 3} ${__("more")}</span>`
			: "";

		const protected_badge = u.is_protected
			? ` <span class="um-flag um-flag--protected" title="${__("Protected — cannot edit here")}">&#128274;</span>`
			: "";
		const self_badge = u.is_self
			? ` <span class="um-flag um-flag--self" title="${__("This is you")}">(${__("You")})</span>`
			: "";

		const email_attr = frappe.utils.escape_html(u.name);
		const can_edit = !u.is_protected && !u.is_self;

		const edit_btn = can_edit
			? `<button class="btn btn-xs btn-default um-edit-btn" data-email="${email_attr}" style="margin-right:5px;">${__("Edit")}</button>`
			: "";
		const toggle_btn = can_edit
			? (u.enabled
				? `<button class="btn btn-xs btn-default um-toggle-btn" data-email="${email_attr}" data-enable="0">${__("Disable")}</button>`
				: `<button class="btn btn-xs btn-default um-toggle-btn" data-email="${email_attr}" data-enable="1">${__("Enable")}</button>`)
			: "";
		const reset_btn = can_edit
			? `<button class="btn btn-xs btn-default um-reset-btn" data-email="${email_attr}" style="margin-right:5px;" title="${__("Reset Password")}">&#128273;</button>`
			: "";

		html += `
			<tr>
				<td><b>${frappe.utils.escape_html(u.full_name || u.first_name || "")}</b>${protected_badge}${self_badge}</td>
				<td class="um-cell-email">${frappe.utils.escape_html(u.name)}</td>
				<td>${role_pills}${more}</td>
				<td class="um-cell-seen">${_um_when(u.last_active)}</td>
				<td>${status_badge}</td>
				<td class="um-cell-actions">${reset_btn}${edit_btn}${toggle_btn}</td>
			</tr>`;
	});

	html += `</tbody></table></div>`;
	$c.html(html);

	$c.find(".um-edit-btn").on("click", function () {
		_um_show_edit_dialog(page, $(this).data("email"));
	});
	$c.find(".um-toggle-btn").on("click", function () {
		_um_toggle_user(page, $(this).data("email"), parseInt($(this).data("enable"), 10));
	});
	$c.find(".um-reset-btn").on("click", function () {
		_um_reset_password(page, $(this).data("email"));
	});
}

function _um_when(value) {
	if (!value) return __("Never");
	try {
		// NOTE: use prettyDate (plain text), NOT comment_when — the latter returns
		// a ready-made `<span class="frappe-timestamp">…</span>`, so escaping it
		// printed the raw markup into the cell. Build the span here instead, so
		// the relative time and the absolute-date tooltip are both escaped by us.
		const rel = frappe.datetime.prettyDate(value);
		const abs = frappe.datetime.str_to_user(value);
		if (!rel) return frappe.utils.escape_html(abs || String(value));
		return (
			'<span title="' + frappe.utils.escape_html(abs || "") + '">' +
			frappe.utils.escape_html(rel) +
			"</span>"
		);
	} catch (e) {
		return frappe.utils.escape_html(String(value));
	}
}

function _um_filtered_users(page) {
	return page._um_users.filter((u) => {
		if (page._um_filter === "active" && !u.enabled) return false;
		if (page._um_filter === "disabled" && u.enabled) return false;
		if (page._um_search) {
			const s = page._um_search;
			const match =
				(u.full_name || "").toLowerCase().includes(s) ||
				(u.name || "").toLowerCase().includes(s) ||
				(u.roles || []).some((r) => r.toLowerCase().includes(s));
			if (!match) return false;
		}
		return true;
	});
}

// ── ROLE PICKER ─────────────────────────────────────────────────────

function _um_render_roles($wrapper, all_roles, checked_set) {
	let grid = '<div class="um-rolegrid">';
	all_roles.forEach((role) => {
		const checked = checked_set && checked_set.has(role);
		const safe = frappe.utils.escape_html(role);
		grid += `
			<label class="${checked ? "is-checked" : ""}">
				<input type="checkbox" class="um-role-check" value="${safe}" ${checked ? "checked" : ""}>
				<span>${safe}</span>
			</label>`;
	});
	grid += "</div>";
	$wrapper.html(grid);

	$wrapper.off("change.um").on("change.um", ".um-role-check", function () {
		$(this).closest("label").toggleClass("is-checked", this.checked);
	});
}

function _um_selected_roles(d) {
	const out = [];
	d.$wrapper.find(".um-role-check:checked").each(function () {
		out.push($(this).val());
	});
	return out;
}

// ── CREATE USER DIALOG ──────────────────────────────────────────────

function _um_show_create_dialog(page) {
	const d = new frappe.ui.Dialog({
		title: __("Create New User"),
		size: "large",
		fields: [
			{ fieldtype: "Section Break", label: __("User Details") },
			{ fieldname: "first_name", label: __("First Name"), fieldtype: "Data", reqd: 1 },
			{ fieldname: "last_name", label: __("Last Name"), fieldtype: "Data" },
			{ fieldtype: "Column Break" },
			{ fieldname: "email", label: __("Email"), fieldtype: "Data", options: "Email", reqd: 1 },
			{ fieldname: "mobile_no", label: __("Mobile"), fieldtype: "Data", options: "Phone" },
			{
				fieldname: "ts_whatsapp_number",
				label: __("WhatsApp Number"),
				fieldtype: "Data",
				description: __("Country code + number, e.g. 919812345678 (no +). For WhatsApp notifications."),
			},
			{
				fieldname: "ts_whatsapp_opt_in",
				label: __("WhatsApp Opt-In"),
				fieldtype: "Check",
				// Matches the Custom Field's own declared default of 0. Consent is
				// recorded deliberately, not by leaving a pre-ticked box alone.
				default: 0,
			},
			{
				fieldtype: "Section Break",
				label: __("Assign Roles"),
				description: __("Select the roles this user should have. Only operational roles are available."),
			},
			{ fieldname: "roles_html", fieldtype: "HTML" },
			{
				fieldtype: "Section Break",
				label: __("Password"),
				description: __("If a default outgoing email account is configured, a welcome email is sent. Otherwise a one-time setup link is shown to you here, to pass on to the user."),
			},
			{
				fieldname: "send_welcome_email",
				label: __("Send Welcome Email (user sets own password)"),
				fieldtype: "Check",
				default: 1,
			},
		],
		primary_action_label: __("Create User"),
		primary_action(values) {
			const selected_roles = _um_selected_roles(d);
			if (selected_roles.length === 0) {
				frappe.msgprint(__("Please select at least one role."));
				return;
			}

			d.disable_primary_action();

			frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.create_user", {
				first_name: values.first_name,
				last_name: values.last_name,
				email: values.email,
				mobile_no: values.mobile_no,
				whatsapp_number: values.ts_whatsapp_number,
				whatsapp_opt_in: values.ts_whatsapp_opt_in ? 1 : 0,
				roles: selected_roles,
				send_welcome_email: values.send_welcome_email ? 1 : 0,
			}).then((result) => {
				d.hide();

				if (result.reset_link) {
					_um_show_link_dialog({
						title: __("User Created"),
						lead: `<p><b>${frappe.utils.escape_html(result.full_name)}</b> (${frappe.utils.escape_html(result.user)}) ${__("has been created.")}</p>`,
						link: result.reset_link,
						expires_in: result.expires_in,
						tail: `<p><b>${__("Roles")}:</b> ${result.roles.map((r) => frappe.utils.escape_html(r)).join(", ")}</p>`,
					});
				} else if (result.send_welcome_email) {
					// Say what was actually done — queued, not delivered. Delivery
					// depends on the mail queue, which can be failing silently.
					frappe.msgprint({
						title: __("User Created"),
						indicator: "green",
						message: `
							<p><b>${frappe.utils.escape_html(result.user)}</b> ${__("has been created.")}</p>
							<p>${__("A welcome email has been <b>queued</b>. If the user does not receive it, check the Email Queue and then use the key icon to issue a one-time reset link you can send them directly.")}</p>
						`,
					});
				} else {
					frappe.show_alert({ message: __("User {0} created", [result.user]), indicator: "green" }, 5);
				}

				_um_force_reload(page);
			}).catch(() => {
				d.enable_primary_action();
			});
		},
	});

	_um_render_roles(d.fields_dict.roles_html.$wrapper, page._um_allowed_roles, new Set());
	d.show();
}

// ── EDIT USER DIALOG ────────────────────────────────────────────────

function _um_show_edit_dialog(page, email) {
	frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.get_user_detail", {
		email: email,
	}).then((user) => {
		if (user.is_protected) {
			frappe.msgprint(__("This user is protected and cannot be edited here."));
			return;
		}
		if (user.is_self) {
			frappe.msgprint(__("You cannot edit your own account."));
			return;
		}

		const current_roles = new Set(user.roles || []);

		const d = new frappe.ui.Dialog({
			// Dialog.set_title() assigns with .html(), and full_name is attacker-
			// controllable: every user holds a self-DocShare with write=1, so any
			// operator can put markup in their own first/last name. clean_name()
			// strips scripts but not <img>/<style>, which is still a beacon and a
			// UI-redress vector inside the IT Head's dialog.
			title: __("Edit User — {0}", [frappe.utils.escape_html(user.full_name || email)]),
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "info_html" },
				{ fieldtype: "Section Break", label: __("Details") },
				{ fieldname: "first_name", label: __("First Name"), fieldtype: "Data", reqd: 1, default: user.first_name || "" },
				{ fieldname: "last_name", label: __("Last Name"), fieldtype: "Data", default: user.last_name || "" },
				{ fieldtype: "Column Break" },
				{ fieldname: "mobile_no", label: __("Mobile"), fieldtype: "Data", options: "Phone", default: user.mobile_no || "" },
				{ fieldtype: "Section Break", label: __("WhatsApp") },
				{
					fieldname: "ts_whatsapp_number",
					label: __("WhatsApp Number"),
					fieldtype: "Data",
					description: __("Country code + number, e.g. 919812345678 (no +)."),
					default: user.whatsapp_number || "",
				},
				{
					fieldname: "ts_whatsapp_opt_in",
					label: __("WhatsApp Opt-In"),
					fieldtype: "Check",
					default: user.whatsapp_opt_in ? 1 : 0,
				},
				{ fieldtype: "Section Break", label: __("Roles") },
				{ fieldname: "roles_html", fieldtype: "HTML" },
				{ fieldtype: "Section Break", label: __("Recent Activity"), collapsible: 1 },
				{ fieldname: "audit_html", fieldtype: "HTML" },
			],
			primary_action_label: __("Save"),
			primary_action(values) {
				const selected_roles = _um_selected_roles(d);
				if (selected_roles.length === 0) {
					frappe.msgprint(__("Please select at least one role."));
					return;
				}

				const wa_number = values.ts_whatsapp_number || "";
				const wa_opt = values.ts_whatsapp_opt_in ? 1 : 0;

				// Only touch WhatsApp if it actually changed. The previous version
				// fired set_user_whatsapp on every save, appending a "WhatsApp
				// updated" audit entry for a change nobody made.
				const wa_changed =
					wa_number !== (user.whatsapp_number || "") ||
					wa_opt !== (user.whatsapp_opt_in ? 1 : 0);

				const profile_changed =
					(values.first_name || "") !== (user.first_name || "") ||
					(values.last_name || "") !== (user.last_name || "") ||
					(values.mobile_no || "") !== (user.mobile_no || "");

				d.disable_primary_action();

				const M = "trustbit_ethanol.ts_gate_entry.ts_user_management.";
				let chain = frappe.xcall(M + "update_user_roles", {
					email: email,
					roles: selected_roles,
				});

				if (profile_changed) {
					chain = chain.then(() => frappe.xcall(M + "update_user_profile", {
						email: email,
						first_name: values.first_name,
						last_name: values.last_name,
						mobile_no: values.mobile_no,
					}));
				}

				if (wa_changed) {
					chain = chain.then(() => frappe.xcall(M + "set_user_whatsapp", {
						email: email,
						whatsapp_number: wa_number,
						whatsapp_opt_in: wa_opt,
					}));
				}

				chain.then(() => {
					d.hide();
					frappe.show_alert({ message: __("Updated {0}", [email]), indicator: "green" }, 3);
					_um_force_reload(page);
				}).catch((err) => {
					// Steps commit independently, so a later failure leaves the
					// earlier ones applied. Say so rather than implying nothing saved.
					d.enable_primary_action();
					frappe.msgprint({
						title: __("Not everything was saved"),
						indicator: "orange",
						message: __("Some changes may have been applied before the error. Reopen this user to see the current state.") +
							"<br><br>" + frappe.utils.escape_html(String((err && (err.message || err)) || "")),
					});
					_um_force_reload(page);
				});
			},
		});

		let info_html = `
			<div class="um-info">
				<div><b>${__("Email")}:</b> ${frappe.utils.escape_html(email)}</div>
				<div><b>${__("Status")}:</b> ${user.enabled ? __("Active") : __("Disabled")}</div>
				<div><b>${__("Last Active")}:</b> ${_um_when(user.last_active)}</div>
			</div>`;
		if (user.has_system_roles) {
			info_html += `<div class="um-note">${__("This user also holds system-level roles, which are not editable here. Only operational roles are shown below.")}</div>`;
		}
		d.fields_dict.info_html.$wrapper.html(info_html);

		_um_render_roles(d.fields_dict.roles_html.$wrapper, page._um_allowed_roles, current_roles);

		const $audit = d.fields_dict.audit_html.$wrapper;
		$audit.html(`<div class="um-audit__empty">${__("Loading...")}</div>`);
		frappe.xcall(
			"trustbit_ethanol.ts_gate_entry.ts_user_management.get_audit_log",
			{ email: email, limit: 20 }
		).then((rows) => {
			if (!rows || !rows.length) {
				$audit.html(`<div class="um-audit__empty">${__("No recorded changes.")}</div>`);
				return;
			}
			// Show who did it, not just what — an audit line without an actor is
			// not much of an audit line.
			const body = rows.map((r) => `
				<div class="um-audit__row">
					${frappe.utils.escape_html(String(r.content || "").replace("[User Management] ", ""))}
					<div class="um-audit__when">${_um_when(r.creation)} · ${frappe.utils.escape_html(r.owner || "")}</div>
				</div>`).join("");
			$audit.html(`<div class="um-audit">${body}</div>`);
		}).catch(() => {
			$audit.html(`<div class="um-audit__empty">${__("Could not load activity.")}</div>`);
		});

		d.show();
	}).catch((err) => {
		frappe.msgprint({
			title: __("Could not open user"),
			indicator: "red",
			message: frappe.utils.escape_html(String((err && (err.message || err)) || "")),
		});
	});
}

// ── TOGGLE USER ─────────────────────────────────────────────────────

function _um_toggle_user(page, email, enable) {
	const action = enable ? __("enable") : __("disable");

	frappe.confirm(
		__("Are you sure you want to {0} user {1}?", [action, email]),
		() => {
			frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.toggle_user", {
				email: email,
				enabled: enable,
			}).then((result) => {
				if (result.warnings && result.warnings.length > 0) {
					frappe.msgprint({
						title: __("Done — with warnings"),
						indicator: "orange",
						message:
							`<p>${frappe.utils.escape_html(email)} ${enable ? __("was enabled.") : __("was disabled.")}</p>` +
							"<ul>" + result.warnings.map((w) => "<li>" + frappe.utils.escape_html(w) + "</li>").join("") + "</ul>",
					});
				} else {
					frappe.show_alert({
						message: enable ? __("User {0} enabled", [email]) : __("User {0} disabled", [email]),
						indicator: enable ? "green" : "orange",
					}, 3);
				}
				_um_force_reload(page);
			}).catch((err) => {
				// Previously there was no catch at all here: a server-side refusal
				// (now including the privileged-role guard) produced total silence.
				frappe.msgprint({
					title: enable ? __("Could not enable user") : __("Could not disable user"),
					indicator: "red",
					message: frappe.utils.escape_html(String((err && (err.message || err)) || "")),
				});
			});
		}
	);
}

// ── RESET PASSWORD ──────────────────────────────────────────────────

function _um_reset_password(page, email) {
	frappe.confirm(
		__("Reset password for {0}?", [email]),
		() => {
			frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.reset_password", {
				email: email,
			}).then((result) => {
				if (result.reset_link) {
					// Server always returns the link now; the email (when an
					// outgoing account exists) is best-effort delivery of the
					// SAME link — tell the IT Head which of the two happened.
					let tail = "";
					if (result.email_attempted) {
						tail = result.email_sent
							? `<p class="text-muted">${__("The same link was also emailed to the user.")}</p>`
							: `<p class="um-warn">${__("Emailing the link failed (outbound email is not working) — share this link with the user directly.")}</p>`;
					}
					_um_show_link_dialog({
						title: __("Password Reset"),
						lead: `<p>${__("Send this one-time link to")} <b>${frappe.utils.escape_html(email)}</b>. ${__("Opening it takes them straight to a page where they set their own password — there is no temporary password to explain.")}</p>`,
						link: result.reset_link,
						expires_in: result.expires_in,
						tail: tail,
					});
				} else {
					frappe.msgprint({
						title: __("Reset Link Queued"),
						indicator: "green",
						message: __("A password reset link has been <b>queued</b> for {0}. If it does not arrive, check the Email Queue.", [frappe.utils.escape_html(email)]),
					});
				}
			}).catch((err) => {
				frappe.msgprint({
					title: __("Could not reset password"),
					indicator: "red",
					message: frappe.utils.escape_html(String((err && (err.message || err)) || "")),
				});
			});
		}
	);
}

// ── CHANGE OWN PASSWORD ─────────────────────────────────────────────

function _um_change_own_password() {
	const d = new frappe.ui.Dialog({
		title: __("Change My Password"),
		fields: [
			{ fieldname: "current_password", label: __("Current Password"), fieldtype: "Password", reqd: 1 },
			{ fieldname: "new_password", label: __("New Password"), fieldtype: "Password", reqd: 1, description: __("Minimum 8 characters") },
			{ fieldname: "confirm_password", label: __("Confirm New Password"), fieldtype: "Password", reqd: 1 },
		],
		primary_action_label: __("Change Password"),
		primary_action(values) {
			if (values.new_password !== values.confirm_password) {
				frappe.msgprint(__("New password and confirmation do not match."));
				return;
			}
			if (values.new_password.length < 8) {
				frappe.msgprint(__("New password must be at least 8 characters."));
				return;
			}
			if (values.new_password === values.current_password) {
				frappe.msgprint(__("New password must be different from current password."));
				return;
			}

			d.disable_primary_action();

			frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.reset_own_password", {
				current_password: values.current_password,
				new_password: values.new_password,
			}).then(() => {
				d.hide();
				frappe.show_alert({ message: __("Password changed successfully."), indicator: "green" }, 5);
			}).catch(() => {
				d.enable_primary_action();
			});
		},
	});
	d.show();
}

// ── ONE-TIME RESET LINK ─────────────────────────────────────────────

function _um_show_link_dialog(opts) {
	// The link is shown ONCE and is single-use, so make copying it the easy
	// path — an IT Head hand-retyping a 60-character key is how this gets
	// abandoned in favour of sharing passwords again.
	const safe_link = frappe.utils.escape_html(opts.link);
	const expiry = opts.expires_in
		? __("This link works once and expires in {0}.", [frappe.utils.escape_html(opts.expires_in)])
		: __("This link works once.");

	const d = new frappe.ui.Dialog({
		title: opts.title,
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Copy Link"),
		primary_action() {
			frappe.utils.copy_to_clipboard(opts.link);
			// copy_to_clipboard shows its own alert; just confirm the handoff step
			d.set_primary_action(__("Copied"), () => d.hide());
		},
	});

	d.fields_dict.body.$wrapper.html(`
		${opts.lead || ""}
		<div class="um-secret">
			<b>${__("Send this link to the user:")}</b>
			<div class="um-link">${safe_link}</div>
		</div>
		<p class="um-warn">${expiry} ${__("If it expires before they use it, just reset again for a fresh one.")}</p>
		${opts.tail || ""}
	`);

	d.show();
}

// ── HELPERS ─────────────────────────────────────────────────────────

function _um_force_reload(page) {
	page._um_loaded_at = 0;
	_um_load(page);
}
