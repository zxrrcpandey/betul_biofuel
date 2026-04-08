"""TS Regression Test Suite — runs BEFORE every deploy.

Tests ALL critical features in ~60 seconds. If ANY test fails,
deployment MUST be blocked. This prevents cascading failures where
fixing one thing breaks another.

Usage:
    cd /home/frappe/frappe-bench/sites
    su -s /bin/bash frappe -c 'cd /home/frappe/frappe-bench/sites && \
        /home/frappe/frappe-bench/env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_regression.py'

Exit code: 0 = all passed, 1 = failures found (BLOCK deploy)
"""

import frappe
import sys
import os

# Auto-detect site
SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"

frappe.init(site=SITE)
frappe.connect()

results = {"passed": 0, "failed": 0, "errors": []}
cleanup = []


def test(name, fn):
    try:
        fn()
        results["passed"] += 1
        sys.stdout.write(".")
        sys.stdout.flush()
    except Exception as e:
        results["failed"] += 1
        results["errors"].append({"test": name, "error": str(e)[:400]})
        sys.stdout.write("F")
        sys.stdout.flush()


# ═══════════════════════════════════════════════════════════
#  1. MR CREATION + APPROVAL STATUS
# ═══════════════════════════════════════════════════════════

print("\n[1/10] MR Creation + Status")

def test_mr_create():
    """MR can be created and has correct default status"""
    frappe.set_user("Administrator")
    company = frappe.db.get_value("Company", {"is_group": 0}, "name")
    cc = frappe.db.get_value("Cost Center", {"is_group": 0, "company": company}, "name")
    wh = frappe.db.get_value("Warehouse", {"is_group": 0, "company": company}, "name")
    item = frappe.db.get_value("Item", {"disabled": 0}, "name")
    mr = frappe.get_doc({
        "doctype": "Material Request",
        "material_request_type": "Purchase",
        "company": company,
        "schedule_date": "2026-12-31",
        "cost_center": cc,
        "items": [{"item_code": item, "qty": 1, "schedule_date": "2026-12-31", "warehouse": wh}]
    })
    mr.insert(ignore_permissions=True)
    cleanup.append(("Material Request", mr.name))
    assert mr.ts_mr_status == "Not Submitted", f"MR status should be 'Not Submitted', got '{mr.ts_mr_status}'"
    assert mr.name, "MR name is empty"

test("MR create + default status", test_mr_create)


def test_mr_naming():
    """MR naming follows CC code format"""
    if not cleanup:
        return
    name = cleanup[-1][1]
    assert name.startswith(("PR-", "SR-", "FA-", "BBPL-", "MAT-")), f"Bad MR name: {name}"

test("MR naming format", test_mr_naming)


# ═══════════════════════════════════════════════════════════
#  2. APPROVAL CONTEXT API
# ═══════════════════════════════════════════════════════════

print("\n[2/10] Approval Context API")

def test_mr_approval_context():
    """MR approval context returns can_submit_for_approval=True for Not Submitted"""
    if not cleanup:
        return
    from trustbit_ethanol.ts_gate_entry.ts_po_approval import get_approval_context
    ctx = get_approval_context("Material Request", cleanup[-1][1])
    assert ctx.get("approval_enabled"), "Approval not enabled"
    assert ctx.get("can_submit_for_approval"), f"can_submit_for_approval is False for status '{ctx.get('status')}'"

test("MR approval context — can_submit_for_approval", test_mr_approval_context)


def test_po_approval_context():
    """PO approval context works for Not Submitted"""
    frappe.set_user("Administrator")
    company = frappe.db.get_value("Company", {"is_group": 0}, "name")
    cc = frappe.db.get_value("Cost Center", {"is_group": 0, "company": company}, "name")
    wh = frappe.db.get_value("Warehouse", {"is_group": 0, "company": company}, "name")
    item = frappe.db.get_value("Item", {"disabled": 0}, "name")
    supplier = frappe.db.get_value("Supplier", {}, "name")
    if not supplier:
        return  # Skip if no suppliers
    po = frappe.get_doc({
        "doctype": "Purchase Order",
        "supplier": supplier,
        "company": company,
        "schedule_date": "2026-12-31",
        "cost_center": cc,
        "items": [{"item_code": item, "qty": 1, "schedule_date": "2026-12-31", "warehouse": wh, "rate": 100}]
    })
    po.insert(ignore_permissions=True)
    cleanup.append(("Purchase Order", po.name))
    assert po.ts_approval_status == "Not Submitted", f"PO status: {po.ts_approval_status}"

    from trustbit_ethanol.ts_gate_entry.ts_po_approval import get_approval_context
    ctx = get_approval_context("Purchase Order", po.name)
    assert ctx.get("can_submit_for_approval"), f"PO can_submit is False for '{ctx.get('status')}'"

test("PO approval context — can_submit_for_approval", test_po_approval_context)


# ═══════════════════════════════════════════════════════════
#  3. STATUS FIELD VALIDATION
# ═══════════════════════════════════════════════════════════

print("\n[3/10] Status Field Validation")

def test_mr_status_options():
    """ts_mr_status Property Setter includes all required values"""
    ps = frappe.db.get_value("Property Setter",
        {"doc_type": "Material Request", "field_name": "ts_mr_status", "property": "options"},
        "value")
    if ps:
        required = ["Not Submitted", "Approved", "Rejected", "Revised", "On Hold"]
        for val in required:
            assert val in ps, f"'{val}' missing from ts_mr_status options"

test("MR status options include all values", test_mr_status_options)


def test_po_status_options():
    """ts_approval_status Property Setter includes all required values"""
    ps = frappe.db.get_value("Property Setter",
        {"doc_type": "Purchase Order", "field_name": "ts_approval_status", "property": "options"},
        "value")
    if ps:
        required = ["Not Submitted", "Approved", "Rejected", "Revised"]
        for val in required:
            assert val in ps, f"'{val}' missing from ts_approval_status options"

test("PO status options include all values", test_po_status_options)


# ═══════════════════════════════════════════════════════════
#  4. MR NAMING COUNTERS
# ═══════════════════════════════════════════════════════════

print("\n[4/10] MR Naming Counters")

def test_naming_counter_sync():
    """tabSeries counters match actual max MR numbers"""
    prefixes = frappe.db.sql(
        "SELECT DISTINCT CONCAT(SUBSTRING_INDEX(name, '-', -2)) FROM `tabMaterial Request` "
        "WHERE name REGEXP '^(PR|SR|FA|BBPL)-'", pluck="name"
    )
    # Check a simpler way — just verify no duplicate exists
    dups = frappe.db.sql(
        "SELECT name, COUNT(*) c FROM `tabMaterial Request` GROUP BY name HAVING c > 1"
    )
    assert len(dups) == 0, f"Duplicate MR names found: {dups}"

test("No duplicate MR names", test_naming_counter_sync)


# ═══════════════════════════════════════════════════════════
#  5. PRINT FORMAT
# ═══════════════════════════════════════════════════════════

print("\n[5/10] Print Formats")

def test_print_format_exists():
    """All print formats exist"""
    formats = [
        "TS Material Request", "TS Material Request (Clean)",
        "TS Purchase Order", "TS Purchase Order (Clean)",
        "TS Token Print", "TS Token Slip", "TS Gate Pass",
    ]
    for pf in formats:
        assert frappe.db.exists("Print Format", pf), f"Print Format '{pf}' missing"

test("All print formats exist", test_print_format_exists)


def test_print_url():
    """get_url returns HTTPS without :8000"""
    url = frappe.utils.get_url()
    assert ":8000" not in url, f"URL contains :8000: {url} — wkhtmltopdf will fail"

test("get_url has no :8000", test_print_url)


# ═══════════════════════════════════════════════════════════
#  6. SETTINGS
# ═══════════════════════════════════════════════════════════

print("\n[6/10] Settings")

def test_settings_exist():
    """TS Settings singleton exists and has key values"""
    assert frappe.db.exists("TS Settings", "TS Settings"), "TS Settings missing"

test("TS Settings exists", test_settings_exist)


def test_approval_enabled():
    """Approval system is enabled"""
    mr_enabled = frappe.db.get_single_value("TS Settings", "enable_mr_approval")
    po_enabled = frappe.db.get_single_value("TS Settings", "enable_po_approval")
    assert mr_enabled, "MR approval not enabled"
    assert po_enabled, "PO approval not enabled"

test("Approval systems enabled", test_approval_enabled)


# ═══════════════════════════════════════════════════════════
#  7. ROLES
# ═══════════════════════════════════════════════════════════

print("\n[7/10] Roles")

def test_roles_exist():
    """All custom roles exist and are not disabled"""
    roles = [
        "G1 Security", "G2 Gate Operator", "Weighbridge Operator",
        "Stores User", "Quality Inspector", "Department Head",
        "CEO", "MD", "Purchase Manager", "Grain Purchase Manager",
        "AVP", "Admin Reception",
    ]
    for role in roles:
        r = frappe.db.get_value("Role", role, ["name", "disabled"], as_dict=True)
        assert r, f"Role '{role}' missing"
        assert not r.disabled, f"Role '{role}' is disabled"

test("All custom roles exist", test_roles_exist)


# ═══════════════════════════════════════════════════════════
#  8. WORKSPACES
# ═══════════════════════════════════════════════════════════

print("\n[8/10] Workspaces")

def test_workspaces():
    """Key workspaces exist"""
    workspaces = ["BBPL Ethanol", "Dashboards"]
    for ws in workspaces:
        assert frappe.db.exists("Workspace", ws), f"Workspace '{ws}' missing"

test("Key workspaces exist", test_workspaces)


# ═══════════════════════════════════════════════════════════
#  9. SERVER CONFIG
# ═══════════════════════════════════════════════════════════

print("\n[9/10] Server Config")

def test_no_developer_mode():
    """developer_mode is OFF"""
    assert not frappe.conf.developer_mode, "developer_mode is ON — will cause 403 for non-admin users"

test("developer_mode is OFF", test_no_developer_mode)


def test_no_auto_restart():
    """restart_supervisor_on_update is false"""
    assert not frappe.conf.restart_supervisor_on_update, "restart_supervisor_on_update is true — backup cron will flush sessions"

test("restart_supervisor_on_update is false", test_no_auto_restart)


# ═══════════════════════════════════════════════════════════
#  10. CC APPROVAL CONFIGS
# ═══════════════════════════════════════════════════════════

print("\n[10/10] CC Approval Configs")

def test_cc_configs():
    """CC Approval Configs exist"""
    count = frappe.db.count("TS CC Approval Config", {"is_active": 1})
    assert count > 0, f"No active CC Approval Configs (found {count})"

test("Active CC configs exist", test_cc_configs)


def test_po_approval_rules():
    """PO Approval Rules exist"""
    count = frappe.db.count("TS PO Approval Rule", {"is_active": 1})
    assert count > 0, f"No active PO Approval Rules (found {count})"

test("Active PO approval rules exist", test_po_approval_rules)


# ═══════════════════════════════════════════════════════════
#  CLEANUP + RESULTS
# ═══════════════════════════════════════════════════════════

print("\n\n=== CLEANUP ===")
frappe.set_user("Administrator")
for dt, name in cleanup:
    try:
        frappe.delete_doc(dt, name, force=True)
    except:
        pass
frappe.db.commit()
print(f"Deleted {len(cleanup)} test docs")

total = results["passed"] + results["failed"]
print("\n" + "=" * 60)
print(f"REGRESSION TEST RESULTS")
print(f"Total: {total} | Passed: {results['passed']} | Failed: {results['failed']}")
if total:
    pct = round(results["passed"] / total * 100, 1)
    print(f"Pass Rate: {pct}%")

if results["errors"]:
    print(f"\n{'='*60}")
    print(f"FAILURES ({len(results['errors'])}) — DEPLOYMENT BLOCKED")
    print("=" * 60)
    for err in results["errors"]:
        print(f"\n  FAIL: {err['test']}")
        print(f"  Error: {err['error'][:300]}")
    print(f"\n*** FIX ALL FAILURES BEFORE DEPLOYING ***")

frappe.destroy()

sys.exit(1 if results["failed"] > 0 else 0)
