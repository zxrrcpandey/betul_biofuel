# Copyright (c) 2026, Trustbit Software and contributors
# RGP Phase B — G1/G2 gate endorsement of returnable-pass material (v2.48.0).
#
# Doctrine (same as ts_rgp.py):
# - POST-only + in-body re-assert (L366/376) + explicit has_permission with
#   ptype="read" (guards hold read-only DocPerms BY DESIGN — authorization is
#   the role gate; L-1 pattern) + role gate + FOR-UPDATE locked read with the
#   gated columns IN the locking SELECT (L362/M-3).
# - Control-plane trios written ONLY via db_set; status flips LAST (L288).
# - NOT flag-gated (D3-4 doctrine): endorsement continues an already-issued
#   pass; only creation reads ts_rgp_enabled.
# - The OUT legs mirror the truck exit: G2 (plant gate) first, then G1
#   (campus gate). The IN legs run the reverse: G1 first, then G2. Gate-IN
#   stamps record arrival only — crediting the return stays with Stores
#   (record_rgp_return + verify), per the two-step industry discipline.
# - Multi-lot returns: the trios record the FIRST physical inward; later lots
#   are appended to the pass trail instead of overwriting the stamps.

import frappe
from frappe import _
from frappe.utils import cstr

from trustbit_ethanol.ts_gate_entry.ts_rgp import (
	RGP_DOCTYPE,
	G1_GATE_ROLES,
	G2_GATE_ROLES,
	GATE_IN_OK_STATUSES,
	_locked_doc,
	_require_post,
	_require_role,
	_bell,
)
from trustbit_ethanol.ts_gate_entry.doctype.ts_returnable_gate_pass.ts_returnable_gate_pass import (
	_rgp_log,
)

# Shared single-source sets (security L-1) — aliases keep this module readable
G2_ROLES = G2_GATE_ROLES
G1_ROLES = G1_GATE_ROLES
_IN_OK_STATUSES = GATE_IN_OK_STATUSES


def _stamp(doc, prefix, remark):
	# cstr (tester DEFECT-1): a JSON-array remark over REST is a non-string —
	# slicing it raw reaches the SQL layer as a fail-loud 500. Coerce first.
	doc.db_set({
		f"{prefix}_by": frappe.session.user,
		f"{prefix}_at": frappe.utils.now_datetime(),
		f"{prefix}_remark": cstr(remark)[:140],
	}, update_modified=True)


@frappe.whitelist(methods=["POST"])
def rgp_gate_out(rgp, checkpoint, remark=""):
	"""Endorse material OUT. checkpoint 'G2' (plant gate, first) then 'G1'
	(campus gate). G2: Issued → Out of Plant. G1: Out of Plant → At Vendor."""
	_require_post()
	if checkpoint not in ("G1", "G2"):
		frappe.throw(_("Invalid checkpoint."))
	_require_role(G2_ROLES if checkpoint == "G2" else G1_ROLES)
	frappe.has_permission(RGP_DOCTYPE, doc=rgp, ptype="read", throw=True)

	doc = _locked_doc(rgp)
	if doc.docstatus != 1:
		frappe.throw(_("Pass is not submitted."))

	if checkpoint == "G2":
		if (doc.status or "") != "Issued":
			frappe.throw(_("G2 can endorse OUT only an Issued pass "
				"(status: {0}).").format(doc.status))
		if doc.g2_out_by:
			frappe.throw(_("G2 already endorsed this pass out at {0}.")
				.format(doc.g2_out_at))
		_stamp(doc, "g2_out", remark)
		doc.db_set("status", "Out of Plant", update_modified=True)
		_rgp_log(doc, "Gate Out Endorsed", "Issued", "Out of Plant",
			comment=_("G2 plant-gate exit endorsed. {0}").format(cstr(remark)[:100]))
		return {"status": "Out of Plant"}

	# G1 — campus gate, second checkpoint
	if (doc.status or "") != "Out of Plant":
		frappe.throw(_("G1 endorses OUT after G2 — pass must be Out of Plant "
			"(status: {0}).").format(doc.status))
	if doc.g1_out_by:
		frappe.throw(_("G1 already endorsed this pass out at {0}.")
			.format(doc.g1_out_at))
	# D4 stock leg — ledger move BEFORE the stamps (L288; same transaction,
	# so a failure rolls the whole endorsement back). No-op when the
	# out-warehouse is unset or the pass has no stock lines.
	from trustbit_ethanol.ts_gate_entry.ts_rgp_stock import make_out_transfer
	se_name = make_out_transfer(doc)
	_stamp(doc, "g1_out", remark)
	doc.db_set("status", "At Vendor", update_modified=True)
	extra = _(" Stock moved to repair warehouse via {0}.").format(se_name) if se_name else ""
	_rgp_log(doc, "Gate Out Endorsed", "Out of Plant", "At Vendor",
		comment=_("G1 campus-gate exit endorsed — material with vendor.{0} {1}")
			.format(extra, cstr(remark)[:100]))
	return {"status": "At Vendor", "stock_entry": se_name}


@frappe.whitelist(methods=["POST"])
def rgp_gate_in(rgp, checkpoint, remark=""):
	"""Record material arriving back. checkpoint 'G1' (campus gate, first)
	then 'G2' (plant gate). Stamps only — NO status change: the return is
	credited by Stores via record_rgp_return + verify (two-step discipline).
	Repeat inward for later lots appends to the trail without overwriting."""
	_require_post()
	if checkpoint not in ("G1", "G2"):
		frappe.throw(_("Invalid checkpoint."))
	_require_role(G1_ROLES if checkpoint == "G1" else G2_ROLES)
	frappe.has_permission(RGP_DOCTYPE, doc=rgp, ptype="read", throw=True)

	doc = _locked_doc(rgp)
	if doc.docstatus != 1:
		frappe.throw(_("Pass is not submitted."))
	if (doc.status or "") not in _IN_OK_STATUSES:
		frappe.throw(_("Nothing is outside on this pass (status: {0}).")
			.format(doc.status))

	prefix = "g1_in" if checkpoint == "G1" else "g2_in"
	already = doc.get(f"{prefix}_by")
	if not already:
		_stamp(doc, prefix, remark)
		comment = _("{0} inward endorsed. {1}").format(checkpoint, cstr(remark)[:100])
	else:
		# later lot — keep the first stamp, add a trail row (remark preserved
		# in the trail even though the stamp fields keep the first arrival)
		comment = _("{0} inward endorsed (additional lot; first inward {1}). {2}") \
			.format(checkpoint, doc.get(f"{prefix}_at"), cstr(remark)[:100])
	_rgp_log(doc, "Gate In Endorsed", doc.status, doc.status, comment=comment)

	if checkpoint == "G2" and not already:
		# material is back inside the plant — tell the store to verify.
		# FIRST arrival only (tester OBS-5: every lot re-belled 20 users);
		# both stores roles, enabled users only (security round-B note)
		recipients = frappe.db.sql_list(
			"""SELECT DISTINCT hr.parent FROM `tabHas Role` hr
			   JOIN `tabUser` u ON u.name = hr.parent AND u.enabled = 1
			   WHERE hr.parenttype = 'User'
			     AND hr.role IN ('Stores User', 'Stores Manager')
			   ORDER BY hr.parent LIMIT 30""")
		_bell(
			recipients,
			_("RGP {0}: material arrived at the plant gate — record the "
			  "return and verify").format(doc.name),
			doc,
		)
	return {"stamped": 0 if already else 1, "status": doc.status}
