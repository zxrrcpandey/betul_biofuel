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
