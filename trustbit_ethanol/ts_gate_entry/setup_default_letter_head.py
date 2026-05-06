# v2.9.11.2 — Default Letter Head seeder
# Idempotent: ensures a "BBPL" Letter Head record exists with is_default=1.
# On prod: no-op (BBPL already is_default=1 since 2026-04 manual setup).
# On demo: creates the record fresh (demo currently has only LH-Top).
#
# The record uses source="Image" with image=/files/BBPL-LH.png so the existing
# letterhead asset on prod (and any uploaded copy on demo) is referenced. If
# the image file is absent, Frappe still renders the wrapper (no broken layout).
#
# Lesson 232 note: Letter Head content here uses table-free <div> markup so
# wkhtmltopdf renders cleanly (no flexbox).
import frappe


BBPL_LETTER_HEAD_NAME = "BBPL"
BBPL_IMAGE_PATH = "/files/BBPL-LH.png"
BBPL_CONTENT = (
    '<div style="text-align: left;">'
    '<img src="/files/BBPL-LH.png" alt="BBPL">'
    "</div>"
)
BBPL_FOOTER = (
    '<div style="text-align: center;font-size:10px;">'
    '<p style="margin: 0;"><strong>'
    "Kindly dispatch your original documents to our office<br>"
    "Khasra No. 497/1, 497/2, 497/3, 5, Bodi Junawani, Betul (M.P.) 460001"
    "</strong></p></div>"
)


def seed_default_letter_head():
    """Idempotent. Ensures BBPL Letter Head exists and is the system default.

    - Creates record on demo (where it's missing).
    - Updates is_default flag if not already set (no-op on prod).
    - Clears is_default=1 on any other Letter Head so the unique-default
      invariant is preserved before flipping BBPL on (prevents Frappe's
      validate hook from silently demoting BBPL).
    """
    if frappe.flags.in_install or frappe.flags.in_test:
        return

    if frappe.db.exists("Letter Head", BBPL_LETTER_HEAD_NAME):
        _ensure_is_default(BBPL_LETTER_HEAD_NAME)
        return

    # Create on demo (or any site missing the record)
    doc = frappe.get_doc(
        {
            "doctype": "Letter Head",
            "letter_head_name": BBPL_LETTER_HEAD_NAME,
            "source": "Image",
            "align": "Left",
            "image": BBPL_IMAGE_PATH,
            "content": BBPL_CONTENT,
            "footer": BBPL_FOOTER,
            "disabled": 0,
        }
    )
    # after_migrate runs as Administrator; bypass needed for fresh-site seed
    doc.flags.ignore_permissions = True
    doc.insert()
    _ensure_is_default(BBPL_LETTER_HEAD_NAME)


def _ensure_is_default(name):
    """Atomically clear other defaults then set BBPL=1. Idempotent."""
    current = frappe.db.get_value("Letter Head", name, "is_default")
    if current:
        return
    # Clear any other is_default=1 first to avoid uniqueness conflict
    frappe.db.sql(
        "UPDATE `tabLetter Head` SET is_default = 0 WHERE name != %s AND is_default = 1",
        (name,),
    )
    frappe.db.set_value("Letter Head", name, "is_default", 1, update_modified=False)
    frappe.db.commit()
