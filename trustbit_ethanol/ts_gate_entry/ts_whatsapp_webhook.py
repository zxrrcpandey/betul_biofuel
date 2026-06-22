# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration (Airtel IQ) — Phase 1a delivery-status webhook.
#
# Airtel POSTs delivery callbacks here. The endpoint is allow_guest (Airtel is
# unauthenticated to Frappe) but verifies a shared secret header and/or a source
# IP allowlist before touching anything. All input is untrusted: it only updates
# whitelisted fields on a TS WhatsApp Log matched by exact provider id, and never
# echoes the payload (Lesson 162 / 175).

import hmac
import json
import re

import frappe

# Airtel/Meta delivery status -> our Log status.
STATUS_MAP = {
	"SENT": "Sent",
	"DELIVERED": "Delivered",
	"READ": "Read",
	"FAILED": "Failed",
}
# Monotonic rank so a late "SENT" can't regress a "DELIVERED"/"READ".
STATUS_RANK = {"Queued": 0, "Sent": 1, "Delivered": 2, "Read": 3, "Failed": 4}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def delivery_status():
	"""Ingest an Airtel delivery-status callback. Always returns a minimal ack;
	never leaks whether a record matched."""
	ack = {"status": "ok"}
	try:
		if not _verify_request():
			return ack
		payload = _read_json()
		if not isinstance(payload, dict):
			return ack

		message_id = _clean(payload.get("messageId"))
		# Airtel's DLR carries messageRequestId (== the id /template/send returned,
		# stored as this Log's vendor_ack_id at send). Prefer it for matching.
		msg_req_id = _clean(payload.get("messageRequestId"))
		vendor_ack = _clean(payload.get("vendorAckId")) or msg_req_id
		raw_status = (
			payload.get("msgStatus") or payload.get("messageStatus") or payload.get("status") or ""
		).upper()
		new_status = STATUS_MAP.get(raw_status)
		if not new_status or not (message_id or vendor_ack):
			return ack

		log_name = _match_log(message_id, vendor_ack)
		if not log_name:
			return ack

		updates = {
			"status_updated_at": frappe.utils.now_datetime(),
		}
		# Only advance the lifecycle forward; Failed always applies.
		current = frappe.db.get_value("TS WhatsApp Log", log_name, "status")
		if new_status == "Failed" or STATUS_RANK.get(new_status, 0) >= STATUS_RANK.get(current, 0):
			updates["status"] = new_status
		# Keep ids fresh if Airtel only returned one at send time.
		if message_id:
			updates["provider_message_id"] = message_id
		if vendor_ack:
			updates["vendor_ack_id"] = vendor_ack
		err_code, err_msg = _extract_error(payload)
		if err_code:
			updates["error_code"] = err_code
		if err_msg:
			updates["error_message"] = err_msg[:900]

		frappe.flags.in_whatsapp_server = True
		try:
			frappe.db.set_value("TS WhatsApp Log", log_name, updates, update_modified=False)
			frappe.db.commit()
		finally:
			frappe.flags.in_whatsapp_server = False
	except Exception as e:
		try:
			frappe.log_error(title="WhatsApp: webhook error"[:140], message=str(e))
		except Exception:
			pass
		frappe.clear_messages()
	return ack


# --------------------------------------------------------------------------- #
# Verification + parsing (all input untrusted)
# --------------------------------------------------------------------------- #
def _verify_request():
	"""True only if the request proves itself via the configured mechanism.
	Fail-closed: if neither a secret nor an IP allowlist is configured, reject."""
	secret = None
	try:
		secret = frappe.get_cached_doc("TS WhatsApp Settings").get_password(
			"ts_whatsapp_callback_secret", raise_exception=False
		)
	except Exception:
		secret = None
	allow = frappe.db.get_single_value("TS WhatsApp Settings", "ts_whatsapp_source_ip_allowlist") or ""
	allow_ips = {ip.strip() for ip in re.split(r"[,\s]+", allow) if ip.strip()}

	if secret:
		# Airtel sends NO custom header on its DLR callback, so ALSO accept the
		# secret passed as a URL query param ?k=<secret> (Airtel echoes back the
		# exact registered callback URL). The X-Callback-Token header still works
		# for providers that can attach one (e.g. WhatsHub360).
		token = frappe.get_request_header("X-Callback-Token") or ""
		if not token:
			# ?k=<secret> query param. A JSON-body POST does NOT merge query
			# params into form_dict, so read request.args first, then fall back.
			try:
				token = (frappe.request.args.get("k") if getattr(frappe, "request", None) else None) or ""
			except Exception:
				token = ""
			if not token:
				try:
					token = (frappe.local.form_dict or {}).get("k") or ""
				except Exception:
					token = ""
		if hmac.compare_digest(str(token), str(secret)):
			return True
		# Secret configured but neither header nor ?k= matched — reject.
		return False
	if allow_ips:
		return _request_ip() in allow_ips
	# Nothing configured to verify against — refuse.
	return False


def _read_json():
	try:
		data = frappe.request.get_data() if getattr(frappe, "request", None) else None
		if data:
			return json.loads(data)
	except Exception:
		pass
	# Fallback to form_dict (Frappe may have parsed it).
	try:
		fd = dict(frappe.local.form_dict or {})
		fd.pop("cmd", None)
		return fd or None
	except Exception:
		return None


def _request_ip():
	try:
		return frappe.local.request_ip
	except Exception:
		return None


def _match_log(message_id, vendor_ack):
	if message_id:
		name = frappe.db.get_value("TS WhatsApp Log", {"provider_message_id": message_id}, "name")
		if name:
			return name
	if vendor_ack:
		return frappe.db.get_value("TS WhatsApp Log", {"vendor_ack_id": vendor_ack}, "name")
	return None


def _extract_error(payload):
	err = payload.get("error")
	if isinstance(err, dict):
		code = err.get("code") or err.get("errorCode")
		msg = err.get("message") or err.get("errorTitle") or err.get("errorDetails")
		if isinstance(msg, dict):
			inner = msg.get("error", {})
			if isinstance(inner, dict):
				code = inner.get("code", code)
				msg = inner.get("message", None)
		return (str(code) if code is not None else None), (str(msg) if msg else None)
	# Airtel may send flat error columns (report CSV: Error Code / Title / Details).
	code = payload.get("errorCode") or payload.get("error_code")
	msg = payload.get("errorTitle") or payload.get("errorDetails") or payload.get("error_message")
	if code or msg:
		return (str(code) if code is not None else None), (str(msg) if msg else None)
	return None, None


def _clean(value):
	if value is None:
		return None
	s = str(value).strip()
	return s or None
