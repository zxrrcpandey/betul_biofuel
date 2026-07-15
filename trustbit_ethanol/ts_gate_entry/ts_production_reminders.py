"""Department Add-Production reminder engine — v2.21 P2 (R4, user decision U7).

Runs on the existing */30 cron. For every system-created 'Pending' department
gate entry whose run still waits ('Awaiting Department Production'):

  stage 1..3   L1/L2/L3 escalating reminders to the category's recipients,
               anchored on the entry's own notified_at (thresholds from
               TS Settings ts_production_reminder_l1/l2/l3_hours; code
               fallbacks 4/12/24h — L171/172: unset Singles read 0).
  stage 4      ONE escalation to the run owner (PM) via Notification Log,
               fired an L3-interval after the L3 reminder.
  stage 5+     U7 — reminders KEEP REPEATING at the L3 cadence until the
               department acts, governed by ts_production_reminder_max
               (total dept reminders; 0/unset = UNLIMITED, the user's
               verbatim "multiple reminder all will trigger").

Discipline:
  - FAIL-CLOSED preamble on BOTH kill switches (master + multi-flow) so the
    cron is inert wherever the stack is dark (prod). Deliberately NOT coupled
    to ts_whatsapp_enabled — In-App reminders must survive channel gaps.
  - Dedup key = the monotonic reminder_stage on the dept entry (at most one
    send per stage; repeats gated on last_reminded_at + L3 hours). Reminder
    state lives on the DEPT ENTRY — zero parent-run writes, so the scheduler
    never contends with concurrent department submissions.
  - Sends go through ts_production_notify.notify_category (per-recipient ×
    channel isolation, Lessons 238/276); one bad entry never blocks the rest.
  - Stop conditions are structural (the SQL only selects entries that still
    gate a waiting run of an active category with notified_at set): entry
    leaves Pending, run advances/rejects, category deactivated, switches off.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, now_datetime, time_diff_in_hours

DEPT_DOCTYPE = "TS Production Department Entry"
RUN_DOCTYPE = "TS Production Entry"

SETTING_L1 = "ts_production_reminder_l1_hours"
SETTING_L2 = "ts_production_reminder_l2_hours"
SETTING_L3 = "ts_production_reminder_l3_hours"
SETTING_MAX = "ts_production_reminder_max"

FALLBACK_HOURS = {SETTING_L1: 4.0, SETTING_L2: 12.0, SETTING_L3: 24.0}


def _switch_on(field):
	try:
		return cint(frappe.db.get_single_value("TS Settings", field)) == 1
	except Exception:
		return False  # fail-closed (Lesson 171/172)


def _hours(field):
	try:
		v = flt(frappe.db.get_single_value("TS Settings", field))
	except Exception:
		v = 0.0
	return v if v > 0 else FALLBACK_HOURS[field]


def run_department_production_reminders():
	"""Scheduler entry point (*/30 cron). Inert unless the Multiple flow is live."""
	if not (_switch_on("ts_production_entry_enabled")
			and _switch_on("ts_production_multi_flow_enabled")):
		return

	l1, l2, l3 = _hours(SETTING_L1), _hours(SETTING_L2), _hours(SETTING_L3)
	try:
		rmax = cint(frappe.db.get_single_value("TS Settings", SETTING_MAX))
	except Exception:
		rmax = 0  # 0 = unlimited (U7 default)

	rows = frappe.db.sql("""
		SELECT d.name, d.category, d.production_entry, d.notified_at,
		       d.reminder_stage, d.last_reminded_at,
		       r.owner AS run_owner, r.production_item_name AS item_name
		FROM `tabTS Production Department Entry` d
		JOIN `tabTS Production Entry` r ON r.name = d.production_entry
		JOIN `tabTS Production BOM Category` c ON c.name = d.category
		WHERE d.status = 'Pending'
		  AND r.ts_variance_status = 'Awaiting Department Production'
		  AND c.active = 1
		  AND d.notified_at IS NOT NULL
	""", as_dict=True)
	if not rows:
		return

	now = now_datetime()
	sent = 0
	for d in rows:
		try:
			sent += 1 if _process_entry(d, now, l1, l2, l3, rmax) else 0
		except Exception:
			frappe.clear_messages()
			frappe.log_error(
				title="Dept production reminder failed",
				message="{0}: {1}".format(d.name, frappe.get_traceback()),
			)
	if sent:
		frappe.db.commit()


def _process_entry(d, now, l1, l2, l3, rmax):
	"""Advance one entry's reminder ladder by at most ONE step. Returns True if sent."""
	stage = cint(d.reminder_stage)
	waited_h = time_diff_in_hours(now, get_datetime(d.notified_at))

	if stage < 3:
		threshold = (l1, l2, l3)[stage]
		if waited_h < threshold:
			return False
		_send_dept_reminder(d, stage + 1, waited_h)
		_stamp(d.name, stage + 1, now)
		return True

	# post-L3: everything below runs at the L3 cadence off last_reminded_at
	since_last = time_diff_in_hours(now, get_datetime(d.last_reminded_at or d.notified_at))
	if since_last < l3:
		return False

	if stage == 3:
		# E4 — the ONE owner (PM) escalation; stage 4 is its sentinel
		_send_pm_escalation(d, waited_h)
		_stamp(d.name, 4, now)
		return True

	# stage >= 4 — U7 repeats. Dept sends so far: stages 1..3 = 3, each stage
	# past the sentinel adds one (stage 5 was send #4, etc.).
	dept_sends = stage - 1
	if rmax and dept_sends >= rmax:
		return False  # capped by configuration; the board still shows the stall
	_send_dept_reminder(d, stage + 1, waited_h, repeat=True)
	_stamp(d.name, stage + 1, now)
	return True


def _stamp(name, stage, now):
	frappe.db.set_value(DEPT_DOCTYPE, name,
	                    {"reminder_stage": stage, "last_reminded_at": now},
	                    update_modified=False)


def _send_dept_reminder(d, stage_no, waited_h, repeat=False):
	from trustbit_ethanol.ts_gate_entry.ts_production_notify import notify_category

	safe_run = frappe.utils.escape_html(d.production_entry)
	safe_item = frappe.utils.escape_html(d.item_name or "")
	nth = stage_no if stage_no <= 3 else stage_no - 1  # dept-send ordinal
	tag = _("Reminder L{0}").format(stage_no) if not repeat \
		else _("OVERDUE — reminder #{0}").format(nth)
	notify_category(
		d.category,
		_("{0}: Add Production required — {1}").format(tag, safe_run),
		_("Production run {0} ({1}) is still waiting for your department's "
		  "Add Production — waiting {2} hour(s). The Store Manager release "
		  "cannot proceed until your department adds it."
		  ).format(safe_run, safe_item, int(waited_h)),
		ref_doctype=RUN_DOCTYPE, ref_name=d.production_entry,
	)


def _send_pm_escalation(d, waited_h):
	"""E4 — bell the run owner once (best-effort; Notification Log only)."""
	owner = d.run_owner
	if not owner or owner in ("Administrator", "Guest"):
		return
	try:
		frappe.get_doc({
			"doctype": "Notification Log",
			"for_user": owner,
			"type": "Alert",
			"subject": _("Escalation: {0} has not added production for {1}").format(
				frappe.utils.escape_html(d.category),
				frappe.utils.escape_html(d.production_entry)),
			"email_content": _(
				"Department {0} has not responded for {1} hour(s) despite three "
				"reminders. The run {2} cannot reach the Store Manager release "
				"until they add their production (or an Administrator skips them)."
			).format(frappe.utils.escape_html(d.category), int(waited_h),
			         frappe.utils.escape_html(d.production_entry)),
			"document_type": RUN_DOCTYPE,
			"document_name": d.production_entry,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.clear_messages()
