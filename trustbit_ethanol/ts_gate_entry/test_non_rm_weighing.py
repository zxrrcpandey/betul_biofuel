import frappe
from frappe.utils import now_datetime, getdate, nowtime


def execute():
	"""Test Non-RM optional weighing — 5 scenarios."""
	results = []

	def test(name, fn):
		frappe.set_user("Administrator")
		try:
			fn()
			results.append(("PASS", name, ""))
		except Exception as e:
			results.append(("FAIL", name, str(e)))
		frappe.db.rollback()

	# S1: Gate Entry with requires_weighing routes to Weighbridge
	def s1():
		# Create a token
		token = frappe.new_doc("TS Token")
		token.entry_type = "Material"
		token.purpose = "Non-Raw Material"
		token.insert(ignore_permissions=True)

		# Create Gate Entry with requires_weighing
		ge = frappe.new_doc("TS Gate Entry")
		ge.token_number = token.name
		ge.material_flow = "Non-Raw Material"
		ge.requires_weighing = 1
		ge.validate()
		assert ge.route_to == "Weighbridge", f"Expected Weighbridge, got {ge.route_to}"
	test("S1: Non-RM with requires_weighing routes to Weighbridge", s1)

	# S2: Gate Entry without requires_weighing routes to Stores
	def s2():
		token = frappe.new_doc("TS Token")
		token.entry_type = "Material"
		token.purpose = "Non-Raw Material"
		token.insert(ignore_permissions=True)

		ge = frappe.new_doc("TS Gate Entry")
		ge.token_number = token.name
		ge.material_flow = "Non-Raw Material"
		ge.requires_weighing = 0
		ge.validate()
		assert ge.route_to == "Stores/Department", f"Expected Stores/Department, got {ge.route_to}"
	test("S2: Non-RM without weighing routes to Stores", s2)

	# S3: Weighbridge Log allows Non-RM token with requires_weighing
	def s3():
		token = frappe.new_doc("TS Token")
		token.entry_type = "Material"
		token.purpose = "Non-Raw Material"
		token.insert(ignore_permissions=True)

		ge = frappe.new_doc("TS Gate Entry")
		ge.token_number = token.name
		ge.material_flow = "Non-Raw Material"
		ge.requires_weighing = 1
		ge.insert(ignore_permissions=True)
		ge.submit()

		# Token should now be PO Linked
		token.reload()
		assert token.status == "PO Linked", f"Expected PO Linked, got {token.status}"

		# Create Weighbridge Log
		wb = frappe.new_doc("TS Weighbridge Log")
		wb.token_number = token.name
		wb.gross_weight = 15000
		wb.insert(ignore_permissions=True)
		assert wb.gate_entry == ge.name
		assert wb.material_flow == "Non-Raw Material"

		# Token should be Gross Weighed
		token.reload()
		assert token.status == "Gross Weighed", f"Expected Gross Weighed, got {token.status}"

		# Status should be Awaiting Tare Weight (not Awaiting Unloading)
		assert wb.status == "Awaiting Tare Weight", f"Expected Awaiting Tare Weight, got {wb.status}"
	test("S3: WB Log for Non-RM skips unloading, goes to Awaiting Tare", s3)

	# S4: Non-RM with weighing can't exit before tare
	def s4():
		token = frappe.new_doc("TS Token")
		token.entry_type = "Material"
		token.purpose = "Non-Raw Material"
		token.insert(ignore_permissions=True)

		ge = frappe.new_doc("TS Gate Entry")
		ge.token_number = token.name
		ge.material_flow = "Non-Raw Material"
		ge.requires_weighing = 1
		ge.insert(ignore_permissions=True)
		ge.submit()

		# Create WB with gross only
		wb = frappe.new_doc("TS Weighbridge Log")
		wb.token_number = token.name
		wb.gross_weight = 15000
		wb.insert(ignore_permissions=True)

		token.reload()
		assert token.status == "Gross Weighed"

		try:
			token.mark_exit()
			raise Exception("Should have thrown — weighing not complete")
		except frappe.exceptions.ValidationError:
			pass
	test("S4: Non-RM with weighing can't exit before tare", s4)

	# S5: Non-RM with weighing can exit after tare
	def s5():
		token = frappe.new_doc("TS Token")
		token.entry_type = "Material"
		token.purpose = "Non-Raw Material"
		token.insert(ignore_permissions=True)

		ge = frappe.new_doc("TS Gate Entry")
		ge.token_number = token.name
		ge.material_flow = "Non-Raw Material"
		ge.requires_weighing = 1
		ge.insert(ignore_permissions=True)
		ge.submit()

		wb = frappe.new_doc("TS Weighbridge Log")
		wb.token_number = token.name
		wb.gross_weight = 15000
		wb.insert(ignore_permissions=True)

		# Add tare weight
		wb.tare_weight = 5000
		wb.save(ignore_permissions=True)

		token.reload()
		assert token.status == "Tare Weighed", f"Expected Tare Weighed, got {token.status}"

		# Now exit should work
		token.mark_exit()
		token.reload()
		assert token.status == "Exited"
	test("S5: Non-RM with weighing can exit after tare", s5)

	# Print results
	passed = sum(1 for r in results if r[0] == "PASS")
	failed = sum(1 for r in results if r[0] == "FAIL")
	print(f"\n{'=' * 60}")
	print(f"RESULTS: {passed}/5 PASSED, {failed}/5 FAILED")
	print("=" * 60)
	for r in results:
		icon = "OK" if r[0] == "PASS" else "XX"
		detail = f" -- {r[2]}" if r[2] else ""
		print(f"  [{icon}] {r[1]}{detail}")
