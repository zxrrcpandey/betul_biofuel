/*
 * Post-Dated Entry — shared JS for all flow DocTypes
 * Loaded via app_include_js on: TS Token, TS Gate Entry, TS Weighbridge Log,
 * TS Quality Inspection, TS Deduction Sheet, TS Unloading Entry
 */

const PD_FLOW_DOCTYPES = [
	"TS Token", "TS Gate Entry", "TS Weighbridge Log",
	"TS Quality Inspection", "TS Deduction Sheet", "TS Unloading Entry"
];

// Date fields per DocType that should be unlocked
const PD_DATE_FIELDS = {
	"TS Token": ["entry_date", "entry_time"],
	"TS Gate Entry": ["entry_date", "entry_time"],
	"TS Weighbridge Log": [],
	"TS Quality Inspection": ["inspection_date"],
	"TS Deduction Sheet": [],
	"TS Unloading Entry": [],
};

$(document).on("page-change", function () {
	// Only run on flow DocType forms
	const route = frappe.get_route();
	if (route[0] !== "Form") return;
	const doctype = route[1];
	if (!PD_FLOW_DOCTYPES.includes(doctype)) return;

	// Hook into the form refresh
	const existing = (frappe.ui.form.handlers[doctype] || {}).refresh || [];
	if (existing._pd_hooked) return;

	frappe.ui.form.on(doctype, {
		refresh: function (frm) {
			_check_post_dated(frm);
		}
	});

	// Mark as hooked to prevent double-binding
	const handlers = frappe.ui.form.handlers[doctype] || {};
	if (handlers.refresh) handlers.refresh._pd_hooked = true;
});

function _check_post_dated(frm) {
	if (!PD_FLOW_DOCTYPES.includes(frm.doc.doctype)) return;

	// Only check for new or draft documents
	if (frm.doc.docstatus > 0) return;

	// Get token_number for transaction-wise check
	let token_number = frm.doc.token_number || frm.doc.name;
	if (frm.doc.doctype === "TS Token") {
		token_number = frm.doc.name;
	}

	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_post_dated.check_post_dated_access",
		args: {
			doctype: frm.doc.doctype,
			token_number: token_number,
		},
		async: true,
		callback: function (r) {
			if (!r.message) return;
			const access = r.message;

			// Remove any existing PD banner
			frm.dashboard.wrapper.find(".pd-banner").remove();

			if (access.enabled) {
				_show_pd_banner(frm, access);
				_unlock_date_fields(frm);
			}
		}
	});
}

function _show_pd_banner(frm, access) {
	const valid_until = frappe.datetime.str_to_user(access.valid_until);
	const from_date = frappe.datetime.str_to_user(access.from_date);
	const to_date = frappe.datetime.str_to_user(access.to_date);

	// Check if expiring soon (within 30 min)
	const now = frappe.datetime.now_datetime();
	const until = access.valid_until;
	const diff_minutes = frappe.datetime.get_minute_diff(until, now);
	const is_expiring = diff_minutes <= 30 && diff_minutes > 0;

	let html;
	if (is_expiring) {
		html = `<div class="pd-banner" style="padding:10px 16px;background:#fff3e0;border:1px solid #ffcc80;border-radius:6px;margin:8px 0;font-size:13px;color:#e65100;">
			<strong>&#9888; Post-Dated Entry Expiring Soon!</strong> — Expires at <strong>${valid_until}</strong> (in ${Math.round(diff_minutes)} min). Complete all backdated entries now.
		</div>`;
	} else {
		html = `<div class="pd-banner" style="padding:10px 16px;background:#e3f2fd;border:1px solid #bbdefb;border-radius:6px;margin:8px 0;font-size:13px;color:#1565c0;">
			<strong>&#128197; Post-Dated Entry Enabled</strong> — Dates <strong>${from_date}</strong> to <strong>${to_date}</strong> allowed. Active until <strong>${valid_until}</strong>.
		</div>`;
	}

	frm.dashboard.wrapper.prepend(html);
}

function _unlock_date_fields(frm) {
	const fields = PD_DATE_FIELDS[frm.doc.doctype] || [];
	fields.forEach(fn => {
		frm.set_df_property(fn, "read_only", 0);
		// Highlight with blue border
		const el = frm.fields_dict[fn]?.$wrapper;
		if (el) {
			el.find("input").css({"border-color": "#2490ef", "background": "#f0f7ff"});
		}
	});
}
