// v2.8.13 — "My Pending Approvals" client helper.
//
// Provides:
//  1. window.ts_apply_my_approvals_filter(listview)
//       Called from mr_list.js, po_list.js, ts_post_dated_entry_request_list.js,
//       ts_budget_proposal_list.js. Applies role-scoped status filter ONCE
//       per page-load (clearable via standard filter bar).
//  2. 3-way toggle button [All Records | My Pending | My Submissions]
//       rendered into .page-actions area of the list view.
//  3. Dashboard tile renderer for the "MR & PO Dashboard" workspace
//       (looks for DOM node with data-ts-my-approvals="1").
//
// Loaded AFTER ts_approval_ux.js (which provides window.ts_bilingual).
// Gated on session.user !== "Guest" + path /app/ per Lesson 190.

(function () {
	"use strict";

	const SUPPORTED_DOCTYPES = [
		"Material Request",
		"Purchase Order",
		"TS Post Dated Entry Request",
		"TS Budget Proposal",
	];

	// localStorage key per user per doctype — remembers last toggle choice.
	function _ls_key(doctype) {
		const u = (frappe.session && frappe.session.user) || "anon";
		return `ts_my_approvals_toggle::${u}::${doctype}`;
	}

	// Session-scope guard: applied-once-per-load flag on the listview instance.
	function _already_applied(listview) {
		return !!listview._ts_my_approvals_applied;
	}
	function _mark_applied(listview) {
		listview._ts_my_approvals_applied = true;
	}

	// Guest + path gate per Lesson 190
	function _user_gate_ok() {
		if (typeof frappe === "undefined" || !frappe.session) return false;
		if (frappe.session.user === "Guest") return false;
		if (!location.pathname.startsWith("/app")) return false;
		return true;
	}

	function _bilingual(en) {
		return (typeof window.ts_bilingual === "function")
			? window.ts_bilingual(en)
			: en;
	}

	// ---------- Public: apply default filter on list-view open ----------
	window.ts_apply_my_approvals_filter = function (listview) {
		if (!_user_gate_ok()) return;
		if (!listview || !listview.doctype) return;
		if (SUPPORTED_DOCTYPES.indexOf(listview.doctype) === -1) return;
		if (_already_applied(listview)) return;

		// Read saved toggle choice (default: "my_pending" for first-time approvers,
		// "all" for non-approvers).
		const saved = localStorage.getItem(_ls_key(listview.doctype));
		_inject_toggle_widget(listview, saved);

		// If user manually set filters via URL before we load → respect them.
		let existing_filters = [];
		try { existing_filters = listview.get_filters_for_args() || []; } catch(e) {}
		const user_set_approval_filter = existing_filters.some(f =>
			f && f[1] && (f[1].startsWith("ts_") || f[1] === "status" || f[1] === "owner" || (listview.doctype === "Material Request" && f[1] === "ts_mr_submitted_by"))
		);
		if (user_set_approval_filter) {
			_mark_applied(listview);
			return;
		}

		// Apply default based on saved toggle (or default to my_pending)
		const choice = saved || "my_pending";
		_apply_toggle_state(listview, choice, /*silent=*/true);
		_mark_applied(listview);
	};

	// ---------- Toggle widget ----------
	function _inject_toggle_widget(listview, saved_choice) {
		// Avoid double-injection
		const $page = $(listview.page.wrapper);
		if ($page.find(".ts-my-approvals-toggle").length) return;

		const current = saved_choice || "my_pending";
		const labels = {
			"all":           _bilingual("All Records"),
			"my_pending":    _bilingual("My Pending"),
			"my_submissions": _bilingual("My Submissions"),
		};

		const esc0 = frappe.utils.escape_html;
		const $toggle = $(`
			<div class="ts-my-approvals-toggle ts-ma-toggle" role="group" aria-label="${esc0(_bilingual("My Pending Approvals"))}">
				<button class="btn btn-default btn-xs" data-choice="all" aria-pressed="false">${esc0(labels.all)}</button>
				<button class="btn btn-default btn-xs" data-choice="my_pending" aria-pressed="false">${esc0(labels.my_pending)}</button>
				<button class="btn btn-default btn-xs" data-choice="my_submissions" aria-pressed="false">${esc0(labels.my_submissions)}</button>
			</div>
		`);
		$toggle.find(`button[data-choice="${current}"]`).addClass("btn-primary").removeClass("btn-default").attr("aria-pressed", "true");

		$toggle.on("click", "button", function () {
			const choice = $(this).data("choice");
			$toggle.find("button").removeClass("btn-primary").addClass("btn-default").attr("aria-pressed", "false");
			$(this).removeClass("btn-default").addClass("btn-primary").attr("aria-pressed", "true");
			localStorage.setItem(_ls_key(listview.doctype), choice);
			_apply_toggle_state(listview, choice, /*silent=*/false);
		});

		// Insert near the standard filter controls. Frappe builds the filter
		// UI async; retry a few times to land next to filter-selector rather
		// than orphan-prepending into .page-actions.
		_insert_toggle_with_retry($page, $toggle, 0);
	}

	function _insert_toggle_with_retry($page, $toggle, attempt) {
		const $target = $page.find(".page-actions .filter-selector").first();
		if ($target.length) {
			$target.before($toggle);
			return;
		}
		if (attempt < 3) {
			setTimeout(function () {
				_insert_toggle_with_retry($page, $toggle, attempt + 1);
			}, 150);
			return;
		}
		// Fallback — place at start of .page-actions
		$page.find(".page-actions").first().prepend($toggle);
	}

	function _apply_toggle_state(listview, choice, silent) {
		// Clear any filter we may have added previously (tracked by field).
		const tracked = listview._ts_my_approvals_tracked_filters || [];
		tracked.forEach(function (f) {
			try { listview.filter_area.remove(f); } catch (e) {}
		});
		listview._ts_my_approvals_tracked_filters = [];

		if (choice === "all") {
			if (!silent) listview.refresh();
			return;
		}

		if (choice === "my_pending") {
			_fetch_and_apply_pending(listview, silent);
			return;
		}

		if (choice === "my_submissions") {
			_fetch_and_apply_submissions(listview, silent);
			return;
		}
	}

	function _fetch_and_apply_pending(listview, silent) {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_my_approvals_api.get_user_pending_statuses",
			args: { doctype: listview.doctype },
			callback: function (r) {
				if (!r || !r.message || !r.message.enabled) {
					if (!silent) listview.refresh();
					return;
				}
				const statuses = r.message.statuses || [];
				const field = r.message.status_field;
				if (!statuses.length || !field) {
					// User has no approver role on this doctype → show nothing from "my pending"
					// Apply a filter that will return no rows (preserves the state).
					try {
						listview.filter_area.add([[listview.doctype, field || "name", "=", "__ts_no_match__"]]);
						listview._ts_my_approvals_tracked_filters.push(field || "name");
					} catch(e) {}
					if (!silent) listview.refresh();
					return;
				}
				try {
					listview.filter_area.add([[listview.doctype, field, "in", statuses]]);
					listview._ts_my_approvals_tracked_filters.push(field);
				} catch(e) {}
				if (!silent) listview.refresh();
			},
			error: function () {
				if (!silent) listview.refresh();
			},
		});
	}

	function _fetch_and_apply_submissions(listview, silent) {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_my_approvals_api.get_my_submissions_filter",
			args: { doctype: listview.doctype },
			callback: function (r) {
				if (!r || !r.message || !r.message.enabled) {
					if (!silent) listview.refresh();
					return;
				}
				const filters = r.message.filters || [];
				filters.forEach(function (f) {
					try {
						listview.filter_area.add([f]);
						listview._ts_my_approvals_tracked_filters.push(f[1]);
					} catch(e) {}
				});
				if (!silent) listview.refresh();
			},
			error: function () {
				if (!silent) listview.refresh();
			},
		});
	}

	// ---------- v2.17.4: reconcile a manual status filter vs. the role default ----------
	// BUG (PO/MR list): the "My Pending" default adds a filter-list filter
	//     <status_field> in [viewer's role statuses]
	// When the user then picks a value in the standard "Approval Status" dropdown
	//     <status_field> = X
	// Frappe's FilterArea.get() keeps BOTH (different conditions on the same field),
	// so the server ANDs `in [...] AND = X` → ZERO rows for any status outside the
	// viewer's own queue (e.g. a CEO filtering "Pending PM"). The manual pick must win.
	//
	// Called from po_list.js / mr_list.js inside their existing refresh override,
	// BEFORE orig_refresh(). Filter.remove() sets field=null (no on_change, no nested
	// refresh) so the rebuilt query simply omits the conflicting `in` filter;
	// update_filters() then tidies the filter-button count — the same path the
	// framework's own filter_area.remove() uses (proven null-safe in prod).
	window.ts_reconcile_manual_status_filter = function (listview, field) {
		try {
			if (!listview || !field) return;
			const fd = listview.page && listview.page.fields_dict;
			const std = fd && fd[field];
			const manual_val = (std && typeof std.get_value === "function") ? std.get_value() : "";
			if (!manual_val) return;  // no manual dropdown selection → nothing to reconcile

			const fl = listview.filter_area && listview.filter_area.filter_list;
			const conflicting = (fl && typeof fl.get_filter === "function") ? fl.get_filter(field) : null;
			if (!conflicting) return;  // no filter-list filter on this field → no collision

			conflicting.remove();  // field=null → dropped from get_filters(); no refresh fired
			if (typeof fl.update_filters === "function") fl.update_filters();  // tidy button count

			// Forget our tracked filter (keep the array a list of fieldname strings).
			if (Array.isArray(listview._ts_my_approvals_tracked_filters)) {
				listview._ts_my_approvals_tracked_filters =
					listview._ts_my_approvals_tracked_filters.filter(function (f) { return f !== field; });
			}
			// Reflect "All Records" in the toggle — the manual filter left the role view.
			const $tg = $(listview.page.wrapper).find(".ts-my-approvals-toggle");
			if ($tg.length) {
				$tg.find("button").removeClass("btn-primary").addClass("btn-default").attr("aria-pressed", "false");
				$tg.find('button[data-choice="all"]').removeClass("btn-default").addClass("btn-primary").attr("aria-pressed", "true");
			}
		} catch (e) {}
	};

	// Dashboard tiles are rendered by the "TS My Approvals Dashboard"
	// Custom HTML Block inside the "MR & PO Dashboard" workspace — native
	// Frappe v15 pattern. This JS is now list-view-only.
})();
