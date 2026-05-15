# Trustbit Ethanol — TS Gate Entry System

**Version:** 2.10.0.7 | **ERPNext:** V15 | **Module:** TS Gate Entry

Custom ERPNext v15 app for **Betul Bio Fuel Pvt. Ltd.** — an ethanol manufacturing plant. Handles the complete vehicle gate-to-exit lifecycle, multi-level PO/MR approval with CC-based routing, budget management, item creation, quality inspection, and interactive dashboards with a **blind token-based system** designed to prevent manipulation.

## Recent ships (May 2026)

| Version | Date | Change |
|---|---|---|
| **2.10.0.7** | 16 May | **BBPL Material Request print format — Remarks section + quotation note** — adds new conditional Remarks section rendering `doc.ts_remark` (Small Text Custom Field on MR — previously NOT shown in BBPL MR format even though the field has been populated in production). Uses existing `.bbpl-section-heading` + `.bbpl-terms-body` styling. Below it, an unconditional yellow-highlighted ⚠ Note callout: "Please mention material request no. in your quotation." — instructs suppliers when the MR is shared as a quotation request. Both inserted between existing Terms and Conditions section and Footer. Demo verified on PR-IT-26-00109 (ts_remark='TEST': Remarks + Note both shown) and BBPL-TRAN-MEC-26-00014 (ts_remark=None: Remarks hidden, Note still shown). Security 0/0/0/0/1 INFO (autoescape protects `{{ doc.ts_remark }}` — no `\|safe` filter applied). Pure print-format JSON edit. 2 files +1/-1 LOC each. 77 active sessions preserved through deploy. Prod regression 17/17 PASS. |
| **2.10.0.6** | 16 May | **BBPL Purchase Order print format — Item Remark col, Payment Description fallback, invoice-match note** — three Jinja edits to the PO format. (1) New 'Item Remark' column added between Item and Quantity, reading `item.ts_item_remark` (Data field). tfoot colspans bumped 6→7 to match the new column count. (2) Payment Terms 'Description' cell logic flipped from `{{ ps.payment_term or ps.description or '' }}` (which showed the link name first) to `{{ ps.description or (frappe.db.get_value('Payment Term', ps.payment_term, 'description') if ps.payment_term else '') or '' }}` — prefer user-entered description, fall back to Payment Term template description. (3) Unconditional yellow-highlighted ⚠ Note "All invoice entries must match the details in the purchase order." placed standalone between Grand Total and Payment Terms section. CSS polish: `white-space: nowrap` on `.num` and `.total-val` so ₹ amounts in crores fit on one line. Column widths rebalanced (S.No 30, Item Remark 85, Quantity 50, Unit 40, Rate 70, GST% 45, Amount 110px). Demo verified on PUR-ORD-2026-03485, PUR-ORD-2026-03539, prod smoke on BBPL-PO-OT-2026-00181. Security 0/0/0/0/2 INFO (autoescape + ORM-bound Payment Term lookup). 2 files. 77 sessions preserved. Prod regression 17/17 PASS. |
| **2.10.0.5** | 15 May | **P0 fix — Material Request amend save blocked by hidden `from_warehouse`** — ERPNext native validator (`BuyingController.validate_from_warehouse` at buying_controller.py:262) throws "Row #N: Accepted Warehouse and Supplier Warehouse cannot be same" when `item.from_warehouse == item.warehouse`. For non-Material-Transfer MR types (Purchase / Material Issue / Manufacture / Customer Provided Material), both row-level `from_warehouse` AND header `set_from_warehouse` are HIDDEN by `depends_on:eval:parent.material_request_type=="Material Transfer"`. During AMEND, stale values carry over invisibly; users get blocked on save with NO UI path to fix. Client (mechanical@betulbiofuel.com → escalated to hr.navaahar's amended Purchase MR `PR-HR-26-00022`) hit this live. Fix: new file `ts_gate_entry/ts_mr_warehouse_guard.py` with `mr_clear_invisible_from_warehouse(doc, method=None)` wired as `Material Request.before_validate` — when `material_request_type != "Material Transfer"`, force-clears `doc.set_from_warehouse` + every row's `from_warehouse`. Material Transfer MRs unchanged. Drop-ship safety verified (BBPL doesn't use drop-ship; non-Transfer drop-ship in ERPNext uses `Item.supplier`, not `from_warehouse`). Lesson 272 candidate: `depends_on` hides field from UI but NOT from `validate()` — auto-clear server-side OR remove the depends_on. 5 demo smoke scenarios PASS (Purchase collision→saves cleared, Transfer valid→preserved, Issue collision→cleared, Manufacture collision→cleared, Transfer legit-collision→still rejected). Prod smoke on synthesized PR-HR-26-00026 → saved cleanly with both fields auto-cleared. 3 files +35/-1 LOC. 77 sessions preserved. Prod regression 17/17 PASS. |
| **2.10.0.4** | 15 May | **BBPL Purchase Receipt print format polish** — header rewritten to use `<img src="/files/BBPL-LH.png">` letterhead image (replaces inline header table); bold wraps on Receipt No / Supplier / Source PO / Bill No values for prominence; Vehicle No + LR No now auto-fetch through the Gate Entry → Token chain (`TS Gate Entry.vehicle_number_display` / `lr_number` then `TS Token.vehicle_number` fallback) when PR's own fields are blank; Transporter row removed per user request; Source PO moved into the main info table (out of Receipt Context). Pure print-format edit — no Python / no permission / no schema change. Demo regression 17/17 BEFORE+AFTER, live-data-tester 7/7 PASS (token+chain, no-token, empty vehicle_no, cancelled, GST 18%, Exempt, Terms set). Security 0/0/0/0/2 INFO (pre-existing `\|safe` filters on privileged Terms / Item description fields, unchanged). 2 files (print format JSON + version bump). |
| **2.10.0.3** | 15 May | **PO-from-MR exemption in budget breach detector** — `_submit_po_for_approval` now bypasses `detect_budget_breach_for_po` when every PO line claiming `material_request` verifies as `docstatus=1 + ts_mr_status='Approved' + company=doc.company` (fail-closed: forged / draft / cancelled / pending / nonexistent / cross-company refs do NOT bypass, the breach check runs as normal). Standalone POs unchanged. Fixes "every PO from MR triggers Pending Budget Override" report. Demo data: 3 stale ₹6.3cr+₹65L+₹22L TTPL Test POs on Coal-BBPL cancelled (committed dropped 271% → 0%); 1 PO blocked by TS G1 link (₹15L, 8% utilization, acceptable). Forgery resistance: 7 direct-logic tests on demo PASS (Approved+same-company = bypass, all 6 others = blocked). 2 files +29/-2 LOC. Security 0/0/0 after MEDIUM fix. |
| **2.10.0.2** | 15 May | **Audit-log enum fix** — extends `TS Approval Log.action` Select options to include `Held for Budget Override` + `Resumed after Budget Override` (added via the doctype JSON `options`, picked up by `bench migrate` `sync_fields()` — no Property Setter / Lesson 239 patch needed). Replaces 2 call-site literals (`"Resumed"` → `"Resumed after Budget Override"`) at the budget-override resume paths in `_submit_*_for_approval`. Existing try/except wrappers around audit writes are KEPT as defense-in-depth per Lesson 238 — audit log is best-effort and must never block the primary workflow. Closes open item (c) from the v2.10.0 ship. Demo regression 17/17 BEFORE+AFTER, demo + prod smoke INSERT of both new action values on a real PO succeeded then cleanup. Prod regression 17/17. 3 files +4/-4 LOC. 22 active sessions preserved through deploy. |
| **2.10.0.1** | 15 May | **Best-effort wrap on budget-override audit log** — `_log_approval_action` + `_log_mr_action` calls inside `_submit_*_for_approval` budget-override branches wrapped in try/except per Lesson 238. Smoke on PUR-ORD-2026-04501 caught: action="Held for Budget Override" not in TS Approval Log Select options → ValidationError; state mutations had already committed via db_set, but exception propagated. Fix: best-effort audit row; flow no longer blocks. Override doc + tabComment carry primary audit trail. Follow-up closed by v2.10.0.2 (above). 2 files +42/-19 LOC, regression 17/17 PASS prod. |
| **2.10.0** | 15 May | **Budget-Exceeded → CEO Approval flow on MR + PO** — new submittable DocType `TS Budget Override Approval` (autoname `BO-{YYYY}-{#####}`, 48 fields, permlevel=1 tamper-wall) + child `TS Budget Override Breach Line` for multi-CC breakdowns. New status `Pending Budget Override` on `ts_mr_status` + `ts_approval_status`. Kill switch `TS Settings.ts_budget_override_flow_enabled` (default ON). Breach detector covers annual + monthly (whichever first), uses `transaction_date`-aware month. 6 whitelisted endpoints + `cancel_linked_override_on_source_cancel` hook. Linked-override banners on MR/PO forms via shared `_show_ts_banner` / `_show_ts_banner_po`. Approve/Reject/Cancel form buttons with typed-confirm on Reject (≥10-char comment + source-name match). Replaces legacy PO hard-block + manual `ceo_budget_override` (deprecated when kill switch ON; re-enables as emergency escape valve when OFF). **Bundled v2.9.17.9 FY fix:** `_resolve_fiscal_year` now date-based (finds FY where today ∈ [year_start, year_end]) + MR/PO banners show FY label. Phase 4 audits all GREEN: security 0/0/0/0/0 final, live-data-tester 30/4 (FAIL1 + FAIL2 fixed, FAIL3 deferred to v2.10.1 polish), ui-designer SHIP, guardian 14/14, predictor CRITICAL-mitigated. E2E smoke approved real demo `BO-2026-08696` → source PO transitioned `Approved → Pending CEO` with TS Approval Log action="Resumed". Regression 17/17 PASS pre+post all fixes. |
| **2.9.17.9** | 15 May | **P0 hotfix — server-side guard against MR approval-chain bypass.** Stock User role had submit=1 on Material Request; users could bypass `submit_for_approval` via native Submit button / Ctrl+Shift+Enter / `frappe.client.submit`. Three prod MRs (PR-CF-PRD-26-00008, 00008-1, 00011 by `takshak.sambare`) had been stranded docstatus=1 with `ts_mr_status='Not Submitted'`, route=NULL. Fix: new `mr_before_submit_block_direct(doc, method)` wired as `Material Request.before_submit`. State-based guard (no flag): bypass Material Transfer/Issue; bypass legit `_approve_mr` Final Approve path (route+status='Approved' set via db_set before submit); else throw. Recovery: cancelled originals, amended into `PR-CF-PRD-26-00008-2` + `PR-CF-PRD-26-00011-1` both at `Pending Department Head` step 2/3. Lessons 270 (`before_submit` fires AFTER docstatus set to 1) + 271 (Lesson 252 flush needs fresh console for smoke). |
| **2.9.17.4** | 14 May | **BBPL Purchase Receipt print format + Print PDF button** — new branded PDF print format mirroring locked PO/MR pattern (table-based layout per Lesson 232, Jinja namespace-correct loop scope, letterhead-overlay fix). Toolbar "🖨 Print PDF" button on submitted PRs (`pr_pi_columns.js` adds `_add_bbpl_print_button` calling `frappe.utils.print_format.download_pdf`). Supplier Address resolved via Address doctype lookup; "Location From PO" sourced from `ts_delivery_location`; per-item GST cell uses Item Tax Template name parsing with `doc.taxes[0].rate` fallback. Includes parse_json bug-fix from review iteration. |
| **2.9.16.7** | 13 May | TS Gate Entry DocPerm seeder for demo/prod parity — idempotent `_seed_gate_entry_docperm()` mirrors v2.9.15.2 Cost Center pattern, re-asserts 10-role DocPerm set on every migrate (Accounts Manager, CEO, G1 Security, G2 Gate Operator, IT Head, MD, Quality Inspector, Stores User, System Manager, Weighbridge Operator). Forward-safe against Lesson 169 master-data drift between environments. |
| **2.9.16.6** | 13 May | **HOTFIX** — Weighbridge "No permission for TS Settings" popup eliminated. `ts_weighbridge_log.js:45` was calling `get_single_value("TS Settings", "ts_flow_v28_enabled")` directly; Weighbridge Operator role lacks TS Settings read perm → modal on every new entry. Fix: new role-scoped `get_flow_v28_flag()` whitelist helper (Lesson 168 pattern, same as `get_g2_print_mode` / `get_two_pass_flag`). |
| **2.9.16.5** | 13 May | **P0 HOTFIX** — `ts_gate_entry/api/` package introduced in v2.9.16 silently shadowed pre-existing `ts_gate_entry/api.py` (Python: package wins over module of same name). 7 whitelisted endpoints unreachable for ~3 hours (Weighbridge token search, Weighbridge weight fetch, G2 print mode, two-pass flag, PO autocomplete, PO lifecycle, SLA scheduler). Fix: move all `api.py` content into `api/__init__.py`, delete `api.py`. New Lesson 264 captured. |
| **2.9.16.4** | 13 May | Purchase Invoice "Token / Receipt Context" section — 5 read-only fields (Token Number/Gate Pass, RST Number, Quality Inspection, Deduction Sheet, Purchase Receipt) snapshotted at insert from first linked PR via items[].purchase_receipt. DS link chain: PR.ts_token → QI (by token_number) → DS (by quality_inspection). One-time backfill on deploy populated 327 existing prod PIs. |
| **2.9.16.3** | 13 May | TS Deduction Suggestion: `purchase_order` Link Custom Field added to Receipt Context section, snapshotted at insert from source QI. Seeder also auto-bumps DocType.modified to invalidate browser form-meta cache (Lesson 263 — caught live; without the bump, newly-added Custom Fields stay invisible until users run `localStorage.clear()`) |
| **2.9.16.2** | 12 May | TS Stock Balance Computed audit-grade Script Report — wraps native, replaces stored val_rate with bal_val/bal_qty live, adds Drift % audit signal column. Stock User excluded (audit-grade). |
| **2.9.16.1** | 12 May | Stock Reports sub-workspace under BBPL Ethanol (seq=10) with 4 shortcuts: TS Stock Ledger FIFO, Stock Ledger (native), Stock Balance → TS Stock Balance Computed, Stock Entry. |
| **2.9.16** | 11 May | TS Stock Ledger FIFO custom Script Report (Cost Center via LEFT JOIN tabGL Entry + COALESCE expense-account preference per Lesson 257, Total Amount = abs(qty)×rate, branded A4-landscape PDF export with BBF logo, 5000-row hard cap). |
| **2.9.15.2** | 12 May | Cost Center read perm seeder for 7 BBF approval roles (Grain Purchase Manager, Department Head, General Manager, AVP, Grain Manager, Quality Manager, Admin Reception) — fixes "No permission for Cost Center" popup blocking PO/MR list views (standard-filter Link autocomplete required CC read) |
| **2.9.15.1** | 12 May | TS Deduction Suggestion "Receipt Context" — 5 read-only fields (Vehicle Number, RST Number, Total Net Weight kg, Supplier Code, Supplier Name) surfaced above QI section; 4 snapshot at insert from QI+PO, net_weight live-fetches from matching Weighbridge Log |
| **2.9.14.6** | 11 May | Stores Workflow submit/approve unblocked (tamper-guard flag fix in ts_mr_transfer) |
| **2.9.14.5** | 11 May | Material Request "Available Qty" shows live stock via new onload hook (ts_mr_available_refresh) |
| **2.9.14.4** | 11 May | PurchaseInvoice class override allows `update_stock=1` on Purchase Returns |
| **2.9.14.3** | 9 May | PI `update_stock` checkbox now visible on Purchase Returns (Property Setter override) |
| **2.9.14.2** | 9 May | G1 → G2 vehicle_origin always-sync (dropped over-aggressive empty-check) |
| **2.9.14.1** | 9 May | SR → PO restore MR `material_request_type` after mapping (try/finally pattern) |
| **2.9.14** | 9 May | Vehicle Origin Custom Field on G1/G2/PR/PI + Awesomplete autocomplete + propagation hooks |
| **2.9.13** | 8 May | Purchase Invoice `before_submit` validator: strict bill_no/bill_date match against linked PRs |
| **2.9.12.7** | 8 May | TS Quality Inspection `populate_template_rows` perm fix for new (unsaved) docs |
| **2.9.12.5** | 7 May | Approval Flow Health Check page (7 validators + 2 capability simulators) |

---

## Key Features

| # | Feature | Version |
|---|---------|---------|
| 1 | **Token Lifecycle** — Stock IN (9-step) + Stock OUT (5-step) + Gate Pass | v1.0+ |
| 2 | **Category-Based PO Approval** — Store/Chemical/Grain/Coal routing | v2.0 |
| 3 | **Cost-Center MR Approval** — CC-based routing with Hold/Resume | v2.0+ |
| 4 | **CC Approval Config** — Per-CC indent/approval/notification with data isolation | v2.5 |
| 5 | **Budget Management** — Monthly budgets, PO blocked on exceed, MR warning banner | v2.1+ |
| 6 | **Item Creator** — 5-step wizard + bulk CSV import | v1.4 |
| 7 | **Quality & Deductions** — Grain/Coal grading with auto-calculations | v1.0 |
| 8 | **Interactive Dashboards** — CEO, MD, Gate Operations, Procurement + Wall Displays | v2.4 |
| 9 | **Gate Pass & Visitor** — Visitor tracking with G2 checkpoint, Admin Reception | v2.2 |
| 10 | **Material Inspection** — Non-RM approve/reject/hold with 4-stage SLA | v2.2 |
| 11 | **Stock OUT / Dispatch** — Reversed weighbridge, Delivery Note on exit | v2.3 |
| 12 | **Weighbridge Hardware** — Fetch weight directly from weighbridge via HTTP | v2.6 |

---

## Architecture

### 40 DocTypes

**Gate Operations (15):** TS Token, TS Gate Entry, TS Gate Entry Item, TS Gate Entry PO, TS Weighbridge Log, TS Quality Inspection, TS Deduction Sheet, TS Deduction Line, TS Unloading Entry, TS Transport Master, TS Transporter Vehicle, TS Vehicle Master, TS Driver Master, TS Gate Pass Destination, TS Visiting Company, TS Visitor

**Approval System (10):** TS Purchase Category, TS Purchase Category Item, TS PO Approval Rule, TS PO Approval Step, TS MR Approval Route, TS MR Approval Step, TS MR Route Cost Center, TS Notification Recipient, TS Approval Log, TS CC Approval Config, TS CC Approval User

**Budget (3):** TS Budget Proposal, TS Budget Proposal Item, TS Budget Override Log

**Item Management (5):** TS Item Creator, TS Item Creator Variant, TS Item Code Settings, TS Code Counter, TS Variant

**Masters (3):** TS Settings, TS Location, TS Material Inspection, TS Material Inspection Item

### 9 Custom Pages

| Page | URL |
|------|-----|
| Item Creator | `/app/item-creator` |
| Item Bulk Import | `/app/item-bulk-import` |
| Budget Control Matrix | `/app/budget-control-matrix` |
| Procurement Dashboard | `/app/procurement-dashboard` |
| Gate Operations Dashboard | `/app/gate-operations-dashboard` |
| Gate Wall Display | `/app/gate-wall-display` |
| CEO Dashboard | `/app/ceo-dashboard` |
| CEO Wall Display | `/app/ceo-wall-display` |
| MD Dashboard | `/app/md-dashboard` |

### 9 API Files

| File | Purpose |
|------|---------|
| `api.py` | PO search, token SLA, weighbridge fetch, PO lifecycle |
| `api_bulk_import.py` | CSV bulk item import |
| `ts_po_approval.py` | PO/MR approval controller, Hold/Resume, CC-aware notifications |
| `ts_budget.py` | Budget proposal, PO check, CEO override, CC budget status |
| `ts_dashboard_procurement.py` | Procurement dashboard API |
| `ts_dashboard_gate.py` | Gate operations dashboard API |
| `ts_dashboard_ceo.py` | CEO dashboard API |
| `ts_dashboard_md.py` | MD dashboard API with PIN lock |
| `dashboard_overrides.py` | PO Connections panel |

### 14 Custom Roles

G1 Security, G2 Gate Operator, Weighbridge Operator, Stores User, Quality Inspector, IT Head, Department Head, General Manager, CEO, MD, Purchase Manager, Grain Purchase Manager, AVP, Admin Reception

---

## Installation

```bash
bench get-app https://github.com/zxrrcpandey/betul_biofuel.git --branch develop
bench --site your-site install-app trustbit_ethanol
bench --site your-site migrate
```

## Deployment (Existing Site)

```bash
chown -R frappe:frappe .git
git pull upstream develop
bench build --app trustbit_ethanol
bench --site your-site migrate
bench --site your-site clear-cache
supervisorctl restart all
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| **2.10.0.4** | 2026-05-15 | **BBPL Purchase Receipt print format polish** — header rewrite to `<img src="/files/BBPL-LH.png">`, bold wraps on Receipt No / Supplier / Source PO / Bill No values, auto-fetch Vehicle No + LR No through Gate Entry / Token chain, Transporter row removed, Source PO promoted into main info table. Pure print-format change. 2 files. |
| **2.10.0.3** | 2026-05-15 | **PO-from-MR exemption** — `_submit_po_for_approval` bypasses `detect_budget_breach_for_po` when every claimed `item.material_request` verifies as `docstatus=1 + ts_mr_status='Approved' + same company` (fail-closed; forged/draft/cancelled/pending/nonexistent/cross-company refs all blocked). Standalone POs still check. Fixes "every PO from MR shows Budget Override" user report. 2 files +29/-2 LOC. |
| **2.10.0.2** | 2026-05-15 | Budget-override **audit-log enum fix** — `TS Approval Log.action` Select options extended (`Held for Budget Override`, `Resumed after Budget Override`); call sites use proper strings. Closes open item (c) from v2.10.0 ship. 3 files +4/-4 LOC. |
| **2.10.0.1** | 2026-05-15 | Best-effort try/except wrap on budget-override audit-log writes (Lesson 238). Workaround for missing Select options on `TS Approval Log.action` — properly fixed in v2.10.0.2. |
| **2.10.0** | 2026-05-15 | **Budget-Exceeded → CEO Approval flow** on MR + PO. New `TS Budget Override Approval` doctype + breach line child + 6 whitelisted endpoints + cancel-cascade hook. Kill switch `ts_budget_override_flow_enabled`. New status `Pending Budget Override`. Bundled v2.9.17.9 FY fix (`_resolve_fiscal_year` date-based). ~2730 LOC, 22 files. |
| **2.9.17.9** | 2026-05-15 | **P0 — server-side guard against MR approval-chain bypass.** `mr_before_submit_block_direct` hook blocks native Submit / Ctrl+Shift+Enter / API bypass. Recovery of 3 stuck prod MRs (PR-CF-PRD-26-00011 → -00011-1, 00008-1 → 00008-2, PR-PRC-BIO-26-00008 → -00008-1). 3 files +40/-1 LOC. Lessons 270 + 271. |
| **2.9.17.8** | 2026-05-14 | Item Creator Item Name **duplicate-detection typeahead** — as user types (3+ chars) in Item Name field, dropdown appears with up to 10 existing items matching via 2-stage search: Stage 1 token-LIKE (handles wrong-sequence like `rice broken` → `Broken Rice`) and Stage 2 `difflib.get_close_matches` Levenshtein fallback (handles typos like `Ricee` → `FCI Rice`). Sorted by `usage_count DESC` so most-used items rank first. Click row opens modal with item details (code/name/group/brand/UOM/usage) + "Open this item" route or "Continue — my item is different". New `@frappe.whitelist() search_existing_items()` helper (fail-closed try/except, perm gate, parameterized SQL). Full keyboard nav (Down/Up/Enter/Esc), 300ms debounce, click-outside-close, dark-mode + mobile breakpoints. 5 files, ~457 LOC. Performance: 30-45ms server-side on prod's 3258 items. |
| **2.9.17.7** | 2026-05-14 | **Material Type dropdown** Custom Field added to TS Gate Entry form. New Select field `ts_material_type` with 15 options (Store Material / Scrap / Bardana / Fly Ash / DWGS / Maize / Rice / Coal / DDGS / Ethanol / Liquid Co2 / Iron / DORB / Fusel oil / Dry Ice). Inserted after `material_flow` in flow_section tab. Installs via `create_custom_fields()` on `after_migrate` hook. No JS / no Python logic / no permission change. |
| **2.9.17.6** | 2026-05-14 | Item Creator default Stock UOM flipped `Kg → Nos`. Value-only change (5 occurrences in `item_creator.js`). Validated against prod data: 3010 items use Nos vs 248 use Kg (92% Nos). Operators no longer need to re-select Nos on every new item. Kg remains selectable. |
| **2.9.17.5** | 2026-05-14 | Item Creator (`/app/item-creator`) flat-form rewrite — 5-step wizard collapsed into single scrolling page with 5 collapsible `<details>` accordion sections + sticky live-code preview bar. Bundled audit fixes: Lesson 168 whitelisted `get_variant_code()` helper (fail-closed), Lesson 163 rogue `__init__.py` removal, Lesson 174 version badge `v2.9.17.5`, dark-mode contract (17 `[data-theme="dark"]` overrides), mobile breakpoints (768px + 480px), accessibility (native `<details>` keyboard nav, `aria-live` on sticky code, `role=radio/tab`, `:focus-visible`). Net −285 LOC. 6 files. |
| **2.9.17.4** | 2026-05-14 | BBPL Purchase Receipt branded Print Format + 🖨 Print PDF button on PR form toolbar. Print format mirrors BBPL PO/MR layout with Supplier Invoice No/Date, Vehicle, Warehouse, Cost Center, Project, optional Supplier Address (multi-line via `frappe.get_doc('Address', ...)`), Location (from items[0].ts_delivery_location), conditional Receipt Context (Token/GE/RST/WB Net/QI/DS/Source PO/QC Status), items table with per-item GST cell (amount bold + rate label), totals + Amount in Words + Terms + Received/Verified signatures + BBPL footer. |
| **2.9.16.7** | 2026-05-13 | TS Gate Entry DocPerm seeder for demo/prod parity — fixed silent permission gap when Custom DocPerm overrides Standard DocPerm; added `_seed_gate_entry_docperm()` idempotent seeder (10 roles incl. Weighbridge Operator). Lesson 169 pattern. |
| **2.9.16** | 2026-05-13 | Stock Reports module (TS Stock Ledger FIFO + TS Stock Balance Computed), DSG PO link, Quality Lab dashboard, PI Receipt Context (5 fields with backfill on 327 PIs). |
| **2.9.15.1** | 2026-05-12 | TS Deduction Suggestion Receipt Context — 5 fields above QI section (vehicle, RST, supplier code/name snapshots + live Net Weight via whitelisted `get_live_net_weight`). |
| **2.9.x** | 2026-04 to 2026-05 | Stores Receiving Dashboard with token-based GRN, Stock Workflow MR transfers, Quality Inspection refinements, Purchase Invoice Receipt Context, MR Available Qty live refresh, PI/PR column updates, MR/PO Print PDF buttons, RST capture on Weighbridge, two-pass G2 gate flow. |
| **2.6.1** | 2026-04-01 | G1 driver details (name/license/mobile) + G2 auto-fetch, operator name, vehicle/driver master removed from G2 |
| **2.6.0** | 2026-03-30 | Complete BBF → TS rename (40 DocTypes, 50+ fields), dual logo (client+Trustbit), weighbridge hardware integration, project on MR/PO, item remark, define use location on MR/PO/PR/PI |
| **2.5.0** | 2026-03-25 | CC Approval Config, Hold/Resume, CC-aware notifications, monthly budgets, stock availability, approval status in list views |
| **2.4.0** | 2026-03-23 | CEO/MD/Gate/Procurement dashboards + wall displays, PO lifecycle tracker |
| **2.3.0** | 2026-03-22 | Stock OUT dispatch, G1/G2 field split, multi-PO gate entry, vehicle/driver blacklist |
| **2.2.x** | 2026-03-19 | Gate Pass, material inspection, optional weighbridge for Non-RM, admin reception |
| **2.1.x** | 2026-03-17 | Budget management module |
| **2.0.x** | 2026-03-17 | Complete approval redesign + 45 bug fixes |
| **1.4.x** | 2026-03-12 | Item Creator, Bulk Import |
| **1.0** | 2026-03-08 | 9-step gate entry flow |

---

## License

MIT

---

*Developed by [Trustbit Technologies Pvt. Ltd.](https://trustbit.com) for Betul Bio Fuel Pvt. Ltd.*
