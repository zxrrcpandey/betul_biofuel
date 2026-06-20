# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration (Airtel IQ) — Phase 1a adapter.
#
# The ONLY module that talks to Airtel. Public entry `send_template()` is
# fail-soft (never raises to the caller), gates on the kill-switch + sandbox +
# opt-out, writes a TS WhatsApp Log row per recipient, and background-enqueues
# the HTTP POST. `_deliver_template()` (the enqueued worker) is the only HTTP
# caller; it retries with backoff on the documented retryable codes only.
#
# Ships INERT: with TS Settings.ts_whatsapp_enabled = 0 (default), send_template
# returns immediately with zero footprint.

import re
import time

import frappe

from trustbit_ethanol.ts_gate_entry.ts_whatsapp_recipients import (
	normalize_msisdn,
	_whatsapp_recipients,
)
from trustbit_ethanol.ts_gate_entry.ts_whatsapp_providers import get_provider, DEFAULT_PROVIDER

# HTTP statuses worth retrying (provider-relayed transient codes are per-provider).
RETRYABLE_HTTP = {429, 500, 502, 503, 504}
HTTP_TIMEOUT = 30


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def send_template(to, template_key, variables=None, ref_doctype=None,
				  ref_name=None, lang=None, triggered_by=None):
	"""Send an approved WhatsApp template. Fail-soft — never raises.

	`to`  : a User email, a 91XXXXXXXXXX number, a recipient dict, or a list of any.
	Returns the list of created TS WhatsApp Log names (empty if the kill-switch
	is off or nothing was sendable).
	"""
	try:
		if not _kill_switch_on():
			return []  # inert when disabled — zero footprint
		recipients = _build_recipients(to)
		if not recipients:
			return []
		tmpl = _resolve_template(template_key)
		sandbox_on, test_numbers = _sandbox_state()
		var_json = frappe.as_json(variables or [])
		names = []
		for rec in recipients:
			name = _process_one(
				rec, template_key, tmpl, var_json, sandbox_on, test_numbers,
				ref_doctype, ref_name, lang, triggered_by,
			)
			if name:
				names.append(name)
		return names
	except Exception as e:
		_log_error("send_template failed", f"{template_key} {ref_doctype}/{ref_name}: {e}")
		frappe.clear_messages()
		return []


# --------------------------------------------------------------------------- #
# Per-recipient gating + log creation
# --------------------------------------------------------------------------- #
def _process_one(rec, template_key, tmpl, var_json, sandbox_on, test_numbers,
				 ref_doctype, ref_name, lang, triggered_by):
	number = normalize_msisdn(rec.get("number"))
	base = {
		"recipient_number": number,
		"recipient_user": rec.get("user"),
		"ref_doctype": ref_doctype,
		"ref_name": ref_name,
		"template_key": template_key,
		"language": lang or (tmpl or {}).get("language") or "en",
		"variables_sent": var_json,
		"triggered_by": triggered_by or frappe.session.user,
		"is_sandbox": 1 if sandbox_on else 0,
		"sent_at": frappe.utils.now_datetime(),
	}

	# Gates (each produces a Skipped log so the admin can see why).
	if not tmpl:
		return _create_log(base, status="Skipped", skip_reason="no_template")
	if not number:
		return _create_log(base, status="Skipped", skip_reason="bad_number")
	# Opt-out only enforced for resolved Users (direct numbers are admin/test sends).
	if rec.get("user") is not None and not rec.get("opted_in"):
		return _create_log(base, status="Skipped", skip_reason="opted_out")
	if sandbox_on and number not in test_numbers:
		return _create_log(base, status="Skipped", skip_reason="sandbox")

	# Sendable — queue it.
	base["template_id"] = tmpl["template_id"]
	log = _create_log(base, status="Queued")
	try:
		frappe.enqueue(
			"trustbit_ethanol.ts_gate_entry.ts_whatsapp._deliver_template",
			queue="short",
			timeout=120,
			log_name=log,
		)
	except Exception as e:
		_update_log(log, status="Failed", skip_reason="enqueue_failed")
		_log_error("enqueue failed", f"{log}: {e}")
	return log


# --------------------------------------------------------------------------- #
# Delivery worker — the ONLY HTTP caller
# --------------------------------------------------------------------------- #
def _deliver_template(log_name):
	"""Enqueued worker. Build the request via the active provider, POST, retry."""
	try:
		log = frappe.get_doc("TS WhatsApp Log", log_name)
	except Exception:
		return
	if log.status != "Queued":
		return  # idempotent — already processed

	conn = _conn_settings()
	provider = get_provider(conn["provider"])
	missing = [k for k in provider["required"] if not conn.get(k)]
	if missing:
		_update_log(log_name, status="Failed", skip_reason="no_credentials:" + ",".join(missing))
		return

	try:
		import requests
	except Exception:
		_update_log(log_name, status="Failed", skip_reason="requests_unavailable")
		return

	try:
		variables = frappe.parse_json(log.variables_sent) or []
	except Exception:
		variables = []
	ctx = {
		"to": log.recipient_number,
		"template_ref": log.template_id,
		"language": log.language,
		"variables": variables,
	}
	retryable_codes = provider.get("retryable_codes") or frozenset()

	max_retries = conn.get("max_retries", 3)
	for attempt in range(max_retries + 1):
		try:
			url, headers, payload = provider["build"](conn, ctx)
			resp = requests.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
			http = resp.status_code
			try:
				data = resp.json()
			except Exception:
				data = {}
			result = provider["parse"](http, data)
			if result.get("ok"):
				_update_log(
					log_name, status="Sent", retry_count=attempt,
					provider_message_id=result.get("message_id"),
					vendor_ack_id=result.get("vendor_ack"),
					status_updated_at=frappe.utils.now_datetime(),
				)
				return
			# Error path.
			code = result.get("error_code")
			retryable = (str(code) in retryable_codes) or (http in RETRYABLE_HTTP)
			if retryable and attempt < max_retries:
				time.sleep(min(2 ** attempt, 8))
				continue
			_update_log(
				log_name, status="Failed", retry_count=attempt,
				error_code=str(code) if code is not None else str(http),
				error_message=_short(result.get("error_message")) or f"HTTP {http}",
				status_updated_at=frappe.utils.now_datetime(),
			)
			return
		except Exception as e:
			# Network/transport error — retryable.
			if attempt < max_retries:
				time.sleep(min(2 ** attempt, 8))
				continue
			_update_log(
				log_name, status="Failed", retry_count=attempt,
				skip_reason="transport_error", error_message=_short(str(e)),
				status_updated_at=frappe.utils.now_datetime(),
			)
			return


# --------------------------------------------------------------------------- #
# Settings / template / sandbox helpers
# --------------------------------------------------------------------------- #
def _kill_switch_on():
	return bool(int(frappe.db.get_single_value("TS Settings", "ts_whatsapp_enabled") or 0))


def _conn_settings():
	token = None
	try:
		token = frappe.get_cached_doc("TS Settings").get_password(
			"ts_whatsapp_auth_token", raise_exception=False
		)
	except Exception:
		token = None
	gv = frappe.db.get_single_value
	# Honor an explicit 0 (operator means "never retry"); only fall back to 3 when unset.
	_mr = gv("TS Settings", "ts_whatsapp_max_retries")
	return {
		"provider": gv("TS Settings", "ts_whatsapp_provider") or DEFAULT_PROVIDER,
		# base_url is an OPTIONAL override; blank -> the provider's own default base.
		"base_url": gv("TS Settings", "ts_whatsapp_base_url") or "",
		"token": token,
		"vendor_uid": gv("TS Settings", "ts_whatsapp_vendor_uid"),
		"from_phone_number_id": gv("TS Settings", "ts_whatsapp_from_phone_number_id"),
		"app_id": gv("TS Settings", "ts_whatsapp_app_id"),
		"sender": gv("TS Settings", "ts_whatsapp_from"),
		"filter_blacklist": bool(int(gv("TS Settings", "ts_whatsapp_filter_blacklist") or 0)),
		"max_retries": int(_mr) if _mr not in (None, "") else 3,
	}


def _resolve_template(event_key):
	rows = frappe.get_all(
		"TS WhatsApp Template Map",
		filters={"parenttype": "TS Settings", "event_key": event_key, "enabled": 1},
		fields=["template_id", "language", "variable_order"],
		limit=1,
	)
	if not rows or not (rows[0].get("template_id") or "").strip():
		return None
	return {
		"template_id": rows[0]["template_id"].strip(),
		"language": rows[0].get("language") or "en",
		"variable_order": rows[0].get("variable_order"),
	}


def _sandbox_state():
	on = bool(int(frappe.db.get_single_value("TS Settings", "ts_whatsapp_sandbox_mode") or 0))
	raw = frappe.db.get_single_value("TS Settings", "ts_whatsapp_test_numbers") or ""
	nums = set()
	for tok in re.split(r"[,\s]+", raw):
		n = normalize_msisdn(tok)
		if n:
			nums.add(n)
	return on, nums


def _build_recipients(to):
	if to is None:
		return []
	items = to if isinstance(to, (list, tuple, set)) else [to]
	user_emails, resolved, direct = [], [], []
	for it in items:
		if isinstance(it, dict):
			resolved.append({
				"user": it.get("user"),
				"number": it.get("number"),
				"opted_in": it.get("opted_in", True),
			})
		elif isinstance(it, str) and "@" in it:
			user_emails.append(it)
		elif it:
			direct.append({"user": None, "number": str(it), "opted_in": True})
	if user_emails:
		resolved.extend(_whatsapp_recipients(user_emails))
	resolved.extend(direct)
	return resolved


# --------------------------------------------------------------------------- #
# Log read/write + response parsing
# --------------------------------------------------------------------------- #
def _create_log(base, status, skip_reason=None):
	doc = frappe.new_doc("TS WhatsApp Log")
	doc.update(base)
	doc.status = status
	if skip_reason:
		doc.skip_reason = skip_reason
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _update_log(log_name, **fields):
	fields = {k: v for k, v in fields.items() if v is not None or k in ("status",)}
	frappe.flags.in_whatsapp_server = True
	try:
		frappe.db.set_value("TS WhatsApp Log", log_name, fields, update_modified=False)
		frappe.db.commit()
	finally:
		frappe.flags.in_whatsapp_server = False


def _short(text, limit=900):
	if text is None:
		return None
	s = str(text)
	return s[:limit]


def _log_error(title, message):
	try:
		frappe.log_error(title=("WhatsApp: " + title)[:140], message=message)
	except Exception:
		pass
