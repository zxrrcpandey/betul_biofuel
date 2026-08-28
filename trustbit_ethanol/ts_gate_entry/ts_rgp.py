# Copyright (c) 2026, Trustbit Software and contributors
# RGP (Returnable Gate Pass) — desk API (v2.47.0, Phase A2).
#
# Doctrine (house rules applied):
# - Every mutation: @frappe.whitelist(methods=["POST"]) + in-body POST re-assert
#   (L366/376 trampoline class) + explicit frappe.has_permission(..., throw=True)
#   (L224) + role gate + SELECT ... FOR UPDATE re-read of the gate fields (L362).
# - Control-plane writes via db_set only; status flips LAST, after side-effect
#   rows exist (L288). The single doc.save() path (record_rgp_return) arms
#   doc.flags.ts_approval_workflow_call in try/finally (L176).
# - Feature is dead while TS Settings.ts_rgp_enabled = 0 (fail-closed; same
#   flag that arms the A1 purpose routing — decision O-2: one switch).
# - Notifications are best-effort bell rows (Notification Log) wrapped
#   per-recipient (L238) + frappe.clear_messages() on failure (L276).
#   Prod outbound email is dead (P1) — no sendmail leg here.

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from trustbit_ethanol.ts_gate_entry.ts_po_approval import _rgp_routing_enabled
from trustbit_ethanol.ts_gate_entry.doctype.ts_returnable_gate_pass.ts_returnable_gate_pass import (
	OPEN_STATUSES,
	_RETURN_PHOTO_RE,
	_rgp_log,
)

RGP_DOCTYPE = "TS Returnable Gate Pass"

STORES_ROLES = {"Stores User", "Stores Manager", "IT Head", "System Manager"}
CLOSE_SHORT_APPROVER_ROLES = {"CEO", "System Manager"}
# Gate role sets + inward statuses live HERE and are imported by ts_rgp_gate
# (security L-1: single source — the reverse import would be circular)
G2_GATE_ROLES = {"G2 Gate Operator", "IT Head", "System Manager"}
G1_GATE_ROLES = {"G1 Security", "IT Head", "System Manager"}
GATE_IN_OK_STATUSES = ("Out of Plant", "At Vendor", "Partially Returned")

# Statuses from which a return lot may be recorded. "Issued"/"Out of Plant"
# included deliberately: Phase B's gate endorsement is not deployed yet, and
# even after it is, a lot can physically arrive before the gate stamps land.
RETURNABLE_STATUSES = ("Issued", "Out of Plant", "At Vendor", "Partially Returned")


def _require_post():
	req = getattr(frappe.local, "request", None)
	if req is not None and req.method != "POST":
		raise frappe.PermissionError


def _require_feature():
	"""Gate for CREATION only (predictor D3-4): if the kill switch is turned
	off while passes are open, their lifecycle endpoints (issue / return /
	verify / close-short) MUST keep working so the passes can be closed out
	and their Work-Order locks released. A flag that also froze the lifecycle
	would strand material at vendors with no exit path."""
	if not _rgp_routing_enabled():
		frappe.throw(
			_("The Returnable Gate Pass feature is not enabled on this site "
			  "(TS Settings › Enable RGP)."),
			title=_("RGP Disabled"),
		)


def _require_role(allowed):
	if not (set(frappe.get_roles(frappe.session.user)) & set(allowed)):
		frappe.throw(_("You do not have a role permitted to perform this action."),
			frappe.PermissionError)


def _locked_doc(name):
	"""Row-lock the pass and return a doc whose GATE FIELDS come from the
	locking SELECT itself (L362 + security M-3: a separate plain SELECT after
	the lock still reads the REPEATABLE-READ snapshot fixed by this request's
	earlier reads — the gated columns must ride IN the FOR UPDATE statement)."""
	rows = frappe.db.sql(
		"""SELECT status, total_balance, docstatus,
		          close_short_requested_by, close_short_approved_by,
		          g2_out_by, g1_out_by, g1_in_by, g2_in_by
		   FROM `tabTS Returnable Gate Pass` WHERE name = %s FOR UPDATE""",
		name, as_dict=True,
	)
	if not rows:
		frappe.throw(_("Returnable Gate Pass {0} not found.").format(name))
	locked = rows[0]
	doc = frappe.get_doc(RGP_DOCTYPE, name)
	doc.status = locked.status
	doc.total_balance = flt(locked.total_balance)
	doc.docstatus = cint(locked.docstatus)
	doc.close_short_requested_by = locked.close_short_requested_by
	doc.close_short_approved_by = locked.close_short_approved_by
	# Phase B gate endpoints gate on these (M-3: every gated field rides
	# in the locking read)
	doc.g2_out_by = locked.g2_out_by
	doc.g1_out_by = locked.g1_out_by
	doc.g1_in_by = locked.g1_in_by
	doc.g2_in_by = locked.g2_in_by
	return doc


def _bell(recipients, subject, doc):
	for user in set(u for u in recipients if u):
		try:
			frappe.get_doc({
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"document_type": RGP_DOCTYPE,
				"document_name": doc.name,
				"subject": subject,
			}).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="RGP bell failed", message=frappe.get_traceback())
			frappe.clear_messages()


# ═══════════════════════════════════════════════════════════════════════
#  Read endpoints
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def rgp_enabled():
	"""Feature-flag getter for JS (L168 — operators lack TS Settings read)."""
	return 1 if _rgp_routing_enabled() else 0


@frappe.whitelist()
def get_rgp_context(rgp):
	"""Button/banner context for the RGP form. Doc-read fenced."""
	frappe.has_permission(RGP_DOCTYPE, doc=rgp, throw=True)
	doc = frappe.get_doc(RGP_DOCTYPE, rgp)
	roles = set(frappe.get_roles(frappe.session.user))
	is_stores = bool(roles & STORES_ROLES)
	status = doc.status or "Draft"
	enabled = bool(_rgp_routing_enabled())
	overdue_days = 0
	due_in_days = None
	months_out = 0
	if doc.expected_return_date and status in RETURNABLE_STATUSES:
		delta = (getdate() - getdate(doc.expected_return_date)).days
		overdue_days = max(0, delta)
		due_in_days = max(0, -delta)
	if doc.challan_date and status in RETURNABLE_STATUSES:
		months_out = max(0, (getdate() - getdate(doc.challan_date)).days // 30)
	close_short_pending = bool(doc.close_short_requested_by) and not doc.close_short_approved_by
	return {
		"enabled": enabled,
		"status": status,
		"is_stores": is_stores,
		"overdue_days": overdue_days,
		"due_in_days": due_in_days,
		"months_out": months_out,
		"sec143_due_date": doc.sec143_due_date,
		"close_short_pending": close_short_pending,
		"balance": flt(doc.total_balance),
		# Lifecycle abilities are deliberately NOT flag-gated (predictor D3-4):
		# an existing pass must stay operable after a flag-off so its WO lock
		# can be released. Only CREATION (create_rgp_from_mr) reads the flag.
		"can_issue": is_stores and doc.docstatus == 1 and status == "Draft",
		"can_record_return": is_stores and doc.docstatus == 1
			and status in RETURNABLE_STATUSES,
		"can_verify": is_stores and doc.docstatus == 1 and status == "Returned",
		"can_request_close_short": is_stores and doc.docstatus == 1
			and status in RETURNABLE_STATUSES and flt(doc.total_balance) > 0
			and not doc.close_short_requested_by,
		"can_approve_close_short": bool(roles & CLOSE_SHORT_APPROVER_ROLES)
			and doc.docstatus == 1 and close_short_pending
			and status not in ("Verified - Closed", "Closed Short", "Cancelled"),
		"can_reject_close_short": bool(roles & CLOSE_SHORT_APPROVER_ROLES)
			and doc.docstatus == 1 and close_short_pending
			and status not in ("Verified - Closed", "Closed Short", "Cancelled"),
		# Phase B — gate endorsement abilities (shared sets, L-1)
		"can_g2_out": bool(roles & G2_GATE_ROLES)
			and doc.docstatus == 1 and status == "Issued" and not doc.g2_out_by,
		"can_g1_out": bool(roles & G1_GATE_ROLES)
			and doc.docstatus == 1 and status == "Out of Plant" and not doc.g1_out_by,
		"can_g1_in": bool(roles & G1_GATE_ROLES)
			and doc.docstatus == 1 and status in GATE_IN_OK_STATUSES,
		"can_g2_in": bool(roles & G2_GATE_ROLES)
			and doc.docstatus == 1 and status in GATE_IN_OK_STATUSES,
	}


@frappe.whitelist()
def get_open_rgps_for_mr(mr):
	"""Open passes against an MR — drives the Create-RGP banner and the
	client-side view of the D3 Work-Order lock (server gate is authoritative)."""
	frappe.has_permission("Material Request", doc=mr, throw=True)
	return frappe.get_all(
		RGP_DOCTYPE,
		filters={"material_request": mr, "status": ("in", OPEN_STATUSES)},
		fields=["name", "status", "total_balance", "expected_return_date"],
		order_by="creation asc",
	)


# ═══════════════════════════════════════════════════════════════════════
#  Mutations
# ═══════════════════════════════════════════════════════════════════════

@frappe.whitelist(methods=["POST"])
def create_rgp_from_mr(mr):
	"""Create a draft RGP pre-filled from an approved Service Request MR."""
	_require_post()
	_require_feature()  # the ONLY flag-gated endpoint — see _require_feature
	_require_role(STORES_ROLES)
	frappe.has_permission("Material Request", doc=mr, throw=True)

	mr_doc = frappe.get_doc("Material Request", mr)
	if mr_doc.material_request_type != "Service Request":
		frappe.throw(_("{0} is not a Service Request indent.").format(mr))
	if mr_doc.docstatus != 1 or (mr_doc.get("ts_mr_status") or "") != "Approved":
		frappe.throw(_("{0} must be approved before an RGP can be issued.").format(mr))

	rgp = frappe.new_doc(RGP_DOCTYPE)
	rgp.material_request = mr_doc.name
	rgp.company = mr_doc.company
	rgp.cost_center = mr_doc.get("cost_center")
	rgp.reason = "Repair"
	for item in (mr_doc.items or []):
		rgp.append("items", {
			"item_code": item.item_code,
			"description": item.get("description"),
			"uom": item.get("stock_uom") or item.get("uom"),
			"qty_out": flt(item.qty),
			"warehouse": item.get("warehouse"),  # D4 stock-leg source
			"mr_item_ref": item.name,
		})
	rgp.insert()  # normal perms — Stores holds create
	return rgp.name


@frappe.whitelist(methods=["POST"])
def issue_rgp(rgp):
	"""Draft (submitted) → Issued. Photo + e-way preconditions enforced here."""
	_require_post()
	_require_role(STORES_ROLES)
	frappe.has_permission(RGP_DOCTYPE, doc=rgp, ptype="write", throw=True)

	doc = _locked_doc(rgp)
	if doc.docstatus != 1:
		frappe.throw(_("Save and Submit the pass before issuing it."))
	if (doc.status or "Draft") != "Draft":
		frappe.throw(_("Only a submitted Draft pass can be issued (status: {0}).")
			.format(doc.status))
	if not (doc.issue_photo_1 or "").strip():
		frappe.throw(_("Issue Photo 1 is mandatory before the pass is issued (D6)."))
	if cint(doc.eway_bill_required) and not (doc.eway_bill_no or "").strip():
		frappe.throw(
			_("An e-way bill number is required (value above ₹50,000 or "
			  "inter-state movement) before this pass can be issued."))

	# Live-data finding (28 Aug 2026): a pass created earlier carries its
	# CREATION day as challan_date (set_only_once). Rule 55 — the challan
	# accompanies the MOVEMENT, which begins at Issue — so restamp the
	# statutory anchor to the issue day and re-derive the Sec 143 clocks.
	# db_set is the sanctioned path around set_only_once for exactly this
	# controller-owned correction; RGP_GATE_FIELDS deliberately excludes it.
	if str(doc.challan_date) != frappe.utils.today():
		from frappe.utils import add_days, add_months, add_years
		from trustbit_ethanol.ts_gate_entry.doctype.ts_returnable_gate_pass.ts_returnable_gate_pass import (
			SEC143_EXEMPT_GOODS,
		)
		today = frappe.utils.today()
		updates = {"challan_date": today}
		if doc.goods_type != SEC143_EXEMPT_GOODS:
			years = 3 if doc.goods_type == "Capital Goods" else 1
			updates["sec143_due_date"] = add_years(today, years)
			updates["sec143_alarm_date"] = add_months(today, 10)
		if not doc.expected_return_date or str(doc.expected_return_date) < today:
			updates["expected_return_date"] = add_days(today, 7)
		doc.db_set(updates, update_modified=True)

	doc.db_set({
		"issued_by": frappe.session.user,
		"issued_at": frappe.utils.now_datetime(),
	}, update_modified=True)
	doc.db_set("status", "Issued", update_modified=True)
	_rgp_log(doc, "Issued", "Draft", "Issued",
		comment=_("Pass issued; visible to the gate. Challan may be printed."))
	return {"status": "Issued"}


@frappe.whitelist(methods=["POST"])
def record_rgp_return(rgp, lines):
	"""Record one return lot. `lines` = JSON list of
	{row_name, qty, condition_in, serial_no_in, remark}. Multi-lot partial
	returns per D6 — balances recompute inside doc.save()."""
	_require_post()
	_require_role(STORES_ROLES)
	frappe.has_permission(RGP_DOCTYPE, doc=rgp, ptype="write", throw=True)

	if isinstance(lines, str):
		lines = json.loads(lines or "[]")
	lines = [l for l in (lines or []) if flt(l.get("qty")) > 0]
	if not lines:
		frappe.throw(_("Nothing to return — every line has zero quantity."))

	doc = _locked_doc(rgp)
	if doc.docstatus != 1 or (doc.status or "") not in RETURNABLE_STATUSES:
		frappe.throw(_("Returns can only be recorded on an issued pass "
			"(status: {0}).").format(doc.status))

	items_by_name = {row.name: row for row in (doc.items or [])}
	from_state = doc.status
	today = frappe.utils.today()

	for line in lines:
		row = items_by_name.get(line.get("row_name"))
		if not row:
			frappe.throw(_("Unknown item row: {0}").format(line.get("row_name")))
		qty = flt(line.get("qty"))
		if qty > flt(row.balance_qty):
			frappe.throw(
				_("Row {0} ({1}): returning {2} but only {3} is outstanding.")
				.format(row.idx, row.item_code, qty, row.balance_qty))
		condition = frappe.utils.cstr(line.get("condition_in")).strip()
		if not condition:
			frappe.throw(_("Row {0}: Condition In is mandatory before the "
				"return is credited (D6).").format(row.idx))
		photo = frappe.utils.cstr(line.get("return_photo")).strip()
		if not photo:
			frappe.throw(_("Row {0}: a return photo is mandatory before the "
				"return is credited (D6).").format(row.idx))
		# M-1/R-3: charset allow-list (File.set_file_name strips only "/",
		# so quotes can survive into a genuine file_url) + a real File row.
		# N-1: SHARED regex imported from the controller — never re-declare
		# a literal copy here (L425 sibling-drift class).
		if not _RETURN_PHOTO_RE.match(photo) or \
				not frappe.db.exists("File", {"file_url": photo}):
			frappe.throw(_("Row {0}: invalid photo reference — upload the "
				"photo through the dialog.").format(row.idx))
		if cint(row.is_serialized):
			serial_in = frappe.utils.cstr(line.get("serial_no_in")).strip()
			serial_out = (row.serial_no_out or "").strip()
			if serial_in != serial_out:
				frappe.throw(
					_("Row {0}: serial mismatch — went out as '{1}', returning "
					  "as '{2}'. The same unit must return (D6).")
					.format(row.idx, serial_out, serial_in or _("(blank)")))
		doc.append("returns", {
			"return_date": today,
			"rgp_item_row": row.name,
			"item_code": row.item_code,
			"qty": qty,
			"serial_no_in": frappe.utils.cstr(line.get("serial_no_in")).strip(),
			"condition_in": condition,
			"return_photo": photo,
			"remark": frappe.utils.cstr(line.get("remark"))[:140],
			"received_by": frappe.session.user,
		})

	# L176 — the ONE legitimate save path; guard flag armed in try/finally.
	doc.flags.ts_approval_workflow_call = True
	try:
		doc.save(ignore_permissions=True)  # write perm already asserted above (L224)
	finally:
		doc.flags.ts_approval_workflow_call = False

	# D4 stock leg — reverse ledger move for the credited lot (same
	# transaction; a failure rolls the lot credit back too). No-op when the
	# out-warehouse is unset or the lot has no stock items.
	# Security HIGH-1 (28 Aug): ONLY when the G1 out-transfer actually ran —
	# an early return on a pass that never left (Issued / Out of Plant) has
	# nothing in the repair warehouse to reverse; moving anyway would conjure
	# phantom stock. g1_out_by is the same "stock is at the repair warehouse"
	# predicate approve_close_short uses.
	se_name = None
	if doc.g1_out_by:
		from trustbit_ethanol.ts_gate_entry.ts_rgp_stock import make_return_transfer
		# Security M-2: accumulate duplicate row_name lines — last-wins would
		# credit the register for the sum but move only the last lot's qty.
		qty_by_row = {}
		for l in lines:
			rn = l.get("row_name")
			qty_by_row[rn] = qty_by_row.get(rn, 0) + flt(l.get("qty"))
		se_name = make_return_transfer(doc, qty_by_row)

	# Side effects committed; NOW flip status (L288).
	doc.reload()
	new_state = "Returned" if flt(doc.total_balance) <= 0 else "Partially Returned"
	doc.db_set("status", new_state, update_modified=True)
	lot_summary = ", ".join(
		f"{flt(l.get('qty'))} × {items_by_name[l.get('row_name')].item_code}"
		for l in lines)
	if se_name:
		lot_summary += _(" — stock returned via {0}").format(se_name)
	elif not doc.g1_out_by:
		lot_summary += _(" — register-only lot (no G1 exit stamp)")
	_rgp_log(doc, "Returned", from_state, new_state,
		comment=_("Lot received: {0}").format(lot_summary))
	return {"status": new_state, "balance": flt(doc.total_balance),
		"stock_entry": se_name}


@frappe.whitelist(methods=["POST"])
def verify_rgp(rgp):
	"""Returned → Verified - Closed. Unlocks the D3 Work-Order conversion."""
	_require_post()
	_require_role(STORES_ROLES)
	frappe.has_permission(RGP_DOCTYPE, doc=rgp, ptype="write", throw=True)

	doc = _locked_doc(rgp)
	if doc.docstatus != 1 or (doc.status or "") != "Returned":
		frappe.throw(_("Only a fully Returned pass can be verified "
			"(status: {0}).").format(doc.status))
	if flt(doc.total_balance) > 0:
		frappe.throw(_("Balance is not zero — record the remaining return or "
			"use Close Short."))
	# D6 photo evidence lives per return LINE (enforced in record_rgp_return);
	# the header return photos remain optional overview shots.

	doc.db_set({
		"verified_by": frappe.session.user,
		"verified_at": frappe.utils.now_datetime(),
	}, update_modified=True)
	doc.db_set("status", "Verified - Closed", update_modified=True)
	_rgp_log(doc, "Verified", "Returned", "Verified - Closed")
	_bell(
		[doc.requested_by, doc.owner],
		_("RGP {0} verified & closed — Work Order conversion unlocked on {1}")
			.format(doc.name, doc.material_request),
		doc,
	)
	return {"status": "Verified - Closed"}


@frappe.whitelist(methods=["POST"])
def request_close_short(rgp, reason):
	"""Stores flags a non-returnable balance for CEO write-off (D8)."""
	_require_post()
	_require_role(STORES_ROLES)
	frappe.has_permission(RGP_DOCTYPE, doc=rgp, ptype="write", throw=True)

	reason = frappe.utils.cstr(reason).strip()
	if len(reason) < 10:
		frappe.throw(_("A close-short reason of at least 10 characters is required."))

	doc = _locked_doc(rgp)
	if doc.docstatus != 1 or (doc.status or "") not in RETURNABLE_STATUSES:
		frappe.throw(_("Close Short applies to an issued pass with material "
			"outstanding (status: {0}).").format(doc.status))
	if flt(doc.total_balance) <= 0:
		frappe.throw(_("Balance is zero — use Verify instead."))
	if doc.close_short_requested_by:
		frappe.throw(_("A close-short request is already pending CEO approval."))

	doc.db_set({
		"close_short_requested_by": frappe.session.user,
		"close_short_requested_at": frappe.utils.now_datetime(),
		"close_short_reason": reason[:500],
	}, update_modified=True)
	_rgp_log(doc, "Closed Short", doc.status, doc.status,
		comment=_("Close-short REQUESTED: {0}").format(reason[:200]))
	_bell(
		[u.parent for u in frappe.get_all("Has Role",
			filters={"role": "CEO", "parenttype": "User"}, fields=["parent"])],
		_("RGP {0}: close-short approval requested (balance {1})")
			.format(doc.name, doc.total_balance),
		doc,
	)
	return {"requested": 1}


@frappe.whitelist(methods=["POST"])
def reject_close_short(rgp, reason=""):
	"""CEO declines the write-off — the request clears and the store keeps
	chasing the balance. Pass status is unchanged."""
	_require_post()
	_require_role(CLOSE_SHORT_APPROVER_ROLES)
	# ptype="read" is DELIBERATE (security L-1): the CEO holds a read-only
	# DocPerm by design; authorization for this verb is the role gate above.
	# "Fixing" this to ptype="write" would silently break the CEO flow.
	frappe.has_permission(RGP_DOCTYPE, doc=rgp, ptype="read", throw=True)

	doc = _locked_doc(rgp)
	if not doc.close_short_requested_by or doc.close_short_approved_by:
		frappe.throw(_("No close-short request is pending on this pass."))

	requester = doc.close_short_requested_by
	doc.db_set({
		"close_short_requested_by": None,
		"close_short_requested_at": None,
		"close_short_reason": None,
	}, update_modified=True)
	_rgp_log(doc, "Rejected", doc.status, doc.status,
		comment=_("Close-short REJECTED by CEO: {0}").format(
			frappe.utils.cstr(reason)[:200]))
	_bell([requester],
		_("RGP {0}: close-short request rejected — keep chasing the balance")
			.format(doc.name),
		doc)
	return {"rejected": 1}


@frappe.whitelist(methods=["POST"])
def approve_close_short(rgp):
	"""CEO writes off the outstanding balance → Closed Short (D8)."""
	_require_post()
	_require_role(CLOSE_SHORT_APPROVER_ROLES)
	# ptype="read" is DELIBERATE (security L-1) — see reject_close_short.
	frappe.has_permission(RGP_DOCTYPE, doc=rgp, ptype="read", throw=True)

	doc = _locked_doc(rgp)
	if doc.docstatus != 1:
		frappe.throw(_("Pass is not submitted."))
	if (doc.status or "") in ("Verified - Closed", "Closed Short", "Cancelled"):
		frappe.throw(_("Pass is already closed (status: {0}).").format(doc.status))
	if not doc.close_short_requested_by:
		frappe.throw(_("No close-short request is pending on this pass."))
	if doc.close_short_requested_by == frappe.session.user and \
			"System Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw(_("The requester cannot approve their own close-short."))

	from_state = doc.status
	doc.db_set({
		"close_short_approved_by": frappe.session.user,
		"close_short_approved_at": frappe.utils.now_datetime(),
		"close_short_qty": flt(doc.total_balance),
	}, update_modified=True)
	doc.db_set("status", "Closed Short", update_modified=True)
	from trustbit_ethanol.ts_gate_entry.ts_rgp_stock import rgp_out_warehouse
	stranded = ""
	if rgp_out_warehouse() and doc.g1_out_by:
		stranded = _(" Any stock-item balance remains in {0} — write it off "
			"via a deliberate Stock Entry.").format(rgp_out_warehouse())
	_rgp_log(doc, "Closed Short", from_state, "Closed Short",
		comment=_("Balance {0} written off by CEO approval.{1}")
			.format(doc.total_balance, stranded))
	_bell(
		[doc.requested_by, doc.owner, doc.close_short_requested_by],
		_("RGP {0} closed SHORT — balance {1} written off")
			.format(doc.name, doc.close_short_qty),
		doc,
	)
	return {"status": "Closed Short"}
