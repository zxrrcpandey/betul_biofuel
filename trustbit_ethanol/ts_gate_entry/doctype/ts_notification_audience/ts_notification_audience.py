# Copyright (c) 2026, Trustbit Software and contributors
# v2.59.0 — who receives each notification + the skip list; read by
# ts_notification_routing.audience() / role_recipients(). The event rows are
# code-owned (EVENTS, seeded by notification_center_api); only the ticks and
# the skip list are the admin's.

import frappe
from frappe import _
from frappe.model.document import Document

from trustbit_ethanol.ts_gate_entry.ts_notification_routing import (
	COLUMNS,
	EVENTS,
	narrow,
	role_holders,
)

# Single-role approvers outside the PO/MR approval step tables.
_APPROVER_ROLES = ("CEO", "MD", "Managing Director", "Grain Manager", "Stores Manager")


class TSNotificationAudience(Document):
	def validate(self):
		seen, rows = set(), []
		for row in self.events or []:
			spec = EVENTS.get(row.event)
			if not spec or row.event in seen:
				continue
			seen.add(row.event)
			row.applies = ",".join(spec["applies"])  # from the code, never the row
			for col in COLUMNS:
				if col not in spec["applies"]:
					row.set(col, 0)
			if not any(row.get(col) for col in COLUMNS):
				frappe.throw(_("Row {0}: tick at least one group for {1}, or nobody is told.").format(
					row.idx, frappe.bold(spec["label"])))
			rows.append(row)
		self.set("events", rows)
		self._validate_skip_list()

	def _validate_skip_list(self):
		seen, rows = set(), []
		for row in self.skip_accounts or []:
			if row.user and row.user not in seen:
				seen.add(row.user)
				rows.append(row)
		self.set("skip_accounts", rows)

		before = self.get_doc_before_save()
		if not seen or (before and {r.user for r in before.skip_accounts or []} == seen):
			return
		# role_recipients() never notifies nobody, so a role whose every notified
		# holder is skipped would quietly get them back: refuse it here instead.
		roles = set(_APPROVER_ROLES)
		for table in ("TS PO Approval Step", "TS MR Approval Step"):
			roles.update(frappe.db.sql_list(
				"SELECT DISTINCT role FROM `tab{0}` WHERE IFNULL(role, '') != ''".format(table)))
		stranded = sorted(r for r in roles if (notified := narrow(r, role_holders(r)))
			and all(u in seen for u in notified))
		if stranded:
			frappe.throw(_("Nobody else would be notified for these approval roles: {0}. Take an account off the skip list, or give the role to someone else first.").format(
				", ".join(frappe.bold(r) for r in stranded)))
