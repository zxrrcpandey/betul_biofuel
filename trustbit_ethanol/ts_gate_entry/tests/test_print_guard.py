"""print_guard: a print format is rendered only on a document of its own DocType, and two formats (BBPL
Purchase Receipt, TS Gate Entry Detailed) look other records up by a plain record number only.
Demo only; every write is rolled back; prints PASS / FAIL lines, never a value.
Usage: same as test_rename_guard.py. Exit 0 = all passed."""

import glob
import json
import os
import sys

import frappe
from frappe.www import printview

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
PR, GED = "BBPL Purchase Receipt", "TS Gate Entry Detailed"
# what a caller can send in place of a record number; an unguarded template looks each of these up
BY_NAME = (("a question", lambda n: {"name": n}), ("a question with a pattern", lambda n: {"name": ["like", n]}), ("a list", lambda n: [n]))
BY_VALUE = (("an equals pair", lambda n: ["=", n]), ("a pattern pair", lambda n: ["like", n]))


def show(user, fmt, doc=None, saved=None, raw=False):
	"""Print as `user`: `doc` is a dict as a browser sends it, `saved` is (doctype, name). Returns (html, exception class)."""
	frappe.set_user(user)
	args = dict(doc=saved[0], name=saved[1]) if saved else dict(doc=json.dumps(doc, default=str))
	try:
		if raw:
			return printview.get_rendered_raw_commands(print_format=fmt, **args)["raw_commands"] or "", None
		return printview.get_html_and_style(print_format=fmt, no_letterhead=1, **args)["html"] or "", None
	except Exception as e:
		return "", type(e)
	finally:
		frappe.set_user("Administrator")
		frappe.clear_messages()


def one(sql, *values):
	got = frappe.db.sql(sql, values)
	return got[0][0] if got else None


def as_sent(dt, dn, **changed):
	return dict(frappe.get_doc(dt, dn).as_dict(), **changed)


def run():
	if SITE == "erpbbpl.com":
		sys.exit("test_print_guard.py is for demo only; it is not run on production.")
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	from trustbit_ethanol import print_guard, rename_guard

	rename_guard.apply()
	checks, skipped = [], []
	try:
		user = frappe.get_doc({"doctype": "User", "email": "pg.reader@example.com", "first_name": "PG",
			"send_welcome_email": 0, "roles": [{"role": "Purchase User"}]}).insert(ignore_permissions=True).name
		todo = {"doctype": "ToDo", "description": "ZT PG", "allocated_to": user, "owner": user}
		checks.append(("the guard is in the list and applied", "trustbit_ethanol.print_guard.apply" in rename_guard.GUARDS
			and printview.get_rendered_template is print_guard.get_rendered_template and not rename_guard._failed))
		checks.append(("precondition: the made-up ToDo prints in the Standard format", show(user, "Standard", todo)[1] is None))

		# 1. every print format of another DocType refuses the made-up ToDo, on both print calls
		for fmt, raw in frappe.get_all("Print Format", {"doc_type": ["not in", ["ToDo", ""]]}, ["name", "raw_printing"], order_by="name", as_list=True):
			html, err = show(user, fmt, todo)
			checks.append((f"refused on a ToDo: {fmt}", err is frappe.PermissionError and not html))
			if raw:  # Frappe refuses the raw call for any other format before it renders
				html, err = show(user, fmt, todo, raw=True)
				checks.append((f"refused on a ToDo (raw print call): {fmt}", err is frappe.PermissionError and not html))

		# 2. each of the app's own formats still prints a saved document of its own DocType
		folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "print_format", "*", "*.json")
		for path in sorted(glob.glob(folder)):
			fmt = json.load(open(path))
			if not frappe.db.exists("Print Format", {"name": fmt["name"], "disabled": 0}):
				skipped.append(fmt["name"] + " (not on this server, or disabled)")
				continue
			meta = frappe.get_meta(fmt["doc_type"])
			name = frappe.db.get_value(fmt["doc_type"], {"docstatus": 1} if meta.is_submittable else {}, "name", order_by="creation desc")
			if not name:
				skipped.append(fmt["name"] + " (no document to print)")
				continue
			checks.append((f"still prints its own document: {fmt['name']}", show("Administrator", fmt["name"], saved=(fmt["doc_type"], name))[1] is None))

		# 3. BBPL Purchase Receipt: the gate entry, token and address are looked up by a plain number only
		base = one("select name from `tabPurchase Receipt` where docstatus = 1 order by creation desc limit 1")
		own, _e = show("Administrator", PR, saved=("Purchase Receipt", base))
		clear = dict(vehicle_no="", lr_no="", ts_non_weighing_grn=0, ts_direct_po_grn=0, ts_token=None, ts_gate_entry=None)
		gate = frappe.db.sql("select name, vehicle_number_display from `tabTS Gate Entry` where length(vehicle_number_display) > 5 order by creation desc limit 50")
		gate = next(((n, v) for n, v in gate if v not in own), None)
		rst = frappe.db.sql("select distinct token_number from `tabTS Weighbridge Log` where length(rst_number) > 3 and ifnull(token_number, '') != '' order by creation desc limit 50")
		rst = ((n, frappe.db.get_value("TS Weighbridge Log", {"token_number": n}, "rst_number")) for (n,) in rst)  # the row the template reads
		rst = next(((n, v) for n, v in rst if v and len(str(v)) > 3 and str(v) not in own), None)
		addr = frappe.db.sql("select name, address_line1 from tabAddress where address_line1 regexp '^[A-Za-z0-9 ]{10,}$' order by creation desc limit 50")
		addr = next(((n, v) for n, v in addr if v not in own), None)
		for label, field, pick, shapes in (("gate entry", "ts_gate_entry", gate, BY_NAME), ("token", "ts_token", rst, BY_VALUE),
				("address", "supplier_address", addr, BY_NAME)):
			checks.append((f"{PR}: there is a {label} to test with", bool(base and pick)))
			if not (base and pick):
				continue
			name, secret = pick[0], str(pick[1])
			html, err = show("Administrator", PR, as_sent("Purchase Receipt", base, **dict(clear, **{field: name})))
			checks.append((f"{PR}: control, a plain {label} number is looked up", err is None and secret in html))
			for shape, key in shapes:
				html, err = show("Administrator", PR, as_sent("Purchase Receipt", base, **dict(clear, **{field: key(name)})))
				checks.append((f"{PR}: {label} sent as {shape} prints, and is not looked up", err is None and secret not in html))
			printview.get_rendered_template = print_guard._core_render  # the central check off: the template's own check
			try:
				html, err = show("Administrator", PR, as_sent("Purchase Receipt", base, **dict(clear, **{field: name, "doctype": "ToDo"})))
			finally:
				rename_guard.apply()
			checks.append((f"{PR}: on a made-up ToDo the {label} is not looked up (template's own check)", err is None and secret not in html))

		# 4. TS Gate Entry Detailed: the token is looked up by a plain number only
		submit = frappe.get_meta("TS Gate Entry").is_submittable
		base = one("select name from `tabTS Gate Entry` where ifnull(token_number, '') != '' and docstatus = %s order by creation desc limit 1", 1 if submit else 0)
		own, _e = show("Administrator", GED, saved=("TS Gate Entry", base)) if base else ("", None)
		tok = frappe.db.sql("select name, ts_driver_mobile_g1 from `tabTS Token` where length(ts_driver_mobile_g1) >= 10 order by creation desc limit 50")
		tok = next(((n, v) for n, v in tok if v not in own), None)
		checks.append((f"{GED}: there is a token to test with", bool(base and tok)))
		if base and tok:
			clear = dict(ts_g1_driver_name="", ts_g1_driver_mobile="", ts_g1_driver_license="")
			name, secret = tok
			html, err = show("Administrator", GED, as_sent("TS Gate Entry", base, **dict(clear, token_number=name)))
			checks.append((f"{GED}: control, a plain token number is looked up", err is None and secret in html))
			for shape, key in BY_NAME:
				html, err = show("Administrator", GED, as_sent("TS Gate Entry", base, **dict(clear, token_number=key(name))))
				checks.append((f"{GED}: token sent as {shape} prints, and is not looked up", err is None and secret not in html))
			printview.get_rendered_template = print_guard._core_render
			try:
				html, err = show("Administrator", GED, as_sent("TS Gate Entry", base, **dict(clear, token_number=name, doctype="ToDo")))
			finally:
				rename_guard.apply()
			checks.append((f"{GED}: on a made-up ToDo the token is not looked up (template's own check)", err is None and secret not in html))
		checks.append(("the guard is back on after the template checks", printview.get_rendered_template is print_guard.get_rendered_template))
	finally:
		frappe.set_user("Administrator")
		frappe.db.rollback()
	for name, ok in checks:
		print(f"  {'PASS' if ok else 'FAIL'}  {name}")
	for name in skipped:
		print(f"  SKIP  {name}")
	print(f"\n{sum(bool(ok) for _n, ok in checks)}/{len(checks)} passed, {len(skipped)} skipped")
	sys.exit(0 if checks and all(ok for _n, ok in checks) else 1)


if __name__ == "__main__":
	run()
