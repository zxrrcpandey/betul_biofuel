import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, time_diff_in_seconds


class TSUnloadingEntry(Document):
	def before_insert(self):
		self.validate_token_status()
		self.auto_fetch_from_gate_entry()

	def validate_token_status(self):
		if not self.token_number:
			return
		token_status = frappe.db.get_value("TS Token", self.token_number, "status")
		if token_status not in ("Graded", "Quality Done"):
			frappe.throw(f"Token {self.token_number} is at stage '{token_status}'. Unloading can only start after Quality Inspection and Grading are complete.")

	def auto_fetch_from_gate_entry(self):
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

			# Fetch all items (removed limit=1)
			items = frappe.db.get_all(
				"TS Gate Entry Item",
				filters={"parent": gate_entry.name},
				fields=["item_code", "item_name", "ordered_qty"]
			)
			if items:
				# For display, show first item (unloading entry has single item fields)
				self.item_code = items[0].item_code
				self.item_name = items[0].item_name
				# Sum all ordered quantities for expected_qty
				self.expected_qty = sum(i.ordered_qty or 0 for i in items)

		wb_log = frappe.db.get_value(
			"TS Weighbridge Log",
			{"token_number": self.token_number},
			"gross_weight"
		)
		if wb_log:
			self.expected_qty = wb_log

	@frappe.whitelist()
	def start_unloading(self):
		# Idempotency: already started
		if self.status in ("Unloading", "Completed"):
			return

		self.unloading_start = now_datetime()
		self.status = "Unloading"
		self.save(ignore_permissions=True)

		token = frappe.get_doc("TS Token", self.token_number)
		token.db_set({
			"unload_start_time": now_datetime(),
			"status": "Unloading"
		})

	@frappe.whitelist()
	def end_unloading(self):
		# Idempotency: already completed
		if self.status == "Completed":
			return

		if not self.unloading_start:
			frappe.throw("Unloading has not been started yet")

		self.unloading_end = now_datetime()
		self.status = "Completed"

		diff = time_diff_in_seconds(self.unloading_end, self.unloading_start)
		self.unloading_duration_minutes = round(diff / 60, 1)
		self.save(ignore_permissions=True)

		token = frappe.get_doc("TS Token", self.token_number)
		token.db_set("unload_end_time", now_datetime())

		# Enable tare weight on weighbridge log
		wb_log = frappe.db.get_value(
			"TS Weighbridge Log",
			{"token_number": self.token_number},
			"name"
		)
		if wb_log:
			frappe.db.set_value("TS Weighbridge Log", wb_log, "unloading_complete", 1)

		# Notify weighbridge
		frappe.publish_realtime(
			"msgprint",
			{
				"message": f"Token {self.token_number} | Unloading complete | Ready for tare weighment",
				"title": "Unloading Complete"
			}
		)
