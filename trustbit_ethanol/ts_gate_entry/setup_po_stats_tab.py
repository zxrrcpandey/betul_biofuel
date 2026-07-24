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
	"""Seed TS Settings threshold + PO Stats tab + HTML field + PO/MR list
	view columns. Idempotent."""
	_seed_threshold_setting()
	_seed_po_custom_fields()
	# v2.9.8.15 — PO list view column setup (append-only field ensure).
	# v2.27.2 — the _seed_mr_list_view_columns() call is REMOVED: it force-
	# overwrote the Material Request List View Settings on EVERY migrate,
	# reverting admin "Pick Columns" choices (prod column-reset root cause).
	_seed_po_list_view_columns()
	frappe.clear_cache(doctype="TS Settings")
	frappe.clear_cache(doctype="Purchase Order")
	frappe.clear_cache(doctype="Material Request")


def _seed_mr_list_view_columns():
	"""v2.27.2 — UNUSED (call removed from seed_po_stats_tab; kept for
	reference only). Do NOT re-wire: MR list columns are admin-owned via the
	List View Settings row now.

	v2.9.8.30 — Material Request list view column setup.

	Mirrors the PO pattern (Lesson 228): hide_name_column=true via JS,
	prototype monkey-patch on setup_columns injects `name` at index 2.
	Server side SETS the exact fields JSON + total_fields=7 to deliver the
	user-requested visible order.

	Desired final visible order (after JS injects name at idx 2):
	  Title (Subject) | ID | Status | Required By | Approval Status | % Ordered | % Received

	NOT append-only here — we ENFORCE the exact spec because the user listed
	the columns explicitly. If admins customize via UI later, next migrate
	will revert; document this trade-off in deploy notes.
	"""
	import json

	name = "Material Request"
	if not frappe.db.exists("List View Settings", name):
		return

	desired_fields = [
		{"fieldname": "title", "label": "Title"},
		{"fieldname": "status_field", "label": "Status", "type": "Status"},
		{"fieldname": "schedule_date", "label": "Required By"},
		{"fieldname": "ts_mr_status", "label": "Approval Status"},
		{"fieldname": "per_ordered", "label": "% Ordered"},
		{"fieldname": "per_received", "label": "% Received"},
	]
	desired_fields_json = json.dumps(desired_fields)
	target_total = "7"

	cur_fields = frappe.db.get_value("List View Settings", name, "fields")
	cur_total = frappe.db.get_value("List View Settings", name, "total_fields")

	updates = {}
	if cur_fields != desired_fields_json:
		updates["fields"] = desired_fields_json
	if cur_total != target_total:
		updates["total_fields"] = target_total
	if updates:
		frappe.db.set_value("List View Settings", name, updates)


def _seed_po_list_view_columns():
	"""PO list view column setup.

	v2.9.8.16 — Frappe v15's `setup_columns()` either auto-appends `name` at
	the END or omits it entirely. The column injection is therefore done
	client-side by po_list.js via the prototype monkey-patch on setup_columns
	(v2.9.8.22).

	v2.9.8.28 — ensure `per_received` is part of the visible fields list.
	v2.9.8.29 — also ensure `per_billed` (% Billed). Both idempotent —
	only adds entries if absent; never removes admin-configured fields.

	v2.27.2 — the total_fields="6" force is REMOVED: it silently reverted the
	admin's "Pick Columns" max-columns choice on EVERY migrate (root cause of
	the prod "PO list columns keep resetting" issue). total_fields is
	admin-owned now; this seeder only APPENDS missing fields, never removes.
	"""
	import json

	name = "Purchase Order"
	if not frappe.db.exists("List View Settings", name):
		return

	# 1. Ensure per_received + per_billed are in the fields JSON (append if missing).
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
			changed = False
			if "per_received" not in existing:
				fields.append({"fieldname": "per_received", "label": "% Received"})
				changed = True
			if "per_billed" not in existing:
				fields.append({"fieldname": "per_billed", "label": "% Billed"})
				changed = True
			if changed:
				updated_fields_json = json.dumps(fields)

	# v2.27.2 — total_fields is deliberately NOT written here (admin-owned).
	if updated_fields_json is not None:
		frappe.db.set_value("List View Settings", name, {"fields": updated_fields_json})


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
