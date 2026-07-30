"""Notification Read Accountability Trail (v2.29.0) — scenario suite.

Automated portion of the plan-v2 612-scenario matrix. Categories that need a
live browser (native-bell DOM, real cross-origin CSRF) are covered at the
decorator-registry / function level here and by the manual walkthrough.

Usage (demo ONLY):
    cd /home/frappe/frappe-bench/sites
    env FRAPPE_SITE=betulbiofuel.mkasystem.com \
        /home/frappe/frappe-bench/env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_notification_trail.py

Exit code: 0 = all passed, 1 = failures (BLOCK).
Creates its own throwaway users + Notification Log rows; cleans up after.
"""

import os
import sys
import time
import uuid

import frappe

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
frappe.init(site=SITE)
frappe.connect()
frappe.set_user("Administrator")

from frappe.utils import add_days, cint, now_datetime  # noqa: E402

from trustbit_ethanol.ts_gate_entry import notification_center_api as nc  # noqa: E402
from trustbit_ethanol.ts_gate_entry import ts_notification_trail as trail  # noqa: E402
from trustbit_ethanol.ts_gate_entry.report.ts_notification_read_audit import (  # noqa: E402
    ts_notification_read_audit as report)

results = {"passed": 0, "failed": 0, "errors": []}
_category_counts = {}
_current_category = ["?"]

TAG = "TRAILTEST-" + uuid.uuid4().hex[:6]
RUN_START = now_datetime()  # scope Error-Log assertions to THIS run only
U1 = "trail_user_a@bbftest.local"
U2 = "trail_user_b@bbftest.local"
_cleanup_rows = []


def category(label):
    _current_category[0] = label
    print("\n[{0}]".format(label))


def check(name, cond, detail=""):
    _category_counts[_current_category[0]] = _category_counts.get(_current_category[0], 0) + 1
    if cond:
        results["passed"] += 1
        sys.stdout.write(".")
    else:
        results["failed"] += 1
        results["errors"].append({"cat": _current_category[0], "test": name,
                                  "detail": str(detail)[:300]})
        sys.stdout.write("F")
    sys.stdout.flush()


def expect_throw(name, fn, exc=Exception):
    try:
        fn()
        check(name, False, "no exception raised")
    except exc:
        check(name, True)
    except Exception as e:
        check(name, False, "wrong exception: {0!r}".format(e))
    finally:
        frappe.clear_messages()


def make_user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email,
                        "first_name": email.split("@")[0],
                        "send_welcome_email": 0}).insert(ignore_permissions=True)


def make_row(for_user, doctype="Purchase Order", docname=None, read=0, subject=None):
    doc = frappe.get_doc({
        "doctype": "Notification Log",
        "for_user": for_user,
        "subject": subject or "{0} test row".format(TAG),
        "type": "Alert",
        "document_type": doctype,
        "document_name": docname or "{0}-{1}".format(TAG, uuid.uuid4().hex[:5]),
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    if read:
        frappe.db.set_value("Notification Log", doc.name, "read", 1, update_modified=False)
    _cleanup_rows.append(doc.name)
    return doc.name


def row(name):
    return frappe.db.get_value(
        "Notification Log", name,
        ["read", "modified", "ts_first_shown_at", "ts_first_shown_via",
         "ts_read_at", "ts_read_via", "creation", "for_user"], as_dict=True)


def set_switch(value):
    frappe.db.set_value("TS Settings", "TS Settings", trail.KILL_SWITCH_FIELD,
                        value, update_modified=False)
    frappe.db.commit()


make_user(U1)
make_user(U2)

# ═══════════════════════════════════════════════════════════════════════
category("0. Seeded schema + hooks wiring")
# ═══════════════════════════════════════════════════════════════════════
meta = frappe.get_meta("Notification Log")
for f in ("ts_first_shown_at", "ts_first_shown_via", "ts_read_at", "ts_read_via"):
    check("field exists: " + f, meta.has_field(f))
    fld = meta.get_field(f)
    check("field read_only: " + f, fld and cint(fld.read_only) == 1)
    check("field no_copy: " + f, fld and cint(fld.no_copy) == 1)
check("settings switch field", frappe.get_meta("TS Settings").has_field(trail.KILL_SWITCH_FIELD))
ovr = frappe.get_hooks("override_whitelisted_methods") or {}
core_read = "frappe.desk.doctype.notification_log.notification_log.mark_as_read"
core_all = "frappe.desk.doctype.notification_log.notification_log.mark_all_as_read"
check("override mark_as_read wired",
      (ovr.get(core_read) or [""])[-1] == "trustbit_ethanol.ts_gate_entry.ts_notification_trail.mark_as_read", ovr.get(core_read))
check("override mark_all_as_read wired",
      (ovr.get(core_all) or [""])[-1] == "trustbit_ethanol.ts_gate_entry.ts_notification_trail.mark_all_as_read", ovr.get(core_all))
sched = frappe.get_hooks("scheduler_events") or {}
cron = sched.get("cron") or {}
check("monthly snapshot scheduled",
      any("monthly_audit_snapshot" in str(v) for v in cron.values()))
am = frappe.get_hooks("after_migrate") or []
check("after_migrate seeder wired",
      any("after_migrate_notification_trail" in x for x in am))
check("report row synced", frappe.db.exists("Report", report.__name__.split(".")[-1].replace("ts_notification_read_audit", "") or "TS Notification Read Audit") or frappe.db.exists("Report", "TS Notification Read Audit"))
roles_on_report = {r.role for r in frappe.get_doc("Report", "TS Notification Read Audit").roles}
for r in ("CEO", "MD", "System Manager", "IT Head"):
    check("report role seeded: " + r, r in roles_on_report or not frappe.db.exists("Role", r))

# ═══════════════════════════════════════════════════════════════════════
category("1. Read-path stamping (4 paths x states)")
# ═══════════════════════════════════════════════════════════════════════
set_switch(1)

# R1 Center per-row click
frappe.set_user(U1)
n = make_row(U1)
nc.mark_read(n)
r = row(n)
check("R1 flips read", cint(r.read) == 1)
check("R1 stamps ts_read_at", bool(r.ts_read_at))
check("R1 via Center Click", r.ts_read_via == "Center Click")
first_at = r.ts_read_at
time.sleep(1.1)
nc.mark_read(n)  # repeat — first-write-wins
r = row(n)
check("R1 repeat does not move stamp", r.ts_read_at == first_at, (first_at, r.ts_read_at))

# R1 foreign row
frappe.set_user(U2)
n_foreign = make_row(U1)
expect_throw("R1 foreign row rejected", lambda: nc.mark_read(n_foreign), frappe.PermissionError)
frappe.set_user("Administrator")
check("R1 foreign row unstamped", not row(n_foreign).ts_read_at)

# R1 non-existent
frappe.set_user(U1)
expect_throw("R1 missing row throws", lambda: nc.mark_read("NON-EXISTENT-" + TAG))

# R2 Center Mark All (filter-scoped)
frappe.set_user(U1)
batch = [make_row(U1) for _ in range(5)]
already_read = make_row(U1, read=1)
res = nc.mark_all_read(category="all", days="all")
check("R2 marked count >= batch", res["marked"] >= 5, res)
ok = all(row(b).ts_read_via == "Center Mark All" and row(b).ts_read_at for b in batch)
check("R2 stamps every unread row", ok)
check("R2 leaves pre-read row unstamped (was not in unread set)",
      not row(already_read).ts_read_at)

# R3 bell wrapper single
n3 = make_row(U1)
trail.mark_as_read(n3)
r = row(n3)
check("R3 flips read", cint(r.read) == 1)
check("R3 via Bell Click", r.ts_read_via == "Bell Click")
n3b = make_row(U2)
expect_throw("R3 foreign docname rejected", lambda: trail.mark_as_read(n3b), frappe.PermissionError)
frappe.set_user("Administrator")
check("R3 foreign row not flipped", cint(row(n3b).read) == 0)
check("R3 foreign row not stamped", not row(n3b).ts_read_at)
frappe.set_user(U1)
check("R3 empty docname no-op", trail.mark_as_read("") is None)
# core parity: a missing docname is a SILENT NO-OP in core (db.set_value
# UPDATE matches 0 rows, no exception) — the wrapper must mirror that
check("R3 missing docname no-ops like core",
      trail.mark_as_read("NON-EXISTENT-" + TAG) is None)

# R4 bell wrapper bulk
batch4 = [make_row(U1) for _ in range(4)]
trail.mark_all_as_read()
ok = all(cint(row(b).read) == 1 and row(b).ts_read_via == "Bell Mark All" for b in batch4)
check("R4 flips + stamps all unread", ok)
r_n = row(batch[0])
check("R4 does not restamp earlier reads", r_n.ts_read_via == "Center Mark All")

# stamped-but-different-path precedence: first-write-wins across paths
n_mix = make_row(U1)
nc.mark_read(n_mix)
trail.mark_as_read(n_mix)
check("cross-path stamp is stable", row(n_mix).ts_read_via == "Center Click")

# ═══════════════════════════════════════════════════════════════════════
category("2. Shown-path stamping (Center list windows + toast)")
# ═══════════════════════════════════════════════════════════════════════
frappe.set_user(U1)
shown_rows = [make_row(U1) for _ in range(6)]
page = nc.get_notifications(days="all", page_length=3)
returned = [x["name"] for x in page["rows"]]
check("S1 returns page_length rows", len(returned) == 3, returned)
stamped = [n for n in shown_rows if row(n).ts_first_shown_at]
check("S1 stamps ONLY returned rows",
      set(n for n in stamped) == set(n for n in shown_rows if n in returned),
      (stamped, returned))
check("S1 via Center List",
      all(row(n).ts_first_shown_via == "Center List" for n in stamped))
page2 = nc.get_notifications(days="all", page_length=3, start=3)
returned2 = [x["name"] for x in page2["rows"]]
check("S1 page 2 stamps its window",
      all(row(n).ts_first_shown_at for n in shown_rows if n in returned2))
first_shown = row(returned[0]).ts_first_shown_at
time.sleep(1.1)
nc.get_notifications(days="all", page_length=3)
check("S1 refetch does not move stamp", row(returned[0]).ts_first_shown_at == first_shown)

# filters still work + unread filter path
res = nc.get_notifications(days="all", unread=1, page_length=50)
check("S1 unread filter returns only unread",
      all(not x["read"] for x in res["rows"]))
res = nc.get_notifications(days="all", category="PO", page_length=50)
check("S1 category filter returns PO only",
      all(x["category"] == "PO" for x in res["rows"]))
res = nc.get_notifications(days="all", search=TAG, page_length=50)
check("S1 search filter works", len(res["rows"]) >= 1)

# S2 toast
n_toast = make_row(U1)
t = nc.latest_unread()
check("S2 toast returns newest unread", t and TAG in t["subject"])
r = row(n_toast)
check("S2 stamps Toast Push", r.ts_first_shown_via == "Toast Push", r)
# toast newest-unread now stamped; a Center fetch may not re-stamp
nc.get_notifications(days="all", page_length=50)
check("S2 toast stamp survives Center fetch",
      row(n_toast).ts_first_shown_via == "Toast Push")

# cross-user: U2 fetch must not stamp U1 rows
frappe.set_user(U2)
n_u1 = make_row(U1)
nc.get_notifications(days="all", page_length=50)
nc.latest_unread()
frappe.set_user("Administrator")
check("S1/S2 cross-user isolation", not row(n_u1).ts_first_shown_at)

# ═══════════════════════════════════════════════════════════════════════
category("3. HTTP-method / CSRF evidence integrity")
# ═══════════════════════════════════════════════════════════════════════
# Decorator registry: POST-only on all six stamp paths.
for fn, label in ((nc.get_notifications, "get_notifications"),
                  (nc.latest_unread, "latest_unread"),
                  (nc.mark_read, "mark_read"),
                  (nc.mark_all_read, "mark_all_read"),
                  (trail.mark_as_read, "trail.mark_as_read"),
                  (trail.mark_all_as_read, "trail.mark_all_as_read")):
    methods = frappe.allowed_http_methods_for_whitelisted_func.get(fn, [])
    check("POST-only decorator: " + label, methods == ["POST"], methods)
# read-only endpoints stay GET-callable (badge must keep working)
for fn, label in ((nc.unread_count, "unread_count"),
                  (nc.notification_center_enabled, "notification_center_enabled")):
    methods = frappe.allowed_http_methods_for_whitelisted_func.get(fn, [])
    check("GET stays allowed: " + label, "GET" in methods or not methods, methods)

# _stampable_request behaviour with a fake request object
class _FakeReq:
    def __init__(self, m):
        self.method = m

_orig_req = getattr(frappe.local, "request", None)
try:
    for m, expected in (("GET", False), ("HEAD", False), ("OPTIONS", False),
                        ("POST", True), ("PUT", True), ("PATCH", True), ("DELETE", True)):
        frappe.local.request = _FakeReq(m)
        check("_stampable_request {0} -> {1}".format(m, expected),
              trail._stampable_request() is expected)
    frappe.local.request = _FakeReq("GET")
    frappe.set_user(U1)
    n_get = make_row(U1)
    trail.stamp_read([n_get], "Center Click")
    trail.stamp_shown([n_get], "Center List")
    check("no evidence written on GET", not row(n_get).ts_read_at and not row(n_get).ts_first_shown_at)
finally:
    frappe.local.request = _orig_req
    frappe.set_user("Administrator")
check("server context (no request) stampable", trail._stampable_request() is True)

# unknown field pair is refused (SQL identifier allow-list)
expect_throw("unknown stamp pair refused",
             lambda: trail._stamp(["x"], "subject", "type", "evil"), ValueError)

# ═══════════════════════════════════════════════════════════════════════
category("4. Cross-user isolation (stamp scoping)")
# ═══════════════════════════════════════════════════════════════════════
frappe.set_user(U2)
n_b = make_row(U2)
frappe.set_user(U1)
trail.stamp_read([n_b], "Center Click")   # direct helper abuse attempt as U1
check("stamp helper self-scopes (foreign row untouched)", not row(n_b).ts_read_at)
# outside-trail detection: flip read via raw core-style write, no stamp
n_out = make_row(U1)
frappe.db.set_value("Notification Log", n_out, "read", 1, update_modified=False)
r = row(n_out)
st = report.derive_trail_status(frappe._dict(
    creation=r.creation, read=r.read, ts_read_at=r.ts_read_at,
    ts_read_via=r.ts_read_via, ts_first_shown_at=r.ts_first_shown_at),
    report._trail_start())
check("unobserved flip -> 'Read flag set outside trail'",
      st == report.STATUS_OUTSIDE_TRAIL, st)
frappe.set_user("Administrator")

# ═══════════════════════════════════════════════════════════════════════
category("5. REST tamper attempts (write-perm surface)")
# ═══════════════════════════════════════════════════════════════════════
frappe.set_user(U1)
n_t = make_row(U1)
check("no write perm on Notification Log",
      not frappe.has_permission("Notification Log", "write"))
check("no create perm on Notification Log",
      not frappe.has_permission("Notification Log", "create"))
check("no delete perm on Notification Log",
      not frappe.has_permission("Notification Log", "delete"))


def _tamper_setvalue():
    from frappe.client import set_value
    set_value("Notification Log", n_t, "ts_read_at", str(now_datetime()))


expect_throw("frappe.client.set_value denied", _tamper_setvalue, frappe.PermissionError)


def _tamper_save():
    d = frappe.get_doc("Notification Log", n_t)
    d.ts_read_at = now_datetime()
    d.save()


expect_throw("doc.save() denied", _tamper_save, frappe.PermissionError)
check("row still unstamped after tamper attempts", not row(n_t).ts_read_at)
# spot-check a representative custom role too
frappe.set_user("Administrator")
some_role_user = frappe.db.get_value(
    "Has Role", {"role": "Stores Manager", "parenttype": "User"}, "parent")
if some_role_user and some_role_user not in ("Administrator",):
    frappe.set_user(some_role_user)
    check("custom role lacks write perm too",
          not frappe.has_permission("Notification Log", "write"))
    frappe.set_user("Administrator")
else:
    check("custom role lacks write perm too", True, "no Stores Manager user; vacuous")

# ═══════════════════════════════════════════════════════════════════════
category("6. Override parity with core bell")
# ═══════════════════════════════════════════════════════════════════════
import inspect
from frappe.desk.doctype.notification_log import notification_log as core_nl

check("wrapper returns core's return (None)", trail.mark_all_as_read() is None)
frappe.set_user(U1)
n_p = make_row(U1)
check("wrapper single returns None (core parity)", trail.mark_as_read(n_p) is None)
sig = inspect.signature(trail.mark_as_read)
check("wrapper accepts docname param", "docname" in sig.parameters)
# read_only replica parity (try/finally — a failure mid-block must not leak
# the flag into later categories, security L4)
n_ro = make_row(U1)
try:
    frappe.flags.read_only = 1
    check("read_only early-return (no flip)", trail.mark_as_read(n_ro) is None and cint(row(n_ro).read) == 0)
    check("read_only: no stamp", not row(n_ro).ts_read_at)
finally:
    frappe.flags.read_only = 0
trail.mark_as_read(n_ro)
check("after read_only lifted, works", cint(row(n_ro).read) == 1)
# Administrator carve-out (security M1): admin bell lists everyone's rows —
# a foreign-row click must flip (core parity) but never stamp (self-scope)
frappe.set_user("Administrator")
n_adm = make_row(U1)
check("Administrator can flip foreign row (core parity)",
      trail.mark_as_read(n_adm) is None and cint(row(n_adm).read) == 1)
check("Administrator flip writes NO stamp (self-scoped)",
      not row(n_adm).ts_read_at)

# ═══════════════════════════════════════════════════════════════════════
category("7. Kill switch + fail-soft")
# ═══════════════════════════════════════════════════════════════════════
set_switch(0)
frappe.set_user(U1)
n_off1 = make_row(U1)
n_off2 = make_row(U1)
nc.get_notifications(days="all", page_length=50)
nc.mark_read(n_off1)
res = nc.mark_all_read(category="all", days="all")
trail.mark_all_as_read()
r1, r2 = row(n_off1), row(n_off2)
check("switch OFF: read flip still works", cint(r1.read) == 1 and cint(r2.read) == 1)
check("switch OFF: zero read stamps", not r1.ts_read_at and not r2.ts_read_at)
check("switch OFF: zero shown stamps", not r1.ts_first_shown_at and not r2.ts_first_shown_at)
n_off3 = make_row(U1)
trail.mark_as_read(n_off3)
check("switch OFF: bell path flips, no stamp",
      cint(row(n_off3).read) == 1 and not row(n_off3).ts_read_at)
set_switch(1)
n_on = make_row(U1)
nc.mark_read(n_on)
check("switch back ON: stamping resumes", bool(row(n_on).ts_read_at))
check("no retro-stamping of OFF-window rows", not row(n_off1).ts_read_at)
frappe.set_user("Administrator")
# missing Singles row => fail-open
frappe.db.sql("DELETE FROM tabSingles WHERE doctype='TS Settings' AND field=%s",
              (trail.KILL_SWITCH_FIELD,))
check("missing switch row -> fail-open ON", trail._trail_enabled() is True)
set_switch(1)
# fail-soft: stamp exception must not break the endpoint
_orig_stamp = trail._stamp
def _boom(*a, **k):
    raise frappe.db.InternalError("forced")
trail._stamp = _boom
frappe.set_user(U1)
n_soft = make_row(U1)
try:
    out = nc.mark_read(n_soft)
    check("stamp failure: endpoint still succeeds", out.get("ok") == 1)
    check("stamp failure: read still flipped", cint(row(n_soft).read) == 1)
finally:
    trail._stamp = _orig_stamp
    frappe.set_user("Administrator")

# ═══════════════════════════════════════════════════════════════════════
category("8. Retention neutrality (modified untouched)")
# ═══════════════════════════════════════════════════════════════════════
frappe.set_user(U1)
pairs = []
for via_path in ("center_click", "center_all", "bell_click", "bell_all", "shown"):
    n_m = make_row(U1)
    before = frappe.db.get_value("Notification Log", n_m, "modified")
    if via_path == "center_click":
        nc.mark_read(n_m)
    elif via_path == "center_all":
        nc.mark_all_read(category="all", days="all")
    elif via_path == "bell_click":
        trail.mark_as_read(n_m)
    elif via_path == "bell_all":
        trail.mark_all_as_read()
    else:
        nc.get_notifications(days="all", page_length=50)
    after = frappe.db.get_value("Notification Log", n_m, "modified")
    pairs.append((via_path, before, after))
    check("modified unchanged via " + via_path, str(before) == str(after), (before, after))
frappe.set_user("Administrator")

# ═══════════════════════════════════════════════════════════════════════
category("9. Races / repeat invocation")
# ═══════════════════════════════════════════════════════════════════════
frappe.set_user(U1)
n_race = make_row(U1)
for i in range(6):  # tight repeat loop — conditional UPDATE must keep 1st value
    trail.stamp_read([n_race], "Center Click" if i == 0 else "Bell Click")
check("repeat stamps keep first via", row(n_race).ts_read_via == "Center Click")
n_race2 = make_row(U1)
trail.stamp_shown([n_race2, n_race2, n_race2], "Center List")  # dup names in one call
check("duplicate names in one call are safe", row(n_race2).ts_first_shown_via == "Center List")
check("empty names no-op", trail.stamp_read([], "Center Click") is None)
# chunking (security L3): a 2500-name batch must run without packet errors
big = ["FAKE-{0}-{1}".format(TAG, i) for i in range(2500)]
try:
    trail._stamp(big, "ts_read_at", "ts_read_via", "Center Mark All")
    check("2500-name batch chunks cleanly", True)
except Exception as e:
    check("2500-name batch chunks cleanly", False, e)
frappe.set_user("Administrator")

# ═══════════════════════════════════════════════════════════════════════
category("10. Trail-status derivation (full matrix)")
# ═══════════════════════════════════════════════════════════════════════
TS = report._trail_start()
NOWDT = now_datetime()
PRE = add_days(TS, -3) if TS else None
POST = NOWDT


def st(creation, read, rat, rvia, sat):
    return report.derive_trail_status(frappe._dict(
        creation=creation, read=read, ts_read_at=rat, ts_read_via=rvia,
        ts_first_shown_at=sat), TS)


if PRE:
    for read in (0, 1):
        for rat, rvia in ((None, None), (NOWDT, "Center Click")):
            for sat in (None, NOWDT):
                check("pre-trail dominates all combos",
                      st(PRE, read, rat, rvia, sat) == report.STATUS_PRE_TRAIL)
for rvia in ("Center Click", "Bell Click"):
    for sat in (None, NOWDT):
        check("opened-by-user " + rvia,
              st(POST, 1, NOWDT, rvia, sat) == report.STATUS_READ_OPENED)
EARLIER = add_days(NOWDT, -1)
for rvia in ("Center Mark All", "Bell Mark All"):
    check("bulk after delivery " + rvia,
          st(POST, 1, NOWDT, rvia, NOWDT) == report.STATUS_READ_BULK_AFTER)
    check("bulk blind " + rvia,
          st(POST, 1, NOWDT, rvia, None) == report.STATUS_READ_BULK_BLIND)
    # ORDER not presence (code-tester blocker): shown BEFORE read = after
    # delivery; shown AFTER read must STAY blind — no exculpatory decay
    check("bulk shown-before-read stays AFTER " + rvia,
          st(POST, 1, NOWDT, rvia, EARLIER) == report.STATUS_READ_BULK_AFTER)
    check("bulk shown-AFTER-read stays BLIND " + rvia,
          st(POST, 1, EARLIER, rvia, NOWDT) == report.STATUS_READ_BULK_BLIND)

# live decay-regression sequence: bell blind-clear, THEN the Center's default
# view (unread=0 → returns read rows) stamps delivery — status must not decay
frappe.set_user(U1)
n_decay = make_row(U1)
trail.mark_all_as_read()                       # Bell Mark All, never displayed
nc.get_notifications(days="all", page_length=50)  # post-read delivery stamp
r_d = row(n_decay)
check("decay repro: both stamps present, shown after read",
      bool(r_d.ts_read_at) and bool(r_d.ts_first_shown_at)
      and r_d.ts_first_shown_at >= r_d.ts_read_at, r_d)
check("decay repro: status stays bulk-blind",
      report.derive_trail_status(frappe._dict(
          creation=r_d.creation, read=r_d.read, ts_read_at=r_d.ts_read_at,
          ts_read_via=r_d.ts_read_via, ts_first_shown_at=r_d.ts_first_shown_at),
          TS) == report.STATUS_READ_BULK_BLIND)
frappe.set_user("Administrator")
check("outside trail", st(POST, 1, None, None, None) == report.STATUS_OUTSIDE_TRAIL)
check("outside trail (shown but unstamped read)",
      st(POST, 1, None, None, NOWDT) == report.STATUS_OUTSIDE_TRAIL)
check("delivered not read", st(POST, 0, None, None, NOWDT) == report.STATUS_SHOWN_UNREAD)
check("no delivery", st(POST, 0, None, None, None) == report.STATUS_NO_DELIVERY)

# ═══════════════════════════════════════════════════════════════════════
category("11. Report role gate")
# ═══════════════════════════════════════════════════════════════════════
DENIED_ROLES = ("GM", "Dept Head", "Purchase Manager", "Grain Purchase Manager",
                "Stores Manager", "QI", "G1 Operator", "G2 Operator",
                "WB Operator", "AVP", "Admin Reception", "Accounts Manager",
                "Purchase User", "Quality Manager")


def _role_user(role):
    return frappe.db.sql(
        """SELECT hr.parent FROM `tabHas Role` hr
            JOIN `tabUser` u ON u.name = hr.parent AND u.enabled = 1
           WHERE hr.role = %s AND hr.parenttype = 'User'
             AND hr.parent NOT IN ('Administrator', 'Guest')
             AND hr.parent NOT IN (SELECT hr2.parent FROM `tabHas Role` hr2
                                   WHERE hr2.role IN ('System Manager','CEO','MD','IT Head')
                                     AND hr2.parenttype='User')
           LIMIT 1""", (role,))


for role in ("CEO", "MD", "System Manager", "IT Head"):
    u = frappe.db.sql(
        """SELECT hr.parent FROM `tabHas Role` hr
            JOIN `tabUser` u ON u.name = hr.parent AND u.enabled = 1
           WHERE hr.role = %s AND hr.parenttype='User'
             AND hr.parent NOT IN ('Administrator','Guest') LIMIT 1""", (role,))
    if u:
        frappe.set_user(u[0][0])
        try:
            report.execute({})
            check("allowed role passes: " + role, True)
        except Exception as e:
            check("allowed role passes: " + role, False, e)
        frappe.set_user("Administrator")
    else:
        check("allowed role passes: " + role, True, "no user with role; vacuous")

for role in DENIED_ROLES:
    u = _role_user(role)
    if u:
        frappe.set_user(u[0][0])
        expect_throw("denied role blocked: " + role,
                     lambda: report.execute({}), frappe.PermissionError)
        frappe.set_user("Administrator")
    else:
        check("denied role blocked: " + role, True, "no pure user with role; vacuous")

frappe.set_user("Guest")
expect_throw("Guest blocked", lambda: report.execute({}), frappe.PermissionError)
frappe.set_user(U1)  # role-less throwaway user
expect_throw("role-less user blocked", lambda: report.execute({}), frappe.PermissionError)
frappe.set_user("Administrator")
try:
    report.execute({})
    check("Administrator passes", True)
except Exception as e:
    check("Administrator passes", False, e)

# ═══════════════════════════════════════════════════════════════════════
category("12. Report input hardening")
# ═══════════════════════════════════════════════════════════════════════
frappe.set_user("Administrator")
expect_throw("span > 180d rejected",
             lambda: report.execute({"from_date": "2025-01-01", "to_date": "2026-07-30"}))
expect_throw("reversed dates rejected",
             lambda: report.execute({"from_date": "2026-07-30", "to_date": "2026-07-01"}))
expect_throw("unknown user rejected",
             lambda: report.execute({"user": "nobody@nowhere.zz' OR '1'='1"}))
cols, data, msg = report.execute({"category": "PO'; DROP TABLE x;--"})
check("hostile category neutralised to 'all'", isinstance(data, list))
cols, data, msg = report.execute({"status": "'; DELETE FROM tabUser;--"})
check("hostile status neutralised (ignored)", isinstance(data, list))
cols, data, msg = report.execute({"user": U1})
check("valid user filter works", all(d.for_user == U1 for d in data) if data else True)
check("banner present + honest", "does" in msg and "180" in msg)
check("row cap literal", report.ROW_LIMIT == 5000)
# Administrator system account hidden everywhere (user request 31 Jul)
n_admin_row = make_row("Administrator")
cols, data, msg = report.execute({"from_date": str(add_days(now_datetime(), -1)),
                                  "to_date": str(now_datetime())})
check("Administrator rows hidden from report",
      all(d.for_user != "Administrator" for d in data))
cols, data, msg = report.execute({"user": "Administrator"})
check("user=Administrator filter returns nothing", len(data) == 0)
check("banner states Administrator exclusion", "Administrator system" in msg)

# ═══════════════════════════════════════════════════════════════════════
category("13. Report confidentiality")
# ═══════════════════════════════════════════════════════════════════════
# derive: a viewer allowed by role gate but NOT on the confidential allow-list
# (System Manager and IT Head ARE in _ADMIN_ROLES, CEO/MD in _ALLOW_ROLES —
# so by design every default-audience member sees PO rows; assert the guard
# still runs and drops nothing for them, and no subject leaks in payload)
cols, data, msg = report.execute({"user": U1})
check("no subject column in payload",
      all("subject" not in (d if isinstance(d, dict) else d.__dict__ or {}) for d in data))
check("no subject in column defs", all(c["fieldname"] != "subject" for c in cols))
from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential
check("confidential helper importable + callable",
      user_sees_confidential("Purchase Order", "Administrator") is True)

# ═══════════════════════════════════════════════════════════════════════
category("14. Seeder idempotency / persistence")
# ═══════════════════════════════════════════════════════════════════════
from trustbit_ethanol.ts_gate_entry import setup_notification_trail as seed

before_fields = frappe.db.count("Custom Field", {"dt": "Notification Log",
                                                 "fieldname": ["like", "ts_%"]})
before_created = frappe.db.get_value(
    "Custom Field", {"dt": "Notification Log", "fieldname": "ts_read_at"}, "creation")
set_switch(0)  # admin turned it off — seeder must NOT flip it back
for _ in range(3):
    seed.after_migrate_notification_trail()
after_fields = frappe.db.count("Custom Field", {"dt": "Notification Log",
                                                "fieldname": ["like", "ts_%"]})
check("field count stable over 3 seeder runs", before_fields == after_fields)
check("trail-start timestamp stable",
      str(before_created) == str(frappe.db.get_value(
          "Custom Field", {"dt": "Notification Log", "fieldname": "ts_read_at"}, "creation")))
check("kill-switch value preserved (insert-only)", trail._trail_enabled() is False)
set_switch(1)
rep_roles = [r.role for r in frappe.get_doc("Report", "TS Notification Read Audit").roles]
check("report roles not duplicated", len(rep_roles) == len(set(rep_roles)))
err_count = frappe.db.count("Error Log", {
    "creation": [">", add_days(now_datetime(), -1)],
    "method": ["like", "%notification trail seeder%"]})
check("seeder Error Log clean", err_count == 0, err_count)

# ═══════════════════════════════════════════════════════════════════════
category("15. Performance bounds")
# ═══════════════════════════════════════════════════════════════════════
frappe.set_user(U1)
perf_rows = [make_row(U1) for _ in range(30)]
t0 = time.time()
for _ in range(5):
    nc.get_notifications(days="all", page_length=50)
dt_stamped = (time.time() - t0) / 5
check("get_notifications with stamping < 500ms avg", dt_stamped < 0.5, dt_stamped)
frappe.set_user("Administrator")  # report timing must run as an authorised user
t0 = time.time()
report.execute({"user": U1})
check("report run < 3s", time.time() - t0 < 3)

# ═══════════════════════════════════════════════════════════════════════
category("16. Timezone honesty + snapshot")
# ═══════════════════════════════════════════════════════════════════════
frappe.set_user(U1)
n_tz = make_row(U1)
nc.mark_read(n_tz)
delta = abs((now_datetime() - row(n_tz).ts_read_at).total_seconds())
check("stamp within 60s of site now_datetime (site tz)", delta < 60, delta)
frappe.set_user("Administrator")
# tranche semantics: a row aged into the archive window IS archived, a
# fresh row is NOT (bounded job — predictor finding); Administrator rows
# are excluded from the archive like the report
n_old = make_row(U1)
n_new = make_row(U1)
n_admin_old = make_row("Administrator")
frappe.db.sql("UPDATE `tabNotification Log` SET creation=%s WHERE name IN %s",
              (add_days(now_datetime(), -150), (n_old, n_admin_old)))
trail.monthly_audit_snapshot()
snap_name = "notification_trail_snapshot_{0}.csv".format(now_datetime().strftime("%Y_%m"))
snap = frappe.db.get_value("File", {"file_name": snap_name}, ["name", "is_private"], as_dict=True)
check("monthly snapshot file created", bool(snap))
check("snapshot is PRIVATE", snap and cint(snap.is_private) == 1)
trail.monthly_audit_snapshot()  # idempotent
check("snapshot idempotent per month",
      frappe.db.count("File", {"file_name": snap_name}) == 1)
if snap:
    content = frappe.get_doc("File", snap.name).get_content()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    check("snapshot has no subject column", "subject" not in content.splitlines()[0])
    check("snapshot carries trail_status", "trail_status" in content.splitlines()[0])
    check("snapshot includes aging-out tranche row", n_old in content)
    check("snapshot excludes fresh row (bounded)", n_new not in content)
    check("snapshot excludes Administrator rows", n_admin_old not in content)
# H1 + squatter-rename: plant a user upload FIRST (it takes the exact
# filename AND the disk path), so the scheduler's own File gets dedup-RENAMED
# on insert — the pinned PREFIX filters must (a) not be suppressed by the
# plant, (b) stay idempotent against the renamed archive, (c) never serve
# the planted content to an auditor.
month_now = now_datetime().strftime("%Y_%m")
for old in frappe.get_all("File", filters={"file_name": ["like", "notification_trail_snapshot_%"]},
                          pluck="name"):
    frappe.delete_doc("File", old, ignore_permissions=True, force=True)  # clean slate + frees disk path
frappe.set_user(U1)
fake = frappe.get_doc({"doctype": "File", "file_name": snap_name,
                       "is_private": 1, "content": "FAKE-PLANTED-CSV"})
fake.insert()  # role All HAS create on File — that is the whole point
frappe.set_user("Administrator")
check("planted file is user-owned + holds exact name (precondition)",
      frappe.db.get_value("File", fake.name, "owner") == U1
      and frappe.db.get_value("File", fake.name, "file_name") == snap_name)
trail.monthly_audit_snapshot()   # must CREATE despite the plant (guard pinned)
admin_snap = frappe.db.get_value("File", trail._snapshot_file_filters(month_now),
                                 ["name", "file_name"], as_dict=True,
                                 order_by="creation asc")
check("scheduler wrote archive despite squatter", bool(admin_snap))
check("archive was dedup-RENAMED by the squatter (scenario is real)",
      admin_snap and admin_snap.file_name != snap_name, admin_snap)
trail.monthly_audit_snapshot()   # idempotency must match the RENAMED archive
check("idempotent against renamed archive",
      frappe.db.count("File", trail._snapshot_file_filters(month_now)) == 1)
report.download_snapshot(month_now)
served = frappe.response.get("filecontent")
if isinstance(served, bytes):
    served = served.decode("utf-8")
check("download retrieves the REAL archive past the squatter",
      "FAKE-PLANTED-CSV" not in (served or "") and "trail_status" in (served or ""))
check("download serves canonical filename",
      frappe.response.get("filename") == snap_name)
# scoped to THIS run (code-tester): the canary deliberately leaves a durable
# record — an unbounded count would red-line the suite forever after one
# legitimate production fire, training people to delete audit evidence
check("no retrievability canary logged THIS RUN (prefix found the rename)",
      frappe.db.count("Error Log", {
          "error": ["like", "%NOT retrievable via the canonical%"],
          "creation": [">=", RUN_START]}) == 0)
frappe.delete_doc("File", fake.name, ignore_permissions=True, force=True)
snap = frappe.db.get_value("File", trail._snapshot_file_filters(month_now),
                           ["name", "is_private"], as_dict=True)

# gated download: report audience only
expect_throw("download_snapshot bad month rejected",
             lambda: report.download_snapshot("2026-07"))
try:
    report.download_snapshot(now_datetime().strftime("%Y_%m"))
    check("download_snapshot works for Administrator",
          frappe.response.get("type") == "download" and
          bool(frappe.response.get("filecontent")))
except Exception as e:
    check("download_snapshot works for Administrator", False, e)
frappe.set_user(U1)
expect_throw("download_snapshot denied for role-less user",
             lambda: report.download_snapshot(), frappe.PermissionError)
frappe.set_user("Administrator")

# ═══════════════════════════════════════════════════════════════════════
#  CLEANUP + RESULTS
# ═══════════════════════════════════════════════════════════════════════
print("\n\n=== CLEANUP ===")
frappe.set_user("Administrator")
# wide LIKE catches strays from any earlier crashed run, not just this TAG
frappe.db.sql("DELETE FROM `tabNotification Log` WHERE subject LIKE %s", ("%TRAILTEST-%",))
if snap:
    frappe.delete_doc("File", snap.name, ignore_permissions=True, force=True)
for email in (U1, U2):
    try:
        frappe.delete_doc("User", email, ignore_permissions=True, force=True)
    except Exception:
        pass
frappe.db.commit()
print("Deleted {0} tagged notification rows + 2 users + snapshot".format(len(_cleanup_rows)))

print("\n" + "=" * 60)
print("NOTIFICATION TRAIL TEST RESULTS")
for cat_label, cnt in _category_counts.items():
    print("  {0:<48} {1:>3} checks".format(cat_label, cnt))
print("Total: {0} | Passed: {1} | Failed: {2}".format(
    results["passed"] + results["failed"], results["passed"], results["failed"]))
if results["errors"]:
    print("\nFAILURES:")
    for e in results["errors"]:
        print("  [{cat}] {test}\n      {detail}".format(**e))
sys.exit(1 if results["failed"] else 0)
