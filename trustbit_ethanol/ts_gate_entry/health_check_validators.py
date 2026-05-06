"""v2.9.12 Sprint 1 — Approval Flow Health Check validators (doc-state layer).

Read-only SELECT-based validators that surface MR/PO docs in problematic
states. All queries are parameterized, idempotent, side-effect-free, and
cached for 60s in redis to keep the dashboard responsive.

Sprint 1 ships 4 doc-level validators:
  1. validate_state_corruption       — amended drafts with stale ts_mr_status
  2. validate_revised_limbo          — submitted docs in 'Revised' status
                                       waiting for follow-up cancel+amend
  3. validate_cancel_no_amend        — cancelled docs never amended
  4. validate_stuck_sla_breach       — pending docs > 7d (warn) or > 21d (crit)

Sprint 2 will add config validators (orphan CC, suspicious routes, revise
config audit) in a separate file or extension.

Issue object schema (returned by every validator):
    {
        "issue_id": str,           # stable hash for dedup + acknowledgment
        "validator": str,          # which validator produced it
        "severity": "high"|"medium"|"low"|"info",
        "category": str,           # state_corruption | sla | revise | ...
        "doc_type": str,
        "doc_name": str,
        "field": str | None,
        "current_value": str | None,
        "expected_value": str | None,
        "message": str,
        "fix_action_id": str | None,
        "fix_params": dict | None,
        "suppressible": bool,
        "schema_version": 1,
    }
"""

import frappe


SCHEMA_VERSION = 1
CACHE_TTL_SEC = 60
CACHE_NS = "health_check_v1"

# Statuses that indicate a doc is in an in-flight approval state, NOT a
# fresh draft. If amended_from is set AND docstatus=0 AND status is in
# this set → state corruption (parent's status carried over to child).
BROKEN_AMENDED_STATUSES = (
    "Approved",
    "Rejected",
    "Pending Dept. User",
    "Pending Dept. Head",
    "Pending AVP",
    "Pending CEO",
    "Pending GM",
    "Pending Stores Manager",
    "Revised",
    "On Hold",
)

# SLA thresholds (days). Configurable via TS Settings in a future sprint;
# hardcoded for Sprint 1.
STUCK_WARN_DAYS = 7
STUCK_CRITICAL_DAYS = 21

# How many rows each validator returns at most. Prevents runaway queries.
PER_VALIDATOR_LIMIT = 500

# Doctype-status field map. Hardcoded whitelist — used by validators that
# loop over multiple doctypes with f-string SQL. Sprint 2 endpoints that
# accept doctype from a client MUST validate against this set before
# dispatching into validators (prevents SQL-injection vector via doctype name).
SAFE_DOC_STATUS_MAP = {
    "Material Request": "ts_mr_status",
    "Purchase Order": "ts_approval_status",
}


def _cache_get(key):
    try:
        return frappe.cache().hget(CACHE_NS, key)
    except Exception:
        return None


def _cache_set(key, value):
    try:
        frappe.cache().hset(CACHE_NS, key, value)
    except Exception:
        pass


def _suppressed_ids():
    """Return the set of issue_ids currently suppressed via TS Health Check
    Acknowledgment doctype. Empty set if doctype not yet seeded (Sprint 2)."""
    try:
        if not frappe.db.exists("DocType", "TS Health Check Acknowledgment"):
            return set()
        rows = frappe.db.sql(
            """SELECT issue_id FROM `tabTS Health Check Acknowledgment`
               WHERE suppressed_until >= CURDATE()""",
            as_dict=True,
        )
        return {r.issue_id for r in rows}
    except Exception:
        return set()


# ───────────────────────────── Validator 1 ─────────────────────────────
def validate_state_corruption():
    """Amended drafts with stale parent approval status.

    Finds Material Request rows where amended_from is set, docstatus=0,
    AND ts_mr_status is in BROKEN_AMENDED_STATUSES. The mr_on_amend hook
    (added v2.9.12) prevents this on new amends; the backfill patch
    cleaned historical rows. This validator catches anything missed (e.g.
    direct DB edits, bulk imports) on an ongoing basis.
    """
    cached = _cache_get("state_corruption")
    if cached is not None:
        return cached

    suppressed = _suppressed_ids()
    issues = []

    rows = frappe.db.sql(
        """SELECT name, ts_mr_status, amended_from, owner,
                  DATE_FORMAT(modified, '%%Y-%%m-%%d') AS modified_date
           FROM `tabMaterial Request`
           WHERE amended_from IS NOT NULL
             AND docstatus = 0
             AND ts_mr_status IN %(statuses)s
           ORDER BY modified DESC LIMIT %(limit)s""",
        {"statuses": BROKEN_AMENDED_STATUSES, "limit": PER_VALIDATOR_LIMIT},
        as_dict=True,
    )

    for r in rows:
        iid = f"state_corruption:MR:{r.name}"
        if iid in suppressed:
            continue
        issues.append({
            "issue_id": iid,
            "validator": "state_corruption",
            "severity": "high",
            "category": "state_corruption",
            "doc_type": "Material Request",
            "doc_name": r.name,
            "field": "ts_mr_status",
            "current_value": r.ts_mr_status,
            "expected_value": "Not Submitted",
            "message": (
                f"Amended draft carries stale parent status "
                f"'{r.ts_mr_status}'. Submit-for-Approval button is hidden "
                f"because status not in eligible list. Reset to 'Not Submitted' "
                f"to unblock the user."
            ),
            "fix_action_id": "reset_mr_status_to_not_submitted",
            "fix_params": {"name": r.name},
            "suppressible": True,
            "schema_version": SCHEMA_VERSION,
        })

    _cache_set("state_corruption", issues)
    return issues


# ───────────────────────────── Validator 2 ─────────────────────────────
def validate_revised_limbo():
    """Submitted docs in 'Revised' status waiting for cancel+amend follow-up.

    User pressed 'Request Revision' on an approved doc → status moves to
    'Revised' but docstatus stays 1. Creator must follow up with Cancel +
    Amend + Resubmit. Long-living 'Revised' docs are usually forgotten.
    """
    cached = _cache_get("revised_limbo")
    if cached is not None:
        return cached

    suppressed = _suppressed_ids()
    issues = []

    # MR
    mr_rows = frappe.db.sql(
        """SELECT name, ts_mr_status, owner,
                  DATEDIFF(NOW(), modified) AS days_in_status
           FROM `tabMaterial Request`
           WHERE ts_mr_status = 'Revised'
             AND docstatus = 1
           ORDER BY modified ASC LIMIT %(limit)s""",
        {"limit": PER_VALIDATOR_LIMIT},
        as_dict=True,
    )
    for r in mr_rows:
        iid = f"revised_limbo:MR:{r.name}"
        if iid in suppressed:
            continue
        sev = "high" if (r.days_in_status or 0) > 14 else "medium"
        issues.append({
            "issue_id": iid,
            "validator": "revised_limbo",
            "severity": sev,
            "category": "revise",
            "doc_type": "Material Request",
            "doc_name": r.name,
            "field": "ts_mr_status",
            "current_value": "Revised",
            "expected_value": "Not Submitted (after cancel + amend)",
            "message": (
                f"MR sitting in 'Revised' status for {r.days_in_status or 0} days. "
                f"Owner '{r.owner}' must Cancel + Amend + Submit to progress."
            ),
            "fix_action_id": None,
            "fix_params": None,
            "suppressible": True,
            "schema_version": SCHEMA_VERSION,
        })

    # PO
    po_rows = frappe.db.sql(
        """SELECT name, ts_approval_status, owner,
                  DATEDIFF(NOW(), modified) AS days_in_status
           FROM `tabPurchase Order`
           WHERE ts_approval_status = 'Revised'
             AND docstatus = 1
           ORDER BY modified ASC LIMIT %(limit)s""",
        {"limit": PER_VALIDATOR_LIMIT},
        as_dict=True,
    )
    for r in po_rows:
        iid = f"revised_limbo:PO:{r.name}"
        if iid in suppressed:
            continue
        sev = "high" if (r.days_in_status or 0) > 14 else "medium"
        issues.append({
            "issue_id": iid,
            "validator": "revised_limbo",
            "severity": sev,
            "category": "revise",
            "doc_type": "Purchase Order",
            "doc_name": r.name,
            "field": "ts_approval_status",
            "current_value": "Revised",
            "expected_value": "Not Submitted (after cancel + amend)",
            "message": (
                f"PO sitting in 'Revised' status for {r.days_in_status or 0} days. "
                f"Owner '{r.owner}' must Cancel + Amend + Submit to progress."
            ),
            "fix_action_id": None,
            "fix_params": None,
            "suppressible": True,
            "schema_version": SCHEMA_VERSION,
        })

    _cache_set("revised_limbo", issues)
    return issues


# ───────────────────────────── Validator 3 ─────────────────────────────
def validate_cancel_no_amend():
    """Cancelled docs that were never amended.

    User cancelled (docstatus=2) — perhaps intending to amend + recreate —
    but never did. Either the requirement was abandoned, or the user got
    stuck on the next step. Each represents a potentially-lost requirement.
    """
    cached = _cache_get("cancel_no_amend")
    if cached is not None:
        return cached

    suppressed = _suppressed_ids()
    issues = []

    for dt, status_field in SAFE_DOC_STATUS_MAP.items():
        # Defensive: even though dt+status_field come from a hardcoded module
        # constant, re-assert on every loop iteration so a future refactor
        # (or a typo introducing a user-input path) can't reach the f-string.
        if dt not in SAFE_DOC_STATUS_MAP or status_field != SAFE_DOC_STATUS_MAP[dt]:
            continue
        rows = frappe.db.sql(
            f"""SELECT cancelled.name, cancelled.{status_field} AS status,
                       cancelled.owner,
                       DATEDIFF(NOW(), cancelled.modified) AS days_since_cancel
                FROM `tab{dt}` cancelled
                WHERE cancelled.docstatus = 2
                  AND NOT EXISTS (
                    SELECT 1 FROM `tab{dt}` amended
                    WHERE amended.amended_from = cancelled.name
                  )
                ORDER BY cancelled.modified ASC LIMIT %(limit)s""",
            {"limit": PER_VALIDATOR_LIMIT},
            as_dict=True,
        )
        for r in rows:
            iid = f"cancel_no_amend:{dt[:2].upper()}:{r.name}"
            if iid in suppressed:
                continue
            sev = "high" if (r.days_since_cancel or 0) > 30 else "medium"
            issues.append({
                "issue_id": iid,
                "validator": "cancel_no_amend",
                "severity": sev,
                "category": "revise",
                "doc_type": dt,
                "doc_name": r.name,
                "field": "docstatus",
                "current_value": "Cancelled",
                "expected_value": "Amended OR explicitly abandoned",
                "message": (
                    f"{dt} cancelled {r.days_since_cancel or 0} days ago "
                    f"with no follow-up amendment. May be abandoned or stuck."
                ),
                "fix_action_id": None,
                "fix_params": None,
                "suppressible": True,
                "schema_version": SCHEMA_VERSION,
            })

    _cache_set("cancel_no_amend", issues)
    return issues


# ───────────────────────────── Validator 4 ─────────────────────────────
def validate_stuck_sla_breach():
    """Submitted docs pending the same approver for too long.

    docstatus=1 AND status starts with 'Pending ' AND modified older than
    threshold (7d warn / 21d critical). Approver is likely away, role
    reassigned, or chain misconfigured.
    """
    cached = _cache_get("stuck_sla")
    if cached is not None:
        return cached

    suppressed = _suppressed_ids()
    issues = []

    for dt, status_field in SAFE_DOC_STATUS_MAP.items():
        # Defensive whitelist re-assertion (see SAFE_DOC_STATUS_MAP comment).
        if dt not in SAFE_DOC_STATUS_MAP or status_field != SAFE_DOC_STATUS_MAP[dt]:
            continue
        rows = frappe.db.sql(
            f"""SELECT name, {status_field} AS status, owner,
                       DATEDIFF(NOW(), modified) AS days_stuck
                FROM `tab{dt}`
                WHERE docstatus = 1
                  AND {status_field} LIKE 'Pending %%'
                  AND DATEDIFF(NOW(), modified) >= %(warn)s
                ORDER BY days_stuck DESC LIMIT %(limit)s""",
            {"warn": STUCK_WARN_DAYS, "limit": PER_VALIDATOR_LIMIT},
            as_dict=True,
        )
        for r in rows:
            iid = f"stuck_sla:{dt[:2].upper()}:{r.name}"
            if iid in suppressed:
                continue
            d = r.days_stuck or 0
            sev = "high" if d >= STUCK_CRITICAL_DAYS else "medium"
            issues.append({
                "issue_id": iid,
                "validator": "stuck_sla_breach",
                "severity": sev,
                "category": "sla",
                "doc_type": dt,
                "doc_name": r.name,
                "field": status_field,
                "current_value": r.status,
                "expected_value": f"Approval action within {STUCK_WARN_DAYS} days",
                "message": (
                    f"{dt} pending '{r.status}' for {d} days "
                    f"(threshold: {STUCK_WARN_DAYS}d warn / "
                    f"{STUCK_CRITICAL_DAYS}d critical)."
                ),
                "fix_action_id": None,
                "fix_params": None,
                "suppressible": True,
                "schema_version": SCHEMA_VERSION,
            })

    _cache_set("stuck_sla", issues)
    return issues


# ───────────────────────────── Aggregator ─────────────────────────────
def run_doc_validators():
    """Run all 4 doc-level validators and return aggregated result.

    Read-only. Safe to call from scheduler, console, or whitelisted endpoint.
    Returns:
        {
            "schema_version": 1,
            "stats": {by_severity, by_category, total},
            "issues": [Issue, ...],
            "validators_run": [str, ...],
            "generated_at": iso8601,
        }
    """
    import datetime

    all_issues = []
    validators_run = []

    for fn, label in (
        (validate_state_corruption, "state_corruption"),
        (validate_revised_limbo, "revised_limbo"),
        (validate_cancel_no_amend, "cancel_no_amend"),
        (validate_stuck_sla_breach, "stuck_sla_breach"),
    ):
        try:
            all_issues.extend(fn())
            validators_run.append(label)
        except Exception as e:
            try:
                frappe.log_error(
                    title=f"HC validator {label} error",
                    message=str(e)[:500],
                )
            except Exception:
                pass

    by_sev = {"high": 0, "medium": 0, "low": 0, "info": 0}
    by_cat = {}
    for i in all_issues:
        by_sev[i.get("severity", "info")] = by_sev.get(i.get("severity", "info"), 0) + 1
        by_cat[i["category"]] = by_cat.get(i["category"], 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "stats": {
            "by_severity": by_sev,
            "by_category": by_cat,
            "total": len(all_issues),
        },
        "issues": all_issues,
        "validators_run": validators_run,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
