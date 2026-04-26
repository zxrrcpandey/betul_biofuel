"""
v2.8.1 Phase B — Purchase Invoice QC Gate.
v2.9.0 Day 4   — extended with DS Gate (separate kill-switch ts_qc_v29_enabled).

Single synchronization point for the flexible GRN/QI/Deduction flow.

v2.8.1 (legacy) gate — flag `ts_qc_gate_enabled`:
  Blocks PI validate when:
    1. Any linked Token lacks an Approved QI (via PR.ts_qc_status != 'Approved')
    2. Any linked Deduction Sheet is not Approved (docstatus != 1 or status != Approved)
  Override: CEO / System Manager / IT Head / Administrator only.

v2.9.0 (new) gate — flag `ts_qc_v29_enabled`:
  Blocks PI submit when:
    For each PI item with grain item_group:
      - find PO → find PR linked to a Token
      - if any Token has a SUBMITTED TS Quality Inspection
      - then a SUBMITTED TS Deduction Sheet for that QI is REQUIRED
    Otherwise → fail with clear error + DS form link.
  Override: same OVERRIDE_ROLES (CEO / SM / IT Head) via existing ts_qc_override.

Both gates can be active independently (additive). Default OFF on prod.

Refs:
  - memory/project_flow_revamp_v2.8.md
  - Lesson 162 (control-plane field guard)
  - Lesson 175 (mutation methods POST)
  - Lesson 192 (no after_save)
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, escape_html


OVERRIDE_ROLES = {"CEO", "System Manager", "IT Head", "Administrator"}
GRAIN_ITEM_GROUP_KEYWORDS = ("grain", "maize", "rice", "corn", "wheat", "broken", "husk")


def _gate_is_enabled():
	"""v2.8.1 gate (ts_qc_gate_enabled). Fail-closed."""
	try:
		return bool(frappe.db.get_single_value("TS Settings", "ts_qc_gate_enabled"))
	except Exception:
		return False


def _v29_gate_is_enabled():
	"""v2.9.0 DS gate (ts_qc_v29_enabled). Fail-closed."""
	try:
		return bool(frappe.db.get_single_value("TS Settings", "ts_qc_v29_enabled"))
	except Exception:
		return False


def _is_grain_item(item_code):
	"""Heuristic: is this a grain item? Used to scope v2.9 DS gate."""
	if not item_code:
		return False
	item_group = frappe.db.get_value("Item", item_code, "item_group")
	if not item_group:
		return False
	group_lc = item_group.lower()
	return any(kw in group_lc for kw in GRAIN_ITEM_GROUP_KEYWORDS)


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

	# v2.8.1 dormant-gate also tried to check DS approval via a `ts_purchase_invoice`
	# field on TS Deduction Sheet — that field never existed in the schema. The
	# v2.9.0 design instead resolves DS via the QI → Token chain inside
	# validate_pi_ds_required() below, which is the correct linkage. Removed the
	# broken lookup here so a no-op silent-pass doesn't masquerade as a real check.

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


def validate_pi_ds_required(doc, method=None):
	"""
	v2.9.0 PI gate — blocks PI save when grain item has submitted QI but no
	submitted DS. Independent from v2.8.1 gate; activated by ts_qc_v29_enabled.

	Logic:
	  - For each PI item:
	      - resolve the linked PR (item.purchase_receipt)
	      - resolve the Token via PR.ts_token (set by stores_receiving_api)
	      - if no token → skip this item (non-Token PR — direct PO etc.)
	      - if Token has any SUBMITTED TS Quality Inspection
	          → require a SUBMITTED TS Deduction Sheet for that QI
	  - If any failures and no override → throw with clear message + DS link.

	Idempotent: repeated PI saves don't break (state read each time).
	Bypass: doc.ts_qc_override (existing v2.8.1 mechanism, role-gated).
	"""
	if not _v29_gate_is_enabled():
		return  # Gate dormant

	# Administrator bypass
	if frappe.session.user == "Administrator":
		return

	# Cancellation / migration bypass
	if doc.docstatus == 2:
		return
	if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_migrate", False):
		return

	failures = []
	checked_qis = set()
	tokens_seen = set()

	for item in (doc.items or []):
		if not item.item_code or not _is_grain_item(item.item_code):
			continue  # gate is grain-only

		pr_name = getattr(item, "purchase_receipt", None)
		if not pr_name:
			continue  # no PR linkage — skip

		pr_data = frappe.db.get_value(
			"Purchase Receipt", pr_name,
			["ts_token", "ts_direct_po_grn"],
			as_dict=True,
		)
		if not pr_data or not pr_data.ts_token:
			continue  # no token = direct PO or non-gate PR
		if pr_data.ts_direct_po_grn:
			continue  # direct PO doesn't need DS

		token = pr_data.ts_token
		if token in tokens_seen:
			continue
		tokens_seen.add(token)

		# Find SUBMITTED QI for this token
		qis = frappe.get_all(
			"TS Quality Inspection",
			filters={"token_number": token, "docstatus": 1},
			fields=["name", "decision"],
		)
		for qi in qis:
			if qi.name in checked_qis:
				continue
			checked_qis.add(qi.name)

			# If decision is Reject — no DS needed (auto-Return path)
			if qi.decision == "Reject":
				continue

			# Find SUBMITTED DS for this QI.
			# Lesson 196: prefer exists() over count() for boolean checks — cheaper
			# and User-Permission aware (matters in role-scoped contexts).
			ds_exists = frappe.db.exists(
				"TS Deduction Sheet",
				{"quality_inspection": qi.name, "docstatus": 1},
			)
			if not ds_exists:
				failures.append({
					"qi": qi.name,
					"token": token,
					"item": item.item_code,
					"item_name": item.item_name,
				})

	if not failures:
		return

	# Override path — same as v2.8.1
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

	# Build user-friendly error with DS form links
	bullet_lines = []
	for f in failures[:10]:  # cap at 10 to avoid massive errors
		ds_url = (
			f"/app/ts-deduction-sheet/new?quality_inspection={f['qi']}"
		)
		bullet_lines.append(
			f"• Item <b>{escape_html(f['item'])}</b> "
			f"(QI <a href='{ds_url}'>{escape_html(f['qi'])}</a>, "
			f"Token {escape_html(f['token'])}) — no submitted Deduction Sheet"
		)
	more = f"<br>… and {len(failures) - 10} more" if len(failures) > 10 else ""
	frappe.throw(_(
		"Purchase Invoice blocked by v2.9 DS Gate:<br><br>{0}{1}<br><br>"
		"Each grain item with a submitted Quality Inspection requires a "
		"submitted Deduction Sheet before PI can be saved.<br><br>"
		"Click any link above to create the missing Deduction Sheet, "
		"or set 'QC Override' (CEO / SM / IT Head only) to bypass."
	).format("<br>".join(bullet_lines), more))


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
