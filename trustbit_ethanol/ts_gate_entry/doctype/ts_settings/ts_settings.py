import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, add_days, today


class TSSettings(Document):
	def validate(self):
		self._validate_pre_post_dated()
		self._validate_cascade_delete_settings()

	def _validate_cascade_delete_settings(self):
		"""v2.11.0 Token Cascade Delete kill-switch + webhook URL hygiene.

		Rules:
		- If `ts_cascade_delete_enabled = 1`, `ts_cascade_webhook_url` MUST be non-empty
		  AND start with `https://` (no http, no relative paths, no localhost).
		- If webhook URL is provided, `ts_cascade_webhook_secret` MUST also be set.
		- Rate-limit count must be in [1, 100]; rate-limit window must be in [60, 604800] (1min..7d).
		- Only System Manager may flip the kill switch ON (defense-in-depth — already
		  enforced by permlevel=1 on the field).

		Lesson 167 / 170 pattern: only validate when the user is ACTIVELY changing
		these fields (has_value_changed) — saving Settings for an unrelated reason
		doesn't re-block on a pre-existing inconsistency.
		"""
		relevant = (
			"ts_cascade_delete_enabled",
			"ts_cascade_webhook_url",
			"ts_cascade_webhook_secret",
			"ts_cascade_rate_limit_count",
			"ts_cascade_rate_limit_seconds",
			"ts_cascade_revert_window_minutes",
		)
		if not self.is_new() and not any(self.has_value_changed(f) for f in relevant):
			return

		if self.get("ts_cascade_delete_enabled"):
			url = (self.get("ts_cascade_webhook_url") or "").strip()
			if url:
				# Shared SSRF guard (v2.11.1 audit B5) — replaces the old leaky
				# substring blocklist with real DNS-resolution-based IP classification
				# (catches decimal/hex/octal/IPv6 loopback forms the blocklist missed).
				from trustbit_ethanol.ts_gate_entry.cascade_delete_webhook import (
					validate_webhook_url_safe,
				)
				validate_webhook_url_safe(url)
				if not self.get("ts_cascade_webhook_secret"):
					frappe.throw(_("Cascade Delete Webhook Secret is required when webhook URL is set."))

		count = self.get("ts_cascade_rate_limit_count")
		if count is not None:
			try:
				count_i = int(count)
			except (TypeError, ValueError):
				count_i = -1
			if count_i < 1 or count_i > 100:
				frappe.throw(_("Cascade Delete rate-limit count must be between 1 and 100."))

		seconds = self.get("ts_cascade_rate_limit_seconds")
		if seconds is not None:
			try:
				seconds_i = int(seconds)
			except (TypeError, ValueError):
				seconds_i = -1
			if seconds_i < 60 or seconds_i > 604800:
				frappe.throw(_("Cascade Delete rate-limit window must be between 60 seconds and 7 days (604800)."))

		# M2 (v2.11.1) — configurable revert grace window. Default 5 min; an admin
		# may widen it up to 60 min so a financial cascade has a longer recovery
		# window (a financial error rarely surfaces within 5 minutes).
		revert_minutes = self.get("ts_cascade_revert_window_minutes")
		if revert_minutes not in (None, "", 0):
			try:
				rm_i = int(revert_minutes)
			except (TypeError, ValueError):
				rm_i = -1
			if rm_i < 5 or rm_i > 60:
				frappe.throw(_("Cascade Delete revert window must be between 5 and 60 minutes."))

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
