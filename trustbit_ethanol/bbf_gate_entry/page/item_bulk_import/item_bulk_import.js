frappe.pages["item-bulk-import"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Bulk Item Import",
		single_column: true,
	});
	page.main.html(frappe.render_template("item_bulk_import"));
	new BBFBulkImport(page);
};

class BBFBulkImport {
	constructor(page) {
		this.page = page;
		this.$page = $(page.main);
		this.parsed_rows = [];
		this.validated_results = [];
		this._bind_events();
	}

	// ─── Event Bindings ───────────────────────────

	_bind_events() {
		var me = this;

		// Download template
		me.$page.find("#bbf-download-template").on("click", function () {
			me._download_template();
		});

		// Upload zone click
		me.$page.find("#bbf-upload-zone").on("click", function () {
			me.$page.find("#bbf-file-input").trigger("click");
		});

		// Drag and drop
		var $zone = me.$page.find("#bbf-upload-zone");
		$zone.on("dragover", function (e) {
			e.preventDefault();
			e.stopPropagation();
			$(this).addClass("dragover");
		});
		$zone.on("dragleave drop", function (e) {
			e.preventDefault();
			e.stopPropagation();
			$(this).removeClass("dragover");
		});
		$zone.on("drop", function (e) {
			var files = e.originalEvent.dataTransfer.files;
			if (files.length) me._handle_file(files[0]);
		});

		// File input
		me.$page.find("#bbf-file-input").on("change", function () {
			if (this.files.length) me._handle_file(this.files[0]);
		});

		// Remove file
		me.$page.find("#bbf-file-remove").on("click", function () {
			me._reset_to_upload();
		});

		// Back button
		me.$page.find("#bbf-btn-back-upload").on("click", function () {
			me._reset_to_upload();
		});

		// Create All
		me.$page.find("#bbf-btn-create-all").on("click", function () {
			me._create_all();
		});

		// Import Another
		me.$page.find("#bbf-btn-import-another").on("click", function () {
			me._reset_to_upload();
		});
	}

	// ─── Template Download ────────────────────────

	_download_template() {
		frappe.call({
			method: "trustbit_ethanol.bbf_gate_entry.api_bulk_import.download_template",
			callback: function (r) {
				if (r.message) {
					var blob = new Blob([r.message.csv_content], { type: "text/csv" });
					var url = URL.createObjectURL(blob);
					var a = document.createElement("a");
					a.href = url;
					a.download = r.message.filename;
					document.body.appendChild(a);
					a.click();
					document.body.removeChild(a);
					URL.revokeObjectURL(url);
				}
			},
		});
	}

	// ─── File Handling ────────────────────────────

	_handle_file(file) {
		var me = this;

		if (!file.name.toLowerCase().endsWith(".csv")) {
			frappe.msgprint("Please upload a CSV file.", "Invalid File");
			return;
		}

		if (file.size > 5 * 1024 * 1024) {
			frappe.msgprint("File too large. Maximum 5MB.", "File Too Large");
			return;
		}

		var reader = new FileReader();
		reader.onload = function (e) {
			me._parse_csv(e.target.result, file.name);
		};
		reader.readAsText(file);
	}

	_parse_csv(text, filename) {
		var me = this;

		frappe.call({
			method: "trustbit_ethanol.bbf_gate_entry.api_bulk_import.parse_uploaded_file",
			args: { file_content: text, file_type: "csv" },
			freeze: true,
			freeze_message: "Parsing file...",
			callback: function (r) {
				if (r.message) {
					me.parsed_rows = r.message;

					if (me.parsed_rows.length === 0) {
						frappe.msgprint("No data rows found in the CSV file.", "Empty File");
						return;
					}

					if (me.parsed_rows.length > 500) {
						frappe.msgprint("Maximum 500 rows allowed. Your file has " + me.parsed_rows.length + " rows.", "Too Many Rows");
						return;
					}

					// Show file badge
					me.$page.find("#bbf-file-name").text(filename);
					me.$page.find("#bbf-file-rows").text(me.parsed_rows.length + " rows");
					me.$page.find("#bbf-upload-info").show();

					// Run validation
					me._validate_and_preview();
				}
			},
		});
	}

	// ─── Validate & Preview ───────────────────────

	_validate_and_preview() {
		var me = this;

		frappe.call({
			method: "trustbit_ethanol.bbf_gate_entry.api_bulk_import.bulk_validate_and_preview",
			args: { rows: me.parsed_rows },
			freeze: true,
			freeze_message: "Validating rows...",
			callback: function (r) {
				if (r.message) {
					me.validated_results = r.message.results;
					me._render_preview(r.message);
				}
			},
		});
	}

	_render_preview(data) {
		var me = this;

		// Update summary
		me.$page.find("#bbf-total-count").text(data.total);
		me.$page.find("#bbf-valid-count").text(data.valid_count);
		me.$page.find("#bbf-error-count").text(data.error_count);

		// Build table rows
		var $tbody = me.$page.find("#bbf-preview-body");
		$tbody.empty();

		data.results.forEach(function (row) {
			var status_html = row.valid
				? '<span class="bbf-status-icon bbf-status-ok">&#10003;</span>'
				: '<span class="bbf-status-icon bbf-status-err">&#10007;</span>';

			var code_html = row.preview_code
				? '<code class="bbf-code-preview">' + frappe.utils.escape_html(row.preview_code) + "</code>"
				: "-";

			var variant_html = "";
			if (row.has_variant && row.variant_codes.length) {
				variant_html = row.variant_codes
					.map(function (vc) {
						return '<span class="bbf-variant-badge">' + frappe.utils.escape_html(vc) + "</span>";
					})
					.join(" ");
			} else {
				variant_html = '<span style="color:#a0aec0;">-</span>';
			}

			var errors_html = "";
			if (row.errors.length) {
				errors_html = '<ul class="bbf-error-list">';
				row.errors.forEach(function (err) {
					errors_html += "<li>" + frappe.utils.escape_html(err) + "</li>";
				});
				errors_html += "</ul>";
			}

			var tr_class = row.valid ? "" : " class='bbf-row-error'";
			$tbody.append(
				"<tr" + tr_class + ">" +
					"<td class='bbf-col-status'>" + status_html + "</td>" +
					"<td>" + code_html + "</td>" +
					"<td>" + frappe.utils.escape_html(row.item_name || "-") + "</td>" +
					"<td>" + frappe.utils.escape_html(row.company || "-") + "</td>" +
					"<td>" + frappe.utils.escape_html(row.item_group || "-") + "</td>" +
					"<td>" + frappe.utils.escape_html(me.parsed_rows[data.results.indexOf(row)]?.stock_uom || "Kg") + "</td>" +
					"<td>" + frappe.utils.escape_html(me.parsed_rows[data.results.indexOf(row)]?.gst_hsn_code || "-") + "</td>" +
					"<td>" + variant_html + "</td>" +
					"<td>" + (errors_html || '<span style="color:#38a169;">OK</span>') + "</td>" +
				"</tr>"
			);
		});

		// Enable/disable create button
		var valid_count = data.valid_count;
		me.$page.find("#bbf-create-count").text(valid_count);
		me.$page.find("#bbf-btn-create-all").prop("disabled", valid_count === 0);

		// Show preview phase
		me.$page.find("#bbf-phase-upload").hide();
		me.$page.find("#bbf-phase-preview").show();
		me.$page.find("#bbf-phase-results").hide();
	}

	// ─── Create All Items ─────────────────────────

	_create_all() {
		var me = this;

		// Filter only valid rows
		var valid_rows = [];
		me.validated_results.forEach(function (vr, idx) {
			if (vr.valid) {
				valid_rows.push(me.parsed_rows[idx]);
			}
		});

		if (!valid_rows.length) {
			frappe.msgprint("No valid rows to create.", "Nothing to Create");
			return;
		}

		frappe.confirm(
			"Create <b>" + valid_rows.length + "</b> item(s)? This will generate item codes and create ERPNext Items.",
			function () {
				me._process_batch(valid_rows);
			}
		);
	}

	_process_batch(rows) {
		var me = this;
		var total = rows.length;
		var BATCH_SIZE = 10;
		var all_results = [];
		var batch_index = 0;

		// Show progress
		me.$page.find("#bbf-progress-overlay").show();
		me._update_progress(0, total);

		function process_next_batch() {
			var start = batch_index * BATCH_SIZE;
			var end = Math.min(start + BATCH_SIZE, total);
			var batch = rows.slice(start, end);

			if (!batch.length) {
				// All done
				me.$page.find("#bbf-progress-overlay").hide();
				me._render_results(all_results);
				return;
			}

			frappe.call({
				method: "trustbit_ethanol.bbf_gate_entry.api_bulk_import.bulk_create_items",
				args: { rows: batch },
				callback: function (r) {
					if (r.message) {
						all_results = all_results.concat(r.message.results);
						me._update_progress(all_results.length, total);
					}
					batch_index++;
					process_next_batch();
				},
				error: function () {
					// Mark remaining as failed
					for (var i = start; i < end; i++) {
						all_results.push({
							row_num: rows[i]._row_num || "?",
							success: false,
							error: "Network or server error",
							item_name: rows[i].item_name || "",
						});
					}
					me._update_progress(all_results.length, total);
					batch_index++;
					process_next_batch();
				},
			});
		}

		process_next_batch();
	}

	_update_progress(done, total) {
		var pct = total > 0 ? Math.round((done / total) * 100) : 0;
		this.$page.find("#bbf-progress-bar").css("width", pct + "%");
		this.$page.find("#bbf-progress-text").text(done + " / " + total);
	}

	// ─── Results ──────────────────────────────────

	_render_results(results) {
		var me = this;

		var success_count = 0;
		var fail_count = 0;

		var $tbody = me.$page.find("#bbf-results-body");
		$tbody.empty();

		results.forEach(function (r) {
			if (r.success) {
				success_count++;
			} else {
				fail_count++;
			}

			var status_html = r.success
				? '<span class="bbf-badge-success">Created</span>'
				: '<span class="bbf-badge-fail">Failed</span>';

			var code_html = "-";
			if (r.success) {
				var display_code = r.template_code || r.item_code || "";
				var link_code = r.template_code || r.item_code || "";
				if (display_code) {
					code_html = '<a href="/app/item/' + encodeURIComponent(link_code) +
						'" class="bbf-code-preview" style="text-decoration:none;">' +
						frappe.utils.escape_html(display_code) + "</a>";
				}
			}

			var variants_html = "-";
			if (r.variant_items && r.variant_items.length) {
				variants_html = r.variant_items
					.map(function (vi) {
						return '<a href="/app/item/' + encodeURIComponent(vi) +
							'" class="bbf-variant-badge" style="text-decoration:none;">' +
							frappe.utils.escape_html(vi) + "</a>";
					})
					.join(" ");
			}

			var detail_html = r.success
				? '<span style="color:#38a169;font-size:12px;">' + (r.message || "OK") + "</span>"
				: '<span style="color:#e53e3e;font-size:12px;">' + frappe.utils.escape_html(r.error || "Unknown error") + "</span>";

			$tbody.append(
				"<tr>" +
					"<td class='bbf-col-status'>" + (r.row_num || "-") + "</td>" +
					"<td>" + status_html + "</td>" +
					"<td>" + code_html + "</td>" +
					"<td>" + frappe.utils.escape_html(r.item_name || r.item_code || "-") + "</td>" +
					"<td>" + variants_html + "</td>" +
					"<td>" + detail_html + "</td>" +
				"</tr>"
			);
		});

		me.$page.find("#bbf-result-success").text(success_count);
		me.$page.find("#bbf-result-fail").text(fail_count);

		// Show results phase
		me.$page.find("#bbf-phase-upload").hide();
		me.$page.find("#bbf-phase-preview").hide();
		me.$page.find("#bbf-phase-results").show();
	}

	// ─── Reset ────────────────────────────────────

	_reset_to_upload() {
		this.parsed_rows = [];
		this.validated_results = [];

		this.$page.find("#bbf-file-input").val("");
		this.$page.find("#bbf-upload-info").hide();
		this.$page.find("#bbf-preview-body").empty();
		this.$page.find("#bbf-results-body").empty();

		this.$page.find("#bbf-phase-upload").show();
		this.$page.find("#bbf-phase-preview").hide();
		this.$page.find("#bbf-phase-results").hide();
		this.$page.find("#bbf-progress-overlay").hide();
	}
}
