frappe.ui.form.on("BBF Item Creator", {
	setup(frm) {
		// Filter BBF Variant to only enabled ones
		frm.set_query("variant", function () {
			return { filters: { enabled: 1 } };
		});
	},

	refresh(frm) {
		// Lock all fields after item is created
		if (frm.doc.status === "Created") {
			_lock_all_fields(frm);
		}

		// Create Item button
		if (
			frm.doc.status === "Draft" &&
			!frm.is_new() &&
			frm.doc.generated_item_code
		) {
			frm.add_custom_button(__("Create Item"), function () {
				frappe.confirm(
					`Create Item with code <b>${frm.doc.generated_item_code}</b>?`,
					function () {
						frm.call("create_item").then(() => {
							frm.reload_doc();
						});
					}
				);
			}, null).addClass("btn-primary");
		}

		// View Item button
		if (frm.doc.item_created) {
			frm.add_custom_button(__(frm.doc.template_item ? "View Variant Item" : "View Item"), function () {
				frappe.set_route("Form", "Item", frm.doc.item_created);
			});
		}

		// View Template Item button
		if (frm.doc.template_item) {
			frm.add_custom_button(__("View Template Item"), function () {
				frappe.set_route("Form", "Item", frm.doc.template_item);
			});
		}

		// Status indicator
		if (frm.doc.status === "Created") {
			frm.page.set_indicator(__("Created"), "green");
		} else {
			frm.page.set_indicator(__("Draft"), "orange");
		}

		// Update preview on refresh
		if (!frm.is_new()) {
			_update_preview(frm);
		}
	},

	// ── Company change ──
	company(frm) {
		if (frm.doc.company) {
			_fetch_company_code(frm);
		} else {
			frm.set_value("company_code", "");
			_update_preview(frm);
		}
	},

	company_code_type(frm) {
		if (frm.doc.company) {
			_fetch_company_code(frm);
		}
	},

	// ── Category change ──
	item_group(frm) {
		if (frm.doc.item_group) {
			_fetch_category_code(frm);
		} else {
			frm.set_value("category_code", "");
			_update_preview(frm);
		}
	},

	category_code_type(frm) {
		if (frm.doc.item_group) {
			_fetch_category_code(frm);
		}
	},

	// ── Variant toggle ──
	has_variant(frm) {
		if (!frm.doc.has_variant) {
			frm.set_value("variant_source", "Brand");
			frm.set_value("brand", "");
			frm.set_value("brand_code", "");
			frm.set_value("variant", "");
			frm.set_value("variant_code", "");
		}
		_update_preview(frm);
	},

	variant_source(frm) {
		// Clear the other source's fields
		if (frm.doc.variant_source === "Brand") {
			frm.set_value("variant", "");
			frm.set_value("variant_code", "");
		} else {
			frm.set_value("brand", "");
			frm.set_value("brand_code", "");
		}
		_update_preview(frm);
	},

	brand(frm) {
		if (frm.doc.brand) {
			frappe.db.get_value("Brand", frm.doc.brand, "brand_code", (r) => {
				frm.set_value("brand_code", r ? r.brand_code : "");
				_update_preview(frm);
			});
		} else {
			frm.set_value("brand_code", "");
			_update_preview(frm);
		}
	},

	variant(frm) {
		if (frm.doc.variant) {
			frappe.db.get_value("BBF Variant", frm.doc.variant, "variant_code", (r) => {
				frm.set_value("variant_code", r ? r.variant_code : "");
				_update_preview(frm);
			});
		} else {
			frm.set_value("variant_code", "");
			_update_preview(frm);
		}
	},

	item_name(frm) {
		_update_preview(frm);
	},
});

// ── Helper: Fetch company code ──
function _fetch_company_code(frm) {
	const field =
		frm.doc.company_code_type === "Numerical"
			? "company_num_code"
			: "company_code";

	frappe.db.get_value("Company", frm.doc.company, field, (r) => {
		frm.set_value("company_code", r ? r[field] || "" : "");
		_fetch_serial_preview(frm);
	});
}

// ── Helper: Fetch category code ──
function _fetch_category_code(frm) {
	const field =
		frm.doc.category_code_type === "Numerical"
			? "category_num_code"
			: "category_code";

	frappe.db.get_value("Item Group", frm.doc.item_group, field, (r) => {
		frm.set_value("category_code", r ? r[field] || "" : "");
		_fetch_serial_preview(frm);
	});
}

// ── Helper: Fetch next serial preview ──
function _fetch_serial_preview(frm) {
	if (frm.doc.company && frm.doc.item_group && !frm.doc.serial_number) {
		frappe.call({
			method:
				"trustbit_ethanol.bbf_gate_entry.doctype.bbf_item_creator.bbf_item_creator.get_next_serial_preview",
			args: {
				company: frm.doc.company,
				item_group: frm.doc.item_group,
			},
			callback(r) {
				if (r.message) {
					// Show preview serial (not committed yet)
					frm.set_value("serial_number", r.message);
					_update_preview(frm);
				}
			},
		});
	} else {
		_update_preview(frm);
	}
}

// ── Helper: Update preview ──
function _update_preview(frm) {
	const cc = frm.doc.company_code || "";
	const cat = frm.doc.category_code || "";
	const ser = frm.doc.serial_number || "";

	if (!cc || !cat) {
		frm.set_value("generated_item_code", "");
		return;
	}

	let parts = [cc, cat, ser || "___"];

	if (frm.doc.has_variant) {
		let v_code = "";
		if (frm.doc.variant_source === "Brand") {
			v_code = frm.doc.brand_code || "";
		} else {
			v_code = frm.doc.variant_code || "";
		}
		if (v_code) {
			parts.push(v_code);
		}
	}

	// Use "-" as default separator (will be overridden by server on save)
	const code = parts.join("-");
	frm.set_value("generated_item_code", code);
}

// ── Helper: Lock all fields after creation ──
function _lock_all_fields(frm) {
	const fields_to_lock = [
		"company",
		"company_code_type",
		"item_group",
		"category_code_type",
		"has_variant",
		"variant_source",
		"brand",
		"variant",
		"item_name",
		"stock_uom",
		"description",
	];
	fields_to_lock.forEach((f) => {
		frm.set_df_property(f, "read_only", 1);
	});
}
