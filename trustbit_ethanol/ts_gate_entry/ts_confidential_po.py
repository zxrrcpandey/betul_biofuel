"""Maize Purchase Order row-level confidentiality (BBF policy, Jun 2026).

A document carrying `ts_confidential = 1` is hidden from EVERY user except a
per-doctype allow-list. The flag is the authoritative marker:
  * Purchase Order  — auto-set when the PO header cost center is the maize
    raw-material cost center `RM MAIZE/RICE/DORB - BBPL`; also manually tickable
    by an allow-list role; tamper-guarded against everyone else (Lesson 162).
  * Material Request — same cost-center auto-set (future-proof; no maize MRs today).
  * Purchase Receipt / Purchase Invoice — DERIVED: set to 1 (read-only) when ANY
    linked Purchase Order is confidential (propagated on before_save).

Enforcement:
  * `permission_query_conditions` (per doctype) — filters list views, reports that
    go through the ORM, and Link-field dropdowns.
  * `has_permission` (per doctype) — gates single-document open / `frappe.client` reads.
  * `confidential_sql_clause()` — for raw-SQL read paths (dashboards/reports) that
    bypass BOTH hooks; each such path applies it explicitly.

Allow-list is data-driven: roles resolved live, plus the 3 store-team emails
(unavoidable literal — the store role is shared across departments, so the team
must be named by user) and the document owner (a creator always sees their own).
"""

import frappe
from frappe import _

MAIZE_COST_CENTER = "RM MAIZE/RICE/DORB - BBPL"
_FLAG = "ts_confidential"

# Platform-admin roles allowed on every confidential doctype.
_ADMIN_ROLES = {"System Manager", "Administrator", "Super Admin", "IT Head"}

# Per-doctype allow-list of ROLES (admin roles + document owner are always added).
_ALLOW_ROLES = {
    "Purchase Order": {"CEO", "MD", "Accounts User", "Accounts Manager", "Grain Purchase Manager"},
    "Purchase Receipt": {"CEO", "MD", "Accounts User", "Accounts Manager"},
    "Purchase Invoice": {"CEO", "MD", "Accounts User", "Accounts Manager"},
    "Material Request": {"CEO", "MD", "Accounts User", "Accounts Manager", "Grain Purchase Manager"},
}

# Per-doctype allow-list of explicit USER emails (the Store Team).
_STORE_TEAM = {
    "prateek.srivastav@betulbiofuel.com",
    "store@betulbiofuel.com",
    "akash.pawar@betulbiofuel.com",
}
_ALLOW_USERS = {
    "Purchase Order": _STORE_TEAM,        # read PO to build the GRN
    "Purchase Receipt": _STORE_TEAM,      # they create/receive the GRN
    "Purchase Invoice": set(),            # invoices are Accounts' domain (user decision)
    "Material Request": set(),
}


# --------------------------------------------------------------------------- #
#  Core decision                                                              #
# --------------------------------------------------------------------------- #
def _is_allowed(doctype, user=None):
    """True if `user` may see ALL confidential docs of `doctype` (allow-list / admin)."""
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    roles = set(frappe.get_roles(user))
    if roles & _ADMIN_ROLES:
        return True
    if roles & _ALLOW_ROLES.get(doctype, set()):
        return True
    if user in _ALLOW_USERS.get(doctype, set()):
        return True
    return False


def user_sees_confidential(doctype, user=None):
    """Public helper for raw-SQL leak plugs: True => no confidential filter needed."""
    return _is_allowed(doctype, user)


# --------------------------------------------------------------------------- #
#  permission_query_conditions providers (list / report / link-search)        #
# --------------------------------------------------------------------------- #
def _pqc(doctype, user):
    user = user or frappe.session.user
    if _is_allowed(doctype, user):
        return ""
    tbl = "`tab{0}`".format(doctype)
    esc = frappe.db.escape(user)
    # Hide confidential rows; a user always sees rows they own.
    return (
        "({tbl}.`{flag}` = 0 OR {tbl}.`{flag}` IS NULL OR {tbl}.`owner` = {esc})"
        .format(tbl=tbl, flag=_FLAG, esc=esc)
    )


def get_pqc_purchase_order(user=None):
    return _pqc("Purchase Order", user)


def get_pqc_purchase_receipt(user=None):
    return _pqc("Purchase Receipt", user)


def get_pqc_purchase_invoice(user=None):
    return _pqc("Purchase Invoice", user)


def get_pqc_material_request(user=None):
    return _pqc("Material Request", user)


# --------------------------------------------------------------------------- #
#  has_permission providers (single-doc open / frappe.client read)            #
# --------------------------------------------------------------------------- #
def _has(doctype, doc, user):
    user = user or frappe.session.user
    if not int(getattr(doc, _FLAG, 0) or 0):
        return True  # not confidential — no objection
    if _is_allowed(doctype, user):
        return True
    if getattr(doc, "owner", None) == user:
        return True
    return False  # confidential + not allowed => deny


def has_permission_purchase_order(doc, user=None, **kwargs):
    return _has("Purchase Order", doc, user)


def has_permission_purchase_receipt(doc, user=None, **kwargs):
    return _has("Purchase Receipt", doc, user)


def has_permission_purchase_invoice(doc, user=None, **kwargs):
    return _has("Purchase Invoice", doc, user)


def has_permission_material_request(doc, user=None, **kwargs):
    return _has("Material Request", doc, user)


# --------------------------------------------------------------------------- #
#  Raw-SQL leak-plug helper                                                    #
# --------------------------------------------------------------------------- #
def confidential_sql_clause(alias, user=None, doctype="Purchase Order"):
    """SQL fragment to AND into a raw query that the hooks cannot reach.

    Returns '' for an allow-listed user (sees everything), else a clause that
    hides confidential rows unless owned by the user. `alias` is the table alias
    used in the query (e.g. 'po', or the doctype table name).
    """
    user = user or frappe.session.user
    if _is_allowed(doctype, user):
        return ""
    esc = frappe.db.escape(user)
    return (
        " AND (`{a}`.`{flag}` = 0 OR `{a}`.`{flag}` IS NULL OR `{a}`.`owner` = {esc})"
        .format(a=alias, flag=_FLAG, esc=esc)
    )


# --------------------------------------------------------------------------- #
#  before_save: auto-set (PO / MR) + tamper-guard                             #
# --------------------------------------------------------------------------- #
def _stored_flag(doc):
    if doc.is_new():
        return 0
    return int(frappe.db.get_value(doc.doctype, doc.name, _FLAG) or 0)


def _doc_has_maize(doc):
    """True if the maize raw-material cost center appears on the PO/MR — the header
    `cost_center` OR any line-item `cost_center`. The header-only check missed POs
    that carry the maize CC only on a line item (audit 2 Jul 2026)."""
    if getattr(doc, "cost_center", None) == MAIZE_COST_CENTER:
        return True
    return any(
        getattr(row, "cost_center", None) == MAIZE_COST_CENTER
        for row in (doc.get("items") or [])
    )


def _is_production_transfer_mr(doc):
    """True for a Multiple-flow production transfer MR (tagged ts_production_run).

    D3 exemption (v2.21 dept-production rework, U6-approved 4 Jul 2026): these are
    Stores-release WORKFLOW documents, not procurement — the Store Manager MUST see
    them to release raw material, so the maize auto-flag must never hide them
    (auto-flagging one would silently stall the run: Stores Manager is deliberately
    NOT in the MR allow-list). NARROW by design — the rejected alternative (adding
    Stores Manager to the allow-list) would leak every confidential procurement MR.

    Defensive getattr: the ts_production_run tag field is seeded only where the
    production stack is migrated (ABSENT on prod's 2.18.x line), so this returns
    False there and maize MRs stay confidential — provable no-op on prod.
    The tamper-guard below stays fully active for these MRs (only the AUTO-SET is
    skipped): a manual flag flip still requires an allow-listed role."""
    return (
        doc.doctype == "Material Request"
        and getattr(doc, "material_request_type", "") == "Material Transfer"
        and bool(getattr(doc, "ts_production_run", None))
    )


def set_confidential_flag(doc, method=None):
    """PO / MR before_save: force-confidential for the maize cost center
    (fill-only, never auto-clears) and block unauthorized manual changes."""
    user = frappe.session.user
    old = _stored_flag(doc)
    # D3 (v2.21): production transfer MRs are never auto-flagged (see
    # _is_production_transfer_mr) — the tamper-guard below still applies to them.
    is_maize = _doc_has_maize(doc) and not _is_production_transfer_mr(doc)

    # 1) Auto-set: maize cost center forces confidential = 1 (never auto-clears).
    if is_maize and not int(getattr(doc, _FLAG, 0) or 0):
        doc.set(_FLAG, 1)

    new = int(getattr(doc, _FLAG, 0) or 0)

    # 2) Tamper-guard: only allow-list/admin may CHANGE the flag, except the
    #    system-driven maize auto-set (0 -> 1 on a maize-CC doc).
    if new == old:
        return
    if _is_allowed(doc.doctype, user):
        return
    if new == 1 and old == 0 and is_maize:
        return  # legitimate auto-set on a maize doc by a non-allowed creator
    doc.set(_FLAG, old)  # revert the unauthorized change
    frappe.throw(
        _("Only authorized roles can change the Confidential flag on {0}.").format(doc.doctype),
        frappe.PermissionError,
    )


def propagate_confidential_to_child(doc, method=None):
    """PR / PI before_save: mark confidential if ANY linked PO is confidential
    (read-only derived field; never auto-cleared)."""
    if int(getattr(doc, _FLAG, 0) or 0):
        return
    po_names = {
        r.get("purchase_order")
        for r in (doc.get("items") or [])
        if r.get("purchase_order")
    }
    for po in po_names:
        if int(frappe.db.get_value("Purchase Order", po, _FLAG) or 0):
            doc.set(_FLAG, 1)
            return


# --------------------------------------------------------------------------- #
#  Idempotent backfill (after_migrate) — self-heal docs that slipped          #
# --------------------------------------------------------------------------- #
def backfill_confidential_flags():
    """Flag any maize-involved PO/MR + their derived PR/PI that slipped the
    save-time auto-set. Called from `seed_confidential_fields()` every after_migrate.

    Catches the gap where a PO carries the maize cost center only on a LINE ITEM
    (header blank/other) so the header-only auto-set never fired, or a doc was
    submitted before the flag existed. Idempotent + fail-closed: only sets 1, never
    clears; `update_modified=False` so it never churns `modified` or trips a tamper
    guard (Lesson 297/303). PR/PI stay DERIVED from a linked confidential PO.
    Returns a per-doctype count of rows flipped.
    """
    flipped = {"Purchase Order": 0, "Material Request": 0,
               "Purchase Receipt": 0, "Purchase Invoice": 0}

    # 1) PO + MR — header OR any line-item on the maize cost center.
    for dt, child in (("Purchase Order", "Purchase Order Item"),
                      ("Material Request", "Material Request Item")):
        names = frappe.db.sql_list(
            """SELECT DISTINCT p.name FROM `tab{dt}` p
                 LEFT JOIN `tab{child}` c ON c.parent = p.name
                WHERE p.docstatus IN (0, 1)
                  AND COALESCE(p.`{flag}`, 0) = 0
                  AND (p.cost_center = %(cc)s OR c.cost_center = %(cc)s)
            """.format(dt=dt, child=child, flag=_FLAG),
            {"cc": MAIZE_COST_CENTER},
        )
        for name in names:
            frappe.db.set_value(dt, name, _FLAG, 1, update_modified=False)
        flipped[dt] = len(names)

    # 2) PR + PI — DERIVED: any linked Purchase Order is now confidential.
    for dt, child in (("Purchase Receipt", "Purchase Receipt Item"),
                      ("Purchase Invoice", "Purchase Invoice Item")):
        names = frappe.db.sql_list(
            """SELECT DISTINCT p.name FROM `tab{dt}` p
                 JOIN `tab{child}` c ON c.parent = p.name
                 JOIN `tabPurchase Order` po ON po.name = c.purchase_order
                WHERE p.docstatus IN (0, 1)
                  AND COALESCE(p.`{flag}`, 0) = 0
                  AND po.`{flag}` = 1
            """.format(dt=dt, child=child, flag=_FLAG),
        )
        for name in names:
            frappe.db.set_value(dt, name, _FLAG, 1, update_modified=False)
        flipped[dt] = len(names)

    if any(flipped.values()):
        frappe.db.commit()
    return flipped
