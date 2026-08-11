"""Shared chain-hop machinery for the TS Script Reports.

Every report that walks the MR -> PO -> PR -> PI document chain MUST use these
helpers instead of hand-rolling per-file copies — this module exists because
the v2.38.2 hop-permission fix was originally patched into each report
separately, and a sweep then found the same class of gap re-appearing
(ts_po_report._mr_owner_map). One implementation, one place to audit.

THE RULES THESE HELPERS ENCODE (do not bypass them):

1. hop_readable(doctype) — a chain hop over a doctype the session user cannot
   read AT ALL renders BLANK (the reports' blank-leg semantics). Without this
   gate, frappe.build_match_conditions raises "No permission to read <doctype>"
   from deep inside db_query and kills the whole report for e.g. a Purchase
   User who lacks Purchase Invoice read.

2. conf_match_clauses(doctype, alias) — every raw-SQL path over Purchase
   Order / Purchase Receipt / Purchase Invoice / Material Request carries BOTH
   confidential_sql_clause (Lesson 297: returns '' for allow-listed users,
   else hides rows unless owner = user) AND build_match_conditions,
   alias-repointed, composed as a clause LIST (never inlined fragments).

3. chunked(names) — IN-lists are chunked at 1000 names; an empty input never
   reaches SQL (callers must skip the hop — visible_docs does this for you),
   so `IN ()` syntax errors cannot happen.

4. fullname_map(user_ids) — person columns render FULL NAMES via one bulk
   query over tabUser. Deliberately raw SQL: operators frequently lack User
   read (Lesson 168), and a creator/approver display name is not confidential
   — it is already surfaced on the source document's own form.

5. contact_blob(row) — address_display is stored HTML with <br> separators;
   convert those to ', ' BEFORE strip_html or the lines run together.
"""

import frappe
from frappe.utils import strip_html

IN_CHUNK = 1000

# The four doctypes governed by the maize confidentiality system (Lesson 297).
CONFIDENTIAL_DOCTYPES = (
	"Purchase Order", "Purchase Receipt", "Purchase Invoice", "Material Request",
)


def chunked(names):
	"""Yield sorted tuples of at most IN_CHUNK names. Yields nothing for empty
	input — callers therefore never emit `IN ()`."""
	names = sorted(names)
	for i in range(0, len(names), IN_CHUNK):
		yield tuple(names[i:i + IN_CHUNK])


def hop_readable(doctype):
	"""True when the session user may read `doctype` at all. Callers MUST skip
	the hop (leg renders blank) when this is False — see module docstring."""
	return frappe.has_permission(doctype, "read")


def conf_match_clauses(doctype, alias):
	"""Clause LIST applying confidentiality + user-permission match conditions
	for `doctype`, columns re-pointed to `alias`. Compose with
	' AND '.join([...]) — never inline into an existing predicate string."""
	from trustbit_ethanol.ts_gate_entry.ts_confidential_po import confidential_sql_clause

	conds = []
	if doctype in CONFIDENTIAL_DOCTYPES:
		conf = confidential_sql_clause(alias, doctype=doctype)
		if conf:
			# helper returns " AND (...)" — strip the leading AND for list use
			conds.append(conf.strip()[4:].strip())
	match = frappe.build_match_conditions(doctype)
	if match:
		conds.append("(%s)" % match.replace(f"`tab{doctype}`", alias))
	return conds


def visible_docs(doctype, alias, names, extra_cols="", docstatus=(1,)):
	"""Rows of `doctype` from `names` that this user may see, with optional
	extra columns (prefixed ', '). Applies the full rule set: hop-readable
	gate, docstatus filter (None = no filter), confidentiality clause and
	match conditions. Empty input or unreadable hop -> []."""
	if not names:
		return []
	if not hop_readable(doctype):
		return []
	conds = []
	if docstatus is not None:
		if len(docstatus) == 1:
			conds.append(f"{alias}.docstatus = {int(list(docstatus)[0])}")
		else:
			conds.append("%s.docstatus IN (%s)" % (alias, ", ".join(str(int(d)) for d in docstatus)))
	conds += conf_match_clauses(doctype, alias)
	where = (" AND " + " AND ".join(conds)) if conds else ""
	out = []
	for chunk in chunked(names):
		out += frappe.db.sql(
			f"""SELECT {alias}.name{extra_cols} FROM `tab{doctype}` {alias}
			WHERE {alias}.name IN %(names)s{where}""",
			{"names": chunk},
			as_dict=True,
		)
	return out


def fullname_map(user_ids):
	"""user_id -> full name (falls back to the id), one bulk query. See rule 4."""
	ids = sorted({u for u in user_ids if u})
	if not ids:
		return {}
	out = {}
	for chunk in chunked(ids):
		for x in frappe.db.sql(
			"SELECT name, full_name FROM `tabUser` WHERE name IN %(ids)s",
			{"ids": chunk},
			as_dict=True,
		):
			out[x["name"]] = x["full_name"] or x["name"]
	return out


def contact_blob(row):
	"""Flatten a PO's address + contact block into one plain-text cell."""
	parts = []
	addr = row.get("address_display") or ""
	if addr:
		addr = addr.replace("<br>", ", ").replace("<br/>", ", ").replace("<br />", ", ")
		addr = " ".join(strip_html(addr).split()).strip(" ,")
		if addr:
			parts.append(addr)
	for key in ("contact_display", "contact_mobile", "contact_email"):
		val = (row.get(key) or "").strip()
		if val and val not in parts:
			parts.append(val)
	return " · ".join(parts)
