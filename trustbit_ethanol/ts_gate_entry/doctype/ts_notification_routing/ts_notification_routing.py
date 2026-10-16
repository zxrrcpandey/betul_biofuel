# Copyright (c) 2026, Trustbit Software and contributors
# v2.58.0 — one row per role; read by ts_notification_routing.role_recipients().

import frappe
from frappe import _
from frappe.model.document import Document

from trustbit_ethanol.ts_gate_entry.ts_notification_routing import (
	AUTOMATIC_ROLES,
	SELECTED,
	role_holders,
)


class TSNotificationRouting(Document):
	def validate(self):
		if self.role in AUTOMATIC_ROLES:
			frappe.throw(_("{0} is an automatic role and cannot be routed.").format(self.role))
		if frappe.db.get_value("Role", self.role, "disabled"):
			frappe.throw(_("Role {0} is disabled.").format(self.role))

		# De-duplicate; the list is kept in "All role holders" mode so switching
		# back to "Selected users only" restores it.
		seen, rows = set(), []
		for row in self.users or []:
			if row.user and row.user not in seen:
				seen.add(row.user)
				rows.append(row)
		self.set("users", rows)

		if self.routing_mode == SELECTED:
			holders = set(role_holders(self.role))
			stale = [r.user for r in rows if r.user not in holders]
			if stale:
				frappe.throw(_("Untick these users first. They no longer hold the role {0} or are disabled: {1}").format(
					frappe.bold(self.role), ", ".join(stale)))
			if not rows:
				frappe.throw(_("Tick at least one user. A role cannot be muted; use All role holders instead."))

		self.ticked_count = len(rows) if self.routing_mode == SELECTED else 0
