import frappe
from frappe.utils import now_datetime, flt


def execute():
	"""Test Material Inspection — 8 scenarios."""
	results = []

	def test(name, fn):
		frappe.set_user("Administrator")
		try:
			fn()
			results.append(("PASS", name, ""))
		except Exception as e:
			results.append(("FAIL", name, str(e)))
		frappe.db.rollback()

	def _create_non_rm_gate_entry():
		"""Helper: create token + gate entry for Non-RM."""
		token = frappe.new_doc("TS Token")
		token.entry_type = "Material"
		token.purpose = "Non-Raw Material"
		token.insert(ignore_permissions=True)

		ge = frappe.new_doc("TS Gate Entry")
		ge.token_number = token.name
		ge.material_flow = "Non-Raw Material"
		ge.insert(ignore_permissions=True)
		ge.submit()
		return token, ge

	# Ensure inspection is enabled
	def _enable_inspection():
		settings = frappe.get_single("TS Settings")
		settings.db_set("enable_material_inspection", 1)

	# S1: Non-RM Gate Entry auto-creates inspection
	def s1():
		_enable_inspection()
		token, ge = _create_non_rm_gate_entry()
		insp = frappe.get_all("TS Material Inspection", filters={"gate_entry": ge.name}, limit=1)
		assert len(insp) == 1, f"Expected 1 inspection, got {len(insp)}"
		insp_doc = frappe.get_doc("TS Material Inspection", insp[0].name)
		assert insp_doc.status == "Pending Inspection"
		assert insp_doc.token_number == token.name
		assert insp_doc.sla_1st_sent  # 1st notification sent
		assert insp_doc.notification_stage == 1
	test("S1: Non-RM Gate Entry creates inspection", s1)

	# S2: Raw Material Gate Entry does NOT create inspection
	def s2():
		_enable_inspection()
		token = frappe.new_doc("TS Token")
		token.entry_type = "Material"
		token.purpose = "Raw Material"
		token.insert(ignore_permissions=True)

		ge = frappe.new_doc("TS Gate Entry")
		ge.token_number = token.name
		ge.material_flow = "Raw Material"
		ge.insert(ignore_permissions=True)
		ge.submit()

		insp = frappe.get_all("TS Material Inspection", filters={"gate_entry": ge.name})
		assert len(insp) == 0, "Should not create inspection for Raw Material"
	test("S2: Raw Material skips inspection", s2)

	# S3: Approve All
	def s3():
		_enable_inspection()
		token, ge = _create_non_rm_gate_entry()
		insp = frappe.get_doc("TS Material Inspection", {"gate_entry": ge.name})
		insp.approve_all()
		insp.reload()
		assert insp.status == "Approved"
		assert insp.inspection_by == "Administrator"
		assert insp.inspection_time
		for item in insp.items:
			assert item.item_status == "Approved"
			assert flt(item.approved_qty) == flt(item.received_qty)
	test("S3: Approve All works", s3)

	# S4: Reject All requires reason
	def s4():
		_enable_inspection()
		token, ge = _create_non_rm_gate_entry()
		insp = frappe.get_doc("TS Material Inspection", {"gate_entry": ge.name})
		try:
			insp.reject_all(reason="")
			raise Exception("Should require reason")
		except frappe.exceptions.ValidationError:
			pass
		insp.reject_all(reason="Damaged items")
		insp.reload()
		assert insp.status == "Rejected"
		assert insp.rejection_reason == "Damaged items"
	test("S4: Reject All requires reason", s4)

	# S5: Item-wise inspection
	def s5():
		_enable_inspection()
		token, ge = _create_non_rm_gate_entry()
		insp = frappe.get_doc("TS Material Inspection", {"gate_entry": ge.name})
		# Set first item to Approved
		if insp.items:
			insp.items[0].item_status = "Approved"
			insp.items[0].approved_qty = insp.items[0].received_qty
		insp.submit_itemwise()
		insp.reload()
		assert insp.status in ("Approved", "Partially Approved")
	test("S5: Item-wise inspection works", s5)

	# S6: GRN blocked when inspection is Pending
	def s6():
		_enable_inspection()
		token, ge = _create_non_rm_gate_entry()
		# Simulate token at Tare Weighed
		token.db_set("status", "Tare Weighed")
		token.reload()
		try:
			token.create_grn()
			raise Exception("Should block GRN — inspection pending")
		except frappe.exceptions.ValidationError as e:
			assert "pending" in str(e).lower()
	test("S6: GRN blocked when inspection Pending", s6)

	# S7: GRN blocked when inspection is Rejected
	def s7():
		_enable_inspection()
		token, ge = _create_non_rm_gate_entry()
		insp = frappe.get_doc("TS Material Inspection", {"gate_entry": ge.name})
		insp.reject_all(reason="Bad quality")
		token.db_set("status", "Tare Weighed")
		token.reload()
		try:
			token.create_grn()
			raise Exception("Should block GRN — inspection rejected")
		except frappe.exceptions.ValidationError as e:
			assert "rejected" in str(e).lower()
	test("S7: GRN blocked when inspection Rejected", s7)

	# S8: GRN allowed when inspection Approved or Auto-Proceeded
	def s8():
		_enable_inspection()
		token, ge = _create_non_rm_gate_entry()
		insp = frappe.get_doc("TS Material Inspection", {"gate_entry": ge.name})
		insp.approve_all()
		# GRN check should pass (won't throw on inspection)
		# We can't fully create GRN without WB data, but we verify inspection check passes
		token.db_set("status", "Tare Weighed")
		token.reload()
		try:
			token.create_grn()
		except frappe.exceptions.ValidationError as e:
			# Should fail on something AFTER inspection check (e.g., no gate entry or WB data)
			assert "inspection" not in str(e).lower(), f"Should not fail on inspection: {e}"
	test("S8: GRN allowed when inspection Approved", s8)

	# Print results
	passed = sum(1 for r in results if r[0] == "PASS")
	failed = sum(1 for r in results if r[0] == "FAIL")
	print(f"\n{'=' * 60}")
	print(f"RESULTS: {passed}/8 PASSED, {failed}/8 FAILED")
	print("=" * 60)
	for r in results:
		icon = "OK" if r[0] == "PASS" else "XX"
		detail = f" -- {r[2]}" if r[2] else ""
		print(f"  [{icon}] {r[1]}{detail}")
