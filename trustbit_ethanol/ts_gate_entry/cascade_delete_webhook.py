"""External audit webhook dispatcher (v2.11.0 Hardening D).

Posts a HMAC-signed copy of the cascade delete log row to the URL configured
in TS Settings.ts_cascade_webhook_url. Receiver verifies signature via the
shared secret in ts_cascade_webhook_secret (Password field, encrypted at rest).

Payload includes ONLY metadata + hash chain — NEVER the JSON snapshot, the
forensic block, or any field that could leak PII off-site beyond what the
admin explicitly opted into.

Lesson references: 238 (best-effort — webhook failure NEVER blocks success
path), 237 (log_error kwargs), 175 (this module is NOT a whitelisted endpoint;
called only from cascade_delete_api in-process).
"""

import hashlib
import hmac
import json
from datetime import datetime
import frappe


def _get_webhook_config() -> tuple[str, str]:
	"""Returns (url, secret). Both empty strings if not configured."""
	url = (frappe.db.get_single_value("TS Settings", "ts_cascade_webhook_url") or "").strip()
	secret = frappe.get_doc("TS Settings").get_password("ts_cascade_webhook_secret", raise_exception=False) or ""
	return url, secret


def _build_payload(log_doc) -> dict:
	"""Build the webhook payload — metadata + hash chain only, no snapshot/forensic."""
	return {
		"log_name": log_doc.name,
		"target_token": log_doc.target_token,
		"approval_status": log_doc.approval_status,
		"initiated_by": log_doc.initiated_by,
		"initiated_at": str(log_doc.initiated_at) if log_doc.initiated_at else None,
		"approved_by": log_doc.approved_by,
		"approved_at": str(log_doc.approved_at) if log_doc.approved_at else None,
		"executed_at": str(log_doc.executed_at) if log_doc.executed_at else None,
		"backup_filename": log_doc.backup_filename,
		"backup_sha256": log_doc.backup_sha256,
		"prev_row_hash": log_doc.prev_row_hash,
		"this_row_hash": log_doc.this_row_hash,
		"dispatch_timestamp_iso": datetime.utcnow().isoformat() + "Z",
	}


def _sign(payload_body: str, secret: str) -> str:
	"""HMAC-SHA256 of the payload body using the shared secret."""
	return hmac.new(
		secret.encode("utf-8"),
		payload_body.encode("utf-8"),
		hashlib.sha256,
	).hexdigest()


def dispatch_webhook(log_doc) -> dict:
	"""POST to configured webhook URL with HMAC signature header.

	Best-effort: 3 attempts with exponential backoff. Persists result onto
	the log row (webhook_delivered + webhook_response_code + webhook_attempted_at
	+ webhook_response_body_excerpt). NEVER throws — caller wraps in try/except
	defense-in-depth per Lesson 238.

	Returns: {"delivered": bool, "code": int, "attempts": int, "error"?: str}
	"""
	url, secret = _get_webhook_config()
	if not url:
		return {"delivered": False, "code": 0, "attempts": 0, "error": "no webhook configured"}
	if not secret:
		frappe.log_error(
			title="Cascade webhook config error",
			message=f"Webhook URL set ({url}) but secret missing. Skipping dispatch for {log_doc.name}.",
		)
		return {"delivered": False, "code": 0, "attempts": 0, "error": "no secret"}

	import requests  # imported lazy — Frappe ships it

	payload = _build_payload(log_doc)
	body = json.dumps(payload, sort_keys=True, default=str)
	signature = _sign(body, secret)
	headers = {
		"Content-Type": "application/json",
		"User-Agent": "BBPL-Cascade-Delete/2.11.0",
		"X-CascadeDelete-Signature": signature,
		"X-CascadeDelete-LogName": log_doc.name,
	}

	last_code = 0
	last_excerpt = ""
	last_error = ""
	import time
	for attempt in range(1, 4):
		try:
			resp = requests.post(url, data=body, headers=headers, timeout=10)
			last_code = resp.status_code
			last_excerpt = (resp.text or "")[:500]
			if 200 <= resp.status_code < 300:
				_persist_webhook_result(log_doc.name, True, last_code, last_excerpt, "")
				return {"delivered": True, "code": last_code, "attempts": attempt}
			# Non-2xx — retry
			last_error = f"HTTP {resp.status_code}"
		except Exception as e:
			last_error = f"{type(e).__name__}: {e}"
			last_code = 0
			last_excerpt = ""
		time.sleep(2 ** attempt)  # 2, 4, 8 sec backoff

	# All 3 attempts failed
	frappe.log_error(
		title=f"Cascade webhook delivery failed for {log_doc.name}",
		message=f"URL={url} attempts=3 last_error={last_error} code={last_code} excerpt={last_excerpt}",
	)
	_persist_webhook_result(log_doc.name, False, last_code, last_excerpt, last_error)
	return {"delivered": False, "code": last_code, "attempts": 3, "error": last_error}


def _persist_webhook_result(log_name: str, delivered: bool, code: int, excerpt: str, error: str):
	"""Write webhook result back to the log row (bypasses controller append-only
	guard via frappe.flags.cascade_delete_api_caller — caller is responsible
	for setting the flag; this helper just calls db_set."""
	try:
		# Direct db_set is sufficient — no append-only check on db_set itself.
		updates = {
			"webhook_delivered": 1 if delivered else 0,
			"webhook_response_code": code,
			"webhook_attempted_at": frappe.utils.now_datetime(),
			"webhook_response_body_excerpt": (excerpt or error)[:500],
		}
		for f, v in updates.items():
			frappe.db.set_value("TS Cascade Delete Log", log_name, f, v, update_modified=False)
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(
			title=f"Cascade webhook result persist failed for {log_name}",
			message=f"{type(e).__name__}: {e}",
		)
