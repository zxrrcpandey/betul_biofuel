import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, add_days, today


class TSSettings(Document):
	def validate(self):
		self._validate_pre_post_dated()

	def _validate_pre_post_dated(self):
		"""Guard against pre-enable window exceeding max_backdate_days policy (Lesson 167).

		Only fires when the user is ACTIVELY changing one of the pre-enable fields.
		Saving TS Settings for an unrelated change (e.g., g2_print_mode) with a
		pre-existing bad pre_post_dated_from won't be blocked — the runtime clamp
		in check_post_dated_access() already handles stale data. (Lesson 170)
		"""
		if not self.get("enable_pre_post_dated"):
			return
		if not self.pre_post_dated_from or not self.pre_post_dated_to:
			return

		# Only validate if one of the relevant fields was actually changed on this save.
		# On insert (no previous version), has_value_changed() returns True for all
		# fields — that's the correct behaviour (first-time enable must meet policy).
		relevant = ("enable_pre_post_dated", "pre_post_dated_from",
		            "pre_post_dated_to", "max_backdate_days")
		if not self.is_new() and not any(self.has_value_changed(f) for f in relevant):
			return

		pre_from = getdate(self.pre_post_dated_from)
		pre_to = getdate(self.pre_post_dated_to)
		today_d = getdate()

		if pre_from > pre_to:
			frappe.throw(_("Pre-Enable From Date cannot be after To Date"))

		if pre_from > today_d:
			frappe.throw(_("Pre-Enable From Date cannot be a future date"))

		max_days = self.get("max_backdate_days")
		try:
			max_days = int(max_days) if max_days is not None else 30
		except (TypeError, ValueError):
			max_days = 30

		if max_days > 0:
			min_allowed = getdate(add_days(today(), -max_days))
			if pre_from < min_allowed:
				frappe.throw(
					_("Pre-Enable From Date ({0}) exceeds the {1}-day backdate policy. "
					  "Earliest allowed: {2}. Either increase max_backdate_days or use a narrower window.").format(
						pre_from, max_days, min_allowed
					)
				)
