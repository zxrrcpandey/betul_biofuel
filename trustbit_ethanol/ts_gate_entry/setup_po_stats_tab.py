"""Purchase Order Stats tab + configurable threshold — v2.8.7.

Business need (CTO Rahul, 21 Apr 2026): bulk-RM POs (e.g. BBPL-PO-OT-2026-00013
with 78 deliveries) make the PO Lifecycle Tracker dominate the form. Move
the tracker into a dedicated "Stats" tab when the delivery count exceeds a
configurable threshold. Small POs (≤ threshold) keep the current inline view.

## What ships

1. `po_deliveries_stats_threshold` (Int) on TS Settings — default 5,
   permlevel=1 (IT Head / SM editable only).
2. `stats_tab` (Tab Break, label "Stats") on Purchase Order — appended
   at the END (after amended_from).
3. `po_stats_html` (HTML field) inside stats_tab — JS renders the stats
   summary + full Lifecycle Tracker here when threshold exceeded.

## Why not collapse tab visibility with depends_on

Frappe Tab Break visibility is not dynamically toggled by `depends_on`
in v15 as cleanly as a Section Break. Simpler: the Stats tab is ALWAYS
present, and po_approval.js decides what to put INSIDE it based on
count:
- count ≤ threshold: Stats tab shows "No stats — deliveries shown on
  main form." placeholder.
- count > threshold: Stats tab shows summary card + full tracker;
  main form shows compact badge.

## Idempotent

If rows already exist, UPDATE properties (label/placement) via
frappe.db.set_value. If missing, INSERT fresh.
"""

import frappe


def seed_po_stats_tab():
	"""Seed TS Settings threshold + PO Stats tab + HTML field. Idempotent."""
	_seed_threshold_setting()
	_seed_po_custom_fields()
	# v2.9.8.15 — ensure PO list view has the right column setup so the ID
	# column lands at position 2, not auto-appended at the end (Lesson 226).
	_seed_po_list_view_columns()
	frappe.clear_cache(doctype="TS Settings")
	frappe.clear_cache(doctype="Purchase Order")


def _seed_po_list_view_columns():
	"""PO list view column setup.

	v2.9.8.16 — Frappe v15's `setup_columns()` either auto-appends `name` at
	the END or omits it entirely. The column injection is therefore done
	client-side by po_list.js via the prototype monkey-patch on setup_columns
	(v2.9.8.22).

	v2.9.8.28 — also ensure `per_received` is part of the visible fields list
	(% Received progress bar). Idempotent: only adds the entry if absent;
	never removes admin-configured fields.

	Bumps tabList View Settings.total_fields to fit ID + Approval Status +
	Grand Total + % Received + injected name = 6 (Tag invisible).
	"""
	import json

	name = "Purchase Order"
	if not frappe.db.exists("List View Settings", name):
		return

	# 1. Ensure per_received is in the fields JSON (append if missing).
	raw = frappe.db.get_value("List View Settings", name, "fields")
	updated_fields_json = None
	if raw:
		try:
			fields = json.loads(raw)
		except Exception as e:
			frappe.log_error(
				message=f"_seed_po_list_view_columns: bad fields JSON: {e}",
				title="seed_po_stats_tab",
			)
			fields = None

		if isinstance(fields, list):
			existing = {(f or {}).get("fieldname") for f in fields}
			if "per_received" not in existing:
				fields.append({"fieldname": "per_received", "label": "% Received"})
				updated_fields_json = json.dumps(fields)

	# 2. total_fields target = 6 (visible 5 + Tag invisible). Adjust if list
	# already has more entries than 5 (e.g. prod has per_billed, transaction_date).
	target_total = "6"
	cur_total = frappe.db.get_value("List View Settings", name, "total_fields")

	updates = {}
	if updated_fields_json is not None:
		updates["fields"] = updated_fields_json
	if cur_total != target_total:
		updates["total_fields"] = target_total
	if updates:
		frappe.db.set_value("List View Settings", name, updates)


def _seed_threshold_setting():
	"""Add `po_deliveries_stats_threshold` Int field to TS Settings."""
	cf_name = "TS Settings-po_deliveries_stats_threshold"
	spec = {
		"doctype": "Custom Field",
		"dt": "TS Settings",
		"fieldname": "po_deliveries_stats_threshold",
		"label": "PO Stats Tab Threshold (Deliveries)",
		"fieldtype": "Int",
		"default": "5",
		"permlevel": 1,
		"insert_after": "ts_two_pass_gates_enabled",
		"description": "When a Purchase Order has MORE than this many deliveries (tokens), the PO Lifecycle Tracker moves into the Stats tab and a compact badge replaces the inline view on the main form. Default 5. Writable by IT Head / System Manager only (permlevel 1).",
	}
	if frappe.db.exists("Custom Field", cf_name):
		try:
			frappe.db.set_value(
				"Custom Field", cf_name,
				{
					"label": spec["label"],
					"default": spec["default"],
					"permlevel": spec["permlevel"],
					"insert_after": spec["insert_after"],
					"description": spec["description"],
				},
			)
		except Exception as e:
			frappe.log_error(message=f"seed_po_stats threshold update: {e}", title="seed_po_stats_tab")
	else:
		try:
			frappe.get_doc(spec).insert(
				ignore_if_duplicate=True, ignore_permissions=True
			)
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(message=f"seed_po_stats threshold insert: {e}", title="seed_po_stats_tab")


def _seed_po_custom_fields():
	"""Seed Purchase Order Stats tab (Tab Break) + po_stats_html (HTML)."""
	fields = [
		{
			"doctype": "Custom Field",
			"dt": "Purchase Order",
			"fieldname": "stats_tab",
			"label": "Stats",
			"fieldtype": "Tab Break",
			# v2.9.8.11 — was `amended_from` (idx 19, inside supplier_section). That
			# anchor split the native first tab so items_section + totals fell into
			# the Stats tab. `connections_tab` is the last native Tab Break (idx 154);
			# anchoring after it makes Stats the new last tab, AFTER Connections.
			"insert_after": "connections_tab",
			"description": "Purchase Order Stats tab — shows delivery summary + full Lifecycle Tracker when PO has more than the configured threshold (default 5) of deliveries.",
		},
		{
			"doctype": "Custom Field",
			"dt": "Purchase Order",
			"fieldname": "po_stats_html",
			"label": "",
			"fieldtype": "HTML",
			"insert_after": "stats_tab",
			"description": "Server-rendered-less container; po_approval.js populates this area with the stats summary + full delivery list.",
		},
	]
	for f in fields:
		cf_name = f"Purchase Order-{f['fieldname']}"
		if frappe.db.exists("Custom Field", cf_name):
			try:
				frappe.db.set_value(
					"Custom Field", cf_name,
					{
						"label": f["label"],
						"insert_after": f["insert_after"],
						"description": f["description"],
					},
				)
			except Exception as e:
				frappe.log_error(message=f"seed_po_stats CF update {cf_name}: {e}", title="seed_po_stats_tab")
		else:
			try:
				frappe.get_doc(f).insert(
					ignore_if_duplicate=True, ignore_permissions=True
				)
			except frappe.DuplicateEntryError:
				pass
			except Exception as e:
				frappe.log_error(message=f"seed_po_stats CF insert {cf_name}: {e}", title="seed_po_stats_tab")
