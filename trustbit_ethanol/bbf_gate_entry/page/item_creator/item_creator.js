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
	new BBFItemCreator(page);
};

class BBFItemCreator {
	constructor(page) {
		this.page = page;
		this.$page = $(page.body);
		this.current_step = 1;
		this.total_steps = 4;

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
			brand: "",
			brand_code: "",
			variant: "",
			variant_code: "",
			item_name: "",
			stock_uom: "Kg",
			description: "",
		};

		this._setup_fields();
		this._bind_events();
		this._load_recent();
	}

	// ── Setup Frappe Fields ──
	_setup_fields() {
		const me = this;

		// Helper to bind Link field value changes properly
		function bind_link_change(control, callback) {
			let _last_val = "";
			// Frappe Link fields need awesomplete-selectcomplete for proper detection
			control.$input.on("awesomplete-selectcomplete", () => {
				setTimeout(() => {
					_last_val = control.get_value();
					callback(_last_val);
				}, 0);
			});
			control.$input.on("change", () => {
				setTimeout(() => {
					const v = control.get_value();
					if (v !== _last_val) {
						_last_val = v;
						callback(v);
					}
				}, 0);
			});
			// Catch clear via blur — only fire if value actually changed
			control.$input.on("blur", () => {
				setTimeout(() => {
					const v = control.get_value();
					if (v !== _last_val) {
						_last_val = v;
						callback(v);
					}
				}, 200);
			});
		}

		// Company
		this.company_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "Company",
				placeholder: "Select Company...",
				change: () => {
					me.state.company = me.company_field.get_value();
					me._fetch_company_code();
				},
			},
			parent: this.$page.find("#bbf-company-field"),
			render_input: true,
		});
		bind_link_change(this.company_field, (val) => {
			me.state.company = val;
			me._fetch_company_code();
		});

		// Item Group
		this.category_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "Item Group",
				placeholder: "Select Item Group...",
				change: () => {
					me.state.item_group = me.category_field.get_value();
					me._fetch_category_code();
				},
			},
			parent: this.$page.find("#bbf-category-field"),
			render_input: true,
		});
		bind_link_change(this.category_field, (val) => {
			me.state.item_group = val;
			me._fetch_category_code();
		});

		// Brand
		this.brand_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "Brand",
				placeholder: "Select Brand...",
				change: () => me._on_brand_change(),
			},
			parent: this.$page.find("#bbf-brand-field"),
			render_input: true,
		});
		bind_link_change(this.brand_field, () => me._on_brand_change());

		// Custom Variant
		this.variant_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "BBF Variant",
				placeholder: "Select Variant...",
				get_query: () => ({ filters: { enabled: 1 } }),
				change: () => me._on_variant_change(),
			},
			parent: this.$page.find("#bbf-variant-field"),
			render_input: true,
		});
		bind_link_change(this.variant_field, () => me._on_variant_change());

		// Item Name
		this.item_name_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Data",
				placeholder: "e.g. Broken Rice Grade A",
				change: () => {
					me.state.item_name = me.item_name_field.get_value();
				},
			},
			parent: this.$page.find("#bbf-item-name-field"),
			render_input: true,
		});

		// Stock UOM
		this.uom_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				options: "UOM",
				placeholder: "Select UOM...",
				default: "Kg",
				change: () => {
					me.state.stock_uom = me.uom_field.get_value();
				},
			},
			parent: this.$page.find("#bbf-uom-field"),
			render_input: true,
		});
		this.uom_field.set_value("Kg");
		bind_link_change(this.uom_field, (val) => {
			me.state.stock_uom = val;
		});

		// Description
		this.desc_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Small Text",
				placeholder: "Optional description...",
				change: () => {
					me.state.description = me.desc_field.get_value();
				},
			},
			parent: this.$page.find("#bbf-description-field"),
			render_input: true,
		});
	}

	_on_brand_change() {
		const val = this.brand_field.get_value();
		this.state.brand = val;
		if (val) {
			frappe.db.get_value("Brand", val, "brand_code", (r) => {
				this.state.brand_code = r ? r.brand_code || "" : "";
				this._update_badge("#bbf-brand-code", this.state.brand_code);
				this._update_preview();
			});
		} else {
			this.state.brand_code = "";
			this._update_badge("#bbf-brand-code", "");
			this._update_preview();
		}
	}

	_on_variant_change() {
		const val = this.variant_field.get_value();
		this.state.variant = val;
		if (val) {
			frappe.db.get_value("BBF Variant", val, "variant_code", (r) => {
				this.state.variant_code = r ? r.variant_code || "" : "";
				this._update_badge("#bbf-variant-code", this.state.variant_code);
				this._update_preview();
			});
		} else {
			this.state.variant_code = "";
			this._update_badge("#bbf-variant-code", "");
			this._update_preview();
		}
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
			me._update_preview();
		});

		// Variant source tabs
		this.$page.find(".bbf-source-tab").on("click", function () {
			const source = $(this).data("source");
			me.state.variant_source = source;

			me.$page.find(".bbf-source-tab").removeClass("active");
			$(this).addClass("active");

			me.$page.find(".bbf-source-panel").removeClass("active");
			me.$page.find(`.bbf-source-panel[data-source="${source}"]`).addClass("active");

			me._update_preview();
		});

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
			frappe.set_route("Form", "Item", me._last_created_item);
		});
		this.$page.find("#bbf-create-another").on("click", () => me._reset());
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
			this._fetch_serial_preview();
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
			this._fetch_serial_preview();
		});
	}

	_fetch_serial_preview() {
		if (!this.state.company || !this.state.item_group) {
			this._update_preview();
			return;
		}

		frappe.call({
			method: "trustbit_ethanol.bbf_gate_entry.doctype.bbf_item_creator.bbf_item_creator.get_next_serial_preview",
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
		const cc = this.state.company_code;
		const cat = this.state.category_code;
		const ser = this.state.serial_number;

		if (!cc || !cat) {
			this.$page.find("#bbf-live-code").html(
				'<span class="bbf-code-placeholder">Select Company & Category to begin</span>'
			);
			return;
		}

		let parts = [cc, cat, ser || "___"];

		if (this.state.has_variant) {
			let v_code = "";
			if (this.state.variant_source === "Brand") {
				v_code = this.state.brand_code;
			} else {
				v_code = this.state.variant_code;
			}
			if (v_code) {
				parts.push(v_code);
			} else {
				parts.push("___");
			}
		}

		const code = parts.join("-");

		// Color-coded parts
		const colored = `<span style="color:#90cdf4">${cc}</span>`
			+ `<span style="color:rgba(255,255,255,0.4)">-</span>`
			+ `<span style="color:#fbd38d">${cat}</span>`
			+ `<span style="color:rgba(255,255,255,0.4)">-</span>`
			+ `<span style="color:#fff">${ser || "___"}</span>`
			+ (this.state.has_variant
				? `<span style="color:rgba(255,255,255,0.4)">-</span><span style="color:#9ae6b4">${parts[3] || "___"}</span>`
				: "");

		this.$page.find("#bbf-live-code").html(colored);
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

		// Populate review if on step 4
		if (step === 4) {
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

		if (step === 2 && this.state.has_variant) {
			if (this.state.variant_source === "Brand" && !this.state.brand) {
				frappe.show_alert({ message: "Please select a Brand or disable variant", indicator: "orange" });
				return false;
			}
			if (this.state.variant_source === "Brand" && this.state.brand && !this.state.brand_code) {
				frappe.show_alert({ message: "Brand code not found. Set brand_code on the Brand master.", indicator: "red" });
				return false;
			}
			if (this.state.variant_source === "Custom Variant" && !this.state.variant) {
				frappe.show_alert({ message: "Please select a Custom Variant or disable variant", indicator: "orange" });
				return false;
			}
		}

		if (step === 3) {
			if (!this.state.stock_uom) {
				frappe.show_alert({ message: "Please select Stock UOM", indicator: "orange" });
				return false;
			}
		}

		return true;
	}

	// ── Review ──
	_populate_review() {
		const s = this.state;
		const v_code = s.variant_source === "Brand" ? s.brand_code : s.variant_code;

		let parts = [s.company_code, s.category_code, s.serial_number || "___"];
		if (s.has_variant && v_code) parts.push(v_code);
		const full_code = parts.join("-");

		this.$page.find("#bbf-review-code").text(full_code);
		this.$page.find("#bbf-review-company").text(s.company + " (" + s.company_code + ")");
		this.$page.find("#bbf-review-category").text(s.item_group + " (" + s.category_code + ")");
		this.$page.find("#bbf-review-serial").text(s.serial_number || "Auto-assigned");
		this.$page.find("#bbf-review-name").text(s.item_name || full_code);
		this.$page.find("#bbf-review-uom").text(s.stock_uom);

		if (s.has_variant) {
			const variant_label = s.variant_source === "Brand"
				? s.brand + " (" + s.brand_code + ")"
				: s.variant + " (" + s.variant_code + ")";
			this.$page.find("#bbf-review-variant").text(variant_label);
			this.$page.find(".bbf-review-variant-row").show();
			this.$page.find("#bbf-review-type").text("Template + Variant");
			this.$page.find("#bbf-review-note").show();
		} else {
			this.$page.find(".bbf-review-variant-row").hide();
			this.$page.find("#bbf-review-type").text("Standalone Item");
			this.$page.find("#bbf-review-note").hide();
		}
	}

	// ── Create Item ──
	_create_item() {
		const me = this;
		const s = me.state;
		const $btn = this.$page.find("#bbf-btn-create");

		// Re-read field values to be safe (blur events may have lagged)
		s.company = me.company_field.get_value() || s.company;
		s.item_group = me.category_field.get_value() || s.item_group;
		s.stock_uom = me.uom_field.get_value() || s.stock_uom || "Kg";
		s.item_name = me.item_name_field.get_value() || s.item_name;
		s.description = me.desc_field.get_value() || s.description;
		if (s.has_variant) {
			if (s.variant_source === "Brand") {
				s.brand = me.brand_field.get_value() || s.brand;
			} else {
				s.variant = me.variant_field.get_value() || s.variant;
			}
		}

		if (!s.company || !s.item_group) {
			frappe.show_alert({ message: "Company or Item Group is missing. Please go back and select.", indicator: "red" });
			return;
		}

		$btn.prop("disabled", true).text("Creating...");

		// First create the BBF Item Creator doc, then call create_item on it
		frappe.call({
			method: "frappe.client.insert",
			args: {
				doc: {
					doctype: "BBF Item Creator",
					company: s.company,
					company_code_type: s.company_code_type,
					item_group: s.item_group,
					category_code_type: s.category_code_type,
					has_variant: s.has_variant ? 1 : 0,
					variant_source: s.variant_source,
					brand: s.brand,
					variant: s.variant,
					item_name: s.item_name,
					stock_uom: s.stock_uom,
					description: s.description,
				},
			},
			callback(r) {
				if (r.message) {
					// Now call create_item on the saved doc
					frappe.xcall(
						"frappe.client.get_list",
						{ doctype: "BBF Item Creator", filters: { name: r.message.name }, fields: ["name"] }
					).then(() => {
						frappe.call({
							method: "run_doc_method",
							args: {
								dt: "BBF Item Creator",
								dn: r.message.name,
								method: "create_item",
							},
							callback(res) {
								$btn.prop("disabled", false).html(
									'<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8l3.5 4L13 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg> Create Item'
								);

								if (res.message) {
									me._last_created_item = res.message.item_code;
									me._last_template_item = res.message.template_code || "";
									me._show_success(res.message);
								}
							},
							error() {
								$btn.prop("disabled", false).html(
									'<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8l3.5 4L13 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg> Create Item'
								);
							},
						});
					});
				}
			},
			error() {
				$btn.prop("disabled", false).html(
					'<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8l3.5 4L13 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg> Create Item'
				);
			},
		});
	}

	// ── Success ──
	_show_success(result) {
		this.$page.find(".bbf-card, .bbf-preview-bar").hide();
		this.$page.find("#bbf-success").show();
		this.$page.find("#bbf-success-code").text(result.item_code);

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
		// Reset state
		this.state = {
			company: "", company_code_type: "Character", company_code: "",
			item_group: "", category_code_type: "Character", category_code: "",
			serial_number: "",
			has_variant: false, variant_source: "Brand",
			brand: "", brand_code: "", variant: "", variant_code: "",
			item_name: "", stock_uom: "Kg", description: "",
		};

		// Reset fields
		this.company_field.set_value("");
		this.category_field.set_value("");
		this.brand_field.set_value("");
		this.variant_field.set_value("");
		this.item_name_field.set_value("");
		this.uom_field.set_value("Kg");
		this.desc_field.set_value("");

		// Reset UI
		this.$page.find("#bbf-has-variant").prop("checked", false);
		this.$page.find(".bbf-variant-options").hide();
		this.$page.find(".bbf-toggle-btn[data-value='Character']").addClass("active")
			.siblings().removeClass("active");
		this.$page.find(".bbf-code-badge").text("---");
		this.$page.find("#bbf-live-code").html('<span class="bbf-code-placeholder">Select Company & Category to begin</span>');

		// Show form, hide success
		this.$page.find(".bbf-card, .bbf-preview-bar").show();
		this.$page.find("#bbf-success").hide();

		// Go to step 1
		this._go_to_step(1);
	}

	// ── Load Recent Items ──
	_load_recent() {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "BBF Item Creator",
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
					const $el = $(`
						<div class="bbf-recent-item">
							<div>
								<div class="bbf-recent-item-code">${item.generated_item_code || item.name}</div>
								<div class="bbf-recent-item-name">${item.item_name || ""}</div>
							</div>
							<span class="bbf-recent-item-status ${status_class}">${item.status}</span>
						</div>
					`);

					$el.on("click", () => {
						if (item.item_created) {
							frappe.set_route("Form", "Item", item.item_created);
						} else {
							frappe.set_route("Form", "BBF Item Creator", item.name);
						}
					});

					$list.append($el);
				});
			},
		});
	}
}
