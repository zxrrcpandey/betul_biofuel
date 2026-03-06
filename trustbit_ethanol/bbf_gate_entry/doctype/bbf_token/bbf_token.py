import random
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, time_diff_in_seconds, getdate, nowtime


class BBFToken(Document):
	def before_insert(self):
		self.generate_token_number()
		self.g1_entry_time = now_datetime()
		self.entry_date = getdate()
		self.entry_time = nowtime()
		self.status = "Token Generated"
		self._auto_create_vehicle_master()

	def generate_token_number(self):
		date_part = getdate().strftime("%y%m%d")
		settings = frappe.get_single("BBF Settings")
		digits = settings.token_suffix_digits or 4
		min_val = 10 ** (digits - 1)
		max_val = (10 ** digits) - 1

		for _ in range(100):
			suffix = random.randint(min_val, max_val)
			token = f"TKN-{date_part}-{suffix}"
			if not frappe.db.exists("BBF Token", token):
				self.token_number = token
				return

		frappe.throw("Could not generate unique token number. Please try again.")

	def _auto_create_vehicle_master(self):
		if self.vehicle_number and not frappe.db.exists("BBF Vehicle Master", self.vehicle_number):
			vehicle = frappe.get_doc({
				"doctype": "BBF Vehicle Master",
				"vehicle_number": self.vehicle_number,
				"vehicle_type": self.vehicle_type
			})
			vehicle.insert(ignore_permissions=True)

	def validate(self):
		self.calculate_turnaround()

	def calculate_turnaround(self):
		self.g1_to_g2_minutes = self._diff_minutes(self.g1_entry_time, self.g2_link_time)
		self.g2_to_wb_minutes = self._diff_minutes(self.g2_link_time, self.wb_gross_time)
		self.wb_to_unload_minutes = self._diff_minutes(self.wb_gross_time, self.unload_start_time)
		self.unloading_duration_minutes = self._diff_minutes(self.unload_start_time, self.unload_end_time)
		self.unload_to_tare_minutes = self._diff_minutes(self.unload_end_time, self.wb_tare_time)
		self.tare_to_quality_minutes = self._diff_minutes(self.wb_tare_time, self.quality_time)
		self.quality_to_grn_minutes = self._diff_minutes(self.quality_time, self.grn_time)
		self.total_turnaround_minutes = self._diff_minutes(self.g1_entry_time, self.g1_exit_time)

	@staticmethod
	def _diff_minutes(start, end):
		if start and end:
			diff = time_diff_in_seconds(end, start)
			return round(diff / 60, 1)
		return 0

	@frappe.whitelist()
	def mark_exit(self):
		self.g1_exit_time = now_datetime()
		self.status = "Exited"
		self.save(ignore_permissions=True)
		self._update_vehicle_master()
		self._update_transport_master()

	def _update_vehicle_master(self):
		if not self.vehicle_number or not frappe.db.exists("BBF Vehicle Master", self.vehicle_number):
			return

		vehicle = frappe.get_doc("BBF Vehicle Master", self.vehicle_number)
		vehicle.total_trips = (vehicle.total_trips or 0) + 1
		vehicle.last_visit_date = getdate()

		if self.total_turnaround_minutes:
			prev_total = (vehicle.avg_turnaround_minutes or 0) * max((vehicle.total_trips - 1), 1)
			vehicle.avg_turnaround_minutes = round(
				(prev_total + self.total_turnaround_minutes) / vehicle.total_trips, 1
			)

		vehicle.save(ignore_permissions=True)

	def _update_transport_master(self):
		gate_entry = frappe.db.get_value(
			"BBF Gate Entry", {"token_number": self.name}, "transporter"
		)
		if not gate_entry:
			return

		transporter = frappe.get_doc("BBF Transport Master", gate_entry)
		transporter.total_trips = (transporter.total_trips or 0) + 1
		transporter.last_trip_date = getdate()

		if self.total_turnaround_minutes:
			prev_total = (transporter.avg_turnaround_minutes or 0) * max((transporter.total_trips - 1), 1)
			transporter.avg_turnaround_minutes = round(
				(prev_total + self.total_turnaround_minutes) / transporter.total_trips, 1
			)

		transporter.save(ignore_permissions=True)
