# Copyright (c) 2026, Trustbit Software and contributors
# RGP Phase C — overdue engine (v2.49.0): aging flag, daily digest, Sec 143
# statutory alarms.
#
# Deliberately a verbatim-shape copy of the v2.45.0 grain digest
# (ts_grain_defer.remind_grain_awaiting_po_link) — the house's proven
# scheduler pattern:
# - Ages computed in PYTHON with now_datetime()/today() — the MySQL session
#   runs UTC while values are stored in site time (5.5h skew; known shipped-
#   bug class). Half-up day rounding matches the register report's tiers.
# - Dedupe is LOG-DERIVED per recipient (Notification Log subject-prefix
#   within a 20h window — 20 not 24 so scheduler drift can never skip a day).
#   No new fields, no migrate.
# - Digest subject prefixes stay UNTRANSLATED: the dedupe LIKE matches them
#   literally; a translated subject would silently disable the guard.
# - Fully fail-soft — a scheduler job must never raise (L238) — and channel
#   is bell-only: prod outbound email is dead (P1), and web push cannot reach
#   Stores (predictor: _PUSH_DOCTYPES excludes RGP).
# - Confidentiality-safe: pass name / vendor NAME / days only — never rupee
#   values (permlevel-1 discipline extends to notification text).
#
# Jobs (hooks.py):
# - flip_overdue_flags        → the existing */30 bucket
# - remind_rgp_overdue        → cron "0 9 * * *" (daily 09:00 site time)
# - scan_sec143_alarms        → same daily slot (runs after the digest)

import frappe
from frappe import _
from frappe.utils import add_to_date, escape_html, getdate, now_datetime, today

# Statuses in which material is outstanding and aging applies.
_AGING_STATUSES = ("Issued", "Out of Plant", "At Vendor", "Partially Returned")

_DIGEST_SUBJECT_PREFIX = "RGP passes overdue"
_SEC143_SUBJECT_PREFIX = "RGP Sec 143 window closing"
_DIGEST_WINDOW_HOURS = 20
_DIGEST_MAX_ROWS = 10


def _stores_recipients():
	return frappe.db.sql_list(
		"""SELECT DISTINCT hr.parent FROM `tabHas Role` hr
		   JOIN `tabUser` u ON u.name = hr.parent AND u.enabled = 1
		   WHERE hr.parenttype = 'User'
		     AND hr.role IN ('Stores User', 'Stores Manager')
		   ORDER BY hr.parent LIMIT 30""")


def _already_notified(recipients, subject_prefix):
	"""Per-recipient daily dedupe (log-derived, grain-digest doctrine)."""
	window_start = add_to_date(now_datetime(), hours=-_DIGEST_WINDOW_HOURS)
	return set(frappe.get_all(
		"Notification Log",
		filters={
			"for_user": ("in", recipients),
			"subject": ("like", subject_prefix + "%"),
			"creation": (">", window_start),
		},
		pluck="for_user",
	))


def _bell_once(recipients, subject, body, doctype=None, docname=None,
		dedupe_prefix=None):
	"""dedupe_prefix defaults to the pre-dash subject prefix; per-PASS alarms
	must pass a prefix that includes the pass name, or the first pass's alarm
	would suppress every other pass's for the same recipient that day."""
	already = _already_notified(recipients,
		dedupe_prefix or subject.split(" — ")[0])
	for user in recipients:
		if user in already:
			continue
		try:
			frappe.get_doc({
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"document_type": doctype or "TS Returnable Gate Pass",
				"document_name": docname or "",
				"subject": subject,
				"email_content": body,
			}).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="RGP digest bell failed",
				message=frappe.get_traceback())
			frappe.clear_messages()


def _overdue_rows():
	"""Open passes past their expected return — day math in Python (UTC skew).
	Half-up rounding matches the register's age_days convention."""
	rows = frappe.db.sql(
		"""SELECT name, supplier_name, expected_return_date, total_balance, status
		   FROM `tabTS Returnable Gate Pass`
		   WHERE docstatus = 1 AND status IN %(sts)s
		     AND expected_return_date IS NOT NULL
		   ORDER BY expected_return_date ASC
		   LIMIT 200""",
		{"sts": _AGING_STATUSES}, as_dict=True)
	out = []
	for r in rows:
		days = (getdate(today()) - getdate(r.expected_return_date)).days
		if days > 0:
			r.days_over = days
			out.append(r)
	return out


def flip_overdue_flags():
	"""*/30 scheduler — keep is_overdue aligned with the calendar, both
	directions (an extended expected date clears the flag). Direct SQL:
	runs as the scheduler, and is_overdue is a control-plane field whose
	only sanctioned writer is this job. NOTE the L428-class trap this
	deliberately avoids: no doc.save(), so no hook interplay at all."""
	try:
		frappe.db.sql(
			"""UPDATE `tabTS Returnable Gate Pass`
			   SET is_overdue = 1
			   WHERE docstatus = 1 AND status IN %(sts)s
			     AND expected_return_date < %(today)s AND IFNULL(is_overdue, 0) = 0""",
			{"sts": _AGING_STATUSES, "today": today()})
		frappe.db.sql(
			"""UPDATE `tabTS Returnable Gate Pass`
			   SET is_overdue = 0
			   WHERE docstatus = 1
			     AND (status NOT IN %(sts)s OR expected_return_date >= %(today)s)
			     AND IFNULL(is_overdue, 0) = 1""",
			{"sts": _AGING_STATUSES, "today": today()})
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="RGP overdue flag scan failed",
			message=frappe.get_traceback())
		frappe.clear_messages()


def remind_rgp_overdue():
	"""Daily 09:00 digest: ONE bell per store-team member (+ each pass's
	requester) listing every overdue pass. Repeats daily until closed;
	per-recipient dedupe via the untranslated subject prefix."""
	try:
		due = _overdue_rows()
		if not due:
			return
		recipients = set(_stores_recipients())
		for r in due:
			owner = frappe.db.get_value(
				"TS Returnable Gate Pass", r.name, "requested_by")
			if owner and frappe.db.get_value("User", owner, "enabled"):
				recipients.add(owner)
		recipients = sorted(recipients)
		if not recipients:
			return

		subject = "{0} — {1} pass(es), oldest {2}d".format(
			_DIGEST_SUBJECT_PREFIX, len(due), due[0].days_over)
		lines = []
		for r in due[:_DIGEST_MAX_ROWS]:
			lines.append(
				"<tr><td>{0}</td><td>{1}</td><td>{2}</td><td><b>{3}d</b></td></tr>"
				.format(escape_html(r.name), escape_html(r.supplier_name or ""),
					escape_html(r.status), r.days_over))
		more = len(due) - _DIGEST_MAX_ROWS
		body = _(
			"These returnable gate passes are past their expected return date:"
			"<br><br><table border='1' cellpadding='4' cellspacing='0'>"
			"<tr><th>Pass</th><th>Vendor</th><th>Status</th><th>Overdue</th></tr>"
			"{0}</table>{1}<br>Chase the vendor, record the return, or raise a "
			"close-short request."
		).format("".join(lines),
			_("<br>… and {0} more.").format(more) if more > 0 else "")
		_bell_once(recipients, subject, body, docname=due[0].name)
	except Exception:
		frappe.log_error(title="RGP overdue digest failed",
			message=frappe.get_traceback())
		frappe.clear_messages()


def scan_sec143_alarms():
	"""Daily: passes whose Sec 143 alarm date (challan + 10 months) has
	arrived and which still hold a balance — the statutory 1y/3y clock is
	closing; overrun = deemed supply taxed from the CHALLAN date. One bell
	per pass per recipient per day (same dedupe doctrine); Accounts joins
	the recipients because the cure is theirs (deemed-supply invoice)."""
	try:
		rows = frappe.db.sql(
			"""SELECT name, supplier_name, sec143_due_date, total_balance
			   FROM `tabTS Returnable Gate Pass`
			   WHERE docstatus = 1 AND status IN %(sts)s
			     AND sec143_alarm_date IS NOT NULL
			     AND sec143_alarm_date <= %(today)s
			     AND IFNULL(total_balance, 0) > 0
			   ORDER BY sec143_due_date ASC
			   LIMIT 50""",
			{"sts": _AGING_STATUSES, "today": today()}, as_dict=True)
		if not rows:
			return
		accounts = frappe.db.sql_list(
			"""SELECT DISTINCT hr.parent FROM `tabHas Role` hr
			   JOIN `tabUser` u ON u.name = hr.parent AND u.enabled = 1
			   WHERE hr.parenttype = 'User' AND hr.role = 'Accounts Manager'
			   ORDER BY hr.parent LIMIT 30""")
		recipients = sorted(set(_stores_recipients()) | set(accounts))
		if not recipients:
			return
		for r in rows:
			subject = "{0} — {1} (deadline {2})".format(
				_SEC143_SUBJECT_PREFIX, r.name,
				frappe.utils.formatdate(r.sec143_due_date, "dd-MM-yyyy"))
			body = _(
				"Pass {0} ({1}) still holds an outstanding balance of {2}. "
				"The GST Section 143 return window closes on <b>{3}</b> — an "
				"overrun becomes a deemed supply taxed from the challan date "
				"with Section 50 interest. Recover the material or plan the "
				"deemed-supply invoice."
			).format(escape_html(r.name), escape_html(r.supplier_name or ""),
				r.total_balance,
				frappe.utils.formatdate(r.sec143_due_date, "dd-MM-yyyy"))
			_bell_once(recipients, subject, body, docname=r.name,
				dedupe_prefix="{0} — {1}".format(_SEC143_SUBJECT_PREFIX, r.name))
	except Exception:
		frappe.log_error(title="RGP Sec143 alarm scan failed",
			message=frappe.get_traceback())
		frappe.clear_messages()
