frappe.pages["item-bulk-import"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "TS Bulk Item Creator",
		single_column: true,
	});
	$(frappe.render_template("item_bulk_import")).appendTo(page.body);
	new TSBulkImport(page);
};

class TSBulkImport {
	constructor(page) {
		this.page = page;
		this.$page = $(page.body);
		this.parsed_rows = [];
		this.validated_results = [];
		this.grid_rows = [];
		this.row_counter = 0;
		this.mode = "grid";

		this._bind_events();
		this._add_rows(5);
	}

	_bind_events() {
		const me = this;

		// Mode toggle
		me.$page.find("#bbf-mode-grid").on("click", () => me._set_mode("grid"));
		me.$page.find("#bbf-mode-csv").on("click", () => me._set_mode("csv"));

		// Add rows
		me.$page.find("#bbf-add-1").on("click", () => me._add_rows(1));
		me.$page.find("#bbf-add-5").on("click", () => me._add_rows(5));
		me.$page.find("#bbf-add-20").on("click", () => me._add_rows(20));

		// Validate grid
		me.$page.find("#bbf-btn-validate-grid").on("click", () => me._validate_grid());

		// Downloads
		me.$page.find("#bbf-download-ref").on("click", () => me._download_reference());
		me.$page.find("#bbf-download-template").on("click", () => me._download_template());

		// CSV upload
		me.$page.find("#bbf-upload-zone").on("click", () => me.$page.find("#bbf-file-input").click());
		me.$page.find("#bbf-upload-zone").on("dragover", (e) => { e.preventDefault(); $(e.currentTarget).addClass("bbf-drag-over"); });
		me.$page.find("#bbf-upload-zone").on("dragleave drop", (e) => { e.preventDefault(); $(e.currentTarget).removeClass("bbf-drag-over"); });
		me.$page.find("#bbf-upload-zone").on("drop", (e) => { me._handle_file(e.originalEvent.dataTransfer.files[0]); });
		me.$page.find("#bbf-file-input").on("change", (e) => { if (e.target.files[0]) me._handle_file(e.target.files[0]); });
		me.$page.find("#bbf-file-remove").on("click", () => me._reset_csv());

		// Phase 2/3
		me.$page.find("#bbf-btn-back-upload").on("click", () => me._back_to_upload());
		me.$page.find("#bbf-btn-create-all").on("click", () => me._create_all());
		me.$page.find("#bbf-btn-import-another").on("click", () => me._reset_all());
	}

	// ═══════════════════════════════════════════════════════════
	//  MODE TOGGLE
	// ═══════════════════════════════════════════════════════════

	_set_mode(mode) {
		this.mode = mode;
		this.$page.find(".bbf-mode-btn").removeClass("active btn-primary-dark").addClass("btn-default");
		this.$page.find("#bbf-mode-" + mode).addClass("active btn-primary-dark").removeClass("btn-default");
		this.$page.find("#bbf-grid-section").toggle(mode === "grid");
		this.$page.find("#bbf-csv-section").toggle(mode === "csv");
		this.$page.find("#bbf-grid-actions").toggle(mode === "grid");
	}

	// ═══════════════════════════════════════════════════════════
	//  INTERACTIVE GRID
	// ═══════════════════════════════════════════════════════════

	_add_rows(count) {
		for (let i = 0; i < count; i++) {
			this.row_counter++;
			const row = {
				idx: this.row_counter,
				item_name: "", company: "", item_group: "",
				stock_uom: "Kg", gst_hsn_code: "",
				variant_codes: "", valuation_rate: "",
				posting_date: "",
			};
			this.grid_rows.push(row);
			this._render_grid_row(row);
		}
		this._update_grid_count();
	}

	_render_grid_row(row) {
		const me = this;
		const idx = row.idx;

		const $tr = $(`<tr data-idx="${idx}">
			<td class="bbf-col-num">${idx}</td>
			<td><input type="text" class="bbf-grid-input" data-field="item_name" placeholder="Item name..."></td>
			<td><div class="bbf-link-wrap" data-field="company"></div></td>
			<td><div class="bbf-link-wrap" data-field="item_group"></div></td>
			<td><div class="bbf-link-wrap" data-field="stock_uom"></div></td>
			<td><div class="bbf-link-wrap" data-field="gst_hsn_code"></div></td>
			<td><input type="text" class="bbf-grid-input" data-field="variant_codes" placeholder="e.g. JDL,SKF"></td>
			<td><input type="number" class="bbf-grid-input bbf-grid-rate" data-field="valuation_rate" placeholder="0"></td>
			<td><input type="date" class="bbf-grid-input" data-field="posting_date" style="width:130px;" title="Leave blank for today. Set for backdated opening stock."></td>
			<td class="bbf-col-del"><span class="bbf-row-delete" title="Delete row">&times;</span></td>
		</tr>`);

		const links = {
			company: { options: "Company", placeholder: "Search company..." },
			item_group: { options: "Item Group", placeholder: "Search item group..." },
			stock_uom: { options: "UOM", placeholder: "UOM..." },
			gst_hsn_code: { options: "GST HSN Code", placeholder: "HSN code..." },
		};

		for (const [field, opts] of Object.entries(links)) {
			const $wrap = $tr.find(`.bbf-link-wrap[data-field="${field}"]`);
			const ctrl = frappe.ui.form.make_control({
				df: { fieldtype: "Link", options: opts.options, placeholder: opts.placeholder },
				parent: $wrap,
				render_input: true,
				only_input: true,
			});

			if (field === "stock_uom") {
				ctrl.set_value("Kg");
				row.stock_uom = "Kg";
			}

			ctrl.$input.on("change", () => {
				row[field] = ctrl.get_value();
				me._update_grid_count();
			});

			row["_ctrl_" + field] = ctrl;
		}

		$tr.find("input.bbf-grid-input").on("change input", function () {
			row[$(this).data("field")] = $(this).val();
			me._update_grid_count();
		});

		$tr.find(".bbf-row-delete").on("click", () => {
			$tr.remove();
			me.grid_rows = me.grid_rows.filter(r => r.idx !== idx);
			me._update_grid_count();
		});

		this.$page.find("#bbf-grid-body").append($tr);
	}

	_update_grid_count() {
		const filled = this.grid_rows.filter(r => r.item_name || r.company || r.item_group).length;
		this.$page.find("#bbf-grid-count").text(filled);
		this.$page.find("#bbf-btn-validate-grid").prop("disabled", filled === 0);
	}

	_get_grid_data() {
		for (const row of this.grid_rows) {
			for (const f of ["company", "item_group", "stock_uom", "gst_hsn_code"]) {
				const ctrl = row["_ctrl_" + f];
				if (ctrl) row[f] = ctrl.get_value() || "";
			}
		}
		return this.grid_rows
			.filter(r => r.item_name || r.company || r.item_group)
			.map((r, i) => ({
				_row_num: i + 2,
				item_name: r.item_name || "",
				company: r.company || "",
				item_group: r.item_group || "",
				stock_uom: r.stock_uom || "Kg",
				gst_hsn_code: r.gst_hsn_code || "",
				variant_codes: r.variant_codes || "",
				valuation_rate: r.valuation_rate || "",
				posting_date: r.posting_date || "",
				has_variant: r.variant_codes ? "Yes" : "No",
				variant_source: r.variant_codes ? "Custom Variant" : "",
				maintain_stock: "Yes",
			}));
	}

	_validate_grid() {
		const rows = this._get_grid_data();
		if (!rows.length) {
			frappe.msgprint("No items to validate. Please fill at least one row.");
			return;
		}
		this.parsed_rows = rows;
		this._validate_and_preview();
	}

	// ═══════════════════════════════════════════════════════════
	//  REFERENCE GUIDE
	// ═══════════════════════════════════════════════════════════

	_download_reference() {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.api_bulk_import.download_reference_guide",
			callback: (r) => {
				if (!r.message) return;
				const d = r.message;
				let csv = "=== REFERENCE GUIDE FOR BULK ITEM IMPORT ===\n\n";

				csv += "--- COMPANIES ---\nName,Char Code,Num Code\n";
				(d.companies || []).forEach(c => csv += `"${c.name}","${c.company_code || ""}","${c.company_num_code || ""}"\n`);

				csv += "\n--- ITEM GROUPS ---\nName,Char Code,Num Code\n";
				(d.item_groups || []).forEach(g => csv += `"${g.name}","${g.category_code || ""}","${g.category_num_code || ""}"\n`);

				csv += "\n--- BRANDS ---\nName,Brand Code\n";
				(d.brands || []).forEach(b => csv += `"${b.name}","${b.brand_code || ""}"\n`);

				csv += "\n--- CUSTOM VARIANTS ---\nName,Variant Code\n";
				(d.variants || []).forEach(v => csv += `"${v.name}","${v.variant_code || ""}"\n`);

				csv += "\n--- UOMs ---\nName\n";
				(d.uoms || []).forEach(u => csv += `"${u.name}"\n`);

				const blob = new Blob([csv], { type: "text/csv" });
				const a = document.createElement("a");
				a.href = URL.createObjectURL(blob);
				a.download = "ts_bulk_import_reference.csv";
				a.click();
				frappe.show_alert({ message: "Reference guide downloaded!", indicator: "green" });
			},
		});
	}

	// ═══════════════════════════════════════════════════════════
	//  CSV TEMPLATE & UPLOAD
	// ═══════════════════════════════════════════════════════════

	_download_template() {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.api_bulk_import.download_template",
			callback: (r) => {
				if (!r.message) return;
				const blob = new Blob([r.message.csv_content], { type: "text/csv" });
				const a = document.createElement("a");
				a.href = URL.createObjectURL(blob);
				a.download = r.message.filename;
				a.click();
			},
		});
	}

	_handle_file(file) {
		if (!file) return;
		if (file.size > 5 * 1024 * 1024) { frappe.msgprint("File too large. Maximum 5MB."); return; }
		if (!file.name.toLowerCase().endsWith(".csv")) { frappe.msgprint("Only .csv files accepted."); return; }
		const reader = new FileReader();
		reader.onload = (e) => this._parse_csv(e.target.result, file.name);
		reader.readAsText(file);
	}

	_parse_csv(text, filename) {
		const me = this;
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.api_bulk_import.parse_uploaded_file",
			args: { file_content: text },
			callback: (r) => {
				if (!r.message) return;
				me.parsed_rows = r.message;
				me.$page.find("#bbf-upload-info").show();
				me.$page.find("#bbf-file-name").text(filename);
				me.$page.find("#bbf-file-rows").text(me.parsed_rows.length + " rows");
				me._validate_and_preview();
			},
		});
	}

	_reset_csv() {
		this.parsed_rows = [];
		this.$page.find("#bbf-upload-info").hide();
		this.$page.find("#bbf-file-input").val("");
	}

	// ═══════════════════════════════════════════════════════════
	//  VALIDATE & PREVIEW (Phase 2)
	// ═══════════════════════════════════════════════════════════

	_validate_and_preview() {
		const me = this;
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.api_bulk_import.bulk_validate_and_preview",
			args: { rows: JSON.stringify(me.parsed_rows) },
			freeze: true,
			freeze_message: "Validating items...",
			callback: (r) => {
				if (!r.message) return;
				me.validated_results = r.message.results;
				me._render_preview(r.message);
				me.$page.find("#bbf-phase-upload").hide();
				me.$page.find("#bbf-phase-preview").show();
			},
		});
	}

	_render_preview(data) {
		this.$page.find("#bbf-total-count").text(data.total);
		this.$page.find("#bbf-valid-count").text(data.valid_count);
		this.$page.find("#bbf-error-count").text(data.error_count);
		this.$page.find("#bbf-create-count").text(data.valid_count);
		this.$page.find("#bbf-btn-create-all").prop("disabled", data.valid_count === 0);

		let html = "";
		(data.results || []).forEach(r => {
			const icon = r.valid ? '<span class="bbf-status-ok">&#10003;</span>' : '<span class="bbf-status-err">&#10007;</span>';
			const code = r.preview_code ? `<span class="bbf-code-badge">${r.preview_code}</span>` : "—";
			const variants = (r.preview_variants || []).map(v => `<span class="bbf-variant-badge">${v}</span>`).join(" ");
			const errors = (r.errors || []).map(e => `<div class="bbf-error-line">&#8226; ${e}</div>`).join("");
			html += `<tr class="${r.valid ? "" : "bbf-row-invalid"}">
				<td class="bbf-col-status">${icon} ${r.row_num}</td>
				<td>${code}</td><td>${r.item_name || "—"}</td>
				<td>${r.company || "—"}</td><td>${r.item_group || "—"}</td>
				<td>${r.stock_uom || "Kg"}</td><td>${r.gst_hsn_code || "—"}</td>
				<td>${variants || "—"}</td>
				<td>${errors || '<span style="color:#999">—</span>'}</td>
			</tr>`;
		});
		this.$page.find("#bbf-preview-body").html(html);
	}

	// ═══════════════════════════════════════════════════════════
	//  CREATE ITEMS (Phase 3)
	// ═══════════════════════════════════════════════════════════

	_create_all() {
		const valid = this.parsed_rows.filter((_, i) => this.validated_results[i] && this.validated_results[i].valid);
		if (!valid.length) return;
		frappe.confirm(`Create <b>${valid.length}</b> items? This cannot be undone.`, () => this._process_batch(valid));
	}

	_process_batch(rows) {
		const me = this;
		const BATCH = 10;
		let done = 0;
		const all_results = [];

		me.$page.find("#bbf-progress-overlay").show();
		me._update_progress(0, rows.length);

		(function next() {
			const batch = rows.slice(done, done + BATCH);
			if (!batch.length) {
				me.$page.find("#bbf-progress-overlay").hide();
				me._render_results(all_results);
				me.$page.find("#bbf-phase-preview").hide();
				me.$page.find("#bbf-phase-results").show();
				return;
			}
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.api_bulk_import.bulk_create_items",
				args: { rows: JSON.stringify(batch) },
				callback: (r) => {
					if (r.message?.results) all_results.push(...r.message.results);
					done += batch.length;
					me._update_progress(done, rows.length);
					next();
				},
				error: () => {
					batch.forEach(b => all_results.push({ row_num: b._row_num, success: false, error: "Batch failed", item_name: b.item_name }));
					done += batch.length;
					me._update_progress(done, rows.length);
					next();
				},
			});
		})();
	}

	_update_progress(done, total) {
		const pct = total ? Math.round((done / total) * 100) : 0;
		this.$page.find("#bbf-progress-bar").css("width", pct + "%");
		this.$page.find("#bbf-progress-text").text(done + " / " + total);
	}

	_render_results(results) {
		let success = 0, fail = 0, html = "";
		results.forEach(r => {
			if (r.success) {
				success++;
				const variants = (r.variant_items || []).map(v => `<a href="/app/item/${v}" class="bbf-variant-badge">${v}</a>`).join(" ");
				html += `<tr><td class="bbf-col-status"><span class="bbf-status-ok">&#10003;</span> ${r.row_num}</td>
					<td><span class="bbf-status-ok">Created</span></td>
					<td><a href="/app/item/${r.item_code}" class="bbf-code-badge">${r.item_code}</a></td>
					<td>${r.item_name || ""}</td><td>${variants || "—"}</td><td>${r.message || ""}</td></tr>`;
			} else {
				fail++;
				html += `<tr class="bbf-row-invalid"><td class="bbf-col-status"><span class="bbf-status-err">&#10007;</span> ${r.row_num}</td>
					<td><span class="bbf-status-err">Failed</span></td><td>—</td>
					<td>${r.item_name || ""}</td><td>—</td><td class="bbf-error-line">${r.error || "Unknown error"}</td></tr>`;
			}
		});
		this.$page.find("#bbf-result-success").text(success);
		this.$page.find("#bbf-result-fail").text(fail);
		this.$page.find("#bbf-results-body").html(html);
	}

	// ═══════════════════════════════════════════════════════════
	//  NAVIGATION
	// ═══════════════════════════════════════════════════════════

	_back_to_upload() {
		this.$page.find("#bbf-phase-preview").hide();
		this.$page.find("#bbf-phase-upload").show();
	}

	_reset_all() {
		this.parsed_rows = [];
		this.validated_results = [];
		this.grid_rows = [];
		this.row_counter = 0;
		this.$page.find("#bbf-grid-body").empty();
		this.$page.find("#bbf-phase-results, #bbf-phase-preview").hide();
		this.$page.find("#bbf-phase-upload").show();
		this._reset_csv();
		this._add_rows(5);
		this._set_mode("grid");
	}
}
