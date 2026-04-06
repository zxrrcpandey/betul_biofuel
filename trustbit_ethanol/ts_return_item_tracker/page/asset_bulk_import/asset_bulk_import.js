frappe.pages["asset-bulk-import"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "TS Bulk Return Item Import", single_column: true });
	$(frappe.render_template("asset_bulk_import")).appendTo(page.body);
	new TSReturnItemBulkImport(page);
};

class TSReturnItemBulkImport {
	constructor(page) {
		this.page = page;
		this.$page = $(page.body);
		this.grid_rows = [];
		this.row_counter = 0;
		this.mode = "grid";
		this._bind_events();
		this._add_rows(5);
	}

	_bind_events() {
		const me = this;
		me.$page.find("#abi-mode-grid").on("click", () => me._set_mode("grid"));
		me.$page.find("#abi-mode-csv").on("click", () => me._set_mode("csv"));
		me.$page.find("#abi-add-1").on("click", () => me._add_rows(1));
		me.$page.find("#abi-add-5").on("click", () => me._add_rows(5));
		me.$page.find("#abi-add-20").on("click", () => me._add_rows(20));
		me.$page.find("#abi-btn-import-grid").on("click", () => me._import_grid());
		me.$page.find("#abi-download-ref").on("click", () => me._download_ref());
		me.$page.find("#abi-download-template").on("click", () => me._download_template());
		me.$page.find("#abi-upload-zone").on("click", () => me.$page.find("#abi-file-input").click());
		me.$page.find("#abi-upload-zone").on("dragover", e => { e.preventDefault(); });
		me.$page.find("#abi-upload-zone").on("drop", e => { e.preventDefault(); me._handle_file(e.originalEvent.dataTransfer.files[0]); });
		me.$page.find("#abi-file-input").on("change", e => { if (e.target.files[0]) me._handle_file(e.target.files[0]); });
		me.$page.find("#abi-file-remove").on("click", () => { me.$page.find("#abi-upload-info").hide(); me.$page.find("#abi-file-input").val(""); });
		me.$page.find("#abi-btn-another").on("click", () => me._reset());
	}

	_set_mode(mode) {
		this.mode = mode;
		this.$page.find(".abi-mode-btn").removeClass("active btn-primary-dark").addClass("btn-default");
		this.$page.find("#abi-mode-" + mode).addClass("active btn-primary-dark").removeClass("btn-default");
		this.$page.find("#abi-grid-section").toggle(mode === "grid");
		this.$page.find("#abi-csv-section").toggle(mode === "csv");
		this.$page.find("#abi-grid-actions").toggle(mode === "grid");
	}

	_add_rows(count) {
		for (let i = 0; i < count; i++) {
			this.row_counter++;
			const row = { idx: this.row_counter, item_name: "", category: "", uom: "Nos", qty: "", purchase_value: "", location: "", warranty_expiry: "" };
			this.grid_rows.push(row);
			this._render_row(row);
		}
		this._update_count();
	}

	_render_row(row) {
		const me = this;
		const $tr = $(`<tr data-idx="${row.idx}">
			<td style="text-align:center; color:#999; font-size:11px;">${row.idx}</td>
			<td><input type="text" class="abi-input" data-field="item_name" placeholder="Item name..."></td>
			<td><div class="abi-link" data-field="category"></div></td>
			<td><div class="abi-link" data-field="uom"></div></td>
			<td><input type="number" class="abi-input" data-field="qty" placeholder="0" style="width:70px; text-align:right;"></td>
			<td><input type="number" class="abi-input" data-field="purchase_value" placeholder="0" style="width:90px; text-align:right;"></td>
			<td><div class="abi-link" data-field="location"></div></td>
			<td><input type="date" class="abi-input" data-field="warranty_expiry" style="width:130px;"></td>
			<td style="text-align:center;"><span class="abi-row-del" title="Delete">&times;</span></td>
		</tr>`);

		// Category — Select control
		const cat_ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Select", options: "\nConsumable\nReturnable\nTool\nEquipment", placeholder: "Category..." },
			parent: $tr.find('.abi-link[data-field="category"]'), render_input: true, only_input: true,
		});
		cat_ctrl.$input.on("change", () => { row.category = cat_ctrl.get_value(); me._update_count(); });
		row._cat = cat_ctrl;

		// UOM — Link
		const uom_ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "UOM", placeholder: "UOM..." },
			parent: $tr.find('.abi-link[data-field="uom"]'), render_input: true, only_input: true,
		});
		uom_ctrl.set_value("Nos"); row.uom = "Nos";
		uom_ctrl.$input.on("change", () => { row.uom = uom_ctrl.get_value(); });
		row._uom = uom_ctrl;

		// Location — Link
		const loc_ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "TS Return Item Location", placeholder: "Location..." },
			parent: $tr.find('.abi-link[data-field="location"]'), render_input: true, only_input: true,
		});
		loc_ctrl.$input.on("change", () => { row.location = loc_ctrl.get_value(); });
		row._loc = loc_ctrl;

		$tr.find("input.abi-input").on("change input", function () { row[$(this).data("field")] = $(this).val(); me._update_count(); });
		$tr.find(".abi-row-del").on("click", () => { $tr.remove(); me.grid_rows = me.grid_rows.filter(r => r.idx !== row.idx); me._update_count(); });

		this.$page.find("#abi-grid-body").append($tr);
	}

	_update_count() {
		const filled = this.grid_rows.filter(r => r.item_name && r.category).length;
		this.$page.find("#abi-grid-count").text(filled);
		this.$page.find("#abi-btn-import-grid").prop("disabled", filled === 0);
	}

	_get_grid_data() {
		for (const row of this.grid_rows) {
			if (row._cat) row.category = row._cat.get_value() || "";
			if (row._uom) row.uom = row._uom.get_value() || "Nos";
			if (row._loc) row.location = row._loc.get_value() || "";
		}
		return this.grid_rows.filter(r => r.item_name && r.category).map(r => ({
			item_name: r.item_name, category: r.category, uom: r.uom || "Nos",
			qty: r.qty || 0, purchase_value: r.purchase_value || 0,
			location: r.location || "", warranty_expiry: r.warranty_expiry || "",
		}));
	}

	_import_grid() {
		const rows = this._get_grid_data();
		if (!rows.length) { frappe.msgprint("Fill at least one row with Item Name and Category."); return; }
		frappe.confirm(`Import <b>${rows.length}</b> assets?`, () => this._process(rows));
	}

	_handle_file(file) {
		if (!file || !file.name.endsWith(".csv")) { frappe.msgprint("Only .csv files accepted."); return; }
		if (file.size > 5 * 1024 * 1024) { frappe.msgprint("Max 5MB."); return; }
		const reader = new FileReader();
		reader.onload = (e) => {
			const lines = e.target.result.trim().split("\n");
			const headers = this._parse_csv_line(lines[0]).map(h => h.trim().toLowerCase().replace(/\s+/g, "_"));
			const rows = [];
			for (let i = 1; i < lines.length && i <= 500; i++) {
				if (!lines[i].trim()) continue;
				const vals = this._parse_csv_line(lines[i]);
				const row = {};
				headers.forEach((h, j) => { row[h] = (vals[j] || "").trim(); });
				if (row.item_name) rows.push(row);
			}
			this.$page.find("#abi-upload-info").show();
			this.$page.find("#abi-file-name").text(file.name);
			this.$page.find("#abi-file-rows").text(rows.length + " rows");
			if (rows.length) {
				frappe.confirm(`Import <b>${rows.length}</b> assets from CSV?`, () => this._process(rows));
			}
		};
		reader.readAsText(file);
	}

	_parse_csv_line(line) {
		// Proper CSV parsing — handles quoted fields with commas inside
		const result = [];
		let current = "";
		let in_quotes = false;
		for (let i = 0; i < line.length; i++) {
			const ch = line[i];
			if (ch === '"') {
				in_quotes = !in_quotes;
			} else if (ch === ',' && !in_quotes) {
				result.push(current);
				current = "";
			} else {
				current += ch;
			}
		}
		result.push(current);
		return result;
	}

	_process(rows) {
		const me = this;
		const total = rows.length;
		let done = 0;
		const results = [];
		me.$page.find("#abi-progress-overlay").show();

		(function next() {
			if (done >= total) {
				me.$page.find("#abi-progress-overlay").hide();
				me._show_results(results);
				return;
			}
			const batch = rows.slice(done, done + 10);
			frappe.call({
				method: "trustbit_ethanol.ts_return_item_tracker.ts_return_item_api.bulk_import_assets",
				args: { rows: JSON.stringify(batch) },
				callback(r) {
					if (r.message) results.push(...r.message);
					done += batch.length;
					const pct = Math.round((done / total) * 100);
					me.$page.find("#abi-progress-bar").css("width", pct + "%");
					me.$page.find("#abi-progress-text").text(done + " / " + total);
					next();
				},
				error() {
					batch.forEach(b => results.push({ success: false, item_name: b.item_name, error: "Batch failed" }));
					done += batch.length;
					next();
				}
			});
		})();
	}

	_show_results(results) {
		let ok = 0, fail = 0, html = "";
		results.forEach((r, i) => {
			if (r.success) {
				ok++;
				html += `<tr><td>${i + 1}</td><td style="color:#10b981; font-weight:600;">Created</td>
					<td><a href="/app/ts-asset-item/${r.name}">${r.name}</a></td>
					<td>${r.item_name}</td><td>${r.category}</td><td>Stock: ${r.current_stock}</td></tr>`;
			} else {
				fail++;
				html += `<tr style="background:#fff5f5;"><td>${i + 1}</td><td style="color:#ef4444; font-weight:600;">Failed</td>
					<td>—</td><td>${r.item_name || "?"}</td><td>—</td><td style="color:#ef4444;">${r.error}</td></tr>`;
			}
		});
		this.$page.find("#abi-result-success").text(ok);
		this.$page.find("#abi-result-fail").text(fail);
		this.$page.find("#abi-results-body").html(html);
		this.$page.find("#abi-phase-upload").hide();
		this.$page.find("#abi-phase-results").show();
	}

	_download_ref() {
		frappe.call({
			method: "trustbit_ethanol.ts_return_item_tracker.ts_return_item_api.get_asset_reference",
			callback(r) {
				if (!r.message) return;
				const d = r.message;
				let csv = "=== TS ASSET TRACKER REFERENCE GUIDE ===\n\n";
				csv += "--- CATEGORIES ---\nConsumable\nReturnable\nTool\nEquipment\n\n";
				csv += "--- UOMs ---\n" + (d.uoms || []).map(u => u.name).join("\n") + "\n\n";
				csv += "--- LOCATIONS ---\n" + (d.locations || []).map(l => l.name + " (" + (l.location_type || "") + ")").join("\n") + "\n";
				const blob = new Blob([csv], { type: "text/csv" });
				const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
				a.download = "ts_return_item_reference.csv"; a.click();
				frappe.show_alert({ message: "Reference downloaded!", indicator: "green" });
			}
		});
	}

	_download_template() {
		const csv = "item_name,category,uom,qty,purchase_value,location,warranty_expiry,purchase_date,amc_expiry,amc_vendor,item_group,description,min_stock_level,expected_return_hours\n" +
			'"Angle Grinder 7 inch","Tool","Nos","1","4500","Main Store","01-04-2027","15-03-2026","","","Power Tools","Bosch 7 inch angle grinder","1","8"\n' +
			'"Safety Helmet","Returnable","Nos","10","850","Main Store","01-01-2028","01-01-2026","","","Safety","ISI marked safety helmet","5",""\n' +
			'"Welding Rod 3.15mm","Consumable","Kg","50","120","Main Store","","","","","Consumables","ER 70S-6 welding rod","10",""\n';
		const blob = new Blob([csv], { type: "text/csv" });
		const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "ts_return_item_template.csv"; a.click();
	}

	_reset() {
		this.grid_rows = []; this.row_counter = 0;
		this.$page.find("#abi-grid-body").empty();
		this.$page.find("#abi-phase-results").hide();
		this.$page.find("#abi-phase-upload").show();
		this.$page.find("#abi-upload-info").hide();
		this._add_rows(5);
		this._set_mode("grid");
	}
}
