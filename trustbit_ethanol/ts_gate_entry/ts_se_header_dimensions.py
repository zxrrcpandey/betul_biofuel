"""v2.16.5 — Stock Entry header accounting defaults that cascade to item rows.

Two header Custom Fields on Stock Entry (Details tab, right after `company`):
  - ts_set_cost_center  ("Cost Center (all items)")  -> Link Cost Center
  - ts_set_project      ("Project (all items)")      -> Link Project

Picking a header value fills the matching field (cost_center / project) on every
item row: instantly in the browser via public/js/stock_entry_header_dims.js, and
as a server safety net on save via cascade_se_header_dimensions().

The server cascade is FILL-BLANK-ONLY (never overwrites a row value), so any
programmatic Stock Entry that leaves the header fields blank — e.g. the MR
Transfer/Issue flow's make_stock_entry draft — simply no-ops here.
"""

import frappe

# (header field on Stock Entry, field to fill on each Stock Entry Detail row)
_HEADER_MAP = (
    ("ts_set_cost_center", "cost_center"),
    ("ts_set_project", "project"),
)


def seed_se_header_dimension_fields():
    """after_migrate — create the 2 header Custom Fields on Stock Entry. Idempotent.

    Kept in this separate module (NOT setup.py, which is locked under
    MR_FULL/PO_FULL/MR_FIELDS) — same pattern as the v2.16.4 vehicle_number seeder.
    """
    fields = [
        {
            "dt": "Stock Entry",
            "fieldname": "ts_set_cost_center",
            "fieldtype": "Link",
            "options": "Cost Center",
            "label": "Cost Center (all items)",
            "insert_after": "company",
            "description": "Setting this fills Cost Center on every item row.",
        },
        {
            "dt": "Stock Entry",
            "fieldname": "ts_set_project",
            "fieldtype": "Link",
            "options": "Project",
            "label": "Project (all items)",
            "insert_after": "ts_set_cost_center",
            "description": "Setting this fills Project on every item row.",
        },
    ]
    created = False
    for f in fields:
        if frappe.db.exists("Custom Field", {"dt": f["dt"], "fieldname": f["fieldname"]}):
            continue
        cf = frappe.get_doc({"doctype": "Custom Field", **f})
        cf.flags.ignore_links = True
        cf.insert(ignore_permissions=True, ignore_links=True)
        created = True
    if created:
        frappe.db.updatedb("Stock Entry")
        frappe.db.commit()


def cascade_se_header_dimensions(doc, method=None):
    """Stock Entry before_validate — fill BLANK item-row cost_center/project from
    the header fields. Fill-only (never overwrites an existing row value), so a
    programmatic SE with blank headers no-ops and per-row overrides are respected.
    """
    if not doc.get("items"):
        return
    for header_field, row_field in _HEADER_MAP:
        header_val = doc.get(header_field)
        if not header_val:
            continue
        for row in doc.items:
            if not row.get(row_field):
                row.set(row_field, header_val)
