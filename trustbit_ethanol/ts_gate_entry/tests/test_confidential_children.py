"""Child rows of the four confidential doctypes follow their parent's rule in list, count and chart queries.
Demo only; every write is rolled back. Usage: same as test_rename_guard.py. Exit 0 = all passed."""

import json
import os
import sys

import frappe

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
METHOD = "trustbit_ethanol.ts_gate_entry.ts_confidential_children.rows"
PO, LINE = "Purchase Order", "Purchase Order Item"


def run():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
	import test_chart_guard as chart
	from frappe import handler

	from trustbit_ethanol import rename_guard
	from trustbit_ethanol.ts_gate_entry import ts_confidential_children as kids

	rename_guard.apply()
	results = []
	try:
		tables = {}
		for parent in kids.PARENTS:
			for df in frappe.get_meta(parent).get_table_fields():
				tables.setdefault(df.options, []).append(parent)
		hooks = frappe.get_hooks("permission_query_conditions")
		registered = {child for child, methods in hooks.items() if METHOD in methods}
		checks = [("hooks.py registers the rule for every child table of the four doctypes",
			sorted(set(tables) - registered) == []),
			("and for no table that is not their child", sorted(registered - set(tables)) == [])]

		def new_user(tag, *roles):
			return frappe.get_doc({"doctype": "User", "email": f"cc.{tag}@example.com", "first_name": "CC " + tag,
				"send_welcome_email": 0, "roles": [{"role": r} for r in roles]}).insert(ignore_permissions=True).name

		user, admin = new_user("pur", "Purchase User"), new_user("adm", "Purchase User", "System Manager")
		own, hidden, readable = {}, {}, {}
		for parent, rule in kids.PARENTS.items():
			frappe.set_user(user)
			readable[parent] = frappe.has_permission(parent, "read")
			# no role on this site may read the parent and is held back by the rule: the rule itself is run in SQL
			own[parent] = set(frappe.get_list(parent, pluck="name", limit_page_length=0) if readable[parent]
				else frappe.db.sql(f"select name from `tab{parent}` where {rule(user)}", pluck=True))
			frappe.set_user("Administrator")
			hidden[parent] = set(frappe.get_all(parent, pluck="name")) - own[parent]
		checks.append(("the sample user reads purchase orders, and some orders are hidden from them",
			bool(own[PO]) and bool(hidden[PO])))

		def listed(who, child, under, **filters):
			frappe.set_user(who)
			try:
				return frappe.get_list(child, fields=["name", "parent"], filters={"parenttype": under, **filters},
					parent_doctype=under, limit_page_length=0)
			finally:
				frappe.set_user("Administrator")

		for child, parents in sorted(tables.items()):
			for parent in parents:
				got = listed(user, child, parent) if readable[parent] else frappe.db.sql(
					f"select parent from `tab{child}` where parenttype = %s and ({kids.rows(user, child)})", (parent,), as_dict=True)
				want = frappe.db.sql(f"select count(*) from `tab{child}` where parenttype = %s and parent in %s",
					(parent, tuple(own[parent]) or ("",)))[0][0]
				total = frappe.db.count(child, {"parenttype": parent})
				leaked = sum(1 for row in got if row.parent in hidden[parent])
				how = "listed" if readable[parent] else "pass the rule in SQL"
				checks.append((f"{child} under {parent}: {len(got)} of {total} rows {how}, none of a hidden parent, "
					"none wrongly dropped", leaked == 0 and len(got) == want))
		checks.append(("each of the four doctypes has hidden records on this site, so no check above is empty",
			all(hidden.values())))

		def call(cmd, who=None, **args):
			frappe.set_user(who or user)
			frappe.local.request = chart.Req()
			frappe.local.form_dict = frappe._dict(cmd=cmd, **args)
			try:
				return handler.execute_cmd(cmd)
			except frappe.PermissionError as e:
				return type(e)
			finally:
				frappe.set_user("Administrator")
				frappe.clear_messages()

		with_lines = set(frappe.get_all(LINE, filters={"parenttype": PO}, pluck="parent"))
		some = sorted(hidden[PO] & with_lines)[:50]
		on_hidden = json.dumps({"parent": ["in", some]})
		checks.append(("the sample has lines of hidden orders to ask for", len(some) > 1))
		checks.append(("frappe.client.get_list on the lines of hidden orders returns nothing", call("frappe.client.get_list",
			doctype=LINE, parent=PO, fields=json.dumps(["parent", "rate"]), filters=on_hidden, limit_page_length=0) == []))
		checks.append(("frappe.client.get_count on them counts nothing or is refused", call("frappe.client.get_count",
			doctype=LINE, filters=on_hidden) in (0, frappe.PermissionError)))
		group = chart.ask(user, {"document_type": LINE, "parent_document_type": PO, "chart_type": "Group By",
			"group_by_type": "Sum", "group_by_based_on": "parent", "aggregate_function_based_on": "amount",
			"number_of_groups": 0, "filters_json": "[]"})
		checks.append(("a group-by chart on order lines names orders, and no hidden one",
			isinstance(group, dict) and bool(group.get("labels")) and not set(group["labels"]) & hidden[PO]))
		heat = chart.heat(LINE, parent=("in", some))
		heat["parent_document_type"] = PO
		checks.append(("a heatmap on the lines of hidden orders counts nothing", chart.total(chart.ask(user, heat)) == 0))

		checks.append(("the check on one child row is in place, on top of Frappe's own",
			frappe.permissions.has_child_permission is kids.one_row and kids._core_row.__module__ == "frappe.permissions"))
		line = frappe.db.get_value(LINE, {"parent": some[0]}, "name")
		refused = [call("frappe.client.get", doctype=LINE, name=line, parent=PO),
			call("frappe.client.get", doctype=LINE, filters=json.dumps({"parent": some[0]}), parent=PO)]
		frappe.set_user(user)
		refused += [frappe.get_doc(LINE, line).has_permission("read"), frappe.has_permission(LINE, "read", line)]
		frappe.set_user("Administrator")
		checks.append(("one line of a hidden order cannot be opened: by row name, by a filter on the order, or in code",
			refused == [frappe.PermissionError, frappe.PermissionError, False, False]))
		seen = sorted(own[PO] & with_lines)[0]
		shown = call("frappe.client.get", doctype=LINE, filters=json.dumps({"parent": seen}), parent=PO)
		frappe.set_user(user)
		whole = frappe.get_doc(PO, seen)
		through = whole.items[0].has_permission("read")
		frappe.set_user("Administrator")
		checks.append(("a line of an order the user may read still opens, on its own and through the order",
			isinstance(shown, dict) and shown.get("parent") == seen and through))
		frappe.set_user(admin)
		lets = frappe.get_doc(LINE, line).has_permission("read")
		frappe.set_user("Administrator")
		checks.append(("a user the rule lets through opens the line of the confidential order", bool(lets)))

		everything = frappe.db.count(LINE, {"parenttype": PO})
		checks.append(("a user the rule lets through still lists every order line", len(listed(admin, LINE, PO)) == everything))
		checks.append(("for that user, and for a table of another doctype, the rule adds nothing",
			kids.rows(admin, LINE) == "" and kids.rows(user, "Sales Order Item") == "" and kids.rows(user, None) == ""))

		mine = some[-1]
		frappe.db.set_value(PO, mine, "owner", user, update_modified=False)
		got = listed(user, LINE, PO, parent=mine)
		checks.append(("the lines of a confidential order the user owns are listed for them",
			len(got) > 0 and len(got) == frappe.db.count(LINE, {"parent": mine})))

		for child in sorted(c for c, parents in tables.items()
				if frappe.db.sql(f"select 1 from `tab{c}` where parenttype not in %s limit 1", (tuple(kids.PARENTS),))):
			dropped = frappe.db.sql(f"select count(*) from `tab{child}` where parenttype not in %s and not ({kids.rows(user, child)})",
				(tuple(kids.PARENTS),))[0][0]
			checks.append((f"{child} is shared with other doctypes: none of their rows is dropped", dropped == 0))

		from erpnext.assets.doctype.asset_capitalization import asset_capitalization as wip

		getter, receipt = wip.__name__ + ".get_items_tagged_to_wip_composite_asset", "Purchase Receipt"
		everything = [row for part in wip.get_items_tagged_to_wip_composite_asset({}) for row in part]
		sales = new_user("sal", "Sales User")
		frappe.set_user(sales)
		reads = frappe.has_permission(receipt, "read")
		frappe.set_user("Administrator")
		checks.append(("the receipt-lines call refuses a login that cannot read receipts",
			not reads and call(getter, who=sales, params="{}") is frappe.PermissionError))
		got = call(getter, params="{}")
		rows = [row for part in got for row in part] if isinstance(got, (list, tuple)) else None
		checks.append((f"a receipt reader gets {len(rows or [])} of {len(everything)} lines: none of a hidden receipt, none wrongly dropped",
			bool(rows) and not {row["purchase_receipt"] for row in rows} & hidden[receipt]
			and len(rows) == sum(1 for row in everything if row.purchase_receipt not in hidden[receipt])))
		got = call(getter, who=admin, params="{}")
		checks.append(("a user the rule lets through gets what ERPNext's own function returns",
			isinstance(got, (list, tuple)) and sum(len(part) for part in got) == len(everything)))
		checks.append(("the guards are also applied in background jobs",
			"trustbit_ethanol.rename_guard.apply" in frappe.get_hooks("before_job")
			and frappe.call(rename_guard.apply, method="x", kwargs={}, transaction_type="job") is None))

		for name, ok in checks:
			results.append(bool(ok))
			print(f"  {'PASS' if ok else 'FAIL'}  {name}")
	finally:
		frappe.set_user("Administrator")
		frappe.db.rollback()
	print(f"\n{sum(results)}/{len(results)} passed")
	sys.exit(0 if results and all(results) else 1)


if __name__ == "__main__":
	run()
