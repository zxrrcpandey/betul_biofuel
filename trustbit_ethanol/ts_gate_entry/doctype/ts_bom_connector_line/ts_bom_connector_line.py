# Copyright (c) 2026, Trustbit Software
# Child of TS BOM Connector — one reporting-only department BOM line.
# Validation lives on the parent (TSBOMConnector._validate_department_lines).

from frappe.model.document import Document


class TSBOMConnectorLine(Document):
	pass
