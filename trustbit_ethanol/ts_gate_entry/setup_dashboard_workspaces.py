"""TS Production + OMC Supply workspaces + their KPI Number Cards.

Registered in hooks.py after_migrate (under the STOCK_RECON_PERMS unlock). Idempotent:
creates each Number Card / Workspace only if missing — never duplicates (same pattern
as setup.seed_number_cards). On a fresh site this builds the dashboards exactly as
verified on demo.

Number-card render note: Frappe's workspace renderer matches a content `number_card`
block's `number_card_name` against the workspace child row's `label` (blocks/block.js).
So each Workspace Number Card child row carries label == the card NAME (the match key),
while the card's own `label` holds the short display text shown on the tile.
"""

import json

import frappe

MODULE = "TS Gate Entry"

# name  = the match key (Number Card .name + content block number_card_name + child label)
# label = the short text shown on the tile
NUMBER_CARDS = [
	{"name": "Production Pending Release", "label": "Pending Release", "dt": "TS Production Entry",
	 "filters": [["TS Production Entry", "ts_variance_status", "=", "Pending Stores Release"]],
	 "color": "#f59e0b", "bg": "#fef3c7"},
	{"name": "Production Released", "label": "Released", "dt": "TS Production Entry",
	 "filters": [["TS Production Entry", "ts_variance_status", "=", "Released"]],
	 "color": "#3b82f6", "bg": "#dbeafe"},
	{"name": "Production Completed", "label": "Completed", "dt": "TS Production Entry",
	 "filters": [["TS Production Entry", "ts_variance_status", "=", "Completed"]],
	 "color": "#10b981", "bg": "#dcfce7"},
	{"name": "Production Draft", "label": "Draft", "dt": "TS Production Entry",
	 "filters": [["TS Production Entry", "ts_variance_status", "=", "Draft"]],
	 "color": "#6b7280", "bg": "#f3f4f6"},
	{"name": "Active OMC Targets", "label": "Active Targets", "dt": "TS OMC Supply Target",
	 "filters": [["TS OMC Supply Target", "is_active", "=", 1]],
	 "color": "#10b981", "bg": "#dcfce7"},
	{"name": "OMC Companies", "label": "OMCs", "dt": "TS OMC",
	 "filters": [], "color": "#6366f1", "bg": "#e0e7ff"},
	{"name": "Dispatch This Month", "label": "Dispatch (This Month)", "dt": "Delivery Note",
	 "filters": [["Delivery Note", "docstatus", "=", 1], ["Delivery Note", "posting_date", "Timespan", "this month"]],
	 "color": "#f97316", "bg": "#ffedd5"},
]

WORKSPACES = [
	{
		"name": "TS Production", "icon": "color-energy-points", "sequence_id": 99,
		"header": "Production Logging",
		"cards": ["Production Pending Release", "Production Released", "Production Completed", "Production Draft"],
		"shortcuts": [
			{"type": "Page", "link_to": "production-logging", "label": "Production Logging", "color": "Green"},
			{"type": "DocType", "link_to": "TS Production Entry", "label": "Production Logs", "color": "Blue"},
			{"type": "DocType", "link_to": "BOM", "label": "BOM", "color": "Cyan"},
			{"type": "DocType", "link_to": "Work Order", "label": "Work Order", "color": "Orange"},
			{"type": "DocType", "link_to": "Job Card", "label": "Job Card", "color": "Yellow"},
			{"type": "DocType", "link_to": "Stock Entry", "label": "Stock Entry", "color": "Grey"},
			# Multi-BOM flow (v2.20.x Phases B-D)
			{"type": "DocType", "link_to": "TS BOM Connector", "label": "BOM Connector", "color": "Purple"},
			{"type": "DocType", "link_to": "TS Production BOM Category", "label": "BOM Categories", "color": "Cyan"},
			{"type": "DocType", "link_to": "TS Production Department Entry", "label": "Dept Consumption", "color": "Purple"},
			{"type": "DocType", "link_to": "Material Request", "label": "Material Requests", "color": "Orange"},
		],
	},
	{
		"name": "OMC Supply", "icon": "solid-success", "sequence_id": 98,
		"header": "OMC Ethanol Supply Tracker",
		"cards": ["Active OMC Targets", "OMC Companies", "Dispatch This Month"],
		"shortcuts": [
			{"type": "Page", "link_to": "omc-supply-tracker", "label": "OMC Supply Tracker", "color": "Green"},
			{"type": "Page", "link_to": "omc-supply-tracker-wall", "label": "Wall Display", "color": "Purple"},
			{"type": "DocType", "link_to": "TS OMC Supply Target", "label": "OMC Supply Targets", "color": "Blue"},
			{"type": "DocType", "link_to": "TS OMC", "label": "OMC Master", "color": "Cyan"},
			{"type": "DocType", "link_to": "Delivery Note", "label": "Dispatch (Delivery Notes)", "color": "Orange"},
			{"type": "DocType", "link_to": "Customer", "label": "Customers (OMC tag)", "color": "Grey"},
		],
	},
]


def _seed_number_cards():
	"""Create each KPI Number Card if missing. name = match key, label = tile text."""
	for c in NUMBER_CARDS:
		if frappe.db.exists("Number Card", c["name"]):
			continue
		if not frappe.db.exists("DocType", c["dt"]):
			continue  # target doctype not on this site yet — skip
		doc = frappe.get_doc({
			"doctype": "Number Card",
			"label": c["name"],  # autoname field:label -> name == match key
			"document_type": c["dt"], "type": "Document Type", "function": "Count",
			"is_public": 1, "show_percentage_stats": 1, "stats_time_interval": "Daily",
			"module": MODULE, "color": c["color"], "background_color": c["bg"],
			"filters_json": json.dumps(c["filters"]),
		})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		# tile shows the short display label; .name stays the match key
		frappe.db.set_value("Number Card", c["name"], "label", c["label"], update_modified=False)


def _seed_workspaces():
	"""Create each workspace (with KPI cards + shortcut tiles) if missing."""
	for w in WORKSPACES:
		if frappe.db.exists("Workspace", w["name"]):
			continue
		content = [{"id": "nc_" + nm.replace(" ", "_"), "type": "number_card",
					"data": {"number_card_name": nm, "col": 4}} for nm in w["cards"]
				   if frappe.db.exists("Number Card", nm)]
		content.append({"id": "hdr_" + w["name"].replace(" ", "_"), "type": "header",
						"data": {"text": "<b>" + w["header"] + "</b>", "col": 12}})
		for s in w["shortcuts"]:
			content.append({"id": "sc_" + s["link_to"].replace(" ", "_"), "type": "shortcut",
							"data": {"shortcut_name": s["label"], "col": 4}})
		ws = frappe.get_doc({
			"doctype": "Workspace", "name": w["name"], "title": w["name"], "label": w["name"],
			"module": MODULE, "public": 1, "is_hidden": 0, "icon": w["icon"],
			"sequence_id": w["sequence_id"], "content": json.dumps(content),
			# child row label == card NAME (the renderer's match key)
			"number_cards": [{"number_card_name": nm, "label": nm} for nm in w["cards"]
							 if frappe.db.exists("Number Card", nm)],
			"shortcuts": [{"type": s["type"], "link_to": s["link_to"], "label": s["label"],
						   "color": s.get("color", "")} for s in w["shortcuts"]],
		})
		ws.flags.ignore_permissions = True
		ws.insert(ignore_permissions=True)


def _ensure_workspace_shortcuts():
	"""Append any shortcut from WORKSPACES that an EXISTING workspace is missing
	(child row + content block; matched by label OR link_to so nothing duplicates).
	Needed because _seed_workspaces only builds MISSING workspaces, and migrate
	never syncs shortcuts on existing ones (Lesson 173). Idempotent."""
	for w in WORKSPACES:
		if not frappe.db.exists("Workspace", w["name"]):
			continue
		ws = frappe.get_doc("Workspace", w["name"])
		have = {(s.label or "") for s in ws.shortcuts} | {(s.link_to or "") for s in ws.shortcuts}
		try:
			content = json.loads(ws.content or "[]")
		except Exception:
			content = []
		block_names = {b.get("data", {}).get("shortcut_name") for b in content
					   if b.get("type") == "shortcut"}
		changed = False
		for s in w["shortcuts"]:
			if s["label"] in have or s["link_to"] in have:
				continue
			if s["type"] == "DocType" and not frappe.db.exists("DocType", s["link_to"]):
				continue  # doctype not on this site yet — skip (cross-site safety)
			ws.append("shortcuts", {"type": s["type"], "link_to": s["link_to"],
									"label": s["label"], "color": s.get("color", "")})
			if s["label"] not in block_names:
				content.append({"id": "sc_" + s["link_to"].replace(" ", "_"),
								"type": "shortcut",
								"data": {"shortcut_name": s["label"], "col": 4}})
			changed = True
		if changed:
			ws.content = json.dumps(content)
			ws.flags.ignore_permissions = True
			ws.save()  # ORM save — migrate would NOT sync this (Lesson 173)


def seed_dashboard_workspaces():
	"""after_migrate entry point (register in hooks.py under unlock). Idempotent."""
	_seed_number_cards()
	_seed_workspaces()
	_ensure_workspace_shortcuts()
