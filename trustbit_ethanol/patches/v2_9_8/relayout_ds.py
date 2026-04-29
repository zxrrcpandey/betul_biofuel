# v2.9.8 — TS Deduction Sheet relayout
#
# Backfills the new derived header fields + populates 3 standard deduction lines
# (Quality / Dhalta / Unloading) on EXISTING DRAFT DS rows so they pick up the
# new layout immediately. Submitted/cancelled DS rows are left alone (data frozen).
#
# Idempotent via Singles flag `ts_v298_ds_relayout_done`.
# No-op on production (0 DS rows there).

import frappe


def execute():
	if frappe.db.get_default("ts_v298_ds_relayout_done"):
		return

	# Find draft DSes that don't yet have grn_amount populated
	candidates = frappe.get_all(
		"TS Deduction Sheet",
		filters={"docstatus": 0},
		fields=["name", "grn_amount"],
	)
	skipped, processed, failed = 0, 0, 0
	for row in candidates:
		if row.grn_amount and float(row.grn_amount or 0) > 0:
			skipped += 1
			continue
		try:
			doc = frappe.get_doc("TS Deduction Sheet", row.name)
			# Run the v2.9.8 fetch helpers if available
			if hasattr(doc, "_auto_fetch_v298_fields"):
				doc._auto_fetch_v298_fields()
			# Populate lines if empty
			if not doc.deductions and hasattr(doc, "_auto_populate_deduction_lines"):
				doc._auto_populate_deduction_lines()
			# Recompute via standard validate path
			doc.flags.ignore_permissions = True
			doc.save()
			processed += 1
		except Exception as e:
			failed += 1
			frappe.log_error(
				message=f"v2.9.8 backfill failed for DS {row.name}: {e}",
				title="patches.v2_9_8.relayout_ds",
			)
			continue

	frappe.db.set_default("ts_v298_ds_relayout_done", "1")
	frappe.db.commit()
	frappe.logger().info(
		f"v2.9.8 DS relayout complete — processed={processed} "
		f"skipped={skipped} failed={failed}"
	)
