# Trustbit Ethanol — BBF Gate Entry System

**Version:** 2.1.2 | **ERPNext:** V15 | **Module:** BBF Gate Entry

Custom ERPNext v15 app for **Betul Bio Fuel Pvt. Ltd.** — an ethanol manufacturing plant. Handles the complete vehicle gate-to-exit lifecycle, multi-level PO/MR approval, budget management, item creation, and quality inspection with a **blind token-based system** designed to prevent manipulation.

---

## Key Features

| # | Feature | Description |
|---|---------|-------------|
| 1 | **9-Step Token Lifecycle** | Blind token system — Token → PO Link → Gross Weight → Quality → Grade → Unload → Tare → GRN → Exit |
| 2 | **Category-Based PO Approval** | Auto-detects Store/Chemical/Grain/Coal from items, routes through configurable step chains |
| 3 | **Cost-Center MR Approval** | Routes Material Requests by Cost Center (Operational → DH+AVP, CAPEX → CEO) |
| 4 | **Budget Management** | Dept Head proposes → CEO approves → auto-creates ERPNext Budget. PO submission blocked if budget exceeded |
| 5 | **Item Creator Wizard** | 5-step wizard for creating items with auto-generated codes + bulk CSV import |
| 6 | **Quality & Deductions** | Grain/Coal quality inspection with GCV, moisture, impurity grading and deduction calculations |
| 7 | **SLA Monitoring** | Token SLA (every 5 min) + Approval SLA (every 30 min) with email alerts and auto-escalation |
| 8 | **Budget Dashboard** | CEO/MD dashboard showing budget vs committed vs actual per Cost Center |

---

## Feature 1: 9-Step Token Lifecycle

Every vehicle gets a **random token** at Gate 1. All downstream departments interact ONLY via token — they never see vehicle, driver, or supplier details.

```
Token Generated → PO Linked → Gross Weighed → Quality Done → Graded → Unloading → Tare Weighed → GRN Created → Exited
```

**Token Format:** `TKN-YYMMDD-XXXX` (random 4-digit suffix, NOT sequential)

### DocTypes

| DocType | Role | Purpose |
|---------|------|---------|
| BBF Token | G1 Security | Central lifecycle tracker, turnaround timestamps |
| BBF Gate Entry | G2 Gate Operator | Links token to Purchase Order (submittable) |
| BBF Weighbridge Log | Weighbridge Operator | Gross/tare weight, net calculation |
| BBF Quality Inspection | Quality Inspector | GCV, moisture, impurities, grading |
| BBF Deduction Sheet | Quality Inspector | Grain/coal deduction calculations |
| BBF Unloading Entry | Stores User | Unloading start/end tracking |

### Key Patterns
- All token status updates via `db_set()` (NOT `save()`) — skips mandatory field validation
- GRN: `Token.create_grn()` creates Purchase Receipt with per-item PO rates
- Turnaround times calculated per stage with `max(0)` to prevent negatives
- Vehicle/Transport masters updated with rolling average turnaround on exit

---

## Feature 2: Category-Based PO Approval (v2.0)

**Pure controller** — no Frappe Workflow. Auto-detects purchase category from PO items' Item Groups.

### How It Works
1. User creates PO → saves → clicks **"Submit for Approval"**
2. System resolves category (Store/Chemical/Grain/Coal) from Item Groups
3. Matches **BBF PO Approval Rule** (category + amount range)
4. PO goes through configurable step chain: Review → Approve → Final Approve
5. On Final Approve → PO submitted (docstatus=1)

### Default Rules (7)

| Category | Amount | Steps |
|----------|--------|-------|
| Store/Chemical | Under ₹1L | CEO (Final Approve) |
| Store/Chemical | ₹1L–₹6L | Purchase Manager (Review) → CEO (Final Approve) |
| Store/Chemical | Above ₹6L | PM (Review) → CEO (Approve) → MD (Final Approve)* |
| Grain | Under ₹6L | Grain PM (Review) → CEO (Final Approve) |
| Grain | Above ₹6L | Grain PM (Review) → CEO (Approve) → MD (Final Approve)* |
| Coal | Under ₹30L | PM (Review) → CEO (Final Approve) |
| Coal | Above ₹30L | PM (Review) → CEO (Approve) → MD (Final Approve)* |

*MD step is manual trigger — CEO clicks "Send to MD"

### Security Features
- **Self-skip:** Submitter's step is automatically skipped
- **Self-approval prevention:** Submitter AND creator cannot approve (checks both `owner` and `bbf_submitted_by`)
- **Multi-step self-skip:** Skips ALL consecutive steps where user holds the role
- **Amount tamper detection:** Server-side check on every approval action
- **Field locking:** PO fields locked during approval AND after rejection/revision
- **Concurrency:** `for_update=True` on all mutations
- **Immutable audit trail:** BBF Approval Log child table (direct insert)
- **Budget check:** Blocks PO if Cost Center budget exceeded

### DocTypes
- **BBF Purchase Category** — maps Item Groups to categories
- **BBF PO Approval Rule** — category + amount → step chain
- **BBF PO Approval Step** — step order, role, action type, manual trigger

---

## Feature 3: Cost-Center MR Approval (v2.0)

Routes Material Requests by Cost Center through configurable approval chains.

| Route | Cost Centers | Steps |
|-------|-------------|-------|
| Operational | 29 operational CCs | Dept Head (Review) → AVP (Final Approve) |
| CAPEX | 5 capital CCs | CEO (Final Approve) |

### DocTypes
- **BBF MR Approval Route** — maps Cost Centers to step chains
- **BBF MR Approval Step** — step order, role, action type

---

## Feature 4: Budget Management (v2.1)

Complete budget proposal, approval, and enforcement system.

### Budget Proposal Workflow
```
Dept Head creates proposal → fills accounts + amounts → "Submit to CEO"
    ↓
CEO reviews → adjusts amounts → "Approve & Activate"
    ↓
ERPNext Budget auto-created (Stop on PO, Warn on MR)
```

### PO Budget Enforcement
- Budget checked at **Submit for Approval** (not at final approve)
- Shows live **budget indicator** on PO form (green/yellow/red utilization bar)
- If budget exceeded → **blocks** with detailed breakdown
- CEO can **override** with mandatory reason (logged in audit trail)
- Committed amount uses **unbilled PO portion** to prevent double-counting

### Budget Dashboard & Control Matrix
- **BBF Budget Dashboard** — script report with bar charts, summary cards, color-coded status
- **Budget Control Matrix** — visual page at `/app/budget-control-matrix` with utilization bars

### DocTypes
- **BBF Budget Proposal** — DH proposes → CEO approves
- **BBF Budget Proposal Item** — account, proposed/approved amounts
- **BBF Budget Override Log** — CEO override audit trail on PO

### BBF Settings (Budget)
- Enable Budget Check on PO (master switch)
- Default Monthly Distribution template

---

## Feature 5: Item Creator & Bulk Import (v1.4)

### Item Creator Wizard (`/app/item-creator`)
5-step wizard: Company & Category → Item Details → Variant Config → Stock & Valuation → Review & Create

**Item Code Format:** `CompanyCode-CategoryCode-Serial[-VariantCode]`
Example: `BBF-RM-001-BRK` (BBF, Raw Material, serial 001, Broken variant)

### Bulk Item Import (`/app/item-bulk-import`)
- CSV template download → drag-drop upload → validation preview → batch creation
- Max 500 rows, 5MB, processed in batches of 10
- Rollback per row on error, clickable links to created items

### DocTypes
- **BBF Item Creator** — item code generation + ERPNext Item creation
- **BBF Item Code Settings** — separator, serial digits, counters
- **BBF Variant** — custom variant codes (separate from Brand)

---

## Feature 6: Quality & Deduction System

### BBF Quality Inspection
- Grain: GCV, moisture, impurities
- Coal: GCV variance, moisture excess
- Auto-detect item category from Item Group

### BBF Deduction Sheet
- **Grain:** Unloading (per KG), Dhalta (gm/qtl), Impurity (%), Brokerage (per MT)
- **Coal:** GCV Shortfall (proportional), Moisture Excess (% of invoice value)

---

## Feature 7: SLA Monitoring

| Schedule | Checker | Action |
|----------|---------|--------|
| Every 5 min | Token SLA | Email alert if token stuck at any stage |
| Every 30 min | Approval SLA | Email alert if PO stuck at any approval step |

- Deduplication via timestamp fields (won't re-alert within SLA period)
- Optional auto-escalation to next approval level

---

## Reports (3)

| Report | Type | Purpose |
|--------|------|---------|
| BBF Vehicle Turnaround Report | Script Report | Historical turnaround with date/status filters |
| BBF Live Vehicle Tracker | Script Report | Real-time active tokens with color alerts |
| BBF Budget Dashboard | Script Report | Budget vs Committed vs Actual per Cost Center |

---

## Custom Pages (3)

| Page | URL | Purpose |
|------|-----|---------|
| Item Creator | `/app/item-creator` | 5-step wizard for item creation |
| Item Bulk Import | `/app/item-bulk-import` | CSV upload for batch item creation |
| Budget Control Matrix | `/app/budget-control-matrix` | Visual budget utilization matrix |

---

## DocTypes (27)

### Gate Entry (10)
BBF Token, BBF Gate Entry, BBF Gate Entry Item, BBF Weighbridge Log, BBF Quality Inspection, BBF Deduction Sheet, BBF Unloading Entry, BBF Transport Master, BBF Transporter Vehicle, BBF Vehicle Master

### Approval System (9)
BBF Purchase Category, BBF Purchase Category Item, BBF PO Approval Rule, BBF PO Approval Step, BBF MR Approval Route, BBF MR Approval Step, BBF MR Route Cost Center, BBF Notification Recipient, BBF Approval Log

### Budget System (3)
BBF Budget Proposal, BBF Budget Proposal Item, BBF Budget Override Log

### Item Management (4)
BBF Item Creator, BBF Item Creator Variant, BBF Item Code Settings, BBF Code Counter, BBF Variant

### Configuration (1)
BBF Settings (singleton)

---

## Custom Roles (13)

| Role | Area |
|------|------|
| G1 Security | Token generation, vehicle exit |
| G2 Gate Operator | Gate entry, PO linking |
| Weighbridge Operator | Gross/tare weight |
| Stores User | Unloading |
| Quality Inspector | QI + deductions |
| Purchase Manager | Review Store/Chemical/Coal POs |
| Grain Purchase Manager | Review Grain POs |
| Department Head | Review Operational MRs |
| AVP | Final approve Operational MRs |
| CEO | Approve all POs, CAPEX MRs |
| MD | Final approve high-value POs |
| IT Head | System config, reports, full access |

---

## Custom Fields on Standard DocTypes

### Purchase Order (29 fields)
Approval: status, category, rule, steps, self_skip_impossible, can_send_to_md
Info: approved_by, approved_date, submitted_by, last_action, revision info
Budget: budget_overridden, budget_override_log
Audit: approval_log (child table), amount_at_submission, last_sla_alert

### Material Request (15 fields)
Status, route, steps, self_skip_impossible, submitted_by, approved_by/date, revision, mr_log

### Purchase Receipt (2 fields)
bbf_token, bbf_gate_entry

### Company, Item Group, Brand
Custom code fields for item code generation

---

## API Controllers (4)

| File | Lines | Purpose |
|------|-------|---------|
| `api.py` | ~100 | PO search, token SLA checker |
| `api_bulk_import.py` | ~320 | CSV template, parse, validate, bulk create |
| `bbf_po_approval.py` | ~1,600 | PO/MR approval controller (pure, no Frappe Workflow) |
| `bbf_budget.py` | ~750 | Budget proposal, PO budget check, CEO override, dashboard |

---

## Hooks

```python
# JS injection into standard DocTypes
doctype_js = {
    "Purchase Order": "public/js/po_approval.js",
    "Material Request": "public/js/mr_approval.js"
}

# Doc events
doc_events = {
    "Purchase Order": {
        "on_cancel": "bbf_po_approval.po_on_cancel",
        "before_insert": "bbf_po_approval.po_on_amend",
        "before_save": "bbf_po_approval.po_before_save",
    },
    "Material Request": {
        "before_save": "bbf_po_approval.mr_before_save",
    }
}

# Scheduler
scheduler_events = {
    "cron": {
        "*/5 * * * *": ["api.check_sla_breaches"],
        "*/30 * * * *": ["bbf_po_approval.check_approval_sla"]
    }
}

# Custom fields created via after_migrate hook
after_migrate = ["setup.create_custom_fields"]
```

---

## Security Measures

- **Doctype validation** on all whitelisted APIs
- **for_update=True** on all mutation methods (concurrency safety)
- **Self-approval prevention** — checks both `owner` and `submitted_by`
- **Amount tamper detection** — server-side check on every approval action
- **Field locking** — client-side (JS) AND server-side (`before_save`)
- **XSS prevention** — `escape_html()` on all user data in HTML/emails
- **SQL injection** — parameterized queries only
- **Budget override audit** — immutable log with mandatory reason
- **SLA deduplication** — prevents duplicate alerts via timestamp fields
- **Comment length limits** — 2000 char max on all text inputs
- **Role-based access** — financial APIs check user roles

---

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/zxrrcpandey/betul_biofuel.git --branch develop
bench --site your-site.localhost install-app trustbit_ethanol
bench --site your-site.localhost migrate
```

## File Structure

```
trustbit_ethanol/
├── hooks.py
├── bbf_gate_entry/
│   ├── api.py                          # PO search, token SLA
│   ├── api_bulk_import.py              # CSV bulk item import
│   ├── bbf_po_approval.py             # PO/MR approval controller
│   ├── bbf_budget.py                  # Budget management controller
│   ├── setup.py                       # Custom fields, roles, permissions
│   ├── doctype/
│   │   ├── bbf_token/                 # Core token DocType
│   │   ├── bbf_gate_entry/            # G2 gate entry
│   │   ├── bbf_weighbridge_log/       # Weight recording
│   │   ├── bbf_quality_inspection/    # Quality checks
│   │   ├── bbf_deduction_sheet/       # Deduction calculations
│   │   ├── bbf_unloading_entry/       # Unloading tracking
│   │   ├── bbf_settings/              # Global config
│   │   ├── bbf_purchase_category/     # Item Group → category mapping
│   │   ├── bbf_po_approval_rule/      # Category + amount → steps
│   │   ├── bbf_mr_approval_route/     # CC → approval chain
│   │   ├── bbf_budget_proposal/       # DH→CEO budget workflow
│   │   ├── bbf_item_creator/          # Item code generator
│   │   ├── bbf_item_code_settings/    # Code format config
│   │   ├── bbf_variant/               # Custom variant codes
│   │   └── ... (27 DocTypes total)
│   ├── report/
│   │   ├── bbf_vehicle_turnaround_report/
│   │   ├── bbf_live_vehicle_tracker/
│   │   └── bbf_budget_dashboard/
│   ├── page/
│   │   ├── item_creator/              # 5-step wizard
│   │   ├── item_bulk_import/          # CSV upload
│   │   └── budget_control_matrix/     # Budget matrix
│   └── print_format/
│       └── bbf_token_print/
├── public/js/
│   ├── po_approval.js                 # PO form: stepper, buttons, budget indicator
│   └── mr_approval.js                 # MR form: stepper, buttons, field locking
└── fixtures/                          # 13 custom roles
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 2.1.2 | 2026-03-17 | 30-parameter audit: 20 bug fixes (double-counting, multi-step skip, XSS, tamper) |
| 2.1.1 | 2026-03-17 | Budget module audit: 11 bug fixes (crash, FY dates, authorization, race conditions) |
| 2.1.0 | 2026-03-17 | Budget Management Module: proposal workflow, PO budget check, CEO override, dashboard |
| 2.0.3 | 2026-03-17 | Third audit: 6 fixes (MR self-skip, SLA XSS, IT Head settings, form layout) |
| 2.0.2 | 2026-03-17 | Second audit: 6 fixes (single-step stuck state, MR docstatus, resubmit permissions) |
| 2.0.1 | 2026-03-17 | First audit: 12 fixes (reject crash, role validation, field locking, doctype injection) |
| 2.0.0 | 2026-03-17 | Complete approval redesign: category PO routing, cost-center MR routing, CTO→IT Head |
| 1.4.x | 2026-03-12 | Item Creator, Bulk Import, approval security fixes |
| 1.3.0 | 2026-03-11 | PO/MR Multi-Level Approval System |
| 1.1.0 | 2026-03-08 | Complete 9-step gate entry flow |

**Total bugs found and fixed: 100+** across 9 audit rounds.

---

## License

MIT

---

*Developed by [Trustbit Technologies](https://trustbit.com) for Betul Bio Fuel Pvt. Ltd.*
