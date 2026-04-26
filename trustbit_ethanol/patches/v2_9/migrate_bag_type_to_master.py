"""v2.9.0 Day 5B — Bag Type free-text -> Master link migration.

Pre-v2.9 QIs may have a free-text bag type stored in any of:
  - bag_type field (now a Link to TS Bag Type Master, but legacy text values may
    have been written before the field was retyped),
  - remarks (rare),
  - hold_reason (rare).

This patch ONLY migrates the `bag_type` column. We try to match the legacy text
against the existing TS Bag Type Master records (by `name` exact, then by
case-insensitive contains on bag_type_name). Unmatched -> log + set safe
fallback "Loose" (auto-create that master record if missing).

Idempotent + safe:
  - Skips rows already linked (where bag_type already exists as a TS Bag Type Master name).
  - Logs every unmatched value to frappe.log_error for ops review.
"""

import frappe


SAFE_FALLBACK = "Loose"


def _ensure_loose_fallback() -> None:
	if frappe.db.exists("TS Bag Type Master", SAFE_FALLBACK):
		return
	doc = frappe.new_doc("TS Bag Type Master")
	doc.bag_type_name = SAFE_FALLBACK
	doc.is_active = 1
	doc.standard_weight_kg = 0.0
	doc.tolerance_kg = 0.0
	doc.description = "Loose / no bag — fallback created by v2.9.0 migration when free-text bag type couldn't be matched."
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)


def _resolve(raw: str, master_names: list, master_lookup: dict) -> str | None:
	"""Return TS Bag Type Master name or None if no confident match."""
	if not raw or not raw.strip():
		return None
	raw_clean = raw.strip()

	# Exact name match (case-sensitive — Frappe names ARE case-sensitive).
	if raw_clean in master_names:
		return raw_clean

	# Case-insensitive equality on either name or bag_type_name display.
	low = raw_clean.lower()
	for n in master_names:
		if n.lower() == low:
			return n

	# Substring match on bag_type_name (display label).
	for name, display in master_lookup.items():
		if display and low in display.lower():
			return name

	return None


def execute():
	_ensure_loose_fallback()

	# Build master lookup once.
	masters = frappe.get_all(
		"TS Bag Type Master",
		fields=["name", "bag_type_name"],
	)
	master_names = [m.name for m in masters]
	master_lookup = {m.name: m.bag_type_name for m in masters}

	# Read raw bag_type column. We expect: either matches a Master.name, or is
	# legacy free-text. We can't trust frappe.get_all (Link field may filter).
	rows = frappe.db.sql(
		"""
		SELECT name, bag_type
		FROM `tabTS Quality Inspection`
		WHERE bag_type IS NOT NULL
		  AND bag_type != ''
		""",
		as_dict=True,
	)

	if not rows:
		print("[v2.9.0] migrate_bag_type_to_master: nothing to migrate.")
		return

	matched = 0
	fallback = 0
	already = 0
	for r in rows:
		raw = (r.bag_type or "").strip()

		# Already linked to a Master record — skip.
		if raw in master_names:
			already += 1
			continue

		resolved = _resolve(raw, master_names, master_lookup)
		if resolved:
			frappe.db.set_value(
				"TS Quality Inspection",
				r.name,
				"bag_type",
				resolved,
				update_modified=False,
			)
			matched += 1
		else:
			frappe.log_error(
				message=f"[v2.9.0] Unmatched legacy bag_type value '{raw}' on QI {r.name} -- fell back to '{SAFE_FALLBACK}'.",
				title="v2.9.0 migrate_bag_type_to_master",
			)
			frappe.db.set_value(
				"TS Quality Inspection",
				r.name,
				"bag_type",
				SAFE_FALLBACK,
				update_modified=False,
			)
			fallback += 1

	frappe.db.commit()
	print(
		f"[v2.9.0] migrate_bag_type_to_master: matched={matched} fallback={fallback} already_linked={already} total={len(rows)}"
	)
