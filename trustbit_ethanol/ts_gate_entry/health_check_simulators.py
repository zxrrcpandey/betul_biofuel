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

# v2.9.12.1 — System Manager + Administrator have implicit role-grants in
# Frappe (full access bypasses DocPerm row checks). Treat them as "all
# permissions present" so the per-flow simulator's Override card doesn't
# show false-positive missing-perm warnings.
IMPLICIT_GRANT_ROLES = ("System Manager", "Administrator")
ALL_PERMS = ("read", "write", "submit", "cancel", "amend")


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
    # v2.9.12.1 — System Manager + Administrator get implicit grants
    sm_match = next((r for r in IMPLICIT_GRANT_ROLES if r in user_roles), None)
    if sm_match:
        return True, f"User has '{sm_match}' role (implicit grant — bypasses DocPerm)"
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


def _caller_can_see_override():
    """v2.9.12.2 — only show System Manager Override row to users who actually
    hold SM/Admin roles. Other ALLOWED_ROLES (CEO/MD/AVP/etc.) see the chain
    without the Override row since they can't use that rescue path anyway.
    """
    try:
        caller_roles = set(frappe.get_roles(frappe.session.user))
        return bool(caller_roles & set(IMPLICIT_GRANT_ROLES))
    except Exception:
        return False


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

    # System Manager override — v2.9.12.2: gated to caller-has-SM-role
    if _caller_can_see_override():
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


def _build_revise_flow_detail(doc):
    """v2.9.12.3 — for the picked doc, return the revise-flow journey:
    who can press Request Revision NOW, who gets notified, and what
    happens next.

    Three blocks:
      - actors: list of {user, role, basis} who can currently revise
      - notifications: list of {user, kind} who gets notified on revise
      - next_actions: list of {actor, action, description} sequence
    """
    status_field = DOCTYPE_STATUS_FIELD.get(doc.doctype)
    status = (doc.get(status_field) if status_field else "") or ""
    detail = {
        "actors": [],
        "notifications": [],
        "next_actions": [],
        "applicable": False,
        "summary": "",
        # v2.9.12.5 — show resubmitters in every state (incl Revised limbo)
        "resubmitters": _resubmit_capable_roles(doc.doctype),
    }

    if doc.docstatus != 1:
        detail["summary"] = (
            f"Revise flow not active — doc is "
            f"{'draft' if doc.docstatus == 0 else 'cancelled'} (docstatus={doc.docstatus})."
        )
        return detail

    if status == "Revised":
        detail["summary"] = (
            "Already in 'Revised' status — revision already requested. "
            "Creator must Cancel + Amend + Resubmit to progress. No further "
            "Revise action possible at this stage."
        )
        # Still show notification + next-actions for context
        if doc.owner:
            detail["notifications"].append({
                "user": doc.owner, "kind": "Creator (notified when revision was requested)",
            })
        detail["next_actions"] = [
            {"actor": "Creator (" + (doc.owner or "—") + ")", "action": "Cancel",
             "description": "Cancel the submitted doc → docstatus moves to 2"},
            {"actor": "Creator", "action": "Amend",
             "description": "Frappe creates new draft <name>-1 with ts_mr_status reset to 'Not Submitted' (v2.9.12 mr_on_amend hook)"},
            {"actor": "Creator", "action": "Submit for Approval",
             "description": "New draft re-enters approval chain at Step 1 with fresh self-skip evaluation"},
        ]
        return detail

    # Determine who can revise NOW based on current step + can_revise flag
    is_approved = (status == "Approved")
    is_pending = status.startswith("Pending ")

    if not (is_approved or is_pending):
        detail["summary"] = f"Revise flow not applicable from status '{status}'."
        return detail

    detail["applicable"] = True

    if is_pending:
        # Step-based revise — find users with the current step's role + can_revise=1
        target_role_label = status.replace("Pending ", "").strip()
        role_aliases = {"Dept. Head": "Department Head", "Dept. User": "Stock User", "GM": "General Manager"}
        target_role = role_aliases.get(target_role_label, target_role_label)

        # Find any TS MR/PO Approval Step with this role + can_revise=1
        child_dt = "TS MR Approval Step" if doc.doctype == "Material Request" else "TS PO Approval Step"
        revise_steps = frappe.db.sql(
            f"""SELECT DISTINCT role FROM `tab{child_dt}`
                WHERE role = %s AND can_revise = 1""",
            (target_role,), as_list=True,
        )
        can_revise = bool(revise_steps)

        if can_revise:
            users = frappe.db.sql(
                """SELECT DISTINCT hr.parent FROM `tabHas Role` hr
                   JOIN `tabUser` u ON u.name = hr.parent
                   WHERE hr.role = %s AND u.enabled = 1
                     AND u.name NOT IN ('Administrator', 'Guest')
                   ORDER BY hr.parent LIMIT 5""",
                (target_role,),
            )
            for (uname,) in users:
                detail["actors"].append({
                    "user": uname, "role": target_role,
                    "basis": f"Has '{target_role}' role + step has can_revise=1",
                })
            if not users:
                detail["actors"].append({
                    "user": "—", "role": target_role,
                    "basis": f"⚠ Step has can_revise=1 but 0 users with '{target_role}' role — STUCK",
                })
        else:
            detail["actors"].append({
                "user": "—", "role": target_role,
                "basis": f"❌ Step role '{target_role}' has can_revise=0 in route config — Revise button HIDDEN at this step. Only higher-level roles or System Manager can revise.",
            })

        detail["summary"] = (
            f"Doc is currently at '{status}'. {'✓' if can_revise else '❌'} "
            f"{target_role} role {'CAN' if can_revise else 'CANNOT'} request revision at this step."
        )

    elif is_approved:
        # Post-approval revision — typically restricted to specific roles
        # (per ts_mr_post_approval_revision.py: Purchase User / Purchase Manager / IT Head)
        post_approval_roles = ["Purchase User", "Purchase Manager", "IT Head"]
        for role in post_approval_roles:
            n = _role_user_count(role)
            if n > 0:
                users = frappe.db.sql(
                    """SELECT DISTINCT hr.parent FROM `tabHas Role` hr
                       JOIN `tabUser` u ON u.name = hr.parent
                       WHERE hr.role = %s AND u.enabled = 1
                         AND u.name NOT IN ('Administrator', 'Guest')
                       ORDER BY hr.parent LIMIT 2""",
                    (role,),
                )
                for (uname,) in users:
                    detail["actors"].append({
                        "user": uname, "role": role,
                        "basis": f"Post-approval revise allowed for '{role}' role",
                    })
        detail["summary"] = (
            "Doc is Approved. Post-approval revision via 'Request Revision' button on the doc form. "
            "Soft-revise: status → 'Revised', docstatus stays 1. Creator must Cancel + Amend to actually edit."
        )

    # Notifications — creator always
    if doc.owner:
        detail["notifications"].append({
            "user": doc.owner, "kind": "Creator (always notified)",
        })

    # Next actions for the creator
    detail["next_actions"] = [
        {"actor": f"Creator ({doc.owner or '—'})", "action": "Cancel",
         "description": "Cancel the submitted doc (docstatus 1 → 2)"},
        {"actor": "Creator", "action": "Amend",
         "description": "Frappe creates new draft <name>-1 with ts_mr_status auto-reset to 'Not Submitted' (v2.9.12 mr_on_amend hook ensures this)"},
        {"actor": "Creator", "action": "Edit + Submit for Approval",
         "description": "Edit the new draft as needed, then click Submit for Approval. Doc re-enters approval chain at Step 1 with fresh self-skip evaluation."},
    ]

    return detail


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
        "revise_flow": _build_revise_flow_detail(doc),  # v2.9.12.3
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

    # v2.9.12.4 — Override card REMOVED from per-flow output entirely. Visual
    # noise per user feedback. SM rescue path is mentioned in Verdict text when
    # applicable (computed below).

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

    # v2.9.12.4 — route-level Revise Flow Summary
    revise_summary = _build_revise_summary_for_flow(target_doctype, steps_raw)

    result = {
        "flow": {
            "type": flow_type,
            "name": flow_name,
            "step_count": len(steps_raw),
        },
        "steps": out_steps,
        "revise_summary": revise_summary,
        "verdict": " · ".join(verdict_lines),
        "schema_version": 1,
    }
    _cache_set(cache_key, result)
    return result


def _resubmit_capable_roles(target_doctype):
    """v2.9.12.5 — list roles with DocPerm.submit on target_doctype + user counts.

    These are the roles whose users will see the Submit-for-Approval button
    on the amended draft (after Cancel + Amend). Per Lesson 169, Custom
    DocPerm overrides Standard if any Custom row exists for the doctype.
    """
    custom = frappe.db.sql(
        """SELECT role FROM `tabCustom DocPerm`
           WHERE parent = %s AND permlevel = 0 AND `submit` = 1""",
        (target_doctype,), as_list=True,
    )
    if custom:
        rows = custom
    else:
        rows = frappe.db.sql(
            """SELECT role FROM `tabDocPerm`
               WHERE parent = %s AND permlevel = 0 AND `submit` = 1""",
            (target_doctype,), as_list=True,
        )
    out = []
    seen = set()
    for (role,) in rows:
        if not role or role in seen:
            continue
        seen.add(role)
        n = _role_user_count(role)
        out.append({"role": role, "user_count": n,
                    "verdict": (f"✓ {n} active user(s) can re-submit" if n > 0
                                else "⚠ Role has 0 active users — re-submit impossible from this role")})
    # sort by user_count desc so most-impactful roles first
    out.sort(key=lambda r: -r["user_count"])
    return out


def _build_revise_summary_for_flow(target_doctype, steps_raw):
    """v2.9.12.4 — per-route revise capability summary.

    For each step shows: role, can_revise flag, can_reject flag, eligible
    user count. Plus a top-level 'who can revise this route' summary.
    """
    rows = []
    revise_capable_roles = []
    for s in steps_raw:
        if not s.role:
            continue
        n = _role_user_count(s.role)
        rows.append({
            "step_order": s.step_order,
            "role": s.role,
            "action_type": s.action_type or "Review",
            "can_revise": bool(s.can_revise),
            "can_reject": bool(s.can_reject),
            "user_count": n,
            "verdict": _step_revise_verdict(s, n),
        })
        if s.can_revise and n > 0:
            revise_capable_roles.append(s.role)

    if revise_capable_roles:
        summary = (
            f"On this route, the following role(s) can press 'Request Revision' "
            f"after their step: {', '.join(revise_capable_roles)}. "
            f"When pressed, status moves to 'Revised', creator gets notified, "
            f"creator must Cancel + Amend + Resubmit."
        )
    else:
        summary = (
            "❌ No step on this route has can_revise=1. Users can't kick back "
            "for revision via the standard chain — only System Manager (override) "
            "can move stuck docs."
        )

    # v2.9.12.5 — also include who can re-submit the amended draft
    resubmitters = _resubmit_capable_roles(target_doctype)

    return {"summary": summary, "rows": rows, "resubmitters": resubmitters}


def _step_revise_verdict(step, user_count):
    if not step.role:
        return "—"
    if step.can_revise and user_count > 0:
        return f"✓ {user_count} user(s) with '{step.role}' role can revise + notify creator"
    if step.can_revise and user_count == 0:
        return f"⚠ Step has can_revise=1 but 0 users with '{step.role}' role"
    if not step.can_revise:
        return f"✗ Step has can_revise=0 — Revise button hidden for '{step.role}' role"
    return "—"


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
    # v2.9.12.1 — System Manager + Administrator have implicit role-grants;
    # no DocPerm rows required. Show them as fully-permitted in the simulator.
    if role in IMPLICIT_GRANT_ROLES:
        return {"present": list(ALL_PERMS), "missing": [], "has_row": True, "implicit": True}
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
