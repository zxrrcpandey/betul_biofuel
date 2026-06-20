# Copyright (c) 2026, Trustbit Software and contributors
# WhatsApp Integration — multi-adapter provider registry.
#
# The shared adapter (ts_whatsapp.py) handles gating, logging, retry and the
# Log lifecycle. The ONLY provider-specific thing is how to build the outbound
# HTTP request and how to read the response. Each provider supplies two pure
# functions:
#
#   build(conn, ctx) -> (url, headers, json_body)
#   parse(http_status, data) -> {"ok", "message_id", "vendor_ack", "error_code", "error_message"}
#
# Adding a new provider = one entry in PROVIDERS. No change to the approval
# shim, the escalation scheduler, the Log, or recipient resolution.
#
# ctx keys: to (91XXXXXXXXXX), template_ref (templateId / template name),
#           language, variables (positional list).
# conn keys: base_url(optional override), token, vendor_uid, from_phone_number_id,
#            app_id, sender, filter_blacklist.

WHATSHUB360_BASE = "https://app.whatshub360.com/api"
AIRTEL_BASE = "https://iqwhatsapp.airtel.in:443/gateway/airtel-xchange/basic/whatsapp-manager/v1"

DEFAULT_PROVIDER = "WhatsHub360"

# Provider-relayed Meta codes worth retrying (Airtel surfaces these). HTTP-level
# retry (429/5xx) is handled by the shared worker and covers WhatsHub360.
AIRTEL_RETRYABLE_CODES = {"130429", "131000", "100", "400", "-8"}


# --------------------------------------------------------------------------- #
# WhatsHub360
# --------------------------------------------------------------------------- #
def _wh360_build(conn, ctx):
	base = (conn.get("base_url") or WHATSHUB360_BASE).rstrip("/")
	vendor_uid = conn.get("vendor_uid") or ""
	url = f"{base}/{vendor_uid}/contact/send-message"
	headers = {
		"Content-Type": "application/json",
		"Accept": "application/json",
		"Authorization": "Bearer " + (conn.get("token") or ""),
	}
	body = {
		"phone_number": ctx["to"],
		"template_name": ctx["template_ref"],
		"template_language": ctx.get("language") or "en",
	}
	if conn.get("from_phone_number_id"):
		body["from_phone_number_id"] = conn["from_phone_number_id"]
	# Positional variables -> field_1, field_2, ... (WhatsHub360 body parameters).
	for i, value in enumerate(ctx.get("variables") or [], start=1):
		body[f"field_{i}"] = str(value)
	return url, headers, body


def _wh360_parse(http_status, data):
	if not (200 <= http_status < 300):
		return {"ok": False, "error_code": str(http_status), "error_message": _text(data)}
	if isinstance(data, dict):
		status = data.get("status")
		success = data.get("success")
		# Explicit failure signalled in a 2xx body.
		if success is False or (isinstance(status, str) and status.lower() in ("error", "failed", "fail")):
			return {
				"ok": False,
				"error_code": str(data.get("code") or data.get("error_code") or "error"),
				"error_message": _text(data),
			}
		inner = data.get("data") if isinstance(data.get("data"), dict) else {}
		mid = (
			data.get("message_id")
			or data.get("whatsapp_message_id")
			or inner.get("message_id")
			or inner.get("whatsapp_message_id")
		)
		return {"ok": True, "message_id": mid, "vendor_ack": None}
	# Non-dict 2xx (e.g. plain "ok") — treat as accepted.
	return {"ok": True, "message_id": None, "vendor_ack": None}


# --------------------------------------------------------------------------- #
# Airtel IQ
# --------------------------------------------------------------------------- #
def _airtel_build(conn, ctx):
	base = (conn.get("base_url") or AIRTEL_BASE).rstrip("/")
	url = base + "/template/send"
	headers = {
		"app-id": conn.get("app_id") or "",
		"Content-Type": "application/json",
		"Authorization": "Basic " + (conn.get("token") or ""),
	}
	body = {
		"templateId": ctx["template_ref"],
		"to": ctx["to"],
		"from": conn.get("sender") or "",
		"filterBlacklistNumbers": bool(conn.get("filter_blacklist")),
		"message": {"variables": [str(v) for v in (ctx.get("variables") or [])]},
	}
	return url, headers, body


def _airtel_parse(http_status, data):
	code, msg = _airtel_error(data)
	if 200 <= http_status < 300 and not code:
		mid, ack = _airtel_ids(data)
		return {"ok": True, "message_id": mid, "vendor_ack": ack}
	return {
		"ok": False,
		"error_code": str(code) if code is not None else str(http_status),
		"error_message": _text(msg) if msg else f"HTTP {http_status}",
	}


def _airtel_error(data):
	if not isinstance(data, dict):
		return None, None
	err = data.get("error")
	if isinstance(err, dict):
		code = err.get("code")
		msg = err.get("message")
		if isinstance(msg, dict):
			inner = msg.get("error", {})
			if isinstance(inner, dict):
				code = inner.get("code", code)
				msg = inner.get("message", None)
		return (str(code) if code is not None else None), msg
	code = data.get("code")
	if code not in (None, 0, "0", 200, "200"):
		return str(code), data.get("title") or data.get("message")
	status = (data.get("messageStatus") or data.get("msgStatus") or "").upper()
	if status == "FAILED":
		return "FAILED", data.get("title") or "Send reported FAILED"
	return None, None


def _airtel_ids(data):
	if not isinstance(data, dict):
		return None, None
	inner = data.get("data") if isinstance(data.get("data"), dict) else {}
	mid = data.get("messageId") or inner.get("messageId")
	ack = data.get("vendorAckId") or inner.get("vendorAckId")
	return mid, ack


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
PROVIDERS = {
	"WhatsHub360": {
		"default_base": WHATSHUB360_BASE,
		"required": ("vendor_uid", "token"),
		"build": _wh360_build,
		"parse": _wh360_parse,
		"retryable_codes": frozenset(),
	},
	"Airtel IQ": {
		"default_base": AIRTEL_BASE,
		"required": ("app_id", "token", "sender"),
		"build": _airtel_build,
		"parse": _airtel_parse,
		"retryable_codes": frozenset(AIRTEL_RETRYABLE_CODES),
	},
}


def get_provider(name):
	return PROVIDERS.get(name) or PROVIDERS[DEFAULT_PROVIDER]


def provider_names():
	return list(PROVIDERS.keys())


def _text(value, limit=900):
	if value is None:
		return None
	return str(value)[:limit]
