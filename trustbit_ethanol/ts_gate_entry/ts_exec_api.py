# Copyright (c) 2026, Trustbit Software and contributors
# BBPL Approvals executive PWA (v2.31.0) — read API for the /exec frontend.
#
# This module is a THIN LENS over the existing approval engine:
#  - queue vocabulary is IMPORTED from ts_my_approvals_api.ROLE_STATUS_MAP
#    (single source of truth — never restated here, v2.9.5 lesson)
#  - every list read is permission-checked (ignore_permissions=False) so the
#    confidential-PO PQC (ts_confidential_po) and User Permissions apply to
#    the app exactly as they do to the desk — NO raw SQL in this module, ever
#  - it exposes NO mutation: the SPA calls the existing workflow endpoints
#    (ts_po_approval / ts_budget_override / ts_post_dated / ts_avp_deputy)
#
# Kill switch: TS Settings.ts_exec_pwa_enabled (Custom Field seeded by
# setup_exec_pwa.py). Fail-OPEN per L171/L172 — only an explicit stored 0
# disables; reads tabSingles directly because get_single_value casts a
# missing Check field to 0, which would silently disable the app.

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint

from trustbit_ethanol.ts_gate_entry.ts_my_approvals_api import (
    ROLE_STATUS_MAP,  # REUSE — the one authoritative role → status vocabulary
    _get_user_statuses,
)

KILL_SWITCH_FIELD = "ts_exec_pwa_enabled"
SW_FLAG_FIELD = "ts_exec_pwa_sw_enabled"

# The ONE queue the shared map cannot express: budget overrides live on
# TS Budget Override Approval (submittable, status "Pending CEO") — a
# DIFFERENT doctype from the TS Budget Proposal entry in ROLE_STATUS_MAP.
# Both CEO and MD may act (ts_budget_override.APPROVER_ROLES = CEO/MD/System
# Manager), so both see the queue. Kept local so the desk's workspace tiles
# (which consume ROLE_STATUS_MAP) are not changed by this feature.
_EXTRA_QUEUES = {
    "TS Budget Override Approval": {
        "field": "status",
        "roles": {"CEO": ["Pending CEO"], "MD": ["Pending CEO"]},
        "extra_filters": {"docstatus": 1},
    },
}

# The 4 executive queues the app surfaces (subset of ROLE_STATUS_MAP + extra).
_APP_DOCTYPES = (
    "Purchase Order",
    "Material Request",
    "TS Post Dated Entry Request",
    "TS Budget Override Approval",
)

_LIST_FIELDS = {
    "Purchase Order": [
        "name", "supplier", "supplier_name", "grand_total", "currency",
        "transaction_date", "ts_approval_status", "ts_po_on_hold",
        "ts_purchase_category", "ts_submitted_by", "modified",
    ],
    "Material Request": [
        "name", "material_request_type", "transaction_date", "schedule_date",
        "ts_mr_status", "ts_mr_submitted_by", "modified",
    ],
    "TS Post Dated Entry Request": [
        "name", "request_type", "status", "requested_by", "request_date",
        "reason", "from_date", "to_date", "modified",
    ],
    "TS Budget Override Approval": [
        "name", "title", "reference_doctype", "reference_name",
        "source_amount", "cost_center", "breach_type", "submitted_by",
        "submitted_at", "submission_reason", "status", "modified",
    ],
}


def flag_on(field):
    """Fail-OPEN Singles read (L171/L172): only an explicit stored 0 disables.
    Shared by www/exec.py and ts_exec_login_alert.py."""
    try:
        rows = frappe.db.sql(
            """SELECT value FROM `tabSingles`
               WHERE doctype = 'TS Settings' AND field = %s LIMIT 1""",
            (field,),
        )
    except Exception:
        return True
    if not rows or rows[0][0] is None:
        return True
    return bool(cint(rows[0][0]))


def _check_session():
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Not permitted."), frappe.PermissionError)


def _guard():
    _check_session()
    if not flag_on(KILL_SWITCH_FIELD):
        frappe.throw(_("BBPL Approvals is temporarily disabled."))


def _status_field(doctype):
    if doctype in ROLE_STATUS_MAP:
        return ROLE_STATUS_MAP[doctype]["field"]
    return _EXTRA_QUEUES[doctype]["field"]


def _extra_queue_statuses(doctype, user):
    cfg = _EXTRA_QUEUES[doctype]
    user_roles = set(frappe.get_roles(user))
    statuses = set()
    for role, role_statuses in cfg["roles"].items():
        if role in user_roles:
            statuses.update(role_statuses)
    return sorted(statuses)


def _mr_estimated_values(names):
    """SUM(items.amount) per MR (L160 — MR has no grand_total). The parent
    list is already permission-checked, so a child read scoped to exactly
    those vetted parents may skip the (parent-less) child permission model."""
    if not names:
        return {}
    rows = frappe.get_all(
        "Material Request Item",
        filters={"parent": ["in", names], "parenttype": "Material Request"},
        fields=["parent", "sum(amount) as total"],
        group_by="parent",
        ignore_permissions=True,
    )
    return {r.parent: float(r.total or 0) for r in rows}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_inbox():
    """Unified pending-approvals list for the session user, oldest first.

    Role-driven via ROLE_STATUS_MAP: a user with no approver role gets an
    empty inbox. CEO additionally sees POs at 'Awaiting Send to …' via the
    tamper-guarded ts_can_send_to_md flag (set at exactly one site in
    ts_po_approval.py:1101, cleared at every exit path) — that state is in
    NOBODY's status queue although only the CEO can act on it.
    """
    _guard()
    user = frappe.session.user
    is_ceo = "CEO" in frappe.get_roles(user)

    items = []
    queues = []
    for doctype in _APP_DOCTYPES:
        if doctype in ROLE_STATUS_MAP:
            statuses = _get_user_statuses(doctype, user)
        else:
            statuses = _extra_queue_statuses(doctype, user)

        field = _status_field(doctype)
        filters = {}
        or_filters = None

        if doctype in ("Purchase Order", "Material Request"):
            # Pending states only exist on drafts; Approved submits the doc.
            filters["docstatus"] = 0
        if doctype in _EXTRA_QUEUES:
            filters.update(_EXTRA_QUEUES[doctype].get("extra_filters") or {})

        if doctype == "Purchase Order" and is_ceo:
            or_filters = [
                [field, "in", statuses or [""]],
                ["ts_can_send_to_md", "=", 1],
            ]
        elif statuses:
            filters[field] = ["in", statuses]
        else:
            continue  # no role on this queue → skip entirely

        try:
            rows = frappe.get_list(
                doctype,
                filters=filters,
                or_filters=or_filters,
                fields=_LIST_FIELDS[doctype],
                order_by="modified asc",
                # 200, not 50: the chip counts read len(rows), so the cap must
                # sit far above any realistic executive queue or the counts lie
                # (ui-designer note B; demo max observed queue = 38).
                limit_page_length=200,
                ignore_permissions=False,  # PQC + User Permissions apply
            )
        except Exception:
            # Fail-closed (empty queue) but NEVER silently: a permission
            # misconfiguration must not present as "nothing pending".
            frappe.log_error(
                title="exec inbox queue failed",
                message="doctype={0} user={1}\n{2}".format(
                    doctype, user, frappe.get_traceback()
                ),
            )
            frappe.clear_messages()
            rows = []

        mr_values = (
            _mr_estimated_values([r.name for r in rows])
            if doctype == "Material Request"
            else {}
        )
        for r in rows:
            card = dict(r)
            card["doctype"] = doctype
            card["status"] = r.get(field)
            if doctype == "Material Request":
                card["estimated_value"] = mr_values.get(r.name)
            items.append(card)
        queues.append({
            "doctype": doctype,
            "count": len(rows),
            "statuses": statuses,
        })

    items.sort(key=lambda d: str(d.get("modified") or ""))
    return {
        "enabled": True,
        "user": user,
        "queues": queues,
        "items": items,
        "total": len(items),
    }


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_my_actions():
    """Self-scoped approval history for the History tab: the session user's
    OWN TS Approval Log rows (PO/MR actions they performed). Hard-filtered by
    action_by = session user — no parameter accepts another user, so this can
    never read someone else's trail. ignore_permissions=True is safe under
    that self-scoping: every row IS the user's own past action (they were a
    participant on the document when they acted); tapping through to the
    document still goes via get_document, which permission-fences."""
    _guard()
    user = frappe.session.user
    rows = frappe.get_all(
        "TS Approval Log",
        filters={
            "action_by": user,
            "parenttype": ["in", ["Purchase Order", "Material Request"]],
        },
        fields=[
            "parent", "parenttype", "action", "from_state", "to_state",
            "action_date", "comment", "po_amount",
        ],
        order_by="action_date desc",
        limit_page_length=50,
        ignore_permissions=True,
    )
    return {
        "user": user,
        "items": [
            {
                "doctype": r.parenttype,
                "name": r.parent,
                "action": r.action,
                "from_state": r.from_state,
                "to_state": r.to_state,
                "action_date": str(r.action_date or ""),
                "comment": r.comment,
                "amount": r.po_amount,
            }
            for r in rows
        ],
    }


def _budget_override_context(doc, user):
    roles = set(frappe.get_roles(user))
    actionable = (
        doc.docstatus == 1
        and (doc.status or "") == "Pending CEO"
        and bool(roles & {"CEO", "MD", "System Manager"})
    )
    return {
        "enabled": True,
        "can_approve": actionable,
        "can_reject": actionable,
        "is_on_hold": False,
        "approval_chain": [],
        "reason_required_for_reject": True,
        "reject_reason_min_length": 10,
    }


def _post_dated_context(doc, user):
    roles = set(frappe.get_roles(user))
    is_approver = bool(roles & {"CEO", "MD"})
    not_self = (doc.requested_by or doc.owner) != user
    actionable = (doc.status or "") == "Pending Approval" and is_approver and not_self
    return {
        "enabled": True,
        "can_approve": actionable,
        "can_reject": actionable,
        "is_on_hold": False,
        "approval_chain": [],
        "reason_required_for_reject": True,
    }


@frappe.whitelist()
@rate_limit(limit=120, seconds=60)
def get_document(doctype, docname):
    """Single-document payload for the detail screen: header fields, item
    lines, approval log and the per-user capability context that drives the
    action buttons. Opens with an explicit doc-level permission fence — the
    engine's get_approval_context deliberately has none of its own."""
    _guard()
    if doctype not in _APP_DOCTYPES:
        frappe.throw(_("Unsupported doctype."), frappe.PermissionError)
    frappe.has_permission(doctype, "read", doc=docname, throw=True)

    doc = frappe.get_doc(doctype, docname)
    user = frappe.session.user
    payload = {"doctype": doctype, "name": doc.name}

    if doctype in ("Purchase Order", "Material Request"):
        # Lazy import: ts_po_approval is a heavy locked module — same pattern
        # the notification hub uses for the WhatsApp adapter.
        from trustbit_ethanol.ts_gate_entry.ts_po_approval import (
            get_approval_context,
        )

        payload["context"] = get_approval_context(doctype, docname)
        payload["items"] = [
            {
                "item_code": d.item_code,
                "item_name": d.item_name,
                "qty": d.qty,
                "uom": d.uom,
                "rate": getattr(d, "rate", None),
                "amount": getattr(d, "amount", None),
                "cost_center": getattr(d, "cost_center", None),
            }
            for d in (doc.get("items") or [])
        ]
        # The TS Approval Log child table hangs off DIFFERENT parentfields:
        # Purchase Order → ts_approval_log, Material Request → ts_mr_log
        # (verified in meta + tabTS Approval Log.parentfield; live-data BUG-1 —
        # reading ts_approval_log on an MR silently returns an empty history).
        log_field = "ts_approval_log" if doctype == "Purchase Order" else "ts_mr_log"
        payload["approval_log"] = [
            {
                "action": d.action,
                "from_state": d.from_state,
                "to_state": d.to_state,
                "action_by": d.action_by,
                "action_by_name": d.action_by_name,
                "action_by_role": d.action_by_role,
                "action_date": str(d.action_date or ""),
                "comment": d.comment,
                "step_order": d.step_order,
            }
            for d in (doc.get(log_field) or [])
        ]

    if doctype == "Purchase Order":
        payload["doc"] = {
            "supplier": doc.supplier,
            "supplier_name": doc.supplier_name,
            "transaction_date": str(doc.transaction_date or ""),
            "grand_total": doc.grand_total,
            "currency": doc.currency,
            "ts_approval_status": doc.ts_approval_status,
            "ts_po_on_hold": cint(doc.get("ts_po_on_hold")),
            "ts_purchase_category": doc.get("ts_purchase_category"),
            "ts_submitted_by": doc.get("ts_submitted_by"),
            "ts_amount_at_submission": doc.get("ts_amount_at_submission"),
            "docstatus": doc.docstatus,
        }
    elif doctype == "Material Request":
        payload["doc"] = {
            "material_request_type": doc.material_request_type,
            "transaction_date": str(doc.transaction_date or ""),
            "schedule_date": str(doc.schedule_date or ""),
            "ts_mr_status": doc.ts_mr_status,
            "ts_mr_submitted_by": doc.get("ts_mr_submitted_by"),
            "estimated_value": sum(float(d.amount or 0) for d in (doc.get("items") or [])),
            "docstatus": doc.docstatus,
        }
    elif doctype == "TS Budget Override Approval":
        payload["doc"] = {
            "title": doc.get("title"),
            "reference_doctype": doc.reference_doctype,
            "reference_name": doc.reference_name,
            "source_amount": doc.source_amount,
            "cost_center": doc.get("cost_center"),
            "breach_type": doc.get("breach_type"),
            "annual_breach_delta": doc.get("annual_breach_delta"),
            "monthly_breach_delta": doc.get("monthly_breach_delta"),
            "submitted_by": doc.get("submitted_by"),
            "submitted_at": str(doc.get("submitted_at") or ""),
            "submission_reason": doc.get("submission_reason"),
            "status": doc.status,
            "docstatus": doc.docstatus,
        }
        payload["context"] = _budget_override_context(doc, user)
        payload["items"] = []
        payload["approval_log"] = []
    elif doctype == "TS Post Dated Entry Request":
        payload["doc"] = {
            "request_type": doc.request_type,
            "status": doc.status,
            "requested_by": doc.requested_by,
            "request_date": str(doc.get("request_date") or ""),
            "reason": doc.get("reason"),
            "from_date": str(doc.get("from_date") or ""),
            "to_date": str(doc.get("to_date") or ""),
            "valid_from": str(doc.get("valid_from") or ""),
            "valid_until": str(doc.get("valid_until") or ""),
            "token_number": doc.get("token_number"),
            "docstatus": doc.docstatus,
        }
        payload["context"] = _post_dated_context(doc, user)
        payload["items"] = []
        payload["approval_log"] = []

    return payload
