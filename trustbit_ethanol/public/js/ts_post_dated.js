/*
 * Post-Dated Entry — shared helper function
 * Loaded via app_include_js — provides _ts_pd_check() called from each DocType's refresh
 */

// Expose globally (esbuild wraps app_include_js in module scope)
window._ts_pd_check = _ts_pd_check;

function _ts_pd_check(frm, doctype, date_fields) {
	if (frm.doc.docstatus > 0) return;

	let token_number = frm.doc.token_number || frm.doc.name;
	if (doctype === "TS Token") token_number = frm.doc.name;

	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_post_dated.check_post_dated_access",
		args: { doctype: doctype, token_number: token_number },
		async: true,
		callback(r) {
			if (!r.message) return;

			// Remove old banner
			frm.dashboard.wrapper.find(".pd-banner").remove();

			if (r.message.enabled) {
				// Show banner
				const valid_until = frappe.datetime.str_to_user(r.message.valid_until);
				const from_date = frappe.datetime.str_to_user(r.message.from_date);
				const to_date = frappe.datetime.str_to_user(r.message.to_date);
				const now = frappe.datetime.now_datetime();
				const diff_min = frappe.datetime.get_minute_diff(r.message.valid_until, now);
				const is_expiring = diff_min <= 30 && diff_min > 0;

				let html;
				if (is_expiring) {
					html = `<div class="pd-banner" style="padding:10px 16px;background:#fff3e0;border:1px solid #ffcc80;border-radius:6px;margin:8px 0;font-size:13px;color:#e65100;">
						<strong>&#9888; Post-Dated Entry Expiring Soon!</strong> — Expires at <strong>${valid_until}</strong> (${Math.round(diff_min)} min).
					</div>`;
				} else {
					html = `<div class="pd-banner" style="padding:10px 16px;background:#e3f2fd;border:1px solid #bbdefb;border-radius:6px;margin:8px 0;font-size:13px;color:#1565c0;">
						<strong>&#128197; Post-Dated Entry Enabled</strong> — Dates <strong>${from_date}</strong> to <strong>${to_date}</strong> allowed. Active until <strong>${valid_until}</strong>.
					</div>`;
				}
				frm.dashboard.wrapper.prepend(html);

				// Unlock date fields
				(date_fields || []).forEach(fn => {
					frm.set_df_property(fn, "read_only", 0);
					const el = frm.fields_dict[fn]?.$wrapper;
					if (el) el.find("input").css({"border-color": "#2490ef", "background": "#f0f7ff"});
				});
			}
		}
	});
}
