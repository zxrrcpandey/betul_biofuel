# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration (Airtel IQ) — Phase 1b escalation scheduler.
#
# Runs every 30 min (hooks.py scheduler_events). Scans PO/MR stuck in a
# Pending state and sends a single escalating reminder (L1 -> L2 -> L3) as each
# threshold is crossed, climbing the chain: L1 current approvers, L2 next step,
# L3 leadership (CEO + MD). Dedup is Log-derived (no new fields on the locked
# PO/MR doctypes): a stage is sent at most once per document. Fully inert when
# the kill-switch is off (returns immediately; send_template also no-ops).

import frappe
from frappe.utils import now_datetime, time_diff_in_hours, cint, flt

from trustbit_ethanol.ts_gate_entry.ts_whatsapp import send_template

REMINDER_KEYS = ("reminder_l1", "reminder_l2", "reminder_l3")


def run_whatsapp_escalations():
	"""Scheduler entry. Fail-soft; per-doc isolation."""
	try:
		if not _enabled():
			return  # inert when the feature is off
		settings = frappe.get_single("TS Settings")
		thresholds = {
			1: cint(settings.get("ts_whatsapp_l1_hours")) or 4,
			2: cint(settings.get("ts_whatsapp_l2_hours")) or 12,
			3: cint(settings.get("ts_whatsapp_l3_hours")) or 24,
		}
		_scan("Purchase Order", thresholds, is_po=True)
		_scan("Material Request", thresholds, is_po=False)
	except Exception as e:
		_log_error("run_whatsapp_escalations failed", str(e))
		frappe.clear_messages()


def _scan(doctype, thresholds, is_po):
	status_field = "ts_approval_status" if is_po else "ts_mr_status"
	step_field = "ts_current_step" if is_po else "ts_mr_current_step"
	rule_field = "ts_approval_rule" if is_po else "ts_mr_approval_route"
	fields = ["name", "modified", status_field, step_field, rule_field]
	if is_po:
		fields.append("grand_total")
	rows = frappe.get_all(
		doctype,
		filters={status_field: ["like", "Pending%"], "docstatus": 0},
		fields=fields,
	)
	for row in rows:
		try:
			_process(doctype, row, thresholds, is_po, status_field, step_field, rule_field)
		except Exception as e:
			_log_error(f"escalation {doctype} {row.get('name')}", str(e))


def _process(doctype, row, thresholds, is_po, status_field, step_field, rule_field):
	hours = time_diff_in_hours(now_datetime(), row.get("modified"))
	crossed = max([st for st in (1, 2, 3) if hours >= thresholds[st]], default=0)
	if not crossed:
		return
	if crossed <= _highest_sent_stage(doctype, row.get("name")):
		return  # this stage (or higher) already sent — Log-derived dedup

	recipients = _stage_recipients(row, crossed, is_po, step_field, rule_field)
	recipients = [u for u in dict.fromkeys(recipients) if u and u not in ("Administrator", "Guest")]
	if not recipients:
		return

	label = "Purchase Order" if is_po else "Material Request"
	hrs = str(int(round(hours)))
	limit = str(thresholds[3])
	for user in recipients:
		name = frappe.db.get_value("User", user, "full_name") or user
		if crossed == 3:
			variables = [name, label, row.get("name"), hrs, limit]
		else:
			variables = [name, label, row.get("name"), hrs]
		send_template(
			user, f"reminder_l{crossed}", variables,
			ref_doctype=doctype, ref_name=row.get("name"),
		)


# --------------------------------------------------------------------------- #
# Recipient resolution (role-based, self-contained — no import from the locked
# approval module). L3 always = leadership.
# --------------------------------------------------------------------------- #
def _stage_recipients(row, stage, is_po, step_field, rule_field):
	if stage == 3:
		return _leadership()
	rule_name = row.get(rule_field)
	if not rule_name:
		return _leadership()
	steps = _steps(rule_name, is_po)
	if not steps:
		return _leadership()
	current = cint(row.get(step_field))
	target_order = current if stage == 1 else _next_order(steps, current)
	step = next((s for s in steps if s["step_order"] == target_order), None)
	if not step or not step.get("role"):
		return _leadership()
	users = _role_users(step["role"])
	return users or _leadership()


def _steps(rule_name, is_po):
	doctype = "TS PO Approval Rule" if is_po else "TS MR Approval Route"
	try:
		doc = frappe.get_cached_doc(doctype, rule_name)
	except Exception:
		return []
	return [
		{"step_order": cint(s.step_order), "role": s.get("role")}
		for s in (doc.get("approval_steps") or [])
	]


def _next_order(steps, current):
	higher = sorted(s["step_order"] for s in steps if s["step_order"] > current)
	return higher[0] if higher else -1


def _role_users(role):
	if not role:
		return []
	return frappe.db.sql_list(
		"""SELECT DISTINCT hr.parent
		   FROM `tabHas Role` hr
		   INNER JOIN `tabUser` u ON u.name = hr.parent
		   WHERE hr.role = %s AND hr.parenttype = 'User'
		     AND u.enabled = 1 AND hr.parent NOT IN ('Administrator', 'Guest')""",
		role,
	)


def _leadership():
	return list(dict.fromkeys(_role_users("CEO") + _role_users("MD")))


# --------------------------------------------------------------------------- #
# Dedup + helpers
# --------------------------------------------------------------------------- #
def _highest_sent_stage(doctype, name):
	"""Highest reminder stage already attempted for this doc (any non-empty Log
	row counts, so sandbox/failed attempts are not retried)."""
	keys = frappe.get_all(
		"TS WhatsApp Log",
		filters={
			"ref_doctype": doctype,
			"ref_name": name,
			"template_key": ["in", list(REMINDER_KEYS)],
		},
		pluck="template_key",
	)
	best = 0
	for k in keys:
		try:
			best = max(best, int(str(k).rsplit("_l", 1)[-1]))
		except Exception:
			continue
	return best


def _enabled():
	return bool(int(frappe.db.get_single_value("TS Settings", "ts_whatsapp_enabled") or 0))


def _log_error(title, message):
	try:
		frappe.log_error(title=("WhatsApp: " + title)[:140], message=message)
	except Exception:
		pass
