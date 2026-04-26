import frappe
from frappe.model.document import Document


class TSQCTemplate(Document):
	def validate(self):
		self._validate_parameter_codes_unique()
		self._validate_numeric_ranges()
		self._validate_bag_type_consistency()

	def _validate_parameter_codes_unique(self):
		"""Within a single template, parameter_code must be unique across rows."""
		if not self.parameters:
			return
		seen = {}
		for row in self.parameters:
			code = (row.parameter_code or "").strip().upper()
			if not code:
				continue
			if code in seen:
				frappe.throw(
					f"Duplicate parameter code '{row.parameter_code}' in row "
					f"{row.idx} (also at row {seen[code]}). Codes must be unique within a template."
				)
			seen[code] = row.idx

	def _validate_numeric_ranges(self):
		"""For Numeric parameters, min_value <= max_value when both provided."""
		if not self.parameters:
			return
		for row in self.parameters:
			if row.parameter_type != "Numeric":
				continue
			if row.min_value is not None and row.max_value is not None:
				if row.min_value > row.max_value:
					frappe.throw(
						f"Row {row.idx} ({row.parameter_label}): "
						f"Min ({row.min_value}) cannot be greater than Max ({row.max_value})."
					)

	def _validate_bag_type_consistency(self):
		"""If supports_bags is unchecked, bag_type should be cleared."""
		if not self.supports_bags and self.bag_type:
			self.bag_type = None
