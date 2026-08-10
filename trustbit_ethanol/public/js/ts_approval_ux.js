// v2.8.10 Phase 1 — Bilingual approval UX helpers for MR + PO
// v2.8.11 Phase 2 — Extended to Post-Dated Entry Request + Budget Proposal
// Always bilingual (EN + हिन्दी in every message)
// Loaded FIRST in app_include_js so window.* helpers are defined before consumers.

window.TS_APPROVAL_TRANSLATIONS = {
	// Status labels
	"Draft": "प्रारूप",
	"Pending Approval": "अनुमोदन हेतु लंबित",
	"Pending Department Head": "विभाग प्रमुख की स्वीकृति हेतु लंबित",
	"Pending AVP": "AVP की स्वीकृति हेतु लंबित",
	"Pending Purchase Manager": "क्रय प्रबंधक की स्वीकृति हेतु लंबित",
	"Pending CEO": "CEO की स्वीकृति हेतु लंबित",
	"Pending MD": "MD की स्वीकृति हेतु लंबित",
	"Pending Stock User": "भंडार उपयोगकर्ता की समीक्षा हेतु लंबित",
	"Active": "सक्रिय",
	"Expired": "समाप्त",
	"Approved": "अनुमोदित",
	"Rejected": "अस्वीकृत",
	"Revised": "संशोधित",
	"On Hold": "रोका गया",
	"Not Submitted": "जमा नहीं किया गया",
	// Actions
	"Approve": "अनुमोदन करें",
	"Reject": "अस्वीकार करें",
	"Submit for Approval": "अनुमोदन हेतु जमा करें",
	"Revise": "संशोधित करें",
	"Hold": "रोकें",
	"Resume": "फिर से शुरू करें",
	// Roles
	"Department Head": "विभाग प्रमुख",
	"Stock User": "भंडार उपयोगकर्ता",
	"Purchase Manager": "क्रय प्रबंधक",
	"Grain Purchase Manager": "अनाज क्रय प्रबंधक",
	"AVP": "AVP",
	"CEO": "CEO",
	"MD": "MD",
	// Document-type labels
	"Material Request": "सामग्री अनुरोध",
	"Purchase Order": "क्रय आदेश",
	"Post-Dated Entry Request": "पूर्व-तिथि प्रविष्टि अनुरोध",
	"Budget Proposal": "बजट प्रस्ताव",
	// Common phrases
	"Current Status": "वर्तमान स्थिति",
	"Next Action": "अगली कार्रवाई",
	"Next Approver": "अगला अनुमोदक",
	"Step": "चरण",
	"of": "में से",
	"Created by": "द्वारा बनाया गया",
	"Approval Routing Notice": "अनुमोदन मार्ग सूचना",
	"Cancel": "रद्द करें",
	"Submit Anyway": "फिर भी जमा करें",
	"Recommended": "सुझाव",
};

window.ts_hi = function(en) {
	// Returns Hindi translation or English if missing
	return (window.TS_APPROVAL_TRANSLATIONS && window.TS_APPROVAL_TRANSLATIONS[en]) || en;
};

window.ts_bilingual = function(en) {
	// Returns "EN | HI" formatted string; when HI missing, just EN
	const hi = window.ts_hi(en);
	return (hi && hi !== en) ? `${en} | ${hi}` : en;
};

// ts_render_approval_banner ("Current Status" form banner) removed 11 Aug 2026 —
// no longer needed. Call sites (po_approval.js, mr_approval.js,
// ts_post_dated_entry_request.js, ts_budget_proposal.js) typeof-guard the call
// and become silent no-ops; they were deliberately left untouched.

window.ts_check_submit_on_behalf = function(frm) {
	// Returns Promise<boolean>: true = proceed with submit, false = cancel
	return new Promise((resolve) => {
		if (!frm || !frm.doc) return resolve(true);

		// v2.8.11.1 HOTFIX: only show the warning on the INITIAL submit by the
		// creator. Approvers (AVP, CEO, MD etc.) clicking Approve also trigger
		// before_submit — their status is already "Pending X" so we skip.
		// Without this guard, every Approve click showed the dialog inappropriately.
		const approval_status_field_map = {
			"Material Request": "ts_mr_status",
			"Purchase Order": "ts_approval_status",
			"TS Post Dated Entry Request": "status",
			"TS Budget Proposal": "status",
		};
		const status_field = approval_status_field_map[frm.doctype];
		if (status_field) {
			const current_status = frm.doc[status_field];
			// If already in an approval state (not Draft/Not Submitted/empty),
			// this is a mid-chain Approve click — skip the warning entirely.
			if (current_status && current_status !== "Draft" && current_status !== "Not Submitted") {
				return resolve(true);
			}
		}

		const owner = frm.doc.owner;
		const current_user = frappe.session.user;
		if (!owner || owner === current_user) return resolve(true);  // no warning

		// Only show warning if current user lacks Department Head role
		// (because that's where the common confusion is — HOD's MR being submitted by AVP)
		const user_roles = frappe.user_roles || [];
		const has_dept_head = user_roles.includes("Department Head");
		if (has_dept_head) return resolve(true);  // no warning — self-skip will work

		// Build bilingual dialog
		const esc = (s) => frappe.utils.escape_html(String(s || ""));
		const owner_name = frm.doc.owner || "?";
		const notice_title = window.ts_bilingual("Approval Routing Notice");
		const body_en = `You are submitting a document created by <b>${esc(owner_name)}</b>.<br>Because you do not have the <b>Department Head</b> role, this approval will route BACK to them first before reaching further approvers.`;
		const body_hi = `आप एक दस्तावेज़ जमा कर रहे हैं जो <b>${esc(owner_name)}</b> ने बनाया था।<br>चूंकि आपके पास <b>विभाग प्रमुख</b> भूमिका नहीं है, यह अनुमोदन पहले उनके पास वापस जाएगा, फिर आगे के अनुमोदकों तक पहुंचेगा।`;
		const rec_bi = window.ts_bilingual("Recommended");
		const rec_en = `Ask <b>${esc(owner_name)}</b> to submit it themselves to skip Dept Head review.`;
		const rec_hi = `<b>${esc(owner_name)}</b> से अनुरोध करें कि वे स्वयं जमा करें ताकि विभाग प्रमुख समीक्षा छूट जाए।`;

		const d = new frappe.ui.Dialog({
			title: `⚠️ ${notice_title}`,
			fields: [
				{ fieldtype: "HTML", options: `<div style="padding: 10px; font-size: 13px; line-height: 1.6;">
					<div style="margin-bottom: 12px;">${body_en}</div>
					<div style="margin-bottom: 12px; padding-top: 12px; border-top: 1px dashed var(--border-color);">${body_hi}</div>
					<div style="margin-top: 16px; padding: 8px 12px; background: var(--bg-light-gray, #fef3c7); border-radius: 4px; font-size: 12px;">
						<b>${esc(rec_bi)}:</b><br>${rec_en}<br><span style="color: var(--text-muted);">${rec_hi}</span>
					</div>
				</div>` }
			],
			primary_action_label: window.ts_bilingual("Submit Anyway"),
			primary_action: () => { d.hide(); resolve(true); },
			secondary_action_label: window.ts_bilingual("Cancel"),
			secondary_action: () => { d.hide(); resolve(false); },
		});
		d.show();
	});
};

// ─── Grid Description hover card (PO / MR / PR / PI + Update Items dialog) ───
// Row's FULL description in a popover card (same Bootstrap component frappe's
// link preview uses). html:false → text-only render, cell content can never
// inject markup. One card at a time; disposed on leave/click/route-change/10s.
(function () {
	const TS_HOVER_ROUTES = new Set(["Purchase Order", "Material Request", "Purchase Receipt", "Purchase Invoice"]);
	const SEL = '.grid-body .grid-static-col[data-fieldname="description"]';
	let active_el = null, show_timer = null, life_timer = null;

	function dispose_card() {
		if (show_timer) { clearTimeout(show_timer); show_timer = null; }
		if (life_timer) { clearTimeout(life_timer); life_timer = null; }
		if (active_el) { $(active_el).popover("dispose"); active_el = null; }
	}

	$(document).on("mouseenter", SEL, function () {
		const pc = this.closest(".page-container");
		const in_scope = (pc && TS_HOVER_ROUTES.has(pc.getAttribute("data-page-route"))) ||
			this.closest('.frappe-control[data-fieldname="trans_items"]');
		if (!in_scope) return;
		const sa = this.querySelector(".static-area");
		const text = sa ? (sa.textContent || "").trim() : "";
		if (!text) return;
		const row = this.closest(".data-row");
		const item_sa = row && row.querySelector('[data-fieldname="item_code"] .static-area');
		const title = ((item_sa && item_sa.textContent) || "Description").trim();

		dispose_card();
		const el = this;
		show_timer = setTimeout(() => {
			show_timer = null;
			$(el).popover({
				trigger: "manual", container: "body", placement: "top",
				boundary: "viewport", html: false, title: title, content: text,
			}).popover("show");
			active_el = el;
			life_timer = setTimeout(dispose_card, 10000);
		}, 350);
	});
	$(document).on("mouseleave", SEL, dispose_card);
	$(document).on("click", dispose_card);
	frappe.router && frappe.router.on && frappe.router.on("change", dispose_card);
})();
