"""chart_guard: the dashboard-chart data call answers only for what the caller may read.
Demo only; every write is rolled back. Usage: same as test_rename_guard.py. Exit 0 = all passed."""

import json
import os
import sys

import frappe

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
SUB, USAGE = "TS AI Check Submission", "TS AI Check Usage"
CMD = "frappe.desk.doctype.dashboard_chart.dashboard_chart.get"
LEDGER = "erpnext.accounts.dashboard_chart_source.account_balance_timeline.account_balance_timeline.get"


class Req:
	method = "POST"


def ask(user, chart, cmd=CMD, **more):
	"""The call as the web handler makes it, as `user`: the dataPoints / labels, or the exception class."""
	from frappe import handler

	frappe.set_user(user)
	frappe.local.request = Req()
	frappe.local.form_dict = frappe._dict(cmd=cmd, chart=json.dumps(chart), **more)
	try:
		return handler.execute_cmd(cmd)
	except (frappe.PermissionError, frappe.ValidationError) as e:
		return type(e)
	finally:
		frappe.set_user("Administrator")
		frappe.clear_messages()


def heat(doctype, kind="Count", column=None, **filters):
	return {"document_type": doctype, "type": "Heatmap", "chart_type": kind, "value_based_on": column,
		"based_on": "creation", "heatmap_year": frappe.utils.getdate().year,
		"filters_json": json.dumps([[doctype, k, *v] for k, v in filters.items()])}


def total(answer):
	return round(sum(float(v or 0) for v in answer["dataPoints"].values()), 4) if isinstance(answer, dict) else answer


def run():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	from frappe.desk.doctype.dashboard_chart import dashboard_chart as core

	core_heatmap = core.get_heatmap_chart_config  # Frappe's own, before the guard replaces it
	from trustbit_ethanol import chart_guard, rename_guard

	rename_guard.apply()
	results, planted = [], None
	try:
		def user(tag, role):
			return frappe.get_doc({"doctype": "User", "email": f"cg.{tag}@example.com", "first_name": "CG " + tag,
				"send_welcome_email": 0, "roles": [{"role": role}]}).insert(ignore_permissions=True).name

		acc, acc2, md = user("acc", "Accounts User"), user("acc2", "Accounts User"), user("md", "MD")
		for name, owner in (("ZT-CG-1", acc), ("ZT-CG-2", acc2)):
			row = frappe.new_doc(SUB)
			row.update({"name": name, "owner": owner, "list_date": frappe.utils.today(), "docstatus": 0})
			row.db_insert()
			frappe.db.set_value(SUB, name, "owner", owner, update_modified=False)  # db_insert stamps the session user
			use = frappe.new_doc(USAGE)
			use.update({"name": name, "submission": name, "model_used": "zt-model", "cost_usd": 1.25, "input_tokens": 40})
			use.db_insert()
		mine = {"name": ("like", "ZT-CG-%")}

		checks = [("guard applied through the request hook and whitelisted",
			core.get is chart_guard.get and core.get_heatmap_chart_config is chart_guard.heatmap
			and frappe.is_whitelisted(chart_guard.get) is None)]
		checks.append(("the guard calls Frappe's own function, not its shared-cache wrapper",
			chart_guard._core_get.__name__ == "get" and chart_guard._core_get.__code__.co_filename.endswith("dashboard_chart.py")))

		for who in (acc, acc2):
			got = [ask(who, heat(USAGE, "Sum", column, **mine)) for column in ("cost_usd", "input_tokens")]
			got.append(ask(who, heat(USAGE, "Count", None, model_used=("like", "zt-%"))))
			checks.append((f"{who.split('@')[0]}: cost, tokens and a model-name probe on the usage record are refused",
				all(g is frappe.PermissionError for g in got)))
		checks.append(("the MD role reads the usage record through a heatmap", total(ask(md, heat(USAGE, "Sum", "cost_usd", **mine))) == 2.5))
		checks.append(("Administrator reads it too", total(ask("Administrator", heat(USAGE, "Sum", "cost_usd", **mine))) == 2.5))

		checks.append(("an Accounts user counts only their own submissions, not all",
			[total(ask(who, heat(SUB, "Count", None, **mine))) for who in (acc, acc2, md)] == [1, 1, 2]))
		po = heat("Purchase Order", "Sum", "base_grand_total")
		checks.append(("a user with no right on Purchase Order gets no order totals", ask(acc, po) is frappe.PermissionError))

		spec = frappe._dict(heat("Purchase Order", "Sum", "base_grand_total"))
		old = core_heatmap(spec, [["Purchase Order", "docstatus", "<", 2, False]], spec.heatmap_year)
		new = ask("Administrator", po)
		checks.append(("for Administrator the numbers equal Frappe's own heatmap", isinstance(new, dict) and total(new) == total(old)
			and len(new["dataPoints"]) == len(old["dataPoints"])))

		line = {"document_type": USAGE, "type": "Line", "chart_type": "Sum", "value_based_on": "cost_usd", "based_on": "creation",
			"timespan": "Last Year", "time_interval": "Monthly", "filters_json": "[]"}
		checks.append(("a line chart on the usage record is refused for an Accounts user too", ask(acc, line) is frappe.PermissionError))
		group = {"document_type": USAGE, "chart_type": "Group By", "group_by_type": "Sum", "group_by_based_on": "model_used",
			"aggregate_function_based_on": "cost_usd", "filters_json": "[]"}
		checks.append(("and so is a group-by chart", ask(acc, group) is frappe.PermissionError))

		saved = frappe.db.get_value("Dashboard Chart", {"chart_type": ("in", ["Count", "Sum", "Average"]),
			"type": ("not in", ["Heatmap"]), "is_public": 1}, "name")
		if saved:
			key = "chart-data:" + saved
			planted = (key, frappe.cache.get_value(key))
			frappe.cache.set_value(key, json.dumps({"labels": ["PLANTED"], "datasets": []}))
			frappe.set_user("Administrator")
			frappe.local.request, frappe.local.form_dict = Req(), frappe._dict(cmd=CMD, chart_name=saved)
			from frappe import handler

			answer = handler.execute_cmd(CMD)
			checks.append(("a saved chart is worked out for the caller, not taken from the shared cache",
				isinstance(answer, dict) and answer.get("labels") != ["PLANTED"]))
		else:
			print("  (this site has no saved public chart to test the cache with)")

		from erpnext.accounts.dashboard_chart_source.account_balance_timeline import account_balance_timeline as ledger

		checks.append(("the account-balance chart call is guarded too, and the guard calls ERPNext's own function",
			ledger.get is chart_guard.ledger_get and frappe.is_whitelisted(chart_guard.ledger_get) is None
			and chart_guard._ledger_get.__code__.co_filename.endswith("account_balance_timeline.py")))
		busy = frappe.db.sql("select account, company from `tabGL Entry` where is_cancelled = 0 group by account order by count(*) desc limit 1")
		if busy:
			balance = {"timespan": "Last Year", "time_interval": "Monthly",
				"filters_json": json.dumps({"account": busy[0][0], "company": busy[0][1]})}
			pur = user("pur", "Purchase User")
			checks.append(("a user who cannot read ledger entries gets no account balances",
				ask(pur, balance, LEDGER) is frappe.PermissionError and ask(pur, balance, LEDGER, no_cache=1) is frappe.PermissionError))
			own = chart_guard._ledger_get(chart=json.dumps(balance))["datasets"][0]["values"]
			got = ask(acc, balance, LEDGER)
			checks.append(("an Accounts user gets the same balances as ERPNext's own function, and they are not all zero",
				isinstance(got, dict) and got["datasets"][0]["values"] == own and any(own)))
		else:
			print("  (this site has no ledger entries to test the account-balance guard with)")

		if busy:
			from erpnext.accounts import utils as accounts
			from erpnext.accounts.doctype.journal_entry import journal_entry as importer  # imports it by name
			from erpnext.accounts.doctype.payment_entry import payment_entry  # noqa: F401  (another one)
			from frappe import handler

			def call(who, cmd, **args):
				frappe.set_user(who)
				frappe.local.request = Req()
				frappe.local.form_dict = frappe._dict(cmd=cmd, **args)
				try:
					return handler.execute_cmd(cmd)
				except frappe.PermissionError as e:
					return type(e)
				finally:
					frappe.set_user("Administrator")
					frappe.clear_messages()

			name, real = busy[0][0], accounts.get_balance_on(busy[0][0])
			checks.append(("ERPNext's own balance function is off the web whitelist, and still answers server code",
				accounts.get_balance_on not in frappe.whitelisted and isinstance(real, float) and real != 0))
			web = "erpnext.accounts.utils.get_balance_on"
			party = frappe.db.get_value("GL Entry", {"party_type": "Supplier", "is_cancelled": 0}, "party")
			checks.append(("a user who cannot read ledger entries gets no balance: asked plainly, with the check switched off, or by supplier",
				[call(pur, web, account=name), call(pur, web, account=name, ignore_account_permission="1"),
				call(pur, web, party_type="Supplier", party=party)] == [frappe.PermissionError] * 3))
			others = sorted(m.__name__ for m in list(sys.modules.values())
				if m is not accounts and getattr(m, "get_balance_on", None) is accounts.get_balance_on)
			checks.append((f"under the {len(others)} other module names that import it, the call is refused for everyone",
				importer.__name__ in others and all(call(who, other + ".get_balance_on", account=name, ignore_account_permission="1")
				is frappe.PermissionError for other in others for who in (pur, acc))))
			checks.append(("an Accounts user still gets the balance, the same number as ERPNext's own function",
				call(acc, web, account=name) == real))

		for name, ok in checks:
			results.append(bool(ok))
			print(f"  {'PASS' if ok else 'FAIL'}  {name}")
	finally:
		frappe.set_user("Administrator")
		frappe.db.rollback()
		if planted:
			frappe.cache.set_value(planted[0], planted[1]) if planted[1] else frappe.cache.delete_value(planted[0])
	print(f"\n{sum(results)}/{len(results)} passed")
	sys.exit(0 if results and all(results) else 1)


if __name__ == "__main__":
	run()
