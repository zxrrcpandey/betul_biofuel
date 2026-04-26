"""v2.9.1 — Strip ts_two_pass_gates_enabled flag and make two-pass MANDATORY.

After v2.8.3 introduced the two-pass gate flow behind a feature flag and
v2.8.3.1 stabilised the WB mirror behavior, the flow has been live for ~10
days on production with zero rollbacks. v2.9.1 removes the kill switch:

  - G1 + G2 are permanent physical gates. Code that branched on the flag
    is simplified to "always on".
  - Token rows still at the legacy pre-G2 statuses ('PO Linked',
    'G1 Entered') are auto-advanced to 'G2 Entered' — they are still
    operationally pre-weighing. The Weighbridge eligibility query in
    api.get_weighbridge_tokens drops 'PO Linked' from its allow-list, so
    these tokens would otherwise become invisible to the WB dropdown.
  - Each migrated token gets an Info comment for audit trail.
  - The TS Settings Single row for `ts_two_pass_gates_enabled` is deleted
    so a stale `1` doesn't linger; the JSON field is removed in the same
    commit, so subsequent migrate runs cannot recreate it.

Idempotency: a hidden Check field `ts_two_pass_cleanup_done` (permlevel=1)
is set to 1 once the patch has succeeded. Re-running migrate is a no-op.
"""

import frappe


PRE_G2_STATUSES = ("PO Linked", "G1 Entered")
TARGET_STATUS = "G2 Entered"


def execute():
	# Idempotency guard — patch already ran successfully.
	try:
		already_done = frappe.db.get_single_value("TS Settings", "ts_two_pass_cleanup_done")
	except Exception:
		already_done = 0
	if already_done:
		print("[v2.9.1] strip_two_pass_flag: already complete — skipping.")
		return

	# 1. Find every token still at a pre-G2 status. Restrict to Material —
	#    Gate Pass uses gate_pass_status, never the new values.
	rows = frappe.db.sql(
		"""
		SELECT name, status
		FROM `tabTS Token`
		WHERE entry_type = 'Material'
		  AND status IN %(statuses)s
		""",
		{"statuses": PRE_G2_STATUSES},
		as_dict=True,
	)

	advanced_po_linked = 0
	advanced_g1_entered = 0

	for r in rows:
		prev = r.status
		try:
			# Direct UPDATE (no doc save -> avoids before_save tamper guards
			# / approval validators / hooks). add_comment preserves the audit
			# trail — visible in the Activity tab on every Token form.
			frappe.db.set_value("TS Token", r.name, "status", TARGET_STATUS, update_modified=False)
			frappe.get_doc("TS Token", r.name).add_comment(
				"Info",
				f"v2.9.1 migration: status auto-advanced from '{prev}' to '{TARGET_STATUS}' "
				f"(two-pass gate flow is now mandatory; flag removed)."
			)
			if prev == "PO Linked":
				advanced_po_linked += 1
			else:
				advanced_g1_entered += 1
		except Exception as e:
			frappe.log_error(
				message=f"strip_two_pass_flag: token {r.name} ({prev}) advance failed: {e}",
				title="v2.9.1 strip_two_pass_flag",
			)

	# 2. Delete the obsolete Singles row for the kill switch. Safe even if
	#    the row doesn't exist (DELETE is a no-op on empty match).
	try:
		frappe.db.delete(
			"Singles",
			{"doctype": "TS Settings", "field": "ts_two_pass_gates_enabled"},
		)
	except Exception as e:
		frappe.log_error(
			message=f"strip_two_pass_flag: Singles cleanup failed: {e}",
			title="v2.9.1 strip_two_pass_flag",
		)

	# 3. Mark patch complete via hidden idempotency flag. Field is created
	#    by setup_two_pass_cleanup_flag in after_migrate — if it doesn't
	#    exist yet (first-run race), we just skip the marker. The DB UPDATE
	#    above already advanced the tokens, so a second run is harmless.
	try:
		if frappe.db.has_column("tabSingles", "value"):
			# Use Single update path so it survives clear_cache.
			settings = frappe.get_single("TS Settings")
			if hasattr(settings, "ts_two_pass_cleanup_done"):
				settings.db_set("ts_two_pass_cleanup_done", 1, update_modified=False)
	except Exception as e:
		frappe.log_error(
			message=f"strip_two_pass_flag: idempotency flag set failed: {e}",
			title="v2.9.1 strip_two_pass_flag",
		)

	frappe.db.commit()

	print(
		f"[v2.9.1] strip_two_pass_flag: advanced {advanced_po_linked} PO Linked + "
		f"{advanced_g1_entered} G1 Entered tokens to G2 Entered "
		f"(scanned={len(rows)})."
	)
