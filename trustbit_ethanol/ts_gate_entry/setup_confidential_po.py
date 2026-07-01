"""after_migrate seeder — `ts_confidential` Check field on PO / PR / PI / MR.

Kept OUT of setup.py (its PO/MR custom-field sections are locked under
PO_FULL / MR_FULL). Mirrors the ts_se_header_dimensions.py pattern: idempotent,
declarative, `create_custom_fields` (insert-or-update), clears doctype cache.

The field is the marker read by `ts_confidential_po.py`. On PO/MR it is
user-writable (allow-list roles tick it; tamper-guarded). On PR/PI it is
read-only (derived by propagation from the linked PO).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

_FIELDS = {
    "Purchase Order": [
        {
            "fieldname": "ts_confidential",
            "label": "Confidential",
            "fieldtype": "Check",
            "insert_after": "ts_purchase_category",
            "default": "0",
            "allow_on_submit": 1,
            "no_copy": 0,
            "in_standard_filter": 0,
            "description": (
                "Hide this PO and its Purchase Receipt / Invoice from users outside "
                "the authorized list. Auto-set for the maize raw-material cost center."
            ),
        }
    ],
    "Material Request": [
        {
            "fieldname": "ts_confidential",
            "label": "Confidential",
            "fieldtype": "Check",
            "insert_after": "company",
            "default": "0",
            "allow_on_submit": 1,
            "no_copy": 0,
            "in_standard_filter": 0,
            "description": "Hide this Material Request from users outside the authorized list.",
        }
    ],
    "Purchase Receipt": [
        {
            "fieldname": "ts_confidential",
            "label": "Confidential",
            "fieldtype": "Check",
            "insert_after": "company",
            "default": "0",
            "read_only": 1,
            "allow_on_submit": 1,
            "no_copy": 0,
            "in_standard_filter": 0,
            "description": "Derived: confidential when any linked Purchase Order is confidential.",
        }
    ],
    "Purchase Invoice": [
        {
            "fieldname": "ts_confidential",
            "label": "Confidential",
            "fieldtype": "Check",
            "insert_after": "company",
            "default": "0",
            "read_only": 1,
            "allow_on_submit": 1,
            "no_copy": 0,
            "in_standard_filter": 0,
            "description": "Derived: confidential when any linked Purchase Order is confidential.",
        }
    ],
}


def seed_confidential_fields():
    """after_migrate — create/update the ts_confidential field on all four doctypes,
    then self-heal any maize doc that slipped the save-time auto-set."""
    create_custom_fields(_FIELDS, ignore_validate=True)
    for doctype in _FIELDS:
        frappe.clear_cache(doctype=doctype)

    # Backfill AFTER the fields exist — flags maize-involved PO/MR (header OR
    # line-item cost center) + derived PR/PI that the before_save auto-set missed
    # (idempotent, never-clear; closes the line-item-maize gap found 2 Jul 2026).
    from trustbit_ethanol.ts_gate_entry.ts_confidential_po import backfill_confidential_flags
    flipped = backfill_confidential_flags()
    if any(flipped.values()):
        frappe.logger().info("ts_confidential backfill flipped: %s" % flipped)
