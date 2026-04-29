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
	"""v2.9.8.16 — PO list view column setup (Lesson 226).

	Frappe v15's `setup_columns()` either auto-appends `name` at the END
	(when `title_field` is set and `hide_name_column` is unset) or omits
	`name` entirely. Frappe's meta does NOT expose `name` as a regular
	DocField, so a Property Setter `in_list_view=1` is a no-op. The
	column injection is therefore done client-side by po_list.js via
	`frappe.listview_settings["Purchase Order"].onload`.

	Server side we just bump tabList View Settings.total_fields=5 so
	the slice in setup_columns doesn't drop Grand Total when our JS
	hook adds name at position 2.

	Idempotent — only writes when current state differs.
	"""
	if frappe.db.exists("List View Settings", "Purchase Order"):
		cur_total = frappe.db.get_value("List View Settings", "Purchase Order", "total_fields")
		if cur_total != "5":
			frappe.db.set_value("List View Settings", "Purchase Order", "total_fields", "5")


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
