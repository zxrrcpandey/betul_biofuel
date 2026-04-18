"""v2.8.2: Add unique index on TS Weighbridge Log.rst_number.

Skips if duplicates exist (manual remediation required). Uses sql_ddl() because
DROP/ALTER INDEX can trigger ImplicitCommitError in patch transaction (Lesson 181).
"""

import frappe


def execute():
	duplicates = frappe.db.sql("""
		SELECT rst_number, COUNT(*) AS cnt
		FROM `tabTS Weighbridge Log`
		WHERE rst_number IS NOT NULL AND rst_number != ''
		GROUP BY rst_number
		HAVING COUNT(*) > 1
	""", as_dict=True)

	if duplicates:
		frappe.log_error(
			message=f"RST duplicates found: {duplicates}. Manual remediation required before unique index can be applied.",
			title="v2.8.2 RST unique index",
		)
		return

	existing = frappe.db.sql("""
		SHOW INDEX FROM `tabTS Weighbridge Log` WHERE Key_name = 'rst_number_unique_idx'
	""")
	if existing:
		return

	frappe.db.commit()
	frappe.db.sql_ddl(
		"ALTER TABLE `tabTS Weighbridge Log` ADD UNIQUE INDEX rst_number_unique_idx (rst_number)"
	)
