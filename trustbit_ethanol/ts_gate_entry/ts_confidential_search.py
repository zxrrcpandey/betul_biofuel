"""Keep confidential PO / PR / PI / MR rows out of Frappe's search routes.

`ts_confidential_po` hides these rows with permission_query_conditions, but search
routes step around that rule:
  * search_widget / search_link with ignore_user_permissions=1 list the doctype, or
    any of its child tables, with permissions switched off;
  * a caller-named `query` runs a whitelisted query function as it is, and some
    ERPNext queries take a parent document in `filters` and return its item rows;
  * global search checks the doctype only.
These wrappers (hooks.py override_whitelisted_methods, which also maps the other
whitelisted paths that import the same core functions) keep the normal row rule for
users outside the allow-list, drop hidden rows from named queries and global search,
and otherwise call the core function unchanged.
"""

import frappe
from frappe.desk import search as core_search
from frappe.utils import global_search as core_global_search

from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential

CONFIDENTIAL_DOCTYPES = ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Material Request")


def _parent_of(doctype):
    """The confidential doctype whose child table `doctype` is, if the user may not fully see it."""
    for dt in CONFIDENTIAL_DOCTYPES:
        if doctype in {df.options for df in frappe.get_meta(dt).get_table_fields()} and not user_sees_confidential(dt):
            return dt


def _restricted(doctype):
    """True if `doctype` is one of the four, or a child table of one, the user may not fully see."""
    return (doctype in CONFIDENTIAL_DOCTYPES and not user_sees_confidential(doctype)) or bool(_parent_of(doctype))


def _hidden(doctype, names):
    """The confidential `doctype` documents among `names` that the user may not see."""
    names = list({n for n in names if isinstance(n, str) and n})
    if not names or user_sees_confidential(doctype) or not frappe.get_meta(doctype).has_field("ts_confidential"):
        return set()
    return set(frappe.get_all(doctype, pluck="name", filters={
        "name": ("in", names), "ts_confidential": ("!=", 0), "owner": ("!=", frappe.session.user)}))


def _row_name(row):
    if isinstance(row, dict):
        return row.get("name") or row.get("value")
    return row[0] if isinstance(row, (list, tuple)) and row else row


def _filter_values(filters):
    """Every plain value inside `filters` (dict, list or JSON text, nested)."""
    if isinstance(filters, str):
        try:
            filters = frappe.parse_json(filters)
        except Exception:
            return [filters]
    if isinstance(filters, dict):
        filters = list(filters.values())
    if not isinstance(filters, (list, tuple)):
        return [filters]
    return [v for f in filters for v in (_filter_values(f) if isinstance(f, (dict, list, tuple)) else [f])]


def _named_query_rows(doctype, rows, filters):
    """Rows of a named query: none if it was asked about a hidden document, else minus hidden ones."""
    if not rows or not isinstance(rows, (list, tuple)):
        return rows
    asked = [v for v in _filter_values(filters) if isinstance(v, str)]
    if any(_hidden(dt, asked) for dt in CONFIDENTIAL_DOCTYPES):
        return []
    names = [_row_name(r) for r in rows]
    hidden = set().union(*(_hidden(dt, names) for dt in CONFIDENTIAL_DOCTYPES))
    parent_dt = _parent_of(doctype)
    if parent_dt:  # child-table rows: drop those of a hidden parent
        parent = dict(frappe.get_all(doctype, filters={"name": ("in", [n for n in names if isinstance(n, str)] or [""])},
                                     fields=["name", "parent"], as_list=True))
        hidden_parents = _hidden(parent_dt, list(parent.values()))
        hidden |= {n for n, p in parent.items() if p in hidden_parents}
    return [r for r in rows if _row_name(r) not in hidden] if hidden else rows


@frappe.whitelist()
def search_widget(doctype, txt, query=None, searchfield=None, start=0, page_length=10, filters=None,
                  filter_fields=None, as_dict=False, reference_doctype=None, ignore_user_permissions=False):
    if _restricted(doctype):
        ignore_user_permissions = False  # keep the row rule that hides confidential rows
    query = query and frappe.override_whitelisted_method(query)  # core calls `query` as named, skipping overrides
    rows = core_search.search_widget(
        doctype, txt, query=query, searchfield=searchfield, start=start, page_length=page_length,
        filters=filters, filter_fields=filter_fields, as_dict=as_dict,
        reference_doctype=reference_doctype, ignore_user_permissions=ignore_user_permissions)
    return _named_query_rows(doctype, rows, filters) if query else rows


@frappe.whitelist()
def search_link(doctype, txt, query=None, filters=None, page_length=10, searchfield=None,
                reference_doctype=None, ignore_user_permissions=False):
    if _restricted(doctype):
        ignore_user_permissions = False
    query = query and frappe.override_whitelisted_method(query)
    rows = core_search.search_link(
        doctype, txt, query=query, filters=filters, page_length=page_length, searchfield=searchfield,
        reference_doctype=reference_doctype, ignore_user_permissions=ignore_user_permissions)
    return _named_query_rows(doctype, rows, filters) if query else rows


@frappe.whitelist()
def global_search(text, start=0, limit=20, doctype=""):
    rows = core_global_search.search(text, start=start, limit=limit, doctype=doctype)
    hidden = {(dt, n) for dt in CONFIDENTIAL_DOCTYPES
              for n in _hidden(dt, [r.get("name") for r in rows if r.get("doctype") == dt])}
    return [r for r in rows if (r.get("doctype"), r.get("name")) not in hidden]


# ERPNext query functions that can name a confidential document or its rows, called directly.
def _query(path, doctype, txt, searchfield, start, page_len, filters):
    rows = frappe.get_attr(path)(doctype, txt, searchfield, start, page_len, filters)
    return _named_query_rows(doctype, rows, filters)


def _as_dict(filters):
    filters = frappe.parse_json(filters) if isinstance(filters, str) else filters
    return filters if isinstance(filters, dict) else {}


@frappe.whitelist()
def get_filtered_child_rows(doctype, txt, searchfield, start, page_len, filters):
    """ERPNext builds a column from every filter KEY with no escaping (SQL injection) and reads any
    parent. As ERPNext version-15 later did: one plain parent the user may read, a child doctype,
    and filter keys that are real columns with plain values."""
    filters = _as_dict(filters)
    parent = filters.get("parent")
    if not parent or not isinstance(parent, str) or not frappe.get_meta(doctype).istable:
        frappe.throw(frappe._("Parent document is required to search child rows"), frappe.PermissionError)
    columns = set(frappe.get_meta(doctype).get_valid_columns())
    if any(k not in columns or not isinstance(v, (str, int, float)) for k, v in filters.items()):
        frappe.throw(frappe._("Invalid filters"), frappe.PermissionError)
    parenttype = frappe.db.get_value(doctype, {"parent": parent}, "parenttype")
    if not parenttype or not frappe.db.exists(parenttype, parent):
        return []
    frappe.has_permission(doctype, parent_doctype=parenttype, throw=True)
    frappe.has_permission(parenttype, doc=parent, throw=True)
    if _hidden(parenttype, [parent]):  # after the permission checks, so the answer says nothing more
        return []
    return _query("erpnext.controllers.queries.get_filtered_child_rows", doctype, txt, searchfield, start, page_len, filters)


@frappe.whitelist()
def get_purchase_receipts(doctype, txt, searchfield, start, page_len, filters):
    return _query("erpnext.controllers.queries.get_purchase_receipts", doctype, txt, searchfield, start, page_len, filters)


@frappe.whitelist()
def get_purchase_invoices(doctype, txt, searchfield, start, page_len, filters):
    return _query("erpnext.controllers.queries.get_purchase_invoices", doctype, txt, searchfield, start, page_len, filters)


@frappe.whitelist()
def get_payment_terms_for_references(doctype, txt, searchfield, start, page_len, filters):
    filters = _as_dict(filters)
    if not isinstance(filters.get("reference"), str):  # an operator would span other documents
        return []
    return _query("erpnext.controllers.queries.get_payment_terms_for_references", doctype, txt, searchfield, start, page_len, filters)


@frappe.whitelist()
def qi_item_query(doctype, txt, searchfield, start, page_len, filters):
    return _query("erpnext.stock.doctype.quality_inspection.quality_inspection.item_query", doctype, txt, searchfield, start, page_len, filters)
