# Trustbit Ethanol — TS Gate Entry System

**Version:** 2.9.14.3 | **ERPNext:** V15 | **Module:** TS Gate Entry

Custom ERPNext v15 app for **Betul Bio Fuel Pvt. Ltd.** — an ethanol manufacturing plant. Handles the complete vehicle gate-to-exit lifecycle, multi-level PO/MR approval with CC-based routing, budget management, item creation, quality inspection, and interactive dashboards with a **blind token-based system** designed to prevent manipulation.

## Recent ships (May 2026)

| Version | Date | Change |
|---|---|---|
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
