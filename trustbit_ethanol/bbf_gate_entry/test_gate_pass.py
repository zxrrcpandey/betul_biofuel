import frappe
from frappe.utils import now_datetime


def execute():
	"""Test Gate Pass feature — 10 scenarios."""
	results = []

	def test(name, fn):
		frappe.set_user("Administrator")
		try:
			fn()
			results.append(("PASS", name, ""))
		except Exception as e:
			results.append(("FAIL", name, str(e)))
		frappe.db.rollback()

	# S1: Create Gate Pass Happy Path
	def s1():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Gate Pass"
		t.visitor_name = "Test Visitor 1"
		t.visit_purpose = "Visitor"
		t.destination = "Admin Office"
		t.contact_number = "9876543210"
		t.number_of_visitors = 1
		t.insert(ignore_permissions=True)
		assert t.entry_type == "Gate Pass"
		assert t.status == "Token Generated"
		assert t.gate_pass_status == "Inside Campus"
		assert t.g1_entry_time
		assert t.token_number.startswith("TKN-")
	test("S1: Create Gate Pass Happy Path", s1)

	# S2: Missing visitor_name fails
	def s2():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Gate Pass"
		t.visitor_name = ""
		t.visit_purpose = "Visitor"
		t.destination = "Admin Office"
		try:
			t.insert(ignore_permissions=True)
			raise Exception("Should have thrown")
		except frappe.exceptions.ValidationError:
			pass
		except frappe.exceptions.MandatoryError:
			pass
	test("S2: Missing visitor_name fails", s2)

	# S3: Missing destination fails
	def s3():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Gate Pass"
		t.visitor_name = "Test"
		t.visit_purpose = "Visitor"
		t.destination = ""
		try:
			t.insert(ignore_permissions=True)
			raise Exception("Should have thrown")
		except frappe.exceptions.ValidationError:
			pass
		except frappe.exceptions.MandatoryError:
			pass
	test("S3: Missing destination fails", s3)

	# S4: Material token no regression
	def s4():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Material"
		t.purpose = "Raw Material"
		t.insert(ignore_permissions=True)
		assert t.entry_type == "Material"
		assert t.status == "Token Generated"
		assert not t.gate_pass_status
	test("S4: Material token no regression", s4)

	# S5: G2 entry on non-G2 destination fails
	def s5():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Gate Pass"
		t.visitor_name = "Test G2"
		t.visit_purpose = "Admin"
		t.destination = "Admin Office"
		t.insert(ignore_permissions=True)
		try:
			t.g2_log_entry()
			raise Exception("Should fail - no G2 checkpoint")
		except frappe.exceptions.ValidationError:
			pass
	test("S5: G2 entry on non-G2 dest fails", s5)

	# S6: G2 entry + exit on BBF Plant
	def s6():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Gate Pass"
		t.visitor_name = "G2 Test"
		t.visit_purpose = "Maintenance"
		t.destination = "BBF Plant"
		t.insert(ignore_permissions=True)
		assert t.gate_pass_status == "Inside Campus"
		t.g2_log_entry()
		t.reload()
		assert t.gate_pass_status == "Inside Plant", f"Got {t.gate_pass_status}"
		assert t.g2_entry_time_gp
		assert t.g2_entry_by == "Administrator"
		t.g2_log_exit()
		t.reload()
		assert t.gate_pass_status == "Inside Campus", f"Got {t.gate_pass_status}"
		assert t.g2_exit_time_gp
		assert t.total_plant_time
	test("S6: G2 entry + exit on BBF Plant", s6)

	# S7: Exit while Inside Plant fails
	def s7():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Gate Pass"
		t.visitor_name = "Still In Plant"
		t.visit_purpose = "Electrician"
		t.destination = "BBF Plant"
		t.insert(ignore_permissions=True)
		t.g2_log_entry()
		t.reload()
		try:
			t.mark_exit()
			raise Exception("Should fail - still inside plant")
		except frappe.exceptions.ValidationError:
			pass
	test("S7: Exit while Inside Plant fails", s7)

	# S8: Full lifecycle G1>G2>G2>G1
	def s8():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Gate Pass"
		t.visitor_name = "Full Lifecycle"
		t.visit_purpose = "Contractor"
		t.destination = "BBF Plant"
		t.host_name = "Mr. Sharma"
		t.number_of_visitors = 2
		t.insert(ignore_permissions=True)
		t.g2_log_entry()
		t.reload()
		t.g2_log_exit()
		t.reload()
		t.mark_exit()
		t.reload()
		assert t.gate_pass_status == "Exited"
		assert t.status == "Exited"
		assert t.g1_exit_time
		assert t.total_campus_time
		assert t.total_plant_time
		assert t.total_turnaround_minutes >= 0  # 0 in tests (same-second), >0 in production
	test("S8: Full lifecycle G1>G2>G2>G1", s8)

	# S9: create_grn on Gate Pass fails
	def s9():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Gate Pass"
		t.visitor_name = "GRN Test"
		t.visit_purpose = "Visitor"
		t.destination = "Admin Office"
		t.insert(ignore_permissions=True)
		try:
			t.create_grn()
			raise Exception("Should fail - no GRN for Gate Pass")
		except frappe.exceptions.ValidationError:
			pass
	test("S9: create_grn on Gate Pass fails", s9)

	# S10: Double G2 entry fails
	def s10():
		t = frappe.new_doc("BBF Token")
		t.entry_type = "Gate Pass"
		t.visitor_name = "Double Entry"
		t.visit_purpose = "Maintenance"
		t.destination = "BBF Plant"
		t.insert(ignore_permissions=True)
		t.g2_log_entry()
		t.reload()
		try:
			t.g2_log_entry()
			raise Exception("Should fail - double entry")
		except frappe.exceptions.ValidationError:
			pass
	test("S10: Double G2 entry fails", s10)

	# Print results
	passed = sum(1 for r in results if r[0] == "PASS")
	failed = sum(1 for r in results if r[0] == "FAIL")
	print(f"\n{'=' * 60}")
	print(f"RESULTS: {passed}/10 PASSED, {failed}/10 FAILED")
	print("=" * 60)
	for r in results:
		icon = "OK" if r[0] == "PASS" else "XX"
		detail = f" -- {r[2]}" if r[2] else ""
		print(f"  [{icon}] {r[1]}{detail}")
