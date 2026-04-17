"""
v2.8.0 patch — hard-delete TS Unloading Entry DocType.

Safe to run on production: zero rows ever existed on erpbbpl.com (verified 17 Apr 2026).
Idempotent: re-runs harmlessly once the DocType + table are gone.

Removes:
  - tabTS Unloading Entry table
  - TS Unloading Entry DocType row
  - Custom DocPerm rows for TS Unloading Entry
  - Property Setter rows for TS Unloading Entry
  - Number Cards filtering on TS Unloading Entry
  - __UserSettings rows for TS Unloading Entry

Reference: memory/project_flow_revamp_v2.8.md (Phase A, Change #3)
"""
import frappe


def execute():
	doctype_name = "TS Unloading Entry"

	# Pre-flight: confirm no rows survive (should always be 0 on prod per audit)
	table_exists = frappe.db.sql(
		"SHOW TABLES LIKE %s", (f"tab{doctype_name}",)
	)
	if table_exists:
		row_count = frappe.db.sql(
			f"SELECT COUNT(*) FROM `tab{doctype_name}`"
		)[0][0]
		if row_count > 0:
			frappe.log_error(
				message=f"Refusing to drop tab{doctype_name}: {row_count} rows exist. "
				        f"Manual cleanup required before v2.8.0 patch can complete.",
				title="v2.8.0 drop_unloading_entry_table",
			)
			# Don't raise — let patch complete so other v2.8 changes apply.
			# Admin must manually delete rows + re-run patch.
			return

	# 1. Custom DocPerm rows
	frappe.db.sql(
		"DELETE FROM `tabCustom DocPerm` WHERE parent=%s", (doctype_name,)
	)

	# 2. Property Setters
	frappe.db.sql(
		"DELETE FROM `tabProperty Setter` WHERE doc_type=%s", (doctype_name,)
	)

	# 3. Number Cards
	frappe.db.sql(
		"DELETE FROM `tabNumber Card` WHERE document_type=%s", (doctype_name,)
	)

	# 4. __UserSettings (grid column cache)
	frappe.db.sql(
		"DELETE FROM `__UserSettings` WHERE doctype=%s", (doctype_name,)
	)

	# 4b. Workspace Links pointing to this DocType (orphan cleanup)
	frappe.db.sql(
		"DELETE FROM `tabWorkspace Link` WHERE link_to=%s", (doctype_name,)
	)

	# 4c. Workspace Shortcuts pointing to this DocType
	frappe.db.sql(
		"DELETE FROM `tabWorkspace Shortcut` WHERE link_to=%s", (doctype_name,)
	)

	# 5. DocType row itself (force=1 removes dependent rows)
	if frappe.db.exists("DocType", doctype_name):
		try:
			frappe.delete_doc("DocType", doctype_name, force=1, ignore_permissions=True)
		except Exception:
			# If delete fails, manually clear DocType row
			frappe.db.sql("DELETE FROM `tabDocType` WHERE name=%s", (doctype_name,))

	# Commit all DML BEFORE the DDL (DROP TABLE triggers implicit commit check in Frappe).
	frappe.db.commit()

	# 6. Drop the table itself — use sql_ddl() which handles DDL context safely.
	if table_exists:
		frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{doctype_name}`")

	frappe.db.commit()
	print(f"v2.8.0 patch: {doctype_name} removed cleanly.")
