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
# v2.28.4 — MR vocabulary only (this validator reads ts_mr_status). The retired
# abbreviations "Pending Dept. User"/"Pending Dept. Head"/"Pending GM" are replaced
# by the values the engine actually writes; the full-name spellings introduced in
# v2.28.3 are ALSO added, because this tuple was silently blind to every one of them
# (a corrupted amend sitting at e.g. "Pending Department Head" went undetected).
# Keep this in step with the ts_mr_status options in seed_data.py.
BROKEN_AMENDED_STATUSES = (
    "Approved",
    "Rejected",
    "Revised",
    "On Hold",
    "Pending AVP",
    "Pending CEO",
    "Pending MD",
    "Pending Stores Manager",
    "Pending Department Head",
    "Pending Stock User",
    "Pending Production Head",
    "Pending Final",
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


# ════════════════════════════════════════════════════════════════════════
# CONFIG-LEVEL VALIDATORS (Sprint 2) — surfaced on Approval Flow tab
# ════════════════════════════════════════════════════════════════════════


# ───────────────────────────── Validator 5 ─────────────────────────────
def validate_orphan_cost_centers():
    """Cost Centers without a TS CC Approval Config.

    Active, non-group CCs that have NO row in `tabTS CC Approval Config`
    (or only inactive rows). MRs against these CCs may stall because no
    route mapping exists. Either disable the CC or assign an MR Route.
    """
    cached = _cache_get("orphan_cc")
    if cached is not None:
        return cached

    suppressed = _suppressed_ids()
    issues = []

    rows = frappe.db.sql(
        """SELECT cc.name AS cc_name
           FROM `tabCost Center` cc
           WHERE cc.disabled = 0 AND cc.is_group = 0
             AND NOT EXISTS (
               SELECT 1 FROM `tabTS CC Approval Config` tcac
               WHERE tcac.cost_center = cc.name AND tcac.is_active = 1
             )
           ORDER BY cc.name LIMIT %(limit)s""",
        {"limit": PER_VALIDATOR_LIMIT},
        as_dict=True,
    )

    for r in rows:
        iid = f"orphan_cc:{r.cc_name}"
        if iid in suppressed:
            continue
        issues.append({
            "issue_id": iid,
            "validator": "orphan_cost_centers",
            "severity": "high",
            "category": "configuration",
            "doc_type": "Cost Center",
            "doc_name": r.cc_name,
            "field": None,
            "current_value": "no_approval_config",
            "expected_value": "TS CC Approval Config row with mr_approval_route set",
            "message": (
                f"Cost Center '{r.cc_name}' has no active TS CC Approval Config. "
                f"MRs targeting this CC fall through to default route — risk of "
                f"stalling. Either disable the CC or assign an MR Route."
            ),
            "fix_action_id": None,
            "fix_params": None,
            "suppressible": True,
            "schema_version": SCHEMA_VERSION,
        })

    _cache_set("orphan_cc", issues)
    return issues


# ───────────────────────────── Validator 6 ─────────────────────────────
def validate_suspicious_routes():
    """MR Approval Routes with suspicious topology.

    Detects:
      - Single-step routes (advisory — may be intentional for small depts)
      - Step-order gaps (e.g. step_order 0,3 with 1,2 missing — high)
    """
    cached = _cache_get("suspicious_routes")
    if cached is not None:
        return cached

    suppressed = _suppressed_ids()
    issues = []

    routes = frappe.db.sql(
        """SELECT name, route_name, is_active FROM `tabTS MR Approval Route`
           ORDER BY name""",
        as_dict=True,
    )

    for route in routes:
        steps = frappe.db.sql(
            """SELECT step_order, role, action_type, can_revise, can_reject
               FROM `tabTS MR Approval Step`
               WHERE parent = %s
               ORDER BY step_order""",
            (route.name,),
            as_dict=True,
        )
        if not steps:
            continue
        orders = [s.step_order for s in steps]

        # 6a: single-step route
        if len(steps) == 1 and route.is_active:
            iid = f"suspicious_route:single_step:{route.name}"
            if iid not in suppressed:
                issues.append({
                    "issue_id": iid,
                    "validator": "suspicious_routes",
                    "severity": "low",
                    "category": "configuration",
                    "doc_type": "TS MR Approval Route",
                    "doc_name": route.name,
                    "field": "step_count",
                    "current_value": "1",
                    "expected_value": "≥2 (review + final approve)",
                    "message": (
                        f"Route '{route.name}' has only 1 approval step "
                        f"({steps[0].role} · {steps[0].action_type}). No reviewer "
                        f"tier — confirm intentional for this department."
                    ),
                    "fix_action_id": None,
                    "fix_params": None,
                    "suppressible": True,
                    "schema_version": SCHEMA_VERSION,
                })

        # 6b: step-order gap
        if len(orders) > 1:
            min_o, max_o = min(orders), max(orders)
            expected = set(range(min_o, max_o + 1))
            actual = set(orders)
            gaps = sorted(expected - actual)
            if gaps:
                iid = f"suspicious_route:gap:{route.name}"
                if iid not in suppressed:
                    issues.append({
                        "issue_id": iid,
                        "validator": "suspicious_routes",
                        "severity": "high",
                        "category": "configuration",
                        "doc_type": "TS MR Approval Route",
                        "doc_name": route.name,
                        "field": "step_order",
                        "current_value": ",".join(str(o) for o in sorted(orders)),
                        "expected_value": "contiguous step_order",
                        "message": (
                            f"Route '{route.name}' has step-order gaps "
                            f"(missing: {gaps}). Steps were likely deleted without "
                            f"renumbering. Re-sequence to contiguous integers."
                        ),
                        "fix_action_id": None,
                        "fix_params": None,
                        "suppressible": True,
                        "schema_version": SCHEMA_VERSION,
                    })

    _cache_set("suspicious_routes", issues)
    return issues


# ───────────────────────────── Validator 7 ─────────────────────────────
def validate_revise_flow_configuration():
    """Revise-flow misconfiguration audit.

    Surfaces:
      7a. Permission gaps — roles with can_revise=1 on any step but missing
          DocPerm cancel/amend (and write+submit for creator-tier roles)
      7b. Only-final-can-revise — routes/rules where ONLY the final step has
          can_revise=1 (advisory; mid-chain reviewers can't kick back)
      7c. Reject-vs-revise asymmetry — steps with can_revise=1 but can_reject=0

    Per user's "full list" caveat, NO allowlist pre-seed for CEO/MD perm gaps —
    they appear as critical findings and admin decides per row.
    """
    cached = _cache_get("revise_config")
    if cached is not None:
        return cached

    suppressed = _suppressed_ids()
    issues = []

    # Collect all (doctype, role) pairs that appear with can_revise=1
    revise_role_pairs = set()
    for s in frappe.db.sql(
        """SELECT role, can_revise FROM `tabTS MR Approval Step`""",
        as_dict=True,
    ):
        if s.can_revise and s.role:
            revise_role_pairs.add(("Material Request", s.role))
    for s in frappe.db.sql(
        """SELECT role, can_revise FROM `tabTS PO Approval Step`""",
        as_dict=True,
    ):
        if s.can_revise and s.role:
            revise_role_pairs.add(("Purchase Order", s.role))

    # 7a: Permission gaps
    for dt, role in sorted(revise_role_pairs):
        # Custom DocPerm overrides Standard (Lesson 169)
        custom = frappe.db.sql(
            """SELECT permlevel, `read`, `write`, `submit`, `cancel`, `amend`
               FROM `tabCustom DocPerm`
               WHERE parent = %s AND role = %s AND permlevel = 0""",
            (dt, role), as_dict=True,
        )
        std = frappe.db.sql(
            """SELECT permlevel, `read`, `write`, `submit`, `cancel`, `amend`
               FROM `tabDocPerm`
               WHERE parent = %s AND role = %s AND permlevel = 0""",
            (dt, role), as_dict=True,
        )
        rows = custom or std
        iid = f"revise_config:perm:{dt}:{role}"
        if iid in suppressed:
            continue

        if not rows:
            issues.append({
                "issue_id": iid,
                "validator": "revise_flow_configuration",
                "severity": "high",
                "category": "configuration",
                "doc_type": dt,
                "doc_name": role,
                "field": "DocPerm",
                "current_value": "no DocPerm row",
                "expected_value": "DocPerm with read+write+cancel+amend",
                "message": (
                    f"Role '{role}' has revise capability on {dt} steps but NO "
                    f"DocPerm row at permlevel=0. Users with this role can't read "
                    f"the doctype at all — entire flow blocked."
                ),
                "fix_action_id": None,
                "fix_params": None,
                "suppressible": True,
                "schema_version": SCHEMA_VERSION,
            })
            continue

        p = rows[0]
        missing = []
        for f in ("read", "write", "submit", "cancel", "amend"):
            if not p.get(f):
                missing.append(f)
        if missing:
            issues.append({
                "issue_id": iid,
                "validator": "revise_flow_configuration",
                "severity": "high",
                "category": "configuration",
                "doc_type": dt,
                "doc_name": role,
                "field": "DocPerm",
                "current_value": f"missing: {', '.join(missing)}",
                "expected_value": "all 5 perms (read+write+submit+cancel+amend)",
                "message": (
                    f"Role '{role}' has revise capability on {dt} but missing "
                    f"DocPerms: {', '.join(missing)}. Users hit silent button-hidden "
                    f"or 'no permission' errors on those actions."
                ),
                "fix_action_id": None,
                "fix_params": None,
                "suppressible": True,
                "schema_version": SCHEMA_VERSION,
            })

    # 7b + 7c: per-route audit
    for parent_dt, child_dt in (
        ("TS MR Approval Route", "TS MR Approval Step"),
        ("TS PO Approval Rule", "TS PO Approval Step"),
    ):
        # Only audit active configs
        active_field = "is_active"
        parents = frappe.db.sql(
            f"SELECT name FROM `tab{parent_dt}` WHERE {active_field} = 1 ORDER BY name",
            as_dict=True,
        )
        for p in parents:
            steps = frappe.db.sql(
                f"""SELECT step_order, role, action_type, can_revise, can_reject
                    FROM `tab{child_dt}` WHERE parent = %s ORDER BY step_order""",
                (p.name,),
                as_dict=True,
            )
            if not steps:
                continue

            revise_steps = [s for s in steps if s.can_revise]

            # 7b: only-final-can-revise (skip if route has only 1 step total)
            if len(steps) > 1 and len(revise_steps) == 1:
                final_step = max(steps, key=lambda s: s.step_order)
                if revise_steps[0].step_order == final_step.step_order:
                    iid = f"revise_config:only_final:{p.name}"
                    if iid not in suppressed:
                        issues.append({
                            "issue_id": iid,
                            "validator": "revise_flow_configuration",
                            "severity": "low",
                            "category": "configuration",
                            "doc_type": parent_dt,
                            "doc_name": p.name,
                            "field": "can_revise",
                            "current_value": "only final step",
                            "expected_value": "≥1 mid-chain step with can_revise=1",
                            "message": (
                                f"{parent_dt} '{p.name}' allows revise ONLY at the "
                                f"final step ({final_step.role}). Mid-chain reviewers "
                                f"can't kick back — must reject or rubber-stamp."
                            ),
                            "fix_action_id": None,
                            "fix_params": None,
                            "suppressible": True,
                            "schema_version": SCHEMA_VERSION,
                        })

            # 7c: revise-without-reject
            for s in revise_steps:
                if not s.can_reject:
                    iid = f"revise_config:no_reject:{p.name}:step_{s.step_order}"
                    if iid not in suppressed:
                        issues.append({
                            "issue_id": iid,
                            "validator": "revise_flow_configuration",
                            "severity": "medium",
                            "category": "configuration",
                            "doc_type": parent_dt,
                            "doc_name": p.name,
                            "field": "can_reject",
                            "current_value": "0",
                            "expected_value": "1 (always pair revise + reject)",
                            "message": (
                                f"Step {s.step_order} ({s.role}) on '{p.name}' "
                                f"allows revise but NOT reject. Forces revise even "
                                f"when rejection is the right action."
                            ),
                            "fix_action_id": None,
                            "fix_params": None,
                            "suppressible": True,
                            "schema_version": SCHEMA_VERSION,
                        })

    _cache_set("revise_config", issues)
    return issues


# ───────────────────────────── Aggregator ─────────────────────────────
def _is_kill_switch_on():
    """Master kill-switch — if ts_health_check_enabled=0, validators return empty."""
    try:
        v = frappe.db.get_single_value("TS Settings", "ts_health_check_enabled")
        return bool(int(v)) if v not in (None, "") else True
    except Exception:
        # Field may not exist yet (pre-migrate state). Default ON.
        return True


def run_all_validators():
    """Run all 7 validators (4 doc + 3 config) and return aggregated result.

    Sprint 1 had `run_doc_validators` which is now an alias for backward
    compat with the smoke tests. Sprint 2 adds `run_all_validators` as the
    canonical aggregator covering both layers.
    """
    import datetime

    all_issues = []
    validators_run = []

    # Master kill-switch (Sprint 2)
    if not _is_kill_switch_on():
        return {
            "schema_version": SCHEMA_VERSION,
            "feature_disabled": True,
            "stats": {"by_severity": {}, "by_category": {}, "total": 0},
            "issues": [],
            "validators_run": [],
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }

    for fn, label in (
        # Sprint 1 — doc layer
        (validate_state_corruption, "state_corruption"),
        (validate_revised_limbo, "revised_limbo"),
        (validate_cancel_no_amend, "cancel_no_amend"),
        (validate_stuck_sla_breach, "stuck_sla_breach"),
        # Sprint 2 — config layer
        (validate_orphan_cost_centers, "orphan_cost_centers"),
        (validate_suspicious_routes, "suspicious_routes"),
        (validate_revise_flow_configuration, "revise_flow_configuration"),
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
        "feature_disabled": False,
        "stats": {
            "by_severity": by_sev,
            "by_category": by_cat,
            "total": len(all_issues),
        },
        "issues": all_issues,
        "validators_run": validators_run,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


# Backward-compat alias used by Sprint 1 smoke tests.
def run_doc_validators():
    """Alias retained for Sprint 1 callers. Use run_all_validators() going forward."""
    return run_all_validators()
