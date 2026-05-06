# v2.9.12 Sprint 1 — backfill amended MR rows that carry stale parent ts_mr_status.
#
# Pre-v2.9.12, Material Request had no on_amend hook, so amended drafts inherited
# the parent's ts_mr_status (e.g. "Approved", "Pending CEO", "Rejected"). This
# made `can_submit_for_approval` predicate fail (status not in eligible list)
# and the Submit-for-Approval button stayed hidden — user couldn't progress.
#
# This one-shot patch sets ts_mr_status="Not Submitted" on all amended drafts
# whose status is in the broken set. Idempotent — re-running finds 0 rows.
#
# update_modified=False: preserves audit trail (Lesson 162). Each row gets
# an "HC Backfill" comment for traceability.
import frappe


BROKEN_STATUSES = (
    "Approved",
    "Rejected",
    "Pending Dept. User",
    "Pending Dept. Head",
    "Pending AVP",
    "Pending CEO",
    "Pending GM",
    "Pending Stores Manager",
    "Revised",
    "On Hold",
)


def execute():
    """Idempotent backfill — finds + repairs amended MRs with stale ts_mr_status."""
    if frappe.flags.in_install or frappe.flags.in_test:
        return

    rows = frappe.db.sql(
        """SELECT name, ts_mr_status, amended_from
           FROM `tabMaterial Request`
           WHERE amended_from IS NOT NULL
             AND docstatus = 0
             AND ts_mr_status IN %(statuses)s""",
        {"statuses": BROKEN_STATUSES},
        as_dict=True,
    )

    if not rows:
        try:
            frappe.log_error(
                title="HC backfill v2.9.12",
                message="No amended MRs with stale ts_mr_status found. Patch is no-op.",
            )
        except Exception:
            pass
        return

    repaired = []
    for r in rows:
        try:
            frappe.db.set_value(
                "Material Request",
                r.name,
                "ts_mr_status",
                "Not Submitted",
                update_modified=False,
            )
            try:
                doc = frappe.get_doc("Material Request", r.name)
                doc.add_comment(
                    "Comment",
                    text=(
                        f"v2.9.12 HC backfill: ts_mr_status reset "
                        f"from '{r.ts_mr_status}' to 'Not Submitted' "
                        f"(amended_from={r.amended_from}). "
                        f"Submit-for-Approval button now visible."
                    ),
                )
            except Exception:
                pass
            repaired.append(r.name)
        except Exception as e:
            try:
                frappe.log_error(
                    title="HC backfill row error",
                    message=f"name={r.name} err={str(e)[:200]}",
                )
            except Exception:
                pass

    frappe.db.commit()

    try:
        frappe.log_error(
            title="HC backfill v2.9.12 done",
            message=(
                f"Repaired {len(repaired)} amended MRs. "
                f"Sample: {', '.join(repaired[:5])}"
            ),
        )
    except Exception:
        pass
