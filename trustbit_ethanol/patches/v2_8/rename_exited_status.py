"""v2.8.3: rename TS Token.status 'Exited' -> 'Campus Exited' for Material flow only.

Gate Pass / Visitor flow uses gate_pass_status='Exited' which is UNTOUCHED
(that column is a different field; this patch only touches tabTS Token.status
for entry_type='Material').

Also updates any Number Card filters_json rows that filter on
`status = "Exited"` so they continue to count legacy and new rows together
via `status IN ("Exited","Campus Exited")`.

Idempotent — running twice is a no-op.
"""

import json

import frappe


def execute():
	# 1. Rename Material tokens that currently carry the legacy 'Exited' status.
	#    Gate Pass tokens on tabTS Token also use `status` but guarded here by
	#    entry_type='Material' so the two flows cannot collide.
	try:
		frappe.db.sql("""
			UPDATE `tabTS Token`
			SET status = 'Campus Exited'
			WHERE status = 'Exited'
			  AND (entry_type = 'Material' OR entry_type IS NULL)
		""")
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(message=f"rename_exited_status token rename: {e}", title="v2.8.3 rename_exited_status")

	# 2. Update Number Card filters_json entries that filter status = "Exited"
	#    to filter on both values via the `in` operator.
	try:
		cards = frappe.db.sql(
			"""SELECT name, filters_json FROM `tabNumber Card` WHERE filters_json LIKE %s""",
			("%\"Exited\"%",),
			as_dict=True,
		)
	except Exception as e:
		frappe.log_error(message=f"rename_exited_status number card select: {e}", title="v2.8.3 rename_exited_status")
		cards = []

	updated = 0
	for c in cards:
		try:
			filters = json.loads(c.filters_json or "[]")
			changed = False
			for i, flt in enumerate(filters):
				# filter format: [doctype, field, operator, value]
				if isinstance(flt, list) and len(flt) >= 4:
					if flt[1] == "status" and flt[2] == "=" and flt[3] == "Exited":
						filters[i] = [flt[0], flt[1], "in", ["Exited", "Campus Exited"]]
						changed = True
					elif (
						flt[1] == "status"
						and flt[2] == "not in"
						and isinstance(flt[3], list)
						and "Exited" in flt[3]
						and "Campus Exited" not in flt[3]
					):
						new_vals = list(flt[3]) + ["Campus Exited"]
						filters[i] = [flt[0], flt[1], "not in", new_vals]
						changed = True
			if changed:
				frappe.db.set_value("Number Card", c.name, "filters_json", json.dumps(filters))
				updated += 1
		except Exception as e:
			frappe.log_error(message=f"Number Card {c.name} filter migration: {e}", title="v2.8.3 rename_exited_status")

	frappe.db.commit()
	frappe.logger().info(f"v2.8.3 rename_exited_status: updated {updated} Number Cards")
