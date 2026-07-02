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

const PL_VERSION = "v1.0.0";
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
		this.is_admin = roles.includes("System Manager") || roles.includes("IT Head") ||
			frappe.session.user === "Administrator";
		this.is_store_mgr = roles.includes("Stores Manager") || this.is_admin;
		this.is_pm = roles.includes("PM") || roles.includes("Grain PM") || this.is_admin;

		// Add-Production form working state
		this.bom = null;
		this.bom_std = null;          // payload from fetch_bom_standard
		this.rm_state = [];           // editable RM rows: {item_code,item_name,std_qty,uom,source_warehouse,qty,edited,removed}
		this.bp_state = [];           // by-product rows (auto-scaled, read-only)

		this.busy = false;
		this.boms = [];               // active BOM list for the picker
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

			<div class="sec-title">
				<span class="bar"></span> Production Automation Pipeline &mdash; One Click, One Human Gate
				<span class="feas-tag tag-auto">&#9881; Auto</span>
				<span class="feas-tag tag-gate">&#128682; Store-Manager gate</span>
			</div>
			<div class="flow-panel glass">
				<div class="flow-head">
					<div class="flow-title">How a production run flows &mdash; PM logs it, the system does the rest</div>
					<div class="flow-legend">
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
					<span class="sf-chip b-pending"><span class="bdot"></span>Pending Stores Release</span>
					<span class="sf-arrow">&rarr;</span>
					<span class="sf-chip b-released"><span class="bdot"></span>Released</span>
					<span class="sf-arrow">&rarr;</span>
					<span class="sf-chip b-completed"><span class="bdot"></span>Completed</span>
				</div>
			</div>

			<div class="sec-title">
				<span class="bar" style="background:var(--green)"></span> At a glance
				<span class="feas-tag tag-auto">Live &middot; ${this.esc(PL_DOCTYPE)}</span>
			</div>
			<div class="kpi-grid" id="pl-kpi-grid"></div>

			${this.is_store_mgr ? `
			<div class="sec-title">
				<span class="bar" style="background:var(--amber)"></span> The One Human Gate &mdash; Store Manager Raw-Material Release
				<span class="feas-tag tag-gate">&#128682; Awaiting approval</span>
			</div>
			<div id="pl-release-zone"></div>
			` : ``}

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
				<div class="form-sec">
					<div class="form-sec-h"><span class="step-pill">Step 1</span> Select BOM</div>
					<div class="fld">
						<label>Bill of Materials <span class="req">*</span></label>
						<select class="inp" id="pl-bom-select"><option value="">Loading BOMs&hellip;</option></select>
					</div>
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
						<thead><tr><th>By-product</th><th class="r">Std / batch</th><th class="r">Qty</th><th>UOM</th></tr></thead>
						<tbody id="pl-bp-body"></tbody>
					</table>
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
						<span>Raw material is <b>auto-calculated from the BOM &times; produced qty</b>. You may edit a quantity or remove a line &mdash; that changes the material <b>RELEASE only</b>; finished-goods consumption always follows the BOM recipe (reconciled as a WIP variance).</span>
					</div>
				</div>
			</div>
			<div class="so-foot">
				<button class="cancel-btn" id="pl-cancel-btn">Cancel</button>
				<button class="submit-btn" id="pl-submit-btn" disabled>
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
					Submit for Store Release
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
		this.$root.on("click", ".pl-approve-btn", function () {
			self.approve_release($(this).data("name"));
		});
		this.$root.on("click", ".pl-reject-btn", function () {
			self.reject_release($(this).data("name"));
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
		this.load_log();
		if (this.is_store_mgr) this.load_pending_releases();
	}

	load_log() {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: PL_DOCTYPE,
				fields: [
					"name", "bom", "production_item", "production_item_name",
					"actual_produced_qty", "production_uom", "ts_variance_status",
					"material_variance_pct", "produced_variance_pct",
					"work_order", "linked_stock_entry", "release_stock_entry", "modified",
				],
				order_by: "modified desc",
				limit_page_length: 30,
			},
			callback: (r) => {
				this.render_log(r.message || []);
				this.render_kpis(r.message || []);
			},
			error: () => {
				this.render_log([]);
				this.render_kpis([]);
			},
		});
	}

	load_pending_releases() {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: PL_DOCTYPE,
				filters: { ts_variance_status: "Pending Stores Release" },
				fields: [
					"name", "bom", "production_item", "production_item_name",
					"actual_produced_qty", "production_uom", "work_order",
					"release_stock_entry", "submitted_by", "owner", "modified",
				],
				order_by: "modified desc",
				limit_page_length: 20,
			},
			callback: (r) => this.render_release_zone(r.message || []),
			error: () => this.render_release_zone([]),
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
			varSum = 0,
			varN = 0;
		rows.forEach((r) => {
			const st = norm(r.ts_variance_status);
			if (st === "pending stores release") pending++;
			if (st === "released") released++;
			if (st === "completed") {
				completed++;
				if (r.material_variance_pct != null) {
					varSum += Math.abs(flt(r.material_variance_pct));
					varN++;
				}
			}
			const md = (r.modified || "").slice(0, 10);
			if (md === today && (st === "completed" || st === "released")) {
				producedToday += flt(r.actual_produced_qty);
				runsToday++;
			}
		});
		const avgVar = varN ? (varSum / varN).toFixed(1) : "0.0";
		const KPIS = [
			{ ico: "🛢️", cls: "ki-green", lbl: "Produced Today", val: this.fmt(producedToday), unit: "", sub: `${runsToday} run(s) released/posted today` },
			{ ico: "🚦", cls: "ki-amber", lbl: "Pending Releases", val: String(pending), unit: "", sub: "awaiting Store Manager" },
			{ ico: "⚙️", cls: "ki-blue", lbl: "Released (in process)", val: String(released), unit: "", sub: "auto-chain recoverable" },
			{ ico: "✓", cls: "ki-purple", lbl: "Completed (recent)", val: String(completed), unit: "", sub: "in the last 30 runs" },
			{ ico: "📊", cls: "ki-slate", lbl: "Avg Material Variance", val: avgVar, unit: "%", sub: "completed runs" },
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
			"Completed": "b-completed",
			"Rejected": "b-rejected",
			"Cancelled": "b-cancelled",
		};
		const cls = map[status] || "b-draft";
		return `<span class="badge ${cls}"><span class="bdot"></span>${this.esc(status || "Draft")}</span>`;
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
						`<td>${this.badge(r.ts_variance_status)}</td>` +
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
		$z.html(
			`<div class="release-stack">` +
				rows
					.map((r) => {
						const itemName = r.production_item_name || r.production_item || "—";
						return (
							`<div class="release-card glass">` +
							`<div class="release-banner">` +
							`<div class="rb-ico">🚦</div>` +
							`<div><div class="rb-t">Release request &middot; ${this.esc(r.name)}</div>` +
							`<div class="rb-s">From ${this.esc(r.submitted_by || r.owner || "—")} &middot; updated ${this.esc(frappe.datetime.str_to_user(r.modified))}</div></div>` +
							`<span class="rb-badge badge b-pending"><span class="bdot"></span>Pending Stores Release</span>` +
							`</div>` +
							`<div class="release-body">` +
							`<div class="rel-meta">` +
							`<div class="rm"><div class="k">BOM</div><div class="v mono">${this.esc(r.bom || "—")}</div></div>` +
							`<div class="rm"><div class="k">Produced Qty</div><div class="v">${this.fmt1(r.actual_produced_qty)} ${this.esc(r.production_uom || "")} ${this.esc(itemName)}</div></div>` +
							`<div class="rm"><div class="k">Work Order</div><div class="v mono">${this.esc(r.work_order || "—")}</div></div>` +
							`<div class="rm"><div class="k">Release SE</div><div class="v mono">${this.esc(r.release_stock_entry || "—")}</div></div>` +
							`</div>` +
							`<div class="release-note"><span style="font-size:15px;line-height:1">ℹ️</span>` +
							`<span>On <b>Release</b>, the system auto-completes the rest: submits the Material Transfer (${this.esc(src)} &rarr; ${this.esc(wip)}), runs Job Cards if it's an operations BOM, posts the Manufacture Stock Entry, and closes the Work Order &mdash; <b>no further clicks</b>.</span></div>` +
							`<div class="release-actions">` +
							`<button class="btn btn-approve pl-approve-btn" data-name="${this.esc(r.name)}">` +
							`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg> Release</button>` +
							`<button class="btn btn-reject pl-reject-btn" data-name="${this.esc(r.name)}">` +
							`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg> Reject</button>` +
							`</div></div></div>`
						);
					})
					.join("") +
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
		if (!this.bp_state.length) {
			$bp.html('<tr class="mat-empty"><td colspan="4">No by-products on this BOM.</td></tr>');
		} else {
			$bp.html(
				this.bp_state
					.map(
						(b) =>
							`<tr><td><div class="mat-item"><span class="mn">${this.esc(b.item_name || b.item_code)}</span><span class="mc mono">${this.esc(b.item_code)}</span></div></td>` +
							`<td class="r std-q">${this.fmt1(b.std_qty)}</td>` +
							`<td class="r"><b class="mono">${this.fmt1(this.round_qty(flt(b.std_qty) * s))}</b></td>` +
							`<td class="uom-cell">${this.esc(b.uom || "")}</td></tr>`
					)
					.join("")
			);
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
		const produced = flt(this.$root.find("#pl-produced-qty").val());
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
			actual_qty: this.round_qty(flt(b.std_qty) * this.scale()),
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
			subtitle: "Draft → Pending Stores Release",
			steps: [
				{ label: "Creating the production entry…", kind: "auto" },
				{ label: "Validating + creating Work Order…", kind: "auto" },
				{ label: "Scaling raw material from BOM…", kind: "auto" },
				{ label: "Building draft release (Material Transfer)…", kind: "auto" },
				{ label: "Routing to Store Manager…", kind: "gate" },
			],
		});

		// Step 1 — insert the draft TS Production Entry.
		this.advance_progress(0);
		frappe.call({
			method: "frappe.client.insert",
			args: {
				doc: {
					doctype: PL_DOCTYPE,
					bom: this.bom,
					actual_produced_qty: produced,
					standard_batches: 1,
					materials: materials,
					byproducts: byproducts,
				},
			},
			callback: (r) => {
				const doc = r.message;
				if (!doc || !doc.name) {
					this.fail_progress(__("The production entry could not be created."));
					this.busy = false;
					return;
				}
				// advance through the "creating" steps while the release call runs
				this.advance_progress(1);
				// Step 2 — submit_for_release (creates WO + draft release SE, flips status).
				frappe.call({
					method: `${PL_REL}.submit_for_release`,
					type: "POST",
					args: { name: doc.name },
					callback: (rr) => {
						const m = rr.message || {};
						this.advance_progress(4);
						this.finish_progress({
							doneStatus: "Submitted ✓ — Pending Store Manager release",
							onDone: () => {
								frappe.show_alert({
									message: __("Submitted {0} — pending Store Manager release.", [doc.name]),
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
