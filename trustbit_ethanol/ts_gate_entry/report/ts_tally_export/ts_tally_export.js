// TS Tally Export — v2.43.0
// Previews one sheet of the client's Tally import workbook; the toolbar button
// exports all three sheets (Pmt Receipt / JV / Sales) as one .xlsx.
// fetch + Blob, NOT frappe.xcall — xcall JSON-parses the response and would
// corrupt the binary.

function _ts_tally_fy_start() {
	try {
		if (typeof erpnext !== "undefined" && erpnext.utils) {
			const fy = erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true);
			if (fy && fy[1]) return fy[1];
		}
	} catch (e) {
		// fall through to the calendar-year start
	}
	return frappe.datetime.year_start();
}

frappe.query_reports["TS Tally Export"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			description: __("Clear this field to include all companies"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: _ts_tally_fy_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "sheet",
			label: __("Preview Sheet"),
			fieldtype: "Select",
			options: ["Pmt Receipt", "JV", "Sales"].join("\n"),
			default: "JV",
			reqd: 1,
			description: __("Preview only — the Export button always writes all three sheets"),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		// Narrations / ledger names are free text — escape so stored markup never renders.
		if (column.fieldtype === "Data" && typeof value === "string") {
			value = frappe.utils.escape_html(value);
		}
		return default_formatter(value, row, column, data);
	},

	onload(report) {
		report.page.add_inner_button(__("Export to Tally (Excel)"), () => {
			const filters = report.get_values();
			if (!filters.from_date || !filters.to_date) {
				frappe.msgprint(__("Please set From Date and To Date before exporting."));
				return;
			}
			if (filters.from_date > filters.to_date) {
				frappe.msgprint(__("From Date cannot be after To Date."));
				return;
			}
			frappe.confirm(
				__("Export all three Tally sheets for {0} to {1}?", [filters.from_date, filters.to_date]),
				() => _ts_tally_download(filters)
			);
		});
	},
};

function _ts_tally_download(filters) {
	frappe.dom.freeze(__("Generating Tally workbook..."));
	fetch("/api/method/trustbit_ethanol.ts_gate_entry.api.ts_tally_export.export_tally_excel", {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			"X-Frappe-CSRF-Token": frappe.csrf_token,
		},
		body: JSON.stringify({ filters }),
	})
		.then((res) => {
			if (!res.ok) {
				// fetch bypasses frappe.call's handler, so surface the server message ourselves
				// (role gate / read gate / "narrow the date range" are all actionable).
				return res.text().then((t) => {
					let msg = `HTTP ${res.status}`;
					try {
						const sm = JSON.parse(JSON.parse(t)._server_messages || "[]");
						if (sm.length) msg = JSON.parse(sm[0]).message || msg;
					} catch (e) {
						// keep the HTTP code
					}
					throw new Error(msg);
				});
			}
			const cd = res.headers.get("Content-Disposition") || "";
			const m = cd.match(/filename="?([^";]+)"?/);
			return res.blob().then((blob) => ({ blob, name: m ? m[1] : null }));
		})
		.then(({ blob, name }) => {
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			a.download = name || `TS_Tally_Export_${filters.company || "ALL"}_${filters.from_date}_to_${filters.to_date}.xlsx`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
		})
		.catch((err) => {
			frappe.msgprint({
				title: __("Export Failed"),
				message: __("Could not generate the Tally workbook: {0}", [frappe.utils.escape_html(err.message)]),
				indicator: "red",
			});
		})
		.finally(() => frappe.dom.unfreeze());
}
