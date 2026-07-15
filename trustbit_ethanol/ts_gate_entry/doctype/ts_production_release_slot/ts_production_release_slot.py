# Copyright (c) 2026, Trustbit Software
# TS Production Release Slot — one department's portion of a Single-flow release
# (v2.21, client request 13 Jul). System-created at submit_for_release; the
# category's recipients enter ACTUAL released quantities via the whitelisted
# endpoint; the Store Manager's approve_release is blocked until every slot is
# Released and then runs the unchanged completion chain (SM stays the final gate).

import frappe
from frappe import _
from frappe.model.document import Document

# Control-plane fields — writable only via the endpoints' db_set (Lesson 162).
CONTROL_FIELDS = ["status", "released_by", "released_at", "notified_at",
                  "production_entry", "category"]


class TSProductionReleaseSlot(Document):
	def before_save(self):
		from trustbit_ethanol.ts_gate_entry.ts_po_approval import _block_gate_field_tampering
		_block_gate_field_tampering(self, CONTROL_FIELDS)
