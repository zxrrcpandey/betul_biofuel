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


_DEPT_CARD_PREFIX = "Dept Pending — "


def _seed_dept_pending_cards():
	"""v2.21 dept-production gate: one 'Dept Pending — <category>' Number Card per
	ACTIVE reporting-only TS Production BOM Category (data-driven, L221 — a new
	department picks up its card on the next migrate). Counts the department's
	system-created 'Pending' gate entries. Cards for vanished/inactive categories
	are removed. Then the cards are appended to the EXISTING TS Production
	workspace (child row + content block — migrate never syncs these, L173).
	Idempotent."""
	if not frappe.db.exists("DocType", "TS Production Department Entry"):
		return
	if not frappe.db.exists("DocType", "TS Production BOM Category"):
		return
	cats = frappe.get_all("TS Production BOM Category",
	                      filters={"active": 1, "is_production": 0},
	                      pluck="name", order_by="name", limit=0)
	wanted = {_DEPT_CARD_PREFIX + c: c for c in cats}

	# create missing cards
	for card_name, cat in wanted.items():
		if frappe.db.exists("Number Card", card_name):
			continue
		doc = frappe.get_doc({
			"doctype": "Number Card",
			"label": card_name,  # autoname field:label -> name == match key
			"document_type": "TS Production Department Entry",
			"type": "Document Type", "function": "Count",
			"is_public": 1, "show_percentage_stats": 0,
			"module": MODULE, "color": "#f59e0b", "background_color": "#fef3c7",
			"filters_json": json.dumps([
				["TS Production Department Entry", "status", "=", "Pending"],
				["TS Production Department Entry", "category", "=", cat],
			]),
		})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("Number Card", card_name, "label",
		                    cat + " — Pending", update_modified=False)

	# drop cards whose category vanished or went inactive/production
	stale = frappe.get_all("Number Card",
	                       filters={"name": ["like", _DEPT_CARD_PREFIX + "%"]},
	                       pluck="name", limit=0)
	for card_name in stale:
		if card_name not in wanted:
			try:
				frappe.delete_doc("Number Card", card_name,
				                  force=1, ignore_permissions=True)
			except Exception:
				frappe.clear_messages()

	# attach to the existing TS Production workspace (create path is covered by
	# _seed_workspaces only for brand-new sites; existing ones need ORM append)
	if not frappe.db.exists("Workspace", "TS Production"):
		return
	ws = frappe.get_doc("Workspace", "TS Production")
	have = {(r.number_card_name or "") for r in (ws.number_cards or [])}
	try:
		content = json.loads(ws.content or "[]")
	except Exception:
		content = []
	block_names = {b.get("data", {}).get("number_card_name") for b in content
	               if b.get("type") == "number_card"}
	changed = False
	for card_name in wanted:
		if card_name not in have:
			# child row label == card NAME (the renderer's match key)
			ws.append("number_cards", {"number_card_name": card_name, "label": card_name})
			changed = True
		if card_name not in block_names:
			# insert dept cards right after the last number_card block so they
			# render with the existing KPI row, not below the shortcuts
			idx = max((i for i, b in enumerate(content)
			           if b.get("type") == "number_card"), default=-1)
			content.insert(idx + 1, {"id": "nc_" + card_name.replace(" ", "_"),
			                         "type": "number_card",
			                         "data": {"number_card_name": card_name, "col": 4}})
			changed = True
	# detach stale dept-card rows/blocks
	for r in list(ws.number_cards or []):
		nm = r.number_card_name or ""
		if nm.startswith(_DEPT_CARD_PREFIX) and nm not in wanted:
			ws.remove(r)
			changed = True
	new_content = [b for b in content
	               if not (b.get("type") == "number_card"
	                       and (b.get("data", {}).get("number_card_name") or "").startswith(_DEPT_CARD_PREFIX)
	                       and b.get("data", {}).get("number_card_name") not in wanted)]
	if len(new_content) != len(content):
		content = new_content
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
	_seed_dept_pending_cards()
