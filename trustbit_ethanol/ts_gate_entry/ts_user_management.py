"""
User Management API — Secure user creation and role management for IT Head.

5-Layer Security:
  Layer 1: @frappe.whitelist() + IT Head role check
  Layer 2: ALLOWED_ROLES whitelist (only these can be assigned)
  Layer 3: PROTECTED_USERS (cannot edit CEO/MD/Admin)
  Layer 4: Self-edit block (cannot edit own account)
  Layer 5: Audit log (Comment on User doc with real identity)
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, cstr, random_string

# ── HARDCODED CONSTANTS (never configurable via UI) ──────────────────────

ALLOWED_ROLES = [
    "G1 Security",
    "G2 Gate Operator",
    "Weighbridge Operator",
    "Stores User",
    "Quality Inspector",
    "Department Head",
    "Admin Reception",
    "Purchase User",
    "Purchase Manager",
    "Grain Purchase Manager",
    "Accounts User",
    "Accounts Manager",
    "AVP",
    "CEO",
    "Managing Director",
    "Return Item Controller",
    "Return Item Custodian",
    "General Manager",
    "Stock User",
    "Stock Manager",
]

BLOCKED_ROLES = [
    "System Manager",
    "Administrator",
    "Script Manager",
    "Website Manager",
    "Workspace Manager",
    "CTO",
    "Auditor",
]

# Users that cannot be edited/disabled via this page
# CEO, MD, System Manager users + system accounts
PROTECTED_USERS = [
    "Administrator",
    "administrator",
    "Guest",
    "guest",
    "admin@gmail.com",
    "admin2@gmail.com",
    "ra.pandey008@gmail.com",
    "pradeep.modi@betulbiofuel.com",
    "managingdirector@betulbiofuel.com",
    "md@gmail.com",
]

# Auto-add these companion roles when a role is selected
ROLE_DEPENDENCIES = {
    "Purchase Manager": ["Purchase User"],
    "Grain Purchase Manager": ["Purchase User"],
    "Accounts Manager": ["Accounts User"],
    "Quality Inspector": ["Stock User"],
    "Stores User": ["Stock User"],
    "Weighbridge Operator": ["Stock User"],
    "Return Item Controller": ["Stock User"],
}

# Modules blocked for all users created through this page
AUTO_BLOCK_MODULES = [
    "ERPNext Integrations",
    "Integrations",
    "Setup",
    "Website",
    "Workflow",
]

# Max users that can be created per hour (rate limit)
MAX_USERS_PER_HOUR = 5


# ── SECURITY HELPERS ─────────────────────────────────────────────────────

def _check_it_head():
    """Layer 1: Only IT Head can use this API."""
    roles = frappe.get_roles(frappe.session.user)
    if "IT Head" not in roles and "Administrator" not in roles:
        frappe.throw(_("Only IT Head can manage users."), frappe.PermissionError)


def _check_not_self(email):
    """Layer 4: Cannot edit own account."""
    if cstr(email).strip().lower() == cstr(frappe.session.user).strip().lower():
        frappe.throw(_("You cannot modify your own account. Contact Administrator."))


def _check_not_protected(email):
    """Layer 3: Cannot edit protected users."""
    email_lower = cstr(email).strip().lower()
    for p in PROTECTED_USERS:
        if email_lower == p.lower():
            frappe.throw(_("This user account is protected and cannot be modified here."))

    # Also protect users with System Manager or Administrator role
    if frappe.db.exists("User", email):
        user_roles = [r.role for r in frappe.get_doc("User", email).roles]
        if "Administrator" in user_roles:
            frappe.throw(_("This user account is protected and cannot be modified here."))


def _validate_roles(roles):
    """Layer 2: Only allowed roles can be assigned. Strict whitelist."""
    if not roles:
        frappe.throw(_("At least one role must be selected."))

    # Normalize: strip whitespace
    cleaned = [cstr(r).strip() for r in roles if cstr(r).strip()]

    # Get all valid Frappe roles for existence check
    valid_roles = set(frappe.get_all("Role", pluck="name"))

    validated = []
    for role in cleaned:
        # Check against allowed list (exact match, case-sensitive)
        if role not in ALLOWED_ROLES:
            frappe.throw(
                _("Role '{0}' is not allowed. Only operational roles can be assigned.").format(
                    frappe.utils.escape_html(role)
                )
            )
        # Check role actually exists in Frappe
        if role not in valid_roles:
            frappe.throw(
                _("Role '{0}' does not exist in the system.").format(
                    frappe.utils.escape_html(role)
                )
            )
        validated.append(role)

    # Auto-add dependency roles
    extras = set()
    for role in validated:
        deps = ROLE_DEPENDENCIES.get(role, [])
        for dep in deps:
            if dep not in validated and dep in ALLOWED_ROLES:
                extras.add(dep)

    validated.extend(extras)
    return list(set(validated))


def _check_rate_limit():
    """Layer 5 extension: Rate limiting — max N users per hour."""
    one_hour_ago = frappe.utils.add_to_date(now_datetime(), hours=-1)
    recent_count = frappe.db.count("User", filters={
        "creation": [">=", one_hour_ago],
        "owner": frappe.session.user,
    })
    if recent_count >= MAX_USERS_PER_HOUR:
        frappe.throw(
            _("Rate limit: maximum {0} users can be created per hour. Please wait.").format(
                MAX_USERS_PER_HOUR
            )
        )


def _audit_log(user_email, action, details=""):
    """Layer 5: Log every action with real IT Head identity."""
    frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Info",
        "reference_doctype": "User",
        "reference_name": user_email,
        "content": "[User Management] {action} by {by} at {time}{details}".format(
            action=action,
            by=frappe.session.user,
            time=now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
            details=(" — " + details) if details else "",
        ),
    }).insert(ignore_permissions=True)


def _get_pending_work(email):
    """Check if user has pending approvals or active tokens."""
    warnings = []

    # Pending PO approvals (user is next approver)
    pending_pos = frappe.db.count("Purchase Order", filters={
        "docstatus": 0,
        "ts_approval_status": ["like", "Pending%"],
    })
    if pending_pos:
        warnings.append("{0} Purchase Orders in approval pipeline".format(pending_pos))

    # Pending MR approvals
    pending_mrs = frappe.db.count("Material Request", filters={
        "docstatus": 0,
        "ts_mr_status": ["like", "Pending%"],
    })
    if pending_mrs:
        warnings.append("{0} Material Requests in approval pipeline".format(pending_mrs))

    # Active tokens (for gate operators)
    active_tokens = frappe.db.count("TS Token", filters={
        "owner": email,
        "status": ["not in", ["Exited", ""]],
    })
    if active_tokens:
        warnings.append("{0} active gate tokens owned by this user".format(active_tokens))

    return warnings


# ── PUBLIC API ───────────────────────────────────────────────────────────

@frappe.whitelist()
def get_allowed_roles():
    """Return the list of roles IT Head can assign."""
    _check_it_head()
    return sorted(ALLOWED_ROLES)


@frappe.whitelist()
def get_users():
    """Return list of all system users with their roles (for the user table)."""
    _check_it_head()

    users = frappe.get_all(
        "User",
        filters={
            "user_type": "System User",
            "name": ["not in", ["Administrator", "Guest"]],
        },
        fields=["name", "email", "full_name", "first_name", "last_name",
                "enabled", "creation", "last_active", "user_image"],
        order_by="full_name asc",
        limit_page_length=0,
    )

    # Get roles for each user
    for user in users:
        user_roles = frappe.get_all(
            "Has Role",
            filters={"parent": user.name, "parenttype": "User"},
            pluck="role",
        )
        # Only show allowed roles (don't expose system roles)
        user["roles"] = sorted([r for r in user_roles if r in ALLOWED_ROLES])
        user["has_system_roles"] = any(r in BLOCKED_ROLES for r in user_roles)
        user["is_protected"] = (
            user.name.lower() in [p.lower() for p in PROTECTED_USERS]
            or "Administrator" in user_roles
        )
        user["is_self"] = user.name.lower() == frappe.session.user.lower()

    return users


@frappe.whitelist()
def create_user(first_name, email, roles, last_name=None, mobile_no=None, send_welcome_email=0):
    """Create a new user with specified operational roles."""
    _check_it_head()
    _check_rate_limit()

    # Parse roles from JSON string
    if isinstance(roles, str):
        import json
        roles = json.loads(roles)

    # Validate roles (Layer 2)
    validated_roles = _validate_roles(roles)

    # Validate email
    email = cstr(email).strip().lower()
    if not email or "@" not in email:
        frappe.throw(_("Please enter a valid email address."))

    if frappe.db.exists("User", email):
        frappe.throw(_("A user with email '{0}' already exists.").format(
            frappe.utils.escape_html(email)
        ))

    first_name = cstr(first_name).strip()
    if not first_name:
        frappe.throw(_("First Name is required."))

    # Check if email sending is possible
    send_email = int(send_welcome_email or 0)
    if send_email:
        has_email_account = frappe.db.exists("Email Account", {"enable_outgoing": 1})
        if not has_email_account:
            send_email = 0

    # Create user
    user = frappe.new_doc("User")
    user.email = email
    user.first_name = first_name
    user.last_name = cstr(last_name).strip() or None
    user.mobile_no = cstr(mobile_no).strip() or None
    user.user_type = "System User"
    user.send_welcome_email = send_email
    user.enabled = 1

    # If not sending welcome email, generate a temporary password
    temp_password = None
    if not send_email:
        temp_password = random_string(12)
        user.new_password = temp_password

    # Assign validated roles
    for role in validated_roles:
        user.append("roles", {"role": role})

    # Block modules
    for mod in AUTO_BLOCK_MODULES:
        user.append("block_modules", {"module": mod})

    user.flags.ignore_permissions = True
    user.flags.no_welcome_mail = not send_email
    user.insert()

    _audit_log(email, "User created", "Roles: {0}".format(", ".join(validated_roles)))
    frappe.db.commit()

    result = {
        "user": email,
        "full_name": user.full_name,
        "roles": validated_roles,
        "send_welcome_email": bool(send_email),
    }

    if temp_password and not send_email:
        result["temp_password"] = temp_password
        result["must_change_password"] = True

    return result


@frappe.whitelist()
def update_user_roles(email, roles):
    """Update roles for an existing user. Only operational roles can be changed."""
    _check_it_head()
    _check_not_self(email)
    _check_not_protected(email)

    if isinstance(roles, str):
        import json
        roles = json.loads(roles)

    validated_roles = _validate_roles(roles)

    if not frappe.db.exists("User", email):
        frappe.throw(_("User '{0}' does not exist.").format(frappe.utils.escape_html(email)))

    user = frappe.get_doc("User", email)

    # Preserve system/non-allowed roles that we don't manage
    preserved_roles = [r.role for r in user.roles if r.role not in ALLOWED_ROLES]

    # Rebuild roles: preserved (untouched) + new validated operational roles
    user.roles = []
    for role in preserved_roles:
        user.append("roles", {"role": role})
    for role in validated_roles:
        user.append("roles", {"role": role})

    # Ensure block_modules are set
    existing_blocked = [m.module for m in user.block_modules]
    for mod in AUTO_BLOCK_MODULES:
        if mod not in existing_blocked:
            user.append("block_modules", {"module": mod})

    user.flags.ignore_permissions = True
    user.save()

    _audit_log(email, "Roles updated", "New roles: {0}".format(", ".join(validated_roles)))
    frappe.db.commit()

    return {"user": email, "roles": validated_roles, "preserved_roles": preserved_roles}


@frappe.whitelist()
def toggle_user(email, enabled):
    """Enable or disable a user."""
    _check_it_head()
    _check_not_self(email)
    _check_not_protected(email)

    enabled = int(enabled)

    if not frappe.db.exists("User", email):
        frappe.throw(_("User '{0}' does not exist.").format(frappe.utils.escape_html(email)))

    # If disabling, check for pending work
    warnings = []
    if not enabled:
        warnings = _get_pending_work(email)

    user = frappe.get_doc("User", email)
    user.enabled = enabled
    user.flags.ignore_permissions = True
    user.save()

    action = "User enabled" if enabled else "User disabled"
    _audit_log(email, action)
    frappe.db.commit()

    return {"user": email, "enabled": enabled, "warnings": warnings}


@frappe.whitelist()
def reset_password(email):
    """Send password reset email to user."""
    _check_it_head()
    _check_not_self(email)
    _check_not_protected(email)

    if not frappe.db.exists("User", email):
        frappe.throw(_("User '{0}' does not exist.").format(frappe.utils.escape_html(email)))

    # Check if email sending is possible
    has_email_account = frappe.db.exists("Email Account", {"enable_outgoing": 1})
    if not has_email_account:
        # Fallback: generate new password
        temp_password = random_string(12)
        user = frappe.get_doc("User", email)
        user.new_password = temp_password
        user.flags.ignore_permissions = True
        user.save()
        _audit_log(email, "Password reset (generated)")
        frappe.db.commit()
        return {"user": email, "method": "generated", "temp_password": temp_password}

    from frappe.utils import get_url
    frappe.sendmail(
        recipients=[email],
        subject=_("Password Reset — Betul Biofuel ERP"),
        message=_(
            "Your password has been reset by IT Admin. "
            "Please click the link below to set a new password:<br><br>"
            "<a href='{0}/update-password?key={1}'>Set New Password</a>"
        ).format(get_url(), frappe.generate_hash(email, 20)),
    )
    _audit_log(email, "Password reset email sent")
    frappe.db.commit()
    return {"user": email, "method": "email_sent"}


@frappe.whitelist()
def get_user_detail(email):
    """Get full detail of a single user for editing."""
    _check_it_head()

    if not frappe.db.exists("User", email):
        frappe.throw(_("User '{0}' does not exist.").format(frappe.utils.escape_html(email)))

    user = frappe.get_doc("User", email)

    all_roles = [r.role for r in user.roles]
    operational_roles = sorted([r for r in all_roles if r in ALLOWED_ROLES])

    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.full_name,
        "mobile_no": user.mobile_no,
        "enabled": user.enabled,
        "user_image": user.user_image,
        "creation": user.creation,
        "last_active": user.last_active,
        "roles": operational_roles,
        "has_system_roles": any(r in BLOCKED_ROLES for r in all_roles),
        "is_protected": user.email.lower() in [p.lower() for p in PROTECTED_USERS] or "Administrator" in all_roles,
        "is_self": user.email.lower() == frappe.session.user.lower(),
    }
