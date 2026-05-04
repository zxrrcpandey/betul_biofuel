"""v2.9.11 — Shared Connections panel Custom Fields.

Adds the Connections (🔗 Related) Tab Break + HTML field to:
  Material Request, Purchase Order, Purchase Receipt, Purchase Invoice

TS Quality Inspection has no Tab Break in its native fields[], so the
panel is added directly in `ts_quality_inspection.json` (a Section
Break + HTML, not a Custom Field).

TS Deduction Sheet already ships its own `ts_connections_html` field
in the doctype JSON (v2.9.10) — no Custom Field needed.

## Anchors (verified live 2 May 2026 against demo + prod)

The Tab Break is anchored AFTER the LAST native Tab Break of each
doctype. Per Lesson 225, anchoring on a non-Tab-Break field would
split native sections. Verified placements:

| Doctype          | Anchor             | Source  |
|------------------|--------------------|---------|
| Material Request | connections_tab    | NATIVE  |
| Purchase Order   | stats_tab          | CUSTOM (v2.8.7 — appears AFTER native connections_tab) |
| Purchase Receipt | connections_tab    | NATIVE  |
| Purchase Invoice | connections_tab    | NATIVE  |

This places the Related tab as the LAST tab in every form.

## Idempotent

UPDATE label/insert_after if the field already exists; INSERT fresh
otherwise. Safe to run on every migrate.
"""

import frappe


_TARGETS = [
	{
		"dt": "Material Request",
		"insert_after": "connections_tab",
	},
	{
		"dt": "Purchase Order",
		# v2.8.7 stats_tab is the LAST Tab Break (anchored AFTER the native
		# connections_tab). Putting Related AFTER stats_tab makes it the new
		# last tab.
		"insert_after": "stats_tab",
	},
	{
		"dt": "Purchase Receipt",
		"insert_after": "connections_tab",
	},
	{
		"dt": "Purchase Invoice",
		"insert_after": "connections_tab",
	},
]


def seed_connections_panel():
	"""Idempotent — adds ts_connections_tab + ts_connections_html on each
	target doctype, anchored after the doctype's last native Tab Break."""
	for t in _TARGETS:
		_seed_for_doctype(t["dt"], t["insert_after"])
		frappe.clear_cache(doctype=t["dt"])


def _seed_for_doctype(dt: str, anchor: str) -> None:
	tab_field = "ts_connections_tab"
	html_field = "ts_connections_html"

	tab_spec = {
		"doctype": "Custom Field",
		"dt": dt,
		"fieldname": tab_field,
		"label": "🔗 Related",
		"fieldtype": "Tab Break",
		"insert_after": anchor,
		"description": "Related Documents — cross-references to Gate Entry, MR, PO, PR, QI, PI, DS, Stock Entry, Token, and Tax Invoices.",
	}
	html_spec = {
		"doctype": "Custom Field",
		"dt": dt,
		"fieldname": html_field,
		"label": "",
		"fieldtype": "HTML",
		"insert_after": tab_field,
		"description": "Server-rendered card grid populated by ts_connections.js (v2.9.11).",
	}

	for f in (tab_spec, html_spec):
		cf_name = f"{dt}-{f['fieldname']}"
		if frappe.db.exists("Custom Field", cf_name):
			try:
				frappe.db.set_value(
					"Custom Field", cf_name,
					{
						"label": f["label"],
						"fieldtype": f["fieldtype"],
						"insert_after": f["insert_after"],
						"description": f["description"],
					},
				)
			except Exception as e:
				frappe.log_error(
					title="seed_connections_panel update",
					message=f"{cf_name}: {e}",
				)
		else:
			try:
				frappe.get_doc(f).insert(
					ignore_if_duplicate=True, ignore_permissions=True
				)
			except frappe.DuplicateEntryError:
				pass
			except Exception as e:
				frappe.log_error(
					title="seed_connections_panel insert",
					message=f"{cf_name}: {e}",
				)
