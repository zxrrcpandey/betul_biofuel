// v2.10.0.8 — Live decomposition of Payment Schedule "Payment Amount (INR)" in
// the PO form when "Including GST" is unticked. Mirrors what the BBPL PO PDF
// will render. DB-stored payment_amount stays unchanged (ERPNext native math).
// Display-only in-browser: rewrites the rendered cell content per grid row
// when the flag is off AND the doc has tax (grand_total > net_total).
// Per-row suffix label is "+ GST" when a GST tax row exists, else "+ Tax".
//
// Loaded via app_include_js so it bypasses Frappe's form-meta cache.

frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		setTimeout(() => _ts_apply_gst_decomposition(frm), 200);
	},
	ts_payment_amount_includes_gst(frm) {
		setTimeout(() => _ts_apply_gst_decomposition(frm), 50);
	},
});

frappe.ui.form.on("Payment Schedule", {
	payment_schedule_add(frm) {
		setTimeout(() => _ts_apply_gst_decomposition(frm), 250);
	},
	payment_schedule_remove(frm) {
		setTimeout(() => _ts_apply_gst_decomposition(frm), 250);
	},
	invoice_portion(frm) {
		setTimeout(() => _ts_apply_gst_decomposition(frm), 100);
	},
	payment_amount(frm) {
		setTimeout(() => _ts_apply_gst_decomposition(frm), 100);
	},
});

window._ts_apply_gst_decomposition = function (frm) {
	if (!frm || frm.doctype !== "Purchase Order") return;
	const grid = frm.fields_dict.payment_schedule && frm.fields_dict.payment_schedule.grid;
	if (!grid || !grid.grid_rows || !grid.grid_rows.length) return;

	const flag_raw = frm.doc.ts_payment_amount_includes_gst;
	const flag = (flag_raw === undefined || flag_raw === null) ? 1 : cint(flag_raw);
	const grand = flt(frm.doc.grand_total);
	const net = flt(frm.doc.net_total);
	const has_tax = grand && net && grand > net;
	const decompose = (flag === 0) && has_tax;
	const currency = frm.doc.currency || "INR";

	// Smart label: "+ GST" when an explicit GST tax row exists, else "+ Tax"
	const has_gst_row = (frm.doc.taxes || []).some(
		(t) => /\bGST\b/i.test(t.description || "") || /\bGST\b/i.test(t.account_head || "")
	);
	const suffix_label = has_gst_row ? "+ GST" : "+ Tax";

	grid.grid_rows.forEach((row) => {
		if (!row || !row.doc) return;
		const $row = row.$wrapper || $(row.row || row.grid_row || []);
		if (!$row || !$row.length) return;
		let $cell = $row.find('.grid-static-col[data-fieldname="payment_amount"]');
		if (!$cell.length) $cell = $row.find('[data-fieldname="payment_amount"]');
		if (!$cell.length) return;

		const portion = flt(row.doc.invoice_portion);
		if (decompose) {
			const row_net = (portion / 100) * net;
			const row_tax = (portion / 100) * (grand - net);
			const html = `${format_currency(row_net, currency)} <span style="color:#666;font-size:0.92em;">${suffix_label} ${format_currency(row_tax, currency)}</span>`;
			if (!$cell.attr("data-ts-gst-original")) {
				$cell.attr("data-ts-gst-original", $cell.html());
			}
			$cell.html(html);
			$cell.attr("data-ts-gst-applied", "1");
		} else if ($cell.attr("data-ts-gst-applied") === "1") {
			const orig = $cell.attr("data-ts-gst-original");
			if (orig) {
				$cell.html(orig);
			} else {
				$cell.text(format_currency(flt(row.doc.payment_amount), currency));
			}
			$cell.removeAttr("data-ts-gst-applied");
			$cell.removeAttr("data-ts-gst-original");
		}
	});
};

// ── GST Breakdown summary table (CGST/SGST/IGST + Total GST Amount) ──────────
// Read-only panel rendered into the `ts_gst_breakdown_html` custom field on the
// Terms tab. Auto-derived from doc.taxes (grouped by account_head/description);
// recomputes on form refresh and on any change to the taxes grid. Display-only,
// stores nothing — mirrors the GST table the BBPL PO PDF renders.
frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		setTimeout(() => _ts_render_gst_breakdown(frm), 220);
		setTimeout(() => _ts_render_gst_breakdown(frm), 800);
	},
});

frappe.ui.form.on("Purchase Taxes and Charges", {
	taxes_add(frm) { setTimeout(() => _ts_render_gst_breakdown(frm), 200); },
	taxes_remove(frm) { setTimeout(() => _ts_render_gst_breakdown(frm), 200); },
	tax_amount(frm) { setTimeout(() => _ts_render_gst_breakdown(frm), 150); },
	rate(frm) { setTimeout(() => _ts_render_gst_breakdown(frm), 150); },
	account_head(frm) { setTimeout(() => _ts_render_gst_breakdown(frm), 150); },
});

window._ts_render_gst_breakdown = function (frm) {
	if (!frm || frm.doctype !== "Purchase Order") return;
	const field = frm.fields_dict && frm.fields_dict.ts_gst_breakdown_html;
	if (!field) return;

	const currency = frm.doc.currency || "INR";
	const grand = flt(frm.doc.grand_total);
	const net = flt(frm.doc.net_total);

	let cgst = 0, sgst = 0, igst = 0;
	(frm.doc.taxes || []).forEach((t) => {
		const amt = flt(t.tax_amount);
		const hay = ((t.account_head || "") + " " + (t.description || "")).toUpperCase();
		if (hay.indexOf("CGST") !== -1) cgst += amt;
		else if (hay.indexOf("SGST") !== -1 || hay.indexOf("UTGST") !== -1) sgst += amt;
		else if (hay.indexOf("IGST") !== -1) igst += amt;
	});

	const classified = cgst + sgst + igst;
	const diff = grand - net;
	const total = classified > 0 ? classified : (diff > 0 ? diff : 0);

	// Blank placeholder (not "") so ControlHTML.refresh_input — which skips falsy
	// content — still ACTIVELY repaints (clearing any stale table) when GST is removed.
	let html = "<div></div>";
	if (total > 0) {
		const td = (txt, bold, right) =>
			`<td style="padding:4px 8px;border:1px solid #d1d8dd;${right ? "text-align:right;" : ""}${bold ? "font-weight:bold;" : ""}">${txt}</td>`;
		const tr = (label, amt, bold) =>
			`<tr>${td(label, bold, false)}${td(format_currency(amt, currency), bold, true)}</tr>`;
		let body = "";
		if (classified > 0) {
			if (cgst > 0) body += tr("CGST", cgst, false);
			if (sgst > 0) body += tr("SGST", sgst, false);
			if (igst > 0) body += tr("IGST", igst, false);
		} else {
			body += tr("GST", diff, false);
		}
		body += tr("Total GST Amount", total, true);
		html =
			`<div style="margin-top:8px;">` +
			`<div style="font-weight:bold;margin-bottom:4px;">GST Breakdown</div>` +
			`<table style="border-collapse:collapse;font-size:0.95em;">` +
			`<thead><tr>` +
			`<th style="padding:4px 8px;border:1px solid #d1d8dd;text-align:left;background:#f7f7f7;">Tax Type</th>` +
			`<th style="padding:4px 8px;border:1px solid #d1d8dd;text-align:right;background:#f7f7f7;">Amount</th>` +
			`</tr></thead><tbody>${body}</tbody></table></div>`;
	}

	// Persist into the HTML field's `options` so Frappe's ControlHTML.refresh_input()
	// re-paints it on EVERY form/tab refresh. A bare $wrapper.html() write is blanked
	// the next time the control (or its lazily-shown tab) refreshes, because Frappe
	// only re-renders an HTML control from df.options — that was the "not showing" bug.
	// We set df.options (persistent) AND paint immediately for the first render.
	field.df.options = html;
	if (field.$wrapper) field.$wrapper.html(html);
};
