// Copyright (c) 2026, Trustbit Software and contributors
// TS WhatsApp Settings — "Preview Messages" button.
//
// Renders each enabled approval template's variable recipe against a recent
// real document and shows the resulting {{1}}..{{N}} values in a dialog, so
// admins can verify a recipe BEFORE going live. Read-only — nothing is sent.

frappe.ui.form.on("TS WhatsApp Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Preview Messages"), function () {
			frappe.call({
				method: "trustbit_ethanol.ts_gate_entry.ts_whatsapp_notify.preview_template_messages",
				freeze: true,
				freeze_message: __("Rendering previews…"),
				callback(r) {
					const rows = r.message || [];
					if (!rows.length) {
						frappe.msgprint(
							__("No enabled templates to preview. Map a Template Id and tick Enabled on at least one row first.")
						);
						return;
					}
					const esc = frappe.utils.escape_html;
					let html = '<div style="font-size:12px">';
					rows.forEach(function (row) {
						html += '<div style="border:1px solid #d1d8dd;border-radius:6px;padding:10px;margin-bottom:10px">';
						html += "<b>" + esc(row.event_key) + "</b> &middot; " + esc(row.doc_type);
						html +=
							'<br><span style="color:#6c757d">Template: ' +
							esc(row.template_id) +
							" &middot; Sample document: " +
							esc(row.sample_doc) +
							"</span>";
						html += '<br><span style="color:#6c757d">Recipe: ' + esc(row.recipe) + "</span>";
						if (row.variables && row.variables.length) {
							html += '<table class="table table-bordered" style="margin-top:6px;margin-bottom:0"><tbody>';
							row.variables.forEach(function (v, i) {
								html +=
									'<tr><td style="width:56px"><b>{{' +
									(i + 1) +
									"}}</b></td><td>" +
									esc(v) +
									"</td></tr>";
							});
							html += "</tbody></table>";
						} else {
							html += '<br><span style="color:#d9534f">No sample document found to render.</span>';
						}
						html += "</div>";
					});
					html += "</div>";
					frappe.msgprint({
						title: __("Template Previews — nothing is sent"),
						message: html,
						wide: true,
					});
				},
			});
		});
	},
});
