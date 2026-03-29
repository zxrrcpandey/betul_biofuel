import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, cint, nowdate, get_first_day, get_last_day


class TSBudgetProposal(Document):
    def validate(self):
        self._calculate_totals()
        self._validate_duplicate()
        self._validate_accounts()

    def _calculate_totals(self):
        self.total_proposed = sum(flt(row.proposed_amount) for row in self.budget_items)
        self.total_approved = sum(flt(row.ceo_approved_amount) for row in self.budget_items)

    def _validate_duplicate(self):
        # Check on new AND on status changes (e.g., resubmit from Revised)
        existing = frappe.db.exists("TS Budget Proposal", {
            "cost_center": self.cost_center,
            "fiscal_year": self.fiscal_year,
            "status": ["in", ["Draft", "Pending CEO", "Approved"]],
            "name": ["!=", self.name or ""]
        })
        if existing:
            frappe.throw(_(
                "A budget proposal already exists for {0} in {1}: {2}"
            ).format(self.cost_center, self.fiscal_year, existing))

    def _validate_accounts(self):
        seen = []
        for row in self.budget_items:
            if row.account in seen:
                frappe.throw(_("Duplicate account {0} in budget items").format(row.account))
            seen.append(row.account)
            if flt(row.proposed_amount) < 0:
                frappe.throw(_("Proposed amount for {0} cannot be negative").format(row.account))
