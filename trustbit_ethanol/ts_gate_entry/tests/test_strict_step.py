"""v2.52.0 Strict Step — standalone, read-only test suite (no DB writes; ends with rollback).

Covers the three decision points the feature adds:
  1. _mr_actionable_steps(): strict / non-strict / last step / missing step / step 0
  2. mr_before_submit_block_direct(): flag-only submit gate (payload state is untrusted)
  3. TSMRApprovalRoute._validate_strict_steps(): final-step warning, wildcard route no-op
plus the schema: the strict_step DocField exists in meta.

Usage (same shape as test_regression.py):
    cd /home/frappe/frappe-bench/sites
    su -s /bin/bash frappe -c 'cd /home/frappe/frappe-bench/sites && \
        /home/frappe/frappe-bench/env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_strict_step.py'
Exit code: 0 = all passed, 1 = failures.
"""

import os
import sys

import frappe

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
RESULTS = []


def test(name, fn):
	try:
		fn()
		RESULTS.append((name, True, ""))
		print(f"  PASS  {name}")
	except Exception as e:  # noqa: BLE001 — a test harness reports every failure
		RESULTS.append((name, False, str(e)[:300]))
		print(f"  FAIL  {name} :: {str(e)[:300]}")


def _steps(*rows):
	"""Build child-like rows (frappe._dict supports .get like a Document)."""
	return [frappe._dict(step_order=o, role=r, action_type=a, strict_step=s) for (o, r, a, s) in rows]


def run():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	from trustbit_ethanol.ts_gate_entry import ts_po_approval as e

	# ── 1. helper table ──────────────────────────────────────────────────────
	base = _steps((1, "Stock User", "Review", 0), (2, "Department Head", "Review", 0), (3, "AVP", "Final Approve", 0))

	def t_non_strict():
		assert [s.step_order for s in e._mr_actionable_steps(base, 2)] == [2, 3]
		assert [s.step_order for s in e._mr_actionable_steps(base, 1)] == [1, 2, 3]
	test("helper: strict off == old >= rule", t_non_strict)

	def t_strict():
		st = _steps((1, "Stock User", "Review", 0), (2, "Department Head", "Review", 1), (3, "AVP", "Final Approve", 0))
		assert [s.step_order for s in e._mr_actionable_steps(st, 2)] == [2]
		assert [s.step_order for s in e._mr_actionable_steps(st, 1)] == [1, 2, 3], "step 1 not strict → unchanged"
	test("helper: strict on current step → only that step", t_strict)

	def t_last():
		st = _steps((1, "Stock User", "Review", 0), (2, "AVP", "Final Approve", 1))
		assert [s.step_order for s in e._mr_actionable_steps(st, 2)] == [2]
		st0 = _steps((1, "Stock User", "Review", 0), (2, "AVP", "Final Approve", 0))
		assert [s.step_order for s in e._mr_actionable_steps(st0, 2)] == [2], "last step identical either way"
	test("helper: last step strict is a no-op", t_last)

	def t_missing_and_zero():
		assert e._mr_actionable_steps(base, 9) == [], "orphan step → empty (fail-open to old rule = nothing >= 9)"
		z = _steps((0, "Purchase User", "Review", 1), (1, "Grain Purchase Manager", "Review", 0), (2, "AVP", "Final Approve", 0))
		assert [s.step_order for s in e._mr_actionable_steps(z, 0)] == [0]
		assert [s.step_order for s in e._mr_actionable_steps(z, None)] == [0], "None current → cint 0"
	test("helper: missing step / step 0 / None", t_missing_and_zero)

	def t_no_attr():
		plain = [frappe._dict(step_order=1, role="A", action_type="Review"), frappe._dict(step_order=2, role="B", action_type="Final Approve")]
		assert [s.step_order for s in e._mr_actionable_steps(plain, 1)] == [1, 2], "rows without the column degrade to old rule"
	test("helper: rows loaded before the column exists → old rule", t_no_attr)

	# ── 2. submit gate table (unsaved doc, before_submit hook called directly) ──
	def _mr(mtype, status, route, flag):
		d = frappe.new_doc("Material Request")
		d.material_request_type = mtype
		d.ts_mr_status = status
		d.ts_mr_approval_route = route
		if flag:
			d.flags.ts_approval_workflow_call = True
		return d

	def t_forged():
		for mtype in ("Purchase", "Service Request"):
			d = _mr(mtype, "Approved", "AVP MR App", flag=False)
			try:
				e.mr_before_submit_block_direct(d)
				raise AssertionError(f"{mtype}: forged Approved+route without the engine flag must be refused")
			except frappe.ValidationError:
				frappe.clear_messages()
	test("submit gate: Approved + route WITHOUT engine flag → refused", t_forged)

	def t_legit():
		d = _mr("Purchase", "Approved", "AVP MR App", flag=True)
		assert e.mr_before_submit_block_direct(d) is None
	test("submit gate: engine flag + Approved + route → passes", t_legit)

	def t_flag_but_pending():
		d = _mr("Purchase", "Pending AVP", "AVP MR App", flag=True)
		try:
			e.mr_before_submit_block_direct(d)
			raise AssertionError("flag alone must not pass a non-Approved status")
		except frappe.ValidationError:
			frappe.clear_messages()
	test("submit gate: flag but status not Approved → refused", t_flag_but_pending)

	def t_transfer():
		for mtype in ("Material Transfer", "Material Issue"):
			assert e.mr_before_submit_block_direct(_mr(mtype, "", "", flag=False)) is None
	test("submit gate: Transfer / Issue keep their early return", t_transfer)

	def t_admin_no_exemption():
		d = _mr("Purchase", "Not Submitted", "", flag=False)
		try:
			e.mr_before_submit_block_direct(d)  # session user IS Administrator here
			raise AssertionError("Administrator session must not bypass the gate")
		except frappe.ValidationError:
			frappe.clear_messages()
	test("submit gate: no Administrator exemption", t_admin_no_exemption)

	def t_flag_not_injectable():
		d = frappe.get_doc({"doctype": "Material Request", "material_request_type": "Purchase",
		                    "ts_mr_status": "Approved", "ts_mr_approval_route": "AVP MR App",
		                    "flags": {"ts_approval_workflow_call": True}})
		assert not d.flags.get("ts_approval_workflow_call"), "'flags' is a reserved key and must be ignored by get_doc(dict)"
	test("submit gate: a client dict cannot inject doc.flags", t_flag_not_injectable)

	# ── 3. route validation ──────────────────────────────────────────────────
	def t_route_wildcard():
		r = frappe.new_doc("TS MR Approval Route")
		r.route_name = "ZZ strict test (unsaved)"
		r.append("approval_steps", {"step_order": 1, "role": "Department Head", "action_type": "Review", "strict_step": 1})
		r.append("approval_steps", {"step_order": 2, "role": "AVP", "action_type": "Final Approve", "strict_step": 1})
		r._validate_strict_steps()  # no cost centres → only the final-step warning, no throw
		frappe.clear_messages()
	test("route validate: strict on final step warns, wildcard route never throws", t_route_wildcard)

	def t_route_untouched():
		r = frappe.new_doc("TS MR Approval Route")
		r.route_name = "ZZ strict test 2 (unsaved)"
		r.append("approval_steps", {"step_order": 1, "role": "Department Head", "action_type": "Review", "strict_step": 0})
		r.append("approval_steps", {"step_order": 2, "role": "AVP", "action_type": "Final Approve", "strict_step": 0})
		r.append("cost_centers", {"cost_center": frappe.db.get_value("Cost Center", {"is_group": 0}, "name")})
		r._validate_strict_steps()  # nothing strict → returns before any lookup
	test("route validate: no strict rows → no-op", t_route_untouched)

	# ── 4. schema ───────────────────────────────────────────────────────────
	def t_meta():
		f = frappe.get_meta("TS MR Approval Step").get_field("strict_step")
		assert f and f.fieldtype == "Check", "strict_step DocField missing"
		assert frappe.db.has_column("TS MR Approval Step", "strict_step"), "strict_step column missing"
	test("schema: strict_step field + column present", t_meta)

	frappe.db.rollback()
	frappe.destroy()


if __name__ == "__main__":
	print("=== v2.52.0 Strict Step tests ===")
	run()
	failed = [r for r in RESULTS if not r[1]]
	print(f"\nTotal: {len(RESULTS)} | Passed: {len(RESULTS) - len(failed)} | Failed: {len(failed)}")
	sys.exit(1 if failed else 0)
