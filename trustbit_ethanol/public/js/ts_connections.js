// v2.9.11.1 — Shared Connections panel — self-installs refresh hooks for 6 doctypes.
// Renders the Related Documents panel into the ts_connections_html field on:
//   TS Deduction Sheet, Material Request, Purchase Order, Purchase Receipt,
//   TS Quality Inspection, Purchase Invoice
// Loaded globally via app_include_js (hooks.py).
//
// v2.9.11.1: empty-state UX — when total items across all sections == 0,
// render a single friendly message instead of 4 empty cards. Common case:
// direct supplier PIs with no upstream chain.

(function () {
	const TS_CONN_DOCTYPES = [
		"TS Deduction Sheet",
		"Material Request",
		"Purchase Order",
		"Purchase Receipt",
		"TS Quality Inspection",
		"Purchase Invoice",
	];

	const STATUS_COLOR = {
		"Draft": "gray", "Not Submitted": "gray", "Submitted": "green",
		"Cancelled": "red", "Approved": "green", "Rejected": "red",
		"On Hold": "orange",
	};

	function _ts_status_class(status) {
		if (!status) return "ts-pill-gray";
		if (status.indexOf("🔒") !== -1) return "ts-pill-gray";
		if (status.startsWith("Pending")) return "ts-pill-orange";
		if (STATUS_COLOR[status]) return "ts-pill-" + STATUS_COLOR[status];
		if (status.indexOf("Approved") !== -1) return "ts-pill-green";
		if (status.indexOf("Reject") !== -1) return "ts-pill-red";
		if (status.indexOf("Cancel") !== -1) return "ts-pill-red";
		return "ts-pill-blue";
	}

	function _ts_render_skeleton(wrapper) {
		const sk = `
		<div class="ts-conn-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;">
			${[1,2,3,4].map(() => `
			<div class="ts-conn-card" style="border:1px solid var(--border-color);border-radius:6px;padding:10px;background:var(--bg-color);">
				<div style="height:14px;width:50%;background:var(--gray-100);margin-bottom:8px;border-radius:3px;"></div>
				<div style="height:10px;width:80%;background:var(--gray-50);margin-bottom:6px;border-radius:3px;"></div>
				<div style="height:10px;width:65%;background:var(--gray-50);border-radius:3px;"></div>
			</div>
			`).join("")}
		</div>`;
		wrapper.html(sk);
	}

	function _ts_render_row(it) {
		const dt_label = frappe.utils.escape_html(it.doctype || "");
		const name_esc = frappe.utils.escape_html(it.name || "");
		const status_esc = frappe.utils.escape_html(it.status || "");
		const url_esc = frappe.utils.escape_html(it.url || "#");
		const pill_cls = _ts_status_class(it.status);
		const cancelled_cls = it.docstatus === 2 ? "ts-conn-cancelled" : "";
		const noperm_cls = it.no_permission ? "ts-conn-noperm" : "";
		const tooltip = it.no_permission
			? `title="You don't have permission to view this document"`
			: "";
		const link_html = it.non_clickable
			? `<span class="ts-conn-name-locked" ${tooltip}>${name_esc}</span>`
			: `<a href="${url_esc}">${name_esc}</a>`;
		return `
		<div class="ts-conn-row ${cancelled_cls} ${noperm_cls}" ${tooltip}>
			<div class="ts-conn-doctype">${dt_label}</div>
			${link_html}
			${status_esc ? `<span class="ts-conn-pill ${pill_cls}">${status_esc}</span>` : ""}
		</div>`;
	}

	function _ts_render(wrapper, sections, frm) {
		if (!sections || sections.length === 0) {
			wrapper.html(`<div style="color:var(--text-muted);font-style:italic;padding:8px;">No related documents.</div>`);
			return;
		}
		// v2.9.11.1 — collapse 4 empty cards into a single message when
		// EVERY section has zero items (typical for direct supplier PIs).
		const total_items = sections.reduce(
			(n, s) => n + ((s.items || []).length), 0);
		if (total_items === 0) {
			wrapper.html(`<div style="color:var(--text-muted);font-style:italic;padding:12px;text-align:center;border:1px dashed var(--border-color);border-radius:6px;">No related documents — this record has no upstream or downstream connections.</div>`);
			return;
		}
		const css = `
		<style>
		.ts-conn-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px; padding:6px 0; }
		.ts-conn-card { border:1px solid var(--border-color); border-radius:6px; padding:12px; background:var(--bg-color); min-height:80px; }
		.ts-conn-card-header { font-weight:bold; font-size:13px; color:var(--heading-color); margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid var(--border-color); display:flex; align-items:center; justify-content:space-between; }
		.ts-conn-card-icon { font-size:16px; margin-right:6px; }
		.ts-conn-row { display:block; padding:5px 0; line-height:1.4; }
		.ts-conn-row + .ts-conn-row { border-top:1px dashed var(--border-color); margin-top:4px; }
		.ts-conn-row a { color:var(--blue-500); text-decoration:none; font-weight:500; word-break:break-all; }
		.ts-conn-row a:hover { text-decoration:underline; }
		.ts-conn-doctype { font-size:10px; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:1px; }
		.ts-conn-cancelled a { text-decoration:line-through; color:var(--text-muted); }
		.ts-conn-noperm .ts-conn-name-locked { color:var(--text-muted); cursor:not-allowed; font-weight:500; }
		.ts-conn-noperm { opacity: 0.65; }
		.ts-conn-pill { display:inline-block; font-size:10px; padding:2px 8px; border-radius:10px; font-weight:600; margin-left:6px; vertical-align:middle; }
		.ts-pill-gray { background:var(--gray-100); color:var(--gray-700); }
		.ts-pill-green { background:var(--green-100); color:var(--green-700); }
		.ts-pill-red { background:var(--red-100); color:var(--red-700); }
		.ts-pill-orange { background:var(--orange-100); color:var(--orange-700); }
		.ts-pill-blue { background:var(--blue-100); color:var(--blue-700); }
		.ts-conn-empty-section { color:var(--text-muted); font-style:italic; font-size:11px; }
		.ts-conn-more { font-size:11px; color:var(--blue-500); margin-top:8px; cursor:pointer; padding:4px 0; display:inline-block; }
		.ts-conn-more:hover { text-decoration:underline; }
		.ts-conn-section-count { font-size:10px; color:var(--text-muted); font-weight:normal; }
		</style>`;

		const cards_html = sections.map((section, sec_idx) => {
			const items = section.items || [];
			const total = section.total_count || items.length;
			const has_more = section.has_more;
			const rows = items.length === 0
				? `<div class="ts-conn-empty-section">— No documents —</div>`
				: items.map(_ts_render_row).join("");
			const more_btn = has_more
				? `<a class="ts-conn-more" data-section-idx="${sec_idx}">+ ${total - items.length} more →</a>`
				: "";
			const count_label = total > 0 ? `<span class="ts-conn-section-count">${total}</span>` : "";
			const icon = section.icon ? `<span class="ts-conn-card-icon">${section.icon}</span>` : "";
			const label = frappe.utils.escape_html(section.label || "");
			return `
			<div class="ts-conn-card" data-section-idx="${sec_idx}">
				<div class="ts-conn-card-header">
					<span>${icon}${label}</span>
					${count_label}
				</div>
				${rows}
				${more_btn}
			</div>`;
		}).join("");

		wrapper.html(css + `<div class="ts-conn-grid">${cards_html}</div>`);

		// Wire up "+N more" click handlers
		wrapper.find(".ts-conn-more").on("click", function (e) {
			e.preventDefault();
			const sec_idx = parseInt($(this).data("section-idx"), 10);
			_ts_open_more_dialog(frm, sections[sec_idx]);
		});
	}

	function _ts_open_more_dialog(frm, section) {
		const all_items = section.all_items || section.items || [];
		// If server didn't send all_items, just show what we have
		const rows = all_items.length === 0
			? `<div style="color:var(--text-muted);font-style:italic;">No documents</div>`
			: all_items.map(_ts_render_row).join("");
		const d = new frappe.ui.Dialog({
			title: __("All {0} ({1})", [section.label, section.total_count || all_items.length]),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "list_html" }],
			primary_action_label: __("Close"),
			primary_action: () => d.hide(),
		});
		d.fields_dict.list_html.$wrapper.html(`
			<div style="max-height:60vh;overflow-y:auto;padding:4px;">
				${rows}
			</div>
		`);
		d.show();
	}

	function _ts_load_connections(frm) {
		const wrapper = frm.fields_dict.ts_connections_html
			? frm.fields_dict.ts_connections_html.$wrapper : null;
		if (!wrapper) return;

		_ts_render_skeleton(wrapper);

		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_connections.get_connections",
			args: { doctype: frm.doctype, name: frm.doc.name },
			callback: function (r) {
				if (!r || !r.message) {
					wrapper.html(`<div style="color:var(--red-500);">⚠️ Could not load connections.
						<a href="javascript:void(0)" onclick="cur_frm.refresh()">Retry</a></div>`);
					return;
				}
				if (r.message.error === "no_permission") {
					wrapper.html(`<div style="color:var(--text-muted);">No permission to view related documents.</div>`);
					return;
				}
				_ts_render(wrapper, r.message.sections || [], frm);
			},
			error: function () {
				wrapper.html(`<div style="color:var(--red-500);">⚠️ Could not load connections.
					<a href="javascript:void(0)" onclick="cur_frm.refresh()">Retry</a></div>`);
			},
		});
	}

	// Self-install refresh hook for each target doctype
	TS_CONN_DOCTYPES.forEach(function (dt) {
		frappe.ui.form.on(dt, {
			refresh: function (frm) {
				if (frm.is_new()) return;
				_ts_load_connections(frm);
			},
		});
	});

	// Expose for debugging / manual trigger
	window.ts_load_connections = _ts_load_connections;
})();
