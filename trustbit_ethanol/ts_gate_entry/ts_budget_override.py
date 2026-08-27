"""v2.10.0 — TS Budget Override Approval endpoints.

Whitelisted entry points for the budget-exceeded → CEO approval flow.
All mutations declare methods=["POST"] (Lesson 175 — CSRF hardening).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from trustbit_ethanol.ts_gate_entry.ts_budget import (
    _is_override_flow_enabled,
    detect_budget_breach_for_mr,
    detect_budget_breach_for_po,
)


APPROVER_ROLES = {"CEO", "MD", "System Manager"}


def _require_post():
    """v2.31.0 — L366: methods=["POST"] is enforced on the TOP-LEVEL cmd only;
    via core's bare-whitelist mapper trampolines (make_mapped_doc 1-arg,
    map_docs 2-arg) a mutation stays GET-reachable with CSRF skipped.
    Re-assert in-body (request is None for console/jobs/tests, which stay
    allowed). Precedent: ts_sr_to_po / ts_po_approval / ts_post_dated."""
    _req = getattr(frappe.local, "request", None)
    if _req is not None and _req.method != "POST":
        raise frappe.PermissionError
SUPPORTED_REF_DOCTYPES = ("Material Request", "Purchase Order")


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════


def _normalize_ref_doctype(ref_doctype):
    if ref_doctype not in SUPPORTED_REF_DOCTYPES:
        frappe.throw(
            _("Unsupported reference doctype '{0}'. Allowed: {1}.").format(
                ref_doctype, ", ".join(SUPPORTED_REF_DOCTYPES)
            )
        )
    return ref_doctype


def _user_is_approver():
    user_roles = set(frappe.get_roles(frappe.session.user))
    return bool(APPROVER_ROLES & user_roles)


def _detector_for(ref_doctype):
    if ref_doctype == "Purchase Order":
        return detect_budget_breach_for_po
    if ref_doctype == "Material Request":
        return detect_budget_breach_for_mr
    frappe.throw(_("Unsupported reference doctype '{0}'.").format(ref_doctype))


def _find_active_override(ref_doctype, ref_name):
    """Return the most recently submitted override for (ref_doctype, ref_name), or None."""
    rows = frappe.get_all(
        "TS Budget Override Approval",
        filters={
            "reference_doctype": ref_doctype,
            "reference_name": ref_name,
            "docstatus": ["!=", 2],
        },
        fields=["name", "status", "decision", "acted_by", "acted_at", "ceo_comment"],
        order_by="creation DESC",
        limit=1,
    )
    return rows[0] if rows else None


def _step1_role_for(source_doc):
    """Resolve the role of the first approval step on the source doc's route.

    Returns a tuple (role, route_name) or (None, None) if route can't be resolved
    yet (e.g. the source-doc is sitting at the breach branch before route selection).
    """
    try:
        if source_doc.doctype == "Purchase Order":
            from trustbit_ethanol.ts_gate_entry.ts_po_approval import (
                _resolve_po_category,
                _find_po_approval_rule,
            )

            category = _resolve_po_category(source_doc)
            rule = _find_po_approval_rule(category, flt(source_doc.grand_total)) if category else None
            if not rule:
                return None, None
            rule_doc = frappe.get_doc("TS PO Approval Rule", rule)
            if not rule_doc.approval_steps:
                return None, rule
            return rule_doc.approval_steps[0].role, rule
        if source_doc.doctype == "Material Request":
            from trustbit_ethanol.ts_gate_entry.ts_po_approval import _find_mr_route

            primary_cc = source_doc.get("cost_center") or (
                source_doc.items[0].cost_center if source_doc.get("items") else None
            )
            route = _find_mr_route(primary_cc, source_doc.get("material_request_type")) if primary_cc else None
            if not route:
                return None, None
            route_doc = frappe.get_doc("TS MR Approval Route", route)
            if not route_doc.approval_steps:
                return None, route
            return route_doc.approval_steps[0].role, route
    except Exception:
        # Best-effort — if route lookup fails, caller falls back to a generic notification.
        return None, None
    return None, None


def _send_budget_override_notification(override_doc, event, recipients, extra=None):
    """Best-effort dispatcher for TS Budget Override Approval (Lesson 238).

    MEDIUM #1 fix (Phase 4 security audit): Previously reused
    `_send_budget_notification` from ts_budget.py which hardcodes
    `reference_doctype='TS Budget Proposal'` and reads `total_proposed`. The
    override doctype has neither — sendmail would log errors referencing a
    nonexistent TS Budget Proposal record. This is a dedicated helper that
    knows the override field shape.

    `event` ∈ {"created", "approved", "rejected", "cancelled"}.
    Recipients = list of email user-IDs (already deduped, no Administrator/Guest).
    """
    if not recipients:
        return
    try:
        subject_map = {
            "created": frappe._("Budget Override Pending: {0}"),
            "approved": frappe._("Budget Override Approved: {0}"),
            "rejected": frappe._("Budget Override Rejected: {0}"),
            "cancelled": frappe._("Budget Override Cancelled: {0}"),
        }
        subject = subject_map.get(event, frappe._("Budget Override Update: {0}")).format(
            override_doc.name
        )
        # Bell channel — email alone is dead (no SMTP). Subject is name+event
        # only; the amount/cost-center stay in the email body, NOT in the bell.
        from trustbit_ethanol.ts_gate_entry.ts_notification_coverage import _coverage_bell
        _coverage_bell(recipients, subject, "TS Budget Override Approval", override_doc.name)
        ref = f"{override_doc.reference_doctype} {override_doc.reference_name}"
        breach_summary = (
            f"Breach Type: {override_doc.breach_type or '—'} · "
            f"Cost Center: {override_doc.cost_center or '—'} · "
            f"Period: {override_doc.period_month or '—'} · "
            f"Source Amount: {frappe.utils.fmt_money(flt(override_doc.source_amount))}"
        )
        extra_lines = ""
        if extra:
            for k, v in extra.items():
                extra_lines += (
                    f"<br><b>{frappe.utils.escape_html(str(k))}:</b> "
                    f"{frappe.utils.escape_html(str(v))}"
                )
        message = (
            f"<p>Source: <b>{frappe.utils.escape_html(ref)}</b></p>"
            f"<p>{frappe.utils.escape_html(breach_summary)}</p>"
            f"<p>Override: <a href=\"/app/ts-budget-override-approval/"
            f"{frappe.utils.escape_html(override_doc.name)}\">"
            f"{frappe.utils.escape_html(override_doc.name)}</a> · "
            f"Status: <b>{frappe.utils.escape_html(override_doc.status)}</b></p>"
            f"{extra_lines}"
        )
        # Per-recipient send so one failure doesn't kill the rest (Lesson 238).
        for r in recipients:
            try:
                frappe.sendmail(
                    recipients=[r],
                    subject=subject,
                    message=message,
                    reference_doctype="TS Budget Override Approval",
                    reference_name=override_doc.name,
                    delayed=False,
                )
            except Exception:
                try:
                    frappe.log_error(
                        title="budget override notify (recipient)",
                        message=f"event={event} doc={override_doc.name} recipient={r} err={frappe.get_traceback()[:1000]}",
                    )
                except Exception:
                    pass
    except Exception:
        # Outer guard — notifications must NEVER block the docstatus transition.
        try:
            frappe.log_error(
                title="budget override notify failed",
                message=f"event={event} doc={override_doc.name} err={frappe.get_traceback()[:1000]}",
            )
        except Exception:
            pass


def _resume_normal_route(source_doc):
    """Approval path: route the source doc into its normal step-1 state.

    Sets frappe.flags.in_budget_override_approval=True so that
    - _block_gate_field_tampering allows the status mutation
    - the breach detector inside _submit_*_for_approval (if called) is bypassed
    """
    from trustbit_ethanol.ts_gate_entry.ts_po_approval import _assign_route_and_first_step

    frappe.flags.in_budget_override_approval = True
    try:
        _assign_route_and_first_step(source_doc)
    finally:
        frappe.flags.in_budget_override_approval = False


def _send_source_to_rejected(source_doc, override_doc):
    """Reject path: flip source doc status to Rejected, leave docstatus untouched.

    Source docs may be docstatus=0 (drafts pending approval) at this point.
    Rejection lands them in 'Rejected' status; users must amend/clone to retry.
    """
    frappe.flags.in_budget_override_approval = True
    try:
        if source_doc.doctype == "Material Request":
            source_doc.db_set("ts_mr_status", "Rejected", update_modified=True)
        elif source_doc.doctype == "Purchase Order":
            source_doc.db_set("ts_approval_status", "Rejected", update_modified=True)
        source_doc.add_comment(
            "Comment",
            text=_("Budget override request {0} was rejected. Reason: {1}").format(
                frappe.bold(override_doc.name),
                frappe.utils.escape_html(override_doc.ceo_comment or ""),
            ),
        )
    finally:
        frappe.flags.in_budget_override_approval = False


# ═══════════════════════════════════════════════════════════════════════
#  GET — preview + lookup
# ═══════════════════════════════════════════════════════════════════════


@frappe.whitelist()
def get_budget_breach_preview(reference_doctype, reference_name):
    """Return a breach snapshot for a source doc — no override created.

    Used by JS to render the warning banner BEFORE submit-for-approval is clicked.
    Read-only; no side effects.
    """
    _normalize_ref_doctype(reference_doctype)
    if not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        return {"has_breach": False, "reason": "doc_not_found"}

    doc = frappe.get_doc(reference_doctype, reference_name)
    frappe.has_permission(reference_doctype, "read", doc=doc, throw=True)

    detector = _detector_for(reference_doctype)
    return detector(doc)


@frappe.whitelist()
def get_budget_override_for_doc(reference_doctype, reference_name):
    """Return the currently-active override for a source doc, or null.

    Used by JS to render the 'Linked Budget Override: BO-XXXX' banner.
    """
    _normalize_ref_doctype(reference_doctype)
    if not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        return None

    doc = frappe.get_doc(reference_doctype, reference_name)
    frappe.has_permission(reference_doctype, "read", doc=doc, throw=True)

    return _find_active_override(reference_doctype, reference_name)


# ═══════════════════════════════════════════════════════════════════════
#  POST — create / approve / reject / cancel
# ═══════════════════════════════════════════════════════════════════════


@frappe.whitelist(methods=["POST"])
def create_budget_override_for_doc(reference_doctype, reference_name, submission_reason=None):
    """Auto-create a TS Budget Override Approval for a breach-detected source doc.
    v2.31.0: _require_post() re-asserted below (L366 trampoline hole).

    Intended to be called server-side from `_submit_*_for_approval` only.
    External callers must hold doc 'submit' perm on the source — defense-in-depth.
    """
    _require_post()  # L366 — trampoline-reachable
    # FAIL2 fix (live-data-tester Phase 4): when kill switch is OFF, this
    # endpoint MUST also be disabled — otherwise a REST caller could create
    # overrides even though the auto-flow inside _submit_*_for_approval is
    # bypassed. _is_override_flow_enabled is fail-closed (returns True on read
    # failure), so this guard never accidentally locks out the legit flow.
    if not _is_override_flow_enabled():
        frappe.throw(
            _("Budget Override Approval flow is disabled. Re-enable TS Settings."
              " > 'Budget Override Flow Enabled' first, or use the legacy"
              " ceo_budget_override endpoint as the manual escape valve."),
        )
    _normalize_ref_doctype(reference_doctype)
    if not reference_name or not frappe.db.exists(reference_doctype, reference_name):
        frappe.throw(_("Source document {0} {1} does not exist.").format(reference_doctype, reference_name))

    source_doc = frappe.get_doc(reference_doctype, reference_name)
    frappe.has_permission(reference_doctype, "submit", doc=source_doc, throw=True)

    # If an active override already exists for this doc, return it (idempotent re-submit).
    existing = _find_active_override(reference_doctype, reference_name)
    if existing and existing["status"] == "Pending CEO":
        return {"name": existing["name"], "status": existing["status"], "reused": True}

    detector = _detector_for(reference_doctype)
    breach = detector(source_doc)
    if not breach.get("has_breach"):
        frappe.throw(_("No budget breach detected for {0} {1}.").format(reference_doctype, reference_name))

    override = frappe.new_doc("TS Budget Override Approval")
    frappe.flags.in_budget_override_approval = True
    try:
        override.reference_doctype = reference_doctype
        override.reference_name = reference_name
        override.source_amount = flt(breach.get("source_amount"))
        override.cost_center = breach.get("cost_center")
        override.company = breach.get("company") or source_doc.get("company")
        override.fiscal_year = breach.get("fiscal_year")
        override.breach_type = breach.get("breach_type")
        override.period_month = breach.get("period_month")

        override.annual_budget_amount = flt(breach.get("annual_budget_amount"))
        override.annual_committed_amount = flt(breach.get("annual_committed_amount"))
        override.annual_spent_amount = flt(breach.get("annual_spent_amount"))
        override.annual_available = flt(breach.get("annual_available"))
        override.annual_breach_delta = flt(breach.get("annual_breach_delta"))
        override.annual_utilization_pct = flt(breach.get("annual_utilization_pct"))

        override.monthly_budget_amount = flt(breach.get("monthly_budget_amount"))
        override.monthly_committed_amount = flt(breach.get("monthly_committed_amount"))
        override.monthly_spent_amount = flt(breach.get("monthly_spent_amount"))
        override.monthly_available = flt(breach.get("monthly_available"))
        override.monthly_breach_delta = flt(breach.get("monthly_breach_delta"))
        override.monthly_utilization_pct = flt(breach.get("monthly_utilization_pct"))

        for line in breach.get("breach_lines") or []:
            override.append("breach_lines", {
                "cost_center": line.get("cost_center"),
                "share_amount": flt(line.get("share_amount")),
                "breach_type": line.get("breach_type") or "None",
                "period_month": line.get("period_month"),
                "annual_budget": flt(line.get("annual_budget")),
                "annual_available": flt(line.get("annual_available")),
                "annual_breach": flt(line.get("annual_breach")),
                "monthly_budget": flt(line.get("monthly_budget")),
                "monthly_available": flt(line.get("monthly_available")),
                "monthly_breach": flt(line.get("monthly_breach")),
            })

        override.submitted_by = frappe.session.user
        override.submitted_at = now_datetime()
        override.submission_reason = (submission_reason or "").strip() or None
        override.status = "Pending CEO"
        override.kill_switch_at_create = 1 if _is_override_flow_enabled() else 0

        override.insert(ignore_permissions=True)
        override.submit()
    finally:
        frappe.flags.in_budget_override_approval = False

    # Q5 recipients on create — CEO + MD.
    recipients = _approver_recipients()
    _send_budget_override_notification(override, "created", recipients, extra={
        "source": f"{reference_doctype} {reference_name}",
        "breach_type": override.breach_type,
        "shortfall": flt(override.annual_breach_delta) + flt(override.monthly_breach_delta),
    })

    return {"name": override.name, "status": override.status, "reused": False}


@frappe.whitelist(methods=["POST"])
def approve_budget_override(name, comment=None):
    """Approver action: approve the override + resume source doc's normal route."""
    _require_post()  # L366 — trampoline-reachable
    if not name:
        frappe.throw(_("Override name is required."))
    if not _user_is_approver():
        frappe.throw(_("Only CEO, MD, or System Manager can act on budget overrides."), frappe.PermissionError)

    override = frappe.get_doc("TS Budget Override Approval", name)
    # MEDIUM #2 defense-in-depth (Lesson 224): role check is necessary but not
    # sufficient — User Permission scoping (per-company / per-CC) must also be
    # honored before mutating the override.
    frappe.has_permission(
        "TS Budget Override Approval", "write", doc=override, throw=True
    )
    if override.docstatus != 1:
        frappe.throw(_("Override must be submitted before action."))
    if override.status != "Pending CEO":
        frappe.throw(_("Override is already {0}.").format(override.status))

    source_doc = frappe.get_doc(override.reference_doctype, override.reference_name)

    # v2.28 Executive Hold — a held source PO cannot progress via budget override.
    # Lazy import (ts_po_approval lazily imports this module); no-op on MR sources.
    from trustbit_ethanol.ts_gate_entry.ts_po_approval import _block_if_on_hold
    _block_if_on_hold(source_doc)

    frappe.flags.in_budget_override_approval = True
    try:
        override.db_set({
            "status": "Approved",
            "decision": "Approved",
            "acted_by": frappe.session.user,
            "acted_at": now_datetime(),
            "ceo_comment": (comment or "").strip() or None,
        }, update_modified=True)
        override.add_comment(
            "Comment",
            text=_("Approved by {0}. {1}").format(
                frappe.utils.get_fullname(frappe.session.user),
                frappe.utils.escape_html((comment or "").strip()),
            ),
        )
    finally:
        frappe.flags.in_budget_override_approval = False

    # Resume normal route on source doc — flag-wrapped to bypass tamper guard + breach detector.
    _resume_normal_route(source_doc)

    # Q5 recipients on Approve — submitter + step-1 role + creator.
    step1_role, _route = _step1_role_for(source_doc)
    recipients = _resolve_approve_recipients(override, step1_role)
    _send_budget_override_notification(override, "approved", recipients, extra={
        "source": f"{override.reference_doctype} {override.reference_name}",
        "next_step_role": step1_role or "(route unresolved — check source doc)",
    })

    new_status = (
        source_doc.get("ts_approval_status")
        if source_doc.doctype == "Purchase Order"
        else source_doc.get("ts_mr_status")
    )
    return {"ok": True, "source_doc_new_status": new_status}


@frappe.whitelist(methods=["POST"])
def reject_budget_override(name, comment):
    """Approver action: reject the override + send source doc to Rejected status.

    `comment` is required and must be ≥10 chars.
    """
    _require_post()  # L366 — trampoline-reachable
    if not name:
        frappe.throw(_("Override name is required."))
    if not (comment or "").strip() or len((comment or "").strip()) < 10:
        frappe.throw(_("Rejection comment (≥10 chars) is required."))
    if not _user_is_approver():
        frappe.throw(_("Only CEO, MD, or System Manager can act on budget overrides."), frappe.PermissionError)

    override = frappe.get_doc("TS Budget Override Approval", name)
    # MEDIUM #2 defense-in-depth — see approve_budget_override.
    frappe.has_permission(
        "TS Budget Override Approval", "write", doc=override, throw=True
    )
    if override.docstatus != 1:
        frappe.throw(_("Override must be submitted before action."))
    if override.status != "Pending CEO":
        frappe.throw(_("Override is already {0}.").format(override.status))

    source_doc = frappe.get_doc(override.reference_doctype, override.reference_name)

    # v2.28 Executive Hold — a held source PO cannot progress via budget override.
    from trustbit_ethanol.ts_gate_entry.ts_po_approval import _block_if_on_hold
    _block_if_on_hold(source_doc)

    frappe.flags.in_budget_override_approval = True
    try:
        override.db_set({
            "status": "Rejected",
            "decision": "Rejected",
            "acted_by": frappe.session.user,
            "acted_at": now_datetime(),
            "ceo_comment": comment.strip(),
        }, update_modified=True)
        override.add_comment(
            "Comment",
            text=_("Rejected by {0}. Reason: {1}").format(
                frappe.utils.get_fullname(frappe.session.user),
                frappe.utils.escape_html(comment.strip()),
            ),
        )
    finally:
        frappe.flags.in_budget_override_approval = False

    _send_source_to_rejected(source_doc, override)

    step1_role, _route = _step1_role_for(source_doc)
    recipients = _resolve_reject_recipients(override, step1_role)
    _send_budget_override_notification(override, "rejected", recipients, extra={
        "source": f"{override.reference_doctype} {override.reference_name}",
        "comment": comment.strip(),
    })

    return {"ok": True, "source_doc_new_status": "Rejected"}


@frappe.whitelist(methods=["POST"])
def cancel_budget_override(name, reason):
    """Submitter (or System Manager) cancels a Pending-CEO override.

    `reason` is required.
    """
    _require_post()  # L366 — trampoline-reachable
    if not name:
        frappe.throw(_("Override name is required."))
    if not (reason or "").strip():
        frappe.throw(_("Cancellation reason is required."))

    override = frappe.get_doc("TS Budget Override Approval", name)
    # MEDIUM #2 defense-in-depth — see approve_budget_override.
    frappe.has_permission(
        "TS Budget Override Approval", "write", doc=override, throw=True
    )

    user_roles = set(frappe.get_roles(frappe.session.user))
    is_submitter = override.submitted_by == frappe.session.user
    is_admin = "System Manager" in user_roles
    if not (is_submitter or is_admin):
        frappe.throw(
            _("Only the submitter or a System Manager can cancel this override."),
            frappe.PermissionError,
        )

    if override.status != "Pending CEO":
        frappe.throw(_("Cannot cancel: override is already {0}.").format(override.status))

    frappe.flags.in_budget_override_approval = True
    try:
        override.db_set({
            "status": "Cancelled",
            "acted_by": frappe.session.user,
            "acted_at": now_datetime(),
            "ceo_comment": _("Cancelled by submitter: {0}").format(reason.strip())[:140] if is_submitter
            else _("Cancelled by admin: {0}").format(reason.strip())[:140],
        }, update_modified=True)
        override.add_comment(
            "Comment",
            text=_("Cancelled by {0}. Reason: {1}").format(
                frappe.utils.get_fullname(frappe.session.user),
                frappe.utils.escape_html(reason.strip()),
            ),
        )
        # Frappe std cancel for is_submittable docs (docstatus=1 → 2).
        override.reload()
        override.cancel()
    finally:
        frappe.flags.in_budget_override_approval = False

    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════
#  Q5 recipient helpers
# ═══════════════════════════════════════════════════════════════════════


def _role_users(role):
    """Return active enabled users holding a role."""
    return frappe.get_all(
        "Has Role",
        filters={"role": role, "parenttype": "User"},
        fields=["parent"],
        pluck="parent",
    )


def _approver_recipients():
    out = set()
    for role in ("CEO", "MD"):
        for u in _role_users(role):
            if u and u not in ("Administrator", "Guest"):
                out.add(u)
    return list(out)


def _resolve_approve_recipients(override, step1_role):
    out = set(_approver_recipients())
    if override.submitted_by:
        out.add(override.submitted_by)
    if step1_role:
        for u in _role_users(step1_role):
            if u and u not in ("Administrator", "Guest"):
                out.add(u)
    return list(out)


def _resolve_reject_recipients(override, step1_role):
    out = set()
    if override.submitted_by:
        out.add(override.submitted_by)
    if override.owner and override.owner != override.submitted_by:
        out.add(override.owner)
    if step1_role:
        for u in _role_users(step1_role):
            if u and u not in ("Administrator", "Guest"):
                out.add(u)
    return list(out)


# ═══════════════════════════════════════════════════════════════════════
#  Hook handler — wire via hooks.py doc_events
# ═══════════════════════════════════════════════════════════════════════


def cancel_linked_override_on_source_cancel(doc, method=None):
    """When source MR/PO is cancelled, auto-cancel any Pending override.

    Wired via hooks.py:
        Material Request.on_cancel → ts_budget_override.cancel_linked_override_on_source_cancel
        Purchase Order.on_cancel   → ts_budget_override.cancel_linked_override_on_source_cancel

    MEDIUM #3 fix (Phase 4 security audit): If override.cancel() throws (e.g. a
    custom validate_cancel guard), the override would silently stay Pending CEO
    while the source doc is already cancelled — inconsistent state. We now
    surface the failure to the SOURCE doc's timeline so the admin sees it
    immediately, AND we force the status to 'Cancelled' via db_set even if the
    docstatus transition fails (idempotent — re-running this handler is safe).
    """
    active_name = None
    try:
        active = _find_active_override(doc.doctype, doc.name)
        if not active or active["status"] != "Pending CEO":
            return
        active_name = active["name"]

        override = frappe.get_doc("TS Budget Override Approval", active_name)
        frappe.flags.in_budget_override_approval = True
        try:
            # Always flip the logical status FIRST so the override is no longer
            # "Pending CEO" from any consumer's point of view (banners, reports,
            # CEO inbox) — even if the docstatus cancel below fails.
            override.db_set({
                "status": "Cancelled",
                "acted_by": frappe.session.user,
                "acted_at": now_datetime(),
                "ceo_comment": _("Auto-cancelled: source {0} {1} was cancelled.").format(
                    doc.doctype, doc.name
                )[:140],
            }, update_modified=True)
            override.add_comment(
                "Comment",
                text=_("Auto-cancelled because source {0} {1} was cancelled.").format(
                    doc.doctype, doc.name
                ),
            )
            override.reload()
            try:
                override.cancel()
            except Exception as cancel_err:
                # docstatus transition failed; status is already 'Cancelled' so
                # consumers see the right state. Surface to source doc timeline
                # so admin notices.
                _surface_override_cancel_failure(doc, active_name, cancel_err)
        finally:
            frappe.flags.in_budget_override_approval = False
    except Exception as e:
        # Never block source doc cancellation on override cleanup failure —
        # but DO surface the issue to the source doc timeline (MEDIUM #3).
        _surface_override_cancel_failure(doc, active_name, e)
        try:
            frappe.log_error(
                title="cancel_linked_override failed",
                message=f"source={doc.doctype} {doc.name} err={frappe.get_traceback()[:1000]}",
            )
        except Exception:
            pass


def _surface_override_cancel_failure(source_doc, override_name, err):
    """MEDIUM #3 helper — adds a visible Comment to the source doc timeline so the
    admin can SEE that linked-override cleanup failed and the override may be
    stuck. Never throws — best-effort timeline write."""
    if not source_doc or not override_name:
        return
    try:
        msg = _("Linked budget override {0} could not be fully cancelled when this doc was cancelled: {1}").format(
            override_name, frappe.utils.escape_html(str(err)[:200])
        )
        source_doc.add_comment("Comment", text=msg)
    except Exception:
        # Comment write itself failed — give up; log_error from the caller covers triage.
        pass
