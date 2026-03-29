import frappe
from frappe.model.document import Document


class TSTransportMaster(Document):
	def before_insert(self):
		if not self.transporter_code:
			count = frappe.db.count("TS Transport Master") + 1
			self.transporter_code = f"TRN-{count:04d}"


@frappe.whitelist()
def add_vehicle(transporter, vehicle_number, vehicle_type, capacity_kg=0):
	"""Create Vehicle Master and add to Transporter's vehicle list."""
	vehicle_number = vehicle_number.strip().upper()

	# Create Vehicle Master if doesn't exist
	if not frappe.db.exists("TS Vehicle Master", vehicle_number):
		frappe.get_doc({
			"doctype": "TS Vehicle Master",
			"vehicle_number": vehicle_number,
			"vehicle_type": vehicle_type,
			"capacity_kg": capacity_kg or 0,
			"transporter": transporter
		}).insert(ignore_permissions=True)
	else:
		# Update transporter on existing vehicle
		frappe.db.set_value("TS Vehicle Master", vehicle_number, "transporter", transporter)

	# Add to transporter's vehicle table if not already there
	doc = frappe.get_doc("TS Transport Master", transporter)
	existing = [row.vehicle for row in doc.vehicles]
	if vehicle_number not in existing:
		doc.append("vehicles", {"vehicle": vehicle_number})
		doc.save(ignore_permissions=True)

	return {"vehicle": vehicle_number}
