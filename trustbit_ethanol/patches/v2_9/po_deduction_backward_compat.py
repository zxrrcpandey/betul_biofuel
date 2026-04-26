"""v2.9.0 Day 5C — Existing PO backward-compat patch.

Logs a warning for every non-cancelled grain Purchase Order created BEFORE
v2.9.0 that has no `ts_deduction_template` set. We do NOT auto-fill — the
template choice is business-sensitive (which template? Maize vs Wheat vs
Husk?). User edits PO on next change and selects.

Non-grain POs: full no-op (deduction terms not applicable).

Idempotent: safe to re-run; only logs (no DB writes).
"""

import frappe


GRAIN_ITEM_GROUPS = ("Grain", "Maize", "Wheat", "Husk", "Rice Husk")


def _is_grain_po(po_name: str) -> bool:
	"""A PO is grain-class if ANY of its items belong to a grain item_group.

	We use raw SQL JOIN to avoid loading every PO doc.
	"""
	rows = frappe.db.sql(
		"""
		SELECT 1
		FROM `tabPurchase Order Item` poi
		LEFT JOIN `tabItem` it ON it.name = poi.item_code
		WHERE poi.parent = %s
		  AND it.item_group IN %s
		LIMIT 1
		""",
		(po_name, GRAIN_ITEM_GROUPS),
	)
	return bool(rows)


def execute():
	# Verify the field exists (created by v2.9.0 setup_po_deduction_terms.py).
	if not frappe.db.has_column("Purchase Order", "ts_deduction_template"):
		print(
			"[v2.9.0] po_deduction_backward_compat: ts_deduction_template column missing — "
			"setup_po_deduction_terms.seed_po_deduction_terms must run first. Skipping."
		)
		return

	# Candidate POs: not cancelled, no template set.
	rows = frappe.db.sql(
		"""
		SELECT name, status, supplier, transaction_date
		FROM `tabPurchase Order`
		WHERE COALESCE(ts_deduction_template, '') = ''
		  AND status NOT IN ('Cancelled', 'Closed')
		""",
		as_dict=True,
	)

	if not rows:
		print("[v2.9.0] po_deduction_backward_compat: nothing to scan.")
		return

	grain_no_template = 0
	non_grain = 0
	for r in rows:
		if _is_grain_po(r.name):
			grain_no_template += 1
			# Log each one — non-fatal. Procurement team will see in Error Log.
			frappe.log_error(
				message=(
					f"[v2.9.0 backward-compat] Grain Purchase Order {r.name} "
					f"(supplier={r.supplier}, txn_date={r.transaction_date}, status={r.status}) "
					"has no ts_deduction_template. Pick a TS Deduction Master template on next edit. "
					"This is informational only — POs continue to function."
				),
				title="v2.9.0 PO grain backward-compat",
			)
		else:
			non_grain += 1

	# No DB writes — pure scan.
	print(
		f"[v2.9.0] po_deduction_backward_compat: scanned={len(rows)} "
		f"grain_no_template={grain_no_template} non_grain={non_grain} (no rows changed)"
	)
