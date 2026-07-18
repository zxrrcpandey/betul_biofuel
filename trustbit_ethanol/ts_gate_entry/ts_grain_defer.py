"""Grain PO deferred-linking (Option 2 — hold at exit).

Grain gate entries (ts_material_type in Maize/Rice/DORB, Raw-Material Stock IN)
skip PO linking at G2. The truck flows through weighment but is HELD at the G2
exit until one of the confidentiality allow-listed store users links the grain
Purchase Order and creates the GRN from the Stores Receiving Dashboard.

Everything here is ADDITIVE and gated by a kill-switch (ts_grain_defer_enabled
on TS Settings, default ON). Locked features are only imported + called, never
edited:
  - ts_confidential_po._STORE_TEAM / user_sees_confidential   (allow-list)
  - stores_receiving_api.create_grn_for_weighed_token          (GRN builder, reused)
  - api.get_purchase_orders                                    (confidential-safe PO search)

Confidentiality: notifications + Section G reveal vehicle / token / material /
net-weight ONLY — never supplier, PO number, or amount.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, cint, flt, escape_html

GRAIN_MATERIAL_TYPES = {"Maize", "Rice", "DORB"}
_KILL_SWITCH_FIELD = "ts_grain_defer_enabled"


@frappe.whitelist()
def grain_defer_enabled():
    """Lightweight getter for the TS Gate Entry form JS — operators lack TS
    Settings read permission (Lesson 168), so the form cannot read the Single
    directly. Returns 1/0."""
    return 1 if _feature_enabled() else 0


def _feature_enabled():
    """Kill-switch read — fail-OPEN default (1) so an unseeded field never
    silently disables the vehicle-hold. Returns bool."""
    try:
        val = frappe.db.get_single_value("TS Settings", _KILL_SWITCH_FIELD)
    except Exception:
        return True
    return True if val is None else bool(cint(val))


def is_grain_stock_in(material_type, material_flow, stock_direction):
    """Server-trusted grain discriminator. A gate entry is 'grain-deferred'
    when it is a Raw-Material Stock-IN carrying a grain material type AND the
    feature is enabled. Used by TS Gate Entry.validate() to stamp the marker."""
    return (
        _feature_enabled()
        and material_type in GRAIN_MATERIAL_TYPES
        and material_flow == "Raw Material"
        and (stock_direction or "Stock IN") == "Stock IN"
    )


def _deferred_gate_entry(token_name):
    """Return the submitted Gate Entry name for a token IF it is grain-deferred."""
    return frappe.db.get_value(
        "TS Gate Entry",
        {"token_number": token_name, "docstatus": 1, "ts_po_deferred": 1},
        "name",
    )


def exit_hold_active(token_doc):
    """G2-exit hold predicate. Held when the feature is on, the token's Gate
    Entry is grain-deferred, and no GRN exists yet (create_grn_for_weighed_token
    stamps token.purchase_receipt, which releases this hold). Toggling the
    kill-switch OFF releases all holds immediately."""
    if not _feature_enabled():
        return False
    if getattr(token_doc, "purchase_receipt", None):
        return False
    return bool(_deferred_gate_entry(token_doc.name))


# --------------------------------------------------------------------------- #
# Stores Receiving Dashboard — Section G (read)                               #
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def get_section_g():
    """Grain — Awaiting PO Link. Grain-deferred, Tare-Weighed tokens with no
    GRN. Confidentiality-safe: vehicle / token / material / net-weight only
    (there is no PO yet). Non-allow-listed users get an empty queue."""
    from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential
    if not user_sees_confidential("Purchase Order"):
        return []
    rows = frappe.db.sql(
        """
        SELECT t.name AS token, t.vehicle_number AS vehicle,
               ge.ts_material_type AS material, t.wb_tare_time AS tare_time,
               TIMESTAMPDIFF(MINUTE, t.wb_tare_time, NOW()) / 60.0 AS age_hours
        FROM `tabTS Token` t
        JOIN `tabTS Gate Entry` ge
          ON ge.token_number = t.name AND ge.docstatus = 1
        WHERE t.status = 'Tare Weighed'
          AND t.docstatus <> 2
          AND t.entry_type = 'Material'
          AND (t.purchase_receipt IS NULL OR t.purchase_receipt = '')
          AND ge.ts_po_deferred = 1
        ORDER BY t.wb_tare_time ASC
        LIMIT 200
        """,
        as_dict=True,
    )
    for r in rows:
        wb = frappe.db.get_value(
            "TS Weighbridge Log",
            {"token_number": r["token"], "docstatus": ["<", 2]},
            ["gross_weight", "tare_weight"],
            as_dict=True,
        )
        r["net_weight"] = int(flt(wb.gross_weight) - flt(wb.tare_weight)) if wb else 0
    return rows


@frappe.whitelist()
def get_grain_purchase_orders(po_id=None, po_date=None, **kwargs):
    """PO search for the Link-Grain-PO dialog — SCOPED to grain (the maize/rice/dorb
    cost center) so a non-grain PO (e.g. a Capex fixed-asset PO in 'Nos', which has
    no weighbridge conversion) can never be picked. Confidential + allow-list gated:
    returns [] for non-allow-listed users (these POs are all ts_confidential=1)."""
    from trustbit_ethanol.ts_gate_entry.ts_confidential_po import (
        user_sees_confidential, MAIZE_COST_CENTER,
    )
    if not user_sees_confidential("Purchase Order"):
        return []
    filters = {
        "docstatus": 1,
        "status": ["not in", ["Closed", "Cancelled", "Completed"]],
        "per_received": ["<", 100],
        "cost_center": MAIZE_COST_CENTER,
    }
    if po_id:
        filters["name"] = ["like", "%{0}%".format(po_id)]
    if po_date:
        filters["transaction_date"] = po_date
    return frappe.get_all(
        "Purchase Order",
        filters=filters,
        fields=["name", "supplier", "supplier_name", "transaction_date",
                "total_qty", "grand_total", "per_received"],
        order_by="transaction_date desc",
        limit=20,
    )


# --------------------------------------------------------------------------- #
# Link / Unlink (mutations)                                                   #
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def get_grain_po_receipt_preview(token_name, po_name):
    """For the Link-Grain-PO dialog: the received quantity SUGGESTED for each PO
    item (the weighbridge net weight converted into the item's UOM). The operator
    can edit these before creating the draft GRN (partial delivery). Confidential
    + grain-scoped + allow-list gated, same as the link itself."""
    from trustbit_ethanol.ts_gate_entry.ts_confidential_po import (
        user_sees_confidential, MAIZE_COST_CENTER,
    )
    if not user_sees_confidential("Purchase Order"):
        frappe.throw(_("You are not allowed to view this Purchase Order."))
    frappe.has_permission("Purchase Order", "read", doc=po_name, throw=True)
    from trustbit_ethanol.ts_gate_entry.stores_receiving_api import (
        _is_kg_uom, _get_uom_conversion_to_kg,
    )
    po = frappe.get_doc("Purchase Order", po_name)
    if po.cost_center != MAIZE_COST_CENTER:
        frappe.throw(_("Purchase Order {0} is not a grain PO.").format(po_name))

    net_kg = flt(frappe.db.get_value(
        "TS Weighbridge Log", {"token_number": token_name, "docstatus": ["<", 2]},
        "net_weight",
    ))
    total_ordered = sum(flt(it.qty) for it in po.items) or 1
    items = []
    for it in po.items:
        share = net_kg if len(po.items) == 1 else net_kg * (flt(it.qty) / total_ordered)
        if _is_kg_uom(it.uom or "Kg"):
            suggested = flt(share, 3)
        else:
            conv = _get_uom_conversion_to_kg(it.item_code, it.uom)
            suggested = flt(share / conv, 3) if conv and flt(conv) > 0 else 0
        items.append({
            "item_code": it.item_code,
            "item_name": it.item_name,
            "uom": it.uom or "Kg",
            "ordered_qty": flt(it.qty),
            "suggested_qty": suggested,
        })
    return {"net_kg": net_kg, "items": items}


@frappe.whitelist(methods=["POST"])
def link_grain_po_and_create_grn(token_name, po_name, received_qty=None):
    """Store-user action: link a confidential grain PO to a deferred token's
    Gate Entry and create the GRN, releasing the G2-exit hold.

    Never trusts the client (Lessons 224/294): re-verifies PO read permission
    (invokes the confidentiality has_permission hook), row-locks the token + GE
    (double-link race guard), and re-checks the deferred/empty state.
    """
    if not token_name or not po_name:
        frappe.throw(_("Token and Purchase Order are both required."))

    # Mutate-role gate up front — the same Stores/Accounts/admin gate the GRN
    # builder enforces, so a read-only allow-listed user (CEO/MD/Grain PM) gets
    # a clear error here instead of a confusing rollback deep in the builder.
    from trustbit_ethanol.ts_gate_entry.stores_receiving_api import (
        _check_mutate, create_grn_for_weighed_token, _is_kg_uom, _get_uom_conversion_to_kg,
    )
    _check_mutate()

    # Authz — confidential-PO read gate. For a maize-CC PO this passes only for
    # the allow-list (3 store emails + Grain PM + Accounts + CEO/MD + admins).
    frappe.has_permission("Purchase Order", "read", doc=po_name, throw=True)

    # Row-lock the token + its submitted Gate Entry.
    frappe.db.sql("SELECT name FROM `tabTS Token` WHERE name=%s FOR UPDATE", (token_name,))
    ge_name = frappe.db.get_value(
        "TS Gate Entry", {"token_number": token_name, "docstatus": 1}, "name"
    )
    if not ge_name:
        frappe.throw(_("No submitted Gate Entry found for token {0}.").format(token_name))
    frappe.db.sql("SELECT name FROM `tabTS Gate Entry` WHERE name=%s FOR UPDATE", (ge_name,))

    ge = frappe.get_doc("TS Gate Entry", ge_name)
    if not cint(ge.ts_po_deferred):
        frappe.throw(_("Gate Entry {0} is not a grain-deferred entry.").format(ge_name))
    if ge.po_list or ge.purchase_order:
        frappe.throw(_("A Purchase Order is already linked to Gate Entry {0}.").format(ge_name))

    token = frappe.get_doc("TS Token", token_name)
    if token.purchase_receipt:
        frappe.throw(_("Token {0} already has a Purchase Receipt.").format(token_name))
    if token.status != "Tare Weighed":
        frappe.throw(
            _("Token {0} must be 'Tare Weighed' to link a PO (currently '{1}').").format(
                token_name, token.status
            )
        )

    po = frappe.get_doc("Purchase Order", po_name)
    if po.docstatus != 1:
        frappe.throw(_("Purchase Order {0} is not submitted.").format(po_name))
    if flt(po.per_received) >= 100:
        frappe.throw(_("Purchase Order {0} is already 100% received.").format(po_name))
    # Only grain POs (maize/rice/dorb cost center) can be linked here — a non-grain
    # PO (e.g. a Capex fixed-asset PO in 'Nos') has no weighbridge conversion and would
    # fail deep in the GRN builder with a confusing "received quantity" error.
    from trustbit_ethanol.ts_gate_entry.ts_confidential_po import MAIZE_COST_CENTER
    if po.cost_center != MAIZE_COST_CENTER:
        frappe.throw(_(
            "Purchase Order {0} is not a grain PO (its cost center is {1}, not the "
            "Maize/Rice/DORB cost center). Only grain POs can be linked to a grain "
            "vehicle here."
        ).format(po_name, po.cost_center or "—"))

    # Populate the empty deferred GE from the PO (mirrors add_purchase_order).
    ge.append("po_list", {
        "purchase_order": po_name,
        "supplier_name": po.supplier_name,
        "grand_total": po.grand_total,
        "item_count": len(po.items),
    })
    for item in po.items:
        ge.append("po_items", {
            "item_code": item.item_code,
            "item_name": item.item_name,
            "ordered_qty": item.qty,
            "uom": item.uom,
            "purchase_order": po_name,
        })
    ge.purchase_order = po_name
    ge.supplier_name = po.supplier_name
    ge.flags.ignore_validate_update_after_submit = True
    ge.flags.ignore_permissions = True
    ge.save()

    # Backfill the QI so the Deduction Sheet late-PO heal chain works.
    try:
        qi = frappe.db.get_value(
            "TS Quality Inspection",
            {"gate_entry": ge_name, "docstatus": ["<", 2]},
            "name",
        )
        if qi:
            frappe.db.set_value(
                "TS Quality Inspection", qi,
                {"purchase_order": po_name, "party_name": po.supplier_name},
                update_modified=False,
            )
    except Exception:
        frappe.log_error(title="grain_defer QI backfill", message=frappe.get_traceback())
        frappe.clear_messages()

    # Received quantity per item, in the PO's UOM. If the operator entered values
    # in the dialog (partial delivery), honor those; otherwise auto-derive from the
    # weighbridge net weight. Either way it's passed as manual_qty so the builder's
    # non-Kg partial-receipt gate lets the line through (Kg lines always use the
    # weighbridge weight directly).
    if received_qty:
        if isinstance(received_qty, str):
            import json
            try:
                received_qty = json.loads(received_qty)
            except (ValueError, TypeError):
                frappe.throw(_("Invalid received-quantity payload."))
        manual_qty = {po_name: {
            k: flt(v) for k, v in (received_qty or {}).items() if flt(v) > 0
        }}
        if not manual_qty[po_name]:
            frappe.throw(_("Enter a received quantity greater than zero for at least one item."))
    else:
        # Grain POs are almost always in Quintal / MT, not Kg. The GRN builder skips
        # non-Kg lines unless a received qty is supplied, so convert the weighbridge
        # net weight (Kg) into each non-Kg line's PO UOM using the builder's OWN
        # conversion helper so the factor never diverges.
        net_kg = flt(frappe.db.get_value(
            "TS Weighbridge Log", {"token_number": token_name, "docstatus": ["<", 2]},
            "net_weight",
        ))
        manual_qty = {}
        non_kg_items = [it for it in po.items if not _is_kg_uom(it.uom or "Kg")]
        kg_items = [it for it in po.items if _is_kg_uom(it.uom or "Kg")]
        if non_kg_items and kg_items:
            # A single weighbridge weight cannot be cleanly split across mixed Kg and
            # non-Kg (Quintal/MT) lines. Fail loud rather than risk over-receipt.
            frappe.throw(_(
                "This grain Purchase Order mixes Kg and non-Kg units across its lines, "
                "which the deferred-link flow cannot split from one weighbridge weight. "
                "Receive it via the standard Stores dashboard instead."
            ))
        if net_kg > 0 and non_kg_items:
            total_ordered = sum(flt(it.qty) for it in non_kg_items)
            for it in non_kg_items:
                conv = _get_uom_conversion_to_kg(it.item_code, it.uom)
                if not conv or flt(conv) <= 0:
                    continue  # builder surfaces its specific "no conversion" error
                share_kg = net_kg if len(non_kg_items) == 1 else net_kg * (
                    flt(it.qty) / total_ordered if total_ordered else 1
                )
                manual_qty.setdefault(po_name, {})[it.item_code] = flt(share_kg / conv, 3)

    # Delegate GRN to the existing (locked-safe) builder — it re-locks the token,
    # re-checks status/PR, builds the draft PR from WB net weight, and stamps
    # token.purchase_receipt, which releases the exit hold.
    result = create_grn_for_weighed_token(token_name, manual_qty_per_po=manual_qty or None)

    ge.add_comment(
        "Comment",
        _("Grain PO {0} linked by {1}; GRN {2} created.").format(
            po_name, frappe.session.user, result.get("purchase_receipt")
        ),
    )
    return {
        "ok": True,
        "token": token_name,
        "purchase_order": po_name,
        "purchase_receipt": result.get("purchase_receipt"),
    }


@frappe.whitelist(methods=["POST"])
def unlink_grain_po(token_name):
    """Reverse a grain PO link while no Purchase Receipt exists (correction
    path — e.g. a wrong PO was linked). Same confidentiality gate as link."""
    if not token_name:
        frappe.throw(_("Token is required."))

    # Same mutate-role + confidentiality gate as link (a pending deferred GE has
    # no PO to permission-check, so gate the actor directly).
    from trustbit_ethanol.ts_gate_entry.stores_receiving_api import _check_mutate
    from trustbit_ethanol.ts_gate_entry.ts_confidential_po import user_sees_confidential
    _check_mutate()
    if not user_sees_confidential("Purchase Order"):
        frappe.throw(_("You are not permitted to manage grain Purchase Order links."),
                     frappe.PermissionError)

    ge_name = frappe.db.get_value(
        "TS Gate Entry", {"token_number": token_name, "docstatus": 1}, "name"
    )
    if not ge_name:
        frappe.throw(_("No submitted Gate Entry found for token {0}.").format(token_name))
    # Lock BEFORE reading, then re-load under lock (mirror link's ordering).
    frappe.db.sql("SELECT name FROM `tabTS Gate Entry` WHERE name=%s FOR UPDATE", (ge_name,))
    ge = frappe.get_doc("TS Gate Entry", ge_name)
    if ge.purchase_order:
        frappe.has_permission("Purchase Order", "read", doc=ge.purchase_order, throw=True)
    if not cint(ge.ts_po_deferred):
        frappe.throw(_("Gate Entry {0} is not a grain-deferred entry.").format(ge_name))

    pr = frappe.db.get_value(
        "Purchase Receipt", {"ts_token": token_name, "docstatus": ["<", 2]}, "name"
    )
    if pr:
        frappe.throw(
            _("Cannot unlink — Purchase Receipt {0} exists. Cancel it first.").format(pr)
        )

    ge.po_list = []
    ge.po_items = []
    ge.purchase_order = None
    ge.supplier_name = None
    ge.flags.ignore_validate_update_after_submit = True
    ge.flags.ignore_permissions = True
    ge.save()
    ge.add_comment("Comment", _("Grain PO unlinked by {0}.").format(frappe.session.user))
    return {"ok": True, "token": token_name}


# --------------------------------------------------------------------------- #
# Notifications (fail-soft, fire-once)                                         #
# --------------------------------------------------------------------------- #
def maybe_notify_grain_pending(token_name):
    """Notify the store team (bell primary + WhatsApp best-effort) once, when a
    grain-deferred truck reaches Tare Weighed. Fully fail-soft — NEVER raises,
    so weighment can never break (Lessons 238/276). Confidentiality-safe."""
    try:
        if not _feature_enabled():
            return
        token = frappe.db.get_value(
            "TS Token", token_name,
            ["name", "vehicle_number", "ts_grain_notified_at", "purchase_receipt"],
            as_dict=True,
        )
        if not token or token.ts_grain_notified_at:
            return
        ge = frappe.db.get_value(
            "TS Gate Entry",
            {"token_number": token_name, "docstatus": 1},
            ["ts_material_type", "ts_po_deferred", "purchase_order"],
            as_dict=True,
        )
        if not ge or not cint(ge.ts_po_deferred) or ge.purchase_order:
            return

        material = escape_html(ge.ts_material_type or "Grain")
        vehicle = escape_html(token.vehicle_number or token_name)
        wb = frappe.db.get_value(
            "TS Weighbridge Log",
            {"token_number": token_name, "docstatus": ["<", 2]},
            ["gross_weight", "tare_weight"],
            as_dict=True,
        )
        net_kg = int(flt(wb.gross_weight) - flt(wb.tare_weight)) if wb else 0

        from trustbit_ethanol.ts_gate_entry.ts_confidential_po import _STORE_TEAM
        recipients = list(_STORE_TEAM)

        subject = _("Grain truck {0} waiting for PO link — {1}").format(vehicle, token_name)
        body = _(
            "A grain truck completed weighment and is <b>blocked at the exit gate</b> "
            "until its Purchase Order is linked.<br><br>"
            "Vehicle: <b>{0}</b><br>Token: <b>{1}</b><br>Material: <b>{2}</b><br>"
            "Net Weight: <b>{3} Kg</b><br><br>"
            "Open the Stores Receiving Dashboard, link the grain PO and create the "
            "GRN to release the truck."
        ).format(vehicle, token_name, material, net_kg)

        # Bell (primary, guaranteed) — one Notification Log per user, isolated.
        for user in recipients:
            try:
                frappe.get_doc({
                    "doctype": "Notification Log",
                    "subject": subject,
                    "email_content": body,
                    "for_user": user,
                    "type": "Alert",
                    "document_type": "TS Token",
                    "document_name": token_name,
                    "from_user": frappe.session.user,
                }).insert(ignore_permissions=True)
            except Exception:
                frappe.clear_messages()

        # WhatsApp (best-effort) — manual positional variables, confidentiality-safe.
        try:
            from trustbit_ethanol.ts_gate_entry.ts_whatsapp import send_template
            send_template(
                recipients,
                "grain_po_pending_link",
                variables=[vehicle, token_name, material, "{0} Kg".format(net_kg)],
                ref_doctype="TS Token",
                ref_name=token_name,
            )
        except Exception:
            frappe.clear_messages()

        # Fire-once stamp.
        frappe.db.set_value(
            "TS Token", token_name, "ts_grain_notified_at", now_datetime(),
            update_modified=False,
        )
    except Exception:
        try:
            frappe.log_error(title="grain_defer notify", message=frappe.get_traceback())
            frappe.clear_messages()
        except Exception:
            pass
