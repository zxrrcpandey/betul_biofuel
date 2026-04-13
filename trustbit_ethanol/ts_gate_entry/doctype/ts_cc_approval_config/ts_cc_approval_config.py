import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


# Roles that should NEVER get CC-based User Permissions (they see everything)
# Purchase User included because purchase team needs to see ALL MRs to create POs (Lesson 145)
UNRESTRICTED_ROLES = {
	"System Manager", "Administrator", "MD", "CEO", "AVP", "IT Head",
	"Accounts Manager", "Accounts User", "Purchase Manager",
	"Grain Purchase Manager", "Purchase User",
}

# Custom field name on User Permission to tag auto-generated ones
UP_MARKER_FIELD = "ts_cc_auto_generated"

# DocTypes to isolate by Cost Center — one User Permission per doctype per user per CC
# MUST be explicit per-doctype (never empty applicable_for — that restricts ALL doctypes)
# NOTE: Material Request Item is included because Frappe's User Permission check
# runs on EVERY Link field including child table fields. Without MR Item here,
# users get "Not permitted" when saving MR with cost_center on item rows (Lesson 150).
ISOLATED_DOCTYPES = ["Material Request", "Material Request Item", "Budget", "TS Budget Proposal"]


class TSCCApprovalConfig(Document):
	def validate(self):
		self._validate_users()
		self._validate_route_steps()
		self._auto_set_flags()

	def after_insert(self):
		self._sync_user_permissions()

	def on_update(self):
		self._sync_user_permissions()

	def on_trash(self):
		self._remove_user_permissions()

	def _validate_users(self):
		"""Validate user mapping."""
		# At least one Creator for Standard flow
		if self.flow_type == "Standard":
			creators = [r for r in self.users if r.action_type == "Creator"]
			if not creators:
				frappe.throw(
					_("At least one user with Action Type 'Creator' is required for Standard MR flow."),
					title=_("Missing Creator"),
				)

		# Check for duplicate user + action_type
		seen = set()
		for row in self.users:
			key = (row.user, row.action_type)
			if key in seen:
				frappe.throw(
					_("Duplicate entry: {0} is already listed as {1}.").format(
						frappe.utils.escape_html(row.user_full_name or row.user),
						frappe.utils.escape_html(row.action_type),
					),
					title=_("Duplicate User"),
				)
			seen.add(key)

		# Validate role is set for Reviewer/Final Approver
		for row in self.users:
			if row.action_type in ("Reviewer", "Final Approver") and not row.role:
				frappe.throw(
					_("Role is required for {0} ({1}). Please select the approval role.").format(
						frappe.utils.escape_html(row.user_full_name or row.user),
						row.action_type,
					),
					title=_("Missing Role"),
				)

		# Validate step_order > 0 for Reviewer/Final Approver
		for row in self.users:
			if row.action_type in ("Reviewer", "Final Approver") and cint(row.step_order) <= 0:
				frappe.throw(
					_("Step Order must be > 0 for {0} ({1}).").format(
						frappe.utils.escape_html(row.user_full_name or row.user),
						row.action_type,
					),
					title=_("Invalid Step Order"),
				)

		# Validate users are enabled
		for row in self.users:
			if row.user and not frappe.db.get_value("User", row.user, "enabled"):
				frappe.msgprint(
					_("Warning: {0} ({1}) is a disabled user.").format(
						frappe.utils.escape_html(row.user_full_name or row.user),
						row.user,
					),
					indicator="orange",
				)

	def _validate_route_steps(self):
		"""Validate step_order values match the linked MR Approval Route."""
		if not self.mr_approval_route or self.flow_type == "Direct PO":
			return

		route_doc = frappe.get_doc("TS MR Approval Route", self.mr_approval_route)
		valid_steps = {s.step_order for s in route_doc.approval_steps}

		for row in self.users:
			if row.step_order and cint(row.step_order) > 0 and cint(row.step_order) not in valid_steps:
				frappe.throw(
					_("Step Order {0} for {1} does not exist in route '{2}'. Valid steps: {3}").format(
						row.step_order, frappe.utils.escape_html(row.user_full_name or row.user),
						self.mr_approval_route, ", ".join(str(s) for s in sorted(valid_steps))
					),
					title=_("Invalid Step Order"),
				)

	def _auto_set_flags(self):
		"""Auto-set can_create_mr and can_approve based on action_type.
		These are derived fields — always recalculated from action_type.
		"""
		for row in self.users:
			if row.action_type == "Creator":
				row.can_create_mr = 1
				row.can_approve = 0
				if not row.step_order:
					row.step_order = 0
			elif row.action_type in ("Reviewer", "Final Approver"):
				row.can_create_mr = 0
				row.can_approve = 1
			elif row.action_type == "Notify Only":
				row.can_create_mr = 0
				row.can_approve = 0
				if not row.step_order:
					row.step_order = 0
			# Ensure gets_notified defaults to 1
			if not row.gets_notified and row.gets_notified != 0:
				row.gets_notified = 1

	# ─── User Permission Management ───────────────────────────────────

	def _sync_user_permissions(self):
		"""Create/update User Permissions for CC-based data isolation."""
		if not self.is_active:
			self._remove_user_permissions()
			return

		# Get all users from this config
		config_users = set()
		for row in self.users:
			if row.user:
				config_users.add(row.user)

		# Get existing auto-generated permissions for this CC
		existing = self._get_auto_permissions()
		existing_users = {}  # user -> list of perm names
		for e in existing:
			existing_users.setdefault(e.user, []).append(e.name)

		# Create permissions for users who need them
		for user in config_users:
			# Skip disabled users
			if not frappe.db.get_value("User", user, "enabled"):
				continue
			# Skip users with unrestricted roles
			if self._user_has_unrestricted_role(user):
				continue
			if user not in existing_users:
				self._create_user_permission(user)

		# Remove permissions for users no longer in this config
		for user, perm_names in existing_users.items():
			if user not in config_users:
				# Only remove THIS CC's permissions if user has no other active CC configs for same CC
				other_cc_count = frappe.db.sql("""
					SELECT COUNT(DISTINCT ccu.parent) as cnt
					FROM `tabTS CC Approval User` ccu
					INNER JOIN `tabTS CC Approval Config` ccc ON ccc.name = ccu.parent
					WHERE ccu.user = %s
						AND ccu.parenttype = 'TS CC Approval Config'
						AND ccc.is_active = 1
						AND ccc.name != %s
						AND ccc.cost_center = %s
				""", (user, self.name, self.cost_center))[0][0] or 0

				if other_cc_count == 0:
					for perm_name in perm_names:
						frappe.delete_doc("User Permission", perm_name, ignore_permissions=True)

	def _get_auto_permissions(self):
		"""Get auto-generated User Permissions for this CC.
		Uses a dedicated marker comment to identify auto-generated ones.
		"""
		# Find User Permissions for this CC that have our marker comment
		perms = frappe.get_all(
			"User Permission",
			filters={
				"allow": "Cost Center",
				"for_value": self.cost_center,
			},
			fields=["name", "user"],
		)

		# Filter to only auto-generated ones (those with our marker comment)
		auto_perms = []
		for p in perms:
			has_marker = frappe.db.exists("Comment", {
				"reference_doctype": "User Permission",
				"reference_name": p.name,
				"comment_type": "Comment",
				"content": UP_MARKER_FIELD,
			})
			if has_marker:
				auto_perms.append(p)

		return auto_perms

	def _create_user_permission(self, user):
		"""Create per-doctype User Permissions for Cost Center access.
		Creates one User Permission for EACH doctype in ISOLATED_DOCTYPES.
		NEVER creates with empty applicable_for (that restricts ALL doctypes).
		"""
		for dt in ISOLATED_DOCTYPES:
			# Check if already exists for this user + CC + doctype
			exists = frappe.db.exists(
				"User Permission",
				{
					"user": user,
					"allow": "Cost Center",
					"for_value": self.cost_center,
					"applicable_for": dt,
				},
			)
			if exists:
				continue

			up = frappe.get_doc({
				"doctype": "User Permission",
				"user": user,
				"allow": "Cost Center",
				"for_value": self.cost_center,
				"apply_to_all_doctypes": 0,
				"applicable_for": dt,
			})
			up.insert(ignore_permissions=True)
			# Tag with marker comment for cleanup tracking
			frappe.get_doc({
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "User Permission",
				"reference_name": up.name,
				"content": UP_MARKER_FIELD,
			}).insert(ignore_permissions=True)

	def _remove_user_permissions(self):
		"""Remove auto-generated User Permissions for this CC."""
		for perm in self._get_auto_permissions():
			frappe.delete_doc("User Permission", perm.name, ignore_permissions=True)

	def _user_has_unrestricted_role(self, user):
		"""Check if user has any role that exempts them from CC restriction."""
		user_roles = set(frappe.get_roles(user))
		return bool(user_roles & UNRESTRICTED_ROLES)


# ═══════════════════════════════════════════════════════════════════════
#  Public Helper APIs
# ═══════════════════════════════════════════════════════════════════════

def get_cc_config(cost_center):
	"""Get active TS CC Approval Config for a cost center, or None."""
	if not cost_center:
		return None
	try:
		config = frappe.get_doc("TS CC Approval Config", cost_center)
		if config.is_active:
			return config
	except frappe.DoesNotExistError:
		pass
	return None


def get_cc_users_for_step(cc_config, step_order):
	"""Get users from CC config for a specific step who should be notified.
	Returns empty list if no users found (caller should fall back to role-based).
	"""
	if not cc_config:
		return []
	users = []
	for row in cc_config.users:
		if cint(row.step_order) == cint(step_order) and row.gets_notified:
			users.append(row.user)
	return users


def get_cc_approvers_for_step(cc_config, step_order):
	"""Get users from CC config who can approve at a specific step.
	Returns empty list if no approvers configured (means: no CC restriction for this step).
	"""
	if not cc_config:
		return []
	users = []
	for row in cc_config.users:
		if cint(row.step_order) == cint(step_order) and row.can_approve:
			users.append(row.user)
	return users


def get_cc_creators(cc_config):
	"""Get users who can create MRs for this CC."""
	if not cc_config:
		return []
	return [row.user for row in cc_config.users if row.can_create_mr]


def get_cc_notify_only_users(cc_config):
	"""Get users who should be notified on final approval (Notify Only type only)."""
	if not cc_config:
		return []
	users = []
	for row in cc_config.users:
		if row.action_type == "Notify Only" and row.gets_notified:
			users.append(row.user)
	return users


@frappe.whitelist()
def get_user_allowed_cost_centers(user=None):
	"""Get list of Cost Centers where CURRENT user is configured as Creator.
	Used by MR form to filter CC dropdown.
	Returns empty list if user has unrestricted role (shows all CCs).
	Always uses session user for security — ignores user parameter.
	"""
	user = frappe.session.user  # Always use session user (security)
	user_roles = set(frappe.get_roles(user))

	# Unrestricted roles see all CCs
	if user_roles & UNRESTRICTED_ROLES:
		return []

	# Check if any CC configs exist at all
	config_count = frappe.db.count("TS CC Approval Config", {"is_active": 1})
	if config_count == 0:
		return []  # No configs = no restriction (backward compat)

	# Get CCs where user is Creator
	ccs = frappe.db.sql("""
		SELECT DISTINCT parent as cost_center
		FROM `tabTS CC Approval User`
		WHERE user = %s
			AND can_create_mr = 1
			AND parenttype = 'TS CC Approval Config'
			AND parent IN (
				SELECT name FROM `tabTS CC Approval Config`
				WHERE is_active = 1
			)
	""", user, as_dict=True)

	return [c.cost_center for c in ccs]


@frappe.whitelist()
def check_direct_po_cc(cost_center):
	"""Check if a Cost Center is configured as Direct PO (no MR allowed).
	Returns dict with is_direct_po and message.
	"""
	if not cost_center:
		return {"is_direct_po": False}

	config = get_cc_config(cost_center)
	if config and config.flow_type == "Direct PO":
		return {
			"is_direct_po": True,
			"message": _("{0} uses Direct PO. Please create a Purchase Order directly — no Material Request needed.").format(
				frappe.utils.escape_html(cost_center)
			),
		}
	return {"is_direct_po": False}


def cleanup_unrestricted_user_permissions():
	"""Remove CC User Permissions for users who have unrestricted roles.
	Called from after_migrate to fix stale permissions.
	Lesson 145: Purchase team users must not have CC restrictions on MR."""
	removed = 0
	for doctype in ISOLATED_DOCTYPES:
		perms = frappe.get_all("User Permission", filters={
			"allow": "Cost Center",
			"applicable_for": doctype,
		}, fields=["name", "user"])

		for perm in perms:
			user_roles = set(frappe.get_roles(perm.user))
			if user_roles & UNRESTRICTED_ROLES:
				frappe.delete_doc("User Permission", perm.name, ignore_permissions=True)
				removed += 1

	if removed:
		frappe.db.commit()
