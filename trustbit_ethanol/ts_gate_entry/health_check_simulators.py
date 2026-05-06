"""v2.9.12 Sprint 3 — Per-doc + per-flow capability simulators.

Two pure-function builders that answer:
  - PER-DOC: "On THIS doc, who can Open/Cancel/Amend/Submit/Approve/Revise + WHY?"
  - PER-FLOW: "On THIS route/rule, what can each step's role do?"

Both are read-only. Side-effect-free. Cached 60s.

Action schema per cell:
  {
    "allowed": True | False,
    "reason_code": "ok" | "blocked_status" | "blocked_perm" | "blocked_config"
                 | "requires_prior" | "not_applicable",
    "reason": "human-readable explanation",
  }
"""

import frappe


CACHE_NS = "health_check_sim_v1"
CACHE_TTL_SEC = 60


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


# Status fields per doctype that gate Submit-for-Approval predicate
SUBMIT_ELIGIBLE_STATUSES = {
    "Material Request": ("", "Draft", "Not Submitted"),
    "Purchase Order": ("", "Draft", "Not Submitted"),
}

DOCTYPE_STATUS_FIELD = {
    "Material Request": "ts_mr_status",
    "Purchase Order": "ts_approval_status",
}


def _has_perm(user, doctype, perm):
    """Check DocPerm for a specific user × doctype × ptype.
    Resolves via the user's roles + Custom DocPerm (overrides Standard per Lesson 169).
    Returns (bool allowed, reason).
    """
    user_roles = set(frappe.get_roles(user))
    custom = frappe.db.sql(
        f"""SELECT role, `{perm}` AS p
            FROM `tabCustom DocPerm`
            WHERE parent = %s AND permlevel = 0""",
        (doctype,), as_dict=True,
    )
    rows = custom or frappe.db.sql(
        f"""SELECT role, `{perm}` AS p
            FROM `tabDocPerm`
            WHERE parent = %s AND permlevel = 0""",
        (doctype,), as_dict=True,
    )
    for r in rows:
        if r.role in user_roles and r.p:
            return True, f"role '{r.role}' has {perm} on {doctype}"
    return False, f"no role with {perm} permission on {doctype}"


def _action_open(user, doc):
    ok, reason = _has_perm(user, doc.doctype, "read")
    return {
        "allowed": bool(ok),
        "reason_code": "ok" if ok else "blocked_perm",
        "reason": "User has read perm on doctype" if ok else f"Missing DocPerm.read — {reason}",
    }


def _action_submit_for_approval(user, doc):
    """Submit-for-Approval button visibility: docstatus=0 AND status in eligible list."""
    if doc.docstatus != 0:
        return {"allowed": False, "reason_code": "not_applicable",
                "reason": f"Doc is {'submitted' if doc.docstatus == 1 else 'cancelled'} (docstatus={doc.docstatus})"}
    status_field = DOCTYPE_STATUS_FIELD.get(doc.doctype)
    if not status_field:
        return {"allowed": False, "reason_code": "not_applicable", "reason": "No status field for doctype"}
    status = doc.get(status_field) or ""
    eligible = SUBMIT_ELIGIBLE_STATUSES.get(doc.doctype, ())
    if status not in eligible:
        return {
            "allowed": False, "reason_code": "blocked_status",
            "reason": f"{status_field}='{status}' not in eligible list {eligible}",
        }
    ok, reason = _has_perm(user, doc.doctype, "write")
    return {
        "allowed": bool(ok),
        "reason_code": "ok" if ok else "blocked_perm",
        "reason": ("Status eligible, user has write perm" if ok else f"Status eligible but missing write — {reason}"),
    }


def _action_approve(user, doc):
    """Approve action — user must be in current step's role chain.
    Simplified for Sprint 3: check if user has any role that appears in the
    doc's approval chain steps (handles higher-level override naturally).
    """
    if doc.docstatus != 1:
        return {"allowed": False, "reason_code": "not_applicable",
                "reason": "Approve is only meaningful on submitted docs"}
    status_field = DOCTYPE_STATUS_FIELD.get(doc.doctype)
    status = doc.get(status_field) if status_field else None
    if not status or not status.startswith("Pending "):
        return {"allowed": False, "reason_code": "not_applicable",
                "reason": f"Status '{status}' is not a pending state"}
    user_roles = set(frappe.get_roles(user))
    target_role = status.replace("Pending ", "").strip()
    role_aliases = {"Dept. Head": "Department Head", "Dept. User": "Stock User",
                    "Final": "AVP", "GM": "General Manager"}
    target_role_actual = role_aliases.get(target_role, target_role)
    if target_role_actual in user_roles:
        return {"allowed": True, "reason_code": "ok",
                "reason": f"User has '{target_role_actual}' role, matches current step"}
    # Higher-level override check
    higher_chain = ["Department Head", "AVP", "General Manager", "CEO", "MD"]
    if target_role_actual in higher_chain:
        idx = higher_chain.index(target_role_actual)
        higher_roles = set(higher_chain[idx + 1:])
        if higher_roles & user_roles:
            return {"allowed": True, "reason_code": "ok",
                    "reason": f"User has higher-level role(s) {sorted(higher_roles & user_roles)}"}
    return {"allowed": False, "reason_code": "blocked_perm",
            "reason": f"User lacks '{target_role_actual}' role and no higher-level override"}


def _action_request_revision(user, doc):
    if doc.docstatus != 1:
        return {"allowed": False, "reason_code": "not_applicable",
                "reason": "Request Revision only on submitted docs"}
    status_field = DOCTYPE_STATUS_FIELD.get(doc.doctype)
    status = doc.get(status_field) if status_field else None
    if status == "Revised":
        return {"allowed": False, "reason_code": "blocked_status",
                "reason": "Already in Revised state"}
    if status != "Approved":
        return {"allowed": False, "reason_code": "blocked_status",
                "reason": "Request Revision typically requires Approved status"}
    # Need can_revise on current step + role match
    return {"allowed": True, "reason_code": "ok", "reason": "Approved doc, role can_revise check at step level"}


def _action_reject(user, doc):
    if doc.docstatus != 1:
        return {"allowed": False, "reason_code": "not_applicable",
                "reason": "Reject only on submitted docs"}
    status_field = DOCTYPE_STATUS_FIELD.get(doc.doctype)
    status = doc.get(status_field) if status_field else None
    if not (status or "").startswith("Pending "):
        return {"allowed": False, "reason_code": "not_applicable",
                "reason": "Reject only at a Pending step"}
    return _action_approve(user, doc)  # same role check


def _action_cancel(user, doc):
    if doc.docstatus != 1:
        return {"allowed": False, "reason_code": "not_applicable",
                "reason": f"Cancel only on submitted docs (docstatus=1), this is {doc.docstatus}"}
    ok, reason = _has_perm(user, doc.doctype, "cancel")
    return {
        "allowed": bool(ok),
        "reason_code": "ok" if ok else "blocked_perm",
        "reason": ("User has cancel perm" if ok else f"Missing cancel perm — {reason}"),
    }


def _action_amend(user, doc):
    if doc.docstatus != 2:
        return {"allowed": False, "reason_code": "requires_prior",
                "reason": f"Amend requires Cancel first (docstatus=2). Current docstatus={doc.docstatus}"}
    ok, reason = _has_perm(user, doc.doctype, "amend")
    return {
        "allowed": bool(ok),
        "reason_code": "ok" if ok else "blocked_perm",
        "reason": ("User has amend perm" if ok else f"Missing amend perm — {reason}"),
    }


ALL_ACTIONS = (
    ("open", _action_open),
    ("submit_for_approval", _action_submit_for_approval),
    ("approve", _action_approve),
    ("request_revision", _action_request_revision),
    ("reject", _action_reject),
    ("cancel", _action_cancel),
    ("amend", _action_amend),
)


def _users_in_chain_for_doc(doc):
    """Build the user list relevant to this doc's capability matrix.
    Returns a list of dicts: {user, kind, roles_summary}
    """
    out = []
    # Creator
    if doc.owner:
        out.append({
            "user": doc.owner,
            "kind": "Creator",
            "roles": list(frappe.get_roles(doc.owner)),
        })

    # Approvers — pick first user from each critical role
    for role, label in (
        ("Department Head", "Step Reviewer · Dept Head"),
        ("AVP", "Final Approver · AVP"),
        ("CEO", "Higher Override · CEO"),
    ):
        users = frappe.db.sql(
            """SELECT DISTINCT hr.parent FROM `tabHas Role` hr
               JOIN `tabUser` u ON u.name = hr.parent
               WHERE hr.role = %s AND u.enabled = 1
                 AND u.name NOT IN ('Administrator', 'Guest')
               ORDER BY hr.parent LIMIT 1""",
            (role,),
        )
        if users and users[0][0] != doc.owner:
            uname = users[0][0]
            out.append({
                "user": uname,
                "kind": label,
                "roles": list(frappe.get_roles(uname)),
            })

    # System Manager override
    sm_users = frappe.db.sql(
        """SELECT DISTINCT hr.parent FROM `tabHas Role` hr
           JOIN `tabUser` u ON u.name = hr.parent
           WHERE hr.role = 'System Manager' AND u.enabled = 1
             AND u.name NOT IN ('Administrator', 'Guest')
           ORDER BY hr.parent LIMIT 1""",
    )
    if sm_users and not any(u["user"] == sm_users[0][0] for u in out):
        out.append({
            "user": sm_users[0][0],
            "kind": "Override · System Manager",
            "roles": list(frappe.get_roles(sm_users[0][0])),
        })

    return out


def build_doc_capability_matrix(doctype, name):
    """Build the per-doc capability matrix.

    Returns:
        {
            "doc": {doctype, name, status, docstatus, owner, ...},
            "users": [{user, kind, roles, actions: {action: {allowed, reason}}}, ...],
            "verdict": "human-readable summary"
        }
    """
    cache_key = f"doc_cap:{doctype}:{name}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if doctype not in DOCTYPE_STATUS_FIELD:
        frappe.throw(f"Capability matrix not supported for doctype '{doctype}'.")

    if not frappe.db.exists(doctype, name):
        frappe.throw(f"{doctype} '{name}' does not exist.")

    doc = frappe.get_doc(doctype, name)
    status_field = DOCTYPE_STATUS_FIELD[doctype]

    users = _users_in_chain_for_doc(doc)
    out_users = []
    for u in users:
        actions = {}
        for action_name, action_fn in ALL_ACTIONS:
            try:
                actions[action_name] = action_fn(u["user"], doc)
            except Exception as e:
                actions[action_name] = {"allowed": False, "reason_code": "error",
                                        "reason": str(e)[:120]}
        out_users.append({
            "user": u["user"],
            "kind": u["kind"],
            "roles": u["roles"],
            "actions": actions,
        })

    # Verdict — what user MUST do to unblock
    status = doc.get(status_field) or ""
    verdict_parts = []
    if doc.docstatus == 0 and status not in ("", "Not Submitted", "Draft"):
        verdict_parts.append(
            f"Draft has stale status '{status}' — Submit-for-Approval hidden. "
            f"Cancel + Amend + Submit OR System Manager runs ack/reset."
        )
    elif doc.docstatus == 1 and status == "Revised":
        verdict_parts.append(
            "Doc in Revised limbo — creator must Cancel + Amend + Submit."
        )
    elif doc.docstatus == 1 and status.startswith("Pending "):
        target = status.replace("Pending ", "")
        verdict_parts.append(f"Awaiting {target} action — Approve / Reject / Request Revision.")
    elif doc.docstatus == 2:
        verdict_parts.append("Cancelled — Amend to recreate as new draft.")
    else:
        verdict_parts.append("No action required.")

    result = {
        "doc": {
            "doctype": doctype,
            "name": name,
            "docstatus": doc.docstatus,
            "status": status,
            "owner": doc.owner,
            "amended_from": doc.get("amended_from"),
        },
        "users": out_users,
        "verdict": " ".join(verdict_parts),
        "schema_version": 1,
    }
    _cache_set(cache_key, result)
    return result


def build_flow_capability_simulator(flow_type, flow_name):
    """Build the per-flow capability simulator for a route or rule.

    flow_type: "MR Route" | "PO Rule"

    Returns:
        {
            "flow": {type, name, step_count, ...},
            "steps": [{step_number, role, user_count, actions, health, notes}, ...],
            "verdict": "human-readable summary"
        }
    """
    cache_key = f"flow_cap:{flow_type}:{flow_name}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if flow_type == "MR Route":
        parent_dt, child_dt = "TS MR Approval Route", "TS MR Approval Step"
        target_doctype = "Material Request"
    elif flow_type == "PO Rule":
        parent_dt, child_dt = "TS PO Approval Rule", "TS PO Approval Step"
        target_doctype = "Purchase Order"
    else:
        frappe.throw(f"Unknown flow_type '{flow_type}'. Use 'MR Route' or 'PO Rule'.")

    if not frappe.db.exists(parent_dt, flow_name):
        frappe.throw(f"{parent_dt} '{flow_name}' not found.")

    steps_raw = frappe.db.sql(
        f"""SELECT step_order, role, role_label, action_type, can_revise, can_reject
            FROM `tab{child_dt}` WHERE parent = %s
            ORDER BY step_order""",
        (flow_name,), as_dict=True,
    )

    out_steps = []

    # Synthetic Creator step
    out_steps.append({
        "step_number": "Creator",
        "role": "Stock User" if flow_type == "MR Route" else "Purchase User",
        "user_count": _role_user_count("Stock User" if flow_type == "MR Route" else "Purchase User"),
        "label": "Doc creator",
        "can_revise": False,
        "can_reject": False,
        "perm_summary": _role_perm_summary(
            "Stock User" if flow_type == "MR Route" else "Purchase User",
            target_doctype,
        ),
        "health": "ok",
        "notes": "Creates the doc + Submit for Approval",
    })

    # Real chain steps
    for s in steps_raw:
        if not s.role:
            continue
        users_in_role = _role_user_count(s.role)
        perms = _role_perm_summary(s.role, target_doctype)
        health = "ok"
        notes = []

        if users_in_role == 0:
            health = "err"
            notes.append("0 users with this role — STEP STUCK")
        if perms.get("missing"):
            health = "err" if health != "err" else health
            notes.append(f"Missing DocPerms: {', '.join(perms['missing'])}")
        if not s.can_revise:
            if health == "ok":
                health = "warn"
            notes.append("can_revise=0 (cannot kick back to creator)")
        if s.can_revise and not s.can_reject:
            if health == "ok":
                health = "warn"
            notes.append("can_revise=1 but can_reject=0 (asymmetric)")

        out_steps.append({
            "step_number": s.step_order,
            "role": s.role,
            "label": s.role_label or f"Step {s.step_order} · {s.action_type}",
            "user_count": users_in_role,
            "can_revise": bool(s.can_revise),
            "can_reject": bool(s.can_reject),
            "perm_summary": perms,
            "health": health,
            "notes": "; ".join(notes) if notes else "Healthy",
            "action_type": s.action_type,
        })

    # Synthetic override row
    out_steps.append({
        "step_number": "Override",
        "role": "System Manager",
        "user_count": _role_user_count("System Manager"),
        "label": "Override · rescue path",
        "can_revise": True,
        "can_reject": True,
        "perm_summary": _role_perm_summary("System Manager", target_doctype),
        "health": "ok",
        "notes": "Full override — can rescue stuck docs",
    })

    # Flow-level verdict
    err_steps = [s for s in out_steps if s.get("health") == "err"]
    warn_steps = [s for s in out_steps if s.get("health") == "warn"]
    verdict_lines = []
    if err_steps:
        verdict_lines.append(
            f"❌ {len(err_steps)} step(s) BROKEN: " +
            ", ".join(f"{s['role']} ({s['notes'][:60]})" for s in err_steps)
        )
    if warn_steps:
        verdict_lines.append(
            f"⚠ {len(warn_steps)} step(s) WARNING: " +
            ", ".join(f"{s['role']}" for s in warn_steps)
        )
    if not err_steps and not warn_steps:
        verdict_lines.append("✓ Flow healthy — every step has working approvers + recovery path.")

    result = {
        "flow": {
            "type": flow_type,
            "name": flow_name,
            "step_count": len(steps_raw),
        },
        "steps": out_steps,
        "verdict": " · ".join(verdict_lines),
        "schema_version": 1,
    }
    _cache_set(cache_key, result)
    return result


def _role_user_count(role):
    if not role:
        return 0
    n = frappe.db.sql(
        """SELECT COUNT(DISTINCT hr.parent) FROM `tabHas Role` hr
           JOIN `tabUser` u ON u.name = hr.parent
           WHERE hr.role = %s AND u.enabled = 1
             AND u.name NOT IN ('Administrator', 'Guest')""",
        (role,),
    )
    return n[0][0] if n else 0


def _role_perm_summary(role, doctype):
    """Return {present: [], missing: [], has_row: bool} for a role on a doctype."""
    custom = frappe.db.sql(
        """SELECT `read`, `write`, `submit`, `cancel`, `amend`
           FROM `tabCustom DocPerm`
           WHERE parent = %s AND role = %s AND permlevel = 0""",
        (doctype, role), as_dict=True,
    )
    std = frappe.db.sql(
        """SELECT `read`, `write`, `submit`, `cancel`, `amend`
           FROM `tabDocPerm`
           WHERE parent = %s AND role = %s AND permlevel = 0""",
        (doctype, role), as_dict=True,
    )
    rows = custom or std
    if not rows:
        return {"present": [], "missing": ["read", "write", "submit", "cancel", "amend"],
                "has_row": False}
    p = rows[0]
    present = [k for k in ("read", "write", "submit", "cancel", "amend") if p.get(k)]
    missing = [k for k in ("read", "write", "submit", "cancel", "amend") if not p.get(k)]
    return {"present": present, "missing": missing, "has_row": True}
