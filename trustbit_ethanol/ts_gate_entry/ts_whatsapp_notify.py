# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration (Airtel IQ) — Phase 1b instant shim.
#
# Called ONCE at the end of ts_po_approval._send_approval_notification (the hub
# that every PO/MR approval action funnels through). Maps the approval `action`
# to a template event_key, builds the positional variables, and fires a
# per-recipient WhatsApp via the adapter. Fail-soft (never raises into the
# approval path) and inert when the kill-switch is off (send_template no-ops).

import frappe
from frappe.utils import flt

from trustbit_ethanol.ts_gate_entry.ts_whatsapp import send_template

# (doctype, _send_approval_notification action) -> template event_key.
# Only the high-value events are mapped; revised/held/resumed/sla_breach are
# intentionally left to email + the escalation scheduler.
ACTION_EVENT = {
	("Purchase Order", "pending_approval"): "po_needs_approval",
	("Purchase Order", "final_approved"): "po_approved",
	("Purchase Order", "rejected"): "po_rejected",
	("Material Request", "mr_pending"): "mr_needs_approval",
	("Material Request", "mr_approved"): "mr_approved",
	("Material Request", "mr_rejected"): "mr_rejected",
}


def whatsapp_notify_for_approval(doc, action, recipients, extra=None):
	"""Fire instant WhatsApp templates for an approval event. Fail-soft."""
	try:
		event_key = ACTION_EVENT.get((doc.doctype, action))
		if not event_key or not recipients:
			return
		extra = extra or {}
		is_po = doc.doctype == "Purchase Order"
		doc_no = doc.name
		amount = (
			frappe.format_value(flt(doc.get("grand_total") or 0), {"fieldtype": "Currency"})
			if is_po else ""
		)
		status = (doc.get("ts_approval_status") if is_po else doc.get("ts_mr_status")) or ""
		cost_center = "" if is_po else (doc.get("cost_center") or "")
		reason = (
			extra.get("reason") or extra.get("rejection_reason")
			or extra.get("comment") or "Not specified"
		)

		seen = set()
		for user in recipients:
			if not user or user in ("Administrator", "Guest") or user in seen:
				continue
			seen.add(user)
			name = frappe.db.get_value("User", user, "full_name") or user
			variables = _variables(event_key, name, doc_no, amount, status, cost_center, reason)
			# `user` (an email) is resolved to a WhatsApp number + opt-in by the
			# adapter; kill-switch / sandbox / opt-out gating all happen there.
			send_template(
				user, event_key, variables,
				ref_doctype=doc.doctype, ref_name=doc.name,
			)
	except Exception as e:
		try:
			frappe.log_error(
				title="WhatsApp: approval notify failed"[:140],
				message=f"{getattr(doc, 'name', '?')} / {action}: {e}",
			)
		except Exception:
			pass
		frappe.clear_messages()


def _variables(event_key, name, doc_no, amount, status, cost_center, reason):
	if event_key == "po_needs_approval":
		return [name, doc_no, amount, status]
	if event_key == "po_approved":
		return [name, doc_no, amount]
	if event_key == "po_rejected":
		return [name, doc_no, reason]
	if event_key == "mr_needs_approval":
		return [name, doc_no, cost_center, status]
	if event_key == "mr_approved":
		return [name, doc_no]
	if event_key == "mr_rejected":
		return [name, doc_no, reason]
	return [name, doc_no]
