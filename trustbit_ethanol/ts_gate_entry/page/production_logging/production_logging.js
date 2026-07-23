/* =====================================================================
   BETUL BIO FUEL — PRODUCTION LOGGING  (custom Frappe page)
   Ported from BBPL_Production_Logging_Dashboard.html (approved mockup) and
   wired to the LIVE backend (ts_production_release.py / ts_production_api.py).

   Real endpoints (all mutations are POST, enforced server-side):
     fetch_bom_standard ........... ts_production_api.fetch_bom_standard
     get_production_settings ...... ts_production_api.get_production_settings
     create + list + get .......... frappe.client.{insert,get_list,get}
     submit_for_release ........... ts_production_release.submit_for_release   (PM gate)
     approve_release .............. ts_production_release.approve_release       (Store Mgr gate)
     reject_release ............... ts_production_release.reject_release

   Design discipline:
     - Single page root `.prodlog`; PAGE-LOCAL theme attribute
       `data-prodlog-theme` on that root (NEVER a global <html> data-theme).
     - page-head + navbar breadcrumb hidden on load, restored on page hide.
     - Every interpolated value escaped with frappe.utils.escape_html.
     - NO mockup dummy-data drivers — all rows come from frappe.call.
     - The progress overlay advances AROUND the real frappe.call; it finishes
       on success and switches to an error state on failure.
   ===================================================================== */

const PL_VERSION = "v1.5.0"; // 23 Jul — Single-flow by-product Post Distribution (opt-in pause + PM split card) // 22 Jul — dept vote-to-delete card + by-product clear-restores-auto-scale // v2.21 Single-flow department release
const PL_DOCTYPE = "TS Production Entry";
const PL_API = "trustbit_ethanol.ts_gate_entry.ts_production_api";
const PL_REL = "trustbit_ethanol.ts_gate_entry.ts_production_release";

frappe.pages["production-logging"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Production Logging",
		single_column: true,
	});

	// Hide the desk page-head + navbar breadcrumb so the liquid-glass canvas owns
	// the viewport. Restore both on page hide (OMC-page pattern).
	const $wrapper = $(wrapper);
	const $pageHead = $wrapper.find(".page-head");
	const $breadcrumbs = $("#navbar-breadcrumbs");
	$pageHead.hide();
	$breadcrumbs.hide();
	wrapper.__pl_restore_chrome = function () {
		$pageHead.show();
		$breadcrumbs.show();
	};

	const c = new ProductionLogging(page, wrapper);
	wrapper.__pl_controller = c;
	c.init();
};

frappe.pages["production-logging"].on_page_show = function (wrapper) {
	// Re-hide if the user navigated back to the page.
	$(wrapper).find(".page-head").hide();
	$("#navbar-breadcrumbs").hide();
	if (wrapper.__pl_controller) wrapper.__pl_controller.reload_data();
};

frappe.pages["production-logging"].on_page_hide = function (wrapper) {
	// Restore the desk chrome we hid (page-head + breadcrumb).
	if (typeof wrapper.__pl_restore_chrome === "function") wrapper.__pl_restore_chrome();
};

class ProductionLogging {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$root = null;
		this.settings = null;

		// role flags (from frappe.user_roles)
		const roles = frappe.user_roles || [];
		this.is_administrator = frappe.session.user === "Administrator"; // U3: ONLY this user may Skip
		this.is_admin = roles.includes("System Manager") || roles.includes("IT Head") ||
			this.is_administrator;
		this.is_store_mgr = roles.includes("Stores Manager") || this.is_admin;
		this.is_pm = roles.includes("PM") || roles.includes("Grain PM") ||
			roles.includes("Manufacturing Manager") || roles.includes("Manufacturing User") ||
			this.is_admin;
		// R3 board viewers — mirrors the server allow-list in ts_production_dept._BOARD_ROLES
		// (server is authoritative; this only decides whether to render the zone).
		this.is_board = this.is_pm || this.is_store_mgr ||
			roles.includes("CEO") || roles.includes("MD");

		// Add-Production form working state
		this.bom = null;
		this.bom_std = null;          // payload from fetch_bom_standard
		this.rm_state = [];           // editable RM rows: {item_code,item_name,std_qty,uom,source_warehouse,qty,edited,removed}
		this.bp_state = [];           // by-product rows (auto-scaled, read-only)

		this.busy = false;
		this.boms = [];               // active BOM list for the picker
		this.dept_ctx = null;         // Phase C: department consumption context (server-resolved)
		this.multi_ctx = null;        // Phase D: Multiple-flow context (kill switch + connectors)
		this.flow_mode = "single";    // Phase D: chooser state ("single" | "multiple")
		this.connector = null;        // Phase D: the selected TS BOM Connector row
		this.warehouses = null;       // Phase D: non-group warehouses (distribution dialog)
	}

	esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}
	fmt(n) {
		return Math.round(flt(n)).toLocaleString("en-IN");
	}
	fmt1(n) {
		return flt(n).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 1 });
	}

	init() {
		this.page.main.html('<div class="prodlog" data-prodlog-theme="light"></div>');
		this.$root = this.page.main.find(".prodlog");
		this.render_shell();
		this.bind_static();
		this.load_settings_then_data();
	}

	// ── Theme (page-local) ────────────────────────────────────────────
	get theme() {
		return this.$root.attr("data-prodlog-theme") || "light";
	}
	toggle_theme() {
		const next = this.theme === "dark" ? "light" : "dark";
		this.$root.attr("data-prodlog-theme", next);
		this.render_theme_ico();
		this.render_flow(); // re-render SVG-bearing rail so var()-driven strokes recompute
	}
	render_theme_ico() {
		const dark = this.theme === "dark";
		this.$root.find("#pl-theme-ico").html(
			dark
				? '<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>'
				: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'
		);
	}

	// ── SHELL (static chrome injected once; data sections filled later) ─
	render_shell() {
		const showAdd = this.is_pm;
		const roleLabel = this.is_store_mgr && !this.is_pm
			? "Store Manager"
			: this.is_pm && this.is_store_mgr
			? "PM + Store Mgr"
			: this.is_pm
			? "Production Mgr"
			: "Viewer";

		const html = `
		<div class="bg-stage" aria-hidden="true">
			<div class="orb a"></div><div class="orb b"></div><div class="orb c"></div><div class="orb d"></div><div class="orb e"></div>
		</div>
		<div class="bg-sheen" aria-hidden="true"></div>

		<div class="hero">
			<div class="wrap">
				<div class="hero-inner">
					<div class="logo">
						<svg viewBox="0 0 24 24" fill="none"><path d="M12 2c-2.5 4-5 6.5-5 10a5 5 0 0010 0c0-3.5-2.5-6-5-10z" fill="#fff" opacity=".95"/><circle cx="12" cy="13" r="2" fill="#16a34a"/></svg>
					</div>
					<div class="brand-block">
						<div class="brand-name">Betul Bio Fuel Pvt Ltd <span class="ver-badge">${this.esc(PL_VERSION)}</span></div>
						<div class="brand-sub">Production Logging &middot; Ethanol Distillery &middot; Plant Floor</div>
					</div>
					<div class="hero-spacer"></div>
					<span class="role-chip"><span class="rk">You are</span> ${this.esc(roleLabel)}</span>
					${showAdd ? `<button class="add-btn" id="pl-add-btn" title="Log a new production run">
						<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
						Add Production
					</button>` : ``}
					<button class="toggle-btn" id="pl-theme-btn" title="Light / Dark" aria-label="Toggle theme">
						<svg id="pl-theme-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"></svg>
					</button>
				</div>
				<div class="meta-row" id="pl-meta-row">
					<span class="live-tag"><span class="pulse-dot"></span> Live &middot; plant floor</span>
					<span class="sep"></span>
					<span>Today <b>${this.esc(frappe.datetime.str_to_user(frappe.datetime.now_date()))}</b></span>
					<span class="sep"></span>
					<span id="pl-feature-tag"></span>
				</div>
			</div>
		</div>

		<div class="wrap">

			<div id="pl-actions-wrap"></div>

			<details class="pl-how glass" id="pl-how"${localStorage.getItem("pl_how_open") === "1" ? " open" : ""}>
				<summary><span class="pl-how-chev">&#9656;</span> How a production run flows
					<span class="pl-how-dim">&mdash; PM logs it, the system does the rest &middot; tap to open</span>
					<span class="feas-tag tag-auto" style="margin-left:auto">&#9881; Auto</span>
					<span class="feas-tag tag-gate">&#128682; Store-Manager gate</span>
				</summary>
				<div class="flow-head">
					<div class="flow-legend" style="margin-left:2px">
						<span><i class="lg-dot lg-input">&#9998;</i> PM input</span>
						<span><i class="lg-dot lg-auto">&#9881;</i> Automated</span>
						<span><i class="lg-dot lg-gate">&#128682;</i> Store-Mgr gate</span>
					</div>
				</div>
				<div class="flow-rail" id="pl-flow-rail"></div>
				<div class="status-flow">
					<span class="sf-lbl">Status flow</span>
					<span class="sf-chip b-draft"><span class="bdot"></span>Draft</span>
					<span class="sf-arrow">&rarr;</span>
					<span class="sf-chip b-pending"><span class="bdot"></span>Pending Department Release</span>
					<span class="sf-arrow">&rarr;</span>
					<span class="sf-chip b-pending"><span class="bdot"></span>Pending Stores Release</span>
					<span class="sf-arrow">&rarr;</span>
					<span class="sf-chip b-released"><span class="bdot"></span>Released</span>
					<span class="sf-arrow">&rarr;</span>
					<span class="sf-chip b-completed"><span class="bdot"></span>Completed</span>
				</div>
			</details>

			${this.zones_html()}

			<div class="sec-title">
				<span class="bar" style="background:var(--green)"></span> At a glance
				<span class="feas-tag tag-auto">Live &middot; ${this.esc(PL_DOCTYPE)}</span>
			</div>
			<div class="kpi-grid" id="pl-kpi-grid"></div>

			<div class="sec-title">
				<span class="bar" style="background:var(--purple)"></span> Production Log &mdash; Recent Runs
				<span class="feas-tag tag-auto">Status-aware &middot; 4-state flow</span>
			</div>
			<div class="panel glass">
				<div class="panel-h">
					<span class="t">Recent Production Entries</span>
					<span class="sub" id="pl-log-sub">last 30 runs &middot; ${PL_DOCTYPE}</span>
				</div>
				<div class="table-scroll">
					<table class="log-table">
						<thead><tr>
							<th>Production ID</th>
							<th>BOM</th>
							<th>Item</th>
							<th class="r">Produced</th>
							<th>Status</th>
							<th class="r">Variance</th>
							<th>Work Order</th>
							<th>Updated</th>
						</tr></thead>
						<tbody id="pl-log-body"></tbody>
					</table>
				</div>
			</div>

			<div class="footer">
				<b>Betul Bio Fuel Pvt Ltd</b> &middot; Production Logging ${this.esc(PL_VERSION)} &nbsp;&middot;&nbsp; Extends <b>${this.esc(PL_DOCTYPE)}</b><br>
				Powered by <b>Trustbit Technologies Pvt. Ltd.</b>
			</div>
		</div>

		${showAdd ? this.slideover_html() : ``}
		${this.progress_html()}
		`;
		this.$root.html(html);
		this.render_theme_ico();
		this.render_flow();
		// "How it works" open/closed persistence — the HTML `toggle` event does
		// NOT bubble, so jQuery delegation can't catch it; bind directly on the
		// static element (shell renders exactly once).
		const how = this.$root.find("#pl-how")[0];
		if (how) how.addEventListener("toggle", function () {
			try { localStorage.setItem("pl_how_open", this.open ? "1" : "0"); } catch (e) { /* private mode */ }
		});
	}

	// v2.21 premium de-clutter — zones in ROLE-FIRST order: your actionable
	// queue renders highest. (Release zone exists only for Store Managers,
	// exactly as before — only the ORDER changes.)
	zones_html() {
		const release = this.is_store_mgr ? `
			<div class="sec-title" id="pl-sec-release">
				<span class="bar" style="background:var(--amber)"></span> The One Human Gate &mdash; Store Manager Raw-Material Release
				<span class="feas-tag tag-gate">&#128682; Awaiting approval</span>
			</div>
			<div id="pl-release-zone"></div>` : ``;
		// v2.21 ① Multiple-flow direct-release zone (SM only)
		const mrelease = this.is_store_mgr ? `<div id="pl-multi-release-wrap"></div>` : ``;
		// v2.21 (13 Jul) Single-flow department release zone (any user; server filters)
		const deptrel = `<div id="pl-deptrel-wrap"></div>`;
		// 22 Jul — department vote-to-delete card (cascade delete; server filters by recipient)
		const cvote = `<div id="pl-cascade-vote-wrap"></div>`;
		const board = `<div id="pl-board-wrap"></div>`;
		// #pl-sdist-wrap = Single-flow by-product Post Distribution (23 Jul) — rides
		// wherever the Multiple dist zone goes, so no layout-order changes needed.
		const dist = `<div id="pl-dist-wrap"></div><div id="pl-sdist-wrap"></div>`;
		const dept = `<div id="pl-dept-wrap"></div>`;
		// dept-only users (no PM/SM hats): their Add-Production + release tasks first
		if (!this.is_pm && !this.is_store_mgr) return dept + cvote + deptrel + board + dist + release + mrelease;
		// SM (and PM+SM): the human gates first; pure PM: dist/board first
		return this.is_store_mgr ? mrelease + release + cvote + deptrel + dist + board + dept
		                         : cvote + deptrel + dist + board + dept + release + mrelease;
	}

	// v2.21 premium de-clutter — "My Actions" strip: one glance = what waits for
	// YOU. Counts update as each zone's data loads; chips scroll to the section.
	update_actions() {
		const $w = this.$root.find("#pl-actions-wrap");
		if (!$w.length) return;
		const acts = [];
		if ((this._n_dept || 0) > 0) acts.push({
			n: this._n_dept, cls: "amber", target: "#pl-dept-wrap",
			l: "Add Production", s: "department" + (this._n_dept > 1 ? "s" : "") + " waiting on you" });
		if (this.is_store_mgr && (this._n_rel || 0) > 0) acts.push({
			n: this._n_rel, cls: "green", target: "#pl-sec-release",
			l: "Release", s: "material release" + (this._n_rel > 1 ? "s" : "") + " awaiting Store Manager" });
		if ((this._n_deptrel || 0) > 0) acts.push({
			n: this._n_deptrel, cls: "amber", target: "#pl-sec-deptrel",
			l: "Release (Dept)", s: "department release" + (this._n_deptrel > 1 ? "s" : "") + " awaiting your actual qty" });
		if (this.is_store_mgr && (this._n_mrel || 0) > 0) acts.push({
			n: this._n_mrel, cls: "green", target: "#pl-sec-mrelease",
			l: "Release (Connector)", s: "connector-flow release" + (this._n_mrel > 1 ? "s" : "") + " — direct Stores→WIP" });
		if ((this._n_dist || 0) > 0) acts.push({
			n: this._n_dist, cls: "violet", target: "#pl-dist-wrap",
			l: "Post Distribution", s: "run" + (this._n_dist > 1 ? "s" : "") + " to split across warehouses" });
		if ((this._n_cvote || 0) > 0) acts.push({
			n: this._n_cvote, cls: "amber", target: "#pl-sec-cvote",
			l: "Delete Vote", s: "delete request" + (this._n_cvote > 1 ? "s" : "") + " awaiting your Yes/No" });
		if ((this._n_sdist || 0) > 0) acts.push({
			n: this._n_sdist, cls: "violet", target: "#pl-sec-sdist",
			l: "Distribute By-Products", s: "run" + (this._n_sdist > 1 ? "s" : "") + " awaiting your warehouse split" });
		if (!acts.length) { $w.empty(); return; }
		$w.html(`<div class="pl-acts">` + acts.map((a) => `
			<div class="pl-act ${a.cls} glass" data-target="${a.target}" role="button" tabindex="0">
				<span class="pl-act-num">${a.n}</span>
				<span class="pl-act-lbl">${this.esc(a.l)}<small>${this.esc(a.s)}</small></span>
				<span class="pl-act-arr">&rarr;</span>
			</div>`).join("") + `</div>`);
	}

	// shared "waiting for Xh" age pill text (used by tiles, rows, strip)
	age_of(ts) {
		if (!ts) return "";
		const ms = Date.now() - new Date(String(ts).replace(" ", "T")).getTime();
		if (!(ms > 0)) return "";
		const h = Math.floor(ms / 3600000);
		return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : h >= 1 ? `${h}h` : `${Math.max(1, Math.floor(ms / 60000))}m`;
	}

	slideover_html() {
		return `
		<div class="scrim" id="pl-form-scrim"></div>
		<aside class="slideover" id="pl-slideover" role="dialog" aria-modal="true" aria-label="Add Production">
			<div class="so-head">
				<div class="so-ico">
					<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2c-2.5 4-5 6.5-5 10a5 5 0 0010 0c0-3.5-2.5-6-5-10z"/></svg>
				</div>
				<div>
					<div class="so-t">Add Production</div>
					<div class="so-s">New run &middot; Draft &middot; Production Manager</div>
				</div>
				<button class="so-close" id="pl-so-close" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
			</div>
			<div class="so-body">
				<div class="form-sec" id="pl-flow-chooser" style="display:none">
					<div class="form-sec-h"><span class="step-pill">Flow</span> Single or Multiple BOM</div>
					<div class="flow-seg" role="tablist">
						<button class="flow-seg-btn active" data-mode="single" role="tab">Single BOM</button>
						<button class="flow-seg-btn seg-multi" data-mode="multiple" role="tab">Multiple (Connector)</button>
					</div>
					<div class="scale-note" id="pl-flow-note" style="display:none">
						<span style="font-size:14px;line-height:1">&#128279;</span>
						<span><b>Multiple flow:</b> every department on the connector must first <b>run its BOM
						and Add Production</b> (they are notified + shown on the status board). Once ALL
						departments are done, the auto <b>Material Request</b> is created and the Store
						Manager releases it — then you distribute the produced output across
						<b>multiple warehouses</b>.</span>
					</div>
				</div>

				<div class="form-sec">
					<div class="form-sec-h"><span class="step-pill">Step 1</span> <span id="pl-step1-label">Select BOM</span></div>
					<div class="fld" id="pl-bom-fld">
						<label>Bill of Materials <span class="req">*</span></label>
						<select class="inp" id="pl-bom-select"><option value="">Loading BOMs&hellip;</option></select>
					</div>
					<div class="fld" id="pl-conn-fld" style="display:none">
						<label>BOM Connector <span class="req">*</span></label>
						<select class="inp" id="pl-conn-select"><option value="">Select a connector&hellip;</option></select>
					</div>
					<div id="pl-conn-depts" style="display:none"></div>
					<div class="fetched hidden" id="pl-fetched-box"></div>
				</div>

				<div class="form-sec" id="pl-step2" style="display:none">
					<div class="form-sec-h"><span class="step-pill">Step 2</span> Produced Qty &amp; By-products</div>
					<div class="fld-row">
						<div class="fld">
							<label>Produced Qty <span class="req">*</span> <span id="pl-prod-uom" class="uom-cell"></span></label>
							<input class="inp" type="number" id="pl-produced-qty" min="0" step="any" value="">
						</div>
						<div class="fld">
							<label>Batches (&times; BOM)</label>
							<input class="inp" type="text" id="pl-batch-mult" value="&mdash;" readonly>
						</div>
					</div>
					<div class="form-sec-h" style="margin-top:6px">By-products <span class="scale-badge" id="pl-bp-badge">auto-scaled</span></div>
					<table class="mat-table">
						<thead><tr><th>By-product</th><th class="r">Std / batch</th><th class="r">Qty (editable)</th><th>UOM</th><th>Warehouse (editable)</th></tr></thead>
						<tbody id="pl-bp-body"></tbody>
					</table>
					<label id="pl-bp-dist-wrap" style="display:none;align-items:flex-start;gap:8px;margin:8px 2px 0;font-size:12px;cursor:pointer;">
						<input type="checkbox" id="pl-bp-dist" style="margin-top:2px;">
						<span><b>Post-distribute by-products</b> — after the Store-Manager release, pause so you can split each by-product across multiple warehouses (one Manufacture entry posts the split).</span>
					</label>
				</div>

				<div class="form-sec" id="pl-step3" style="display:none">
					<div class="form-sec-h"><span class="step-pill">Step 3</span> Raw Material &mdash; auto-scaled from BOM <span class="scale-badge" id="pl-rm-badge">&times; 1.0</span></div>
					<table class="mat-table">
						<thead><tr><th>Item</th><th class="r">Std / batch</th><th class="r">Release Qty</th><th>UOM</th><th class="c">Remove</th></tr></thead>
						<tbody id="pl-rm-body"></tbody>
					</table>
					<div class="mat-foot">
						<span class="std-q" id="pl-rm-count"></span>
						<button class="reset-line" id="pl-reset-rm">
							<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 109-9 9 9 0 00-6.4 2.6L3 8"/><path d="M3 3v5h5"/></svg>
							Reset from BOM
						</button>
					</div>
					<div class="scale-note">
						<span style="font-size:14px;line-height:1">&#9881;</span>
						<span>Raw material is <b>auto-calculated from the BOM &times; produced qty</b>. You may edit a quantity or remove a line &mdash; production releases AND consumes your ACTUAL quantities. By-product quantities &amp; warehouses are editable below.</span>
					</div>
				</div>
			</div>
			<div class="so-foot">
				<button class="cancel-btn" id="pl-cancel-btn">Cancel</button>
				<button class="submit-btn" id="pl-submit-btn" disabled>
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
					Submit for Release & Department
				</button>
			</div>
		</aside>`;
	}

	progress_html() {
		return `
		<div class="prog-scrim" id="pl-prog-scrim" role="dialog" aria-modal="true" aria-labelledby="pl-prog-title">
			<div class="prog-card glass">
				<div class="prog-head">
					<div class="prog-ico" id="pl-prog-ico">
						<svg class="prog-spin" id="pl-prog-ico-svg" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round"><path d="M21 12a9 9 0 11-6.2-8.6" opacity=".9"/></svg>
					</div>
					<div>
						<div class="prog-title" id="pl-prog-title">Working&hellip;</div>
						<div class="prog-sub" id="pl-prog-sub">Please wait &mdash; automation in progress</div>
					</div>
				</div>
				<div class="prog-bar-wrap">
					<div class="prog-bar"><div class="prog-fill" id="pl-prog-fill"></div></div>
					<div class="prog-pct" id="pl-prog-pct">0%</div>
				</div>
				<div class="prog-status" id="pl-prog-status" aria-live="polite" role="status"><span class="ps-dot"></span><span id="pl-prog-status-text">Starting&hellip;</span></div>
				<ul class="prog-steps" id="pl-prog-steps"></ul>
				<div class="prog-err-box" id="pl-prog-err"></div>
				<div class="prog-foot" id="pl-prog-foot">Auto-closes when complete</div>
				<button class="prog-close-btn" id="pl-prog-close">Close</button>
			</div>
		</div>`;
	}

	// ── Static bindings (delegated; survive re-renders of data zones) ───
	bind_static() {
		const self = this;
		this.$root.on("click", "#pl-theme-btn", () => self.toggle_theme());

		if (this.is_pm) {
			this.$root.on("click", "#pl-add-btn", () => self.open_form());
			this.$root.on("click", "#pl-so-close, #pl-cancel-btn, #pl-form-scrim", () => self.close_form());
			this.$root.on("change", "#pl-bom-select", (e) => self.on_bom_change(e.target.value));
			// Phase D — flow chooser + connector picker + distribution action
			this.$root.on("click", ".flow-seg-btn", function () {
				self.on_flow_change($(this).data("mode"));
			});
			this.$root.on("change", "#pl-conn-select", (e) => self.on_connector_change(e.target.value));
			this.$root.on("click", ".pl-sdist-btn", function () {
			self.open_distribution_dialog($(this).data("name"), true);
		});
		this.$root.on("click", ".pl-dist-btn", function () {
				self.open_distribution_dialog($(this).data("name"));
			});
			this.$root.on("input", "#pl-produced-qty", () => self.on_produced_change());
			this.$root.on("input", "#pl-rm-body input.qty", function () {
				self.edit_rm(parseInt($(this).data("idx"), 10), this.value);
			});
			this.$root.on("click", "#pl-rm-body .rm-x", function () {
				self.toggle_rm(parseInt($(this).data("idx"), 10));
			});
			this.$root.on("click", "#pl-reset-rm", () => self.reset_rm());
			this.$root.on("click", "#pl-submit-btn", () => self.submit_for_release());
		}

		// Store-Manager actions (delegated on the release zone)
		// v2.21 (13 Jul) — department release with actuals
		this.$root.on("click", ".pl-deptrel-btn", function () {
			self.open_release_dialog($(this).data("i"));
		});
		// 22 Jul — department vote-to-delete Yes/No
		this.$root.on("click", ".pl-cvote-btn", function () {
			self.cast_cascade_vote($(this).data("i"), $(this).data("d"));
		});
		// v2.21 UAT ③ — dept user corrects their own Logged entry (reopen for re-entry)
		this.$root.on("click", ".pl-reopen-btn", function () {
			const name = $(this).data("name");
			frappe.prompt({
				fieldname: "reason", fieldtype: "Small Text", reqd: 1,
				label: __("Why are you correcting this entry? (min 10 characters)"),
			}, (v) => {
				if ((v.reason || "").trim().length < 10) {
					frappe.msgprint(__("Please give a reason of at least 10 characters."));
					return;
				}
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.ts_production_dept.cancel_department_entry",
					type: "POST", args: { name: name, reason: v.reason },
					callback: (r) => {
						if (r.message && r.message.ok) {
							frappe.show_alert({ message: __("Reopened — it is back in your Add Production list."), indicator: "orange" });
							self.reload_data();
						}
					},
				});
			}, __("Correct / Reopen"), __("Reopen"));
		});
		// v2.21 ① one-click Multiple-flow direct release
		this.$root.on("click", ".pl-mrelease-btn", function () {
			const name = $(this).data("name");
			const $btn = $(this);
			frappe.confirm(
				__("Release raw material for {0}? This transfers directly Stores → Work In Progress (no in-transit step) and moves the run to Post Distribution.", [name]),
				() => {
					$btn.prop("disabled", true);
					frappe.call({
						method: "trustbit_ethanol.ts_gate_entry.ts_production_multi.release_multi_run",
						type: "POST", args: { name: name },
						callback: (r) => {
							if (r.message && r.message.ok) {
								frappe.show_alert({
									message: r.message.already_released
										? __("Already released — {0}.", [name])
										: __("Released {0} — material moved Stores → WIP; ready for distribution.", [name]),
									indicator: "green" });
								self.reload_data();
							} else {
								$btn.prop("disabled", false);
							}
						},
						error: () => $btn.prop("disabled", false),
					});
				});
		});
		this.$root.on("click", ".pl-approve-btn", function () {
			self.approve_release($(this).data("name"));
		});
		this.$root.on("click", ".pl-reject-btn", function () {
			self.reject_release($(this).data("name"));
		});

		// Department production (v2.21 — Add Production gate)
		this.$root.on("click", ".pl-deptlog-btn", function () {
			self.open_dept_dialog(parseInt($(this).data("idx"), 10));
		});
		// v2.21 — Administrator-only skip valve on the status board (U3)
		this.$root.on("click", ".pl-skip-btn", function () {
			self.open_skip_dialog($(this).data("name"), $(this).data("cat"));
		});
		// v2.21 premium — My-Actions chips scroll to their section
		this.$root.on("click keydown", ".pl-act", function (e) {
			if (e.type === "keydown") {
				if (e.key !== "Enter" && e.key !== " ") return;
				e.preventDefault();
			}
			const t = self.$root.find($(this).data("target"));
			if (t.length) t[0].scrollIntoView({ behavior: "smooth", block: "start" });
		});
		// v2.21 premium — row "details" expand-on-demand
		this.$root.on("click", ".pl-more", function () {
			const $x = $(this).closest(".pl-row").next(".pl-xpand");
			$x.toggle();
			$(this).html($x.is(":visible") ? "details &#9662;" : "details &#9656;");
		});
		// v2.21 premium — "Older (N)" queue folds
		this.$root.on("click", ".pl-fold", function () {
			const $b = $(this).next(".pl-fold-body");
			$b.toggle();
			$(this).find(".pl-fold-chev").html($b.is(":visible") ? "&#9662;" : "&#9656;");
		});
		// v2.21 — department tile click = toggle the board's department filter
		this.$root.on("click keydown", ".pl-dept-tile", function (e) {
			if (e.type === "keydown") {
				if (e.key !== "Enter" && e.key !== " ") return;
				e.preventDefault(); // Space must not scroll the page (a11y)
			}
			const cat = $(this).data("cat");
			self._board_cat_filter = self._board_cat_filter === cat ? null : cat;
			if (self._board_data) self.render_board(self._board_data);
		});

		this.$root.on("click", "#pl-prog-close", () => self.close_progress());

		$(document).on("keydown.prodlog", (e) => {
			if (e.key === "Escape") self.close_form();
		});
	}

	// ── DATA LOAD ──────────────────────────────────────────────────────
	load_settings_then_data() {
		frappe.call({
			method: `${PL_API}.get_production_settings`,
			callback: (r) => {
				this.settings = r.message || {};
				this.render_feature_tag();
				this.reload_data();
				if (this.is_pm) this.load_boms();
			},
			error: () => {
				this.settings = {};
				this.render_feature_tag();
				this.reload_data();
				if (this.is_pm) this.load_boms();
			},
		});
	}

	render_feature_tag() {
		const enabled = this.settings && this.settings.enabled;
		const $tag = this.$root.find("#pl-feature-tag");
		if (enabled) {
			$tag.html('<span class="live-tag"><span class="pulse-dot"></span> Production flow ENABLED</span>');
		} else {
			$tag.html('<span class="off-tag">Feature ships OFF &middot; kill switch <b>ts_production_entry_enabled</b></span>');
		}
	}

	reload_data() {
		if (this.is_store_mgr) this.load_multi_releases();
		this.load_log();
		if (this.is_store_mgr) this.load_pending_releases();
		this.load_dept_context();  // Phase C — server decides visibility (kill switch + recipients)
		this.load_my_release_slots(); // v2.21 (13 Jul) — Single-flow department release
		this.load_my_cascade_votes(); // 22 Jul — department vote-to-delete card
		if (this.is_pm) this.load_single_distributions(); // 23 Jul — by-product Post Distribution
		this.load_multi_context(); // Phase D — chooser + pending distributions
		this.load_multi_board();   // v2.21 — dept status board (R3)
	}

	// ── v2.21 (13 Jul) — Single-flow DEPARTMENT RELEASE ─────────────────
	load_my_release_slots() {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_production_dept_release.get_my_release_slots",
			callback: (r) => this.render_release_slots(r.message || []),
			error: () => this.render_release_slots([]),
		});
	}

	render_release_slots(slots) {
		const $wrap = this.$root.find("#pl-deptrel-wrap");
		if (!$wrap.length) return;
		this._n_deptrel = slots.length;
		this.update_actions();
		this.release_slots = slots;
		if (!slots.length) { $wrap.empty(); return; }
		const row = (s, i) => `
			<div class="pl-row amber">
				<span class="pl-rid">${this.esc(s.production_entry)}</span>
				<span class="pl-rwhat"><b>${this.esc(s.category)}</b>
					<span class="dim">· ${(s.items || []).length} item(s) to release · dept ${this.esc(s.department || "—")}</span></span>
				<span class="pl-pill age">notified ${this.age_of(s.notified_at) || "—"} ago</span>
				<span class="pl-sp"></span><span class="pl-more">details &#9656;</span>
				<button class="btn btn-approve btn-sm pl-deptrel-btn" data-i="${i}">&#128666; Release with Actuals</button>
			</div>
			<div class="pl-xpand" style="display:none">
				${(s.items || []).map((it) => `<span><b>${this.esc(it.item_name || it.item_code)}</b> — planned ${this.fmt1(it.planned_qty)} ${this.esc(it.uom || "")} from ${this.esc(it.warehouse || "—")}</span>`).join("")}
				<span><b>Note</b> enter the ACTUAL quantity your department used; production consumes your actuals, and the Store Manager release runs after all departments release.</span>
			</div>`;
		$wrap.html(`
			<div class="sec-title" id="pl-sec-deptrel">
				<span class="bar" style="background:var(--amber)"></span> Release &mdash; Your Department (Single Flow)
				<span class="feas-tag tag-gate">&#128666; Enter actual used qty</span>
			</div>
			<div class="glass pl-rows">${slots.map(row).join("")}</div>`);
	}

	open_release_dialog(i) {
		const s = (this.release_slots || [])[i];
		if (!s) return;
		const rows = (s.items || []).map((it, j) => `
			<tr>
				<td style="padding:6px 8px;">${frappe.utils.escape_html(it.item_name || it.item_code)}<br>
					<span style="font-size:11px;opacity:.6;font-family:monospace">${frappe.utils.escape_html(it.item_code)} · ${frappe.utils.escape_html(it.warehouse || "")}</span></td>
				<td style="padding:6px 8px;text-align:right;opacity:.7">${flt(it.planned_qty).toLocaleString("en-IN")}</td>
				<td style="padding:6px 8px;"><input type="number" min="0" step="any" class="form-control pl-rel-qty" data-j="${j}" value="${flt(it.planned_qty)}" style="text-align:right"></td>
				<td style="padding:6px 8px;">${frappe.utils.escape_html(it.uom || "")}</td>
			</tr>`).join("");
		const self = this;
		const d = new frappe.ui.Dialog({
			title: __("Release — {0} ({1})", [s.category, s.production_entry]),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "rel_html" }],
			primary_action_label: __("Release with Actuals"),
			primary_action: () => {
				const items = (s.items || []).map((it, j) => ({
					item_code: it.item_code,
					actual_qty: flt(d.$wrapper.find(`.pl-rel-qty[data-j="${j}"]`).val()),
				}));
				d.get_primary_btn().prop("disabled", true);
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.ts_production_dept_release.release_department_slot",
					type: "POST", args: { name: s.name, items: items },
					callback: (r) => {
						d.hide();
						if (r.message && r.message.ok) {
							frappe.show_alert({
								message: (r.message.remaining && r.message.remaining.length)
									? __("Released. Waiting for the other departments before the Store Manager can release.")
									: __("Released — all departments done; the Store Manager can now release."),
								indicator: "green" });
							self.reload_data();
						}
					},
					error: () => d.get_primary_btn().prop("disabled", false),
				});
			},
		});
		d.fields_dict.rel_html.$wrapper.html(`
			<div style="margin:4px 0 6px;font-size:12px"><b>${frappe.utils.escape_html(s.category)}</b> · enter the ACTUAL quantity your department used (0 = not used). Production consumes your actuals.</div>
			<table style="width:100%;border-collapse:collapse;font-size:12.5px">
				<thead><tr style="opacity:.65;text-align:left">
					<th style="padding:6px 8px;">Item · Warehouse</th><th style="padding:6px 8px;text-align:right">Planned</th>
					<th style="padding:6px 8px;">Actual Used</th><th style="padding:6px 8px;">UOM</th>
				</tr></thead><tbody>${rows}</tbody>
			</table>`);
		d.show();
	}

	// ── 22 Jul — DEPARTMENT VOTE-TO-DELETE (cascade delete) ─────────────
	load_my_cascade_votes() {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.production_cascade_api.get_my_cascade_votes",
			callback: (r) => this.render_cascade_votes(r.message || []),
			error: () => this.render_cascade_votes([]),
		});
	}

	render_cascade_votes(votes) {
		const $wrap = this.$root.find("#pl-cascade-vote-wrap");
		if (!$wrap.length) return;
		this._n_cvote = votes.length;
		this.update_actions();
		this.cascade_votes = votes;
		if (!votes.length) { $wrap.empty(); return; }
		const row = (v, i) => `
			<div class="pl-row" style="border-left:3px solid var(--red,#dc2626)">
				<span class="pl-rid">${this.esc(v.production)}</span>
				<span class="pl-rwhat"><b>${this.esc(v.category)}</b>
					<span class="dim">· deletion requested by ${this.esc(v.initiated_by)}</span></span>
				<span class="pl-pill age">confirm removal of your department's records</span>
				<span class="pl-sp"></span>
				<button class="btn btn-approve btn-sm pl-cvote-btn" data-i="${i}" data-d="Yes">&#10003; Yes, delete ours</button>
				<button class="btn btn-danger btn-sm pl-cvote-btn" data-i="${i}" data-d="No">&#10007; No, keep ours</button>
			</div>`;
		$wrap.html(`
			<div class="sec-title" id="pl-sec-cvote">
				<span class="bar" style="background:var(--red,#dc2626)"></span> Delete Requests &mdash; Confirm Removal of Your Department's Entries
				<span class="feas-tag tag-gate">&#128465; Vote Yes / No</span>
			</div>
			<div class="glass pl-rows">${votes.map(row).join("")}</div>`);
	}

	cast_cascade_vote(i, decision) {
		const v = (this.cascade_votes || [])[i];
		if (!v) return;
		const self = this;
		frappe.confirm(
			decision === "Yes"
				? __("Vote YES to delete your department's entries for {0}? The CEO makes the final call.", [v.production])
				: __("Vote NO to keep your department's entries for {0}? The CEO can still override.", [v.production]),
			() => {
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.production_cascade_api.cascade_department_vote",
					type: "POST",
					args: { log_name: v.log_name, category: v.category, decision: decision },
					callback: (r) => {
						if (r.message && r.message.success) {
							frappe.show_alert({ message: __("Vote recorded: {0}", [decision]), indicator: "green" });
							self.load_my_cascade_votes();
						}
					},
				});
			}
		);
	}

	// ── Phase D — Multiple (BOM Connector) flow ─────────────────────────
	load_multi_context() {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_production_multi.get_multi_context",
			callback: (r) => {
				this.multi_ctx = r.message || { enabled: false, connectors: [] };
				this.render_multi_ui();
				if (this.multi_ctx.enabled && this.is_pm && this.multi_ctx.authorized !== false) this.load_pending_distributions();
				else this.$root.find("#pl-dist-wrap").empty();
			},
			error: () => {
				this.multi_ctx = { enabled: false, connectors: [] };
				this.render_multi_ui();
			},
		});
	}

	render_multi_ui() {
		const on = !!(this.multi_ctx && this.multi_ctx.enabled &&
			(this.multi_ctx.connectors || []).length);
		this.$root.find("#pl-flow-chooser").toggle(on && this.is_pm && this.multi_ctx.authorized !== false);
		const $sel = this.$root.find("#pl-conn-select");
		if (on && $sel.length) {
			$sel.html('<option value="">Select a connector…</option>' +
				(this.multi_ctx.connectors || []).map((c) =>
					`<option value="${this.esc(c.name)}">${this.esc(c.name)} — ${this.esc(c.main_bom_item || c.main_bom)} (${(c.department_boms || []).length} dept)</option>`
				).join(""));
		}
	}

	on_flow_change(mode) {
		if (this.busy) return;
		this.flow_mode = mode === "multiple" ? "multiple" : "single";
		const multi = this.flow_mode === "multiple";
		this.$root.find(".flow-seg-btn").removeClass("active")
			.filter(`[data-mode="${this.flow_mode}"]`).addClass("active");
		this.$root.find("#pl-flow-note").toggle(multi);
		this.$root.find("#pl-bom-fld").toggle(!multi);
		this.$root.find("#pl-conn-fld").toggle(multi);
		this.$root.find("#pl-conn-depts").toggle(multi).empty();
		this.$root.find("#pl-step1-label").text(multi ? "Select BOM Connector" : "Select BOM");
		this.$root.find("#pl-submit-btn").html(multi
			? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg> Submit — notify departments'
			: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg> Submit for Release & Department');
		// reset the working state — the two flows must never share a half-built form
		this.bom = null; this.bom_std = null; this.connector = null;
		this.rm_state = []; this.bp_state = [];
		this.$root.find("#pl-bp-dist").prop("checked", false);  // opt-in resets per form open
		this.$root.find("#pl-bom-select").val("");
		this.$root.find("#pl-conn-select").val("");
		this.$root.find("#pl-fetched-box").addClass("hidden").empty();
		this.$root.find("#pl-step2, #pl-step3").hide();
		this.recompute_submit_enabled();
	}

	on_connector_change(name) {
		this.connector = (this.multi_ctx && (this.multi_ctx.connectors || [])
			.find((c) => c.name === name)) || null;
		const $depts = this.$root.find("#pl-conn-depts");
		if (!this.connector) {
			$depts.hide().empty();
			this.bom = null; this.bom_std = null;
			this.$root.find("#pl-step2, #pl-step3").hide();
			this.recompute_submit_enabled();
			return;
		}
		const lines = this.connector.department_boms || [];
		$depts.show().html(
			`<div class="conn-dept-list">` +
			`<div class="cdl-h">Department BOMs — <b>must Add Production (gates the release)</b></div>` +
			(lines.length ? lines.map((l) =>
				`<div class="cdl-row"><span class="mono">${this.esc(l.bom)}</span>` +
				`<span>${this.esc(l.category)}</span>` +
				`<span class="cdl-tag">notified &middot; must add production</span></div>`).join("")
				: `<div class="cdl-row" style="opacity:.6">No department BOMs on this connector — release Material Request is created immediately.</div>`) +
			`</div>`);
		// the connector's MAIN BOM drives materials/by-products exactly like Single
		this.on_bom_change(this.connector.main_bom);
	}

	load_log() {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: PL_DOCTYPE,
				fields: [
					"name", "bom", "production_item", "production_item_name",
					"actual_produced_qty", "production_uom", "ts_variance_status",
					"material_variance_pct", "produced_variance_pct", "variance_breach",
					"work_order", "linked_stock_entry", "release_stock_entry", "modified",
				],
				order_by: "modified desc",
				limit_page_length: 30,
			},
			callback: (r) => {
				const rows = r.message || [];
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.ts_production_dept_release.pending_department_map",
					callback: (m) => { this._pendDept = m.message || {}; this.render_log(rows); this.render_kpis(rows); },
					error: () => { this._pendDept = {}; this.render_log(rows); this.render_kpis(rows); },
				});
			},
			error: () => {
				this.render_log([]);
				this.render_kpis([]);
			},
		});
	}

	load_pending_releases() {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_production_dept_release.get_store_release_queue",
			callback: (r) => this.render_release_zone(r.message || []),
			error: () => this.render_release_zone([]),
		});
	}

	// ── v2.21 ① Multiple-flow release — direct Stores→WIP, one click ────
	load_multi_releases() {
		if (!this.is_store_mgr) return;
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: PL_DOCTYPE,
				filters: { ts_variance_status: "Pending Material Request", flow_type: "Multiple" },
				fields: ["name", "bom", "production_item_name", "actual_produced_qty",
					"production_uom", "material_request", "work_order", "bom_connector", "modified"],
				order_by: "modified desc", limit_page_length: 20,
			},
			callback: (r) => this.render_multi_release_zone(r.message || []),
			error: () => this.render_multi_release_zone([]),
		});
	}

	render_multi_release_zone(rows) {
		const $wrap = this.$root.find("#pl-multi-release-wrap");
		if (!$wrap.length) return;
		this._n_mrel = rows.length;
		this.update_actions();
		if (!rows.length) { $wrap.empty(); return; }
		const row = (r) => `
			<div class="pl-row green">
				<span class="pl-rid">${this.esc(r.name)}</span>
				<span class="pl-rwhat">${this.esc(r.production_item_name || "")} · <b>${this.fmt1(r.actual_produced_qty)} ${this.esc(r.production_uom || "")}</b>
					<span class="dim">· MR ${this.esc(r.material_request || "—")} · ${this.esc(r.bom_connector || "")}</span></span>
				<span class="pl-pill age">waiting ${this.age_of(r.modified) || "—"}</span>
				<span class="pl-sp"></span><span class="pl-more">details &#9656;</span>
				<button class="btn btn-approve btn-sm pl-mrelease-btn" data-name="${this.esc(r.name)}">
					<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg> Release</button>
			</div>
			<div class="pl-xpand" style="display:none">
				<span><b>Work Order</b> <span class="mono">${this.esc(r.work_order || "—")}</span></span>
				<span><b>On Release</b> the raw material transfers <b>directly Stores &rarr; Work In Progress</b> (no in-transit step), then the run moves to Post Distribution.</span>
			</div>`;
		$wrap.html(`
			<div class="sec-title" id="pl-sec-mrelease">
				<span class="bar" style="background:var(--amber)"></span>
				Store Manager Release &mdash; Multiple (Connector) Flow
				<span class="feas-tag tag-gate">&#128682; Direct Stores &rarr; WIP</span>
			</div>
			<div class="glass pl-rows">${rows.map(row).join("")}</div>`);
	}

	// ── Phase C — Department consumption (REPORTING ONLY, no stock) ─────
	load_dept_context() {
		this.load_bom_names();
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_production_dept.get_department_context",
			callback: (r) => {
				this.dept_ctx = r.message || { enabled: false, categories: [], pending: [] };
				this.render_dept_zone();
			},
			error: () => {
				this.dept_ctx = { enabled: false, categories: [], pending: [] };
				this.render_dept_zone();
			},
		});
	}

	render_dept_zone() {
		const $wrap = this.$root.find("#pl-dept-wrap");
		if (!$wrap.length) return;
		const ctx = this.dept_ctx || {};
		if (!ctx.enabled || !(ctx.categories || []).length) {
			this._n_dept = 0;
			this.update_actions();
			$wrap.empty();
			return;
		}
		const pending = ctx.pending || [];
		this._n_dept = pending.length;
		this.update_actions();
		let inner;
		if (!pending.length) {
			inner = `<div class="panel glass dept-empty">&#9989; No production runs are waiting for your department &mdash; when a Production Manager submits a Multiple run using your category, your Add Production task appears here.</div>`;
		} else {
			inner = `<div class="glass pl-rows">` + pending.map((p, i) => `
				<div class="pl-row green">
					<span class="pl-rid">${this.esc(p.production_entry)}</span>
					<span class="pl-rwhat"><b>${this.esc(p.category)}</b>
						<span class="dim">· dept BOM ${this.esc(this.bom_label(p.dept_bom))} · main: ${this.esc(p.item_name || "—")} ${this.fmt1(p.produced_qty)} ${this.esc(p.uom || "")}</span></span>
					<span class="pl-pill age">notified ${this.age_of(p.notified_at) || "—"} ago</span>
					<span class="pl-sp"></span><span class="pl-more">details &#9656;</span>
					<button class="btn btn-approve btn-sm pl-deptlog-btn" data-idx="${i}">&#127981; Add Production</button>
				</div>
				<div class="pl-xpand" style="display:none">
					<span><b>Department</b> ${this.esc(p.department || "—")}</span>
					<span><b>Notified</b> ${this.esc((p.notified_at || "").slice(0, 16) || "—")}</span>
					<span><b>Date</b> ${this.esc(p.posting_date || "—")}</span>
					<span><b>Note</b> the Store-Manager release waits for this · actuals-only, no stock moves</span>
					<a href="/app/ts-production-entry/${encodeURIComponent(p.production_entry)}" target="_blank" rel="noopener">View Run &rarr;</a>
				</div>`).join("") + `</div>`;
		}
		// v2.21 UAT ③ — your own still-correctable Logged entries (run waiting, no MR yet)
		const reopen = ctx.reopenable || [];
		let fixer = "";
		if (reopen.length) {
			fixer = `<div class="sec-title" style="margin-top:14px">
					<span class="bar" style="background:var(--amber)"></span> Logged by You &mdash; Still Correctable
					<span class="feas-tag tag-gate">&#9998; Reopens for re-entry</span>
				</div>
				<div class="glass pl-rows">` + reopen.map((p) => `
				<div class="pl-row amber">
					<span class="pl-rid">${this.esc(p.production_entry)}</span>
					<span class="pl-rwhat"><b>${this.esc(p.category)}</b>
						<span class="dim">· logged ${this.age_of(p.logged_at) || "—"} ago · main: ${this.esc(p.item_name || "—")} ${this.fmt1(p.produced_qty)} ${this.esc(p.uom || "")}</span></span>
					<span class="pl-sp"></span><span class="pl-more">details &#9656;</span>
					<button class="btn btn-reopen btn-sm pl-reopen-btn" data-name="${this.esc(p.dept_entry)}">&#9998; Correct / Reopen</button>
				</div>
				<div class="pl-xpand" style="display:none">
					<span><b>Department</b> ${this.esc(p.department || "—")}</span>
					<span><b>Reopen</b> puts this back in your Add Production list to re-enter the quantities. Possible only until the Store-Manager release is requested.</span>
				</div>`).join("") + `</div>`;
		}
		$wrap.html(`
			<div class="sec-title">
				<span class="bar" style="background:var(--green)"></span> Add Production &mdash; Your Departments
				<span class="feas-tag tag-gate">&#127981; Gates the Store-Manager release</span>
			</div>
			${inner}${fixer}`);
	}

	open_dept_dialog(idx) {
		const p = ((this.dept_ctx || {}).pending || [])[idx];
		if (!p) return;
		const rows = (p.materials || []).map((m, i) => `
			<tr>
				<td style="padding:6px 8px;">${frappe.utils.escape_html(m.item_name || m.item_code)}<br>
					<span style="font-size:11px;opacity:.6;font-family:monospace">${frappe.utils.escape_html(m.item_code)}</span></td>
				<td style="padding:6px 8px;text-align:right;opacity:.7">${flt(m.std_qty).toLocaleString("en-IN")}</td>
				<td style="padding:6px 8px;"><input type="number" min="0" step="any" class="form-control pl-dd-qty" data-i="${i}" value="${flt(m.std_qty)}" style="text-align:right"></td>
				<td style="padding:6px 8px;">${frappe.utils.escape_html(m.uom || "")}</td>
				<td style="padding:6px 8px;"><input type="text" class="form-control pl-dd-remark" data-i="${i}" maxlength="140"></td>
			</tr>`).join("");
		// Fresh Dialog per click — never an inline page sibling (Lesson 296).
		const d = new frappe.ui.Dialog({
			title: __("Add Production — {0}", [p.production_entry]),
			size: "large",
			fields: [
				{ fieldtype: "Date", fieldname: "posting_date", label: __("Posting Date"),
				  default: frappe.datetime.now_date(), reqd: 1 },
				{ fieldtype: "Data", fieldname: "remark", label: __("Remark") },
				{ fieldtype: "HTML", fieldname: "mat_html" },
			],
			primary_action_label: __("Add Production"),
			primary_action: (values) => this.submit_dept_entry(d, p, values),
		});
		// v2.21 SE Style — the note + (for produce-style depts) the output field
		const produce = p.se_style === "Consume + Produce Output" && p.output;
		const note = produce
			? __("&#9432; Stock WILL move: materials are issued AND {0} is received into stock", [frappe.utils.escape_html(p.output.item_name || "")])
			: p.se_style === "Consumption Only"
			? __("&#9432; Stock WILL move: these materials are issued from the store to your department")
			: __("&#9432; Actuals-only — no stock will move; the release waits for this");
		const outHtml = produce ? `
			<div style="margin:10px 0 2px;padding:8px;border:1px dashed var(--amber,#d97706);border-radius:8px;font-size:12.5px">
				<b>&#127981; Output produced — ${frappe.utils.escape_html(p.output.item_name || p.output.item_code)}</b>
				<span style="opacity:.6;font-family:monospace">(${frappe.utils.escape_html(p.output.item_code)})</span><br>
				<span style="opacity:.65;font-size:11.5px">Standard: ${flt(p.output.std_qty).toLocaleString("en-IN")} ${frappe.utils.escape_html(p.output.uom || "")} — enter the ACTUAL produced quantity:</span>
				<input type="number" min="0" step="any" class="form-control pl-dd-output" value="${flt(p.output.std_qty)}" style="text-align:right;max-width:220px;display:inline-block;margin-left:8px"> ${frappe.utils.escape_html(p.output.uom || "")}
			</div>` : "";
		d.fields_dict.mat_html.$wrapper.html(`
			<div style="margin:4px 0 6px;font-size:12px;"><b>${frappe.utils.escape_html(p.category)}</b> &middot; dept BOM <span style="font-family:monospace">${frappe.utils.escape_html(this.bom_label(p.dept_bom))}</span>
			&middot; <span style="font-weight:600">${note}</span></div>
			<table style="width:100%;border-collapse:collapse;font-size:12.5px">
				<thead><tr style="opacity:.65;text-align:left">
					<th style="padding:6px 8px;">Material</th><th style="padding:6px 8px;text-align:right">Std Qty</th>
					<th style="padding:6px 8px;">Actual Qty</th><th style="padding:6px 8px;">UOM</th><th style="padding:6px 8px;">Remark</th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table>${outHtml}`);
		d.show();
	}

	submit_dept_entry(d, p, values) {
		const mats = (p.materials || []).map((m, i) => ({
			item_code: m.item_code, std_qty: m.std_qty, uom: m.uom,
			qty: flt(d.$wrapper.find(`.pl-dd-qty[data-i="${i}"]`).val()),
			remark: (d.$wrapper.find(`.pl-dd-remark[data-i="${i}"]`).val() || ""),
		})).filter((m) => m.qty > 0);
		if (!mats.length) {
			frappe.show_alert({ message: __("Enter a quantity for at least one material."), indicator: "orange" });
			return;
		}
		let output_qty = null;
		if (p.se_style === "Consume + Produce Output" && p.output) {
			output_qty = flt(d.$wrapper.find(".pl-dd-output").val());
			if (!(output_qty > 0)) {
				frappe.show_alert({ message: __("Enter the produced output quantity ({0}).", [p.output.item_name || ""]), indicator: "orange" });
				return;
			}
		}
		d.get_primary_btn().prop("disabled", true);
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_production_dept.submit_department_production",
			type: "POST",
			args: {
				name: p.dept_entry,
				materials: mats, posting_date: values.posting_date, remark: values.remark || "",
				output_qty: output_qty,
			},
			callback: (r) => {
				d.hide();
				if (r.message && r.message.ok) {
					frappe.show_alert({
						message: r.message.mr_created
							? __("Production added — all departments done! Material Request {0} routed to the Store Manager.", [r.message.material_request || ""])
							: __("Production added — {0}. Waiting for the other departments.", [r.message.name]),
						indicator: "green",
					});
					this.load_dept_context();
					this.load_multi_board();
				}
			},
			error: () => d.get_primary_btn().prop("disabled", false),
		});
	}

	// ── v2.21 — Department Production Status board (R3: PM/Grain PM/CEO/MD/SM) ──
	load_multi_board() {
		if (!this.is_board) return;
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_production_dept.get_multi_run_board",
			callback: (r) => this.render_board(r.message || { enabled: false, runs: [] }),
			error: () => {
				// distinguish a failed call from "feature disabled" (ui-designer LOW-2)
				const $wrap = this.$root.find("#pl-board-wrap");
				if ($wrap.length) $wrap.html(`
					<div class="sec-title"><span class="bar" style="background:var(--amber)"></span> Department Production Status</div>
					<div class="panel glass dept-empty">&#9888;&#65039; Couldn't load the status board — reload the page to retry.</div>`);
			},
		});
	}

	render_board(data) {
		const $wrap = this.$root.find("#pl-board-wrap");
		if (!$wrap.length) return;
		if (!data.enabled) { $wrap.empty(); return; }
		this._board_data = data; // kept for tile-filter re-renders
		const runs = data.runs || [];
		let waiting = runs.filter((r) => r.ts_variance_status === "Awaiting Department Production");
		const dept_tiles = this.dept_tiles_html(waiting);
		if (this._board_cat_filter) {
			waiting = waiting.filter((r) => (r.departments || [])
				.some((s) => s.category === this._board_cat_filter && s.status === "Pending"));
		}
		const rest = runs.filter((r) => r.ts_variance_status !== "Awaiting Department Production").slice(0, 5);
		const pillOf = (s) => {
			if (s.status === "Logged")
				return `<span class="pl-pill ok" title="${this.esc(s.submitted_by || "")} · ${this.esc((s.logged_at || "").slice(0, 16))}">&#10003; ${this.esc(s.category)}</span>`;
			if (s.status === "Skipped")
				return `<span class="pl-pill skip" title="by Administrator · ${this.esc((s.logged_at || "").slice(0, 16))}">&#10539; ${this.esc(s.category)}</span>`;
			const skipBtn = this.is_administrator
				? ` <button class="pl-skip-btn" data-name="${this.esc(s.name)}" data-cat="${this.esc(s.category)}" title="Administrator only">Skip</button>`
				: "";
			const stg = cint(s.reminder_stage);
			const stageTag = !stg ? "" : stg <= 3 ? ` · L${stg}` : stg === 4 ? " · escalated" : ` · #${stg - 1}`;
			return `<span class="pl-pill age" title="${stg ? this.esc("last reminded " + ((s.last_reminded_at || "").slice(0, 16))) : ""}">&#8987; ${this.esc(s.category)} · ${this.age_of(s.notified_at) || "—"}${stageTag}</span>${skipBtn}`;
		};
		const card = (r) => {
			const total = (r.departments || []).length;
			const done = total - (r.pending_count || 0);
			const pct = total ? Math.round((done / total) * 100) : 0;
			return `
				<div class="pl-row amber">
					<span class="pl-rid">${this.esc(r.name)}</span>
					<span class="pl-rwhat">${(r.departments || []).map(pillOf).join(" ") || '<span class="dim">No department entries.</span>'}</span>
					<span class="pl-sp"></span>
					<span class="pl-prog"><span class="pl-pbar"><i style="width:${pct}%"></i></span><span class="pl-ptxt">${done} of ${total}</span></span>
					<span class="pl-more">details &#9656;</span>
				</div>
				<div class="pl-xpand" style="display:none">
					<span><b>Item</b> ${this.esc(r.production_item_name || "")} · ${this.fmt1(r.actual_produced_qty)} ${this.esc(r.production_uom || "")}</span>
					<span><b>Connector</b> ${this.esc(r.bom_connector || "—")}</span>
					<span><b>Status</b> ${this.esc(r.ts_variance_status)}</span>
					<span>${r.material_request ? `<b>MR</b> <span class="mono">${this.esc(r.material_request)}</span>` : "<b>Note</b> the Store-Manager release fires automatically when all departments complete"}</span>
					${(r.departments || []).filter((s) => s.status !== "Pending").map((s) =>
						`<span><b>${this.esc(s.category)}</b> ${s.status === "Skipped" ? "skipped" : "by " + this.esc(s.submitted_by || "—")} · ${this.esc((s.logged_at || "").slice(0, 16))}</span>`).join("")}
				</div>`;
		};
		if (!waiting.length && !rest.length && !this._board_cat_filter) {
			$wrap.html(`
				<div class="sec-title"><span class="bar" style="background:var(--amber)"></span> Department Production Status
					<span class="feas-tag tag-auto">&#128202; PM · CEO · MD · Store Manager</span></div>
				<div class="panel glass dept-empty">&#127881; No Multiple-flow runs are waiting on departments right now.</div>`);
			return;
		}
		$wrap.html(`
			<div class="sec-title"><span class="bar" style="background:var(--amber)"></span> Department Production Status
				<span class="feas-tag tag-auto">&#128202; PM · CEO · MD · Store Manager</span></div>
			${dept_tiles}
			${waiting.length ? `<div class="glass pl-rows">${waiting.map(card).join("")}</div>`
				: (this._board_cat_filter
					? '<div class="panel glass dept-empty">No runs are waiting on this department. &#9989;</div>' : "")}
			${rest.length ? `<details style="margin:8px 0 12px"><summary style="cursor:pointer;font-size:12px;opacity:.7;padding:2px 4px">Recently completed department gates (${rest.length})</summary><div class="glass pl-rows" style="margin-top:6px">${rest.map(card).join("")}</div></details>` : ""}`);
	}

	// v2.21 — department-wise rollup of the board data (pure regroup, no extra calls)
	dept_tiles_html(waiting) {
		const agg = {}; // category -> {pending, oldest}
		(waiting || []).forEach((r) => (r.departments || []).forEach((s) => {
			if (!agg[s.category]) agg[s.category] = { pending: 0, oldest: null };
			if (s.status === "Pending") {
				agg[s.category].pending += 1;
				const t = s.notified_at || "";
				if (t && (!agg[s.category].oldest || t < agg[s.category].oldest)) {
					agg[s.category].oldest = t;
				}
			}
		}));
		const cats = Object.keys(agg).sort();
		// stuck-filter guard: if the filtered category vanished from the board,
		// clear the filter so the user isn't stranded with no tile to un-click
		if (this._board_cat_filter && !cats.includes(this._board_cat_filter)) {
			this._board_cat_filter = null;
		}
		if (!cats.length) return "";
		const mine = new Set(((this.dept_ctx || {}).categories) || []);
		const age = (ts) => this.age_of(ts);
		return `<div class="pl-dept-tiles">` + cats.map((c) => {
			const a = agg[c];
			const active = this._board_cat_filter === c;
			const cls = a.pending ? "tile-pending" : "tile-clear";
			return `<div class="pl-dept-tile ${cls}${active ? " tile-active" : ""}${mine.has(c) ? " tile-mine" : ""}" data-cat="${this.esc(c)}" role="button" tabindex="0" aria-pressed="${active ? "true" : "false"}">
				<div class="tile-name">${this.esc(c)}${mine.has(c) ? ' <span class="tile-you">your action</span>' : ""}</div>
				<div class="tile-count">${a.pending ? a.pending + " pending" : "&#10003; all clear"}</div>
				${a.pending && a.oldest ? `<div class="tile-age">oldest waiting ${age(a.oldest)}</div>` : ""}
			</div>`;
		}).join("") + `</div>`;
	}

	open_skip_dialog(name, category) {
		// Fresh Dialog (L296); server re-verifies Administrator (L142/U3).
		const d = new frappe.ui.Dialog({
			title: __("Skip department — {0}", [category]),
			fields: [
				{ fieldtype: "HTML", fieldname: "warn_html" },
				{ fieldtype: "Small Text", fieldname: "reason", label: __("Skip Reason"), reqd: 1,
				  description: __("At least 10 characters — recorded on the entry.") },
			],
			primary_action_label: __("Skip (Administrator)"),
			primary_action: (v) => {
				if (!v.reason || v.reason.trim().length < 10) {
					frappe.show_alert({ message: __("Reason must be at least 10 characters."), indicator: "orange" });
					return;
				}
				d.get_primary_btn().prop("disabled", true);
				frappe.call({
					method: "trustbit_ethanol.ts_gate_entry.ts_production_dept.admin_skip_department",
					type: "POST",
					args: { name: name, reason: v.reason },
					callback: (r) => {
						d.hide();
						if (r.message && r.message.ok) {
							frappe.show_alert({
								message: r.message.mr_created
									? __("Skipped — all departments resolved; Material Request {0} routed to the Store Manager.", [r.message.material_request || ""])
									: __("Skipped — waiting for the remaining departments."),
								indicator: "green",
							});
							this.load_multi_board();
							this.load_dept_context();
						}
					},
					error: () => d.get_primary_btn().prop("disabled", false),
				});
			},
		});
		d.fields_dict.warn_html.$wrapper.html(
			`<div style="font-size:12.5px;margin-bottom:6px"><b>&#9888;&#65039; Administrator-only bypass.</b> ` +
			`Skipping counts this department as resolved — the release Material Request is created as soon as no department is left Pending.</div>`);
		d.show();
	}

	// ── Phase D — pending distributions (PM posts the multi-warehouse split) ──
	// ── 23 Jul — Single-flow BY-PRODUCT Post Distribution (PM) ──────────
	load_single_distributions() {
		frappe.call({
			method: "trustbit_ethanol.ts_gate_entry.ts_production_release.get_awaiting_distributions",
			callback: (r) => this.render_sdist_zone(r.message || []),
			error: () => this.render_sdist_zone([]),
		});
	}

	render_sdist_zone(rows) {
		const $wrap = this.$root.find("#pl-sdist-wrap");
		if (!$wrap.length) return;
		this._n_sdist = rows.length;
		this.update_actions();
		if (!rows.length) { $wrap.empty(); return; }
		const row = (r) => `
			<div class="pl-row violet">
				<span class="pl-rid">${this.esc(r.name)}</span>
				<span class="pl-rwhat">${this.esc(r.production_item_name || r.production_item)} · <b>${this.fmt1(r.actual_produced_qty)} ${this.esc(r.production_uom || "")}</b>
					<span class="dim">· ${(r.byproducts || []).length} by-product(s) to split</span></span>
				<span class="pl-pill age">waiting ${this.age_of(r.modified) || "—"}</span>
				<span class="pl-sp"></span><span class="pl-more">details &#9656;</span>
				<button class="btn btn-approve btn-sm btn-violet pl-sdist-btn" data-name="${this.esc(r.name)}">&#127981; Distribute By-Products</button>
			</div>
			<div class="pl-xpand" style="display:none">
				${(r.byproducts || []).map((b) => `<span><b>${this.esc(b.item_name || b.item_code)}</b> — ${this.fmt1(b.actual_qty)} ${this.esc(b.uom || "")} to split</span>`).join("")}
				<span><b>Note</b> the finished good goes to the standard FG warehouse; you split ONLY the by-products — each must total exactly its produced quantity.</span>
			</div>`;
		$wrap.html(`
			<div class="sec-title" id="pl-sec-sdist">
				<span class="bar" style="background:var(--purple)"></span> Post Distribution &mdash; By-Products (Single Flow)
				<span class="feas-tag tag-auto">&#127981; PM action</span>
			</div>
			<div class="glass pl-rows">${rows.map(row).join("")}</div>`);
	}

	load_pending_distributions() {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: PL_DOCTYPE,
				filters: { ts_variance_status: "Awaiting Distribution", flow_type: "Multiple" },
				fields: ["name", "bom", "production_item", "production_item_name",
					"actual_produced_qty", "production_uom", "work_order",
					"material_request", "release_stock_entry", "modified"],
				order_by: "modified desc",
				limit_page_length: 20,
			},
			callback: (r) => this.render_dist_zone(r.message || []),
			error: () => this.render_dist_zone([]),
		});
	}

	render_dist_zone(rows) {
		const $wrap = this.$root.find("#pl-dist-wrap");
		if (!$wrap.length) return;
		this._n_dist = rows.length;
		this.update_actions();
		if (!rows.length) { $wrap.empty(); return; }
		const row = (r) => `
			<div class="pl-row violet">
				<span class="pl-rid">${this.esc(r.name)}</span>
				<span class="pl-rwhat">${this.esc(r.production_item_name || r.production_item)} · <b>${this.fmt1(r.actual_produced_qty)} ${this.esc(r.production_uom || "")}</b>
					<span class="dim">· MR ${this.esc(r.material_request || "—")}</span></span>
				<span class="pl-pill age">waiting ${this.age_of(r.modified) || "—"}</span>
				<span class="pl-sp"></span><span class="pl-more">details &#9656;</span>
				<button class="btn btn-approve btn-sm btn-violet pl-dist-btn" data-name="${this.esc(r.name)}">&#127981; Post Distribution</button>
			</div>
			<div class="pl-xpand" style="display:none">
				<span><b>Work Order</b> <span class="mono">${this.esc(r.work_order || "—")}</span></span>
				<span><b>Release SE</b> <span class="mono">${this.esc(r.release_stock_entry || "—")}</span></span>
				<span><b>Note</b> split the produced output (and by-products) across warehouses — totals must exactly match; posting creates ONE Manufacture Stock Entry.</span>
			</div>`;
		$wrap.html(`
			<div class="sec-title">
				<span class="bar" style="background:var(--purple)"></span> Post Distribution
				<span class="feas-tag tag-auto">&#127981; PM action</span>
			</div>
			<div class="glass pl-rows">${rows.map(row).join("")}</div>`);
	}

	open_distribution_dialog(name, single) {
		if (this.busy) return;
		Promise.all([
			new Promise((res) => frappe.call({
				method: "frappe.client.get",
				args: { doctype: PL_DOCTYPE, name },
				callback: (r) => res(r.message || null), error: () => res(null),
			})),
			this.load_warehouses(),
		]).then(([doc, warehouses]) => {
			if (!doc) return;
			// single (23 Jul) = Single-flow BY-PRODUCTS-ONLY split: FG is synthesized
			// server-side to the standard FG warehouse and is NOT part of the dialog.
			const targets = (single ? [] : [{
				item_code: doc.production_item,
				item_name: doc.production_item_name || doc.production_item,
				line_type: "Finished",
				target: flt(doc.actual_produced_qty),
				uom: doc.production_uom || "",
			}]).concat((doc.byproducts || [])
				.filter((b) => flt(b.actual_qty) > 0)
				.map((b) => ({
					item_code: b.item_code,
					item_name: b.item_name || b.item_code,
					line_type: "By-Product",
					target: flt(b.actual_qty),
					uom: b.uom || "",
					rate: flt(b.rate),
				})));
			if (single && !targets.length) {
				frappe.show_alert({ message: __("No by-products with quantity to split."), indicator: "orange" });
				return;
			}
			const wh_opts = warehouses.map((w) =>
				`<option value="${frappe.utils.escape_html(w)}">${frappe.utils.escape_html(w)}</option>`).join("");
			const blocks = targets.map((t, ti) => `
				<div class="pl-dist-block" data-ti="${ti}" style="border:1px solid ${t.line_type === "Finished" ? "#86efac" : "#93c5fd"};border-radius:8px;padding:10px 12px;margin-bottom:10px;">
					<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
						<b>${frappe.utils.escape_html(t.item_name)}</b>
						<span style="font-size:10.5px;padding:1px 8px;border-radius:8px;background:${t.line_type === "Finished" ? "#dcfce7;color:#15803d" : "#dbeafe;color:#1d4ed8"};font-weight:600">${t.line_type}</span>
						<span style="margin-left:auto;font-size:12px;" class="pl-dist-total" data-ti="${ti}">0 of ${t.target} ${frappe.utils.escape_html(t.uom)}</span>
					</div>
					<div class="pl-dist-rows" data-ti="${ti}">
						<div class="pl-dist-row" style="display:flex;gap:8px;margin-bottom:6px;">
							<select class="form-control pl-dist-wh" style="flex:2">${wh_opts}</select>
							<input type="number" min="0" step="any" class="form-control pl-dist-qty" placeholder="Qty" style="flex:1;text-align:right">
							<button type="button" class="btn btn-xs pl-dist-del" style="flex:0">&times;</button>
						</div>
					</div>
					<button type="button" class="btn btn-xs btn-default pl-dist-add" data-ti="${ti}" style="border:1px dashed #94a3b8;">+ Add warehouse</button>
				</div>`).join("");

			const d = new frappe.ui.Dialog({
				title: single ? __("Distribute By-Products — {0}", [doc.name])
				              : __("Post Distribution — {0}", [doc.name]),
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "dist_html" }],
				primary_action_label: single ? __("Post By-Product Split") : __("Post Distribution"),
				primary_action: () => this.submit_distribution(d, doc, targets, single),
			});
			d.fields_dict.dist_html.$wrapper.html(
				`<div style="font-size:12px;margin-bottom:8px;">Each item's split must total <b>exactly</b> its produced quantity — the button unlocks when everything balances.</div>` + blocks +
				`<div id="pl-dist-summary" style="font-size:12px;font-weight:600;margin-top:4px;color:#b45309;">0 of ${targets.length} item(s) balanced</div>`);
			const $w = d.fields_dict.dist_html.$wrapper;
			const recompute = () => {
				let balanced = 0;
				targets.forEach((t, ti) => {
					let sum = 0;
					$w.find(`.pl-dist-rows[data-ti="${ti}"] .pl-dist-qty`).each(function () {
						sum += flt($(this).val());
					});
					const ok = Math.abs(sum - t.target) < 1e-6 && sum > 0;
					if (ok) balanced += 1;
					$w.find(`.pl-dist-total[data-ti="${ti}"]`)
						.text(`${sum} of ${t.target} ${t.uom}`)
						.css("color", ok ? "#15803d" : (sum > t.target ? "#b91c1c" : ""));
				});
				$w.find("#pl-dist-summary")
					.text(`${balanced} of ${targets.length} item(s) balanced`)
					.css("color", balanced === targets.length ? "#15803d" : "#b45309");
				d.get_primary_btn().prop("disabled", balanced !== targets.length);
			};
			$w.on("input change", ".pl-dist-qty, .pl-dist-wh", recompute);
			$w.on("click", ".pl-dist-add", function () {
				const ti = $(this).data("ti");
				const $rows = $w.find(`.pl-dist-rows[data-ti="${ti}"]`);
				$rows.append($rows.find(".pl-dist-row").first().clone().find("input").val("").end());
				recompute();
			});
			$w.on("click", ".pl-dist-del", function () {
				const $rows = $(this).closest(".pl-dist-rows");
				if ($rows.find(".pl-dist-row").length > 1) $(this).closest(".pl-dist-row").remove();
				else $(this).closest(".pl-dist-row").find("input").val("");
				recompute();
			});
			d.show();
			recompute();
		});
	}

	load_warehouses() {
		if (this.warehouses) return Promise.resolve(this.warehouses);
		return new Promise((res) => frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Warehouse",
				filters: { is_group: 0, disabled: 0 },
				fields: ["name"], limit_page_length: 100, order_by: "name",
			},
			callback: (r) => {
				this.warehouses = (r.message || []).map((w) => w.name);
				res(this.warehouses);
			},
			error: () => res([]),
		}));
	}

	submit_distribution(d, doc, targets, single) {
		const $w = d.fields_dict.dist_html.$wrapper;
		const rows = [];
		targets.forEach((t, ti) => {
			$w.find(`.pl-dist-rows[data-ti="${ti}"] .pl-dist-row`).each(function () {
				const qty = flt($(this).find(".pl-dist-qty").val());
				const wh = $(this).find(".pl-dist-wh").val();
				if (qty > 0 && wh) {
					rows.push({ item_code: t.item_code, warehouse: wh, qty: qty,
						uom: t.uom, rate: t.rate || 0 });
				}
			});
		});
		if (!rows.length) return;
		d.get_primary_btn().prop("disabled", true);
		d.hide();
		this.busy = true;
		this.start_progress({
			title: single ? "Posting by-product split…" : "Posting distribution…",
			subtitle: `${doc.name} · Awaiting Distribution → Completed`,
			steps: [
				{ label: "Validating the split (sum = produced)…", kind: "auto" },
				{ label: "Completing Job Cards (if any)…", kind: "auto" },
				{ label: single ? "Posting the Manufacture entry with split by-products…"
				                : "Posting the multi-warehouse Manufacture entry…", kind: "auto" },
				{ label: "Closing the Work Order + reconciling WIP…", kind: "auto" },
			],
		});
		this.advance_progress(0);
		frappe.call({
			method: single
				? "trustbit_ethanol.ts_gate_entry.ts_production_release.complete_single_distribution"
				: "trustbit_ethanol.ts_gate_entry.ts_production_multi.complete_distribution",
			type: "POST",
			args: { name: doc.name, distribution: rows },
			callback: (r) => {
				const m = r.message || {};
				this.advance_progress(3);
				this.finish_progress({
					doneStatus: "Done ✓ — output distributed & Work Order closed",
					onDone: () => {
						frappe.show_alert({
							message: __("Distribution posted — {0} completed via {1}.",
								[doc.name, m.stock_entry || ""]),
							indicator: "green",
						});
						this.reload_data();
					},
				});
				this.busy = false;
			},
			error: (err) => {
				this.fail_progress(this.err_text(err) ||
					__("The distribution could not be posted."));
				this.busy = false;
			},
		});
	}

	bom_label(bom_id) {
		// "<ID> — <item name>" when known; graceful fallback to the bare ID.
		const nm = (this._bom_names || {})[bom_id];
		return nm ? `${bom_id} — ${nm}` : (bom_id || "—");
	}

	load_bom_names() {
		if (this._bom_names) return;
		this._bom_names = {};
		frappe.call({
			method: "frappe.client.get_list",
			args: { doctype: "BOM", fields: ["name", "item_name"], limit_page_length: 500 },
			callback: (r) => {
				(r.message || []).forEach((b) => { this._bom_names[b.name] = b.item_name; });
				this.render_dept_zone();  // re-render cards with names once loaded
			},
			error: () => {},  // no BOM read permission — cards keep the bare ID
		});
	}

	load_boms() {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "BOM",
				filters: { is_active: 1, docstatus: 1 },
				fields: ["name", "item", "item_name", "quantity", "uom", "with_operations"],
				order_by: "modified desc",
				limit_page_length: 200,
			},
			callback: (r) => {
				this.boms = r.message || [];
				this.render_bom_options();
			},
			error: () => {
				this.boms = [];
				this.render_bom_options();
			},
		});
	}

	render_bom_options() {
		const $sel = this.$root.find("#pl-bom-select");
		if (!this.boms.length) {
			$sel.html('<option value="">No active BOMs found</option>');
			return;
		}
		let opts = '<option value="">Select a BOM&hellip;</option>';
		this.boms.forEach((b) => {
			const label = `${b.name} — ${b.item_name || b.item} · ${this.fmt1(b.quantity)} ${b.uom || ""}${b.with_operations ? " · ops" : " · no-ops"}`;
			opts += `<option value="${this.esc(b.name)}">${this.esc(label)}</option>`;
		});
		$sel.html(opts);
	}

	// ── PIPELINE STEPPER ───────────────────────────────────────────────
	render_flow() {
		const FLOW = [
			{ n: 1, kind: "input", ico: "🧪", name: "Pick BOM", desc: "PM selects BOM; details fetch from BOM." },
			{ n: 2, kind: "input", ico: "✎", name: "Produced + By-products", desc: "Enter produced qty; RM auto-scales." },
			{ n: 3, kind: "input", ico: "✎", name: "Edit RM lines", desc: "PM may edit qty / remove a line (release only)." },
			{ n: 4, kind: "auto", ico: "⚙️", name: "Work Order", desc: "System creates + submits the Work Order." },
			{ n: 5, kind: "auto", ico: "⚙️", name: "Draft Release SE", desc: "Builds DRAFT Material Transfer (Stores → WIP)." },
			{ n: 6, kind: "gate", ico: "🚦", name: "Store Mgr Releases", desc: "The ONE human gate — release the raw material." },
			{ n: 7, kind: "auto", ico: "⚙️", name: "Job Cards", desc: "Auto-completes Job Cards (operations BOMs)." },
			{ n: 8, kind: "auto", ico: "⚙️", name: "Manufacture SE", desc: "Posts produced qty + by-products (BOM-scaled)." },
			{ n: 9, kind: "auto", ico: "⚙️", name: "WO Completed", desc: "Work Order completes + closes." },
			{ n: 10, kind: "auto", ico: "⚙️", name: "Return surplus WIP", desc: "Auto-returns any surplus WIP → Stores." },
		];
		const arrow = (gate) =>
			`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${gate ? "2.6" : "2"}" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`;
		let h = "";
		FLOW.forEach((s, i) => {
			const kc = s.kind === "gate" ? "kind-gate" : s.kind === "input" ? "kind-input" : "";
			const icc = s.kind === "gate" ? "ico-gate" : s.kind === "input" ? "ico-input" : "ico-auto";
			const kpill =
				s.kind === "gate"
					? '<span class="step-kind k-gate">🚦 Gate</span>'
					: s.kind === "input"
					? '<span class="step-kind k-input">✎ Input</span>'
					: '<span class="step-kind k-auto">⚙️ Auto</span>';
			h +=
				`<div class="flow-step"><div class="step-card ${kc}">` +
				`<div class="step-top"><div class="step-ico ${icc}">${s.ico}</div><div class="step-num">Step ${s.n}</div></div>` +
				kpill +
				`<div class="step-name">${this.esc(s.name)}</div>` +
				`<div class="step-desc">${this.esc(s.desc)}</div>` +
				`</div></div>`;
			if (i < FLOW.length - 1) {
				const gate = FLOW[i + 1].kind === "gate" || s.kind === "gate";
				h += `<div class="flow-arrow${gate ? " gate-arrow" : ""}">${arrow(gate)}</div>`;
			}
		});
		this.$root.find("#pl-flow-rail").html(h);
	}

	// ── KPI CARDS (derived from the live log set) ───────────────────────
	render_kpis(rows) {
		const norm = (s) => (s || "").toLowerCase();
		const today = frappe.datetime.now_date();
		let producedToday = 0,
			runsToday = 0,
			pending = 0,
			released = 0,
			completed = 0,
			varN = 0;
		rows.forEach((r) => {
			const st = norm(r.ts_variance_status);
			if (st === "pending stores release") pending++;
			if (st === "released") released++;
			if (st === "completed") {
				completed++;
				if (cint(r.variance_breach)) varN++;
			}
			const md = (r.modified || "").slice(0, 10);
			if (md === today && (st === "completed" || st === "released")) {
				producedToday += flt(r.actual_produced_qty);
				runsToday++;
			}
		});
		const KPIS = [
			{ ico: "🛢️", cls: "ki-green", lbl: "Produced Today", val: this.fmt(producedToday), unit: "", sub: `${runsToday} run(s) released/posted today` },
			{ ico: "🚦", cls: "ki-amber", lbl: "Pending Releases", val: String(pending), unit: "", sub: "awaiting Store Manager" },
			{ ico: "⚙️", cls: "ki-blue", lbl: "Released (in process)", val: String(released), unit: "", sub: "auto-chain recoverable" },
			{ ico: "✓", cls: "ki-purple", lbl: "Completed (recent)", val: String(completed), unit: "", sub: "in the last 30 runs" },
			{ ico: "⚠️", cls: "ki-slate", lbl: "Variance Breaches", val: String(varN), unit: "", sub: "completed runs over tolerance" },
		];
		this.$root.find("#pl-kpi-grid").html(
			KPIS.map(
				(k) =>
					`<div class="kpi glass">` +
					`<div class="kpi-head"><div class="kpi-ico ${k.cls}">${k.ico}</div><div class="kpi-lbl">${this.esc(k.lbl)}</div></div>` +
					`<div class="kpi-val">${this.esc(k.val)}${k.unit ? ` <small>${this.esc(k.unit)}</small>` : ""}</div>` +
					`<div class="kpi-sub">${this.esc(k.sub)}</div>` +
					`</div>`
			).join("")
		);
	}

	// ── STATUS BADGE ───────────────────────────────────────────────────
	badge(status) {
		const map = {
			"Draft": "b-draft",
			"Pending Stores Release": "b-pending",
			"Released": "b-released",
			"Awaiting Department Production": "b-pending",
			"Pending Material Request": "b-matreq",
			"Awaiting Distribution": "b-distrib",
			"Completed": "b-completed",
			"Rejected": "b-rejected",
			"Cancelled": "b-cancelled",
		};
		const cls = map[status] || "b-draft";
		return `<span class="badge ${cls}"><span class="bdot"></span>${this.esc(status || "Draft")}</span>`;
	}
	// DISPLAY-ONLY: while a run's department slots are Pending, show "Pending
	// Department Release" — the stored status stays "Pending Stores Release".
	_statusCell(r) {
		const pend = (this._pendDept || {})[r.name] || [];
		if (r.ts_variance_status === "Pending Stores Release" && pend.length) {
			return `<span class="badge b-pending" title="Waiting on: ${this.esc(pend.join(", "))}"><span class="bdot"></span>Pending Department Release</span>`;
		}
		return this.badge(r.ts_variance_status);
	}
	var_cell(v) {
		if (v == null) return '<span style="color:var(--faint)">—</span>';
		const a = Math.abs(flt(v));
		const cls = a <= 2 ? "var-good" : a <= 5 ? "var-warn" : "var-bad";
		return `<span class="${cls}">${a.toFixed(1)}%</span>`;
	}

	// ── PRODUCTION LOG TABLE ───────────────────────────────────────────
	render_log(rows) {
		const $b = this.$root.find("#pl-log-body");
		if (!rows.length) {
			$b.html('<tr><td colspan="8"><div class="log-empty">No production entries yet.</div></td></tr>');
			return;
		}
		$b.html(
			rows
				.map((r) => {
					const itemName = r.production_item_name || r.production_item || "—";
					const itemCode = r.production_item || "";
					const variance =
						(r.ts_variance_status === "Completed" || r.ts_variance_status === "Released")
							? this.var_cell(r.material_variance_pct)
							: '<span style="color:var(--faint)">—</span>';
					return (
						`<tr>` +
						`<td><a class="pid log-link" href="/app/${encodeURIComponent(PL_DOCTYPE.toLowerCase().replace(/ /g, "-"))}/${encodeURIComponent(r.name)}" target="_blank" rel="noopener">${this.esc(r.name)}</a></td>` +
						`<td><div class="bom-cell"><span class="bm mono">${this.esc(r.bom || "—")}</span></div></td>` +
						`<td>${this.esc(itemName)} <span class="bt mono" style="color:var(--faint)">${this.esc(itemCode)}</span></td>` +
						`<td class="r mono"><b>${this.fmt1(r.actual_produced_qty)}</b> <span class="uom-cell">${this.esc(r.production_uom || "")}</span></td>` +
						`<td>${this._statusCell(r)}</td>` +
						`<td class="r">${variance}</td>` +
						`<td class="mono" style="color:var(--muted)">${this.esc(r.work_order || "—")}</td>` +
						`<td style="color:var(--faint);white-space:nowrap">${this.esc(frappe.datetime.str_to_user(r.modified))}</td>` +
						`</tr>`
					);
				})
				.join("")
		);
	}

	// ── STORE-MANAGER PENDING-RELEASE CARDS ────────────────────────────
	render_release_zone(rows) {
		const $z = this.$root.find("#pl-release-zone");
		if (!$z.length) return;
		// count only runs whose departments have ALL released (the gated ones are shown
		// as "waiting", not actionable) — 14 Jul UX fix
		this._n_rel = rows.filter((r) => !((r.pending_departments || []).length)).length;
		this.update_actions();
		if (!rows.length) {
			$z.html(
				`<div class="glass empty-card"><div class="ec-ico">✅</div>` +
					`<div class="ec-t">No releases awaiting your approval</div>` +
					`<div class="ec-s">When a Production Manager submits a run, its raw-material release request appears here.</div></div>`
			);
			return;
		}
		const wip = this.settings && this.settings.wip_warehouse ? this.settings.wip_warehouse : "WIP";
		const src = this.settings && this.settings.release_source_warehouse ? this.settings.release_source_warehouse : "Stores";
		const week_ago = frappe.datetime.add_days(frappe.datetime.now_date(), -7);
		const row = (r) => {
			const itemName = r.production_item_name || r.production_item || "—";
			const gatedBy = r.pending_departments || [];
			const gated = gatedBy.length > 0;
			const action = gated
				? `<span class="pl-pill" style="background:var(--amber-bg,rgba(245,158,11,.14));color:var(--amber,#b45309);border:1px solid var(--amber-bd,rgba(245,158,11,.35));white-space:nowrap">&#8987; Waiting: ${this.esc(gatedBy.join(", "))}</span>`
				: `<button class="btn btn-approve btn-sm pl-approve-btn" data-name="${this.esc(r.name)}">` +
				  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg> Release</button>`;
			return (
				`<div class="pl-row ${gated ? "amber" : "green"}">` +
				`<span class="pl-rid">${this.esc(r.name)}</span>` +
				`<span class="pl-rwhat">${this.esc(itemName)} · <b>${this.fmt1(r.actual_produced_qty)} ${this.esc(r.production_uom || "")}</b>` +
				` <span class="dim">· ${this.esc(r.bom || "—")} · from ${this.esc(r.submitted_by || r.owner || "—")}</span></span>` +
				`<span class="pl-pill age">waiting ${this.age_of(r.modified) || "—"}</span>` +
				`<span class="pl-sp"></span><span class="pl-more">details &#9656;</span>` +
				action +
				`</div>` +
				`<div class="pl-xpand" style="display:none">` +
				`<span><b>Work Order</b> <span class="mono">${this.esc(r.work_order || "—")}</span></span>` +
				`<span><b>Release SE</b> <span class="mono">${this.esc(r.release_stock_entry || "—")}</span></span>` +
				`<span><b>Updated</b> ${this.esc(frappe.datetime.str_to_user(r.modified))}</span>` +
				(gated
					? `<span><b>Waiting for departments</b> ${this.esc(gatedBy.join(", "))} must release their own material first — then this run becomes releasable.</span>`
					: `<span><b>On Release</b> the system submits the Material Transfer (${this.esc(src)} &rarr; ${this.esc(wip)}), runs Job Cards, posts the Manufacture Stock Entry and closes the Work Order — no further clicks.</span>`) +
				`</div>`
			);
		};
		const fresh = rows.filter((r) => (r.modified || "").slice(0, 10) >= week_ago);
		const older = rows.filter((r) => (r.modified || "").slice(0, 10) < week_ago);
		$z.html(
			`<div class="glass pl-rows">` +
				fresh.map(row).join("") +
				(older.length
					? `<div class="pl-fold"><span class="pl-fold-chev">&#9656;</span> Older release requests (${older.length})</div>` +
					  `<div class="pl-fold-body" style="display:none">${older.map(row).join("")}</div>`
					: "") +
			`</div>`
		);
	}

	// ── ADD-PRODUCTION FORM ────────────────────────────────────────────
	open_form() {
		this.$root.find("#pl-form-scrim").addClass("open");
		this.$root.find("#pl-slideover").addClass("open");
		document.body.style.overflow = "hidden";
	}
	close_form() {
		this.$root.find("#pl-form-scrim").removeClass("open");
		this.$root.find("#pl-slideover").removeClass("open");
		document.body.style.overflow = "";
	}

	on_bom_change(bom) {
		this.bom = bom;
		const $fetched = this.$root.find("#pl-fetched-box");
		this.$root.find("#pl-step2,#pl-step3").hide();
		this.$root.find("#pl-submit-btn").prop("disabled", true);
		if (!bom) {
			$fetched.addClass("hidden").empty();
			this.bom_std = null;
			return;
		}
		$fetched.removeClass("hidden").html('<div class="fc"><div class="v">Fetching BOM standard…</div></div>');
		frappe.call({
			method: `${PL_API}.fetch_bom_standard`,
			args: { bom },
			callback: (r) => {
				this.bom_std = r.message || {};
				this.render_fetched();
				this.seed_states();
				this.$root.find("#pl-step2,#pl-step3").show();
				this.render_form();
				this.recompute_submit_enabled();
			},
			error: () => {
				$fetched.html('<div class="fc"><div class="v" style="color:var(--red-txt)">Could not fetch BOM.</div></div>');
				this.bom_std = null;
			},
		});
	}

	render_fetched() {
		const b = this.bom_std || {};
		const lock =
			'<svg class="lock-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/></svg>';
		this.$root.find("#pl-fetched-box").html(
			`<div class="fc"><div class="k">${lock} Selected BOM</div><div class="v"><span class="mono">${this.esc(this.bom || "—")}</span> — ${this.esc(b.production_item_name || "")}</div></div>` +
			`<div class="fc"><div class="k">${lock} Finished Item</div><div class="v">${this.esc(b.production_item_name || "—")}</div></div>` +
			`<div class="fc"><div class="k">${lock} Item Code</div><div class="v mono">${this.esc(b.production_item || "—")}</div></div>` +
			`<div class="fc"><div class="k">UOM</div><div class="v">${this.esc(b.production_uom || "—")}</div></div>` +
			`<div class="fc"><div class="k">BOM Batch Qty</div><div class="v">${this.fmt1(b.bom_quantity)} ${this.esc(b.production_uom || "")}</div></div>` +
			`<div class="fc"><div class="k">Company</div><div class="v" style="font-size:11px">${this.esc(b.company || "—")}</div></div>` +
			`<div class="fc"><div class="k">Materials</div><div class="v">${(b.materials || []).length} item(s) &middot; ${(b.byproducts || []).length} by-product(s)</div></div>`
		);
		this.$root.find("#pl-prod-uom").text(b.production_uom ? `(${b.production_uom})` : "");
	}

	seed_states() {
		const b = this.bom_std || {};
		// default the produced qty to the BOM batch qty so a single batch is the baseline
		const $q = this.$root.find("#pl-produced-qty");
		if (!flt($q.val())) $q.val(flt(b.bom_quantity) || "");
		this.reset_rm();
		this.bp_state = (b.byproducts || []).map((x) => ({
			item_code: x.item_code,
			item_name: x.item_name,
			std_qty: flt(x.std_qty),
			uom: x.uom,
			rate: flt(x.rate),
			target_warehouse: x.target_warehouse,
		}));
	}

	scale() {
		const b = this.bom_std || {};
		const batch = flt(b.bom_quantity) || 0;
		const produced = flt(this.$root.find("#pl-produced-qty").val());
		return batch > 0 ? produced / batch : 0;
	}

	reset_rm() {
		const b = this.bom_std || {};
		const s = this.scale();
		this.rm_state = (b.materials || []).map((x) => ({
			item_code: x.item_code,
			item_name: x.item_name,
			std_qty: flt(x.std_qty),
			uom: x.uom,
			source_warehouse: x.source_warehouse,
			qty: this.round_qty(flt(x.std_qty) * s),
			edited: false,
			removed: false,
		}));
		this.render_form();
		this.recompute_submit_enabled();
	}

	round_qty(n) {
		// keep 3-dp precision for small recipe quantities, integers for large
		return Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 1000) / 1000;
	}

	on_produced_change() {
		const s = this.scale();
		// rescale only the rows the PM hasn't manually edited or removed
		this.rm_state.forEach((r) => {
			if (!r.edited && !r.removed) r.qty = this.round_qty(flt(r.std_qty) * s);
		});
		this.render_form();
		this.recompute_submit_enabled();
	}

	render_form() {
		const s = this.scale();
		this.$root.find("#pl-batch-mult").val(this.fmt1(s) + " ×");
		this.$root.find("#pl-rm-badge").text("× " + this.fmt1(s));
		this.$root.find("#pl-bp-badge").text("auto-scaled ×" + this.fmt1(s));

		// by-products (read-only, auto-scaled)
		const $bp = this.$root.find("#pl-bp-body");
		// Post-Distribution opt-in only makes sense when the BOM has by-products
		this.$root.find("#pl-bp-dist-wrap").css("display", this.bp_state.length ? "flex" : "none");
		if (!this.bp_state.length) {
			$bp.html('<tr class="mat-empty"><td colspan="5">No by-products on this BOM.</td></tr>');
		} else {
			$bp.html(
				this.bp_state
					.map(
						(b, i) =>
							`<tr><td><div class="mat-item"><span class="mn">${this.esc(b.item_name || b.item_code)}</span><span class="mc mono">${this.esc(b.item_code)}</span></div></td>` +
							`<td class="r std-q">${this.fmt1(b.std_qty)}</td>` +
							`<td class="r"><input class="inp bp-q" type="number" min="0" step="any" data-i="${i}" value="${b.edited_qty != null ? flt(b.edited_qty) : this.round_qty(flt(b.std_qty) * s)}" style="max-width:110px;text-align:right"></td>` +
							`<td class="uom-cell">${this.esc(b.uom || "")}</td>` +
							`<td><input class="inp bp-wh" type="text" data-i="${i}" value="${this.esc(b.target_warehouse || "")}" placeholder="warehouse" style="max-width:190px"></td></tr>`
					)
					.join("")
			);
			const self = this;
			$bp.find(".bp-q").on("change", function () {
				// Blank => restore auto-scale (edited_qty=null), NOT 0. flt('')=0 used to
				// stick as an explicit zero and silently DROP the by-product from stock.
				const raw = ($(this).val() || "").trim();
				self.bp_state[$(this).data("i")].edited_qty = raw === "" ? null : flt(raw);
				self.render_form(); // a cleared cell snaps back to the auto-scaled qty
			});
			$bp.find(".bp-wh").on("change", function () {
				self.bp_state[$(this).data("i")].target_warehouse = ($(this).val() || "").trim();
			});
		}

		// raw material (editable)
		const $rm = this.$root.find("#pl-rm-body");
		if (!this.rm_state.length) {
			$rm.html('<tr class="mat-empty"><td colspan="5">No raw materials on this BOM.</td></tr>');
		} else {
			$rm.html(
				this.rm_state
					.map(
						(r, idx) =>
							`<tr class="mat-row ${r.removed ? "removed" : ""} ${r.edited ? "qty-edited" : ""}">` +
							`<td><div class="mat-item"><span class="mn">${this.esc(r.item_name || r.item_code)}</span><span class="mc mono">${this.esc(r.item_code)}</span></div></td>` +
							`<td class="r std-q">${this.fmt1(this.round_qty(flt(r.std_qty) * s))}</td>` +
							`<td class="r"><input class="qty mono" type="number" step="any" min="0" value="${this.esc(r.qty)}" data-idx="${idx}" ${r.removed ? "disabled" : ""}></td>` +
							`<td class="uom-cell">${this.esc(r.uom || "")}</td>` +
							`<td class="c"><button class="rm-x" title="${r.removed ? "Restore" : "Remove"} line" data-idx="${idx}">${r.removed ? "↺" : "×"}</button></td>` +
							`</tr>`
					)
					.join("")
			);
		}
		const active = this.rm_state.filter((r) => !r.removed && flt(r.qty) > 0).length;
		this.$root.find("#pl-rm-count").text(`${active} material line(s) to release`);
	}

	edit_rm(idx, val) {
		if (!this.rm_state[idx]) return;
		this.rm_state[idx].qty = flt(val);
		this.rm_state[idx].edited = true;
		const $row = this.$root.find("#pl-rm-body .mat-row").eq(idx);
		$row.addClass("qty-edited");
		this.recompute_submit_enabled();
	}
	toggle_rm(idx) {
		if (!this.rm_state[idx]) return;
		this.rm_state[idx].removed = !this.rm_state[idx].removed;
		this.render_form();
		this.recompute_submit_enabled();
	}

	recompute_submit_enabled() {
		const ok =
			!!this.bom &&
			!!this.bom_std &&
			flt(this.$root.find("#pl-produced-qty").val()) > 0 &&
			this.rm_state.some((r) => !r.removed && flt(r.qty) > 0) &&
			!this.busy;
		this.$root.find("#pl-submit-btn").prop("disabled", !ok);
	}

	// ── TRIGGER 1: CREATE the production log (PM: insert -> submit_for_release)
	submit_for_release() {
		if (this.busy) return;
		const multi = this.flow_mode === "multiple";
		const produced = flt(this.$root.find("#pl-produced-qty").val());
		if (multi && !this.connector) {
			frappe.show_alert({ message: __("Pick a BOM Connector first."), indicator: "orange" });
			return;
		}
		if (!this.bom || produced <= 0) {
			frappe.show_alert({ message: __("Pick a BOM and enter a produced qty."), indicator: "orange" });
			return;
		}
		const materials = this.rm_state
			.filter((r) => !r.removed && flt(r.qty) > 0 && r.item_code)
			.map((r) => ({
				item_code: r.item_code,
				actual_qty: flt(r.qty),
				uom: r.uom,
				source_warehouse: r.source_warehouse,
			}));
		if (!materials.length) {
			frappe.show_alert({ message: __("Add at least one raw-material line to release."), indicator: "orange" });
			return;
		}
		const byproducts = this.bp_state.map((b) => ({
			item_code: b.item_code,
			actual_qty: b.edited_qty != null ? flt(b.edited_qty) : this.round_qty(flt(b.std_qty) * this.scale()),
			uom: b.uom,
			rate: flt(b.rate),
			target_warehouse: b.target_warehouse,
		}));

		this.busy = true;
		this.recompute_submit_enabled();
		this.close_form();

		// Steps shown in the bar — they mirror the real server-side work the two
		// chained calls perform. The bar advances to ~90% while the calls run, then
		// jumps to 100% on success (or flips to an error state on failure).
		this.start_progress({
			title: "Creating production log…",
			subtitle: multi ? "Draft → Awaiting Department Production" : "Draft → Pending Stores Release",
			steps: multi ? [
				{ label: "Creating the production entry…", kind: "auto" },
				{ label: "Validating + creating Work Order…", kind: "auto" },
				{ label: "Creating department production entries…", kind: "auto" },
				{ label: "Notifying departments — Add Production…", kind: "auto" },
				{ label: "Waiting for departments → then MR to Store Manager…", kind: "gate" },
			] : [
				{ label: "Creating the production entry…", kind: "auto" },
				{ label: "Validating + creating Work Order…", kind: "auto" },
				{ label: "Scaling raw material from BOM…", kind: "auto" },
				{ label: "Building draft release (Material Transfer)…", kind: "auto" },
				{ label: "Routing to Store Manager…", kind: "gate" },
			],
		});

		// Step 1 — insert the draft TS Production Entry.
		this.advance_progress(0);
		const doc_payload = {
			doctype: PL_DOCTYPE,
			bom: this.bom,
			actual_produced_qty: produced,
			standard_batches: 1,
			materials: materials,
			byproducts: byproducts,
			ts_byproduct_distribution:
				this.$root.find("#pl-bp-dist").is(":checked") && byproducts.length ? 1 : 0,
		};
		if (multi) {
			doc_payload.flow_type = "Multiple";
			doc_payload.bom_connector = this.connector.name;
		}
		frappe.call({
			method: "frappe.client.insert",
			args: { doc: doc_payload },
			callback: (r) => {
				const doc = r.message;
				if (!doc || !doc.name) {
					this.fail_progress(__("The production entry could not be created."));
					this.busy = false;
					return;
				}
				// advance through the "creating" steps while the release call runs
				this.advance_progress(1);
				// Step 2 — Single: submit_for_release (WO + draft release SE).
				//          Multiple: submit_multi_for_release (WO + auto Material Request).
				frappe.call({
					method: multi
						? "trustbit_ethanol.ts_gate_entry.ts_production_multi.submit_multi_for_release"
						: `${PL_REL}.submit_for_release`,
					type: "POST",
					args: { name: doc.name },
					callback: (rr) => {
						const m = rr.message || {};
						this.advance_progress(4);
						const awaiting_depts = m.ts_variance_status === "Awaiting Department Production";
						const sf_depts = (m.department_categories || []);
						this.finish_progress({
							doneStatus: multi
								? (awaiting_depts
									? "Submitted ✓ — departments notified to Add Production"
									: "Submitted ✓ — Material Request routed to Store Manager")
								: (sf_depts.length ? "Submitted ✓ — Pending Department Release (" + sf_depts.join(", ") + ")" : "Submitted ✓ — Pending Store Manager release"),
							onDone: () => {
								frappe.show_alert({
									message: multi
										? (awaiting_depts
											? __("Submitted {0} — {1} department(s) notified. The Store-Manager release fires automatically when all add their production.", [doc.name, (m.department_entries || []).length])
											: __("Submitted {0} — Material Request {1} awaits Store Manager release.", [doc.name, m.material_request || ""]))
										: (sf_depts.length ? __("Submitted {0} — {1} department(s) must release first, then the Store Manager releases the rest.", [doc.name, sf_depts.length]) : __("Submitted {0} — pending Store Manager release.", [doc.name])),
									indicator: "green",
								});
								this.reset_after_submit();
								this.reload_data();
								this.flash_after_reload(doc.name);
							},
						});
						this.busy = false;
					},
					error: (err) => {
						this.fail_progress(this.err_text(err) || __("Could not submit for release."));
						this.busy = false;
						// the draft entry exists but isn't submitted — surface it for cleanup
						frappe.show_alert({
							message: __("Draft {0} was created but not submitted. Open it to retry or delete.", [doc.name]),
							indicator: "orange",
						});
						this.reload_data();
					},
				});
			},
			error: (err) => {
				this.fail_progress(this.err_text(err) || __("The production entry could not be created."));
				this.busy = false;
			},
		});
	}

	reset_after_submit() {
		this.bom = null;
		this.bom_std = null;
		this.rm_state = [];
		this.bp_state = [];
		this.$root.find("#pl-bom-select").val("");
		this.$root.find("#pl-produced-qty").val("");
		this.$root.find("#pl-fetched-box").addClass("hidden").empty();
		this.$root.find("#pl-step2,#pl-step3").hide();
	}

	flash_after_reload(name) {
		// the log reloads async; flash the matching row when it lands
		setTimeout(() => {
			const $rows = this.$root.find("#pl-log-body tr");
			$rows.each((_, tr) => {
				const $tr = $(tr);
				if ($tr.find(".pid").text().trim() === name) {
					$tr.addClass("flash");
					if (tr.scrollIntoView) tr.scrollIntoView({ behavior: "smooth", block: "center" });
				}
			});
		}, 700);
	}

	// ── TRIGGER 2: APPROVE — run the auto-completion chain (Store Mgr) ───
	approve_release(name) {
		if (this.busy || !name) return;
		this.busy = true;
		this.set_release_buttons_disabled(name, true);

		this.start_progress({
			title: "Running automation chain…",
			subtitle: `${name} · Released → Completed`,
			steps: [
				{ label: "Releasing raw material (Stores → WIP)…", kind: "gate" },
				{ label: "Auto-completing Job Cards…", kind: "auto" },
				{ label: "Posting Manufacture entry…", kind: "auto" },
				{ label: "Completing Work Order…", kind: "auto" },
				{ label: "Returning surplus to Stores…", kind: "auto" },
			],
		});
		this.advance_progress(0);

		frappe.call({
			method: `${PL_REL}.approve_release`,
			type: "POST",
			args: { name },
			callback: (r) => {
				const m = r.message || {};
				this.advance_progress(4);
				this.finish_progress({
					doneStatus: "Done ✓ — Production logged & Work Order closed",
					onDone: () => {
						frappe.show_alert({
							message: __("Release approved — {0} completed.", [name]),
							indicator: "green",
						});
						this.reload_data();
					},
				});
				this.busy = false;
			},
			error: (err) => {
				this.fail_progress(this.err_text(err) || __("The release could not be completed."));
				this.busy = false;
				this.set_release_buttons_disabled(name, false);
				this.reload_data();
			},
		});
	}

	reject_release(name) {
		if (this.busy || !name) return;
		frappe.prompt(
			[
				{
					label: __("Reason for rejection"),
					fieldname: "reason",
					fieldtype: "Small Text",
					reqd: 1,
					description: __("At least 10 characters."),
				},
			],
			(values) => {
				const reason = (values.reason || "").trim();
				if (reason.length < 10) {
					frappe.msgprint({ title: __("Reason too short"), message: __("Please enter at least 10 characters."), indicator: "red" });
					return;
				}
				this.busy = true;
				this.set_release_buttons_disabled(name, true);
				frappe.call({
					method: `${PL_REL}.reject_release`,
					type: "POST",
					args: { name, reason },
					freeze: true,
					freeze_message: __("Rejecting release…"),
					callback: () => {
						frappe.show_alert({ message: __("Release rejected — {0}.", [name]), indicator: "orange" });
						this.busy = false;
						this.reload_data();
					},
					error: (err) => {
						this.busy = false;
						this.set_release_buttons_disabled(name, false);
						frappe.msgprint({ title: __("Could not reject"), message: this.err_text(err) || __("Please try again."), indicator: "red" });
					},
				});
			},
			__("Reject Raw-Material Release · {0}", [name]),
			__("Reject")
		);
	}

	set_release_buttons_disabled(name, disabled) {
		this.$root.find(`.pl-approve-btn[data-name="${$.escapeSelector(name)}"]`).prop("disabled", disabled);
		this.$root.find(`.pl-reject-btn[data-name="${$.escapeSelector(name)}"]`).prop("disabled", disabled);
	}

	err_text(err) {
		try {
			if (err && err._server_messages) {
				const msgs = JSON.parse(err._server_messages).map((m) => {
					try {
						return JSON.parse(m).message;
					} catch (e) {
						return m;
					}
				});
				return msgs.join("<br>");
			}
			if (err && err.message) return err.message;
		} catch (e) {
			/* noop */
		}
		return "";
	}

	// ── PROGRESS OVERLAY DRIVER (tied to the real frappe.call) ──────────
	// start_progress(cfg)  -> opens the overlay, builds step rows, bar at 0%.
	// advance_progress(i)  -> animate the bar smoothly up to step i's boundary
	//                          (capped below 100% until finish), marking earlier
	//                          steps done + the current step active. Called right
	//                          before / between the chained frappe.call's.
	// finish_progress(cfg) -> jump to 100%, green, all steps done, auto-close.
	// fail_progress(msg)   -> stop the bar where it is, red error state, show the
	//                          server message + a manual Close button.
	start_progress(cfg) {
		this._prog = {
			steps: cfg.steps || [],
			n: (cfg.steps || []).length,
			targetFrac: 0,
			curFrac: 0,
			raf: null,
			active: -1,
			done: false,
		};
		const $svg = this.$root.find("#pl-prog-ico-svg");
		this.$root.find("#pl-prog-ico").removeClass("done err");
		$svg.addClass("prog-spin").html('<path d="M21 12a9 9 0 11-6.2-8.6" opacity=".9"/>');
		this.$root.find("#pl-prog-title").text(cfg.title || "Working…");
		this.$root.find("#pl-prog-sub").text(cfg.subtitle || "");
		this.$root.find("#pl-prog-status").removeClass("is-done is-err");
		this.$root.find("#pl-prog-status-text").text("Starting…");
		this.$root.find("#pl-prog-foot").text("Auto-closes when complete").show();
		this.$root.find("#pl-prog-err").removeClass("show").empty();
		this.$root.find("#pl-prog-close").removeClass("show");
		this.$root.find("#pl-prog-fill").removeClass("done err").css("width", "0%");
		this.$root.find("#pl-prog-pct").text("0%");

		this.$root.find("#pl-prog-steps").html(
			this._prog.steps
				.map(
					(s, i) =>
						`<li id="pl-pstep${i}">` +
						`<span class="ps-node" id="pl-pnode${i}">${i + 1}</span>` +
						`<span class="ps-label">${this.esc(s.label)}</span>` +
						`<span class="ps-tag ${s.kind === "gate" ? "t-gate" : "t-auto"}">${s.kind === "gate" ? "🚦 gate" : "⚙️ auto"}</span>` +
						`</li>`
				)
				.join("")
		);

		this.$root.find("#pl-prog-scrim").addClass("open");
		document.body.style.overflow = "hidden";
		this._animate_progress();
	}

	advance_progress(stepIndex) {
		if (!this._prog || this._prog.done) return;
		const n = this._prog.n || 1;
		// cap the auto-advance at 90% so the final jump to 100% reads as "the
		// server confirmed success", never the animation pre-empting the result.
		const frac = Math.min(0.9, (stepIndex + 1) / n * 0.9);
		this._prog.targetFrac = Math.max(this._prog.targetFrac, frac);
		this._activate_step(Math.min(stepIndex, n - 1));
	}

	_activate_step(i) {
		if (!this._prog || i === this._prog.active) return;
		// mark all steps before i as done
		for (let k = 0; k <= i && k < this._prog.n; k++) {
			const $li = this.$root.find("#pl-pstep" + k);
			const $nd = this.$root.find("#pl-pnode" + k);
			if (k < i) {
				$li.removeClass("active").addClass("done");
				$nd.html("✓");
			}
		}
		const $cur = this.$root.find("#pl-pstep" + i);
		const $curNode = this.$root.find("#pl-pnode" + i);
		$cur.addClass("active").removeClass("done");
		$curNode.html('<span class="ps-spin">◔</span>');
		this.$root.find("#pl-prog-status-text").text(this._prog.steps[i] ? this._prog.steps[i].label : "");
		this._prog.active = i;
	}

	_animate_progress() {
		if (!this._prog) return;
		const tick = () => {
			if (!this._prog || this._prog.done) return;
			const p = this._prog;
			// ease toward the target fraction
			p.curFrac += (p.targetFrac - p.curFrac) * 0.08;
			if (Math.abs(p.targetFrac - p.curFrac) < 0.002) p.curFrac = p.targetFrac;
			const pct = Math.round(p.curFrac * 100);
			this.$root.find("#pl-prog-fill").css("width", pct + "%");
			this.$root.find("#pl-prog-pct").text(pct + "%");
			p.raf = requestAnimationFrame(tick);
		};
		this._prog.raf = requestAnimationFrame(tick);
	}

	finish_progress(cfg) {
		if (!this._prog) return;
		const p = this._prog;
		p.done = true;
		if (p.raf) cancelAnimationFrame(p.raf);
		// mark every step done
		for (let k = 0; k < p.n; k++) {
			this.$root.find("#pl-pstep" + k).removeClass("active").addClass("done");
			this.$root.find("#pl-pnode" + k).html("✓");
		}
		this.$root.find("#pl-prog-fill").addClass("done").css("width", "100%");
		this.$root.find("#pl-prog-pct").text("100%");
		this.$root.find("#pl-prog-ico").addClass("done");
		this.$root.find("#pl-prog-ico-svg").removeClass("prog-spin").html('<path d="M20 6L9 17l-5-5" stroke-width="2.6"/>');
		this.$root.find("#pl-prog-status").addClass("is-done");
		this.$root.find("#pl-prog-status-text").text(cfg.doneStatus || "Done");
		this.$root.find("#pl-prog-foot").text("Closing…");
		setTimeout(() => {
			this.close_progress();
			if (typeof cfg.onDone === "function") cfg.onDone();
		}, 1100);
	}

	fail_progress(msg) {
		if (!this._prog) {
			frappe.msgprint({ title: __("Error"), message: msg || __("Something went wrong."), indicator: "red" });
			return;
		}
		const p = this._prog;
		p.done = true;
		if (p.raf) cancelAnimationFrame(p.raf);
		if (p.active >= 0) {
			this.$root.find("#pl-pstep" + p.active).removeClass("active").addClass("err");
			this.$root.find("#pl-pnode" + p.active).html("✕");
		}
		this.$root.find("#pl-prog-fill").addClass("err");
		this.$root.find("#pl-prog-ico").addClass("err");
		this.$root.find("#pl-prog-ico-svg").removeClass("prog-spin").html('<path d="M18 6L6 18M6 6l12 12" stroke-width="2.6"/>');
		this.$root.find("#pl-prog-status").addClass("is-err");
		this.$root.find("#pl-prog-status-text").text(__("Failed"));
		this.$root.find("#pl-prog-title").text(__("Could not complete"));
		this.$root.find("#pl-prog-foot").hide();
		this.$root.find("#pl-prog-err").addClass("show").html(msg || __("Something went wrong. Please try again."));
		this.$root.find("#pl-prog-close").addClass("show");
	}

	close_progress() {
		this.$root.find("#pl-prog-scrim").removeClass("open");
		document.body.style.overflow = "";
		if (this._prog && this._prog.raf) cancelAnimationFrame(this._prog.raf);
		this._prog = null;
	}
}
