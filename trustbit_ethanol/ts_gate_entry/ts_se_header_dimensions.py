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

# (Stock Entry header field  <-  Material Request source field) — for auto-fetch.
_MR_SOURCE_MAP = (
    ("ts_set_cost_center", "cost_center"),
    ("ts_set_project", "ts_project"),
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


def _autofetch_header_dims_from_mr(doc):
    """Fill BLANK header dimension fields from the Stock Entry's source Material
    Request. ERPNext's make_stock_entry mapper stamps `material_request` on every
    item row created from an MR — both the Stores Material-Issue flow
    (ts_mr_transfer.approve_transfer) and a manual "Get Items From -> Material
    Request" — so the MR that drove the approval (and carries the cost_center used
    for routing + ts_project) can be recovered from the rows and re-applied.

    Fill-blank only: a header value the user picked is never overwritten, and a
    standalone Stock Entry with no MR link simply no-ops (manual entry as before).
    """
    # Nothing to fetch if both header dims are already set.
    if doc.get("ts_set_cost_center") and doc.get("ts_set_project"):
        return
    mr_name = None
    for row in doc.items:
        if row.get("material_request"):
            mr_name = row.material_request
            break
    if not mr_name:
        return
    mr = frappe.db.get_value(
        "Material Request", mr_name, ["cost_center", "ts_project"], as_dict=True
    )
    if not mr:
        return
    for header_field, mr_field in _MR_SOURCE_MAP:
        if not doc.get(header_field) and mr.get(mr_field):
            doc.set(header_field, mr.get(mr_field))


def cascade_se_header_dimensions(doc, method=None):
    """Stock Entry before_validate — fill BLANK item-row cost_center/project from
    the header fields. Fill-only (never overwrites an existing row value), so a
    programmatic SE with blank headers no-ops and per-row overrides are respected.

    The header fields themselves are first auto-fetched from the source Material
    Request (when blank) so an MR-sourced issue inherits the MR's Cost Center +
    Project end-to-end without manual re-entry.
    """
    if not doc.get("items"):
        return
    _autofetch_header_dims_from_mr(doc)
    for header_field, row_field in _HEADER_MAP:
        header_val = doc.get(header_field)
        if not header_val:
            continue
        for row in doc.items:
            if not row.get(row_field):
                row.set(row_field, header_val)
