import frappe
from frappe import _
from frappe.utils import getdate, now_datetime, get_datetime


class TSPostDatedEntryRequest(frappe.model.document.Document):
	def validate(self):
		self._validate_dates()
		self._validate_request_type()

	def before_insert(self):
		# Only IT Head or System Manager can create
		user_roles = frappe.get_roles()
		if "IT Head" not in user_roles and "System Manager" not in user_roles:
			frappe.throw(_("Only IT Head can create Post-Dated Entry Requests"))

		self.requested_by = frappe.session.user
		self.request_date = now_datetime()
		self.status = "Draft"

	def _validate_dates(self):
		if self.from_date and self.to_date:
			if getdate(self.from_date) > getdate(self.to_date):
				frappe.throw(_("From Date cannot be after To Date"))

		if self.valid_from and self.valid_until:
			if get_datetime(self.valid_from) >= get_datetime(self.valid_until):
				frappe.throw(_("Valid From must be before Valid Until"))

		if self.from_date and getdate(self.from_date) > getdate():
			frappe.throw(_("From Date cannot be a future date"))

		# Check max backdate days (0 = unlimited)
		settings = frappe.get_cached_doc("TS Settings")
		max_days = settings.get("max_backdate_days")
		if max_days is None:
			max_days = 30
		if self.from_date and max_days > 0:
			from frappe.utils import date_diff
			days_back = date_diff(getdate(), getdate(self.from_date))
			if days_back > max_days:
				frappe.throw(
					_("Cannot backdate more than {0} days. From Date is {1} days in the past.").format(
						max_days, days_back
					)
				)

	def _validate_request_type(self):
		if self.request_type == "Transaction-wise" and not self.token_number:
			frappe.throw(_("Token Number is required for Transaction-wise requests"))

		if self.request_type == "DocType-wise" and not self.allowed_doctypes:
			frappe.throw(_("At least one DocType must be selected for DocType-wise requests"))
