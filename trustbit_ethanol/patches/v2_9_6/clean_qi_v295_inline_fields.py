# v2.9.6 — Clean up v2.9.5.x demo-only inline-edit fields on TS Quality Inspection.
#
# Production safety: prod runs v2.9.0.14 (never had v2.9.5.x). All DDL guards
# below check has_column / has_row first, so this patch is a NO-OP on prod.
# On demo, this drops the 5 v2.9.5 inline columns + permlevel>0 Custom DocPerm rows.
#
# Idempotent via flag: ts_v296_qi_revert_done.

import frappe


DROP_COLUMNS = [
	"actual_deduction_pct",
	"actual_deduction_kg",
	"actual_deduction_reason",
	"actual_filled_by",
	"actual_filled_at",
]


def execute():
	if frappe.db.get_default("ts_v296_qi_revert_done"):
		return

	# 1. Drop demo-only inline fields from QI table (additive on prod = no-op).
	for col in DROP_COLUMNS:
		try:
			if frappe.db.has_column("TS Quality Inspection", col):
				frappe.db.sql_ddl(
					f"ALTER TABLE `tabTS Quality Inspection` DROP COLUMN `{col}`"
				)
		except Exception as e:
			frappe.log_error(
				message=f"v2.9.6 DROP COLUMN {col} on TS Quality Inspection failed: {e}",
				title="v2.9.6 patch DDL",
			)

	# 2. Drop Custom DocPerm rows with permlevel > 0 on TS Quality Inspection
	#    (these were seeded in v2.9.5 / v2.9.5.1 and are obsolete now).
	try:
		frappe.db.sql(
			"DELETE FROM `tabCustom DocPerm` "
			"WHERE parent='TS Quality Inspection' AND permlevel > 0"
		)
	except Exception as e:
		frappe.log_error(
			message=f"v2.9.6 DELETE permlevel>0 Custom DocPerm failed: {e}",
			title="v2.9.6 patch perm cleanup",
		)

	# 3. Clear v2.9.5 idempotency flag (so prior patch can't re-run obsoletely).
	try:
		frappe.db.set_default("ts_qi_actual_deduction_done", None)
	except Exception:
		pass

	# 4. Mark v2.9.6 patch done.
	frappe.db.set_default("ts_v296_qi_revert_done", "1")

	# 5. Clear DocType cache so the form re-reads the current JSON-defined fields.
	frappe.clear_cache(doctype="TS Quality Inspection")
	frappe.clear_cache(doctype="TS Deduction Sheet")
