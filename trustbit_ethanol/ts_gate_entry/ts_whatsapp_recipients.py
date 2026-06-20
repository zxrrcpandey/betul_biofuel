# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration (Airtel IQ) — Phase 1a recipient resolution + consent.
#
# Resolves an approver/user list -> WhatsApp numbers (opt-in filtered), owns the
# User number/consent Custom Fields (seeded after_migrate), and provides a
# one-time bench-console bulk importer. Designed so Phase 2 external Contacts
# plug in via `_resolve_external` without changing `_whatsapp_recipients`.

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields as _create_custom_fields

WHATSAPP_USER_FIELDS = {
	"User": [
		{
			"fieldname": "ts_whatsapp_section",
			"fieldtype": "Section Break",
			"label": "WhatsApp",
			"insert_after": "mobile_no",
			"collapsible": 1,
		},
		{
			"fieldname": "ts_whatsapp_number",
			"fieldtype": "Data",
			"label": "WhatsApp Number",
			"insert_after": "ts_whatsapp_section",
			"description": "Country code + number, e.g. 91XXXXXXXXXX (no '+'). Distinct from Mobile No.",
		},
		{
			"fieldname": "ts_whatsapp_opt_in",
			"fieldtype": "Check",
			"label": "WhatsApp Opt-In",
			"insert_after": "ts_whatsapp_number",
			"default": "0",
			"description": "Must be ON for the user to receive any WhatsApp message.",
		},
		{
			"fieldname": "ts_whatsapp_opted_out_at",
			"fieldtype": "Datetime",
			"label": "WhatsApp Opted-Out At",
			"insert_after": "ts_whatsapp_opt_in",
			"read_only": 1,
			"description": "Set when the user opts out (or Meta reports opt-out). Non-empty = suppress all sends.",
		},
	]
}


def seed_whatsapp_user_fields():
	"""after_migrate: add the WhatsApp number/consent Custom Fields to User."""
	if not frappe.db.exists("DocType", "User"):
		return
	_create_custom_fields(WHATSAPP_USER_FIELDS, ignore_validate=True)


def normalize_msisdn(raw):
	"""Normalize a phone number to Airtel's 91XXXXXXXXXX form (digits only, no '+').
	Returns the normalized string, or None if it can't be made valid (caller
	then skips with reason 'bad_number', avoiding Airtel error -23)."""
	if not raw:
		return None
	digits = "".join(ch for ch in str(raw) if ch.isdigit())
	if not digits:
		return None
	if digits.startswith("00"):          # 00<cc><number>
		digits = digits[2:]
	if len(digits) == 10:                # bare Indian mobile
		return "91" + digits
	if len(digits) == 11 and digits.startswith("0"):  # 0XXXXXXXXXX
		return "91" + digits[1:]
	if len(digits) == 12 and digits.startswith("91"):  # already 91XXXXXXXXXX
		return digits
	if 11 <= len(digits) <= 15:          # other valid country-coded msisdn
		return digits
	return None


def _resolve_external(contact_or_supplier):
	"""Phase-2 stub: resolve an external Contact/Supplier to a WhatsApp number.
	Returns None in Phase 1 (no external sends)."""
	return None


def _whatsapp_recipients(users):
	"""Resolve a list of User names/emails to recipient dicts.

	Returns: [{"user", "number", "opted_in"}] for every ENABLED user requested.
	`number` may be None (caller logs 'no_number'); `opted_in` reflects opt-in
	AND not-opted-out. Disabled users are dropped entirely.
	"""
	if not users:
		return []
	if isinstance(users, str):
		users = [users]
	# De-dup while preserving order.
	seen = set()
	ordered = []
	for u in users:
		if u and u not in seen and u not in ("Administrator", "Guest"):
			seen.add(u)
			ordered.append(u)
	if not ordered:
		return []

	rows = frappe.get_all(
		"User",
		filters={"name": ["in", ordered], "enabled": 1},
		fields=[
			"name",
			"ts_whatsapp_number",
			"ts_whatsapp_opt_in",
			"ts_whatsapp_opted_out_at",
		],
	)
	by_name = {r["name"]: r for r in rows}

	out = []
	for u in ordered:
		r = by_name.get(u)
		if not r:
			continue  # disabled or non-existent
		number = normalize_msisdn(r.get("ts_whatsapp_number"))
		opted_in = bool(r.get("ts_whatsapp_opt_in")) and not r.get("ts_whatsapp_opted_out_at")
		out.append({"user": u, "number": number, "opted_in": opted_in})
	return out


def import_whatsapp_numbers(mapping, opt_in=True):
	"""One-time bench-console bulk importer.

	    from trustbit_ethanol.ts_gate_entry.ts_whatsapp_recipients import import_whatsapp_numbers
	    import_whatsapp_numbers({"user@bbpl.com": "9198765XXXXX", ...})

	Normalizes each number, sets User.ts_whatsapp_number, and (default) flips
	opt-in ON. Skips users that don't exist or numbers that won't normalize.
	Returns a summary dict.
	"""
	if not isinstance(mapping, dict):
		frappe.throw("import_whatsapp_numbers expects a dict of {user_email: number}")
	result = {"set": [], "skipped_no_user": [], "skipped_bad_number": []}
	for email, raw in mapping.items():
		if not frappe.db.exists("User", email):
			result["skipped_no_user"].append(email)
			continue
		number = normalize_msisdn(raw)
		if not number:
			result["skipped_bad_number"].append(email)
			continue
		updates = {"ts_whatsapp_number": number}
		if opt_in:
			updates["ts_whatsapp_opt_in"] = 1
			updates["ts_whatsapp_opted_out_at"] = None
		frappe.db.set_value("User", email, updates, update_modified=False)
		result["set"].append(email)
	frappe.db.commit()
	result["counts"] = {
		"set": len(result["set"]),
		"skipped_no_user": len(result["skipped_no_user"]),
		"skipped_bad_number": len(result["skipped_bad_number"]),
	}
	return result
