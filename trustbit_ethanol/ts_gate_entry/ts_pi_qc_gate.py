"""
v2.8.1 Phase B — Purchase Invoice QC Gate.

Single synchronization point for the flexible GRN/QI/Deduction flow. Blocks
PI save when:
  1. Any linked Token lacks an Approved QI (via PR.ts_qc_status != 'Approved')
  2. Any linked Deduction Sheet is not Approved (docstatus != 1 or status != Approved)

Override: CEO / System Manager / IT Head / Administrator only, with mandatory reason.
Tamper guard: permlevel 1 fields + Lesson 162 before_save guard.

Feature flag: TS Settings.ts_qc_gate_enabled. Default OFF (opt-in).

Refs: memory/project_flow_revamp_v2.8.md
"""

import frappe
from frappe import _
from frappe.utils import now_datetime


OVERRIDE_ROLES = {"CEO", "System Manager", "IT Head", "Administrator"}


def _gate_is_enabled():
	"""Read flag fail-closed: OFF if flag absent, ON only when explicitly set to 1."""
	try:
		return bool(frappe.db.get_single_value("TS Settings", "ts_qc_gate_enabled"))
	except Exception:
		return False


def validate_pi_qc_approved(doc, method=None):
	"""PI validate hook — block if QC not approved on any linked PR, unless override."""
	if not _gate_is_enabled():
		return  # Gate dormant

	# Administrator bypass — unconditional (migrations, imports, scripted flows)
	if frappe.session.user == "Administrator":
		return

	failures = []
	checked_prs = set()

	for item in (doc.items or []):
		pr_name = getattr(item, "purchase_receipt", None)
		if not pr_name or pr_name in checked_prs:
			continue
		checked_prs.add(pr_name)

		# Direct PO exemption — flagged PRs skip the QC gate
		pr_flags = frappe.db.get_value(
			"Purchase Receipt", pr_name,
			["ts_qc_status", "ts_direct_po_grn", "ts_token"],
			as_dict=True,
		)
		if not pr_flags:
			continue
		if pr_flags.ts_direct_po_grn:
			continue  # Direct PO doesn't need QC gate

		qc_status = pr_flags.ts_qc_status or "Pending"
		if qc_status != "Approved":
			failures.append(
				f"PR {pr_name}: QC status is '{qc_status}'"
				f"{' (Token: ' + pr_flags.ts_token + ')' if pr_flags.ts_token else ''}"
			)

	# Also check any Deduction Sheets linked to this PI are Approved
	linked_ds = frappe.get_all(
		"TS Deduction Sheet",
		filters={"ts_purchase_invoice": doc.name or "__new__"},
		fields=["name", "status", "docstatus"],
	)
	for ds in linked_ds:
		if ds.docstatus != 1 or ds.status != "Approved":
			failures.append(f"Deduction Sheet {ds.name}: status '{ds.status}', docstatus {ds.docstatus}")

	if not failures:
		return

	# Gate would fail — check override
	if doc.ts_qc_override:
		user_roles = set(frappe.get_roles(frappe.session.user))
		if not (user_roles & OVERRIDE_ROLES):
			raise frappe.PermissionError(
				_("Only CEO / System Manager / IT Head can set QC Override")
			)
		if not (doc.ts_qc_override_reason or "").strip():
			frappe.throw(_("QC Override Reason is mandatory when override is set"))
		# Override accepted — let save proceed
		return

	# No override → block
	frappe.throw(_(
		"Purchase Invoice blocked by QC Gate:\n\n{0}\n\n"
		"Options:\n"
		"• Approve the linked Quality Inspection(s) first\n"
		"• Or set 'QC Override' with a reason (CEO / System Manager / IT Head only)"
	).format("\n".join(f"• {f}" for f in failures)))


def _block_pi_qc_override_tampering(doc, method=None):
	"""PI before_save hook — Lesson 162 tamper guard on override fields."""
	if not _gate_is_enabled():
		return

	# Let Administrator + internal flows through (e.g., scripted adjustments)
	if frappe.session.user == "Administrator":
		return
	if getattr(frappe.flags, "in_pi_qc_internal", False):
		return

	watched = ["ts_qc_override", "ts_qc_override_reason", "ts_qc_override_by", "ts_qc_override_at"]
	changed = any(doc.has_value_changed(f) for f in watched) if not doc.is_new() else bool(doc.ts_qc_override)

	if not changed:
		return

	user_roles = set(frappe.get_roles(frappe.session.user))
	if not (user_roles & OVERRIDE_ROLES):
		raise frappe.PermissionError(
			_("QC Override fields are locked. Only CEO / System Manager / IT Head may edit.")
		)

	# Auto-populate audit fields on override flip
	frappe.flags.in_pi_qc_internal = True
	try:
		if doc.ts_qc_override and doc.has_value_changed("ts_qc_override"):
			doc.ts_qc_override_by = frappe.session.user
			doc.ts_qc_override_at = now_datetime()
			# Add audit comment
			reason = (doc.ts_qc_override_reason or "").strip()
			doc.add_comment(
				"Info",
				f"[PI_QC_OVERRIDE] by {frappe.session.user}: "
				f"{frappe.utils.escape_html(reason)[:500]}"
			)
	finally:
		frappe.flags.in_pi_qc_internal = False
