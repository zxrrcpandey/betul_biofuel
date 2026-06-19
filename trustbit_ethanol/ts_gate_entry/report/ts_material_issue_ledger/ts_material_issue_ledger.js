// TS Material Issue Ledger — v2.14.0
// Combined view of material-movement requests (MR type = Material Issue /
// Material Transfer) and their actual movements (SE purpose = Material Issue /
// Material Transfer) with Requested vs Issued vs Pending per row. Status filter
// post-computed. Standalone SE movements (no MR origin) included via a 2nd pass.
// Toolbar: Export Branded PDF + Export Excel (all 17 columns, totals row).

frappe.query_reports["TS Material Issue Ledger"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
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
			fieldname: "report_type",
			label: __("Report Type"),
			fieldtype: "Select",
			options: [
				{ value: "Detailed", label: __("Detailed (per transaction)") },
				{ value: "Consolidated", label: __("Consolidated (Item × Cost Center)") },
			],
			default: "Detailed",
			reqd: 1,
			description: __("Consolidated = item-wise actual consumption summed by Cost Center (defaults to Material Issue; widen via Stock Entry Type)"),
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "source_warehouse",
			label: __("Source Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "material_request_type",
			label: __("Material Request Type"),
			fieldtype: "Select",
			// Display labels are prefixed "MR ·" so this dropdown is instantly
			// distinguishable from the Stock Entry Type filter; the bound VALUE
			// stays the raw type ("Material Issue" / "Material Transfer") for the query.
			options: [
				{ value: "All", label: __("MR · All") },
				{ value: "Material Issue", label: __("MR · Material Issue") },
				{ value: "Material Transfer", label: __("MR · Material Transfer") },
			],
			default: "All",
			description: __("Material REQUEST type — a specific type also hides standalone Stock Entries (no MR origin)"),
		},
		{
			fieldname: "stock_entry_type",
			label: __("Stock Entry Type"),
			fieldtype: "Select",
			// "SE ·" prefixed display labels — value stays the raw movement type.
			options: [
				{ value: "All", label: __("SE · All") },
				{ value: "Material Issue", label: __("SE · Material Issue") },
				{ value: "Material Transfer", label: __("SE · Material Transfer") },
			],
			default: "All",
			description: __("Stock ENTRY (movement) type — includes standalone Stock Entries of this type"),
		},
		{
			fieldname: "use_location",
			label: __("Use Location (contains)"),
			fieldtype: "Data",
			description: __("Substring match against the request's Use Location"),
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["All", "Pending", "Partial", "Fulfilled", "Standalone"].join("\n"),
			default: "All",
		},
		{
			fieldname: "include_cancelled",
			label: __("Include Cancelled"),
			fieldtype: "Check",
			default: 0,
		},
	],

	onload(report) {
		const _export = (endpoint, ext) => {
			const filters = report.get_values();
			if (!filters.company || !filters.from_date || !filters.to_date) {
				frappe.msgprint(__("Please set Company, From Date and To Date before exporting."));
				return;
			}
			frappe.confirm(
				__("Export current report rows to {0}?", [ext.toUpperCase()]),
				() => {
					frappe.dom.freeze(__("Generating {0}...", [ext.toUpperCase()]));
					fetch(`/api/method/${endpoint}`, {
						method: "POST",
						headers: {
							"Content-Type": "application/json",
							"X-Frappe-CSRF-Token": frappe.csrf_token,
						},
						body: JSON.stringify({ filters }),
					})
						.then((res) => {
							if (!res.ok) throw new Error(`HTTP ${res.status}`);
							return res.blob();
						})
						.then((blob) => {
							const url = URL.createObjectURL(blob);
							const a = document.createElement("a");
							a.href = url;
							a.download = `TS_Material_Issue_Ledger_${filters.from_date}_to_${filters.to_date}.${ext}`;
							document.body.appendChild(a);
							a.click();
							a.remove();
							URL.revokeObjectURL(url);
						})
						.catch((err) => {
							frappe.msgprint({
								title: __("Export Failed"),
								message: __("Could not generate export: {0}", [err.message]),
								indicator: "red",
							});
						})
						.finally(() => frappe.dom.unfreeze());
				}
			);
		};

		report.page.add_inner_button(__("Export Branded PDF"), () =>
			_export("trustbit_ethanol.ts_gate_entry.api.ts_pdf_export.export_material_issue_ledger_pdf", "pdf")
		);
		report.page.add_inner_button(__("Export Excel"), () =>
			_export("trustbit_ethanol.ts_gate_entry.api.ts_excel_export.export_material_issue_ledger_excel", "xlsx")
		);
	},

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (!data) return formatted;
		// Consolidated mode — bold the per-Cost-Center subtotal rows.
		if (data.is_subtotal) {
			return `<span style="font-weight:700;">${formatted}</span>`;
		}
		// Status indicator pill
		if (column.fieldname === "status" && value) {
			const map = {
				"Pending": "red",
				"Partial": "orange",
				"Fulfilled": "green",
				"Standalone": "blue",
			};
			const color = map[value] || "gray";
			return `<span class="indicator-pill ${color}">${frappe.utils.escape_html(value)}</span>`;
		}
		// Pending Qty colored cell
		if (column.fieldname === "pending_qty") {
			const pending = parseFloat(value);
			if (pending > 0) {
				return `<span style="color:#cc0000;font-weight:600;">${formatted}</span>`;
			}
			if (pending === 0 && data.status === "Fulfilled") {
				return `<span style="color:#16a34a;">${formatted}</span>`;
			}
		}
		// Issued Qty subtle green when matches requested
		if (column.fieldname === "issued_qty") {
			const iss = parseFloat(value);
			const req = parseFloat(data.requested_qty);
			if (iss > 0 && iss >= req && req > 0) {
				return `<span style="color:#16a34a;">${formatted}</span>`;
			}
		}
		return formatted;
	},
};
