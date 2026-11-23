"""rename_guard: a reader cannot rename through either API route; a writer still renames normally.
Demo only; every write is rolled back. Usage: same as test_mst_report.py. Exit 0 = all passed."""

import json
import os
import sys

import frappe
from frappe import handler
from frappe.api import v2

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
REFUSED = (frappe.ValidationError, frappe.PermissionError)


class Req:
	def __init__(self, method):
		self.method = method


def call(route, user, dt, dn, http, docs=None, **args):
	"""Rename through one API route as `user`; returns the exception class, or None if it renamed."""
	frappe.db.savepoint("rg")
	frappe.set_user(user)
	frappe.local.request, frappe.local.response = Req(http), frappe._dict(docs=[])
	try:
		if route == "v1":
			handler.run_doc_method(method="rename", dt=None if docs else dt, dn=dn, docs=docs and json.dumps(docs),
				args=json.dumps(args))
		else:
			frappe.local.form_dict = frappe._dict(args)
			v2.execute_doc_method(dt, dn, "rename")
		return None
	except REFUSED as e:
		return type(e)
	finally:
		frappe.set_user("Administrator")
		frappe.clear_messages()


def run():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	from trustbit_ethanol import rename_guard
	from frappe.model.document import Document

	rename_guard.apply()
	results = []
	try:
		sg = frappe.get_doc({"doctype": "Supplier Group", "supplier_group_name": "ZT RG Group",
			"parent_supplier_group": "All Supplier Groups"}).insert().name
		todo = frappe.get_doc({"doctype": "ToDo", "description": "ZT RG"}).insert().name
		reader = frappe.get_doc({"doctype": "User", "email": "rg.reader@example.com", "first_name": "RG",
			"send_welcome_email": 0, "roles": [{"role": "Purchase User"}]}).insert(ignore_permissions=True).name
		frappe.set_user(reader)
		pre = frappe.has_permission("Supplier Group", "read", sg) and not frappe.has_permission("Supplier Group", "write", sg)
		frappe.set_user("Administrator")
		checks = [("patch applied and whitelisted", Document.rename is rename_guard.rename
				and frappe.is_whitelisted(rename_guard.rename) is None),
			("reader has read, not write (precondition)", pre)]
		for route in ("v1", "v2"):
			for http in ("GET", "POST") if route == "v1" else ("GET",):
				got = call(route, reader, "Supplier Group", sg, http, name=sg + " X", validate_rename=False)
				frappe.db.rollback(save_point="rg")
				checks.append((f"{route} {http}: reader refused", got is not None and frappe.db.exists("Supplier Group", sg)))
		forged = {"doctype": "ToDo", "name": todo, "description": "ZT RG", "allocated_to": reader, "__islocal": 1}
		got = call("v1", reader, "ToDo", todo, "POST", docs=forged, name=todo + "z", force=True)
		frappe.db.rollback(save_point="rg")
		checks.append(("v1 docs=: a forged copy naming the reader does not grant rename",
			got is not None and frappe.db.exists("ToDo", todo)))
		got = call("v1", reader, "ToDo", todo, "POST", docs=dict(forged, name={}), name=todo + "z", force=True)
		frappe.db.rollback(save_point="rg")
		checks.append(("v1 docs=: a forged empty name is refused", got is not None and frappe.db.exists("ToDo", todo)))
		got = call("v1", "Administrator", "ToDo", todo, "POST", name=todo + "x", validate_rename=False)
		frappe.db.rollback(save_point="rg")
		checks.append(("writer: Allow Rename off is still enforced", got is not None and frappe.db.exists("ToDo", todo)))
		got = call("v1", "Administrator", "ToDo", todo, "POST", name=todo + "x", force=True)
		frappe.db.rollback(save_point="rg")
		checks.append(("writer: Allow Rename off is enforced when the call says force too",
			got is not None and frappe.db.exists("ToDo", todo)))
		got = call("v1", "Administrator", "Supplier Group", sg, "POST", name=sg + " Y")
		checks.append(("writer: normal rename still works", got is None and frappe.db.exists("Supplier Group", sg + " Y")))
		kept, rename_guard.GUARDS = rename_guard.GUARDS, ("trustbit_ethanol.no_such_guard.apply", *rename_guard.GUARDS)
		try:
			rename_guard.apply()
			from frappe.desk.doctype.dashboard_chart import dashboard_chart
			checks.append(("a guard that cannot be loaded is left off and does not stop the request or the other guards",
				"trustbit_ethanol.no_such_guard.apply" in rename_guard._failed
				and dashboard_chart.get.__module__ == "trustbit_ethanol.chart_guard"
				and frappe.permissions.has_child_permission.__module__.endswith("ts_confidential_children")
				and not set(kept) & rename_guard._failed))
		finally:
			rename_guard.GUARDS = kept
			rename_guard._failed.discard("trustbit_ethanol.no_such_guard.apply")
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
