"""v2.8.13 — "My Pending Approvals" seed.

Business need (CTO Rahul, 22 Apr 2026): Dept Heads, AVPs, CEOs, MDs
only see documents pending THEIR action. Default list view applies
user-role-scoped status filter; dashboard tiles expose pending counts
across MR/PO/Post-Dated/Budget Proposal.

## What ships

1. `ts_my_approvals_enabled` (Check) on TS Settings — default 1,
   permlevel=1 (IT Head / SM editable only). Kill switch.
2. Workspace "MR & PO Dashboard" nested under "BBPL Ethanol" parent.
   Contains HTML block that renders 4 live tiles via
   get_my_pending_counts API.

## Idempotent

- Custom Field: insert-or-update pattern (same as setup_po_stats_tab).
- Workspace: check exists before insert; update content on subsequent runs.
"""

import frappe


# v2.8.13 — native Frappe pattern: workspace uses Number Card + Shortcut
# blocks, matching existing dashboards (Management Dashboard, G1 Security Gate,
# Weighbridge, Stores). Custom Number Cards dispatch to whitelisted Python
# methods in ts_my_approvals_api that read frappe.session.user and return
# role-scoped counts.

NUMBER_CARDS = [
	{
		"name": "Pending MR for Me",
		"label": "Pending MR for Me",
		"document_type": "Material Request",
		"method": "trustbit_ethanol.ts_gate_entry.ts_my_approvals_api.count_mr_pending_for_me",
		"color": "#3b82f6",  # Blue — matches "Vehicles Inside Plant" card pattern
	},
	{
		"name": "Pending PO for Me",
		"label": "Pending PO for Me",
		"document_type": "Purchase Order",
		"method": "trustbit_ethanol.ts_gate_entry.ts_my_approvals_api.count_po_pending_for_me",
		"color": "#10b981",  # Green — matches "Vehicles Today" card
	},
	{
		"name": "Pending Post-Dated for Me",
		"label": "Pending Post-Dated for Me",
		"document_type": "TS Post Dated Entry Request",
		"method": "trustbit_ethanol.ts_gate_entry.ts_my_approvals_api.count_post_dated_pending_for_me",
		"color": "#a855f7",  # Purple — distinct, vibrant
	},
	{
		"name": "Pending Budget for Me",
		"label": "Pending Budget for Me",
		"document_type": "TS Budget Proposal",
		"method": "trustbit_ethanol.ts_gate_entry.ts_my_approvals_api.count_budget_pending_for_me",
		"color": "#ef4444",  # Red — matches "Pending Exit" card
	},
]


def seed_my_approvals():
	"""Seed kill-switch flag + 4 Number Cards + 'MR & PO Dashboard' workspace. Idempotent."""
	_seed_kill_switch_setting()
	_seed_number_cards()
	_seed_dashboard_workspace()
	frappe.clear_cache(doctype="TS Settings")


def _seed_kill_switch_setting():
	"""Add `ts_my_approvals_enabled` Check field to TS Settings."""
	cf_name = "TS Settings-ts_my_approvals_enabled"
	spec = {
		"doctype": "Custom Field",
		"dt": "TS Settings",
		"fieldname": "ts_my_approvals_enabled",
		"label": "Enable My Pending Approvals (List filter + Dashboard)",
		"fieldtype": "Check",
		"default": "1",
		"permlevel": 1,
		"insert_after": "po_deliveries_stats_threshold",
		"description": (
			"When ON, list views for Material Request, Purchase Order, "
			"TS Post Dated Entry Request, and TS Budget Proposal auto-apply "
			"a role-scoped status filter on page open (clearable). Also "
			"enables the 'MR & PO Dashboard' workspace tiles. Writable by "
			"IT Head / System Manager only (permlevel 1). Default ON."
		),
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
			frappe.log_error(
				message=f"seed_my_approvals kill-switch update: {e}",
				title="seed_my_approvals",
			)
	else:
		try:
			frappe.get_doc(spec).insert(
				ignore_if_duplicate=True, ignore_permissions=True
			)
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"seed_my_approvals kill-switch insert: {e}",
				title="seed_my_approvals",
			)

	# Set default=1 on existing Singles row if missing. fail-open pattern
	# from seed_ts_settings() — doesn't overwrite intentional OFF.
	rows = frappe.db.sql("""
		SELECT value FROM `tabSingles`
		WHERE doctype='TS Settings' AND field='ts_my_approvals_enabled' LIMIT 1
	""")
	if not rows:
		try:
			doc = frappe.get_doc("TS Settings")
			if hasattr(doc, "ts_my_approvals_enabled"):
				doc.set("ts_my_approvals_enabled", 1)
				doc.save(ignore_permissions=True)
				frappe.db.commit()
		except Exception as e:
			frappe.log_error(
				message=f"seed_my_approvals default set: {e}",
				title="seed_my_approvals",
			)


def _seed_number_cards():
	"""Create 4 Number Card records (type=Custom) dispatched to py count methods."""
	for card_spec in NUMBER_CARDS:
		name = card_spec["name"]
		fields = {
			"label": card_spec["label"],
			"type": "Custom",
			"document_type": card_spec["document_type"],
			"method": card_spec["method"],
			"color": card_spec["color"],
			"show_percentage_stats": 0,
			"is_public": 1,
			"module": "TS Gate Entry",
		}
		if frappe.db.exists("Number Card", name):
			try:
				doc = frappe.get_doc("Number Card", name)
				for k, v in fields.items():
					setattr(doc, k, v)
				doc.save(ignore_permissions=True)
			except Exception as e:
				frappe.log_error(
					message=f"seed_my_approvals number_card update {name}: {e}",
					title="seed_my_approvals",
				)
		else:
			try:
				frappe.get_doc({"doctype": "Number Card", "name": name, **fields}).insert(
					ignore_if_duplicate=True, ignore_permissions=True
				)
			except frappe.DuplicateEntryError:
				pass
			except Exception as e:
				frappe.log_error(
					message=f"seed_my_approvals number_card insert {name}: {e}",
					title="seed_my_approvals",
				)


def _seed_custom_html_block():
	"""Create Custom HTML Block 'TS My Approvals Dashboard' for workspace embed.

	This is the native v15 pattern — workspace `custom_block` type references
	a Custom HTML Block record which supplies html + script + style. Much more
	reliable than JS injection into layout-main-section.
	"""
	block_name = CUSTOM_HTML_BLOCK_NAME
	block_html = (
		'<div id="ts-ma-root" style="padding:4px 0 16px;">'
		'<div class="ts-ma-loading" style="color:var(--text-muted,#64748b);padding:10px;">Loading…</div>'
		'</div>'
	)
	# IMPORTANT — Frappe's create_shadow_element wraps the script body in
	# an IIFE and exposes `root_element` as the shadow root. Use
	# `root_element.querySelector(...)` NOT `$(selector)` because jQuery
	# would search the main document (not the shadow DOM) and fail silently.
	block_script = """
if (typeof frappe === 'undefined' || !frappe.call) return;
var rootEl = root_element.querySelector('#ts-ma-root');
if (!rootEl) return;
if (rootEl.dataset.tsMaRendered === '1') return;
rootEl.dataset.tsMaRendered = '1';

var esc = (frappe.utils && frappe.utils.escape_html) ? frappe.utils.escape_html : function(s){ return String(s||''); };
var bilingual = (typeof window.ts_bilingual === 'function') ? window.ts_bilingual : function(s){ return s; };

frappe.call({
    method: 'trustbit_ethanol.ts_gate_entry.ts_my_approvals_api.get_my_pending_counts',
    callback: function(r){
        if (!r || !r.message){
            rootEl.innerHTML = '<div style="color:#64748b;padding:10px;">'+esc(bilingual('No pending approvals'))+'</div>';
            return;
        }
        var items = r.message.items || [];
        if (!items.length){
            rootEl.innerHTML = '<div style="color:#64748b;padding:10px;">'+esc(bilingual('No pending approvals'))+'</div>';
            return;
        }
        var tiles = items.map(function(it){
            var count = parseInt(it.count, 10) || 0;
            var tooltip = (it.statuses || []).map(String).join(', ');
            var label = bilingual(String(it.label || ''));
            var accent = count > 0 ? '#1d4ed8' : '#94a3b8';
            var bg = count > 0 ? '#eff6ff' : '#f8fafc';
            return '<a class="ts-ma-tile" href="'+esc(String(it.list_route||''))+'" title="'+esc(tooltip)+'" aria-label="'+esc(label+' '+count+' '+bilingual('Pending'))+'" '
                + 'style="display:block;padding:16px 18px;border-radius:8px;background:'+bg+';border-left:4px solid '+accent+';text-decoration:none !important;color:inherit;box-shadow:0 1px 2px rgba(0,0,0,0.04);">'
                + '<div style="font-size:13px;color:#475569;margin-bottom:6px;">'+esc(label)+'</div>'
                + '<div style="font-size:28px;font-weight:700;color:'+accent+';line-height:1.1;">'+esc(String(count))+'</div>'
                + '<div style="font-size:11px;color:#64748b;margin-top:6px;">'+esc(bilingual('Pending'))+' &middot; '+esc(tooltip)+'</div>'
                + '</a>';
        }).join('');
        rootEl.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin-top:6px;">'+tiles+'</div>';
    },
    error: function(){
        rootEl.innerHTML = '<div style="color:#dc2626;padding:10px;">'+esc(bilingual('Error loading dashboard'))+'</div>';
    }
});
"""
	block_style = """
[data-theme="dark"] #ts-ma-root .ts-ma-tile { background: var(--bg-color, #18181b) !important; }
#ts-ma-root .ts-ma-tile:hover { transform: translateY(-1px); box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
"""

	if frappe.db.exists("Custom HTML Block", block_name):
		try:
			doc = frappe.get_doc("Custom HTML Block", block_name)
			doc.html = block_html
			doc.script = block_script
			doc.style = block_style
			doc.private = 0
			doc.save(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(
				message=f"seed_my_approvals custom_html_block update: {e}",
				title="seed_my_approvals",
			)
	else:
		try:
			frappe.get_doc({
				"doctype": "Custom HTML Block",
				"name": block_name,
				"html": block_html,
				"script": block_script,
				"style": block_style,
				"private": 0,
			}).insert(ignore_if_duplicate=True, ignore_permissions=True)
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"seed_my_approvals custom_html_block insert: {e}",
				title="seed_my_approvals",
			)


def _seed_dashboard_workspace():
	"""Create 'MR & PO Dashboard' workspace nested under 'BBPL Ethanol'.

	Native Frappe pattern — matches Management Dashboard / G1 / Weighbridge:
	header + 4 number_cards + spacer + header + shortcuts. Uses child
	tables (number_cards, shortcuts) to resolve content block references.
	"""
	ws_name = "MR & PO Dashboard"

	if not frappe.db.exists("Workspace", "BBPL Ethanol"):
		return

	# Build child-table rows (4 number_cards + 4 shortcuts)
	# Workspace Number Card child requires `number_card_name` (Link to Number Card).
	ws_number_cards = [
		{"number_card_name": c["name"], "label": c["label"]} for c in NUMBER_CARDS
	]
	# Workspace Shortcut requires `type` + `label`; color uses Frappe color names.
	# Colors match their paired Number Card so the visual pair is obvious.
	ws_shortcuts = [
		{"label": "All Pending MRs", "type": "DocType", "link_to": "Material Request", "color": "Blue"},
		{"label": "All Pending POs", "type": "DocType", "link_to": "Purchase Order", "color": "Green"},
		{"label": "Post-Dated Entry Requests", "type": "DocType", "link_to": "TS Post Dated Entry Request", "color": "Purple"},
		{"label": "Budget Proposals", "type": "DocType", "link_to": "TS Budget Proposal", "color": "Red"},
	]
	content_json = _build_workspace_content_json()

	if frappe.db.exists("Workspace", ws_name):
		try:
			doc = frappe.get_doc("Workspace", ws_name)
			doc.title = ws_name
			doc.parent_page = "BBPL Ethanol"
			doc.public = 1
			doc.is_hidden = 0
			doc.module = "TS Gate Entry"
			doc.content = content_json
			# Replace child tables
			doc.set("number_cards", ws_number_cards)
			doc.set("shortcuts", ws_shortcuts)
			doc.save(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(
				message=f"seed_my_approvals workspace update: {e}",
				title="seed_my_approvals",
			)
	else:
		try:
			frappe.get_doc({
				"doctype": "Workspace",
				"name": ws_name,
				"title": ws_name,
				"label": ws_name,
				"parent_page": "BBPL Ethanol",
				"public": 1,
				"is_hidden": 0,
				"module": "TS Gate Entry",
				"content": content_json,
				"sequence_id": 99.5,
				"icon": "organization",
				"number_cards": ws_number_cards,
				"shortcuts": ws_shortcuts,
			}).insert(ignore_if_duplicate=True, ignore_permissions=True)
		except frappe.DuplicateEntryError:
			pass
		except Exception as e:
			frappe.log_error(
				message=f"seed_my_approvals workspace insert: {e}",
				title="seed_my_approvals",
			)

	# Lesson 173 — bench migrate doesn't auto-sync workspace shortcuts/cards.
	try:
		frappe.clear_cache(doctype="Workspace")
	except Exception:
		pass


def _build_workspace_content_json():
	"""Return the workspace 'content' JSON string — native block pattern.

	Matches existing workspaces (Management Dashboard / G1 / Weighbridge):
	header + 4 number_cards (col=3 each) + spacer + header + 4 shortcuts
	(col=3 each). number_card blocks reference Number Card records by
	their `label` field (per Frappe convention — NOT by `name`).
	shortcut blocks reference the `shortcut_name` which matches the
	`label` of rows in Workspace.shortcuts child table.
	"""
	import json
	blocks = [
		{
			"type": "header",
			"data": {
				"text": '<span class="h4"><b>My Pending Approvals</b></span>',
				"col": 12,
			},
		},
	]
	for card in NUMBER_CARDS:
		blocks.append({
			"type": "number_card",
			"data": {"number_card_name": card["label"], "col": 3},
		})
	blocks.append({"type": "spacer", "data": {"col": 12}})
	blocks.append({
		"type": "header",
		"data": {"text": '<span class="h4"><b>Quick Links</b></span>', "col": 12},
	})
	for label in ["All Pending MRs", "All Pending POs", "Post-Dated Entry Requests", "Budget Proposals"]:
		blocks.append({
			"type": "shortcut",
			"data": {"shortcut_name": label, "col": 3},
		})
	return json.dumps(blocks)
