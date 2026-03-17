# Trustbit Ethanol Custom App

Custom ERPNext v15 app for **Betul Bio Fuel Pvt. Ltd.** — an ethanol manufacturing plant. This app handles the complete vehicle gate entry to exit flow with a **blind token-based system** designed to prevent manipulation and favoritism.

---

## Core Design Principle: Anonymity by Token

Every vehicle gets a **Token** at Gate 1. All downstream departments (weighbridge, stores, quality, GRN) interact ONLY via Token Number — they NEVER see vehicle number, transporter, supplier, or driver details. This prevents favoritism and manipulation.

**Token Number Format:** `TKN-YYMMDD-XXXX` where XXXX is a random 4-digit number (NOT sequential).
Example: `TKN-260307-4829`

---

## Features

### 1. BBF Token (G-1 Security Gate)
- Security guard generates a token for every vehicle entering premises
- Random token number generation with collision check
- Auto-captures entry date, time, vehicle number, vehicle type
- Vehicle number and driver details are hidden from all downstream roles (field-level `permlevel`)
- Print format shows ONLY: Token Number (large barcode), Date, Time — nothing else
- Status auto-updates as downstream DocTypes are processed
- **Mark Exit** button to record vehicle exit with one click
- Auto-creates Vehicle Master record on first entry

### 2. BBF Gate Entry (G-2 Gate Operator)
- Gate operator scans/enters token number
- Auto-fetches entry date, time, vehicle type from token
- **PO Search** with 3 flexible parameters:
  - Search by PO ID
  - Search by PO Date
  - Search by Tentative Qty (with 80%-120% tolerance)
- PO items auto-populated into child table on selection
- Supplier name hidden from Gate Operator (visible only to Accounts/IT Head)
- Material flow selection: Raw Material → Weighbridge, Non-Raw Material → Stores
- Transport details (transporter, LR number) hidden from floor staff
- On submit: Updates token status to "PO Linked" with timestamp

### 3. BBF Weighbridge Log
- Weighbridge operator enters ONLY token number
- Auto-fetches gate entry, purchase order, material flow
- **Gross Weight** recording with auto-timestamp and operator tracking
- **Tare Weight** locked until unloading is confirmed complete (controlled by `unloading_complete` flag)
- **Net Weight** = Gross - Tare (auto-calculated)
- Weight difference % from PO quantity (auto-calculated)
- CCTV snapshot attachment support for both gross and tare weighment
- Real-time notification to Stores team when gross weight is recorded

### 4. BBF Unloading Entry
- Stores team records unloading — sees only token number, item, and quantity
- **Start Unloading** button — auto-records start timestamp
- **End Unloading** button — auto-records end timestamp, calculates duration
- On completion: Enables tare weight field on Weighbridge Log
- Real-time notification to Weighbridge Operator when unloading is complete

### 5. BBF Transport Master
- Master data for transporters with full details (name, contact, GSTIN, PAN, bank details)
- Links to ERPNext Supplier for payment processing
- **Performance tracking** (auto-updated): Total trips, average turnaround, last trip date
- Visible only to Accounts Manager and IT Head roles

### 6. BBF Vehicle Master
- Tracks all vehicles that enter the plant
- Auto-created when a vehicle's first token is generated
- Links to transporter
- **Trip log** (auto-updated): Total trips, average turnaround, last visit date
- Visible only to Accounts Manager and IT Head roles

### 7. BBF Settings (Singleton)
- **SLA Threshold** — configurable minutes before escalation alert (default: 30 min)
- **Escalation Email** — IT Head/Plant Head email for SLA breach notifications
- **Token Suffix Digits** — configurable random digit count (4 or 5)
- **Shift Configuration** — default shift start/end times

---

## Complete Vehicle Flow

```
G1 Security          G2 Gate Operator       Weighbridge          Stores              Weighbridge
    |                      |                    |                   |                    |
    |-- Token Generated -->|                    |                   |                    |
    |                      |-- PO Linked ------>|                   |                    |
    |                      |                    |-- Gross Weighed ->|                    |
    |                      |                    |                   |-- Unloading ------>|
    |                      |                    |                   |-- Unload Done ---->|
    |                      |                    |                   |                    |-- Tare Weighed
    |                      |                    |                   |                    |
    |<------ Exit --------------------------------------------------------------------|
```

---

## Turnaround Tracking

Every token automatically tracks timestamps and calculates duration at each stage:

| Stage | Timestamp | Duration Calculated |
|---|---|---|
| G1 Entry | `g1_entry_time` | — |
| G2 PO Link | `g2_link_time` | G1 → G2 (minutes) |
| Gross Weight | `wb_gross_time` | G2 → Weighbridge |
| Unloading Start | `unload_start_time` | Weighbridge → Unloading |
| Unloading End | `unload_end_time` | Unloading Duration |
| Tare Weight | `wb_tare_time` | Unloading → Tare |
| Quality | `quality_time` | Tare → Quality |
| GRN | `grn_time` | Quality → GRN |
| Exit | `g1_exit_time` | **Total Turnaround** |

---

## Reports

### BBF Vehicle Turnaround Report
- Filter by date range and status
- Shows: Token Number, Entry Time, Exit Time, Total Turnaround, Stage-wise breakdown
- Highlights tokens exceeding SLA threshold in red

### BBF Live Vehicle Tracker
- Shows all active tokens (status != "Exited") in real-time
- Current stage, time at current stage
- Color-coded indicators:
  - Green: < 15 min at stage
  - Yellow: 15–30 min at stage
  - Red: > 30 min at stage (SLA breach)

---

## Role Permissions

| DocType | G1 Security | G2 Gate Operator | Weighbridge Operator | Stores User | Accounts Manager | IT Head |
|---|---|---|---|---|---|---|
| BBF Token | Create, Read | Read | Read | Read | Full | Full |
| BBF Gate Entry | — | Create, Read, Write | Read | Read | Full | Full |
| BBF Weighbridge Log | — | Read | Create, Read, Write | Read | Full | Full |
| BBF Transport Master | — | — | — | — | Full | Full |
| BBF Vehicle Master | — | — | — | — | Full | Full |
| BBF Unloading Entry | — | — | — | Create, Read, Write | Full | Full |

**Field-level restrictions (permlevel 1 — only Accounts/IT Head can see):**
- `vehicle_number` on BBF Token
- `driver_name`, `driver_mobile` on BBF Token
- `supplier_name` on BBF Gate Entry
- `transporter` on BBF Gate Entry

---

## SLA Monitoring

- Scheduler runs every **5 minutes** checking for tokens stuck at any stage
- If a token exceeds the configured SLA threshold (default 30 min), an email alert is sent to the escalation email
- Alert includes: Token Number, Current Stage, Time at Stage

---

## Print Format

**BBF Token Print** — Minimalist print format showing only:
- Token Number (large, bold, centered)
- Barcode (scannable)
- Date and Time
- Company name footer

No vehicle number, driver, or purpose is shown — ensuring anonymity.

---

## Tech Stack

- **Framework:** Frappe v15
- **ERP:** ERPNext v15
- **Database:** MariaDB
- **App Name:** `trustbit_ethanol`
- **Module:** BBF Gate Entry

---

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/zxrrcpandey/betul_biofuel.git --branch develop
bench --site your-site.localhost install-app trustbit_ethanol
bench --site your-site.localhost migrate
```

## Development Setup

```bash
# Clone and install
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/zxrrcpandey/betul_biofuel.git --branch develop
bench install-app trustbit_ethanol

# Start development server
bench start
```

### Pre-commit

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/trustbit_ethanol
pre-commit install
```

Tools used: ruff, eslint, prettier, pyupgrade

---

## File Structure

```
trustbit_ethanol/
├── hooks.py
├── modules.txt
├── bbf_gate_entry/
│   ├── __init__.py
│   ├── api.py                          # Whitelisted APIs (PO search, SLA check)
│   ├── doctype/
│   │   ├── bbf_settings/               # Singleton config
│   │   ├── bbf_token/                  # Core token DocType
│   │   ├── bbf_gate_entry/             # G2 gate entry with PO linking
│   │   ├── bbf_gate_entry_item/        # Child table for PO items
│   │   ├── bbf_weighbridge_log/        # Gross/tare weight recording
│   │   ├── bbf_unloading_entry/        # Unloading start/end tracking
│   │   ├── bbf_transport_master/       # Transporter master data
│   │   └── bbf_vehicle_master/         # Vehicle registry
│   ├── report/
│   │   ├── bbf_vehicle_turnaround_report/
│   │   └── bbf_live_vehicle_tracker/
│   └── print_format/
│       └── bbf_token_print/
├── public/
├── templates/
└── patches/
```

---

## License

MIT
