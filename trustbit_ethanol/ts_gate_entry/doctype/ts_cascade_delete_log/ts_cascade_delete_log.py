"""TS Cascade Delete Log controller (v2.11.0).

Append-only audit doctype for the Token Cascade Delete System. Every cascade
deletion produces ONE log row; the row is hash-chained to the prior row
(Hardening A) and locked against modification post-submit.

Lesson references:
- 162 (permlevel=1 tamper protection for approval/exec fields)
- 175 (POST CSRF — but here only the API mutates this doc; no whitelist on this controller)
- 176 (try/finally flag-bypass for API-driven mutations)
- 222 (defensive on_save without blocking API refresh path)
- 224 (dual-gate: doctype perms + API has_permission check)
- 237 (frappe.log_error kwargs)
- 238 (best-effort side-effects)
- 262 + 275 (surgical-SQL fallback inside the engine — NOT this controller)
- A (hash-chain audit)
"""

import hashlib
import json
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


# Fields that contribute to the hash chain (deterministic order).
# Append-only — never reorder, never remove. Adding a new field requires a
# migration that re-hashes the entire chain. v2.13.0 `executed_as` is
# DELIBERATELY EXCLUDED here (same precedent as `cut_point` + `force_payment_links`)
# so the chain stays verifiable on every existing prod log row. The executor's
# identity is captured in `forensic_block_json` + `executed_as` (permlevel=1)
# for audit purposes.
_HASH_FIELDS = (
	"target_token",
	"initiated_by",
	"initiated_at",
	"approved_by",
	"approved_at",
	"force_pr_override",
	"force_mi_override",
	"backup_filename",
	"backup_sha256",
	"executed_at",
	"prev_row_hash",
)

# Fields the API is allowed to mutate on a SUBMITTED log row.
# Anything else triggers append-only guard in on_update_after_submit().
_API_MUTABLE_FIELDS = {
	"approval_status",
	"approved_by",
	"approved_at",
	"rejection_reason",
	"executed_at",
	"executed_as",  # v2.13.0 — resolved executor user (Stock User init flow)
	"execution_result_json",
	"webhook_delivered",
	"webhook_response_code",
	"webhook_attempted_at",
	"webhook_response_body_excerpt",
	"revert_window_expires_at",
	"reverted_at",
	"reverted_by",
	"revert_result_json",
	"integrity_scan_run_at",
	"integrity_scan_orphans_json",
	"integrity_scan_clean",
}


class TSCascadeDeleteLog(Document):
	# ------------------------------------------------------------------ validate
	def validate(self):
		self._validate_state_machine()
		self._validate_two_person_rule()
		self._validate_force_overrides()
		self._compute_hash_chain()

	def _validate_state_machine(self):
		"""Allowed transitions: Pending CEO Approval → {Approved, Rejected, Cancelled}
		→ Executed → {Reverted, Failed}.
		"""
		if self.is_new():
			if self.approval_status not in (None, "", "Pending CEO Approval"):
				frappe.throw(_("New cascade delete logs must start at 'Pending CEO Approval'."))
			self.approval_status = "Pending CEO Approval"
			if not self.initiated_at:
				self.initiated_at = now_datetime()
			return

		old = self.get_doc_before_save()
		if not old:
			return
		old_st, new_st = old.approval_status, self.approval_status
		if old_st == new_st:
			return

		allowed = {
			"Pending CEO Approval": {"Approved", "Rejected", "Cancelled"},
			"Approved": {"Executed", "Failed", "Cancelled"},
			"Executed": {"Reverted"},
			"Failed": set(),
			"Rejected": set(),
			"Cancelled": set(),
			"Reverted": set(),
		}
		if new_st not in allowed.get(old_st, set()):
			frappe.throw(_("Invalid status transition: {0} → {1}").format(old_st, new_st))

	def _validate_two_person_rule(self):
		"""Hardening C — approver MUST differ from initiator."""
		if self.approved_by and self.initiated_by and self.approved_by == self.initiated_by:
			frappe.throw(_("Two-person rule violated: approver ({0}) must differ from initiator ({1}).")
			             .format(self.approved_by, self.initiated_by))

	def _validate_force_overrides(self):
		"""Type-to-confirm gates — must match exact strings server-side (Q&A item 4)."""
		if (self.confirm_token_name_typed or "").strip() != (self.target_token or "").strip():
			# Only enforce on first save (when the user typed it).
			# On subsequent API saves (state transitions), the field is read-only
			# so this comparison passes vacuously.
			if self.is_new():
				frappe.throw(_("Confirm Token Name must exactly match the Target Token."))
		if self.force_pr_override:
			if (self.force_pr_confirmation_typed or "") != "FORCE-DELETE-PR":
				if self.is_new():
					frappe.throw(_("Force-PR confirmation must equal: FORCE-DELETE-PR"))
		if self.force_mi_override:
			if (self.force_mi_confirmation_typed or "") != "FORCE-DELETE-MI":
				if self.is_new():
					frappe.throw(_("Force-MI confirmation must equal: FORCE-DELETE-MI"))
		# B4 (v2.11.1) — Force-Payment-Links override (delete a chain with downstream
		# Payment Entries / Journal Entries / Landed Cost Vouchers) — defence-in-depth
		# type-to-confirm check on the doc itself (the API validates it too).
		if self.get("force_payment_links"):
			if (self.get("force_payment_confirmation_typed") or "") != "FORCE-DELETE-WITH-PAYMENTS":
				if self.is_new():
					frappe.throw(_("Force-Payment-Links confirmation must equal: FORCE-DELETE-WITH-PAYMENTS"))

	def _compute_hash_chain(self):
		"""Hardening A — compute this_row_hash deterministically over _HASH_FIELDS.

		prev_row_hash is set by the API at insert time (it pulls the latest
		submitted row's this_row_hash). This method recomputes this_row_hash
		every save so that any field-tamper attempt invalidates the chain.
		"""
		if not self.prev_row_hash:
			# Genesis row OR API didn't set it yet — fetch most recent submitted row.
			prev = frappe.db.sql(
				"""SELECT this_row_hash FROM `tabTS Cascade Delete Log`
				   WHERE docstatus = 1 AND name != %s
				   ORDER BY creation DESC LIMIT 1""",
				(self.name or "",), as_dict=True,
			)
			self.prev_row_hash = (prev[0].this_row_hash if prev else "") or ""

		# Canonical-serialized field tuple → SHA-256.
		canonical = "|".join(
			str(self.get(f) or "") for f in _HASH_FIELDS
		)
		self.this_row_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

	# ------------------------------------------------------------------ append-only guards
	def on_update_after_submit(self):
		"""Block any modification after submit UNLESS api caller flag is set.

		Lesson 176 — API endpoints set frappe.flags.cascade_delete_api_caller = True
		inside try/finally before saving the log. Direct UI/console edits raise.

		For surgical-precision tamper detection, we whitelist exactly the API-
		mutable fields. Anything else changed → throw.
		"""
		if getattr(frappe.flags, "cascade_delete_api_caller", False):
			return

		old = self.get_doc_before_save()
		if not old:
			return

		tampered = []
		for fname in self.meta.get_valid_columns():
			if fname in ("modified", "modified_by", "docstatus", "_user_tags",
			             "_comments", "_assign", "_liked_by"):
				continue
			if fname in _API_MUTABLE_FIELDS:
				continue
			if (old.get(fname) or "") != (self.get(fname) or ""):
				tampered.append(fname)

		if tampered:
			frappe.throw(_(
				"TS Cascade Delete Log is append-only after submit. "
				"Tampering detected on fields: {0}. "
				"To change approval state, use the Cascade Delete API endpoints."
			).format(", ".join(tampered)))

	def on_cancel(self):
		"""Audit log is append-only — block cancel entirely."""
		frappe.throw(_("TS Cascade Delete Log cannot be cancelled. Audit log is append-only."))

	def on_trash(self):
		"""Audit log is append-only — block delete entirely (defense-in-depth)."""
		if not getattr(frappe.flags, "in_install", False):
			frappe.throw(_("TS Cascade Delete Log cannot be deleted. Audit log is append-only."))


# ---------------------------------------------------------------- standalone helpers


def verify_chain_integrity(starting_log_name: str | None = None) -> dict:
	"""Walk the hash chain forward and verify every link.

	Returns a dict with `total_rows`, `broken_links` (list of names), and `clean` flag.

	Called by the cascade_delete_api.verify_chain_integrity endpoint AND by the
	nightly scheduler. Read-only; no side effects.
	"""
	rows = frappe.db.sql(
		"""SELECT name, prev_row_hash, this_row_hash
		   FROM `tabTS Cascade Delete Log`
		   WHERE docstatus = 1
		   ORDER BY creation ASC""",
		as_dict=True,
	)
	broken = []
	expected_prev = ""
	for r in rows:
		if (r.prev_row_hash or "") != expected_prev:
			broken.append(r.name)
		expected_prev = r.this_row_hash or ""
	return {
		"total_rows": len(rows),
		"broken_links": broken,
		"clean": len(broken) == 0,
	}
