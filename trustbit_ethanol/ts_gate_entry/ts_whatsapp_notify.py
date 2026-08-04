# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration (Airtel IQ) — Phase 1b instant shim + variable recipes.
#
# Called ONCE at the end of ts_po_approval._send_approval_notification (the hub
# that every PO/MR approval action funnels through). Maps the approval `action`
# to a template event_key, builds the template's variables from a per-event
# RECIPE (the Template Map row's "Variable Order" field), and fires a
# per-recipient WhatsApp via the adapter. Fail-soft (never raises into the
# approval path) and inert when the kill-switch is off (send_template no-ops).
#
# SELF-SERVICE: the "Variable Order" recipe is a comma/newline list of tokens,
# in the order matching the Airtel template's {{1}},{{2}},…. Admins add/remove
# tokens to change the variable count, or change a token to change its content —
# all from the TS WhatsApp Settings UI, no code change. A blank recipe falls
# back to the default 5 so existing/unconfigured rows keep working.
#
# The resolver is READ-ONLY: tokens map to a fixed vocabulary, and `field:<f>`
# is restricted to actual non-Password docfields (no internal attrs, no eval).

import re

import frappe
from frappe import _
from frappe.utils import flt, formatdate, get_url_to_form

from trustbit_ethanol.ts_gate_entry.ts_whatsapp import send_template

# (doctype, _send_approval_notification action) -> template event_key.
ACTION_EVENT = {
	("Purchase Order", "pending_approval"): "po_needs_approval",
	("Purchase Order", "final_approved"): "po_approved",
	("Purchase Order", "rejected"): "po_rejected",
	("Material Request", "mr_pending"): "mr_needs_approval",
	("Material Request", "mr_approved"): "mr_approved",
	("Material Request", "mr_rejected"): "mr_rejected",
	("TS Item Creator", "item_pending"): "item_needs_approval",
	("TS Item Creator", "item_approved"): "item_approved",
	("TS Item Creator", "item_rejected"): "item_rejected",
	("TS Cascade Delete Log", "cascade_pending"): "cascade_needs_approval",
	("TS Cascade Delete Log", "cascade_executed"): "cascade_executed",
	("TS Cascade Delete Log", "cascade_rejected"): "cascade_rejected",
}

# Human-readable label for the doc_type token / {{2}}.
DOCTYPE_LABEL = {
	"Purchase Order": "Purchase Order",
	"Material Request": "Material Request",
	"TS Item Creator": "Item Request",
	"TS Cascade Delete Log": "Cascade Delete",
}

# Which field holds the approval status, per doctype (the status token).
_STATUS_FIELD = {
	"Purchase Order": "ts_approval_status",
	"Material Request": "ts_mr_status",
	"TS Item Creator": "status",
	"TS Cascade Delete Log": "approval_status",
}

# Used when a Template Map row's recipe is blank (keeps unconfigured rows working).
_DEFAULT_RECIPE = "approver, doc_type, doc_no, status, raised_by"
_MAX_VARS = 20
_MAX_LEN = 300
# field:<f> is restricted to scalar docfields — never secrets, child tables,
# layout, or blob/code fieldtypes (which would ship an unreadable repr).
_UNSAFE_FIELDTYPES = {
	"Password", "Table", "Table MultiSelect", "Section Break", "Column Break",
	"Tab Break", "HTML", "Code", "JSON", "Signature", "Geolocation", "Image",
}


def whatsapp_notify_for_approval(doc, action, recipients, extra=None):
	"""Fire instant WhatsApp templates for an approval event. Fail-soft."""
	try:
		event_key = ACTION_EVENT.get((doc.doctype, action))
		if not event_key or not recipients:
			return
		recipe = _get_recipe(event_key) or _DEFAULT_RECIPE

		seen = set()
		for user in recipients:
			if not user or user in ("Administrator", "Guest") or user in seen:
				continue
			seen.add(user)
			name = frappe.db.get_value("User", user, "full_name") or user
			variables = _resolve_recipe(recipe, doc, name, extra)
			# `user` (an email) is resolved to a WhatsApp number + opt-in by the
			# adapter; kill-switch / sandbox / opt-out gating all happen there.
			send_template(
				user, event_key, variables,
				ref_doctype=doc.doctype, ref_name=doc.name,
			)
	except Exception as e:
		try:
			frappe.log_error(
				title="WhatsApp: approval notify failed"[:140],
				message=f"{getattr(doc, 'name', '?')} / {action}: {e}",
			)
		except Exception:
			pass
		frappe.clear_messages()


# --------------------------------------------------------------------------- #
# Variable recipe resolver (self-service, read-only)
# --------------------------------------------------------------------------- #
def _get_recipe(event_key):
	"""The 'Variable Order' recipe configured on this event's enabled Template
	Map row (empty string if none / not enabled)."""
	return (
		frappe.db.get_value(
			"TS WhatsApp Template Map",
			{"parenttype": "TS WhatsApp Settings", "event_key": event_key, "enabled": 1},
			"variable_order",
		)
		or ""
	)


def _resolve_recipe(recipe, doc, recipient_name, extra):
	"""Comma/newline token recipe -> ordered list of WhatsApp variable strings.
	Each token resolves to a value from the document; unknown/failed tokens
	yield an empty string (fail-soft). Capped at _MAX_VARS / _MAX_LEN."""
	tokens = [t.strip() for t in re.split(r"[,\n]+", recipe or "") if t.strip()]
	out = []
	for tok in tokens[:_MAX_VARS]:
		try:
			out.append(_short(_resolve_token(tok, doc, recipient_name, extra)))
		except Exception:
			out.append("")
	return out


def _resolve_token(token, doc, recipient_name, extra):
	raw = token.strip()
	low = raw.lower()

	# field:/text: take a verbatim argument (which may contain spaces) — handle
	# these FIRST, before the named-token normalization below.
	if low.startswith("field:"):
		# Read-only access to an ACTUAL docfield only — never internal attrs,
		# methods, Password, or container/blob fieldtypes.
		fn = raw[6:].strip()
		if fn:
			meta = frappe.get_meta(doc.doctype)
			df = meta.get_field(fn) if meta.has_field(fn) else None
			if df and df.fieldtype not in _UNSAFE_FIELDTYPES:
				return doc.get(fn)
		return ""
	if low.startswith("text:"):
		return raw[5:].strip()

	# Named tokens — be forgiving: tolerate a leading "N=" / "N:" index prefix
	# (the old documentation style) and treat spaces/hyphens as underscores,
	# so "1=Doc No", "doc-no" and "doc_no" all resolve the same.
	key = re.sub(r"^\s*\d+\s*[=:]\s*", "", raw).strip().lower().replace(" ", "_").replace("-", "_")

	if key == "approver":
		return recipient_name
	if key in ("doc_type", "doctype", "type"):
		return DOCTYPE_LABEL.get(doc.doctype, doc.doctype)
	if key in ("doc_no", "docno", "no", "name"):
		return doc.name
	if key in ("url", "link", "doc_url", "doc_link"):
		# Direct desk link to the document (host from site config; name is
		# percent-encoded by get_url_to_form — some MR names contain a space).
		# WhatsApp renders it as a tappable link inside the template variable.
		# Both sites pin http_port 443 (L135, do not change server config) —
		# strip the redundant default port so the link reads clean.
		u = get_url_to_form(doc.doctype, doc.name)
		if u.startswith("https://") and ":443/" in u:
			# Both sites pin host_name + http_port "443" in site_config (L139),
			# so the FIRST ":443/" is deterministically the host:port boundary
			# and count=1 stops there. (quoted() passes "/" and ":" through —
			# do not rely on name encoding here.)
			u = u.replace(":443/", "/", 1)
		return u
	if key == "status":
		return doc.get(_STATUS_FIELD.get(doc.doctype, "status")) or "Pending Approval"
	if key in ("raised_by", "raisedby", "creator"):
		# requester (Item) / initiator (Cascade) / owner (PO·MR), in that order.
		u = doc.get("requested_by") or doc.get("initiated_by") or doc.owner
		return frappe.db.get_value("User", u, "full_name") or u or "ERP User"
	if key == "amount":
		return frappe.format_value(flt(doc.get("grand_total") or 0), {"fieldtype": "Currency"})
	if key in ("cost_center", "costcenter"):
		return doc.get("cost_center") or ""
	if key == "supplier":
		return doc.get("supplier_name") or doc.get("supplier") or ""
	if key in ("item_code", "itemcode"):
		return doc.get("generated_item_code") or doc.get("item_code") or ""
	if key in ("item_name", "itemname"):
		return doc.get("item_name") or ""
	if key == "token":
		return doc.get("target_token") or doc.get("token_number") or ""
	if key == "date":
		d = doc.get("transaction_date") or doc.get("schedule_date") or doc.get("creation")
		return formatdate(d) if d else ""
	if key == "company":
		return doc.get("company") or ""
	if key == "reason":
		ex = extra or {}
		return ex.get("reason") or ex.get("rejection_reason") or ex.get("comment") or "Not specified"
	# Unknown token -> empty (safe, keeps positional alignment).
	return ""


def _short(value):
	if value is None:
		return ""
	return str(value)[:_MAX_LEN]


# event_key -> the doctype a sample document is drawn from (for Preview).
_EVENT_DOCTYPE = {
	"po_needs_approval": "Purchase Order",
	"po_approved": "Purchase Order",
	"po_rejected": "Purchase Order",
	"mr_needs_approval": "Material Request",
	"mr_approved": "Material Request",
	"mr_rejected": "Material Request",
	"item_needs_approval": "TS Item Creator",
	"item_approved": "TS Item Creator",
	"item_rejected": "TS Item Creator",
	"cascade_needs_approval": "TS Cascade Delete Log",
	"cascade_executed": "TS Cascade Delete Log",
	"cascade_rejected": "TS Cascade Delete Log",
}


@frappe.whitelist()
def preview_template_messages():
	"""READ-ONLY preview for the TS WhatsApp Settings 'Preview Messages' button.

	For each ENABLED Template Map event, render its recipe against the most
	recent real document of that type and return the resulting {{1}}..{{N}}
	values — so admins can verify a recipe WITHOUT sending anything. No writes,
	no WhatsApp send. IT Head / System Manager only (mirrors the doctype perms).
	"""
	if not (set(frappe.get_roles()) & {"System Manager", "IT Head"}):
		frappe.throw(_("Not permitted — IT Head / System Manager only."), frappe.PermissionError)

	out = []
	rows = frappe.get_all(
		"TS WhatsApp Template Map",
		filters={"parenttype": "TS WhatsApp Settings", "enabled": 1},
		fields=["event_key", "template_id", "variable_order"],
		order_by="idx",
	)
	for r in rows:
		dt = _EVENT_DOCTYPE.get(r.event_key)
		recipe = r.variable_order or _DEFAULT_RECIPE
		sample = frappe.db.get_value(dt, {}, "name", order_by="creation desc") if dt else None
		variables = []
		if sample:
			try:
				doc = frappe.get_doc(dt, sample)
				variables = _resolve_recipe(recipe, doc, "(Approver's name)", {"reason": "(sample reason)"})
			except Exception:
				variables = []
		out.append({
			"event_key": r.event_key,
			"template_id": r.template_id or "(not set)",
			"doc_type": dt or "—",
			"sample_doc": sample or "(no document found)",
			"recipe": recipe,
			"variables": variables,
		})
	return out
