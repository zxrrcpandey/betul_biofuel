frappe.pages["item-creator"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Item Creator",
		single_column: true,
	});

	page.set_title_sub("Build structured item codes for ERPNext");

	// Load the HTML template
	$(frappe.render_template("item_creator")).appendTo(page.body);

	// Initialize the controller
	new TSItemCreator(page);
};

class TSItemCreator {
	constructor(page) {
		this.page = page;
		this.$page = $(page.body);
		this.current_step = 1;
		this.total_steps = 5;

		// State
		this.state = {
			company: "",
			company_code_type: "Character",
			company_code: "",
			item_group: "",
			category_code_type: "Character",
			category_code: "",
			serial_number: "",
			has_variant: false,
			variant_source: "Brand",
			// Standalone fields
			item_name: "",
			stock_uom: "Kg",
			gst_hsn_code: "",
			item_tax_template: "",
			maintain_stock: 1,
			valuation_rate: 0,
			standard_rate: 0,
			opening_stock: 0,
			opening_warehouse: "",
			description: "",
		};

		// Variant rows: [{brand, brand_code, variant, variant_code, valuation_rate, standard_rate, opening_stock, description}]
		this.variant_rows = [];
		this._variant_row_counter = 0;

		this._setup_fields();
		this._bind_events();
		this._load_recent();
	}

	// ── Helper: Monkey-patch Link controls ──
	_on_link_value(control, callback) {
		let _debounce_timer = null;
		const debounced = (val) => {
			clearTimeout(_debounce_timer);
			_debounce_timer = setTimeout(() => callback(val), 150);
		};
		const orig = control.set_formatted_input.bind(control);
		control.set_formatted_input = function (value) {
			orig(value);
			debounced(control.get_value());
		};
		control.$input.on("change", () => {
			debounced(control.get_value());
		});
	}

	// ── Setup Frappe Fields ──
	_setup_fields() {
		const me = this;

		// Company
		this.company_field = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Company", placeholder: "Select Company..." },
			parent: this.$page.find("#bbf-company-field"),
			render_input: true,
		});
		this._on_link_value(this.company_field, (val) => {
			me.state.company = val;
			me._fetch_company_code();
		});

		// Item Group
		this.category_field = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Item Group", placeholder: "Select Item Group..." },
			parent: this.$page.find("#bbf-category-field"),
			render_input: true,
		});
		this._on_link_value(this.category_field, (val) => {
			me.state.item_group = val;
			me._fetch_category_code();
		});

		// Item Name
		this.item_name_field = frappe.ui.form.make_control({
			df: { fieldtype: "Data", placeholder: "e.g. Broken Rice Grade A" },
			parent: this.$page.find("#bbf-item-name-field"),
			render_input: true,
		});
		this.item_name_field.$input.on("input change", () => {
			me.state.item_name = me.item_name_field.get_value();
		});

		// Stock UOM
		this.uom_field = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "UOM", placeholder: "Select UOM..." },
			parent: this.$page.find("#bbf-uom-field"),
			render_input: true,
		});
		this.uom_field.set_value("Kg");
		this._on_link_value(this.uom_field, (val) => {
			me.state.stock_uom = val || "Kg";
		});

		// HSN/SAC Code
		this.hsn_field = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "GST HSN Code", placeholder: "Search HSN/SAC code..." },
			parent: this.$page.find("#bbf-hsn-field"),
			render_input: true,
		});
		this._on_link_value(this.hsn_field, (val) => {
			me.state.gst_hsn_code = val;
		});

		// Item Tax Template
		this.tax_template_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "Item Tax Template",
				placeholder: "Select tax template...",
				get_query: () => ({ filters: { company: me.state.company || undefined } }),
			},
			parent: this.$page.find("#bbf-tax-template-field"),
			render_input: true,
		});
		this._on_link_value(this.tax_template_field, (val) => {
			me.state.item_tax_template = val;
		});

		// Valuation Rate (standalone)
		this.valuation_rate_field = frappe.ui.form.make_control({
			df: { fieldtype: "Currency", placeholder: "0.00" },
			parent: this.$page.find("#bbf-valuation-rate-field"),
			render_input: true,
		});
		this.valuation_rate_field.$input.on("change", () => {
			me.state.valuation_rate = flt(me.valuation_rate_field.get_value());
		});

		// Standard Selling Rate (standalone)
		this.standard_rate_field = frappe.ui.form.make_control({
			df: { fieldtype: "Currency", placeholder: "0.00" },
			parent: this.$page.find("#bbf-standard-rate-field"),
			render_input: true,
		});
		this.standard_rate_field.$input.on("change", () => {
			me.state.standard_rate = flt(me.standard_rate_field.get_value());
		});

		// Opening Stock (standalone)
		this.opening_stock_field = frappe.ui.form.make_control({
			df: { fieldtype: "Float", placeholder: "0" },
			parent: this.$page.find("#bbf-opening-stock-field"),
			render_input: true,
		});
		this.opening_stock_field.$input.on("change", () => {
			me.state.opening_stock = flt(me.opening_stock_field.get_value());
		});

		// Opening Warehouse (standalone)
		this.opening_warehouse_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "Warehouse",
				placeholder: "Select warehouse...",
				get_query: () => ({ filters: { company: me.state.company || undefined } }),
			},
			parent: this.$page.find("#bbf-opening-warehouse-field"),
			render_input: true,
		});
		this._on_link_value(this.opening_warehouse_field, (val) => {
			me.state.opening_warehouse = val;
		});

		// Opening Warehouse (variant mode — shared)
		this.opening_warehouse_variant_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "Warehouse",
				placeholder: "Select warehouse...",
				get_query: () => ({ filters: { company: me.state.company || undefined } }),
			},
			parent: this.$page.find("#bbf-opening-warehouse-variant-field"),
			render_input: true,
		});
		this._on_link_value(this.opening_warehouse_variant_field, (val) => {
			me.state.opening_warehouse = val;
		});

		// Description (standalone)
		this.desc_field = frappe.ui.form.make_control({
			df: { fieldtype: "Small Text", placeholder: "Optional description..." },
			parent: this.$page.find("#bbf-description-field"),
			render_input: true,
		});
		this.desc_field.$input.on("input change", () => {
			me.state.description = me.desc_field.get_value();
		});
	}

	// ── Bind Events ──
	_bind_events() {
		const me = this;

		// Toggle buttons (Character / Numerical)
		this.$page.find(".bbf-toggle-btn").on("click", function () {
			const $btn = $(this);
			const target = $btn.data("target");
			const value = $btn.data("value");

			$btn.siblings().removeClass("active");
			$btn.addClass("active");

			if (target === "company") {
				me.state.company_code_type = value;
				me._fetch_company_code();
			} else {
				me.state.category_code_type = value;
				me._fetch_category_code();
			}
		});

		// Has Variant toggle
		this.$page.find("#bbf-has-variant").on("change", function () {
			me.state.has_variant = $(this).is(":checked");
			me.$page.find(".bbf-variant-options").toggle(me.state.has_variant);
			me._toggle_stock_mode();
			me._update_preview();

			// Add a default empty row if none exist
			if (me.state.has_variant && me.variant_rows.length === 0) {
				me._add_variant_row();
			}
		});

		// Variant source tabs
		this.$page.find(".bbf-source-tab").on("click", function () {
			const source = $(this).data("source");
			me.state.variant_source = source;

			me.$page.find(".bbf-source-tab").removeClass("active");
			$(this).addClass("active");

			// Clear existing variant rows and add a fresh one
			me.variant_rows = [];
			me.$page.find("#bbf-variant-grid").empty();
			me._add_variant_row();
		});

		// Maintain Stock toggle
		this.$page.find("#bbf-maintain-stock").on("change", function () {
			me.state.maintain_stock = $(this).is(":checked") ? 1 : 0;
			me._toggle_stock_mode();
		});

		// Add Variant button
		this.$page.find("#bbf-add-variant").on("click", () => me._add_variant_row());

		// Navigation
		this.$page.find("#bbf-btn-next").on("click", () => me._next_step());
		this.$page.find("#bbf-btn-back").on("click", () => me._prev_step());
		this.$page.find("#bbf-btn-create").on("click", () => me._create_item());

		// Step click
		this.$page.find(".bbf-step").on("click", function () {
			const step = parseInt($(this).data("step"));
			if (step < me.current_step || me._validate_step(me.current_step)) {
				me._go_to_step(step);
			}
		});

		// Success actions
		this.$page.find("#bbf-view-item").on("click", () => {
			if (me._last_template_item) {
				frappe.set_route("Form", "Item", me._last_template_item);
			} else {
				frappe.set_route("Form", "Item", me._last_created_item);
			}
		});
		this.$page.find("#bbf-create-another").on("click", () => me._reset());
	}

	// ── Toggle stock fields based on variant mode ──
	_toggle_stock_mode() {
		const is_variant = this.state.has_variant;
		const has_stock = this.state.maintain_stock;

		if (!is_variant && has_stock) {
			this.$page.find(".bbf-standalone-stock").show();
			this.$page.find(".bbf-variant-stock").hide();
		} else if (is_variant && has_stock) {
			this.$page.find(".bbf-standalone-stock").hide();
			this.$page.find(".bbf-variant-stock").show();
		} else {
			this.$page.find(".bbf-standalone-stock").hide();
			this.$page.find(".bbf-variant-stock").hide();
		}
	}

	// ── Variant Row Management ──
	_add_variant_row() {
		const me = this;
		const idx = this._variant_row_counter++;
		const row = {
			id: idx,
			brand: "",
			brand_code: "",
			variant: "",
			variant_code: "",
			valuation_rate: 0,
			standard_rate: 0,
			opening_stock: 0,
			description: "",
		};
		this.variant_rows.push(row);

		const is_brand = this.state.variant_source === "Brand";
		const link_label = is_brand ? "Brand" : "Custom Variant";
		const $row = $(`
			<div class="bbf-variant-row-card" data-row-id="${idx}">
				<div class="bbf-variant-row-header">
					<span class="bbf-variant-row-num">${this.variant_rows.length}</span>
					<span class="bbf-variant-row-code-badge" id="bbf-vrow-code-${idx}">---</span>
					<button class="bbf-variant-row-remove" data-row-id="${idx}" title="Remove variant">
						<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
					</button>
				</div>
				<div class="bbf-variant-row-body">
					<div class="bbf-vrow-fields-grid">
						<div class="bbf-vrow-field">
							<label class="bbf-label-sm">${link_label} <span style="color:#e53e3e">*</span></label>
							<div id="bbf-vrow-link-${idx}"></div>
						</div>
						<div class="bbf-vrow-field">
							<label class="bbf-label-sm">Valuation Rate</label>
							<div id="bbf-vrow-valuation-${idx}"></div>
						</div>
						<div class="bbf-vrow-field">
							<label class="bbf-label-sm">Selling Rate</label>
							<div id="bbf-vrow-selling-${idx}"></div>
						</div>
						<div class="bbf-vrow-field">
							<label class="bbf-label-sm">Opening Stock</label>
							<div id="bbf-vrow-stock-${idx}"></div>
						</div>
					</div>
					<div class="bbf-vrow-desc">
						<label class="bbf-label-sm">Description</label>
						<div id="bbf-vrow-desc-${idx}"></div>
					</div>
				</div>
			</div>
		`);

		this.$page.find("#bbf-variant-grid").append($row);

		// Link field (Brand or Custom Variant)
		if (is_brand) {
			const brand_ctrl = frappe.ui.form.make_control({
				df: { fieldtype: "Link", options: "Brand", placeholder: "Select Brand..." },
				parent: $row.find(`#bbf-vrow-link-${idx}`),
				render_input: true,
			});
			this._on_link_value(brand_ctrl, (val) => {
				row.brand = val;
				row.variant = "";
				row.variant_code = "";
				if (val) {
					frappe.db.get_value("Brand", val, "brand_code", (r) => {
						row.brand_code = r ? r.brand_code || "" : "";
						$row.find(`#bbf-vrow-code-${idx}`).text(row.brand_code || "---");
						if (!row.brand_code) {
							me._prompt_set_code("Brand", val, "brand_code", (code) => {
								row.brand_code = code;
								$row.find(`#bbf-vrow-code-${idx}`).text(code);
								me._update_preview();
							});
						} else {
							me._update_preview();
						}
					});
				} else {
					row.brand_code = "";
					$row.find(`#bbf-vrow-code-${idx}`).text("---");
					me._update_preview();
				}
			});
			row._brand_ctrl = brand_ctrl;
		} else {
			const variant_ctrl = frappe.ui.form.make_control({
				df: {
					fieldtype: "Link",
					options: "TS Variant",
					placeholder: "Select Variant...",
					get_query: () => ({ filters: { enabled: 1 } }),
				},
				parent: $row.find(`#bbf-vrow-link-${idx}`),
				render_input: true,
			});
			this._on_link_value(variant_ctrl, (val) => {
				row.variant = val;
				row.brand = "";
				row.brand_code = "";
				if (val) {
					frappe.db.get_value("TS Variant", val, "variant_code", (r) => {
						row.variant_code = r ? r.variant_code || "" : "";
						$row.find(`#bbf-vrow-code-${idx}`).text(row.variant_code || "---");
						me._update_preview();
					});
				} else {
					row.variant_code = "";
					$row.find(`#bbf-vrow-code-${idx}`).text("---");
					me._update_preview();
				}
			});
			row._variant_ctrl = variant_ctrl;
		}

		// Valuation Rate
		const val_ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Currency", placeholder: "0.00" },
			parent: $row.find(`#bbf-vrow-valuation-${idx}`),
			render_input: true,
		});
		val_ctrl.$input.on("change", () => {
			row.valuation_rate = flt(val_ctrl.get_value());
		});

		// Standard Selling Rate
		const sell_ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Currency", placeholder: "0.00" },
			parent: $row.find(`#bbf-vrow-selling-${idx}`),
			render_input: true,
		});
		sell_ctrl.$input.on("change", () => {
			row.standard_rate = flt(sell_ctrl.get_value());
		});

		// Opening Stock
		const stock_ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Float", placeholder: "0" },
			parent: $row.find(`#bbf-vrow-stock-${idx}`),
			render_input: true,
		});
		stock_ctrl.$input.on("change", () => {
			row.opening_stock = flt(stock_ctrl.get_value());
		});

		// Description
		const desc_ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Small Text", placeholder: "Optional description..." },
			parent: $row.find(`#bbf-vrow-desc-${idx}`),
			render_input: true,
		});
		desc_ctrl.$input.on("input change", () => {
			row.description = desc_ctrl.get_value();
		});

		// Remove button
		$row.find(".bbf-variant-row-remove").on("click", function () {
			const rid = parseInt($(this).data("row-id"));
			me.variant_rows = me.variant_rows.filter((r) => r.id !== rid);
			$row.remove();
			me._renumber_variant_rows();
			me._update_preview();
		});

		this._update_preview();
	}

	_renumber_variant_rows() {
		this.$page.find(".bbf-variant-row-card").each(function (i) {
			$(this).find(".bbf-variant-row-num").text(i + 1);
		});
	}

	// ── Fetch Codes ──
	_fetch_company_code() {
		if (!this.state.company) {
			this.state.company_code = "";
			this._update_badge("#bbf-company-code", "");
			this._update_preview();
			return;
		}

		const field = this.state.company_code_type === "Numerical" ? "company_num_code" : "company_code";
		frappe.db.get_value("Company", this.state.company, field, (r) => {
			this.state.company_code = r ? r[field] || "" : "";
			this._update_badge("#bbf-company-code", this.state.company_code);
			if (!this.state.company_code) {
				this._prompt_set_code("Company", this.state.company, field, (code) => {
					this.state.company_code = code;
					this._update_badge("#bbf-company-code", code);
					this._fetch_serial_preview();
				});
			} else {
				this._fetch_serial_preview();
			}
		});
	}

	_fetch_category_code() {
		if (!this.state.item_group) {
			this.state.category_code = "";
			this._update_badge("#bbf-category-code", "");
			this._update_preview();
			return;
		}

		const field = this.state.category_code_type === "Numerical" ? "category_num_code" : "category_code";
		frappe.db.get_value("Item Group", this.state.item_group, field, (r) => {
			this.state.category_code = r ? r[field] || "" : "";
			this._update_badge("#bbf-category-code", this.state.category_code);
			if (!this.state.category_code) {
				this._prompt_set_code("Item Group", this.state.item_group, field, (code) => {
					this.state.category_code = code;
					this._update_badge("#bbf-category-code", code);
					this._fetch_serial_preview();
				});
			} else {
				this._fetch_serial_preview();
			}
		});
	}

	// ── Prompt to set missing code directly ──
	_prompt_set_code(doctype, name, fieldname, callback) {
		const is_num = fieldname.includes("num");
		const label = is_num ? "Numerical Code (e.g. 01)" : "Character Code (e.g. BBF)";

		const d = new frappe.ui.Dialog({
			title: `Set ${doctype} Code`,
			fields: [
				{
					fieldtype: "HTML",
					options: `<div style="margin-bottom:12px;padding:12px 16px;background:#ebf8ff;border-radius:8px;border:1px solid #bee3f8;color:#2b6cb0;font-size:13px;">
						<b>${name}</b> does not have a <b>${fieldname.replace(/_/g, " ")}</b> set.
						Enter one below to continue.
					</div>`,
				},
				{
					fieldname: "code_value",
					fieldtype: "Data",
					label: label,
					reqd: 1,
					description: is_num
						? "2-3 digit number (e.g. 01, 02)"
						: "2-3 letter uppercase code (e.g. BBF, RM, GRN)",
				},
			],
			primary_action_label: "Save Code",
			primary_action(values) {
				let code = values.code_value.trim().toUpperCase();
				if (!is_num && !/^[A-Z0-9]{1,5}$/.test(code)) {
					frappe.msgprint("Code must be 1-5 alphanumeric characters.");
					return;
				}
				if (is_num && !/^[0-9]{1,5}$/.test(code)) {
					frappe.msgprint("Numerical code must be 1-5 digits.");
					return;
				}

				frappe.call({
					method: "frappe.client.set_value",
					args: { doctype: doctype, name: name, fieldname: fieldname, value: code },
					callback(r) {
						if (r.message) {
							d.hide();
							frappe.show_alert({
								message: `${doctype} code set to <b>${code}</b>`,
								indicator: "green",
							});
							callback(code);
						}
					},
				});
			},
		});
		d.show();
		setTimeout(() => d.fields_dict.code_value.$input.focus(), 200);
	}

	_fetch_serial_preview() {
		if (!this.state.company || !this.state.item_group) {
			this._update_preview();
			return;
		}

		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.doctype.ts_item_creator.ts_item_creator.get_next_serial_preview",
			args: { company: this.state.company, item_group: this.state.item_group },
			callback: (r) => {
				if (r.message) {
					this.state.serial_number = r.message;
					this._update_badge("#bbf-serial-code", this.state.serial_number);
					this._update_preview();
				}
			},
		});
	}

	// ── Update Preview ──
	_update_badge(selector, value) {
		const $badge = this.$page.find(selector);
		$badge.text(value || "---");
		if (value) {
			$badge.addClass("bbf-active");
			setTimeout(() => $badge.removeClass("bbf-active"), 300);
		}
	}

	_update_preview() {
		const esc = frappe.utils.escape_html;
		const cc = this.state.company_code;
		const cat = this.state.category_code;
		const ser = this.state.serial_number;

		if (!cc || !cat) {
			this.$page.find("#bbf-live-code").html(
				'<span class="bbf-code-placeholder">Select Company & Category to begin</span>'
			);
			return;
		}

		const base = `${cc}-${cat}-${ser || "___"}`;

		if (this.state.has_variant && this.variant_rows.length > 0) {
			// Show template code + variant count
			const count = this.variant_rows.length;
			const first_code = this.variant_rows[0].brand_code || this.variant_rows[0].variant_code || "___";
			const colored = `<span style="color:#90cdf4">${esc(cc)}</span>`
				+ `<span style="color:rgba(255,255,255,0.4)">-</span>`
				+ `<span style="color:#fbd38d">${esc(cat)}</span>`
				+ `<span style="color:rgba(255,255,255,0.4)">-</span>`
				+ `<span style="color:#fff">${esc(ser || "___")}</span>`
				+ `<span style="color:rgba(255,255,255,0.4)">-</span>`
				+ `<span style="color:#9ae6b4">${esc(first_code)}</span>`
				+ (count > 1 ? `<span style="color:rgba(255,255,255,0.5);font-size:16px;margin-left:8px;">+${count - 1} more</span>` : "");

			this.$page.find("#bbf-live-code").html(colored);
		} else {
			const colored = `<span style="color:#90cdf4">${esc(cc)}</span>`
				+ `<span style="color:rgba(255,255,255,0.4)">-</span>`
				+ `<span style="color:#fbd38d">${esc(cat)}</span>`
				+ `<span style="color:rgba(255,255,255,0.4)">-</span>`
				+ `<span style="color:#fff">${esc(ser || "___")}</span>`;

			this.$page.find("#bbf-live-code").html(colored);
		}
	}

	// ── Step Navigation ──
	_go_to_step(step) {
		this.current_step = step;

		// Update step indicators
		this.$page.find(".bbf-step").each(function () {
			const s = parseInt($(this).data("step"));
			$(this).toggleClass("active", s === step);
			$(this).toggleClass("done", s < step);
		});

		// Update step lines
		this.$page.find(".bbf-step-line").each(function (i) {
			$(this).toggleClass("done", i < step - 1);
		});

		// Show/hide content
		this.$page.find(".bbf-step-content").removeClass("active");
		this.$page.find(`.bbf-step-content[data-step="${step}"]`).addClass("active");

		// Show/hide buttons
		this.$page.find("#bbf-btn-back").toggle(step > 1);
		this.$page.find("#bbf-btn-next").toggle(step < this.total_steps);
		this.$page.find("#bbf-btn-create").toggle(step === this.total_steps);

		// Step 4: toggle stock/variant mode (use setTimeout to ensure DOM is visible first)
		if (step === 4) {
			setTimeout(() => this._toggle_stock_mode(), 0);
		}

		// Populate review if on step 5
		if (step === 5) {
			this._populate_review();
		}
	}

	_next_step() {
		if (this._validate_step(this.current_step)) {
			this._go_to_step(this.current_step + 1);
		}
	}

	_prev_step() {
		if (this.current_step > 1) {
			this._go_to_step(this.current_step - 1);
		}
	}

	_validate_step(step) {
		if (step === 1) {
			if (!this.state.company) {
				frappe.show_alert({ message: "Please select a Company", indicator: "orange" });
				return false;
			}
			if (!this.state.company_code) {
				frappe.show_alert({ message: "Company code not found. Set it on the Company master.", indicator: "red" });
				return false;
			}
			if (!this.state.item_group) {
				frappe.show_alert({ message: "Please select an Item Group", indicator: "orange" });
				return false;
			}
			if (!this.state.category_code) {
				frappe.show_alert({ message: "Category code not found. Set it on the Item Group master.", indicator: "red" });
				return false;
			}
		}

		if (step === 2) {
			if (!this.state.stock_uom) {
				frappe.show_alert({ message: "Please select Stock UOM", indicator: "orange" });
				return false;
			}
			if (!this.state.gst_hsn_code) {
				frappe.show_alert({ message: "Please select HSN/SAC Code (required for GST)", indicator: "orange" });
				return false;
			}
		}

		if (step === 3 && this.state.has_variant) {
			if (this.variant_rows.length === 0) {
				frappe.show_alert({ message: "Please add at least one variant", indicator: "orange" });
				return false;
			}
			for (let i = 0; i < this.variant_rows.length; i++) {
				const row = this.variant_rows[i];
				const v_code = row.brand_code || row.variant_code;
				if (!v_code) {
					frappe.show_alert({
						message: `Variant #${i + 1}: Please select a ${this.state.variant_source === "Brand" ? "Brand" : "Custom Variant"} with a valid code`,
						indicator: "orange"
					});
					return false;
				}
			}
		}

		if (step === 4) {
			if (!this.state.has_variant) {
				// Standalone: check opening warehouse if opening stock set
				if (this.state.maintain_stock && flt(this.state.opening_stock) > 0 && !this.state.opening_warehouse) {
					frappe.show_alert({ message: "Please select Opening Warehouse for the opening stock", indicator: "orange" });
					return false;
				}
			} else {
				// Variant: check opening warehouse if any variant has opening stock
				const has_opening = this.variant_rows.some((r) => flt(r.opening_stock) > 0);
				if (this.state.maintain_stock && has_opening && !this.state.opening_warehouse) {
					frappe.show_alert({ message: "Please select Opening Warehouse (some variants have opening stock)", indicator: "orange" });
					return false;
				}
			}
		}

		return true;
	}

	// ── Review ──
	_populate_review() {
		const s = this.state;
		const base_code = [s.company_code, s.category_code, s.serial_number || "___"].join("-");

		this.$page.find("#bbf-review-company").text(s.company + " (" + s.company_code + ")");
		this.$page.find("#bbf-review-category").text(s.item_group + " (" + s.category_code + ")");
		this.$page.find("#bbf-review-serial").text(s.serial_number || "Auto-assigned");
		this.$page.find("#bbf-review-name").text(s.item_name || base_code);
		this.$page.find("#bbf-review-uom").text(s.stock_uom);
		this.$page.find("#bbf-review-hsn").text(s.gst_hsn_code || "-");
		this.$page.find("#bbf-review-tax").text(s.item_tax_template || "-");
		this.$page.find("#bbf-review-maintain-stock").text(s.maintain_stock ? "Yes" : "No");

		if (s.has_variant && this.variant_rows.length > 0) {
			// Multi-variant review
			this.$page.find("#bbf-review-code").text(base_code + " (Template)");
			this.$page.find("#bbf-review-type").text(`Template + ${this.variant_rows.length} Variant(s)`);

			// Hide standalone fields
			this.$page.find(".bbf-review-standalone-row").hide();
			this.$page.find(".bbf-review-opening-row").hide();

			// Build variants table
			const $tbody = this.$page.find("#bbf-review-variants-body").empty();
			const esc_html = frappe.utils.escape_html;
			this.variant_rows.forEach((row) => {
				const v_code = row.brand_code || row.variant_code;
				const v_name = row.brand || row.variant || "-";
				const full_code = base_code + "-" + v_code;
				$tbody.append(`
					<tr>
						<td><code>${esc_html(full_code)}</code></td>
						<td>${esc_html(v_name)}</td>
						<td>${row.valuation_rate ? format_currency(row.valuation_rate) : "-"}</td>
						<td>${row.standard_rate ? format_currency(row.standard_rate) : "-"}</td>
						<td>${flt(row.opening_stock) || "-"}</td>
					</tr>
				`);
			});

			this.$page.find("#bbf-review-variants").show();
			this.$page.find("#bbf-review-note").show();
			this.$page.find("#bbf-review-note-text").html(
				`This will create <b>1 Template</b> + <b>${this.variant_rows.length} Variant(s)</b> in ERPNext.`
			);
		} else {
			// Standalone review
			this.$page.find("#bbf-review-code").text(base_code);
			this.$page.find("#bbf-review-type").text("Standalone Item");

			this.$page.find(".bbf-review-standalone-row").show();
			this.$page.find("#bbf-review-valuation").text(s.valuation_rate ? format_currency(s.valuation_rate) : "-");
			this.$page.find("#bbf-review-selling-rate").text(s.standard_rate ? format_currency(s.standard_rate) : "-");

			if (s.maintain_stock && flt(s.opening_stock) > 0) {
				this.$page.find("#bbf-review-opening").text(
					s.opening_stock + " @ " + (s.opening_warehouse || "-")
				);
				this.$page.find(".bbf-review-opening-row").show();
			} else {
				this.$page.find(".bbf-review-opening-row").hide();
			}

			this.$page.find("#bbf-review-variants").hide();
			this.$page.find("#bbf-review-note").hide();
		}
	}

	// ── Create Item ──
	_create_item() {
		const me = this;
		const s = me.state;
		const $btn = this.$page.find("#bbf-btn-create");

		// Re-read current field values (prefer live values over stale state)
		s.company = me.company_field.get_value() || s.company;
		s.item_group = me.category_field.get_value() || s.item_group;
		s.stock_uom = me.uom_field.get_value() || "Kg";
		s.item_name = me.item_name_field.get_value() || s.item_name;
		s.gst_hsn_code = me.hsn_field.get_value() || s.gst_hsn_code;
		s.item_tax_template = me.tax_template_field.get_value() || "";
		s.description = me.desc_field.get_value() || "";

		if (!s.has_variant) {
			s.valuation_rate = flt(me.valuation_rate_field.get_value());
			s.standard_rate = flt(me.standard_rate_field.get_value());
			s.opening_stock = flt(me.opening_stock_field.get_value());
			s.opening_warehouse = me.opening_warehouse_field.get_value() || "";
		} else {
			s.opening_warehouse = me.opening_warehouse_variant_field.get_value() || s.opening_warehouse;
		}

		if (!s.company || !s.item_group) {
			frappe.show_alert({ message: "Company or Item Group is missing.", indicator: "red" });
			return;
		}

		$btn.prop("disabled", true).text("Creating...");

		// Build the doc payload
		const doc = {
			doctype: "TS Item Creator",
			company: s.company,
			company_code_type: s.company_code_type,
			company_code: s.company_code,
			item_group: s.item_group,
			category_code_type: s.category_code_type,
			category_code: s.category_code,
			has_variant: s.has_variant ? 1 : 0,
			variant_source: s.variant_source,
			item_name: s.item_name,
			stock_uom: s.stock_uom,
			gst_hsn_code: s.gst_hsn_code,
			item_tax_template: s.item_tax_template,
			maintain_stock: s.maintain_stock,
			opening_warehouse: s.opening_warehouse,
			description: s.description,
		};

		if (s.has_variant && me.variant_rows.length > 0) {
			// Multi-variant mode: pass variant rows as child table
			doc.variants = me.variant_rows.map((row) => ({
				brand: row.brand || "",
				brand_code: row.brand_code || "",
				variant: row.variant || "",
				variant_code: row.variant_code || "",
				valuation_rate: row.valuation_rate || 0,
				standard_rate: row.standard_rate || 0,
				opening_stock: row.opening_stock || 0,
				description: row.description || "",
			}));
		} else {
			// Standalone mode
			doc.valuation_rate = s.valuation_rate;
			doc.standard_rate = s.standard_rate;
			doc.opening_stock = s.opening_stock;
		}

		frappe.call({
			method: "frappe.client.insert",
			args: { doc: doc },
			callback(r) {
				if (r.message) {
					frappe.call({
						method: "run_doc_method",
						args: {
							dt: "TS Item Creator",
							dn: r.message.name,
							method: "create_item",
						},
						callback(res) {
							me._reset_btn($btn);
							if (res.message) {
								me._last_created_item = res.message.item_code;
								me._last_template_item = res.message.template_code || "";
								me._show_success(res.message);
							}
						},
						error() {
							me._reset_btn($btn);
						},
					});
				}
			},
			error() {
				me._reset_btn($btn);
			},
		});
	}

	_reset_btn($btn) {
		$btn.prop("disabled", false).html(
			'<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8l3.5 4L13 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg> Create Item'
		);
	}

	// ── Success ──
	_show_success(result) {
		this.$page.find(".bbf-card, .bbf-preview-bar").hide();
		this.$page.find("#bbf-success").show();

		if (result.variant_items && result.variant_items.length > 0) {
			// Multi-variant success
			this.$page.find("#bbf-success-title").text("Items Created Successfully!");
			this.$page.find("#bbf-success-code").text(result.template_code + " (Template)");

			const $list = this.$page.find("#bbf-success-variant-list").empty().show();
			result.variant_items.forEach((code) => {
				$list.append(`<div class="bbf-success-variant-item">
					<a href="/app/item/${encodeURIComponent(code)}">${code}</a>
				</div>`);
			});
		} else {
			this.$page.find("#bbf-success-title").text("Item Created Successfully!");
			this.$page.find("#bbf-success-code").text(result.item_code);
			this.$page.find("#bbf-success-variant-list").hide();
		}

		if (result.template_code) {
			this.$page.find("#bbf-success-template").show();
			this.$page.find("#bbf-success-template-link")
				.text(result.template_code)
				.attr("href", `/app/item/${result.template_code}`);
		} else {
			this.$page.find("#bbf-success-template").hide();
		}

		this._load_recent();
	}

	// ── Reset ──
	_reset() {
		this.state = {
			company: "", company_code_type: "Character", company_code: "",
			item_group: "", category_code_type: "Character", category_code: "",
			serial_number: "",
			has_variant: false, variant_source: "Brand",
			item_name: "", stock_uom: "Kg", gst_hsn_code: "", item_tax_template: "",
			maintain_stock: 1, valuation_rate: 0, standard_rate: 0,
			opening_stock: 0, opening_warehouse: "",
			description: "",
		};

		this.variant_rows = [];
		this._variant_row_counter = 0;

		// Reset fields
		this.company_field.set_value("");
		this.category_field.set_value("");
		this.item_name_field.set_value("");
		this.uom_field.set_value("Kg");
		this.hsn_field.set_value("");
		this.tax_template_field.set_value("");
		this.valuation_rate_field.set_value("");
		this.standard_rate_field.set_value("");
		this.opening_stock_field.set_value("");
		this.opening_warehouse_field.set_value("");
		this.opening_warehouse_variant_field.set_value("");
		this.desc_field.set_value("");

		// Reset UI
		this.$page.find("#bbf-has-variant").prop("checked", false);
		this.$page.find("#bbf-maintain-stock").prop("checked", true);
		this.$page.find(".bbf-variant-options").hide();
		this.$page.find(".bbf-standalone-stock").show();
		this.$page.find(".bbf-variant-stock").hide();
		this.$page.find("#bbf-variant-grid").empty();
		this.$page.find(".bbf-toggle-btn[data-value='Character']").addClass("active")
			.siblings().removeClass("active");
		this.$page.find(".bbf-source-tab[data-source='Brand']").addClass("active")
			.siblings().removeClass("active");
		this.$page.find(".bbf-code-badge").text("---");
		this.$page.find("#bbf-live-code").html('<span class="bbf-code-placeholder">Select Company & Category to begin</span>');

		// Show form, hide success
		this.$page.find(".bbf-card, .bbf-preview-bar").show();
		this.$page.find("#bbf-success").hide();

		this._go_to_step(1);
	}

	// ── Load Recent Items ──
	_load_recent() {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "TS Item Creator",
				fields: ["name", "generated_item_code", "item_name", "status", "item_created"],
				order_by: "creation desc",
				limit_page_length: 6,
			},
			callback: (r) => {
				const $list = this.$page.find("#bbf-recent-list").empty();
				if (!r.message || !r.message.length) {
					this.$page.find("#bbf-recent").hide();
					return;
				}

				this.$page.find("#bbf-recent").show();
				r.message.forEach((item) => {
					const status_class = item.status === "Created" ? "status-created" : "status-draft";
					const _esc = frappe.utils.escape_html;
					const $el = $(`
						<div class="bbf-recent-item">
							<div>
								<div class="bbf-recent-item-code">${_esc(item.generated_item_code || item.name)}</div>
								<div class="bbf-recent-item-name">${_esc(item.item_name || "")}</div>
							</div>
							<span class="bbf-recent-item-status ${status_class}">${_esc(item.status)}</span>
						</div>
					`);

					$el.on("click", () => {
						if (item.item_created && item.item_created.trim()) {
							const first_item = item.item_created.split(",")[0].trim();
							frappe.set_route("Form", "Item", first_item);
						} else {
							frappe.set_route("Form", "TS Item Creator", item.name);
						}
					});

					$list.append($el);
				});
			},
		});
	}
}
