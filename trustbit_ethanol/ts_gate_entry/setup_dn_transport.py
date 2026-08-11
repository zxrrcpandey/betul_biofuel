"""Delivery Note "Transport Details" section — v2.39.0.

Sales-department capture of dispatch transport + TPIA batch details at the
end of the Address & Contact tab. Transporter REUSES the existing TS
Transport Master; the rest are free-text/date values copied from the
physical dispatch papers. All fields optional with no defaults: TS Token's
_create_delivery_note() auto-inserts DNs on Stock OUT exit, so a reqd
custom field would break Mark Exit, and a "default" would DDL-back-stamp
every existing DN row (L367). Idempotent; registered in after_migrate.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

_FIELDS = {
    "Delivery Note": [
        {"fieldname": "ts_transport_details_section", "label": "Transport Details",
            "fieldtype": "Section Break", "insert_after": "company_contact_person"},
        {"fieldname": "ts_transporter", "label": "Transport Name", "fieldtype": "Link",
            "options": "TS Transport Master", "insert_after": "ts_transport_details_section",
            "description": "Registered transporter (TS Transport Master)"},
        {"fieldname": "ts_driver_name", "label": "Driver Name", "fieldtype": "Data",
            "insert_after": "ts_transporter"},
        {"fieldname": "ts_driver_mobile", "label": "Driver Mobile Number", "fieldtype": "Data",
            "options": "Phone", "insert_after": "ts_driver_name"},
        {"fieldname": "ts_driver_license_number", "label": "Driver Licence Number",
            "fieldtype": "Data", "insert_after": "ts_driver_mobile"},
        {"fieldname": "ts_transport_details_column", "fieldtype": "Column Break",
            "insert_after": "ts_driver_license_number"},
        {"fieldname": "ts_vehicle_number", "label": "Vehicle Number", "fieldtype": "Data",
            "insert_after": "ts_transport_details_column"},
        {"fieldname": "ts_certificate_number", "label": "Certificate Number",
            "fieldtype": "Data", "insert_after": "ts_vehicle_number"},
        {"fieldname": "ts_tpia_main_batch_number", "label": "TPIA Main Batch Number",
            "fieldtype": "Data", "insert_after": "ts_certificate_number"},
        {"fieldname": "ts_tpia_secondary_batch_number", "label": "TPIA Secondary Batch Number",
            "fieldtype": "Data", "insert_after": "ts_tpia_main_batch_number"},
        {"fieldname": "ts_tpia_batch_date", "label": "TPIA Batch Date", "fieldtype": "Date",
            "insert_after": "ts_tpia_secondary_batch_number"},
    ]
}


_TM_PICKER_ROLES = ("Sales User", "Sales Manager")


def seed_dn_transport_fields():
    """after_migrate — create/heal the Delivery Note Transport Details fields."""
    create_custom_fields(_FIELDS, ignore_validate=True)
    _grant_transport_master_picker_access()
    frappe.clear_cache(doctype="Delivery Note")


def _grant_transport_master_picker_access():
    """SELECT-only TS Transport Master grant so the Sales-facing ts_transporter
    Link resolves — the picker enforces a perm on the link target, so without
    this it is empty for Sales. ptype="select" (not "read") on purpose: select
    perm serves ONLY meta.search_fields (transporter_name, transporter_code)
    to the picker and keeps the master's bank/PAN/GSTIN block and form
    click-through blocked for Sales. add_permission is idempotent and runs
    setup_custom_perms first (L169); roles filtered to existing ones (L290).
    NOTE: its dedup guard keys on (parent, role, permlevel) NOT ptype — a
    pre-existing row for the role blocks this grant instead of widening it.
    NOTE: a fresh Custom DocPerm row inherits the doctype's field DEFAULTS
    (read/export landed 1 on demo), so add_permission(ptype="select") alone
    ships a read grant — PROVEN live. The update_permission_property loop
    therefore zeroes EVERY grantable right except select, every migrate
    (L292 self-healing restriction, immune to default drift across
    Frappe upgrades)."""
    from frappe.permissions import add_permission, update_permission_property

    _ZEROED = ("read", "write", "create", "delete", "report", "export", "print", "email", "share")
    for role in _TM_PICKER_ROLES:
        if frappe.db.exists("Role", role):
            add_permission("TS Transport Master", role, ptype="select")
            for ptype in _ZEROED:
                update_permission_property("TS Transport Master", role, 0, ptype, 0)
    frappe.clear_cache(doctype="TS Transport Master")
