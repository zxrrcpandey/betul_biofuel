import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, add_days, today


class TSSettings(Document):
	def validate(self):
		self._validate_pre_post_dated()
		self._validate_cascade_delete_settings()
		self._validate_bom_configs()

	def on_update(self):
		self._sync_buying_allow_multiple_items()

	def _sync_buying_allow_multiple_items(self):
		"""Mirror `ts_allow_multiple_items` -> Buying Settings.allow_multiple_items.

		`allow_multiple_items` is the field ERPNext's native guard reads
		(erpnext/buying/utils.py:validate_for_items) to decide whether the same
		item may appear in more than one row on Material Request / Purchase Order
		(and RFQ / Supplier Quotation). Surfacing the control on TS Settings keeps
		the team's config in one place; this keeps the underlying Buying Settings
		flag consistent. 1:1 polarity (checked = allow = 1).

		Only syncs when the flag actually changed (Lesson 170) so unrelated
		TS Settings saves don't churn the Buying Settings cache. PR / PI have no
		native duplicate guard, so this intentionally does not touch them.
		"""
		if not self.is_new() and not self.has_value_changed("ts_allow_multiple_items"):
			return
		desired = cint(self.get("ts_allow_multiple_items"))
		current = cint(frappe.db.get_single_value("Buying Settings", "allow_multiple_items"))
		if current != desired:
			frappe.db.set_single_value("Buying Settings", "allow_multiple_items", desired)
			frappe.clear_cache(doctype="Buying Settings")

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

	def _validate_bom_configs(self):
		"""v2.41 per-BOM production config (Single-flow release source + cost centre).

		Frappe does NOT run child-doctype controllers on a parent Settings save, so
		each row's TSProductionBOMConfig.validate() is invoked explicitly here —
		remove this loop and every row-level check goes inert.

		Change-gated per Lesson 170 — but has_value_changed() is unreliable for a
		Table field (child Document lists never compare equal), so the gate compares
		a value signature instead: an unrelated Settings save must never start
		throwing on pre-existing rows (e.g. a warehouse that became department-
		claimed after the row was saved)."""
		rows = self.get("ts_production_bom_configs") or []

		# The duplicate scan ALWAYS runs (cheap, and a duplicate is wrong no matter
		# when it appeared): the REST child-PUT path re-saves the parent AFTER the
		# child row is already in the DB, so the change gate below would skip it.
		seen = {}
		for row in rows:
			if row.bom and row.bom in seen:
				frappe.throw(
					_("Rows #{0} and #{1} both configure BOM {2} — keep exactly one "
					  "row per BOM.").format(
						seen[row.bom], row.idx, frappe.utils.escape_html(row.bom)),
					title=_("Duplicate BOM Config"))
			if row.bom:
				seen[row.bom] = row.idx

		def _sig(rowlist):
			return [(r.get("bom"), r.get("source_warehouse"), r.get("cost_center"),
			         r.get("releaser_user"), r.get("releaser_role"))
			        for r in (rowlist or [])]

		if not self.is_new():
			before = self.get_doc_before_save()
			if before and _sig(before.get("ts_production_bom_configs")) == _sig(rows):
				return

		for row in rows:
			row.validate()
