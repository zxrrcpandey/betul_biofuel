// v2.47.0 — RGP Phase A2: "Create RGP" surface on approved Service Request MRs.
//
// Deliberately a SEPARATE doctype_js file (second entry in hooks.py, loaded
// AFTER mr_approval.js) so the MR_FULL-locked mr_approval.js is never touched.
// Server-side gates are authoritative (ts_rgp.py + the ts_sr_to_po WO lock);
// everything here is display-only convenience.

frappe.ui.form.on("Material Request", {
	refresh(frm) {
		if (frm.is_new()) return; // L166 — nothing on unsaved forms
		if (frm.doc.material_request_type !== "Service Request") return;
		if (frm.doc.docstatus !== 1) return;

		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_rgp.rgp_enabled",
			callback(r) {
				if (!cint(r.message)) return;
				ts_rgp_render_mr_surface(frm);
			},
		});
	},
});

function ts_rgp_render_mr_surface(frm) {
	frappe.call({
		method: "trustbit_ethanol.ts_gate_entry.ts_rgp.get_open_rgps_for_mr",
		args: { mr: frm.doc.name },
		callback(r) {
			const open = r.message || [];
			ts_rgp_render_open_banner(frm, open);

			const is_stores = frappe.user.has_role("Stores User")
				|| frappe.user.has_role("Stores Manager")
				|| frappe.user.has_role("IT Head")
				|| frappe.user.has_role("System Manager");
			if (!is_stores) return;
			if ((frm.doc.ts_mr_status || "") !== "Approved") return;

			frm.add_custom_button(__("Create RGP"), () => {
				frappe.confirm(
					__("Create a Returnable Gate Pass for {0}? / {0} के लिए रिटर्नेबल गेट पास बनाएँ?",
						[frm.doc.name]),
					() => {
						frappe.call({
							method: "trustbit_ethanol.ts_gate_entry.ts_rgp.create_rgp_from_mr",
							args: { mr: frm.doc.name },
							freeze: true,
							freeze_message: __("Creating RGP…"),
							callback(res) {
								if (res.message) {
									frappe.set_route("Form", "TS Returnable Gate Pass", res.message);
								}
							},
						});
					}
				);
			}, __("RGP"));
		},
	});
}

function ts_rgp_render_open_banner(frm, open) {
	const KEY = "ts-rgp-open-banner";
	frm.$wrapper.find(`.${KEY}`).remove();
	if (!open.length) return;

	// L425 lesson: single insertion point, .first() — never every dashboard section
	const rows = open
		.map((o) => `${frappe.utils.escape_html(o.name)} (${frappe.utils.escape_html(o.status)}, `
			+ `${__("balance")} ${o.total_balance || 0})`)
		.join(" · ");
	const html = `
		<div class="${KEY}" style="margin:8px 0;padding:8px 12px;border:1px solid var(--orange-300, #e8a33d);
			background:var(--orange-50, #fff8ec);border-radius:6px;font-size:12.5px;">
			<b>${__("RGP open — Work Order conversion locked")}</b>:
			${rows}<br>
			<span style="opacity:.75">${__("Verify the return (or close short) to unlock the Work Order.")} /
			${__("वर्क ऑर्डर अनलॉक करने के लिए वापसी सत्यापित करें।")}</span>
		</div>`;
	// Walkthrough fix (28 Aug 2026): insert AFTER the first dashboard section
	// on frm.page.wrapper — the EXACT anchor _show_ts_banner uses and the only
	// one proven visible on this form. Inserting BEFORE it parks the banner
	// inside the collapsed .form-dashboard container, which v15 keeps hidden.
	const anchor = $(frm.page.wrapper).find(".form-dashboard-section").first();
	if (anchor.length) {
		anchor.after(html);
	} else {
		$(frm.page.wrapper).find(".form-layout").first().prepend(html);
	}
}
