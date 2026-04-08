frappe.pages["user-management"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "User Management",
		single_column: true,
	});

	page.main.html('<div id="um-container" style="padding: 15px;"></div>');

	// Store refs
	page._um_users = [];
	page._um_allowed_roles = [];
	page._um_filter = "all"; // all, active, disabled
	page._um_search = "";

	// Add filter controls
	page.add_field({
		fieldname: "status_filter",
		label: __("Status"),
		fieldtype: "Select",
		options: "All\nActive\nDisabled",
		default: "All",
		change() { page._um_filter = this.get_value().toLowerCase(); _um_render_table(page); },
	});

	page.add_field({
		fieldname: "search",
		label: __("Search"),
		fieldtype: "Data",
		change() { page._um_search = (this.get_value() || "").toLowerCase(); _um_render_table(page); },
	});

	// Primary action: New User
	page.set_primary_action(__("+ New User"), () => _um_show_create_dialog(page), "es-line-add");

	// Secondary action: Change My Password
	page.add_inner_button(__("Change My Password"), () => _um_change_own_password());

	// Load data
	_um_load(page);
};

frappe.pages["user-management"].refresh = function (wrapper) {
	_um_load(wrapper.page);
};

// ── DATA LOADING ────────────────────────────────────────────────────

function _um_load(page) {
	const $c = $("#um-container");
	$c.html('<div style="text-align:center;padding:40px;color:#888;">Loading users...</div>');

	// Load allowed roles and users in parallel
	Promise.all([
		frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.get_allowed_roles"),
		frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.get_users"),
	]).then(([roles, users]) => {
		page._um_allowed_roles = roles || [];
		page._um_users = users || [];
		_um_render_table(page);
	}).catch((err) => {
		$c.html('<div style="text-align:center;padding:40px;color:#e53e3e;">' +
			'<b>Access Denied</b><br>Only IT Head can access this page.</div>');
	});
}

// ── TABLE RENDERING ─────────────────────────────────────────────────

function _um_render_table(page) {
	const $c = $("#um-container");
	const users = _um_filtered_users(page);

	const total = page._um_users.length;
	const active = page._um_users.filter(u => u.enabled).length;
	const disabled = total - active;

	let html = `
		<div style="margin-bottom:5px;text-align:right;font-size:10px;color:#94a3b8;">${page._um_allowed_roles.length} roles available</div>
		<div style="margin-bottom:15px;">
			<div style="display:flex;gap:15px;flex-wrap:wrap;">
				<div class="um-stat-card" style="background:#dbeafe;border-left:4px solid #3b82f6;padding:10px 15px;border-radius:6px;min-width:120px;">
					<div style="font-size:22px;font-weight:700;color:#1e40af;">${total}</div>
					<div style="font-size:12px;color:#64748b;">Total Users</div>
				</div>
				<div class="um-stat-card" style="background:#dcfce7;border-left:4px solid #10b981;padding:10px 15px;border-radius:6px;min-width:120px;">
					<div style="font-size:22px;font-weight:700;color:#059669;">${active}</div>
					<div style="font-size:12px;color:#64748b;">Active</div>
				</div>
				<div class="um-stat-card" style="background:#fee2e2;border-left:4px solid #ef4444;padding:10px 15px;border-radius:6px;min-width:120px;">
					<div style="font-size:22px;font-weight:700;color:#dc2626;">${disabled}</div>
					<div style="font-size:12px;color:#64748b;">Disabled</div>
				</div>
			</div>
		</div>

		<div style="background:white;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
			<table class="table table-hover" style="margin:0;">
				<thead>
					<tr style="background:#f8fafc;">
						<th style="padding:10px 15px;font-size:12px;color:#64748b;font-weight:600;">User</th>
						<th style="padding:10px 15px;font-size:12px;color:#64748b;font-weight:600;">Email</th>
						<th style="padding:10px 15px;font-size:12px;color:#64748b;font-weight:600;">Roles</th>
						<th style="padding:10px 15px;font-size:12px;color:#64748b;font-weight:600;">Status</th>
						<th style="padding:10px 15px;font-size:12px;color:#64748b;font-weight:600;text-align:right;">Actions</th>
					</tr>
				</thead>
				<tbody>`;

	if (users.length === 0) {
		html += `<tr><td colspan="5" style="text-align:center;padding:30px;color:#94a3b8;">No users found</td></tr>`;
	}

	users.forEach(u => {
		const status_badge = u.enabled
			? '<span style="background:#dcfce7;color:#059669;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;">Active</span>'
			: '<span style="background:#fee2e2;color:#dc2626;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;">Disabled</span>';

		const role_pills = (u.roles || []).slice(0, 3).map(r =>
			'<span style="background:#f1f5f9;color:#475569;padding:2px 8px;border-radius:4px;font-size:11px;margin-right:3px;">' +
			frappe.utils.escape_html(r) + '</span>'
		).join("");
		const more = (u.roles || []).length > 3
			? `<span style="color:#94a3b8;font-size:11px;">+${u.roles.length - 3} more</span>` : "";

		const protected_badge = u.is_protected
			? ' <span title="Protected — cannot edit here" style="color:#f59e0b;font-size:11px;">&#128274;</span>' : "";
		const self_badge = u.is_self
			? ' <span title="This is you" style="color:#8b5cf6;font-size:11px;">(You)</span>' : "";

		const can_edit = !u.is_protected && !u.is_self;
		const edit_btn = can_edit
			? `<button class="btn btn-xs btn-default um-edit-btn" data-email="${frappe.utils.escape_html(u.name)}" style="margin-right:5px;">Edit Roles</button>`
			: '';
		const toggle_btn = can_edit
			? (u.enabled
				? `<button class="btn btn-xs btn-danger-light um-toggle-btn" data-email="${frappe.utils.escape_html(u.name)}" data-enable="0" style="border-color:#fca5a5;color:#dc2626;">Disable</button>`
				: `<button class="btn btn-xs btn-success-light um-toggle-btn" data-email="${frappe.utils.escape_html(u.name)}" data-enable="1" style="border-color:#86efac;color:#059669;">Enable</button>`)
			: '';
		const reset_btn = can_edit
			? `<button class="btn btn-xs btn-default um-reset-btn" data-email="${frappe.utils.escape_html(u.name)}" style="margin-right:5px;" title="Reset Password">&#128273;</button>`
			: '';

		html += `
			<tr>
				<td style="padding:10px 15px;vertical-align:middle;">
					<b>${frappe.utils.escape_html(u.full_name || u.first_name || "")}</b>${protected_badge}${self_badge}
				</td>
				<td style="padding:10px 15px;vertical-align:middle;color:#64748b;font-size:13px;">
					${frappe.utils.escape_html(u.name)}
				</td>
				<td style="padding:10px 15px;vertical-align:middle;">
					${role_pills}${more}
				</td>
				<td style="padding:10px 15px;vertical-align:middle;">${status_badge}</td>
				<td style="padding:10px 15px;vertical-align:middle;text-align:right;">
					${reset_btn}${edit_btn}${toggle_btn}
				</td>
			</tr>`;
	});

	html += `</tbody></table></div>`;
	$c.html(html);

	// Bind actions
	$c.find(".um-edit-btn").on("click", function () {
		_um_show_edit_dialog(page, $(this).data("email"));
	});
	$c.find(".um-toggle-btn").on("click", function () {
		_um_toggle_user(page, $(this).data("email"), parseInt($(this).data("enable")));
	});
	$c.find(".um-reset-btn").on("click", function () {
		_um_reset_password(page, $(this).data("email"));
	});
}

function _um_filtered_users(page) {
	return page._um_users.filter(u => {
		// Status filter
		if (page._um_filter === "active" && !u.enabled) return false;
		if (page._um_filter === "disabled" && u.enabled) return false;
		// Search filter
		if (page._um_search) {
			const s = page._um_search;
			const match = (u.full_name || "").toLowerCase().includes(s)
				|| (u.name || "").toLowerCase().includes(s)
				|| (u.roles || []).some(r => r.toLowerCase().includes(s));
			if (!match) return false;
		}
		return true;
	});
}

// ── CREATE USER DIALOG ──────────────────────────────────────────────

function _um_show_create_dialog(page) {
	const role_options = page._um_allowed_roles.map(r => ({
		label: r, value: r, checked: false
	}));

	const d = new frappe.ui.Dialog({
		title: __("Create New User"),
		size: "large",
		fields: [
			{
				fieldtype: "Section Break",
				label: __("User Details"),
			},
			{
				fieldname: "first_name",
				label: __("First Name"),
				fieldtype: "Data",
				reqd: 1,
			},
			{
				fieldname: "last_name",
				label: __("Last Name"),
				fieldtype: "Data",
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldname: "email",
				label: __("Email"),
				fieldtype: "Data",
				options: "Email",
				reqd: 1,
			},
			{
				fieldname: "mobile_no",
				label: __("Mobile"),
				fieldtype: "Data",
				options: "Phone",
			},
			{
				fieldtype: "Section Break",
				label: __("Assign Roles"),
				description: __("Select the roles this user should have. Only operational roles are available."),
			},
			{
				fieldname: "roles_html",
				fieldtype: "HTML",
			},
			{
				fieldtype: "Section Break",
				label: __("Password"),
				description: __("If email server is configured, a welcome email with login link will be sent. Otherwise, a temporary password will be generated."),
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
			// Collect checked roles
			const selected_roles = [];
			d.$wrapper.find(".um-role-check:checked").each(function () {
				selected_roles.push($(this).val());
			});

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
				roles: selected_roles,
				send_welcome_email: values.send_welcome_email ? 1 : 0,
			}).then((result) => {
				d.hide();

				if (result.temp_password) {
					// Show temporary password dialog
					frappe.msgprint({
						title: __("User Created Successfully"),
						indicator: "green",
						message: `
							<p><b>${frappe.utils.escape_html(result.full_name)}</b> (${frappe.utils.escape_html(result.user)}) has been created.</p>
							<div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:12px;margin:10px 0;">
								<b style="color:#92400e;">Temporary Password (share securely):</b><br>
								<code style="font-size:16px;letter-spacing:1px;user-select:all;">${frappe.utils.escape_html(result.temp_password)}</code>
							</div>
							<p style="color:#dc2626;font-size:12px;"><b>Important:</b> The user must change this password on first login.</p>
							<p><b>Roles:</b> ${result.roles.map(r => frappe.utils.escape_html(r)).join(", ")}</p>
						`,
					});
				} else {
					frappe.show_alert({
						message: __("User {0} created. Welcome email sent.", [frappe.utils.escape_html(result.user)]),
						indicator: "green",
					}, 5);
				}

				_um_load(page);
			}).catch(() => {
				d.enable_primary_action();
			});
		},
	});

	// Render role checkboxes in a grid
	const $roles_html = d.fields_dict.roles_html.$wrapper;
	let roles_grid = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;">';
	page._um_allowed_roles.forEach(role => {
		roles_grid += `
			<label style="display:flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid #e2e8f0;border-radius:6px;cursor:pointer;font-size:13px;">
				<input type="checkbox" class="um-role-check" value="${frappe.utils.escape_html(role)}">
				${frappe.utils.escape_html(role)}
			</label>`;
	});
	roles_grid += '</div>';
	$roles_html.html(roles_grid);

	d.show();
}

// ── EDIT ROLES DIALOG ───────────────────────────────────────────────

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
			title: __("Edit Roles — {0}", [frappe.utils.escape_html(user.full_name || email)]),
			size: "large",
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "info_html",
				},
				{
					fieldtype: "Section Break",
					label: __("Roles"),
				},
				{
					fieldname: "roles_html",
					fieldtype: "HTML",
				},
			],
			primary_action_label: __("Save Roles"),
			primary_action() {
				const selected_roles = [];
				d.$wrapper.find(".um-role-check:checked").each(function () {
					selected_roles.push($(this).val());
				});

				if (selected_roles.length === 0) {
					frappe.msgprint(__("Please select at least one role."));
					return;
				}

				d.disable_primary_action();

				frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.update_user_roles", {
					email: email,
					roles: selected_roles,
				}).then((result) => {
					d.hide();
					frappe.show_alert({
						message: __("Roles updated for {0}", [frappe.utils.escape_html(email)]),
						indicator: "green",
					}, 3);
					_um_load(page);
				}).catch(() => {
					d.enable_primary_action();
				});
			},
		});

		// Info section
		const $info = d.fields_dict.info_html.$wrapper;
		let info_html = `
			<div style="display:flex;gap:20px;margin-bottom:10px;">
				<div><b>Email:</b> ${frappe.utils.escape_html(email)}</div>
				<div><b>Status:</b> ${user.enabled ? '<span style="color:#059669;">Active</span>' : '<span style="color:#dc2626;">Disabled</span>'}</div>
			</div>`;
		if (user.has_system_roles) {
			info_html += `<div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:8px 12px;margin-bottom:10px;font-size:12px;color:#92400e;">
				This user also has system-level roles (not editable here). Only operational roles are shown below.
			</div>`;
		}
		$info.html(info_html);

		// Render role checkboxes
		const $roles_html = d.fields_dict.roles_html.$wrapper;
		let roles_grid = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;">';
		page._um_allowed_roles.forEach(role => {
			const checked = current_roles.has(role) ? "checked" : "";
			roles_grid += `
				<label style="display:flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid ${checked ? '#3b82f6' : '#e2e8f0'};border-radius:6px;cursor:pointer;font-size:13px;${checked ? 'background:#eff6ff;' : ''}">
					<input type="checkbox" class="um-role-check" value="${frappe.utils.escape_html(role)}" ${checked}>
					${frappe.utils.escape_html(role)}
				</label>`;
		});
		roles_grid += '</div>';
		$roles_html.html(roles_grid);

		// Update border color on check/uncheck
		d.$wrapper.on("change", ".um-role-check", function () {
			const $label = $(this).closest("label");
			if (this.checked) {
				$label.css({ "border-color": "#3b82f6", "background": "#eff6ff" });
			} else {
				$label.css({ "border-color": "#e2e8f0", "background": "" });
			}
		});

		d.show();
	});
}

// ── TOGGLE USER ─────────────────────────────────────────────────────

function _um_toggle_user(page, email, enable) {
	const action = enable ? "enable" : "disable";

	frappe.confirm(
		__("Are you sure you want to {0} user <b>{1}</b>?", [action, frappe.utils.escape_html(email)]),
		() => {
			frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.toggle_user", {
				email: email,
				enabled: enable,
			}).then((result) => {
				// Show warnings if any
				if (result.warnings && result.warnings.length > 0) {
					frappe.msgprint({
						title: __("User {0}d — Warnings", [action]),
						indicator: "orange",
						message: `
							<p>User has been ${action}d. Please note:</p>
							<ul>${result.warnings.map(w => "<li>" + frappe.utils.escape_html(w) + "</li>").join("")}</ul>
							<p style="font-size:12px;color:#64748b;">These workflows may need attention from another user.</p>
						`,
					});
				} else {
					frappe.show_alert({
						message: __("User {0} {1}d", [frappe.utils.escape_html(email), action]),
						indicator: enable ? "green" : "orange",
					}, 3);
				}
				_um_load(page);
			});
		}
	);
}

// ── RESET PASSWORD ──────────────────────────────────────────────────

function _um_reset_password(page, email) {
	frappe.confirm(
		__("Reset password for <b>{0}</b>?", [frappe.utils.escape_html(email)]),
		() => {
			frappe.xcall("trustbit_ethanol.ts_gate_entry.ts_user_management.reset_password", {
				email: email,
			}).then((result) => {
				if (result.method === "generated") {
					frappe.msgprint({
						title: __("Password Reset"),
						indicator: "green",
						message: `
							<p>New temporary password for <b>${frappe.utils.escape_html(email)}</b>:</p>
							<div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:12px;margin:10px 0;">
								<code style="font-size:16px;letter-spacing:1px;user-select:all;">${frappe.utils.escape_html(result.temp_password)}</code>
							</div>
							<p style="color:#dc2626;font-size:12px;"><b>Share this securely.</b> User should change it on first login.</p>
						`,
					});
				} else {
					frappe.show_alert({
						message: __("Password reset email sent to {0}", [frappe.utils.escape_html(email)]),
						indicator: "green",
					}, 5);
				}
			});
		}
	);
}

// ── CHANGE OWN PASSWORD ─────────────────────────────────────────────

function _um_change_own_password() {
	const d = new frappe.ui.Dialog({
		title: __("Change My Password"),
		fields: [
			{
				fieldname: "current_password",
				label: __("Current Password"),
				fieldtype: "Password",
				reqd: 1,
			},
			{
				fieldname: "new_password",
				label: __("New Password"),
				fieldtype: "Password",
				reqd: 1,
				description: __("Minimum 8 characters"),
			},
			{
				fieldname: "confirm_password",
				label: __("Confirm New Password"),
				fieldtype: "Password",
				reqd: 1,
			},
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
				frappe.show_alert({
					message: __("Password changed successfully."),
					indicator: "green",
				}, 5);
			}).catch(() => {
				d.enable_primary_action();
			});
		},
	});
	d.show();
}
