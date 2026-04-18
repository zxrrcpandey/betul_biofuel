"""Stores Receiving Dashboard — API

Unified pending-GRN dashboard for Stores User across three flows:
  Section A — Tokens at "Tare Weighed" (weighbridge-complete, existing flow)
  Section B — Non-RM without weighing tokens (currently invisible gap)
  Section C — Approved Direct PO with no PR (no token flow)

All mutation endpoints are idempotent, role-gated, POST-only, and protected
against Lesson 162 audit-marker tampering.
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, getdate, time_diff_in_hours


READ_ROLES = {
	"Stores User", "Accounts Manager", "Accounts User",
	"IT Head", "System Manager", "Administrator",
	"CEO", "MD",
}

MUTATE_ROLES = {
	"Stores User", "Accounts Manager", "Accounts User",
	"IT Head", "System Manager", "Administrator",
}

# Inspection states that allow GRN creation. "Auto-Proceeded" and "Partially
# Approved" are EXCLUDED — Auto-Proceeded bypasses human QA (SLA timer), and
# Partially Approved means SOME items rejected (we don't have per-item filter
# yet, so block the whole batch for safety).
INSPECTION_PASS_STATES = {"Approved", "Not Required"}

# Token flow constants
STATUS_TARE_WEIGHED = "Tare Weighed"


def _copy_po_header_fields(pr, po, token=None):
	"""v2.8.1.3: copy PO header fields that ERPNext's make_purchase_receipt mapper
	would normally propagate. We build PR programmatically, so these need to be
	explicit. Covers tax template, project, cost center, terms, payment terms,
	addresses, contact, and the taxes child table.

	Safe to call regardless of whether fields are already set (only copies when
	PR field is empty/missing to avoid overwriting explicit values).

	v2.8.2: also propagates `custom_rst_number` from TS Token (which mirrored it
	from TS Weighbridge Log at Gross save) onto the Purchase Receipt header.
	`token` parameter is optional — when omitted (e.g., legacy callers), RST is
	simply not copied and the PR field stays empty (admin can backfill).
	"""
	HEADER_FIELDS = [
		"tax_category",
		"taxes_and_charges",
		"shipping_rule",
		"project",
		"cost_center",
		"terms",
		"tc_name",
		"payment_terms_template",
		"supplier_address",
		"contact_person",
		"shipping_address",
		"apply_discount_on",
		"additional_discount_percentage",
		"discount_amount",
		"base_discount_amount",
	]
	for f in HEADER_FIELDS:
		po_val = getattr(po, f, None)
		if po_val and not pr.get(f):
			pr.set(f, po_val)

	# Copy taxes child table (Purchase Taxes and Charges) — one-to-one mirror
	if not pr.get("taxes") and getattr(po, "taxes", None):
		for tax in po.taxes:
			pr.append("taxes", {
				"charge_type": tax.charge_type,
				"account_head": tax.account_head,
				"description": tax.description,
				"rate": tax.rate,
				"tax_amount": 0,  # let Frappe recalculate against PR items
				"cost_center": tax.cost_center,
				"category": tax.category,
				"add_deduct_tax": tax.add_deduct_tax,
				"included_in_print_rate": tax.included_in_print_rate,
				"row_id": tax.row_id,
				"account_currency": tax.account_currency,
			})

	# v2.8.2: propagate RST Number from TS Token (mirrored from Weighbridge Log)
	if token is not None:
		rst = token.get("custom_rst_number") if isinstance(token, dict) else getattr(token, "custom_rst_number", None)
		if rst and not pr.get("custom_rst_number"):
			pr.set("custom_rst_number", rst)


STATUS_GRN_CREATED = "GRN Created"
FLOW_NON_RM = "Non-Raw Material"
FLOW_TYPE_DIRECT_PO = "Direct PO"


def _check_read():
	roles = set(frappe.get_roles(frappe.session.user))
	if not (READ_ROLES & roles):
		frappe.throw(_("You do not have access to the Stores Receiving Dashboard."),
					 frappe.PermissionError)


def _check_mutate():
	roles = set(frappe.get_roles(frappe.session.user))
	if not (MUTATE_ROLES & roles):
		frappe.throw(_("You do not have permission to create GRN."),
					 frappe.PermissionError)


def _age_hours(dt):
	if not dt:
		return 0
	try:
		return round(time_diff_in_hours(now_datetime(), dt), 1)
	except Exception:
		return 0


def _get_direct_po_ccs():
	"""Return list of cost centers flagged as Direct PO in active CC Approval Configs."""
	rows = frappe.db.sql("""
		SELECT name FROM `tabTS CC Approval Config`
		WHERE is_active = 1 AND flow_type = %s
	""", (FLOW_TYPE_DIRECT_PO,), as_dict=True)
	return [r["name"] for r in rows]


def _get_inspection_gate_enabled():
	"""Read block_grn_on_inspection_pending from TS Settings — fail-closed.

	Frappe Singles return 0 (not None) for missing Check fields, so
	`getattr(..., 1)` fallback never triggers. We read `tabSingles` directly
	via raw SQL (Singles doctype has no `modified` column so ORM ordering
	fails) and default ON when the row is missing.
	"""
	rows = frappe.db.sql("""
		SELECT value FROM `tabSingles`
		WHERE doctype = 'TS Settings' AND field = 'block_grn_on_inspection_pending'
		LIMIT 1
	""")
	# Missing row → default ON (fail-closed, safer for a security gate)
	if not rows or rows[0][0] is None:
		return True
	return bool(cint(rows[0][0]))


# ═══════════════════════════════════════════════════════════════════════
#  READ ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_dashboard_data():
	"""Single-call endpoint returning all sections + counts."""
	_check_read()
	d = _fetch_section_d_drafts()
	a = _fetch_section_a_tokens()
	b = _fetch_section_b_non_weighing()
	return {
		"d": d,
		"a": a,
		"b": b,
		"counts": {
			"section_d_count": len(d),
			"section_a_count": len(a),
			"section_b_count": len(b),
			"total_pending": len(a) + len(b),
		},
	}


@frappe.whitelist()
def get_section_a_tokens():
	_check_read()
	return _fetch_section_a_tokens()


@frappe.whitelist()
def get_section_b_non_weighing():
	_check_read()
	return _fetch_section_b_non_weighing()


@frappe.whitelist()
def get_section_c_direct_po():
	_check_read()
	return _fetch_section_c_direct_po()


# ─── Internal fetch helpers (no auth — caller must check) ────────────

def _fetch_section_d_drafts():
	"""Draft PRs created via this dashboard that haven't been submitted yet.

	Shown at the top so users remember to review qty and submit.
	Includes both Section B (ts_non_weighing_grn=1) and Section C
	(ts_direct_po_grn=1) drafts.
	"""
	drafts = frappe.db.sql("""
		SELECT pr.name, pr.supplier_name, pr.posting_date, pr.creation,
		       pr.owner, pr.ts_token, pr.ts_gate_entry,
		       pr.ts_non_weighing_grn, pr.ts_direct_po_grn,
		       pr.ts_stores_dashboard_source, pr.grand_total, pr.currency
		FROM `tabPurchase Receipt` pr
		WHERE pr.docstatus = 0
		  AND (IFNULL(pr.ts_non_weighing_grn, 0) = 1
		       OR IFNULL(pr.ts_direct_po_grn, 0) = 1)
		ORDER BY pr.creation DESC
		LIMIT 100
	""", as_dict=True)

	result = []
	for pr in drafts:
		# Get items for detail pills
		items = frappe.db.get_all("Purchase Receipt Item",
			filters={"parent": pr["name"]},
			fields=["item_code", "item_name", "qty", "uom"],
			order_by="idx")

		# Get linked PO from items
		po_name = frappe.db.get_value("Purchase Receipt Item",
			{"parent": pr["name"]}, "purchase_order") or ""

		source = "Non-RM Token" if pr.get("ts_non_weighing_grn") else "Direct PO"

		result.append({
			"pr": pr["name"],
			"supplier": pr.get("supplier_name") or "",
			"po": po_name,
			"token": pr.get("ts_token") or "",
			"source": source,
			"total": flt(pr.get("grand_total")),
			"currency": pr.get("currency") or "INR",
			"items": items,
			"items_count": len(items),
			"created_by": pr.get("owner") or "",
			"age_hours": _age_hours(pr.get("creation")),
		})
	return result


def _fetch_section_a_tokens():
	tokens = frappe.db.sql("""
		SELECT
			t.name, t.vehicle_number, t.entry_time, t.status, t.purpose
		FROM `tabTS Token` t
		WHERE t.status = %s
		  AND t.docstatus != 2
		  AND (t.purchase_receipt IS NULL OR t.purchase_receipt = '')
		  AND t.entry_type = 'Material'
		ORDER BY t.entry_time ASC
		LIMIT 500
	""", (STATUS_TARE_WEIGHED,), as_dict=True)

	result = []
	for tok in tokens:
		ge = frappe.db.get_value("TS Gate Entry",
			{"token_number": tok["name"], "docstatus": 1},
			["name", "purchase_order", "supplier_name", "material_flow"],
			as_dict=True) or {}

		wb = frappe.db.get_value("TS Weighbridge Log",
			{"token_number": tok["name"]},
			["net_weight"], as_dict=True) or {}

		insp_status = _get_inspection_status_label(tok["name"])

		items = []
		if ge.get("name"):
			items = frappe.db.get_all("TS Gate Entry Item",
				filters={"parent": ge["name"]},
				fields=["item_code", "item_name", "ordered_qty", "uom"])

		result.append({
			"token": tok["name"],
			"vehicle": tok.get("vehicle_number") or "",
			"supplier": ge.get("supplier_name") or "",
			"po": ge.get("purchase_order") or "",
			"material_flow": ge.get("material_flow") or tok.get("purpose") or "",
			"net_weight": flt(wb.get("net_weight")),
			"inspection_status": insp_status,
			"items": items,
			"items_count": len(items),
			"age_hours": _age_hours(tok.get("entry_time")),
			"entry_time": str(tok.get("entry_time") or ""),
		})
	return result


def _fetch_section_b_non_weighing():
	gate_entries = frappe.db.sql("""
		SELECT ge.name, ge.token_number, ge.purchase_order, ge.supplier_name,
		       ge.material_flow, ge.creation
		FROM `tabTS Gate Entry` ge
		WHERE ge.docstatus = 1
		  AND ge.material_flow = %s
		  AND IFNULL(ge.requires_weighing, 0) = 0
		ORDER BY ge.creation ASC
		LIMIT 500
	""", (FLOW_NON_RM,), as_dict=True)

	result = []
	for ge in gate_entries:
		if not ge.get("token_number"):
			continue
		tok = frappe.db.get_value("TS Token", ge["token_number"],
			["name", "vehicle_number", "status", "purchase_receipt", "docstatus", "entry_time"],
			as_dict=True)
		if not tok:
			continue
		if tok.get("docstatus") == 2:
			continue
		if tok.get("purchase_receipt"):
			continue
		if tok.get("status") in (STATUS_GRN_CREATED, "Exited", "Cancelled"):
			continue

		insp_status = _get_inspection_status_label(tok["name"])

		items = frappe.db.get_all("TS Gate Entry Item",
			filters={"parent": ge["name"]},
			fields=["item_code", "item_name", "ordered_qty", "uom"])

		result.append({
			"token": tok["name"],
			"gate_entry": ge["name"],
			"vehicle": tok.get("vehicle_number") or "",
			"supplier": ge.get("supplier_name") or "",
			"po": ge.get("purchase_order") or "",
			"inspection_status": insp_status,
			"items": items,
			"items_count": len(items),
			"age_hours": _age_hours(tok.get("entry_time") or ge.get("creation")),
			"entry_time": str(tok.get("entry_time") or ge.get("creation") or ""),
		})
	return result


def _get_missing_items_for_po(po_name):
	"""Return list of item_codes referenced on PO that don't exist in Item master.

	Data-integrity check: POs sometimes reference items that were later
	deleted (demo data debris or bad cleanup scripts). make_purchase_receipt
	raises a generic DoesNotExistError in that case.
	"""
	rows = frappe.db.sql("""
		SELECT DISTINCT poi.item_code
		FROM `tabPurchase Order Item` poi
		LEFT JOIN `tabItem` i ON i.name = poi.item_code
		WHERE poi.parent = %s AND i.name IS NULL
	""", (po_name,), as_dict=True)
	return [r["item_code"] for r in rows]


def _fetch_section_c_direct_po():
	direct_ccs = _get_direct_po_ccs()
	if not direct_ccs or not all(isinstance(c, str) for c in direct_ccs):
		return []

	placeholders = ", ".join(["%s"] * len(direct_ccs))
	params = [*direct_ccs]
	pos = frappe.db.sql(f"""
		SELECT po.name, po.supplier, po.supplier_name, po.cost_center,
		       po.grand_total, po.currency, po.transaction_date,
		       po.per_received, po.status, po.company
		FROM `tabPurchase Order` po
		WHERE po.docstatus = 1
		  AND IFNULL(po.ts_approval_status, '') = 'Approved'
		  AND po.cost_center IN ({placeholders})
		  AND IFNULL(po.per_received, 0) < 100
		  AND po.status NOT IN ('Closed', 'Completed')
		ORDER BY po.transaction_date ASC
		LIMIT 500
	""", params, as_dict=True)

	# Exclude POs that already have an un-cancelled PR (catches the
	# draft-PR race: per_received only updates on PR submit, so we check
	# for ANY docstatus<2 PR against the PO).
	result = []
	for po in pos:
		has_pr = frappe.db.sql("""
			SELECT pri.name FROM `tabPurchase Receipt Item` pri
			INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
			WHERE pri.purchase_order = %s AND pr.docstatus < 2
			LIMIT 1
		""", (po["name"],))
		if has_pr:
			continue

		po_items = frappe.db.get_all("Purchase Order Item",
			filters={"parent": po["name"]},
			fields=["item_code", "item_name", "qty", "uom", "rate"],
			order_by="idx")
		missing_items = _get_missing_items_for_po(po["name"])

		result.append({
			"po": po["name"],
			"supplier": po.get("supplier_name") or po.get("supplier") or "",
			"cc": po.get("cost_center") or "",
			"total": flt(po.get("grand_total")),
			"currency": po.get("currency") or "INR",
			"per_received": flt(po.get("per_received")),
			"items_count": len(po_items),
			"items": po_items,
			"missing_items": missing_items,
			"has_missing_items": bool(missing_items),
			"approved_on": str(po.get("transaction_date") or ""),
			"age_hours": _age_hours(po.get("transaction_date")),
		})
	return result


def _get_inspection_status_label(token_name):
	"""Return a short label: Approved / Pending / On Hold / Rejected / Not Required.

	Fails CLOSED on import/lookup errors — returns "Error" which is NOT in
	INSPECTION_PASS_STATES, so a failing inspection module blocks GRN rather
	than silently allowing it.
	"""
	try:
		from trustbit_ethanol.ts_gate_entry.doctype.ts_material_inspection.ts_material_inspection import (
			get_inspection_status_for_token,
		)
	except ImportError as e:
		frappe.log_error(f"Inspection module import failed: {e}",
						 "stores_receiving_api._get_inspection_status_label")
		return "Error"

	try:
		insp = get_inspection_status_for_token(token_name)
	except Exception as e:
		frappe.log_error(f"Inspection lookup failed for {token_name}: {e}",
						 "stores_receiving_api._get_inspection_status_label")
		return "Error"

	if not insp:
		return "Not Required"
	return getattr(insp, "status", None) or (insp.get("status") if isinstance(insp, dict) else None) or "Unknown"


def _get_inspection_doc(token_name):
	"""Return the inspection doc/dict or None. Used for extracting .name for audit."""
	try:
		from trustbit_ethanol.ts_gate_entry.doctype.ts_material_inspection.ts_material_inspection import (
			get_inspection_status_for_token,
		)
		return get_inspection_status_for_token(token_name)
	except Exception:
		return None


# ═══════════════════════════════════════════════════════════════════════
#  MUTATION ENDPOINTS — POST only, gated, idempotent, fail-closed
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist(methods=["POST"])
def create_grn_for_weighed_token(token_name):
	"""Section A action — create Draft PR for a Tare-Weighed token.

	Uses weighbridge net weight (gross − tare) as pre-filled qty, converted
	to PO item UOM. Replicates the logic from the locked Token.create_grn()
	but creates a DRAFT so user can review/edit qty before submitting.
	Does NOT touch the locked ts_token.py create_grn().
	"""
	_check_mutate()

	if not token_name:
		frappe.throw(_("Token name is required"))

	frappe.db.sql("SELECT name FROM `tabTS Token` WHERE name = %s FOR UPDATE", (token_name,))
	token = frappe.get_doc("TS Token", token_name)

	if token.entry_type != "Material":
		frappe.throw(_("Only Material tokens can have GRN created"))
	if token.docstatus == 2:
		frappe.throw(_("Cannot create GRN for a cancelled token"))
	if token.purchase_receipt:
		frappe.throw(_("Purchase Receipt {0} already exists for this token").format(token.purchase_receipt))
	if token.status != STATUS_TARE_WEIGHED:
		frappe.throw(_("Token status must be 'Tare Weighed', currently '{0}'").format(token.status))

	# Inspection gate for Non-RM
	if token.purpose != "Raw Material":
		block_on_insp = _get_inspection_gate_enabled()
		insp_label = _get_inspection_status_label(token.name)
		if block_on_insp and insp_label not in INSPECTION_PASS_STATES:
			frappe.throw(_(
				"GRN blocked — Material Inspection is '{0}'. "
				"Ask Quality Lab to approve before receiving. "
				"Contact IT Head if an override is needed."
			).format(insp_label))

	# Gate Entry
	gate_entry = frappe.db.get_value("TS Gate Entry",
		{"token_number": token.name, "docstatus": 1},
		["name", "purchase_order", "transporter", "lr_number", "lr_date", "supplier_name"],
		as_dict=True)
	if not gate_entry:
		frappe.throw(_("No submitted Gate Entry found for this token"))
	if not gate_entry.purchase_order:
		frappe.throw(_("No Purchase Order linked in the Gate Entry"))

	po = frappe.get_doc("Purchase Order", gate_entry.purchase_order)
	po.check_permission("read")
	if po.docstatus != 1:
		frappe.throw(_("Purchase Order {0} is not submitted").format(po.name))

	# Weighbridge net weight
	wb_log = frappe.db.get_value("TS Weighbridge Log",
		{"token_number": token.name},
		["gross_weight", "tare_weight", "net_weight"],
		as_dict=True)
	if not wb_log or not wb_log.net_weight:
		frappe.throw(_("Weighbridge Log with net weight not found. Ensure both gross and tare weights are recorded."))

	# Gate Entry items
	ge_items = frappe.get_all("TS Gate Entry Item",
		filters={"parent": gate_entry["name"]},
		fields=["item_code", "item_name", "ordered_qty", "uom", "purchase_order"])
	if not ge_items:
		frappe.throw(_("No items found in Gate Entry"))

	# Pre-flight item existence check
	missing = [it["item_code"] for it in ge_items if not frappe.db.exists("Item", it["item_code"])]
	if missing:
		frappe.throw(_(
			"Cannot create GRN: the following items are missing from the Item master: {0}. "
			"Please ask IT Head / Item Manager to restore these items."
		).format(", ".join(missing)))

	# Deduction sheet (optional)
	deduction_sheet = frappe.db.get_value("TS Deduction Sheet",
		{"token_number": token.name, "status": "Approved"},
		["name", "net_weight", "net_payable", "total_deduction", "item_rate"],
		as_dict=True)

	settings = frappe.get_single("TS Settings")
	warehouse = settings.default_warehouse
	accepted_warehouse = settings.default_accepted_warehouse
	if not warehouse:
		frappe.throw(_("Default Warehouse is not set. Please contact IT Head."))

	net_weight_kg = flt(wb_log.net_weight)

	# Build PR items — use weighbridge net weight, same logic as locked create_grn
	pr_items = []
	for ge_item in ge_items:
		# Weight distribution for multi-item deliveries
		if len(ge_items) == 1:
			weight_kg = net_weight_kg
		else:
			total_ordered = sum(flt(i["ordered_qty"]) for i in ge_items)
			proportion = flt(ge_item["ordered_qty"]) / total_ordered if total_ordered else 1
			weight_kg = net_weight_kg * proportion

		item_uom = ge_item["uom"] or "Kg"

		# Convert KG to PO item UOM
		if item_uom == "Kg":
			received_qty = weight_kg
		else:
			conversion_factor = _get_uom_conversion_to_kg(ge_item["item_code"], item_uom)
			if conversion_factor and flt(conversion_factor) > 0:
				received_qty = flt(weight_kg / conversion_factor, 3)
			else:
				received_qty = weight_kg
				item_uom = "Kg"

		item_po_name = ge_item["purchase_order"] or po.name
		po_item = frappe.db.get_value("Purchase Order Item",
			{"parent": item_po_name, "item_code": ge_item["item_code"]},
			["name", "rate", "ts_delivery_location", "ts_item_remark"],
			as_dict=True)

		rate = flt(po_item.rate) if po_item else 0
		if not rate and deduction_sheet and deduction_sheet.item_rate:
			rate = flt(deduction_sheet.item_rate)

		# v2.8.1.2: propagate PO HEADER project to PR item (uniform across rows).
		# Some PO items have project=NULL — ERPNext rejects mixed project values
		# in the same PR. Header-level project is the source of truth.
		item_po_project = frappe.db.get_value("Purchase Order", item_po_name, "project") or po.project or ""

		pr_items.append({
			"item_code": ge_item["item_code"],
			"item_name": ge_item["item_name"],
			"qty": received_qty,
			"uom": item_uom,
			"stock_uom": item_uom,
			"rate": rate,
			"warehouse": accepted_warehouse or warehouse,
			"purchase_order": item_po_name,
			"purchase_order_item": po_item.name if po_item else None,
			"project": item_po_project,
			"ts_delivery_location": (po_item.ts_delivery_location if po_item else "") or "",
			"ts_item_remark": (po_item.ts_item_remark if po_item else "") or "",
		})

	pr = frappe.get_doc({
		"doctype": "Purchase Receipt",
		"supplier": po.supplier,
		"supplier_name": po.supplier_name,
		"posting_date": getdate(),
		"company": po.company,
		"currency": po.currency,
		"buying_price_list": po.buying_price_list,
		"set_warehouse": warehouse,
		"items": pr_items,
		"ts_token": token.name,
		"ts_gate_entry": gate_entry["name"],
		"lr_no": gate_entry["lr_number"] or "",
		"lr_date": gate_entry["lr_date"],
		"transporter_name": gate_entry["transporter"] or "",
		"ts_stores_dashboard_source": "Section A",
	})

	# v2.8.1.3: copy missing header fields that ERPNext's make_purchase_receipt
	# mapper would normally propagate (tax template, project, cost center, terms,
	# payment terms, addresses, contact, discount). We build PR programmatically
	# so these need explicit copying. taxes child table also copied below.
	# v2.8.2: `token` passed so RST Number flows through to PR.
	_copy_po_header_fields(pr, po, token)

	frappe.flags.in_stores_dashboard_mutation = True
	try:
		pr.flags.ignore_permissions = True
		pr.insert()
	finally:
		frappe.flags.in_stores_dashboard_mutation = False

	# Link token → PR (hide from Section A), defer status to on_submit hook
	token.db_set({"purchase_receipt": pr.name})

	esc = frappe.utils.escape_html
	try:
		pr.add_comment("Comment",
			_("Created via Stores Receiving Dashboard — Weighed token path. "
			  "Token: {0}. Gate Entry: {1}. Net Weight: {2} Kg. "
			  "Gross: {3} Kg, Tare: {4} Kg. Actor: {5}. "
			  "Draft PR created — please review quantities and submit.").format(
				esc(token.name), esc(gate_entry["name"]),
				net_weight_kg, flt(wb_log.gross_weight), flt(wb_log.tare_weight),
				esc(frappe.session.user),
			))
	except Exception as e:
		frappe.log_error(f"Audit comment failed on PR {pr.name}: {e}",
						 "stores_receiving_api.create_grn_for_weighed_token")

	frappe.db.commit()

	return {"purchase_receipt": pr.name, "docstatus": pr.docstatus}


def _get_uom_conversion_to_kg(item_code, uom):
	"""Get how many KG per 1 unit of the given UOM.

	Standalone version of TSToken._get_uom_conversion_to_kg — duplicated
	because the Token method is locked and instance-bound.
	"""
	if item_code:
		conversions = frappe.get_all("UOM Conversion Detail",
			filters={"parent": item_code},
			fields=["uom", "conversion_factor"])
		conv_map = {c.uom: flt(c.conversion_factor) for c in conversions}
		if uom in conv_map and "Kg" in conv_map:
			kg_factor = conv_map["Kg"]
			if kg_factor > 0:
				return conv_map[uom] / kg_factor

	global_factor = frappe.db.get_value("UOM Conversion Factor",
		{"from_uom": uom, "to_uom": "Kg"}, "value")
	if global_factor and flt(global_factor) > 0:
		return flt(global_factor)

	reverse_factor = frappe.db.get_value("UOM Conversion Factor",
		{"from_uom": "Kg", "to_uom": uom}, "value")
	if reverse_factor and flt(reverse_factor) > 0:
		return 1 / flt(reverse_factor)

	known = {"Quintal": 100, "MT": 1000, "Ton": 1000, "Gram": 0.001}
	return known.get(uom)


@frappe.whitelist(methods=["POST"])
def create_grn_for_non_weighing_token(token_name):
	"""Section B action — create PR for Non-RM no-weighing token.

	Uses PO ordered_qty (not weighbridge net weight).
	Hard-blocks on inspection status unless TS Settings.block_grn_on_inspection_pending is OFF.
	Idempotent via FOR UPDATE lock + purchase_receipt re-check.
	"""
	_check_mutate()

	if not token_name:
		frappe.throw(_("Token name is required"))

	# Lock the token row for update (idempotency against double-click)
	frappe.db.sql("SELECT name FROM `tabTS Token` WHERE name = %s FOR UPDATE", (token_name,))

	token = frappe.get_doc("TS Token", token_name)

	if token.entry_type != "Material":
		frappe.throw(_("Only Material tokens can have GRN created"))
	if token.docstatus == 2:
		frappe.throw(_("Cannot create GRN for a cancelled token"))
	if token.purchase_receipt:
		frappe.throw(_("Purchase Receipt {0} already exists for this token").format(token.purchase_receipt))
	if token.status in (STATUS_GRN_CREATED, "Exited", "Cancelled"):
		frappe.throw(_("Token status '{0}' does not allow GRN creation").format(token.status))

	# Find linked gate entry (must be Non-RM + not requiring weighing)
	ge_name = frappe.db.get_value("TS Gate Entry",
		{"token_number": token.name, "docstatus": 1},
		"name")
	if not ge_name:
		frappe.throw(_("No submitted Gate Entry found for this token"))
	gate_entry = frappe.get_doc("TS Gate Entry", ge_name)

	if (gate_entry.material_flow or "") != FLOW_NON_RM:
		frappe.throw(_("This action is only for Non-Raw Material gate entries. Use the Token form to create GRN for Raw Material."))
	if cint(gate_entry.get("requires_weighing") or 0) == 1:
		frappe.throw(_("This gate entry requires weighing. Complete weighbridge and use the Token form to create GRN."))

	if not gate_entry.purchase_order:
		frappe.throw(_("No Purchase Order linked in the Gate Entry"))

	# Load PO — get_doc enforces user read permission via User Permissions.
	# We then explicitly check_permission to be explicit and future-proof.
	po = frappe.get_doc("Purchase Order", gate_entry.purchase_order)
	po.check_permission("read")
	if po.docstatus != 1:
		frappe.throw(_("Purchase Order {0} is not submitted").format(po.name))

	# Inspection gate (fail-closed default ON)
	block_on_insp = _get_inspection_gate_enabled()
	insp_label = _get_inspection_status_label(token.name)
	inspection_id = None
	insp_doc = _get_inspection_doc(token.name)
	if insp_doc:
		inspection_id = getattr(insp_doc, "name", None) or (insp_doc.get("name") if isinstance(insp_doc, dict) else None)

	if block_on_insp and insp_label not in INSPECTION_PASS_STATES:
		frappe.throw(_(
			"GRN blocked — Material Inspection is '{0}'. "
			"Ask Quality Lab to approve before receiving. "
			"Contact IT Head if an override is needed."
		).format(insp_label))

	# Build PR items from Gate Entry Items using ordered_qty
	ge_items = frappe.get_all("TS Gate Entry Item",
		filters={"parent": gate_entry.name},
		fields=["item_code", "item_name", "ordered_qty", "uom", "purchase_order"])

	if not ge_items:
		frappe.throw(_("No items found in Gate Entry"))

	# Pre-flight: verify every item still exists in Item master
	missing = [it["item_code"] for it in ge_items if not frappe.db.exists("Item", it["item_code"])]
	if missing:
		frappe.throw(_(
			"Cannot create GRN: the following items are missing from the Item master: {0}. "
			"Please ask IT Head / Item Manager to restore these items or correct the Purchase Order."
		).format(", ".join(missing)))

	settings = frappe.get_single("TS Settings")
	warehouse = settings.default_warehouse
	accepted_warehouse = settings.default_accepted_warehouse
	if not warehouse:
		frappe.throw(_("Default Warehouse is not set. Please contact IT Head."))

	pr_items = []
	for ge_item in ge_items:
		item_po_name = ge_item.purchase_order or po.name
		po_item = frappe.db.get_value("Purchase Order Item",
			{"parent": item_po_name, "item_code": ge_item.item_code},
			["name", "rate", "uom", "stock_uom", "ts_delivery_location", "ts_item_remark"],
			as_dict=True)

		rate = flt(po_item.rate) if po_item else 0
		uom = (po_item.uom if po_item and po_item.uom else ge_item.uom) or "Nos"
		stock_uom = (po_item.stock_uom if po_item and po_item.stock_uom else uom)
		delivery_location = (po_item.ts_delivery_location if po_item else "") or ""
		item_remark = (po_item.ts_item_remark if po_item else "") or ""

		# v2.8.1.2: propagate PO HEADER project (see Section A comment)
		item_po_project = frappe.db.get_value("Purchase Order", item_po_name, "project") or po.project or ""

		pr_items.append({
			"item_code": ge_item.item_code,
			"item_name": ge_item.item_name,
			"qty": flt(ge_item.ordered_qty) or 0,
			"uom": uom,
			"stock_uom": stock_uom,
			"rate": rate,
			"warehouse": accepted_warehouse or warehouse,
			"purchase_order": item_po_name,
			"purchase_order_item": po_item.name if po_item else None,
			"project": item_po_project,
			"ts_delivery_location": delivery_location,
			"ts_item_remark": item_remark,
		})

	pr = frappe.get_doc({
		"doctype": "Purchase Receipt",
		"supplier": po.supplier,
		"supplier_name": po.supplier_name,
		"posting_date": getdate(),
		"company": po.company,
		"currency": po.currency,
		"buying_price_list": po.buying_price_list,
		"set_warehouse": warehouse,
		"items": pr_items,
		"ts_token": token.name,
		"ts_gate_entry": gate_entry.name,
		"lr_no": gate_entry.lr_number or "",
		"lr_date": gate_entry.lr_date,
		"transporter_name": gate_entry.transporter or "",
		"ts_non_weighing_grn": 1,
		"ts_stores_dashboard_source": "Section B",
	})

	# v2.8.1.3: copy missing PO header fields (tax template, project, terms, etc.)
	# v2.8.2: `token` passed so RST Number flows through to PR.
	_copy_po_header_fields(pr, po, token)

	# ignore_permissions: caller already validated via _check_mutate() (MUTATE_ROLES)
	# and po.check_permission("read") above; Stores User has PR create via Custom DocPerm.
	# Setting flag for pr_before_save tamper guard to allow audit marker writes.
	frappe.flags.in_stores_dashboard_mutation = True
	try:
		pr.flags.ignore_permissions = True
		pr.insert()
		# NEVER auto-submit — user must review quantities on the PR form first.
	finally:
		frappe.flags.in_stores_dashboard_mutation = False

	# Link token → PR (so Section B hides it), but do NOT set status to
	# "GRN Created" yet. That happens in pr_on_submit_update_token() when
	# user reviews qty and submits the PR.
	token.db_set({"purchase_receipt": pr.name})

	# Audit comment — escape all string fields
	esc = frappe.utils.escape_html
	try:
		override_note = "NO (gate active)" if block_on_insp else "YES (gate disabled)"
		pr.add_comment("Comment",
			_("Created via Stores Receiving Dashboard — Non-RM no-weighing path. "
			  "Token: {0}. Gate Entry: {1}. Inspection: {2}{3}. "
			  "Inspection gate override: {4}. Actor: {5}. "
			  "Draft PR created — please review quantities and submit.").format(
				esc(token.name), esc(gate_entry.name), esc(insp_label),
				(" ({0})".format(esc(inspection_id)) if inspection_id else ""),
				override_note,
				esc(frappe.session.user),
			))
	except Exception as e:
		frappe.log_error(f"Audit comment failed on PR {pr.name}: {e}",
						 "stores_receiving_api.create_grn_for_non_weighing_token")

	frappe.db.commit()

	return {"purchase_receipt": pr.name, "docstatus": pr.docstatus}


@frappe.whitelist(methods=["POST"])
def create_grn_from_direct_po(po_name):
	"""Section C action — create PR for Approved Direct PO with no token.

	Uses ERPNext standard make_purchase_receipt helper.
	Validates CC is Direct PO, no un-cancelled PR exists (draft-race guard),
	docstatus=1, approval=Approved.
	"""
	_check_mutate()

	if not po_name:
		frappe.throw(_("Purchase Order name is required"))

	# Lock the PO row (idempotency)
	frappe.db.sql("SELECT name FROM `tabPurchase Order` WHERE name = %s FOR UPDATE", (po_name,))

	po = frappe.get_doc("Purchase Order", po_name)
	po.check_permission("read")

	if po.docstatus != 1:
		frappe.throw(_("Purchase Order {0} is not submitted").format(po_name))
	if (getattr(po, "ts_approval_status", None) or "") != "Approved":
		frappe.throw(_("Purchase Order {0} is not approved (status: {1})").format(
			po_name, getattr(po, "ts_approval_status", "")))
	if flt(po.per_received) >= 100:
		frappe.throw(_("Purchase Order {0} is already fully received").format(po_name))
	if po.status in ("Closed", "Completed"):
		frappe.throw(_("Purchase Order is {0} — cannot create GRN").format(po.status))

	# DRAFT-RACE GUARD: per_received only updates on PR submit, so a Draft PR
	# against this PO would be invisible to per_received check. Look for ANY
	# non-cancelled PR linked to this PO.
	existing = frappe.db.sql("""
		SELECT pr.name, pr.docstatus FROM `tabPurchase Receipt Item` pri
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		WHERE pri.purchase_order = %s AND pr.docstatus < 2
		LIMIT 1
	""", (po_name,), as_dict=True)
	if existing:
		frappe.throw(_("A Purchase Receipt ({0}) already exists for {1}. Refresh the dashboard.").format(
			existing[0]["name"], po_name))

	# Validate CC is Direct PO
	from trustbit_ethanol.ts_gate_entry.doctype.ts_cc_approval_config.ts_cc_approval_config import (
		get_cc_config,
	)
	cc_config = get_cc_config(po.cost_center)
	if not cc_config or cc_config.flow_type != FLOW_TYPE_DIRECT_PO:
		frappe.throw(_(
			"Cost Center '{0}' is not configured as Direct PO. "
			"Use the Token / Gate Entry flow for this purchase order."
		).format(po.cost_center or "(none)"))

	# Pre-flight: verify every item on the PO still exists in Item master.
	# make_purchase_receipt would raise an unhelpful 404 otherwise.
	missing = _get_missing_items_for_po(po_name)
	if missing:
		frappe.throw(_(
			"Cannot create GRN: the following items on this Purchase Order are missing from "
			"the Item master: {0}. Please ask IT Head / Item Manager to restore these items "
			"or cancel and re-create this Purchase Order with valid items."
		).format(", ".join(missing)))

	# Build PR via ERPNext helper
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
	pr = make_purchase_receipt(po_name)

	# Stamp audit fields (requires in_stores_dashboard_mutation flag for tamper guard)
	pr.ts_direct_po_grn = 1
	pr.ts_stores_dashboard_source = "Section C"

	frappe.flags.in_stores_dashboard_mutation = True
	try:
		pr.flags.ignore_permissions = True
		pr.insert()
		# NEVER auto-submit — user must review quantities on the PR form first.
	finally:
		frappe.flags.in_stores_dashboard_mutation = False

	# Audit comment — escape user-controlled strings
	esc = frappe.utils.escape_html
	try:
		pr.add_comment("Comment",
			_("Created via Stores Receiving Dashboard — Direct PO path. "
			  "PO: {0}. CC: {1}. Actor: {2}. "
			  "Draft PR created — please review quantities and submit.").format(
				esc(po_name), esc(po.cost_center or ""), esc(frappe.session.user)))
	except Exception as e:
		frappe.log_error(f"Audit comment failed on PR {pr.name}: {e}",
						 "stores_receiving_api.create_grn_from_direct_po")

	frappe.db.commit()

	return {"purchase_receipt": pr.name, "docstatus": pr.docstatus}


# ═══════════════════════════════════════════════════════════════════════
#  PR TAMPER GUARD — Lesson 162 pattern
# ═══════════════════════════════════════════════════════════════════════

_AUDIT_MARKER_FIELDS = ("ts_non_weighing_grn", "ts_direct_po_grn", "ts_stores_dashboard_source")


def pr_before_save_audit_guard(doc, method=None):
	"""Block client tampering of Stores Receiving Dashboard audit markers.

	Fields ts_non_weighing_grn / ts_direct_po_grn / ts_stores_dashboard_source
	must only be written by this API (which sets frappe.flags.in_stores_dashboard_mutation).
	Prevents a user with PR write from forging or stripping audit provenance.
	"""
	# Internal mutation — allow
	if getattr(frappe.flags, "in_stores_dashboard_mutation", False):
		return

	# Admin/SM bypass — allow (for support fixes)
	user_roles = set(frappe.get_roles(frappe.session.user))
	if "Administrator" in user_roles or frappe.session.user == "Administrator":
		return

	# New doc: reject if any marker is set (only our API should set these)
	if doc.is_new():
		for f in _AUDIT_MARKER_FIELDS:
			if doc.get(f):
				frappe.throw(_("Field {0} is an audit marker and cannot be set directly.").format(f),
							 frappe.PermissionError)
		return

	# Existing doc: reject if any marker value changed
	for f in _AUDIT_MARKER_FIELDS:
		if doc.has_value_changed(f):
			frappe.throw(_("Field {0} is an audit marker and cannot be modified.").format(f),
						 frappe.PermissionError)


# ═══════════════════════════════════════════════════════════════════════
#  PR LIFECYCLE HOOKS — token status sync on submit/cancel/delete
# ═══════════════════════════════════════════════════════════════════════

def pr_on_submit_update_token(doc, method=None):
	"""When user submits a PR, update the linked TS Token to 'GRN Created'.

	The dashboard creates PRs as Draft. Token status stays unchanged until
	user reviews quantities and clicks Submit on the PR form. This hook
	fires on ALL PR submissions (dashboard-created and manual).
	"""
	token_name = doc.get("ts_token")
	if not token_name:
		return
	if not frappe.db.exists("TS Token", token_name):
		return

	frappe.db.set_value("TS Token", token_name, {
		"status": STATUS_GRN_CREATED,
		"grn_time": now_datetime(),
	}, update_modified=False)


def pr_on_cancel_clear_token(doc, method=None):
	"""When a PR is cancelled, revert the linked TS Token so it can be re-received."""
	token_name = doc.get("ts_token")
	if not token_name:
		return
	if not frappe.db.exists("TS Token", token_name):
		return

	# Only revert if this PR is still the one linked to the token
	current_pr = frappe.db.get_value("TS Token", token_name, "purchase_receipt")
	if current_pr != doc.name:
		return

	# Find previous status (Non-RM without weighing → "PO Linked" or fallback)
	ge = frappe.db.get_value("TS Gate Entry",
		{"token_number": token_name, "docstatus": 1},
		["requires_weighing", "material_flow"], as_dict=True)

	if ge and ge.material_flow == FLOW_NON_RM and not cint(ge.requires_weighing):
		revert_status = "PO Linked"
	else:
		revert_status = "Tare Weighed"

	frappe.db.set_value("TS Token", token_name, {
		"status": revert_status,
		"purchase_receipt": "",
		"grn_time": None,
	}, update_modified=False)


def pr_after_delete_clear_token(doc, method=None):
	"""When a draft PR is deleted, clear the token reference so it reappears in dashboard."""
	token_name = doc.get("ts_token")
	if not token_name:
		return
	if not frappe.db.exists("TS Token", token_name):
		return

	current_pr = frappe.db.get_value("TS Token", token_name, "purchase_receipt")
	if current_pr != doc.name:
		return

	frappe.db.set_value("TS Token", token_name, {
		"purchase_receipt": "",
	}, update_modified=False)
