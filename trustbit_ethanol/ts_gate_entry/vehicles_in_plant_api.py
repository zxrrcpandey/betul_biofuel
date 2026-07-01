"""v2.8.3 Vehicles In Plant — API module (sibling of the custom page folder).

Follows the stores_receiving_api.py pattern: page folder holds only
json/html/js per Lesson 163 (no __init__.py, no embedded controller).

Whitelisted GET endpoint returning tokens currently on premises (Material only).

Statuses considered "on premises":
  - G2 Entered (just came in)
  - Gross Weighed / Quality Done / Graded / Unloading (being processed)
  - Tare Weighed / GRN Created (waiting for G2 exit)
  - Plant Exited (left plant area, still on campus — waiting for G1 final exit)

Excludes: G1 Entered (not yet at G2), Campus Exited / Exited (fully gone).

Security: standard whitelist (GET). Role guard also enforced server-side
(G1 Security / G2 Gate Operator / Stores User / IT Head / System Manager /
CTO / Admin Reception / Administrator).
"""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, time_diff_in_seconds


ON_PREMISES_STATUSES = (
	"G2 Entered",
	"Gross Weighed",
	"Quality Done",
	"Graded",
	"Unloading",
	"Tare Weighed",
	"GRN Created",
	"Plant Exited",
)


ALLOWED_ROLES = {
	"G1 Security",
	"G2 Gate Operator",
	"Stores User",
	"IT Head",
	"System Manager",
	"CTO",
	"Admin Reception",
	"Administrator",
}


@frappe.whitelist()
def get_vehicles_in_plant():
	"""Return the live list of Material tokens currently on premises.

	Columns returned per row:
	  name, token_number, vehicle_number, driver_name, supplier_name,
	  purchase_order, g2_mat_entry_time, time_on_premises_min, status

	driver_name is read directly from TS Token (real column).
	supplier_name is sourced via the linked TS Gate Entry -> Purchase Order.
	"""
	# Fix 3 (Phase 4 audit): role guard. Page-level roles list already restricts
	# navigation but the API must independently authorize direct calls.
	roles = set(frappe.get_roles())
	if not (roles & ALLOWED_ROLES):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	# Confidentiality leak-plug: G1/G2/Stores roles are NOT on the PO
	# confidentiality allow-list — blank the PO name + supplier for any
	# maize-confidential PO so the gate dashboard doesn't expose it.
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential
	_sees_conf_po = user_sees_confidential("Purchase Order")

	# Pull only real columns on tabTS Token. driver_name IS a real column;
	# supplier_name is NOT — it comes from Purchase Order via TS Gate Entry.
	tokens = frappe.db.sql(
		"""
		SELECT
			t.name,
			t.token_number,
			t.vehicle_number,
			t.driver_name,
			t.g2_mat_entry_time,
			t.g1_entry_time,
			t.status
		FROM `tabTS Token` t
		WHERE t.entry_type = 'Material'
		  AND t.status IN %(statuses)s
		ORDER BY COALESCE(t.g2_mat_entry_time, t.g1_entry_time) ASC
		LIMIT 200
		""",
		{"statuses": tuple(ON_PREMISES_STATUSES)},
		as_dict=True,
	)

	now = now_datetime()
	out = []
	for t in tokens:
		anchor = t.get("g2_mat_entry_time") or t.get("g1_entry_time")
		minutes = None
		if anchor:
			try:
				minutes = int(flt(time_diff_in_seconds(now, anchor)) // 60)
			except Exception:
				minutes = None

		# Look up the submitted Gate Entry to find the PO and supplier.
		ge = frappe.db.get_value(
			"TS Gate Entry",
			{"token_number": t["name"], "docstatus": 1},
			["purchase_order"],
			as_dict=True,
		)
		po_name = (ge and ge.get("purchase_order")) or ""
		supplier_name = ""
		if po_name:
			# Hide a confidential PO (name + supplier) from non-allowed gate roles.
			if not _sees_conf_po and int(
				frappe.db.get_value("Purchase Order", po_name, "ts_confidential") or 0
			):
				po_name = ""
			else:
				supplier_name = frappe.db.get_value("Purchase Order", po_name, "supplier_name") or ""

		out.append({
			"name": t["name"],
			"token_number": t.get("token_number") or "",
			"vehicle_number": t.get("vehicle_number") or "",
			"driver_name": t.get("driver_name") or "",
			"supplier_name": supplier_name,
			"purchase_order": po_name,
			"g2_mat_entry_time": t.get("g2_mat_entry_time"),
			"time_on_premises_min": minutes,
			"status": t.get("status") or "",
		})

	return out
