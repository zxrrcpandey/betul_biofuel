"""v2.8.6.1: Backfill ts_po_reference on existing Purchase Invoices.

v2.8.6 added a validate hook that auto-populates ts_po_reference from
items.purchase_order. But existing PIs (docstatus=0 or 1) haven't been
re-saved since the hook was added, so their ts_po_reference column is
NULL. Accounts users see an empty field on every historical PI.

This patch walks all PIs that have at least one item with a purchase_order
link and backfills ts_po_reference via direct SQL (no doc.save, so no
modified timestamp bump, no validate re-run, no permission issues).

Idempotent — running twice produces the same result. Safe to re-run on
every migrate; uses UPDATE WHERE to only touch rows that need it.
"""

import frappe


def execute():
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
