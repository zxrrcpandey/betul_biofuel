"""Production category notifications — channel-pluggable engine (Phase B of the
Multi-BOM production plan; see memory/project_production_multi_flow.md).

`notify_category(category, subject, message, ...)` fans a notification out to every
active recipient row of a TS Production BOM Category:

  - In-App   -> Notification Log (bell). ALWAYS deliverable — the guaranteed channel.
  - Email    -> frappe.sendmail. Best-effort: SMTP is currently unconfigured on both
                servers (parked, pending client creds) so sends fail-soft until then.
  - WhatsApp -> ts_whatsapp.send_template (existing adapter): kill-switch-gated +
                fail-soft by design; needs a TS WhatsApp Template Map row for the
                event_key. Inert until the team enables WhatsApp.

Discipline: every recipient x channel send is isolated in its own try/except
(Lesson 238) and swallowed failures also clear_messages() (Lesson 276) so a
notification failure can NEVER block or pollute the calling workflow. This module
is a library — it exposes NO whitelisted endpoints.
"""

import frappe
from frappe import _
from frappe.utils import cint

DEFAULT_WHATSAPP_EVENT_KEY = "production_category_notify"


def notify_category(category, subject, message, ref_doctype=None, ref_name=None,
                    whatsapp_event_key=None, whatsapp_variables=None):
	"""Fan out `subject`/`message` to every active recipient of `category`.

	Returns a summary dict {"in_app": n, "email": n, "whatsapp": n, "skipped": n}
	(counts of ATTEMPTED sends per channel — delivery itself is best-effort).
	Never raises: any unexpected failure is logged and an empty summary returned.
	"""
	summary = {"in_app": 0, "email": 0, "whatsapp": 0, "skipped": 0}
	try:
		cat = frappe.db.get_value(
			"TS Production BOM Category", category,
			["name", "active"], as_dict=True)
		if not cat or not cint(cat.active):
			return summary

		rows = frappe.get_all(
			"TS Production Notify Recipient",
			filters={"parent": cat.name, "parenttype": "TS Production BOM Category", "active": 1},
			fields=["channel", "user", "role", "email", "mobile"],
			order_by="idx", limit=0,
		)
		for row in rows:
			targets = _resolve_targets(row)
			if not targets:
				summary["skipped"] += 1
				continue
			for target in targets:
				_send_one(row.channel, target, subject, message,
				          ref_doctype, ref_name,
				          whatsapp_event_key or DEFAULT_WHATSAPP_EVENT_KEY,
				          whatsapp_variables, summary)
	except Exception:
		# The engine itself must never break a workflow (Lesson 238).
		frappe.log_error(
			title="notify_category failed",
			message=f"category={category} ref={ref_doctype}/{ref_name}\n{frappe.get_traceback()}",
		)
		frappe.clear_messages()
	return summary


def _resolve_targets(row):
	"""Resolve one recipient row to concrete targets (data-driven — Lesson 221).

	Returns a list of dicts: {"user": <name or None>, "email": ..., "mobile": ...}.
	Priority: explicit user -> role fan-out -> literal email/mobile fallback.
	"""
	targets = []
	if row.user:
		if frappe.db.get_value("User", row.user, "enabled"):
			info = frappe.db.get_value(
				"User", row.user, ["email", "mobile_no"], as_dict=True) or {}
			targets.append({"user": row.user,
			                "email": info.get("email"),
			                "mobile": row.mobile or info.get("mobile_no")})
	elif row.role:
		holders = frappe.db.sql("""
			SELECT u.name, u.email, u.mobile_no
			FROM `tabHas Role` hr
			JOIN `tabUser` u ON u.name = hr.parent AND u.enabled = 1
			WHERE hr.role = %s AND u.name NOT IN ('Administrator', 'Guest')
		""", (row.role,), as_dict=True)
		for h in holders:
			targets.append({"user": h.name, "email": h.email, "mobile": h.mobile_no})
	elif row.email or row.mobile:
		targets.append({"user": None, "email": row.email, "mobile": row.mobile})
	return targets


def _send_one(channel, target, subject, message, ref_doctype, ref_name,
              event_key, variables, summary):
	"""One recipient x one channel — isolated fail-soft send (Lessons 238/276)."""
	# Counters record ATTEMPTS (incremented before the send), so a failed send —
	# e.g. email with SMTP unconfigured — is still visible in the summary.
	if channel == "In-App":
		try:
			if target.get("user"):
				summary["in_app"] += 1
				_notification_log(target["user"], subject, message, ref_doctype, ref_name)
			else:
				summary["skipped"] += 1  # in-app needs a User account
		except Exception:
			frappe.clear_messages()
	elif channel == "Email":
		try:
			if target.get("email"):
				summary["email"] += 1
				frappe.sendmail(recipients=[target["email"]], subject=subject,
				                message=message, delayed=True)
			else:
				summary["skipped"] += 1
		except Exception:
			# SMTP unconfigured (current reality on both servers) or bad address —
			# never blocks the workflow.
			frappe.clear_messages()
	elif channel == "WhatsApp":
		try:
			mobile = target.get("mobile")
			if mobile:
				summary["whatsapp"] += 1
				from trustbit_ethanol.ts_gate_entry.ts_whatsapp import send_template
				send_template(mobile, event_key, variables or {"1": subject},
				              ref_doctype, ref_name)
			else:
				summary["skipped"] += 1
		except Exception:
			# Adapter is itself kill-switch-gated + fail-soft; belt-and-braces here.
			frappe.clear_messages()


def _notification_log(user, subject, message, ref_doctype, ref_name):
	"""Bell notification (mirrors ts_mr_transfer._create_system_notification)."""
	doc = frappe.get_doc({
		"doctype": "Notification Log",
		"for_user": user,
		"type": "Alert",
		"subject": subject,
		"email_content": message,
		"document_type": ref_doctype,
		"document_name": ref_name,
	})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
