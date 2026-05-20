// TS Cascade Delete Log form-level UI (v2.11.0)
// Append-only audit log — most field edit suppressed; key actions exposed as buttons.

frappe.ui.form.on("TS Cascade Delete Log", {
	refresh(frm) {
		_hide_standard_buttons_on_submitted(frm);
		_render_status_indicator(frm);
		_add_verify_chain_button(frm);
		_render_revert_countdown(frm);
		_pretty_print_json_fields(frm);
		_add_integrity_scan_button(frm);
	},
});

function _hide_standard_buttons_on_submitted(frm) {
	if (frm.doc.docstatus === 1) {
		// Frappe shows Submit/Save buttons; suppress Save since fields are read-only.
		try { frm.disable_save(); } catch(e) {}
	}
}

function _render_status_indicator(frm) {
	const status = frm.doc.approval_status || "";
	const colors = {
		"Pending CEO Approval": "orange",
		"Approved": "blue",
		"Rejected": "gray",
		"Cancelled": "gray",
		"Executed": "green",
		"Reverted": "purple",
		"Failed": "red",
	};
	const color = colors[status] || "gray";
	try {
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline_alert(
			`<div><strong>Status:</strong> <span style="color:${color}">${frappe.utils.escape_html(status)}</span></div>`,
			color
		);
	} catch(e) {}
}

function _add_verify_chain_button(frm) {
	if (frm.is_new()) return;
	if (frm.doc.docstatus !== 1) return;
	frm.add_custom_button(__("🔗 Verify Hash Chain"), () => {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.cascade_delete_api.verify_chain_integrity",
			callback(r) {
				if (!r.message) return;
				const m = r.message;
				const ok = m.clean;
				const title = ok ? "✓ Chain Intact" : "✗ Chain BROKEN";
				const color = ok ? "green" : "red";
				let body = `<p>Total submitted rows scanned: <strong>${m.total_rows}</strong></p>`;
				if (!ok) {
					body += `<p style="color:red;">Broken link at: <code>${m.broken_links.join(", ")}</code></p>`;
				} else {
					body += `<p style="color:green;">All ${m.total_rows} rows hash-chain to predecessor cleanly.</p>`;
				}
				frappe.msgprint({ title, message: body, indicator: color });
			},
		});
	});
}

function _add_integrity_scan_button(frm) {
	if (frm.is_new()) return;
	if (frm.doc.docstatus !== 1) return;
	if (frm.doc.approval_status !== "Executed" && frm.doc.approval_status !== "Reverted") return;
	frm.add_custom_button(__("🔍 Run Integrity Scan"), () => {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.cascade_delete_api.run_integrity_scan",
			args: { log_name: frm.doc.name },
			callback(r) {
				if (!r.message) return;
				const m = r.message;
				const ok = m.clean;
				const title = ok ? "✓ No Orphans" : `✗ ${m.orphan_count} Orphan(s) Found`;
				const color = ok ? "green" : "red";
				frappe.msgprint({
					title,
					message: `<pre style="font-size:11px;">${frappe.utils.escape_html(JSON.stringify(m.orphans || {}, null, 2))}</pre>`,
					indicator: color,
				});
				frm.reload_doc();
			},
		});
	});
}

function _render_revert_countdown(frm) {
	if (frm.doc.docstatus !== 1) return;
	if (frm.doc.approval_status !== "Executed") return;
	if (!frm.doc.revert_window_expires_at) return;

	const expires = frappe.datetime.str_to_obj(frm.doc.revert_window_expires_at);
	const now = new Date();
	const ms_remaining = expires - now;

	if (ms_remaining <= 0) {
		// Window expired; show nothing or "Frozen" indicator
		return;
	}

	const min = Math.floor(ms_remaining / 60000);
	const sec = Math.floor((ms_remaining % 60000) / 1000);
	const countdown_text = `${min}:${String(sec).padStart(2, "0")}`;

	frm.add_custom_button(
		__(`⏪ Revert (${countdown_text} remaining)`),
		() => _confirm_revert(frm),
	);

	// Tick the countdown every second by re-running refresh once.
	if (!frm._cd_countdown_started) {
		frm._cd_countdown_started = true;
		setTimeout(() => {
			frm._cd_countdown_started = false;
			frm.refresh();
		}, 1000);
	}
}

function _confirm_revert(frm) {
	frappe.confirm(
		__(
			"⚠️ This will RESTORE the cascade-deleted chain for token <strong>{0}</strong>. " +
			"Backup SHA-256 will be verified before restore. Proceed?",
			[frappe.utils.escape_html(frm.doc.target_token)]
		),
		() => {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.cascade_delete_api.revert_cascade",
				args: { log_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Restoring cascade chain…"),
				callback(r) {
					if (r.message && r.message.success) {
						frappe.show_alert({ message: __("Revert complete."), indicator: "green" });
						frm.reload_doc();
					} else {
						frappe.msgprint({
							title: __("Revert Failed"),
							message: (r.message && r.message.error) || __("Unknown error — see Error Log."),
							indicator: "red",
						});
					}
				},
			});
		}
	);
}

function _pretty_print_json_fields(frm) {
	// Render forensic_block_json + target_chain_json + execution_result_json as
	// indented JSON for readability. Editable=0 on submit so we render to the
	// description / aside, not edit the field itself.
	const json_fields = [
		"forensic_block_json",
		"target_chain_json",
		"execution_result_json",
		"revert_result_json",
		"integrity_scan_orphans_json",
	];
	for (const fn of json_fields) {
		const val = frm.doc[fn];
		if (!val) continue;
		try {
			const parsed = JSON.parse(val);
			const pretty = JSON.stringify(parsed, null, 2);
			frm.set_df_property(fn, "description",
				`<pre style="font-size:10px;background:#f5f5f5;padding:4px;max-height:300px;overflow:auto;">${frappe.utils.escape_html(pretty)}</pre>`);
		} catch(e) {
			// invalid JSON — leave as-is
		}
	}
}
