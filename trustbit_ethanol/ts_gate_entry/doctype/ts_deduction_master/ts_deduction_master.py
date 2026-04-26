import frappe
from frappe.model.document import Document


class TSDeductionMaster(Document):
	def validate(self):
		self._validate_unique_default_per_category()
		self._validate_deduction_codes_unique()

	def _validate_unique_default_per_category(self):
		"""
		Per Plan v4 Gap 10: only one master can have is_default=1 per category.

		Race-safe pattern: SELECT FOR UPDATE locks the row in tabTS Deduction Master
		matching (category, is_default=1, name != self.name). If concurrent saves race,
		the second one waits, sees the lock, and is rejected here.
		"""
		if not self.is_default:
			return
		if not self.category:
			return

		existing = frappe.db.sql(
			"""
			SELECT name FROM `tabTS Deduction Master`
			WHERE category = %s
			  AND is_default = 1
			  AND name != %s
			FOR UPDATE
			""",
			(self.category, self.name or ""),
			as_dict=True,
		)
		if existing:
			others = ", ".join(row["name"] for row in existing)
			frappe.throw(
				f"Only one TS Deduction Master can be the default for category "
				f"'{self.category}'. Currently default: {others}. "
				f"Uncheck 'Default for Category' on the other master first."
			)

	def _validate_deduction_codes_unique(self):
		"""Within a single master, deduction_code must be unique across rows."""
		if not self.deductions:
			return
		seen = {}
		for row in self.deductions:
			code = (row.deduction_code or "").strip().upper()
			if not code:
				continue
			if code in seen:
				frappe.throw(
					f"Duplicate deduction code '{row.deduction_code}' in row "
					f"{row.idx} (also at row {seen[code]}). Codes must be unique within a master."
				)
			seen[code] = row.idx
