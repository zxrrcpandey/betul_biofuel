"""Purchase Invoice LR Number + LR Date — v2.8.4 Custom Field seed.

Business need (CTO Rahul, 21 Apr 2026): capture the transporter's Lorry Receipt
number and date on the Purchase Invoice itself so the supplier's printed invoice
carries the LR reference.

Why Custom Fields (not JSON edit) — Purchase Invoice is a standard ERPNext doctype,
so we MUST use Custom Fields. Field names `lr_no` + `lr_date` deliberately match
the NATIVE ERPNext fields on Purchase Receipt so `make_purchase_invoice()` mapper
auto-carries the values on PI creation from PR (Lesson 183).

Why isolated from setup.py — setup.py holds MR_FULL / PO_FULL locked seed functions.
Per prior lessons we never bundle new seeds into setup.py.

Idempotent via `ignore_if_duplicate=True` and caught DuplicateEntryError.
"""

import frappe


def seed_pi_lr_fields():
	"""Seed/update lr_no + lr_date Custom Fields on Purchase Invoice.

	Background: ERPNext's GST India module ALREADY ships these Custom Fields
	on Purchase Invoice (module='GST India') but with labels "Transport Receipt
	No / Date" and print_hide=1. BBF needs them visible on PI print AND labeled
	"LR Number / LR Date" per CTO Rahul's request.

	Strategy: if field already exists (typical on prod with GST India installed),
	UPDATE its label + print_hide + in_standard_filter to BBF's spec. If field
	does not exist (rare — only on non-GST sites), INSERT fresh.

	Placement: insert_after=bill_date (v2.8.0.2 field) so transporter-reference
	fields sit together in the Details tab. Keeps PI form layout consistent
	with PR form.

	Field names match Purchase Receipt's native `lr_no` + `lr_date` so ERPNext's
	make_purchase_invoice() mapper auto-carries values from PR to PI (Lesson 183).
	"""
	spec = {
		"lr_no": {
			"label": "LR Number",
			"fieldtype": "Data",
			"insert_after": "bill_date",
			"in_standard_filter": 1,
			"print_hide": 0,
			"description": "Lorry Receipt number from transporter. Auto-carried from Purchase Receipt.lr_no when PI is created from a PR (ERPNext mapper matches by fieldname per Lesson 183). Editable if correction needed.",
		},
		"lr_date": {
			"label": "LR Date",
			"fieldtype": "Date",
			"insert_after": "lr_no",
			"in_standard_filter": 0,
			"print_hide": 0,
			"description": "Lorry Receipt date. Auto-carried from Purchase Receipt.lr_date on PI creation. Editable if correction needed.",
		},
	}

	for fieldname, props in spec.items():
		cf_name = f"Purchase Invoice-{fieldname}"
		existing = frappe.db.exists("Custom Field", cf_name)
		if existing:
			# Field already exists (likely from GST India module) — overwrite
			# label + print_hide + insert_after + filter to BBF's spec. Does
			# NOT touch the `module` column so GST India's ownership marker
			# is preserved.
			frappe.db.set_value(
				"Custom Field", cf_name,
				{
					"label": props["label"],
					"insert_after": props["insert_after"],
					"in_standard_filter": props["in_standard_filter"],
					"print_hide": props["print_hide"],
					"description": props["description"],
				},
			)
			frappe.logger().info(
				f"[seed_pi_lr] Updated {cf_name} label='{props['label']}' "
				f"print_hide={props['print_hide']} insert_after={props['insert_after']}"
			)
		else:
			# No existing field — insert from scratch.
			try:
				payload = {"doctype": "Custom Field", "dt": "Purchase Invoice",
						   "fieldname": fieldname}
				payload.update(props)
				frappe.get_doc(payload).insert(
					ignore_if_duplicate=True, ignore_permissions=True
				)
				frappe.logger().info(
					f"[seed_pi_lr] Created new Custom Field {cf_name}"
				)
			except frappe.DuplicateEntryError:
				pass
			except Exception as e:
				frappe.log_error(
					message=f"seed_pi_lr insert failure on {cf_name}: {e}",
					title="seed_pi_lr_fields",
				)

	frappe.clear_cache(doctype="Purchase Invoice")
