import frappe
from frappe import _
from frappe.model.document import Document


SNAPSHOT_FIELDS = (
    "reference_doctype",
    "reference_name",
    "source_amount",
    "cost_center",
    "company",
    "fiscal_year",
    "breach_type",
    "period_month",
    "annual_budget_amount",
    "annual_committed_amount",
    "annual_spent_amount",
    "annual_available",
    "annual_breach_delta",
    "annual_utilization_pct",
    "monthly_budget_amount",
    "monthly_committed_amount",
    "monthly_spent_amount",
    "monthly_available",
    "monthly_breach_delta",
    "monthly_utilization_pct",
    "kill_switch_at_create",
    "submitted_by",
    "submitted_at",
)


class TSBudgetOverrideApproval(Document):
    def before_save(self):
        self._compute_title()
        self._block_snapshot_tampering()
        self._validate_decision_consistency()

    def validate(self):
        self._validate_reference_exists()
        self._validate_status_transition()

    def on_submit(self):
        # Submit is the act of creation completing — status stays Pending CEO.
        # The actual approve/reject flips happen via whitelisted endpoints, which
        # set frappe.flags.in_budget_override_approval and call doc.db_set + doc.cancel where needed.
        if self.status != "Pending CEO":
            frappe.throw(_("Newly submitted override must be in 'Pending CEO' state."))

    def before_cancel(self):
        # Cancellation is permitted from Approved (escape valve) / Rejected (reopen) /
        # Pending CEO (submitter cancel) — all flows go through cancel_budget_override endpoint.
        if not getattr(frappe.flags, "in_budget_override_approval", False):
            # Direct UI cancel is allowed only for System Manager.
            if "System Manager" not in frappe.get_roles(frappe.session.user):
                frappe.throw(_("Cancel this override via the workflow, not directly."))

    def _compute_title(self):
        bt = self.breach_type or "Budget"
        ref_dt = self.reference_doctype or ""
        ref_nm = self.reference_name or ""
        self.title = f"{bt} breach — {ref_dt} {ref_nm}".strip()

    def _validate_reference_exists(self):
        if not (self.reference_doctype and self.reference_name):
            frappe.throw(_("Reference document is mandatory."))
        if not frappe.db.exists(self.reference_doctype, self.reference_name):
            frappe.throw(
                _("Reference {0} {1} does not exist.").format(
                    self.reference_doctype, self.reference_name
                )
            )

    def _validate_status_transition(self):
        # Allowed transitions:
        #   Pending CEO -> Approved | Rejected | Cancelled
        #   Approved/Rejected/Cancelled -> (terminal — no change)
        # On amend (Frappe std cancel-then-amend), status MAY revert to Pending CEO.
        if self.is_new():
            if self.status != "Pending CEO":
                frappe.throw(_("New override must start in 'Pending CEO' status."))
            return
        previous = self.get_doc_before_save()
        if not previous:
            return
        if previous.status == self.status:
            return
        terminal = ("Approved", "Rejected", "Cancelled")
        if previous.status in terminal:
            # The only path out of terminal is Frappe amend, which we let through.
            if not getattr(frappe.flags, "in_budget_override_approval", False):
                frappe.throw(
                    _("Cannot change status of an override that has been {0}.").format(
                        previous.status
                    )
                )

    def _validate_decision_consistency(self):
        if self.status == "Rejected":
            if not (self.ceo_comment or "").strip() or len((self.ceo_comment or "").strip()) < 5:
                frappe.throw(_("CEO comment (≥5 chars) is required when rejecting an override."))
            if self.decision != "Rejected":
                self.decision = "Rejected"
        elif self.status == "Approved":
            if self.decision != "Approved":
                self.decision = "Approved"

    def _block_snapshot_tampering(self):
        """Lesson 162 — tamper guard on permlevel=1 snapshot fields.

        permlevel=1 alone is bypassable by users with write@permlevel=1 perm
        (CEO, MD have read but not write at level 1; SM does have write).
        This guard compares against DB and rejects any change unless
        frappe.flags.in_budget_override_approval is set (Lesson 176 — flag is
        only set inside the create/approve/reject server-side helpers).
        """
        if self.is_new():
            return
        if getattr(frappe.flags, "in_budget_override_approval", False):
            return
        previous = self.get_doc_before_save()
        if not previous:
            return
        for fieldname in SNAPSHOT_FIELDS:
            new_val = self.get(fieldname)
            old_val = previous.get(fieldname)
            if new_val != old_val:
                frappe.throw(
                    _("Snapshot field '{0}' cannot be modified after creation.").format(fieldname)
                )
        # Also guard the breach_lines child table.
        new_lines_sig = self._signature_for_lines(self.breach_lines or [])
        old_lines_sig = self._signature_for_lines(previous.breach_lines or [])
        if new_lines_sig != old_lines_sig:
            frappe.throw(_("Breach line snapshot cannot be modified after creation."))

    @staticmethod
    def _signature_for_lines(rows):
        return tuple(
            (
                getattr(r, "cost_center", None),
                getattr(r, "share_amount", None),
                getattr(r, "breach_type", None),
                getattr(r, "annual_budget", None),
                getattr(r, "annual_available", None),
                getattr(r, "annual_breach", None),
                getattr(r, "monthly_budget", None),
                getattr(r, "monthly_available", None),
                getattr(r, "monthly_breach", None),
            )
            for r in rows
        )
