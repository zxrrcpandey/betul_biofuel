"""v2.9.0 Day 6 — QC Yearly Dashboard wiring.

Lesson 173: bench migrate does NOT auto-sync Workspace shortcuts/number_cards
when JSON file changes. We use frappe.reload_doc() for the Quality Lab
workspace so the new "QC Yearly Dashboard" shortcut appears immediately.

Idempotent — safe to re-run.
"""

import frappe


def reload_quality_lab_workspace() -> None:
	"""Force-reload the Quality Lab workspace JSON after migrate."""
	try:
		frappe.reload_doc(
			"ts_gate_entry",
			"workspace",
			"quality_lab",
			force=True,
		)
		frappe.db.commit()
		print("[v2.9.0] setup_qc_dashboard: reloaded Quality Lab workspace.")
	except Exception as e:
		# Log but don't break migrate.
		frappe.log_error(
			message=f"reload_quality_lab_workspace failed: {e}",
			title="v2.9.0 setup_qc_dashboard",
		)
