import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, flt


class TSQualityInspection(Document):
	def before_insert(self):
		self.inspector = frappe.session.user
		self.validate_token_status()
		self.auto_fetch_references()

	def validate_token_status(self):
		if not self.token_number:
			return
		token_status = frappe.db.get_value("TS Token", self.token_number, "status")
		if token_status not in ("Gross Weighed",):
			frappe.throw(f"Token {self.token_number} is at stage '{token_status}'. Quality Inspection can only be created for tokens with status 'Gross Weighed'.")

	def auto_fetch_references(self):
		if not self.token_number:
			return

		gate_entry = frappe.db.get_value(
			"TS Gate Entry",
			{"token_number": self.token_number, "docstatus": 1},
			["name", "purchase_order"],
			as_dict=True
		)
		if gate_entry:
			self.gate_entry = gate_entry.name
			self.purchase_order = gate_entry.purchase_order

			# Fetch first item from gate entry
			items = frappe.db.get_all(
				"TS Gate Entry Item",
				filters={"parent": gate_entry.name},
				fields=["item_code", "item_name"],
				limit=1
			)
			if items:
				self.item_code = items[0].item_code
				self.item_name = items[0].item_name

	def validate(self):
		self.calculate_variances()

	def calculate_variances(self):
		if self.item_category == "Coal":
			if self.actual_gcv and flt(self.po_gcv):
				self.gcv_variance_percent = round(
					((flt(self.actual_gcv) - flt(self.po_gcv)) / flt(self.po_gcv)) * 100, 2
				)
			if self.po_moisture_percent and self.actual_moisture_percent:
				self.moisture_variance_percent = round(
					self.actual_moisture_percent - self.po_moisture_percent, 3
				)

	@frappe.whitelist()
	def complete_inspection(self):
		# Idempotency: already completed
		if self.status in ("Completed", "Rejected"):
			return {"status": self.status}

		if not self.decision:
			frappe.throw("Please set a Decision (Accept/Reject/Hold) before completing")
		if not self.grade:
			frappe.throw("Please set a Grade before completing")

		self.status = "Completed" if self.decision != "Reject" else "Rejected"
		self.save(ignore_permissions=True)

		# Update token status
		token = frappe.get_doc("TS Token", self.token_number)
		token.db_set({
			"quality_time": now_datetime(),
			"status": "Quality Done"
		})

		return {"status": self.status}
