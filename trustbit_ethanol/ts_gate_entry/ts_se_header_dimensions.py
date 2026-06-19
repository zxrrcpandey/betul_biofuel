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
        # v2.17.6 — read-only parent Cost Center, auto-mirrored from the item rows,
        # purely so the Stock Entry LIST VIEW can be filtered by Cost Center (the
        # native cost_center lives on the child rows, which cannot be a standard
        # filter). Maintained by _set_filter_cost_center() in before_validate.
        {
            "dt": "Stock Entry",
            "fieldname": "ts_cost_center",
            "fieldtype": "Link",
            "options": "Cost Center",
            "label": "Cost Center",
            "insert_after": "ts_set_project",
            "read_only": 1,
            "no_copy": 1,
            "in_standard_filter": 1,
            "description": "Auto-set from the item rows — enables Cost Center filtering in the list view.",
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
    # Idempotent: ensure the list-view filter flag is set even if the field
    # pre-existed without it (defensive — migrate re-runs this seeder).
    if frappe.db.exists("Custom Field", {"dt": "Stock Entry", "fieldname": "ts_cost_center"}):
        cf_name = frappe.db.get_value(
            "Custom Field", {"dt": "Stock Entry", "fieldname": "ts_cost_center"}, "name"
        )
        if not frappe.db.get_value("Custom Field", cf_name, "in_standard_filter"):
            frappe.db.set_value("Custom Field", cf_name, "in_standard_filter", 1)
            created = True
    _grant_cost_center_read_to_stock_roles()
    if created:
        frappe.db.updatedb("Stock Entry")
        frappe.clear_cache(doctype="Stock Entry")
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
    _set_filter_cost_center(doc)


def _set_filter_cost_center(doc):
    """Mirror the first item row's cost_center up to the read-only parent field
    `ts_cost_center` so the Stock Entry LIST VIEW can be filtered by Cost Center.
    The native cost_center is per-row (a child field can't be a standard filter),
    so this derived header value is what the list filter targets. Recomputed every
    save from the rows (the rows are the source of truth); blank if no row has one.
    """
    cc = None
    for row in doc.items:
        if row.get("cost_center"):
            cc = row.cost_center
            break
    doc.ts_cost_center = cc


def _grant_cost_center_read_to_stock_roles():
    """Idempotent — grant Cost Center READ (permlevel 0) to the stock/manufacturing
    roles that use the Stock Entry list, so the new `ts_cost_center` standard-filter
    autocomplete works for them (else Frappe throws "No permission for Cost Center").
    Mirrors _seed_cost_center_approval_perms (Lesson 169 safe: get_doc + db_insert,
    never raw INSERT which would drop the Standard DocPerm). Skips missing roles and
    rows that already exist.
    """
    for role in ("Stock Manager", "Manufacturing User", "Manufacturing Manager"):
        if not frappe.db.exists("Role", role):
            continue
        if frappe.db.exists("Custom DocPerm", {
            "parent": "Cost Center", "role": role, "permlevel": 0,
        }):
            continue
        try:
            frappe.get_doc({
                "doctype": "Custom DocPerm",
                "parent": "Cost Center",
                "parenttype": "DocType",
                "parentfield": "permissions",
                "role": role,
                "permlevel": 0,
                "read": 1,
            }).db_insert()
        except Exception as e:
            frappe.log_error(message=f"grant CC read to {role}: {e}",
                             title="grant_cost_center_read_to_stock_roles")


def backfill_se_cost_center_filter():
    """One-time data op — populate `ts_cost_center` on EXISTING Stock Entries from
    their first item row's cost_center, so the new list filter finds historical
    records. Idempotent (re-derives from the rows each run). Run via bench console
    on each server after deploy; NOT wired to after_migrate (would re-scan every
    Stock Entry on every migrate). Returns the count updated.
    """
    names = frappe.get_all("Stock Entry", pluck="name")
    n = 0
    for name in names:
        row = frappe.db.sql(
            """SELECT cost_center FROM `tabStock Entry Detail`
               WHERE parent=%s AND IFNULL(cost_center,'')<>''
               ORDER BY idx LIMIT 1""", (name,))
        cc = row[0][0] if row else None
        if cc and frappe.db.get_value("Stock Entry", name, "ts_cost_center") != cc:
            frappe.db.set_value("Stock Entry", name, "ts_cost_center", cc,
                                update_modified=False)
            n += 1
    frappe.db.commit()
    return n
