# v2.9.12 Sprint 2 — Acknowledgment doctype controller.
#
# Issued when an admin chooses to suppress a Health Check finding for a
# bounded period (TTL ≤ 90 days). Validators consult this doctype on every
# run and skip issues whose `issue_id` has an unexpired ack row.
import datetime

import frappe
from frappe.model.document import Document


MAX_SUPPRESS_DAYS = 90
MIN_REASON_CHARS = 10


class TSHealthCheckAcknowledgment(Document):
	def validate(self):
		# 1. suppressed_until must be in the future and within TTL cap
		if not self.suppressed_until:
			frappe.throw("Suppressed Until is required.")
		try:
			until = (
				self.suppressed_until
				if isinstance(self.suppressed_until, datetime.date)
				else datetime.date.fromisoformat(str(self.suppressed_until))
			)
		except Exception:
			frappe.throw("Suppressed Until must be a valid date (YYYY-MM-DD).")

		today = datetime.date.today()
		if until <= today:
			frappe.throw("Suppressed Until must be a future date.")
		max_until = today + datetime.timedelta(days=MAX_SUPPRESS_DAYS)
		if until > max_until:
			frappe.throw(
				f"Suppressed Until cannot be more than {MAX_SUPPRESS_DAYS} days from today "
				f"({max_until.isoformat()}). Re-acknowledge after expiry if still applicable."
			)

		# 2. Reason must be at least N chars (forces meaningful note)
		reason = (self.reason or "").strip()
		if len(reason) < MIN_REASON_CHARS:
			frappe.throw(
				f"Reason must be at least {MIN_REASON_CHARS} characters "
				f"(currently {len(reason)})."
			)

		# 3. issue_id sanity — must be non-empty and not whitespace-only
		issue_id = (self.issue_id or "").strip()
		if not issue_id:
			frappe.throw("Issue ID is required.")
