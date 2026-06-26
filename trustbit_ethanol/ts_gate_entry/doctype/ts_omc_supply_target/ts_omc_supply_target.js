// TS OMC Supply Target — client UX: ESY window derive + live annual preview.
// Authoritative computation is server-side (validate); this is UX only.

frappe.ui.form.on("TS OMC Supply Target", {
	esy_code(frm) {
		_derive_esy_window(frm);
	},

	refresh(frm) {
		_recompute_annual_preview(frm);
	},

	q1_allocation_kl: (frm) => _recompute_annual_preview(frm),
	q2_allocation_kl: (frm) => _recompute_annual_preview(frm),
	q3_allocation_kl: (frm) => _recompute_annual_preview(frm),
	q4_allocation_kl: (frm) => _recompute_annual_preview(frm),
});

// Depot-plan child grid — live UX preview of the derived quarter. The parent
// validate() stamps the authoritative `quarter` server-side from instructed_on.
frappe.ui.form.on("TS OMC Depot Plan", {
	instructed_on(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const q = _quarter_for_date(row.instructed_on, frm.doc.esy_start);
		frappe.model.set_value(cdt, cdn, "quarter", q || "");
	},
});

function _quarter_for_date(d, esyStart) {
	if (!d || !esyStart) {
		return null;
	}
	const dt = frappe.datetime.str_to_obj(d);
	const start = frappe.datetime.str_to_obj(esyStart);
	if (!dt || !start) {
		return null;
	}
	// months elapsed since esy_start (Nov), mapped to Q1..Q4 (3 months each)
	let months = (dt.getFullYear() - start.getFullYear()) * 12 + (dt.getMonth() - start.getMonth());
	if (months < 0 || months > 11) {
		return null;
	}
	return "Q" + (Math.floor(months / 3) + 1);
}

function _derive_esy_window(frm) {
	const code = (frm.doc.esy_code || "").trim();
	const m = code.match(/^(?:ESY\s*)?(\d{4})\s*[-/]\s*(\d{2,4})\s*$/);
	if (!m) {
		return;
	}
	const startYear = parseInt(m[1], 10);
	if (!startYear) {
		return;
	}
	// Only auto-fill blank dates — never clobber a manual override.
	if (!frm.doc.esy_start) {
		frm.set_value("esy_start", `${startYear}-11-01`);
	}
	if (!frm.doc.esy_end) {
		frm.set_value("esy_end", `${startYear + 1}-10-31`);
	}
}

function _recompute_annual_preview(frm) {
	// Read-only client preview; the server recomputes authoritatively (Lesson 294).
	const annual =
		flt(frm.doc.q1_allocation_kl) +
		flt(frm.doc.q2_allocation_kl) +
		flt(frm.doc.q3_allocation_kl) +
		flt(frm.doc.q4_allocation_kl);
	frm.set_value("annual_allocation_kl", annual);
}
