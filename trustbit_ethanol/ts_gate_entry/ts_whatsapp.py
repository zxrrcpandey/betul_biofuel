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

# Meta/Airtel codes worth retrying (transient). Everything else fails fast.
RETRYABLE_CODES = {"130429", "131000", "100", "400", "-8"}
# HTTP statuses worth retrying.
RETRYABLE_HTTP = {429, 500, 502, 503, 504}
DEFAULT_BASE_URL = "https://iqwhatsapp.airtel.in:443/gateway/airtel-xchange/basic/whatsapp-manager/v1"
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
	"""Enqueued worker. POST the template to Airtel, retry retryable codes."""
	try:
		log = frappe.get_doc("TS WhatsApp Log", log_name)
	except Exception:
		return
	if log.status != "Queued":
		return  # idempotent — already processed

	conn = _conn_settings()
	missing = [k for k in ("base_url", "app_id", "token", "sender") if not conn.get(k)]
	if missing:
		_update_log(log_name, status="Failed", skip_reason="no_credentials:" + ",".join(missing))
		return

	try:
		import requests
	except Exception:
		_update_log(log_name, status="Failed", skip_reason="requests_unavailable")
		return

	url = conn["base_url"].rstrip("/") + "/template/send"
	headers = {
		"app-id": conn["app_id"],
		"Content-Type": "application/json",
		"Authorization": "Basic " + conn["token"],
	}
	try:
		variables = frappe.parse_json(log.variables_sent) or []
	except Exception:
		variables = []
	payload = {
		"templateId": log.template_id,
		"to": log.recipient_number,
		"from": conn["sender"],
		"filterBlacklistNumbers": bool(conn.get("filter_blacklist")),
		"message": {"variables": [str(v) for v in variables]},
	}

	max_retries = conn.get("max_retries", 3)
	for attempt in range(max_retries + 1):
		try:
			resp = requests.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
			http = resp.status_code
			try:
				data = resp.json()
			except Exception:
				data = {}
			code, msg = _extract_error(data)
			if 200 <= http < 300 and not code:
				mid, ack = _extract_ids(data)
				_update_log(
					log_name, status="Sent", retry_count=attempt,
					provider_message_id=mid, vendor_ack_id=ack,
					status_updated_at=frappe.utils.now_datetime(),
				)
				return
			# Error path.
			retryable = (str(code) in RETRYABLE_CODES) or (http in RETRYABLE_HTTP)
			if retryable and attempt < max_retries:
				time.sleep(min(2 ** attempt, 8))
				continue
			_update_log(
				log_name, status="Failed", retry_count=attempt,
				error_code=str(code) if code is not None else str(http),
				error_message=_short(msg) or f"HTTP {http}",
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
		"base_url": (gv("TS Settings", "ts_whatsapp_base_url") or DEFAULT_BASE_URL),
		"app_id": gv("TS Settings", "ts_whatsapp_app_id"),
		"token": token,
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


def _extract_error(data):
	"""Return (code, message) from an Airtel/Meta response, or (None, None)."""
	if not isinstance(data, dict):
		return None, None
	err = data.get("error")
	if isinstance(err, dict):
		code = err.get("code")
		msg = err.get("message")
		if isinstance(msg, dict):  # nested Airtel wrapper: error.message.error.{code,message}
			inner = msg.get("error", {})
			if isinstance(inner, dict):
				code = inner.get("code", code)
				msg = inner.get("message", None)
		return (str(code) if code is not None else None), msg
	# Top-level Airtel-specific failure (e.g. code -8) with a non-success code.
	code = data.get("code")
	if code not in (None, 0, "0", 200, "200"):
		return str(code), data.get("title") or data.get("message")
	# messageStatus / msgStatus FAILED without a structured code.
	status = (data.get("messageStatus") or data.get("msgStatus") or "").upper()
	if status == "FAILED":
		return "FAILED", data.get("title") or "Send reported FAILED"
	return None, None


def _extract_ids(data):
	if not isinstance(data, dict):
		return None, None
	inner = data.get("data") if isinstance(data.get("data"), dict) else {}
	mid = data.get("messageId") or inner.get("messageId")
	ack = data.get("vendorAckId") or inner.get("vendorAckId")
	return mid, ack


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
