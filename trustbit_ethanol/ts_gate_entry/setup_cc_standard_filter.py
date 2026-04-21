"""Cost Center standard filter on PO + MR list views — v2.8.8.3.

CTO Rahul wants Cost Center to appear as a filter input in the
`.standard-filter-section` bar at the top of Purchase Order and
Material Request list views (alongside ID, Supplier, Status, etc.).

Frappe renders that bar by scanning DocType fields with
`in_standard_filter=1`. `cost_center` on PO and MR natively has
`in_standard_filter=0`, so it doesn't appear there by default.

This seed adds a Property Setter setting `in_standard_filter=1` on
the `cost_center` field of both DocTypes. Frappe then renders a
Link-autocomplete filter input in that row. Users can pick a
single CC (fast common case) OR use the existing + Filter button
with the `in` operator to pick multiple.

Idempotent — checks existence before insert, updates value if
already present.
"""

import frappe


_TARGETS = ("Purchase Order", "Material Request")


def seed_cc_standard_filter():
	"""Add Property Setter in_standard_filter=1 on cost_center for PO + MR."""
	for dt in _TARGETS:
		ps_name = f"{dt}-cost_center-in_standard_filter"
		try:
			if frappe.db.exists("Property Setter", ps_name):
				frappe.db.set_value("Property Setter", ps_name, "value", "1")
				frappe.logger().info(f"[seed_cc_std_filter] Updated {ps_name}")
			else:
				frappe.get_doc({
					"doctype": "Property Setter",
					"doctype_or_field": "DocField",
					"doc_type": dt,
					"field_name": "cost_center",
					"property": "in_standard_filter",
					"value": "1",
					"property_type": "Check",
				}).insert(ignore_if_duplicate=True, ignore_permissions=True)
				frappe.logger().info(f"[seed_cc_std_filter] Created {ps_name}")
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"seed_cc_std_filter on {dt}: {e}",
				title="seed_cc_standard_filter",
			)

		frappe.clear_cache(doctype=dt)
