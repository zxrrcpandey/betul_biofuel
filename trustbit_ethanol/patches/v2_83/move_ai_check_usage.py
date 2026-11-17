"""AI Checker: the AI model, tokens and cost of a check used to sit in four columns of the submission.
They now live in TS AI Check Usage, which only the MD role reads. Copy what the old columns hold into
that doctype, then drop the columns, so nothing of them is left where a submitter can reach.
Runs after the doctypes are synced. Safe to run again: with no old column it does nothing.
Rollback note: the previous code writes those columns, so going back needs a migrate as well."""

import frappe

from trustbit_ethanol.ts_gate_entry import ts_ai_check as ai


def execute():
	if frappe.db.table_exists(ai.REPORT):  # the report said "AI (model name)"; the model belongs to the usage record only
		frappe.db.sql(f"UPDATE `tab{ai.REPORT}` SET read_by = 'AI' WHERE read_by LIKE 'AI (%%'")
	table = "tab" + ai.SUBMISSION
	if not frappe.db.table_exists(ai.SUBMISSION) or not frappe.db.table_exists(ai.USAGE):
		return
	old = [f for f in ai.USAGE_FIELDS if frappe.db.sql(f"SHOW COLUMNS FROM `{table}` LIKE %s", f)]
	if not old:
		return
	if len(old) == len(ai.USAGE_FIELDS):
		rows = frappe.db.sql(
			f"""SELECT name, model_used, input_tokens, output_tokens, cost_usd FROM `{table}`
			WHERE IFNULL(model_used, '') != '' OR input_tokens > 0 OR output_tokens > 0 OR cost_usd > 0""",
			as_dict=True)
		for row in rows:
			ai.save_usage(row.pop("name"), row)
		frappe.db.commit()
	drops = ", ".join(f"DROP COLUMN `{fieldname}`" for fieldname in old)
	frappe.db.sql_ddl(f"ALTER TABLE `{table}` {drops}")
	frappe.clear_cache(doctype=ai.SUBMISSION)
