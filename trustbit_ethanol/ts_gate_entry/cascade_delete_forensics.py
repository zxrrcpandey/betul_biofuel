"""Forensic block capture for the cascade delete log (v2.11.0 Q&A item 7).

Captures: IP, OS, device, browser, GeoIP city/country, session_id, server timezone,
request timestamp, screen size (client-supplied), language (client-supplied), UA.

GeoIP is OPTIONAL — if the `geoip2` package isn't installed or the database isn't
available, we skip it gracefully and log the omission. The forensic block is
informational only — never used for auth decisions.

Lesson references: 237 (log_error kwargs), 238 (best-effort), 175 (POST CSRF —
the caller endpoint must declare methods=["POST"] before invoking this).
"""

import json
import socket
from datetime import datetime
import frappe


def _ua_parse(ua: str) -> dict:
	"""Best-effort User-Agent parser. Returns dict with os/device/browser keys.

	Not perfect — we ship a tiny built-in parser that covers Chrome / Firefox /
	Safari / Edge / Mobile Safari / Android Chrome / iOS. If the project later
	adopts the `user-agents` PyPI package, this can be swapped for that.
	"""
	if not ua:
		return {"os": "unknown", "device": "unknown", "browser": "unknown"}
	ua_lower = ua.lower()

	os_name = "unknown"
	if "windows" in ua_lower: os_name = "Windows"
	elif "iphone" in ua_lower or "ios" in ua_lower: os_name = "iOS"
	elif "ipad" in ua_lower: os_name = "iPadOS"
	elif "android" in ua_lower: os_name = "Android"
	elif "mac os" in ua_lower or "macintosh" in ua_lower: os_name = "macOS"
	elif "linux" in ua_lower: os_name = "Linux"

	device = "desktop"
	if "mobile" in ua_lower or "iphone" in ua_lower or "android" in ua_lower: device = "mobile"
	elif "ipad" in ua_lower or "tablet" in ua_lower: device = "tablet"

	browser = "unknown"
	if "edg/" in ua_lower: browser = "Edge"
	elif "chrome/" in ua_lower: browser = "Chrome"
	elif "firefox/" in ua_lower: browser = "Firefox"
	elif "safari/" in ua_lower: browser = "Safari"

	return {"os": os_name, "device": device, "browser": browser}


def _geoip_lookup(ip: str) -> dict:
	"""Best-effort GeoIP lookup. Returns {city, country, asn} or {} on failure.

	Tries `geoip2` PyPI package with a `GeoLite2-City.mmdb` at conventional path.
	If unavailable, returns empty dict and logs a note.
	"""
	try:
		import geoip2.database
		# Conventional install path; admin must drop the .mmdb file here.
		import os
		mmdb = os.environ.get("GEOIP_MMDB_PATH", "/etc/geoip/GeoLite2-City.mmdb")
		if not os.path.exists(mmdb):
			return {}
		with geoip2.database.Reader(mmdb) as reader:
			rec = reader.city(ip)
			return {
				"city": getattr(rec.city, "name", None),
				"country": getattr(rec.country, "name", None),
				"country_code": getattr(rec.country, "iso_code", None),
			}
	except Exception:
		return {}


def capture_forensic_block(client_hints: dict | None = None) -> dict:
	"""Build the forensic JSON block from request context + optional client hints.

	`client_hints` is an optional dict the JS client can POST alongside the
	cascade initiate payload — must contain `screen` (str) and `language` (str).

	Returns the dict; caller serializes to JSON and stores in
	TS Cascade Delete Log.forensic_block_json.
	"""
	client_hints = client_hints or {}
	out = {
		"timestamp_iso": datetime.utcnow().isoformat() + "Z",
		"server_timezone": frappe.utils.get_system_timezone(),
		"server_hostname": socket.gethostname(),
		"session_id": frappe.session.sid if hasattr(frappe.session, "sid") else None,
		"session_user": frappe.session.user,
		"client_screen": (client_hints.get("screen") or "")[:64],
		"client_language": (client_hints.get("language") or "")[:32],
	}

	try:
		request = frappe.local.request if hasattr(frappe.local, "request") else None
		if request is not None:
			ua = request.headers.get("User-Agent", "")
			ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or ""
			out["client_ip"] = ip[:64]
			out["user_agent"] = ua[:512]
			out["referer"] = (request.headers.get("Referer") or "")[:256]
			parsed = _ua_parse(ua)
			out.update({f"ua_{k}": v for k, v in parsed.items()})
			geo = _geoip_lookup(ip)
			out.update({f"geo_{k}": v for k, v in geo.items()})
	except Exception as e:
		out["forensic_capture_error"] = f"{type(e).__name__}: {e}"

	return out


def serialize_forensic_block(block: dict) -> str:
	"""Wrap-and-truncate helper. Keeps the stored JSON under 8 KB."""
	body = json.dumps(block, indent=2, sort_keys=True, default=str)
	if len(body) > 8000:
		body = body[:8000] + "\n... (truncated)"
	return body
