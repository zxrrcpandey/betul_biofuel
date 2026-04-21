"""v2.8.6.1 + v2.8.6.2: Backfill ts_po_reference on existing Purchase Invoices.

v2.8.6 added a validate hook that auto-populates ts_po_reference from
items.purchase_order. But existing PIs (docstatus=0 or 1) haven't been
re-saved since the hook was added, so their ts_po_reference column is
NULL. Accounts users see an empty field on every historical PI.

This patch walks all PIs that have at least one item with a purchase_order
link and backfills ts_po_reference via direct SQL (no doc.save, so no
modified timestamp bump, no validate re-run, no permission issues).

## v2.8.6.2 defensive guard

On a fresh prod install where v2.8.6 + v2.8.6.1 deploy together, the
`tabPurchase Invoice.ts_po_reference` COLUMN may not exist yet when this
patch runs — post_model_sync patches fire BEFORE after_migrate hooks,
and the column is created by `setup_pi_po_ref.seed_pi_po_ref_field()`
which lives in after_migrate.

Without a guard, the patch raises `OperationalError 1054 Unknown column`
which aborts the migrate and prevents after_migrate hooks from running
(the seed never creates the column, the field never appears, every
subsequent migrate hits the same error).

Defensive check: if the column doesn't exist yet, skip the backfill.
It will be re-attempted on next migrate when the column has been added
(patches are re-runnable until they complete successfully).

Idempotent — running twice produces the same result. Safe to re-run on
every migrate; uses UPDATE WHERE to only touch rows that need it.
"""

import frappe


def _column_exists(table: str, column: str) -> bool:
	"""Return True if the given column exists in the given table.
	Uses information_schema query rather than SHOW COLUMNS so the check
	itself cannot raise OperationalError on a missing column."""
	try:
		rows = frappe.db.sql(
			"""SELECT 1 FROM information_schema.COLUMNS
			   WHERE TABLE_SCHEMA = DATABASE()
			     AND TABLE_NAME = %s AND COLUMN_NAME = %s""",
			(table, column),
		)
		return bool(rows)
	except Exception:
		return False


def execute():
	# v2.8.6.2: on a fresh install, the column may not exist yet — skip
	# gracefully and rely on next migrate (after seed_pi_po_ref_field
	# creates the Custom Field) to backfill.
	if not _column_exists("tabPurchase Invoice", "ts_po_reference"):
		frappe.logger().info(
			"[backfill_pi_po_reference] ts_po_reference column not present yet; "
			"skipping. Will re-run on next migrate after seed creates the field."
		)
		return
	# Find PIs with populated items.purchase_order where ts_po_reference is
	# NULL or out-of-date. For simplicity we backfill all PIs where
	# ts_po_reference is NULL — don't re-touch rows that already have a value
	# (those were computed by the live hook after v2.8.6 deployed).
	rows = frappe.db.sql("""
		SELECT pi.name
		FROM `tabPurchase Invoice` pi
		INNER JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
		WHERE pii.purchase_order IS NOT NULL AND pii.purchase_order != ''
		  AND (pi.ts_po_reference IS NULL OR pi.ts_po_reference = '')
		GROUP BY pi.name
	""", as_dict=True)

	updated = 0
	for r in rows:
		po_rows = frappe.db.sql("""
			SELECT DISTINCT purchase_order, MIN(idx) AS first_idx
			FROM `tabPurchase Invoice Item`
			WHERE parent = %s AND purchase_order IS NOT NULL AND purchase_order != ''
			GROUP BY purchase_order
			ORDER BY first_idx
		""", (r.name,), as_dict=True)
		ordered = [p.purchase_order for p in po_rows]
		value = ", ".join(ordered)
		if value:
			frappe.db.set_value(
				"Purchase Invoice", r.name, "ts_po_reference", value,
				update_modified=False,
			)
			updated += 1

	frappe.db.commit()
	frappe.logger().info(
		f"[backfill_pi_po_reference] v2.8.6.1 — backfilled ts_po_reference on {updated} PIs"
	)
