"""v2.10.0 — Re-apply PROPERTY_SETTERS that add "Pending Budget Override" to
ts_mr_status (Material Request) and ts_approval_status (Purchase Order).

Lesson 239: bench migrate does NOT auto-apply seed_data PROPERTY_SETTERS for
existing Select fields. Without this patch, submitting a budget-breaching MR/PO
would throw "Approval Status cannot be 'Pending Budget Override'. It should be
one of …" because the Select-field options validator rejects the new value.

This patch runs make_property_setter with validate_fields_for_doctype=False so
both options strings get stored as the canonical Property Setter row.

Idempotent — running this multiple times re-writes the same value.
"""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


MR_STATUS_OPTIONS = (
    "Not Submitted\n"
    "Pending Dept. User\n"
    "Pending Dept. Head\n"
    "Pending AVP\n"
    "Pending CEO\n"
    "Pending GM\n"
    "Pending Budget Override\n"
    "Approved\n"
    "Revised\n"
    "Rejected\n"
    "On Hold\n"
    "Pending Stores Manager"
)

PO_STATUS_OPTIONS = (
    "Not Submitted\n"
    "Pending PM\n"
    "Pending Grain PM\n"
    "Pending Dept. Head\n"
    "Pending CEO\n"
    "Pending MD\n"
    "Pending GM\n"
    "Pending Budget Override\n"
    "Approved\n"
    "Revised\n"
    "Rejected"
)


def execute():
    make_property_setter(
        "Material Request",
        "ts_mr_status",
        "options",
        MR_STATUS_OPTIONS,
        "Text",
        validate_fields_for_doctype=False,
    )
    make_property_setter(
        "Purchase Order",
        "ts_approval_status",
        "options",
        PO_STATUS_OPTIONS,
        "Text",
        validate_fields_for_doctype=False,
    )
    frappe.db.commit()
    frappe.clear_cache(doctype="Material Request")
    frappe.clear_cache(doctype="Purchase Order")
