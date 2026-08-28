// v2.47.0 — TS Returnable Gate Pass form (RGP Phase A2).
// ui-designer spec 28 Aug 2026. Hindi strings inlined deliberately —
// editing the shared ts_approval_ux.js would need the L318/319 ?v bump +
// hooks.py lock cycle for translations alone.

frappe.ui.form.on("TS Returnable Gate Pass", {
	setup(frm) {
		frm.set_query("material_request", () => ({
			filters: {
				material_request_type: "Service Request",
				docstatus: 1,
				ts_mr_status: "Approved",
			},
		}));
		frm.set_query("supplier_address", () => ({
			filters: { link_doctype: "Supplier", link_name: frm.doc.supplier || "" },
		}));
	},

	refresh(frm) {
		// L296 — strip stale banner BEFORE any is_new() branch
		frm.$wrapper.find(".ts-rgp-banner").remove();

		if (frm.is_new()) {
			// L166 — defaults only on genuinely new forms
			if (!frm.doc.challan_date) {
				frm.set_value("challan_date", frappe.datetime.get_today());
			}
			if (!frm.doc.expected_return_date) {
				frm.set_value("expected_return_date",
					frappe.datetime.add_days(frappe.datetime.get_today(), 7));
			}
			return;
		}

		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_rgp.get_rgp_context",
			args: { rgp: frm.doc.name },
			callback(r) {
				if (r.message) ts_rgp_render(frm, r.message);
			},
		});
	},

	challan_date(frm) {
		if (frm.is_new() && frm.doc.challan_date) {
			frm.set_value("expected_return_date",
				frappe.datetime.add_days(frm.doc.challan_date, 7));
		}
	},
});

const TS_RGP_TERMINAL = ["Verified - Closed", "Closed Short", "Cancelled"];

function ts_rgp_render(frm, ctx) {
	ts_rgp_indicator(frm, ctx);
	ts_rgp_banner(frm, ctx);
	ts_rgp_buttons(frm, ctx);
	ts_rgp_locks(frm, ctx);
}

// ── indicator ──────────────────────────────────────────────────────────
function ts_rgp_indicator(frm, ctx) {
	const colors = {
		"Draft": "grey", "Issued": "blue", "Out of Plant": "orange",
		"At Vendor": "orange", "Partially Returned": "orange",
		"Returned": "blue", "Verified - Closed": "green",
		"Closed Short": "red", "Cancelled": "grey",
	};
	let label = ctx.status;
	let color = colors[ctx.status] || "grey";
	if (ctx.status === "Returned") label = __("Returned — Awaiting Verification");
	// Walkthrough feedback (28 Aug): once gate-IN stamps exist the material is
	// physically back, but the enum stays until Stores credits the return —
	// surface the arrival state in the label so "At Vendor" doesn't mislead.
	if (["Out of Plant", "At Vendor", "Partially Returned"].includes(ctx.status)) {
		if (frm.doc.g2_in_by) {
			label = __("{0} · Inside Plant — awaiting Stores", [ctx.status]);
			color = "blue";
		} else if (frm.doc.g1_in_by) {
			label = __("{0} · Arrived at Campus Gate", [ctx.status]);
			color = "blue";
		}
	}
	if (!TS_RGP_TERMINAL.includes(ctx.status)) {
		if (ctx.overdue_days > 0) {
			label = `${ctx.status} · ${__("Overdue")} ${ctx.overdue_days}d`;
			color = "red";
		}
		if (ctx.close_short_pending) {
			label = `${ctx.status} · ${__("Close-Short Pending CEO")}`;
			color = "red";
		}
	}
	frm.page.set_indicator(label, color);
}

// ── banner (ONE at a time, precedence order; .first() per L425) ────────
function ts_rgp_banner(frm, ctx) {
	const open = !TS_RGP_TERMINAL.includes(ctx.status) && ctx.status !== "Draft";
	const fmt = (d) => frappe.datetime.str_to_user(d);
	let text = "", bg = "", border = "";

	if (open && frm.doc.g2_in_by && ctx.balance > 0
		&& ["Out of Plant", "At Vendor", "Partially Returned"].includes(ctx.status)) {
		text = __("Material is back inside the plant (G2 inward {0}) — record the return to credit it. / सामग्री संयंत्र में वापस आ गई है — वापसी दर्ज करें।",
			[frappe.datetime.str_to_user(frm.doc.g2_in_at)]);
		bg = "#ddedeb"; border = "#0e6e68";
	} else if (open && ctx.months_out >= 10 && ctx.sec143_due_date) {
		text = __("Out for {0} months — the GST Section 143 limit falls on {1}.",
			[ctx.months_out, fmt(ctx.sec143_due_date)]);
		bg = "#fef2f2"; border = "#dc2626";
	} else if (open && ctx.overdue_days > 0) {
		text = __("Overdue by {0} day(s) — was due back on {1}. / {0} दिन अतिदेय — वापसी {1} को अपेक्षित थी।",
			[ctx.overdue_days, fmt(frm.doc.expected_return_date)]);
		bg = "#fef2f2"; border = "#dc2626";
	} else if (ctx.close_short_pending) {
		text = __("Close-short of {0} unit(s) is pending CEO approval. / {0} इकाई की कमी CEO स्वीकृति हेतु लंबित।",
			[ctx.balance]);
		bg = "#fffbeb"; border = "#d97706";
	} else if (open && ctx.due_in_days !== null && ctx.due_in_days <= 2) {
		text = __("Due back in {0} day(s), on {1}.",
			[ctx.due_in_days, fmt(frm.doc.expected_return_date)]);
		bg = "#fffbeb"; border = "#d97706";
	} else if (ctx.status === "Closed Short") {
		text = __("Closed short — {0} unit(s) written off.", [frm.doc.close_short_qty || 0]);
		bg = "#f3f4f6"; border = "#6b7280";
	}
	if (!text) return;

	const html = `<div class="ts-rgp-banner" style="margin:8px 0;padding:8px 12px;
		background:${bg};border:1px solid ${border};border-left:4px solid ${border};
		border-radius:6px;font-size:12.5px;">${frappe.utils.escape_html(text)}</div>`;
	$(frm.page.wrapper).find(".form-dashboard-section").first().after(html);
}

// ── buttons ────────────────────────────────────────────────────────────
// NOT gated on ctx.enabled (predictor D3-4): lifecycle buttons must stay
// available after a flag-off so open passes can be closed out. The server's
// can_* flags are authoritative; only CREATION is flag-gated.
function ts_rgp_buttons(frm, ctx) {
	if (ctx.can_issue) {
		frm.add_custom_button(__("Issue Gate Pass"), () => {
			frappe.confirm(
				__("Issue this returnable gate pass? The challan becomes final and the material may leave the plant.<br>क्या यह वापसी योग्य गेट पास जारी करें? चालान अंतिम हो जाएगा और सामग्री संयंत्र से बाहर जा सकेगी।"),
				() => ts_rgp_post(frm, "issue_rgp", {})
			);
		}).addClass("btn-primary");
	}

	if (ctx.can_record_return && !ctx.close_short_pending) {
		frm.add_custom_button(__("Record Return"), () => ts_rgp_return_dialog(frm))
			.addClass(ctx.can_issue ? "" : "btn-primary");
	}

	if (ctx.can_verify) {
		frm.add_custom_button(__("Verify & Close"), () => {
			frappe.confirm(
				__("Verify and close this gate pass? Verification is acceptance — no further return can be recorded.<br>क्या यह गेट पास सत्यापित कर बंद करें? सत्यापन ही स्वीकृति है — इसके बाद कोई वापसी दर्ज नहीं होगी।"),
				() => ts_rgp_post(frm, "verify_rgp", {})
			);
		}).addClass("btn-success");
	}

	if (ctx.can_request_close_short) {
		frm.add_custom_button(__("Request Close-Short"), () => {
			frappe.prompt(
				[{
					fieldname: "reason", fieldtype: "Small Text",
					label: __("Reason"), reqd: 1,
					description: __("At least 10 characters — recorded on the pass."),
				}],
				(v) => ts_rgp_post(frm, "request_close_short", { reason: v.reason }),
				__("Request Close-Short — {0} unit(s) will be written off; CEO approval required",
					[ctx.balance])
			);
		}, __("More"));
	}

	if (ctx.can_approve_close_short) {
		frm.add_custom_button(__("Approve Close-Short"), () => {
			frappe.confirm(
				__("Approve close-short of {0} unit(s)? This closes the pass permanently.<br>{0} इकाई की कमी सहित बंद करने की स्वीकृति दें? यह पास स्थायी रूप से बंद हो जाएगा।",
					[ctx.balance]),
				() => ts_rgp_post(frm, "approve_close_short", {})
			);
		}).addClass("btn-danger");
	}

	if (ctx.can_reject_close_short) {
		frm.add_custom_button(__("Reject Close-Short"), () => {
			frappe.prompt(
				[{
					fieldname: "reason", fieldtype: "Small Text",
					label: __("Rejection Reason"), reqd: 1,
				}],
				(v) => ts_rgp_post(frm, "reject_close_short", { reason: v.reason }),
				__("Reject Close-Short")
			);
		}, __("More"));
	}

	// ── Phase B: gate endorsement buttons (guard-facing, one confirm each) ──
	if (ctx.can_g2_out) {
		frm.add_custom_button(__("G2 · Endorse OUT"), () => {
			frappe.confirm(
				__("Material verified against the pass at the plant gate? / संयंत्र गेट पर सामग्री का पास से मिलान हो गया?"),
				() => ts_rgp_gate_post(frm, "rgp_gate_out", "G2")
			);
		}).addClass("btn-warning");
	}
	if (ctx.can_g1_out) {
		frm.add_custom_button(__("G1 · Final Exit OUT"), () => {
			frappe.confirm(
				__("Allow the material to leave the campus? / सामग्री को परिसर से बाहर जाने दें?"),
				() => ts_rgp_gate_post(frm, "rgp_gate_out", "G1")
			);
		}).addClass("btn-warning");
	}
	if (ctx.can_g1_in) {
		frm.add_custom_button(__("G1 · Endorse IN"), () => {
			frappe.confirm(
				__("Record material arriving back at the campus gate? / परिसर गेट पर सामग्री की वापसी दर्ज करें?"),
				() => ts_rgp_gate_post(frm, "rgp_gate_in", "G1")
			);
		});
	}
	if (ctx.can_g2_in) {
		frm.add_custom_button(__("G2 · Endorse IN"), () => {
			frappe.confirm(
				__("Record material arriving back inside the plant? Stores will then verify. / संयंत्र में सामग्री की वापसी दर्ज करें?"),
				() => ts_rgp_gate_post(frm, "rgp_gate_in", "G2")
			);
		});
	}

	if (frm.doc.docstatus === 1 && ctx.status !== "Cancelled") {
		frm.add_custom_button(__("Print Challan"), () => {
			const url = frappe.urllib.get_full_url(
				`/printview?doctype=${encodeURIComponent("TS Returnable Gate Pass")}`
				+ `&name=${encodeURIComponent(frm.doc.name)}`
				+ `&format=${encodeURIComponent("BBPL RGP Challan")}&no_letterhead=1`);
			window.open(url, "_blank");
		});
	}

	if (frm.doc.material_request) {
		frm.add_custom_button(__("Source Indent"), () => {
			frappe.set_route("Form", "Material Request", frm.doc.material_request);
		}, __("View"));
	}
}

function ts_rgp_gate_post(frm, method, checkpoint) {
	frappe.call({
		method: `trustbit_ethanol.ts_gate_entry.ts_rgp_gate.${method}`,
		args: { rgp: frm.doc.name, checkpoint: checkpoint },
		freeze: true,
		freeze_message: __("Endorsing…"),
		callback(r) {
			// Walkthrough finding (28 Aug): gate-IN changes no status by
			// design, so a silent success read as "nothing happened" —
			// always confirm the endorsement explicitly.
			const m = r.message || {};
			let msg;
			if (method === "rgp_gate_out") {
				msg = __("{0} exit endorsed ✓ — status: {1}", [checkpoint, m.status]);
			} else if (m.stamped) {
				msg = __("{0} inward recorded ✓ (status stays {1} — Stores credits the return)",
					[checkpoint, m.status]);
			} else {
				msg = __("{0} inward recorded for an additional lot ✓ (first stamp kept)",
					[checkpoint]);
			}
			frappe.show_alert({ message: msg, indicator: "green" }, 6);
			frm.reload_doc();
		},
	});
}

function ts_rgp_post(frm, method, args) {
	frappe.call({
		method: `trustbit_ethanol.ts_gate_entry.ts_rgp.${method}`,
		args: Object.assign({ rgp: frm.doc.name }, args),
		freeze: true,
		freeze_message: __("Working…"),
		callback() { frm.reload_doc(); },
	});
}

// ── form locks ─────────────────────────────────────────────────────────
function ts_rgp_locks(frm, ctx) {
	if (TS_RGP_TERMINAL.includes(ctx.status)) {
		frm.disable_save();
		["expected_return_date", "remarks", "return_photo_1", "return_photo_2",
			"eway_bill_no", "eway_bill_date"].forEach((f) =>
			frm.set_df_property(f, "read_only", 1));
		frm.set_df_property("returns", "cannot_add_rows", 1);
		frm.set_df_property("returns", "cannot_delete_rows", 1);
	}
}

// ── Record Return dialog (per-row qty/serial/condition/photo — D6) ─────
function ts_rgp_return_dialog(frm) {
	const rows = (frm.doc.items || []).filter((it) => flt(it.balance_qty) > 0);
	if (!rows.length) {
		frappe.msgprint(__("Nothing is outstanding on this pass."));
		return;
	}
	const esc = frappe.utils.escape_html;
	const photo_by_row = {};

	let body = `
		<div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">
			${esc(frm.doc.supplier_name || "")} · ${esc(frm.doc.name)} ·
			${__("due")} ${frappe.datetime.str_to_user(frm.doc.expected_return_date)}<br>
			${__("Enter the quantity actually received back for each line.")} /
			${__("प्रत्येक पंक्ति के लिए वास्तव में वापस प्राप्त मात्रा दर्ज करें।")}
		</div>
		<div style="overflow-x:auto"><table class="table table-bordered" style="min-width:720px;font-size:12px;">
		<thead><tr>
			<th style="width:28px">#</th><th>${__("Item")}</th>
			<th style="width:70px;text-align:right">${__("Issued")}</th>
			<th style="width:80px;text-align:right">${__("Balance")}</th>
			<th style="width:110px">${__("Return Now")}</th>
			<th style="width:150px">${__("Serial")}</th>
			<th style="width:140px">${__("Condition In")}</th>
			<th style="width:100px">${__("Photo")}</th>
		</tr></thead><tbody>`;

	rows.forEach((it, j) => {
		const cond = ["", "Good", "Damaged", "Needs Repair", "Beyond Repair"]
			.map((c) => `<option value="${c}">${c || __("Select…")}</option>`).join("");
		body += `<tr data-row="${esc(it.name)}">
			<td style="color:var(--text-muted)">${j + 1}</td>
			<td><b>${esc(it.item_name || it.item_code)}</b><br>
				<span style="font-family:monospace;font-size:11px;opacity:.6">${esc(it.item_code)}</span></td>
			<td style="text-align:right">${flt(it.qty_out)}</td>
			<td style="text-align:right;font-weight:700;color:var(--red-500,#b94a48)">${flt(it.balance_qty)}</td>
			<td><input type="number" class="form-control rgp-ret-qty" data-j="${j}"
				min="0" max="${flt(it.balance_qty)}" step="any" style="text-align:right;height:44px"></td>
			<td>${cint(it.is_serialized)
				? `<input type="text" class="form-control rgp-ret-serial" data-j="${j}"
					placeholder="${esc(it.serial_no_out || "")}" style="height:44px">`
				: "—"}</td>
			<td><select class="form-control rgp-ret-cond" data-j="${j}" style="height:44px">${cond}</select></td>
			<td><button type="button" class="btn btn-xs btn-default rgp-ret-photo" data-j="${j}"
				style="height:44px;width:100%">${__("Upload")}</button></td>
		</tr>`;
	});
	body += `</tbody></table></div>
		<div class="rgp-ret-footer" style="font-size:11.5px;color:var(--text-muted);margin-top:6px;"></div>`;

	const d = new frappe.ui.Dialog({
		title: __("Record Return — {0}", [frm.doc.name]),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "ret_html" }],
		primary_action_label: __("Confirm Return"),
		primary_action() {
			const lines = [];
			let bad = "";
			rows.forEach((it, j) => {
				const qty = flt(d.$wrapper.find(`.rgp-ret-qty[data-j="${j}"]`).val());
				if (!qty) return;
				if (qty > flt(it.balance_qty)) bad = __("Row {0}: quantity exceeds balance.", [j + 1]);
				const cond2 = d.$wrapper.find(`.rgp-ret-cond[data-j="${j}"]`).val();
				if (!cond2) bad = bad || __("Row {0}: Condition In is required.", [j + 1]);
				if (!photo_by_row[j]) bad = bad || __("Row {0}: a return photo is required.", [j + 1]);
				lines.push({
					row_name: it.name,
					qty: qty,
					serial_no_in: (d.$wrapper.find(`.rgp-ret-serial[data-j="${j}"]`).val() || "").trim(),
					condition_in: cond2,
					return_photo: photo_by_row[j] || "",
				});
			});
			if (!lines.length) { frappe.msgprint(__("Enter a return quantity on at least one line.")); return; }
			if (bad) { frappe.msgprint(bad); return; }
			d.get_primary_btn().prop("disabled", true);
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.ts_rgp.record_rgp_return",
				args: { rgp: frm.doc.name, lines: JSON.stringify(lines) },
				freeze: true,
				callback() { d.hide(); frm.reload_doc(); },
				error() { d.get_primary_btn().prop("disabled", false); },
			});
		},
	});
	d.get_field("ret_html").$wrapper.html(body);

	const refresh_footer = () => {
		let now = 0;
		rows.forEach((it, j) => { now += flt(d.$wrapper.find(`.rgp-ret-qty[data-j="${j}"]`).val()); });
		const out = flt(frm.doc.total_balance) - now;
		d.$wrapper.find(".rgp-ret-footer").text(
			__("Returning {0} of {1} unit(s). {2} unit(s) will remain outstanding.",
				[now, flt(frm.doc.total_balance), out]));
	};
	d.$wrapper.on("input", ".rgp-ret-qty", refresh_footer);
	refresh_footer();

	d.$wrapper.on("click", ".rgp-ret-photo", function () {
		const j = $(this).data("j");
		const btn = $(this);
		new frappe.ui.FileUploader({
			doctype: "TS Returnable Gate Pass",
			docname: frm.doc.name,
			folder: "Home/Attachments",
			allow_multiple: false,
			restrictions: { allowed_file_types: ["image/*"], max_file_size: 5 * 1024 * 1024 },
			on_success(fd) {
				photo_by_row[j] = fd.file_url;
				btn.removeClass("btn-default").addClass("btn-success").text("✓ " + __("Added"));
			},
		});
	});

	d.show();
}
