// v2.9.14 — Vehicle Origin autocomplete
// Suggests previously-entered values across G1/G2/PR/PI on the
// vehicle_origin Data field using Awesomplete.

(function () {
	"use strict";
	const FIELD = "vehicle_origin";
	const SOURCES = ["TS Gate Entry", "TS Token", "Purchase Receipt", "Purchase Invoice"];

	async function _fetch_suggestions() {
		const seen = new Set();
		for (const dt of SOURCES) {
			try {
				const rows = await frappe.db.get_list(dt, {
					fields: [FIELD],
					filters: [[FIELD, "is", "set"]],
					limit: 200,
					order_by: "modified desc",
				});
				for (const r of rows || []) {
					const v = (r[FIELD] || "").trim();
					if (v) seen.add(v);
				}
			} catch (e) {
				// user may lack read perm on a doctype — silently skip
			}
		}
		return Array.from(seen).sort();
	}

	function _attach(frm) {
		if (!frm || !frm.fields_dict || !frm.fields_dict[FIELD]) return;
		const fld = frm.fields_dict[FIELD];
		if (fld.__vehicle_origin_wired) return;
		const $input = fld.$input;
		if (!$input || !$input.length || typeof Awesomplete === "undefined") return;
		fld.__vehicle_origin_wired = true;
		const aw = new Awesomplete($input[0], { list: [], minChars: 1, maxItems: 10, autoFirst: true });
		$input.on("focus", async function () {
			if (fld.__suggestions_loaded) return;
			fld.__suggestions_loaded = true;
			aw.list = await _fetch_suggestions();
		});
	}

	for (const dt of SOURCES) {
		frappe.ui.form.on(dt, { refresh: _attach, onload: _attach });
	}
})();
