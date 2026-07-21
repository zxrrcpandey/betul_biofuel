# Copyright (c) 2026, Trustbit Software and contributors
# Notification Center — bell coverage for previously email-only events.
#
# Outgoing email is unconfigured on both servers (no default outgoing Email
# Account), so events that only called frappe.sendmail were invisible to
# users. This module adds the missing in-app bell (Notification Log) channel
# WITHOUT touching any producer's state machine: it never writes reminder/SLA
# stamp fields, and the approval-SLA emitter below is a read-only wrapper so
# the locked ts_po_approval.py stays byte-untouched.

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime


def _coverage_bell(users, subject, doctype, name, body=None, permission_check=False):
    """Fail-soft per-user Notification Log insert (mirrors ts_grain_defer's
    bell loop). NEVER raises — a bell must not break the calling flow
    (Lessons 238/276). With permission_check=True a recipient who cannot read
    the document is silently skipped (confidential grain/maize POs, L297)."""
    for user in dict.fromkeys(users or []):
        if not user or user in ("Administrator", "Guest"):
            continue
        try:
            if not frappe.db.exists("User", user):
                continue
            if permission_check and not frappe.has_permission(
                    doctype, "read", doc=name, user=user):
                continue
            frappe.get_doc({
                "doctype": "Notification Log",
                "for_user": user,
                "type": "Alert",
                "subject": frappe.utils.strip_html(subject or "")[:140],
                "document_type": doctype,
                "document_name": name,
                "email_content": body or subject,
            }).insert(ignore_permissions=True)
        except Exception:
            frappe.clear_messages()


def emit_stuck_approval_bells():
    """Scheduler (*/30): bell the CURRENT-step approvers of POs/MRs pending
    beyond the approval SLA. Read-only wrapper — check_approval_sla in the
    locked ts_po_approval.py keeps emailing the CTL address; this only adds
    the in-app channel for the people who can actually act.

    Dedup is log-derived (zero new fields on the locked PO/MR): while a
    recipient still has ANY unread bell for the document, no new bell is
    inserted; once they read it, a still-stuck doc may bell again next scan.
    """
    try:
        sla_hours = cint(frappe.db.get_single_value("TS Settings", "approval_sla_hours"))
        if not sla_hours:
            return
        from trustbit_ethanol.ts_gate_entry.ts_whatsapp_reminders import _stage_recipients

        now = now_datetime()
        for doctype, status_field, step_field, rule_field in (
            ("Purchase Order", "ts_approval_status", "ts_current_step", "ts_approval_rule"),
            ("Material Request", "ts_mr_status", "ts_mr_current_step", "ts_mr_approval_route"),
        ):
            label = "PO" if doctype == "Purchase Order" else "MR"
            # rule IS SET: a ruleless pending doc would fall through the
            # resolver to its CEO+MD fallback and bell-spam leadership.
            rows = frappe.get_all(
                doctype,
                filters={status_field: ["like", "Pending%"], "docstatus": 0,
                         rule_field: ["is", "set"],
                         "modified": ["<", add_to_date(now, hours=-sla_hours)]},
                fields=["name", "modified", status_field, step_field, rule_field],
            )
            for row in rows:
                try:
                    users = _stage_recipients(
                        row, 1, doctype == "Purchase Order", step_field, rule_field)
                    # Dedup: skip while an unread bell exists for the doc, AND
                    # for one SLA window after ANY bell (read included) — a
                    # read-but-stuck doc re-bells at most once per window.
                    window_start = add_to_date(now, hours=-sla_hours)
                    users = [
                        u for u in dict.fromkeys(users)
                        if u and u not in ("Administrator", "Guest")
                        and not frappe.db.exists("Notification Log", {
                            "for_user": u, "document_type": doctype,
                            "document_name": row.name, "read": 0})
                        and not frappe.db.exists("Notification Log", {
                            "for_user": u, "document_type": doctype,
                            "document_name": row.name,
                            "creation": [">=", window_start]})
                    ]
                    # Subject is name-only — NO amount/supplier (confidential-safe);
                    # permission_check drops non-allow-listed users on confidential POs.
                    _coverage_bell(
                        users,
                        _("{0} {1} awaiting your approval (overdue)").format(label, row.name),
                        doctype, row.name, permission_check=True)
                except Exception:
                    frappe.clear_messages()
    except Exception:
        try:
            frappe.log_error(title="notification coverage: approval bells",
                             message=frappe.get_traceback()[:2000])
        except Exception:
            pass
        frappe.clear_messages()
