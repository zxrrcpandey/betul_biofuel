"""Request for Quotation Item — Cost Center dimension (inherited from the MR).

ERPNext's RFQ Item ships `project_name` but no `cost_center`, so the dimension
dies at the RFQ hop — and accounts_controller.py:1094-1097 then fills the gap
with the COMPANY DEFAULT, silently booking MR -> RFQ -> SQ purchases to
`Main - BBPL` instead of the requesting department. See pending_works.md.

No mapper code is needed: frappe/model/mapper.py:173-204 map_fields() walks the
TARGET meta (Custom Fields included) and copies any same-named source value.
Three properties are therefore load-bearing:
  * the name `cost_center` IS the mechanism — deliberately NOT ts_-prefixed
    (contra feedback_ts_prefix.md); a prefix breaks both hops silently
  * no_copy must stay absent — no_copy=1 on either side kills the copy
  * NO "default" — a CF default becomes a DDL default and back-stamps every
    pre-existing row (L367)
`print_hide` is mandatory: RFQ has no app print format, so Standard renders every
child field with print_hide=0 — and on_submit emails that PDF TO SUPPLIERS.

Own module rather than setup.py (which the MR_FULL / PO_FULL locks filename-match),
matching setup_dn_transport.py / setup_msme_fields.py / setup_confidential_po.py.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

_FIELDS = {
	"Request for Quotation Item": [
		{
			"fieldname": "cost_center",
			"label": "Cost Center",
			"fieldtype": "Link",
			"options": "Cost Center",
			"insert_after": "project_name",
			"print_hide": 1,
			"ignore_user_permissions": 1,
			"description": "Inherited from the source Material Request Item; carries on to Supplier Quotation and Purchase Order.",
		},
	]
}


def seed_rfq_dimension_fields():
	"""after_migrate — create/heal RFQ Item.cost_center. Idempotent + self-healing."""
	# If a future ERPNext ships the field natively, CustomField.validate would throw
	# and abort the whole create_custom_fields run for every field after it.
	meta = frappe.get_meta("Request for Quotation Item")
	if meta.has_field("cost_center") and not frappe.db.exists(
		"Custom Field", "Request for Quotation Item-cost_center"
	):
		return

	create_custom_fields(_FIELDS, ignore_validate=True)
	frappe.clear_cache(doctype="Request for Quotation Item")


def _fill_rows(rows, project_field):
	"""Fill cost_center + project on item rows that carry an MR link. Fill-only.

	PROJECT RESOLUTION ORDER IS DELIBERATE: the MR *header* custom field
	`ts_project` wins over the native item-level `project`. BBPL fills ts_project
	and the native field is deliberately hidden (setup.py:1120-1139 — "downstream
	code reads ts_project, not the native ERPNext project field"), so the native
	item column is empty on 77 MRs that DO have a project. Reading the item field
	alone is exactly why the project never reached the RFQ. This mirrors what
	ts_po_approval._copy_project_from_mr already does on the PO route.

	`project_field` differs by target: RFQ Item calls it `project_name`, SQ Item
	and PO Item call it `project`.
	"""
	mr_names = {r.get("material_request") for r in rows if r.get("material_request")}
	mri_names = {r.get("material_request_item") for r in rows if r.get("material_request_item")}

	headers = {}
	if mr_names:
		headers = {
			d.name: d.ts_project
			for d in frappe.get_all(
				"Material Request",
				filters={"name": ("in", list(mr_names))},
				fields=["name", "ts_project"],
			)
		}
	items = {}
	if mri_names:
		items = {
			d.name: d
			for d in frappe.get_all(
				"Material Request Item",
				filters={"name": ("in", list(mri_names))},
				fields=["name", "parent", "cost_center", "project"],
			)
		}

	for r in rows:
		src = items.get(r.get("material_request_item"))
		if src and not r.get("cost_center") and src.cost_center:
			r.cost_center = src.cost_center
		if not r.get(project_field):
			parent = (src.parent if src else None) or r.get("material_request")
			value = headers.get(parent) or (src.project if src else None)
			if value:
				r.set(project_field, value)


def _sourced_rows(doc):
	return [
		r
		for r in (doc.get("items") or [])
		if r.get("material_request") or r.get("material_request_item")
	]


def rfq_inherit_mr_dimensions(doc, method=None):
	"""Request for Quotation before_validate — carry both dimensions from the MR.

	cost_center already arrives via the same-name mapper copy; this adds the
	project (which the mapper cannot find, see _fill_rows) and covers an RFQ whose
	material_request link was set by hand rather than by the mapper.
	"""
	rows = _sourced_rows(doc)
	if rows:
		_fill_rows(rows, "project_name")


def sq_inherit_mr_dimensions(doc, method=None):
	"""Supplier Quotation before_validate — same carry, plus the header fallback.

	Largely a no-op on the normal RFQ -> SQ mapper path. It matters for the
	SUPPLIER PORTAL route: create_rfq_items (request_for_quotation.py:508)
	hand-copies a fixed 11-field allowlist carrying material_request_item but
	neither dimension, bypassing the mapper entirely.
	"""
	rows = _sourced_rows(doc)
	if rows:
		_fill_rows(rows, "project")
	_set_header_dimensions(doc)


@frappe.whitelist()
def make_request_for_quotation(source_name, target_doc=None):
	"""MR -> RFQ mapper wrapper: fill the dimensions BEFORE the form is saved.

	The core mapper same-name-copies cost_center (the Custom Field exists now) but
	cannot see the project, which lives on the MR header as ts_project. Filling it
	only in before_validate meant it appeared AFTER the user pressed Save. Filling
	the mapper's RETURN value means the new unsaved form already renders both.

	Decorator deliberately mirrors core (no methods= restriction): this builds an
	in-memory doc and persists nothing, and get_mapped_doc still enforces the
	target "create" permission. Restricting it would break the desk Create button.
	"""
	from erpnext.stock.doctype.material_request.material_request import (
		make_request_for_quotation as _core,
	)

	doc = _core(source_name, target_doc)
	rows = _sourced_rows(doc)
	if rows:
		_fill_rows(rows, "project_name")
	return doc


@frappe.whitelist()
def make_supplier_quotation_from_rfq(source_name, target_doc=None, for_supplier=None):
	"""RFQ -> SQ mapper wrapper — same pre-save fill, plus the header cost centre."""
	from erpnext.buying.doctype.request_for_quotation.request_for_quotation import (
		make_supplier_quotation_from_rfq as _core,
	)

	doc = _core(source_name, target_doc, for_supplier)
	rows = _sourced_rows(doc)
	if rows:
		_fill_rows(rows, "project")
	_set_header_dimensions(doc)
	return doc


@frappe.whitelist()
def make_purchase_order(source_name, target_doc=None, args=None):
	"""SQ -> PO mapper wrapper: mirror project into ts_project BEFORE the form saves.

	Without this the PO opens with a blank Project box (the visible field is
	ts_project; native `project` is hidden) and only fills once the buyer saves.
	"""
	from erpnext.buying.doctype.supplier_quotation.supplier_quotation import (
		make_purchase_order as _core,
	)

	doc = _core(source_name, target_doc, args)
	po_inherit_project(doc)
	return doc


def po_inherit_project(doc, method=None):
	"""Purchase Order before_validate — mirror native `project` into `ts_project`.

	BBPL reads ts_project on the PO and the native `project` field is HIDDEN by a
	Property Setter (setup.py:1110-1146), so ts_project is the box a buyer sees.
	MR -> PO carries it by same-name copy (both doctypes have ts_project), but
	Supplier Quotation has NO ts_project field — so the MR -> RFQ -> SQ -> PO
	route lands with native `project` set and ts_project EMPTY, i.e. a blank
	Project box even though the value is present. Mirror it across.

	Ordering: before_validate runs BEFORE ts_po_approval.po_before_save, whose
	_copy_project_from_mr is skipped once doc.project is set, and whose own
	ts_project -> project mirror (:2833) then finds the two equal and no-ops.

	Fill-only — never overwrites a ts_project the user chose.
	"""
	if not doc.get("ts_project") and doc.get("project"):
		doc.ts_project = doc.project


def _set_header_dimensions(doc):
	"""Fill the SQ header cost_center AND project when the rows agree on one value.

	Both are needed, for different reasons:
	  * cost_center — accounts_controller.py:1094-1097 fills a blank ITEM cost
	    centre from the HEADER, then the company default. Without this a row the
	    buyer adds by hand (no MR link, nothing for _fill_rows to work from) still
	    lands on `Main - BBPL`.
	  * project — Supplier Quotation Item.project is in_list_view=0, so the value
	    is invisible in the grid unless the row is expanded. The header field is
	    where a buyer actually reads it, and leaving it blank while cost_center
	    populated is what read as "project is not fetching".

	Only set when unambiguous: never guess across rows that disagree, and never
	overwrite a value already present.
	"""
	for fieldname in ("cost_center", "project"):
		if doc.get(fieldname):
			continue
		found = {r.get(fieldname) for r in (doc.get("items") or []) if r.get(fieldname)}
		if len(found) == 1:
			doc.set(fieldname, found.pop())
