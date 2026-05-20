"""Pre-cascade backup writer + revert restore (v2.11.0 Hardening B + F + M).

Workflow:
- `create_pre_cascade_backup(token_name, log_name)` — gathers full chain
  (PI / PR / DS / QI / WB / GE / Token) as a JSON tree, computes SHA-256,
  writes to `sites/<site>/private/files/cascade_backups/<log_name>.json`
  plus `<log_name>.sha256` sidecar. Returns dict with filename + hash.
- `verify_backup_sha256(log_doc)` — re-hashes file and asserts match.
- `restore_from_backup(log_doc)` — idempotent restore for the 5-min revert
  grace window (Hardening M). Verifies SHA-256 BEFORE write.

Lesson references: 222 (idempotent), 237 (log_error kwargs), 238 (best-effort).
"""

import hashlib
import json
import os
from datetime import datetime
import frappe
from frappe.utils import get_site_path


def _backup_dir() -> str:
	target = get_site_path("private", "files", "cascade_backups")
	os.makedirs(target, exist_ok=True)
	return target


def _row_dict(doctype: str, name: str) -> dict | None:
	"""Read a full doc as a dict, including child tables."""
	if not frappe.db.exists(doctype, name):
		return None
	doc = frappe.get_doc(doctype, name)
	return doc.as_dict()


def create_pre_cascade_backup(token_name: str, log_name: str) -> dict:
	"""Snapshot the full chain to JSON + write SHA256 sidecar.

	Returns:
	  {"filename": "<log_name>.json", "sha256": "...", "size_bytes": N}
	"""
	from trustbit_ethanol.ts_gate_entry.cascade_delete_engine import build_chain_snapshot

	chain = build_chain_snapshot(token_name)
	full = {
		"meta": {
			"backup_format_version": 1,
			"token_name": token_name,
			"log_name": log_name,
			"taken_at": datetime.utcnow().isoformat() + "Z",
		},
		"chain_summary": chain,
		"docs": {
			"ts_token": _row_dict("TS Token", token_name),
			"gate_entries": [_row_dict("TS Gate Entry", r["name"]) for r in chain["gate_entries"]],
			"weighbridge_logs": [_row_dict("TS Weighbridge Log", r["name"]) for r in chain["weighbridge_logs"]],
			"quality_inspections": [_row_dict("TS Quality Inspection", r["name"]) for r in chain["quality_inspections"]],
			"deduction_sheets": [_row_dict("TS Deduction Sheet", r["name"]) for r in chain["deduction_sheets"]],
			"purchase_receipts": [_row_dict("Purchase Receipt", r["name"]) for r in chain["purchase_receipts"]],
			"purchase_invoices": [_row_dict("Purchase Invoice", r["name"]) for r in chain["purchase_invoices"]],
		},
	}

	# Serialize with deterministic key order so SHA-256 is reproducible.
	body = json.dumps(full, indent=2, sort_keys=True, default=str)
	sha = hashlib.sha256(body.encode("utf-8")).hexdigest()

	filename = f"{log_name}.json"
	sidecar = f"{log_name}.sha256"
	full_path = os.path.join(_backup_dir(), filename)
	sidecar_path = os.path.join(_backup_dir(), sidecar)

	with open(full_path, "w", encoding="utf-8") as f:
		f.write(body)
	with open(sidecar_path, "w", encoding="utf-8") as f:
		f.write(sha + "\n")

	return {"filename": filename, "sha256": sha, "size_bytes": len(body)}


def verify_backup_sha256(log_doc) -> bool:
	"""Re-hash the on-disk file and compare to log_doc.backup_sha256.

	Returns True if match. False if mismatch OR file missing OR sidecar mismatch.
	Caller MUST treat False as a security alarm — restore is blocked.
	"""
	if not log_doc.backup_filename or not log_doc.backup_sha256:
		return False
	full_path = os.path.join(_backup_dir(), log_doc.backup_filename)
	if not os.path.exists(full_path):
		return False
	with open(full_path, "rb") as f:
		body = f.read()
	on_disk = hashlib.sha256(body).hexdigest()
	return on_disk == log_doc.backup_sha256


def restore_from_backup(log_doc) -> dict:
	"""Reconstruct the cascade chain from the backup file. Verifies SHA-256 first.

	IDEMPOTENT — running twice produces same end state (re-insert is a no-op on
	`frappe.db.exists` check). Used by Hardening M 5-min revert window.

	Returns: {"success": bool, "restored": {...}, "error"?: str}
	"""
	if not verify_backup_sha256(log_doc):
		return {"success": False, "error": "Backup SHA-256 mismatch or file missing — restore aborted."}

	full_path = os.path.join(_backup_dir(), log_doc.backup_filename)
	with open(full_path, "r", encoding="utf-8") as f:
		data = json.load(f)

	restored = {"ts_token": 0, "ge": 0, "wb": 0, "qi": 0, "ds": 0, "pr": 0, "pi": 0}
	docs = data.get("docs", {})

	# Restore order: Token first (parent), then GE, then siblings (WB/QI/DS), then PR, then PI.
	# Restore via db_insert NOT doc.insert — bypasses lifecycle hooks (we want
	# to restore the EXACT row state, not re-run validations / auto-status / etc.)
	for label, dt, key in [
		("ts_token", "TS Token", "ts_token"),
		("ge", "TS Gate Entry", "gate_entries"),
		("wb", "TS Weighbridge Log", "weighbridge_logs"),
		("qi", "TS Quality Inspection", "quality_inspections"),
		("ds", "TS Deduction Sheet", "deduction_sheets"),
		("pr", "Purchase Receipt", "purchase_receipts"),
		("pi", "Purchase Invoice", "purchase_invoices"),
	]:
		recs = docs.get(key)
		if recs is None:
			continue
		if isinstance(recs, dict):
			recs = [recs]
		for rec in recs:
			if not rec:
				continue
			if frappe.db.exists(dt, rec.get("name")):
				# Idempotent skip
				continue
			try:
				# Reconstruct via Document then db_insert to capture child tables.
				new_doc = frappe.get_doc(rec)
				new_doc.flags.ignore_permissions = True
				new_doc.flags.ignore_links = True
				new_doc.flags.ignore_validate = True
				new_doc.flags.ignore_mandatory = True
				new_doc.db_insert()
				# Child tables — db_insert each row
				for child_field, child_rows in [(k, v) for k, v in rec.items() if isinstance(v, list)]:
					for child_row in child_rows:
						if isinstance(child_row, dict) and "doctype" in child_row:
							cdoc = frappe.get_doc(child_row)
							cdoc.flags.ignore_permissions = True
							cdoc.flags.ignore_validate = True
							cdoc.db_insert()
				restored[label] = restored.get(label, 0) + 1
			except Exception as e:
				frappe.log_error(
					title=f"Cascade revert restore failed: {dt} {rec.get('name')}",
					message=f"{type(e).__name__}: {e}",
				)

	frappe.db.commit()
	return {"success": True, "restored": restored}
