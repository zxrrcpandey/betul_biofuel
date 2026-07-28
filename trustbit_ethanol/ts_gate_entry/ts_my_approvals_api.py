# v2.8.13 — "My Pending Approvals" API helpers
#
# Role-scoped list-view filtering + dashboard count helpers for MR/PO/
# Post-Dated/Budget Proposal. All endpoints are safe GET reads (no
# mutation) and require authenticated session.
#
# Kill switch: TS Settings.ts_my_approvals_enabled (Check, permlevel=1,
# default=1). When OFF, helpers return empty payloads → pre-feature
# behavior restored.
#
# Security model:
#  - Idempotent reads → @frappe.whitelist() sufficient (no methods=POST
#    per Lesson 175 since we're not mutating)
#  - allow_guest=False (default) — no public exposure
#  - JS gates session check on client side per Lesson 190
#  - Uses frappe.db.count() with filter dict — no raw SQL

import frappe
from frappe import _

# Authoritative role → status mapping. DB-verified strings from
# seed_data.py PROPERTY_SETTERS (MR:184, PO:200) + doctype JSON options.
ROLE_STATUS_MAP = {
	# v2.28.1 — "On Hold" added to every MR approver role: a held MR still needs a
	# human (the holder or anyone at/above the held step) to Resume it, so it must
	# stay visible in the default "My Pending" list view instead of silently
	# dropping out. Every role listed here IS a Review-step role on live routes
	# (Stock User / Department Head / AVP all hold Review steps) and can therefore
	# hold + resume. Read-only impact: list filter + workspace tiles + number cards;
	# no notification/SLA/reminder path reads ROLE_STATUS_MAP.
	"Material Request": {
		"field": "ts_mr_status",
		"roles": {
			"Stock User":       ["Pending Dept. User", "On Hold"],
			"Department Head":  ["Pending Dept. Head", "On Hold"],
			"AVP":              ["Pending AVP", "On Hold"],
			"CEO":              ["Pending CEO", "On Hold"],
			"General Manager":  ["Pending GM", "On Hold"],
		},
	},
	"Purchase Order": {
		"field": "ts_approval_status",
		"roles": {
			"Purchase Manager":       ["Pending PM"],
			"Grain Purchase Manager": ["Pending Grain PM"],
			"Department Head":        ["Pending Dept. Head"],
			"CEO":                    ["Pending CEO"],
			"MD":                     ["Pending MD"],
			"General Manager":        ["Pending GM"],
		},
	},
	"TS Post Dated Entry Request": {
		"field": "status",
		"roles": {
			"CEO": ["Pending Approval"],
			"MD":  ["Pending Approval"],
		},
	},
	"TS Budget Proposal": {
		"field": "status",
		"roles": {
			"CEO": ["Pending CEO"],
		},
	},
	# Production Logging — the single gate is the Store-Manager raw-material release
	# (the CEO variance gate was removed). Surfaces in the Stores Manager inbox.
	"TS Production Entry": {
		"field": "ts_variance_status",
		"roles": {
			"Stores Manager": ["Pending Stores Release"],
		},
	},
}

# Submission-owner field per doctype for the "My Submissions" toggle.
SUBMITTER_FIELD_MAP = {
	"Material Request":          "ts_mr_submitted_by",
	"Purchase Order":            "ts_submitted_by",
	"TS Post Dated Entry Request": None,  # uses owner
	"TS Budget Proposal":        None,    # uses owner
	"TS Production Entry":       "submitted_by",
}


def _feature_enabled():
	"""Kill-switch check (Lesson 191 — Python gate, not JSON reqd)."""
	try:
		val = frappe.db.get_single_value("TS Settings", "ts_my_approvals_enabled")
		if val is None:
			return True  # field not yet seeded — default ON
		return bool(int(val))
	except Exception:
		return True  # fail open — feature is purely additive UX


def _validate_doctype(doctype):
	if doctype not in ROLE_STATUS_MAP:
		frappe.throw(
			_("Unsupported doctype for My Pending Approvals: {0}").format(doctype),
			title=_("Invalid doctype"),
		)


def _guest_check():
	user = frappe.session.user
	if not user or user == "Guest":
		# Return empty instead of throwing — helper is called from
		# client-side rendering paths and we don't want to noise the log.
		return False
	return True


def _get_user_statuses(doctype, user=None):
	"""Internal: return UNION of statuses for all roles the user holds on this doctype."""
	user = user or frappe.session.user
	cfg = ROLE_STATUS_MAP[doctype]
	user_roles = set(frappe.get_roles(user))
	statuses = set()
	for role, role_statuses in cfg["roles"].items():
		if role in user_roles:
			statuses.update(role_statuses)
	return sorted(statuses)


@frappe.whitelist()
def get_user_pending_statuses(doctype):
	"""Return {status_field, statuses:[...]} for current user on given doctype.

	Empty statuses array means user has no approver role on this doctype
	OR feature is kill-switched — caller should NOT apply filter.
	"""
	if not _guest_check():
		return {"status_field": "", "statuses": [], "enabled": False}
	if not _feature_enabled():
		return {"status_field": "", "statuses": [], "enabled": False}
	_validate_doctype(doctype)
	cfg = ROLE_STATUS_MAP[doctype]
	return {
		"status_field": cfg["field"],
		"statuses": _get_user_statuses(doctype),
		"enabled": True,
	}


@frappe.whitelist()
def get_my_pending_counts():
	"""Return count of pending-my-approval docs across the configured doctypes
	(every entry in ROLE_STATUS_MAP — MR / PO / Post-Dated / Budget / Production).

	Used by the 'MR & PO Dashboard' workspace tiles. Returns per-doctype
	count + status list (for hover tooltip) + link to filtered list.
	"""
	if not _guest_check() or not _feature_enabled():
		return {"enabled": False, "items": []}

	user = frappe.session.user
	items = []
	for doctype, cfg in ROLE_STATUS_MAP.items():
		statuses = _get_user_statuses(doctype, user)
		if not statuses:
			continue
		try:
			# Use frappe.get_list() so User Permissions apply — for CC-restricted
			# Dept Heads the count reflects docs they can actually see. This is
			# slightly slower than frappe.db.count() but avoids cross-CC
			# count-leak noted in security scan.
			names = frappe.get_list(
				doctype,
				filters={cfg["field"]: ["in", statuses]},
				pluck="name",
				limit_page_length=0,
				ignore_permissions=False,
			)
			count = len(names or [])
		except Exception:
			# doctype may not exist in certain installs — fail open
			count = 0
		# Build deep-link: /app/material-request?ts_mr_status=["in", [...]]
		# Frappe's list view accepts filter query-string via the list route
		items.append({
			"doctype": doctype,
			"label": doctype,
			"field": cfg["field"],
			"statuses": statuses,
			"count": int(count or 0),
			"list_route": f"/app/{frappe.scrub(doctype).replace('_', '-')}",
		})
	return {"enabled": True, "items": items, "user": user}


def _count_pending_for_me(doctype):
	"""Count pending docs for current user on given doctype.

	Used by Custom Number Card type — returns plain integer (Frappe
	Number Card widget accepts either int or dict with 'value' key).
	"""
	if not _guest_check() or not _feature_enabled():
		return 0
	if doctype not in ROLE_STATUS_MAP:
		return 0
	cfg = ROLE_STATUS_MAP[doctype]
	statuses = _get_user_statuses(doctype)
	if not statuses:
		return 0
	try:
		names = frappe.get_list(
			doctype,
			filters={cfg["field"]: ["in", statuses]},
			pluck="name",
			limit_page_length=0,
			ignore_permissions=False,
		)
		return len(names or [])
	except Exception:
		return 0


@frappe.whitelist()
def count_mr_pending_for_me(**kwargs):
	"""Custom Number Card method: pending Material Requests for current user."""
	return _count_pending_for_me("Material Request")


@frappe.whitelist()
def count_po_pending_for_me(**kwargs):
	"""Custom Number Card method: pending Purchase Orders for current user."""
	return _count_pending_for_me("Purchase Order")


@frappe.whitelist()
def count_post_dated_pending_for_me(**kwargs):
	"""Custom Number Card method: pending Post-Dated Entry Requests for current user."""
	return _count_pending_for_me("TS Post Dated Entry Request")


@frappe.whitelist()
def count_budget_pending_for_me(**kwargs):
	"""Custom Number Card method: pending Budget Proposals for current user."""
	return _count_pending_for_me("TS Budget Proposal")


@frappe.whitelist()
def count_production_release_pending_for_me(**kwargs):
	"""Custom Number Card method: production runs pending raw-material release for the
	current user (Stores Manager). Counts 'Pending Stores Release' entries."""
	return _count_pending_for_me("TS Production Entry")


@frappe.whitelist()
def get_my_submissions_filter(doctype):
	"""Return filter-spec for 'My Submissions' toggle on given doctype.

	Normalizes owner-vs-submitted_by across doctypes so the JS helper
	doesn't need per-doctype knowledge.
	"""
	if not _guest_check() or not _feature_enabled():
		return {"filters": [], "enabled": False}
	_validate_doctype(doctype)
	user = frappe.session.user
	submitter_field = SUBMITTER_FIELD_MAP.get(doctype)
	# Use submitter field when available, else owner.
	if submitter_field:
		filters = [[doctype, submitter_field, "=", user]]
	else:
		filters = [[doctype, "owner", "=", user]]
	return {"filters": filters, "enabled": True, "user": user}
