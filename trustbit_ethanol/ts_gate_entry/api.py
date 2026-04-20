import frappe
from frappe.utils import now_datetime, time_diff_in_seconds, getdate


@frappe.whitelist()
def get_g2_print_mode():
	"""Return TS Settings.g2_print_mode for G2 operators who don't have
	TS Settings read permission. Returns ONLY the g2_print_mode value,
	no other sensitive settings fields exposed. Called from ts_gate_entry.js
	when rendering the Print PDF button for G2-only users (Lesson 168).
	"""
	val = frappe.db.get_single_value("TS Settings", "g2_print_mode")
	return {"g2_print_mode": val or "Detailed + Slip"}


@frappe.whitelist()
def get_two_pass_flag():
	"""v2.8.3 Lesson 168: role-scoped getter for ts_two_pass_gates_enabled.
	Direct frappe.db.get_single_value from JS fails for roles (G2 Gate Operator, CTO)
	without TS Settings read perm. Exposes ONLY the boolean — no other settings fields.
	"""
	try:
		return {"enabled": bool(frappe.db.get_single_value("TS Settings", "ts_two_pass_gates_enabled"))}
	except Exception:
		return {"enabled": False}


@frappe.whitelist()
def get_purchase_orders(po_id=None, po_date=None, **kwargs):
	filters = {"docstatus": 1, "status": ["not in", ["Closed", "Cancelled", "Completed"]], "per_received": ["<", 100]}

	if po_id:
		filters["name"] = ["like", f"%{po_id}%"]
	if po_date:
		filters["transaction_date"] = po_date

	return frappe.get_all(
		"Purchase Order",
		filters=filters,
		fields=["name", "supplier", "supplier_name", "transaction_date", "total_qty", "grand_total", "per_received"],
		order_by="transaction_date desc",
		limit=20
	)


@frappe.whitelist()
def get_weighbridge_tokens(doctype, txt, searchfield, start, page_len, filters):
	"""Return tokens eligible for weighbridge:
	- Stock IN: Raw Material OR Non-RM with requires_weighing (status=PO Linked)
	- Stock OUT: SI Linked, Tare Recorded, or Loading Done
	"""
	return frappe.db.sql("""
		SELECT t.name, t.token_number, t.purpose, t.entry_date, t.stock_direction
		FROM `tabTS Token` t
		WHERE t.entry_type = 'Material'
		AND (
			/* Stock IN: PO Linked + (Raw Material OR requires_weighing) */
			(t.status = 'PO Linked' AND (
				t.purpose = 'Raw Material'
				OR EXISTS (
					SELECT 1 FROM `tabTS Gate Entry` ge
					WHERE ge.token_number = t.name
					AND ge.docstatus = 1
					AND ge.requires_weighing = 1
				)
			))
			OR
			/* Stock OUT: eligible statuses */
			(t.stock_direction = 'Stock OUT' AND t.status IN ('SI Linked', 'Tare Recorded', 'Loading Done'))
		)
		AND (t.name LIKE %(txt)s OR t.token_number LIKE %(txt)s)
		ORDER BY t.creation DESC
		LIMIT %(start)s, %(page_len)s
	""", {"txt": f"%{txt}%", "start": start, "page_len": page_len})


@frappe.whitelist()
def check_sla_breaches():
	settings = frappe.get_single("TS Settings")
	threshold = settings.sla_threshold_minutes or 30
	escalation_email = settings.escalation_email

	active_tokens = frappe.get_all(
		"TS Token",
		filters={"status": ["not in", ["Exited", "Campus Exited", "Token Generated"]]},
		fields=["name", "token_number", "status", "g1_entry_time", "g2_link_time",
				"wb_gross_time", "quality_time", "grading_time",
				"unload_start_time", "unload_end_time",
				"wb_tare_time", "grn_time"]
	)

	breaches = []
	now = now_datetime()

	for token in active_tokens:
		last_timestamp = _get_last_timestamp(token)
		if last_timestamp:
			minutes_at_stage = time_diff_in_seconds(now, last_timestamp) / 60
			if minutes_at_stage > threshold:
				breaches.append({
					"token": token.token_number,
					"status": token.status,
					"minutes": round(minutes_at_stage, 1)
				})

	if breaches and escalation_email:
		_send_sla_alert(breaches, escalation_email, threshold)

	return breaches


def _get_last_timestamp(token):
	timestamp_map = {
		"PO Linked": token.g2_link_time,
		"Gross Weighed": token.wb_gross_time,
		"Quality Done": token.quality_time,
		"Graded": token.grading_time,
		# v2.8.0: "Unloading" state retired; legacy tokens may still have unload_start_time
		"Tare Weighed": token.wb_tare_time,
		"GRN Created": token.grn_time,
		# Stock OUT statuses
		"SI Linked": token.g2_link_time,
		"Tare Recorded": token.wb_tare_time,
		"Gross Recorded": token.wb_gross_time,
		"Dispatch Ready": token.wb_gross_time,
	}
	return timestamp_map.get(token.status)


def _send_sla_alert(breaches, email, threshold):
	rows = ""
	for b in breaches:
		rows += f"<tr><td>{b['token']}</td><td>{b['status']}</td><td>{b['minutes']} min</td></tr>"

	message = f"""
	<h3>Stuck Vehicle Alert - Tokens exceeding {threshold} minutes (CTL Breach)</h3>
	<table border="1" cellpadding="5" cellspacing="0">
		<thead><tr><th>Token</th><th>Stage</th><th>Time at Stage</th></tr></thead>
		<tbody>{rows}</tbody>
	</table>
	"""

	frappe.sendmail(
		recipients=[email],
		subject=f"TS Gate Entry - Stuck Vehicle Alert ({len(breaches)} tokens)",
		message=message
	)


@frappe.whitelist()
def get_po_lifecycle(po_name):
	"""Return complete lifecycle data for all deliveries against a Purchase Order."""
	if not po_name or not frappe.db.exists("Purchase Order", po_name):
		return {"deliveries": [], "total": 0}

	# Find gate entries linked to this PO
	# 1. Via po_list child table (multi-PO support)
	ge_from_child = frappe.db.get_all(
		"TS Gate Entry PO",
		filters={"purchase_order": po_name, "docstatus": 1},
		pluck="parent"
	)
	# 2. Via legacy purchase_order field
	ge_from_legacy = frappe.db.get_all(
		"TS Gate Entry",
		filters={"purchase_order": po_name, "docstatus": 1},
		pluck="name"
	)
	ge_names = list(set(ge_from_child + ge_from_legacy))

	if not ge_names:
		return {"deliveries": [], "total": 0}

	deliveries = []
	for ge_name in ge_names:
		ge = frappe.db.get_value("TS Gate Entry", ge_name,
			["name", "token_number", "stock_direction", "material_flow",
			 "requires_weighing", "docstatus"],
			as_dict=True)
		if not ge or ge.docstatus != 1 or not ge.token_number:
			continue

		token = frappe.db.get_value("TS Token", ge.token_number,
			["name", "token_number", "status", "vehicle_number", "entry_date",
			 "g1_entry_time", "g1_exit_time", "stock_direction", "purpose",
			 "purchase_receipt", "delivery_note"],
			as_dict=True)
		if not token:
			continue

		# Downstream documents
		wb = frappe.db.get_all("TS Weighbridge Log",
			filters={"token_number": token.name},
			fields=["name", "status", "gross_weight", "tare_weight", "net_weight"],
			limit=1)
		qi = frappe.db.get_all("TS Quality Inspection",
			filters={"token_number": token.name},
			fields=["name", "status", "grade"],
			limit=1)
		ds = frappe.db.get_all("TS Deduction Sheet",
			filters={"token_number": token.name},
			fields=["name", "status"],
			limit=1)
		# TS Unloading Entry removed in v2.8.0 — no lookup
		ue = []
		mi = frappe.db.get_all("TS Material Inspection",
			filters={"token_number": token.name},
			fields=["name", "status"],
			limit=1)
		pr = frappe.db.get_all("Purchase Receipt",
			filters={"ts_token": token.name, "docstatus": ["!=", 2]},
			fields=["name", "status"],
			limit=1)

		steps = _build_lifecycle_steps(
			token.status,
			ge.stock_direction or "Stock IN",
			ge.material_flow or token.purpose,
			ge.requires_weighing
		)

		delivery = {
			"gate_entry": ge.name,
			"token": token.name,
			"token_number": token.token_number,
			"token_status": token.status,
			"vehicle_number": token.vehicle_number or "",
			"stock_direction": ge.stock_direction or "Stock IN",
			"material_flow": ge.material_flow or token.purpose or "",
			"entry_date": str(token.entry_date) if token.entry_date else "",
			"g1_entry_time": str(token.g1_entry_time) if token.g1_entry_time else "",
			"steps": steps,
			"docs": {
				"gate_entry": {"name": ge.name},
				"weighbridge": wb[0] if wb else None,
				"quality_inspection": qi[0] if qi else None,
				"deduction_sheet": ds[0] if ds else None,
				"unloading": ue[0] if ue else None,
				"material_inspection": mi[0] if mi else None,
				"purchase_receipt": pr[0] if pr else None,
				"delivery_note": {"name": token.delivery_note} if token.delivery_note else None,
			}
		}
		deliveries.append(delivery)

	deliveries.sort(key=lambda d: d.get("g1_entry_time", ""), reverse=True)
	return {"deliveries": deliveries, "total": len(deliveries)}


def _build_lifecycle_steps(token_status, stock_direction, material_flow, requires_weighing):
	"""Build lifecycle step definitions with completion status."""
	if stock_direction == "Stock OUT":
		steps = [
			{"label": "G1 Entry", "short": "G1"},
			{"label": "SI Linked", "short": "G2"},
			{"label": "Tare Recorded", "short": "Tare"},
			{"label": "Loading Done", "short": "Loading"},
			{"label": "Gross Recorded", "short": "Gross"},
			{"label": "Dispatch Ready", "short": "Dispatch"},
			{"label": "Exited", "short": "Exit"},
		]
		order = ["Token Generated", "SI Linked", "Tare Recorded",
				 "Loading Done", "Gross Recorded", "Dispatch Ready", "Exited"]
	elif material_flow == "Raw Material":
		steps = [
			{"label": "G1 Entry", "short": "G1"},
			{"label": "PO Linked", "short": "G2"},
			{"label": "Gross Weighed", "short": "Gross"},
			{"label": "Quality Done", "short": "QI"},
			{"label": "Graded", "short": "Grade"},
			{"label": "Tare Weighed", "short": "Tare"},
			{"label": "GRN Created", "short": "GRN"},
			{"label": "Exited", "short": "Exit"},
		]
		order = ["Token Generated", "PO Linked", "Gross Weighed",
				 "Quality Done", "Graded",
				 "Tare Weighed", "GRN Created", "Exited"]
	elif requires_weighing:
		# Non-RM with weighing
		steps = [
			{"label": "G1 Entry", "short": "G1"},
			{"label": "PO Linked", "short": "G2"},
			{"label": "Gross Weighed", "short": "Gross"},
			{"label": "Tare Weighed", "short": "Tare"},
			{"label": "GRN Created", "short": "GRN"},
			{"label": "Exited", "short": "Exit"},
		]
		order = ["Token Generated", "PO Linked", "Gross Weighed",
				 "Tare Weighed", "GRN Created", "Exited"]
	else:
		# Non-RM without weighing
		steps = [
			{"label": "G1 Entry", "short": "G1"},
			{"label": "PO Linked", "short": "G2"},
			{"label": "GRN Created", "short": "GRN"},
			{"label": "Exited", "short": "Exit"},
		]
		order = ["Token Generated", "PO Linked", "GRN Created", "Exited"]

	# v2.8.3: "Campus Exited" and "Plant Exited" are treated as the terminal
	# "Exited" for pipeline visualisation purposes. G1/G2 Entered collapse
	# to the existing "PO Linked" bucket so the tile layout does not change.
	_normalised = token_status
	if _normalised in ("Campus Exited", "Plant Exited"):
		_normalised = "Exited"
	elif _normalised in ("G1 Entered", "G2 Entered"):
		_normalised = "PO Linked"

	status_map = {s: i for i, s in enumerate(order)}
	current_idx = status_map.get(_normalised, 0)

	for i, step in enumerate(steps):
		if _normalised == "Exited":
			step["status"] = "done"
		elif i < current_idx:
			step["status"] = "done"
		elif i == current_idx:
			step["status"] = "current"
		else:
			step["status"] = "pending"

	return steps


# ═══════════════════════════════════════════════════════════════════════
#  WEIGHBRIDGE INTEGRATION — Fetch weight from local weighbridge
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def fetch_weighbridge_weight():
	"""Fetch current weight reading from the weighbridge hardware.
	Reads from the URL configured in TS Settings → Weighbridge URL.
	Returns weight in KG as a float.
	"""
	import requests
	import re

	settings = frappe.get_single("TS Settings")
	url = settings.weighbridge_url
	if not url:
		frappe.throw("Weighbridge URL not configured. Go to TS Settings → Weighbridge tab.")

	timeout = int(settings.weighbridge_timeout or 5)

	try:
		response = requests.get(url, timeout=timeout)
		response.raise_for_status()
	except requests.ConnectionError:
		frappe.throw("Cannot connect to weighbridge at {}. Check if the device is online.".format(
			frappe.utils.escape_html(url)))
	except requests.Timeout:
		frappe.throw("Weighbridge did not respond within {} seconds.".format(timeout))
	except requests.RequestException as e:
		frappe.throw("Weighbridge error: {}".format(frappe.utils.escape_html(str(e))))

	# Parse weight from response (HTML or plain text)
	raw = response.text.strip()

	# Extract numbers from response (handles HTML like "<html><body>012500</body></html>")
	numbers = re.findall(r'[\d.]+', raw)
	if not numbers:
		frappe.throw("Could not read weight from weighbridge response: {}".format(
			frappe.utils.escape_html(raw[:100])))

	# Take the largest number (the weight) — handles cases with multiple numbers
	weight = max(float(n) for n in numbers)

	return {"weight_kg": weight, "raw": raw[:50]}
