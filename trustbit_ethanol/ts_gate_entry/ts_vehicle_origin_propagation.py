"""v2.9.14 + v2.9.14.2 — Vehicle Origin propagation.

G1 (TS Gate Entry) is the source of truth. Two propagation paths:
1. G1 → G2 (TS Token): on TS Gate Entry on_update, ALWAYS sync vehicle_origin
   to the linked TS Token (referenced via gate_entry.token_number). v2.9.14.2:
   dropped the Token-empty-check; G1 and G2 are the same physical gate event,
   so any G1 correction must immediately reflect on G2.
2. PR → PI: on Purchase Invoice validate, if PI.vehicle_origin is empty AND
   any item has a linked Purchase Receipt with a non-empty vehicle_origin,
   copy the FIRST non-empty PR's vehicle_origin to the PI header.

PR fetches from G1 declaratively via fetch_from (see setup.py).
"""
import frappe


def propagate_to_token(doc, method=None):
    if not doc.get("vehicle_origin") or not doc.get("token_number"):
        return
    # v2.9.14.2 — G1 is source of truth; always sync to linked Token
    current = frappe.db.get_value("TS Token", doc.token_number, "vehicle_origin")
    if current != doc.vehicle_origin:
        frappe.db.set_value("TS Token", doc.token_number, "vehicle_origin", doc.vehicle_origin, update_modified=False)


def propagate_to_pi(doc, method=None):
    if doc.get("vehicle_origin"):
        return
    pr_names = sorted({(it.purchase_receipt or "").strip() for it in (doc.get("items") or [])} - {""})
    if not pr_names:
        return
    rows = frappe.get_all("Purchase Receipt", filters={"name": ["in", pr_names]}, fields=["vehicle_origin"])
    for r in rows:
        if (r.vehicle_origin or "").strip():
            doc.vehicle_origin = r.vehicle_origin
            return
