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
from frappe.utils import now_datetime, cstr

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
    "Return Item Controller",
    "Return Item Custodian",
    "Stock User",
    "Stock Manager",
]

# Privileged roles. This page may neither GRANT these nor MANAGE (disable /
# password-reset) an account that holds one — see _check_not_protected.
#
# ⚠ Every name here must exist in `tabRole`; a role name that does not exist is a
# silent no-op that reads as protection while protecting nothing. Verified against
# production 8 Aug 2026: the executive role this app actually uses is "MD" (11
# holders) — "Managing Director" does NOT exist and had been the only entry
# standing for the MD role, so MD-holders were fully manageable from here. Both
# spellings are retained (notification_center_api.py covers both the same way) so
# a server that later creates the long form is still covered.
#
# Deliberately NOT listed: operational roles such as "Stores Manager" (58 holders)
# and "Grain Manager" (4). Those are ordinary staff roles — blocking them would
# make most of the workforce unmanageable from this page, which is the opposite of
# what it is for.
BLOCKED_ROLES = [
    "System Manager",
    "Administrator",
    "Super Admin",
    "Script Manager",
    "Website Manager",
    "Workspace Manager",
    "CTO",
    "Auditor",
    "CEO",
    "MD",
    "Managing Director",
    "AVP",
    "General Manager",
    "IT Head",
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

# Max users one IT Head can create per hour.
#
# This limit was dormant for its whole life: the counter filters on
# `owner = session user`, but create_user inserts while the session is elevated to
# Administrator, so every account was owned by Administrator and the count was
# always 0. create_user now restores real ownership, which makes this fire for the
# first time — so the number has to be an operationally realistic one. Onboarding a
# full gate shift (G1, G2, Weighbridge, Stores, QI across shifts) is routinely
# 10-15 accounts in one sitting, and the original 5 would have hard-stopped a
# legitimate batch. 20 still stops a runaway loop or a hijacked session.
MAX_USERS_PER_HOUR = 20


# ── SECURITY HELPERS ─────────────────────────────────────────────────────

def _require_post():
    """Re-assert POST inside the function body (Lesson 366).

    `@frappe.whitelist(methods=["POST"])` is enforced by handler.py against the
    TOP-LEVEL `cmd` only. Core's mapper trampolines — `frappe.model.mapper.map_docs`
    and `make_mapped_doc` — are bare `@frappe.whitelist()` (all verbs) and gate
    solely on `method in frappe.whitelisted`; neither calls `is_valid_http_method`.
    So any whitelisted function is re-enterable by GET through them, and
    `validate_csrf_token` (auth.py) returns early on GET. That makes every mutator
    here CSRF-reachable via
    `/api/method/frappe.model.mapper.map_docs?method=<this>&source_names=[...]`
    despite the decorator.

    `request` is None for background jobs, bench console and tests — those stay
    allowed.
    """
    req = getattr(frappe.local, "request", None)
    if req is not None and req.method != "POST":
        raise frappe.PermissionError


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
    """Layer 3: Cannot edit protected users.

    Two independent guards:
      1. the literal PROTECTED_USERS address list (system + named executive accounts)
      2. any account holding a BLOCKED_ROLE — the privileged roles this page may not
         grant are also the ones whose holders it may not disable or password-reset.

    Testing only "Administrator" here was a no-op: Frappe treats Administrator as the
    superuser account rather than a role granted to humans (0 holders on production),
    so every System Manager / CEO / MD / AVP / General Manager account fell straight
    through to an editable state and the protection rested entirely on the hardcoded
    address list.
    """
    email_lower = cstr(email).strip().lower()
    for p in PROTECTED_USERS:
        if email_lower == p.lower():
            frappe.throw(_("This user account is protected and cannot be modified here."))

    if frappe.db.exists("User", email):
        user_roles = frappe.get_all(
            "Has Role",
            filters={"parent": email, "parenttype": "User"},
            pluck="role",
        )
        held = sorted(set(user_roles) & set(BLOCKED_ROLES))
        if held:
            frappe.throw(
                _("This user holds a privileged role ({0}) and cannot be modified here.").format(
                    frappe.utils.escape_html(", ".join(held))
                )
            )


def _validate_roles(roles):
    """Layer 2: Only allowed roles can be assigned. Strict whitelist."""
    if not roles:
        frappe.throw(_("At least one role must be selected."))

    # Normalize: strip whitespace, remove None/empty
    cleaned = [cstr(r).strip() for r in roles if cstr(r).strip()]
    if not cleaned:
        frappe.throw(_("At least one role must be selected."))

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
        oldest = frappe.db.get_value(
            "User",
            {"creation": [">=", one_hour_ago], "owner": frappe.session.user},
            "creation",
            order_by="creation asc",
        )
        retry_at = frappe.utils.add_to_date(oldest, hours=1) if oldest else None
        frappe.throw(
            _("Rate limit: you can create {0} users per hour. You can add more after {1}.").format(
                MAX_USERS_PER_HOUR,
                frappe.utils.format_datetime(retry_at) if retry_at else _("the hour elapses"),
            )
        )


def _reset_link_expiry_label():
    """Human-readable lifetime of a password-reset link.

    `reset_password_link_expiry_duration` is a Duration field in SECONDS
    (frappe default 1200 = 20 minutes). 0/unset means the link never expires.
    The IT Head has to know this before sending it to someone off-shift.
    """
    seconds = frappe.utils.cint(
        frappe.db.get_single_value("System Settings", "reset_password_link_expiry_duration")
    )
    if not seconds:
        return _("no expiry")
    if seconds < 3600:
        return _("{0} minutes").format(int(round(seconds / 60.0)) or 1)
    hours = seconds / 3600.0
    return _("{0} hours").format(int(hours) if hours == int(hours) else round(hours, 1))


def _audit_log(user_email, action, details="", by=None):
    """Layer 5: Log every action with real IT Head identity.

    `by` overrides the session actor. Needed for events recorded during the
    /update-password request, which runs as GUEST (update_password is
    allow_guest=True), so frappe.session.user there is not the person the entry
    is actually about.

    The Comment's OWNER is set to the same actor. The desk timeline renders the
    owner as the author of the line, so leaving it as the session user made a
    correctly-worded entry display as "Guest [User Management] Password changed
    by <real user>" — the prefix contradicting the text right next to it.
    """
    actor = by or frappe.session.user
    doc = frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Info",
        "reference_doctype": "User",
        "reference_name": user_email,
        "content": "[User Management] {action} by {by} at {time}{details}".format(
            action=action,
            by=actor,
            time=now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
            details=(" — " + details) if details else "",
        ),
    })
    doc.insert(ignore_permissions=True)

    # owner is set by set_user_and_timestamp() at insert from the session, so it
    # has to be corrected afterwards (same mechanism as create_user's owner fix).
    if actor and actor != doc.owner and frappe.db.exists("User", actor):
        frappe.db.set_value("Comment", doc.name, "owner", actor, update_modified=False)
    return doc


def _sole_approver_roles(email):
    """Roles this user holds that NO other enabled user holds.

    Disabling the only holder of an approver role stalls every document routed
    to it, which is the one offboarding risk worth interrupting the IT Head for.
    """
    held = frappe.get_all(
        "Has Role", filters={"parent": email, "parenttype": "User"}, pluck="role"
    )
    sole = []
    for role in held:
        others = frappe.db.sql(
            """
            SELECT COUNT(*) FROM `tabHas Role` hr
            INNER JOIN `tabUser` u ON u.name = hr.parent
            WHERE hr.role = %s AND hr.parenttype = 'User'
              AND u.enabled = 1 AND u.name != %s
            """,
            (role, email),
        )[0][0]
        if not others:
            sole.append(role)
    return sorted(sole)


def _get_pending_work(email):
    """Warnings for disabling `email` — scoped to THIS user, not the whole company.

    The counts below were previously unfiltered, so every disable reported the
    same company-wide totals and the warning carried no information. Approval
    routing is role-based, so "their" pending work is the set of documents
    sitting at a status one of their roles is the approver for. ROLE_STATUS_MAP
    in ts_my_approvals_api is the authoritative status↔role mapping; reuse it
    rather than restating the vocabulary here.
    """
    from trustbit_ethanol.ts_gate_entry.ts_my_approvals_api import (
        ROLE_STATUS_MAP,
        _get_user_statuses,
    )

    warnings = []

    for doctype, label in (("Purchase Order", "Purchase Orders"),
                           ("Material Request", "Material Requests")):
        try:
            cfg = ROLE_STATUS_MAP[doctype]
            field = cfg["field"]

            statuses = _get_user_statuses(doctype, email)
            if statuses:
                count = frappe.db.count(doctype, filters={
                    "docstatus": 0,
                    field: ["in", statuses],
                })
                if count:
                    warnings.append(
                        "{0} {1} awaiting this user's approval step".format(count, label)
                    )

            # ROLE_STATUS_MAP is EXACT-match, so a pending status the map has not
            # caught up with would silently count as zero — fail-open at the worst
            # possible moment. But warning about EVERY unmapped status re-creates
            # the cry-wolf problem this function is being fixed for: demo's live
            # vocabulary carries "Pending Budget Override" and "Pending Stores
            # Manager", neither of which concerns a gate operator.
            #
            # These statuses are spelled "Pending <Role>", so an unmapped one is
            # THIS user's problem precisely when they hold the named role. That
            # closes the gap without inventing noise.
            known = {s for role_statuses in cfg["roles"].values() for s in role_statuses}
            live = {
                r[0] for r in frappe.db.sql(
                    "SELECT DISTINCT `{0}` FROM `tab{1}` WHERE docstatus = 0 "
                    "AND `{0}` LIKE 'Pending%%'".format(field, doctype)
                ) if r[0]
            }
            user_roles = set(frappe.get_roles(email))
            mine_unmapped = [
                s for s in (live - known)
                if s[len("Pending "):].strip() in user_roles
            ]
            if mine_unmapped:
                count = frappe.db.count(doctype, filters={
                    "docstatus": 0,
                    field: ["in", mine_unmapped],
                })
                if count:
                    warnings.append(
                        "{0} {1} awaiting this user at {2} (status not yet in the "
                        "approval map — please verify).".format(
                            count, label, ", ".join(sorted(mine_unmapped))
                        )
                    )
        except Exception:
            # Never block a disable on a reporting failure, but do not pretend
            # there is no pending work either. log_error is itself a DB insert
            # and can throw on a poisoned transaction — nest it, same as the
            # login-path handler.
            try:
                frappe.log_error(
                    title="User Management: pending-work check failed",
                    message=frappe.get_traceback(),
                )
            except Exception:
                pass
            warnings.append(
                "Could not verify pending {0} for this user — check manually.".format(label)
            )

    # Active tokens (for gate operators). "Plant Exited" is deliberately NOT
    # terminal — the vehicle has left the plant but is still on campus and
    # g1_final_exit still has to run.
    active_tokens = frappe.db.count("TS Token", filters={
        "owner": email,
        "status": ["not in", ["Exited", "Campus Exited", ""]],
    })
    if active_tokens:
        warnings.append("{0} active gate tokens owned by this user".format(active_tokens))

    sole = _sole_approver_roles(email)
    if sole:
        warnings.append(
            "This user is the ONLY enabled holder of: {0}. "
            "Assign these roles to someone else first or approvals will stall.".format(
                ", ".join(sole)
            )
        )

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
        # Must mirror _check_not_protected exactly. If this is narrower than the
        # server guard, the page renders Edit/Disable/Reset buttons that always
        # throw — the user-facing half of the same rule has to move with it.
        user["is_protected"] = (
            user.name.lower() in [p.lower() for p in PROTECTED_USERS]
            or user["has_system_roles"]
        )
        user["is_self"] = user.name.lower() == frappe.session.user.lower()

    return users


def _normalize_whatsapp(whatsapp_number, opt_in):
    """Validate + normalize a WhatsApp number to 91XXXXXXXXXX and return the
    User field values to set. Empty input clears the number + opt-in."""
    from trustbit_ethanol.ts_gate_entry.ts_whatsapp_recipients import normalize_msisdn
    raw = cstr(whatsapp_number).strip()
    if not raw:
        return {"ts_whatsapp_number": None, "ts_whatsapp_opt_in": 0}
    number = normalize_msisdn(raw)
    if not number:
        frappe.throw(_("WhatsApp Number '{0}' is not valid. Use country code + number, e.g. 919812345678.").format(
            frappe.utils.escape_html(raw)
        ))
    opted = 1 if int(opt_in or 0) else 0
    values = {"ts_whatsapp_number": number, "ts_whatsapp_opt_in": opted}
    if opted:
        values["ts_whatsapp_opted_out_at"] = None
    return values


@frappe.whitelist(methods=["POST"])
def create_user(first_name, email, roles, last_name=None, mobile_no=None, send_welcome_email=0,
                whatsapp_number=None, whatsapp_opt_in=0):
    """Create a new user with specified operational roles."""
    _require_post()
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

    # Only claim we can email if there is a DEFAULT outgoing account — an
    # enabled-but-not-default account cannot actually send, and silently taking
    # the email path leaves the new user with no password anywhere.
    send_email = int(send_welcome_email or 0)
    if send_email:
        has_email_account = frappe.db.exists(
            "Email Account", {"enable_outgoing": 1, "default_outgoing": 1}
        )
        if not has_email_account:
            send_email = 0

    # Create user — run as Administrator to avoid Frappe internal permission
    # checks (e.g., Notification Settings save in check_enable_disable)
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        user = frappe.new_doc("User")
        user.email = email
        user.first_name = first_name
        user.last_name = cstr(last_name).strip() or None
        user.mobile_no = cstr(mobile_no).strip() or None
        user.update(_normalize_whatsapp(whatsapp_number, whatsapp_opt_in))
        user.user_type = "System User"
        user.send_welcome_email = send_email
        user.enabled = 1

        # No temporary password. When we cannot email, the IT Head is given a
        # one-time reset LINK after insert (see below) — the user then sets their
        # own password on frappe's "password expired" page. A temp password would
        # have to be explained, and nothing in frappe enforces changing it.
        #
        # The account is left with NO usable password until that link is used,
        # which is the intended state: it cannot be logged into by anyone.

        # Assign validated roles
        for role in validated_roles:
            user.append("roles", {"role": role})

        # Block modules
        for mod in AUTO_BLOCK_MODULES:
            user.append("block_modules", {"module": mod})

        user.flags.ignore_permissions = True
        user.flags.no_welcome_mail = not send_email
        user.insert()

        # Attribute the account to the IT Head who actually created it. The insert
        # runs while the session is Administrator, and set_user_and_timestamp()
        # stamps owner = session user, so every account made here was owned by
        # "Administrator" — losing the audit trail AND silently disabling
        # _check_rate_limit(), which counts by owner.
        frappe.db.set_value(
            "User", user.name, "owner", original_user, update_modified=False
        )

        # When we cannot email, mint the one-time link the IT Head will pass on.
        reset_link = None
        if not send_email:
            reset_link = user.reset_password(send_email=False, password_expired=True)

        frappe.db.commit()
    finally:
        frappe.set_user(original_user)

    _audit_log(email, "User created", "Roles: {0}".format(", ".join(validated_roles)))
    if reset_link:
        # Must use the SAME wording reset_password() logs. _link_issuer() matches
        # on this exact prefix to name the issuer when the link is later consumed;
        # without it, an account onboarded here reports its first password change
        # as "self-service" and the IT Head who actually issued the link vanishes
        # from the trail.
        _audit_log(email, "Password reset link generated (shared by IT Head)",
                   "Initial credential for the new account")
    frappe.db.commit()

    result = {
        "user": email,
        "full_name": user.full_name,
        "roles": validated_roles,
        "send_welcome_email": bool(send_email),
    }

    if reset_link:
        result["reset_link"] = reset_link
        result["expires_in"] = _reset_link_expiry_label()

    return result


@frappe.whitelist(methods=["POST"])
def update_user_roles(email, roles):
    """Update roles for an existing user. Only operational roles can be changed."""
    _require_post()
    _check_it_head()
    _check_not_self(email)
    _check_not_protected(email)

    if isinstance(roles, str):
        import json
        roles = json.loads(roles)

    validated_roles = _validate_roles(roles)

    if not frappe.db.exists("User", email):
        frappe.throw(_("User '{0}' does not exist.").format(frappe.utils.escape_html(email)))

    # Run as Administrator for Frappe internal permission checks
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
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
        frappe.db.commit()
    finally:
        frappe.set_user(original_user)

    _audit_log(email, "Roles updated", "New roles: {0}".format(", ".join(validated_roles)))
    frappe.db.commit()

    return {"user": email, "roles": validated_roles, "preserved_roles": preserved_roles}


@frappe.whitelist(methods=["POST"])
def toggle_user(email, enabled):
    """Enable or disable a user."""
    _require_post()
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

    # Frappe's check_enable_disable() calls toggle_notifications() which
    # saves Notification Settings — requires System Manager. Run as Admin.
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        user = frappe.get_doc("User", email)
        user.enabled = enabled
        user.flags.ignore_permissions = True
        user.save()
        frappe.db.commit()
    finally:
        frappe.set_user(original_user)

    action = "User enabled" if enabled else "User disabled"
    _audit_log(email, action)
    frappe.db.commit()

    return {"user": email, "enabled": enabled, "warnings": warnings}


@frappe.whitelist(methods=["POST"])
def reset_own_password(current_password, new_password):
    """Allow IT Head to change their own password."""
    _require_post()
    _check_it_head()

    email = frappe.session.user
    if not email or email in ("Administrator", "Guest"):
        frappe.throw(_("Invalid user session."))

    # Verify current password
    from frappe.utils.password import check_password
    try:
        check_password(email, current_password)
    except frappe.AuthenticationError:
        frappe.throw(_("Current password is incorrect."))

    # Validate new password length
    new_password = cstr(new_password).strip()
    if len(new_password) < 8:
        frappe.throw(_("New password must be at least 8 characters."))

    # Set new password
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        user = frappe.get_doc("User", email)
        user.new_password = new_password
        user.flags.ignore_permissions = True
        # This save runs under an Administrator elevation, so on_user_update's
        # announcement would otherwise credit "Administrator" for a change the
        # account holder made themselves. Carry the real actor on the document
        # instance — NOT frappe.flags, which is global and survives a raise (L176).
        user.flags.ts_pwd_actor = original_user
        user.save()
        frappe.db.commit()
    finally:
        frappe.set_user(original_user)

    _audit_log(email, "Own password changed")
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist(methods=["POST"])
def reset_password(email):
    """Hand the IT Head a one-time reset link, and ALSO email the same link
    when outbound mail is configured. The link is unconditional; the email is
    best-effort delivery of it, never a gate."""
    _require_post()
    _check_it_head()
    _check_not_self(email)
    _check_not_protected(email)

    if not frappe.db.exists("User", email):
        frappe.throw(_("User '{0}' does not exist.").format(frappe.utils.escape_html(email)))

    # Mint the link ONCE — every User.reset_password() call rotates the stored
    # key, so a second mint (e.g. send_email=True later) would kill the link
    # already shown to the IT Head. `password_expired=True` lands the user on
    # /update-password with the old-password field hidden: nothing to explain,
    # no temporary credential left in a chat thread (there is no per-user
    # "must reset" flag in Frappe this could use instead — the only forced-reset
    # redirect fires from the GLOBAL System Setting in LoginManager.login()).
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        user = frappe.get_doc("User", email)
        link = user.reset_password(send_email=False, password_expired=True)
        frappe.db.commit()
    finally:
        frappe.set_user(original_user)

    # Audit BEFORE the send attempt, and it MUST start with LINK_AUDIT_PREFIX's
    # wording: _link_issuer() attributes a consumed link to the IT Head via this
    # Comment's sub-second gap from the key mint (120s tolerance). A slow SMTP
    # failure below would push a later Comment past that window and make the
    # Activity entry affirmatively claim "Self-service" for an IT-Head reset.
    _audit_log(email, "Password reset link generated (shared by IT Head)")
    frappe.db.commit()

    # Best-effort email of the SAME link. Only a default outgoing account can
    # deliver (frappe.sendmail resolves the sender through it; enabled-but-not-
    # default cannot send). password_reset_mail() sends synchronously (now=True)
    # and RAISES on failure — e.g. an OAuth-dead account — so it is contained
    # here: the IT Head keeps the on-screen link no matter what mail does.
    # The follow-up audit strings must NOT start with LINK_AUDIT_PREFIX's
    # wording or the newest-row LIKE match would steal attribution from the
    # Comment written above.
    email_sent = False
    email_attempted = False
    if frappe.db.exists("Email Account", {"enable_outgoing": 1, "default_outgoing": 1}):
        email_attempted = True
        # Elevated DELIBERATELY (try/finally, L176): send_login_mail derives the
        # From identity from the SESSION user (STANDARD_USERS check in user.py) —
        # sent as the IT Head, M365 rejects the send-as once mail is restored;
        # as Administrator the sender stays the default outgoing account.
        # No rollback in the except: SendMailContext commits the Email Queue row
        # itself before the exception reaches us (status "Not Sent", retry+1), so
        # the scheduler may still retry it up to 3 times — email_sent=False means
        # "immediate send failed", not "will never be delivered".
        frappe.set_user("Administrator")
        try:
            user.password_reset_mail(link)
            email_sent = True
        except Exception as e:
            frappe.clear_messages()  # L276: a caught throw still leaks its message
            # cstr(e) only — NEVER title-only log_error and NEVER
            # get_traceback(with_context=True) here: this is the M365 OAuth
            # subsystem, whose frame-variable dumps leak the client_secret
            # into Error Log (the standing P1).
            frappe.log_error(
                title="User Management: reset link email failed",
                # `or` fallback matters: an empty message makes log_error fall
                # back to the with_context frame dump — the leak path itself.
                message=frappe.utils.cstr(e) or f"{type(e).__name__}: (no message)",
            )
        finally:
            frappe.set_user(original_user)
        # Outside the elevation: _audit_log stamps the Comment owner from the
        # session actor — it must read the IT Head, not Administrator.
        if email_sent:
            _audit_log(email, "Reset link also emailed to the user")
        else:
            _audit_log(email, "Reset link email failed (outbound mail error); link shared by IT Head")
        frappe.db.commit()

    # Notification Center bells at GENERATION time (v2.37.4): the audit Comment
    # above is timeline-only — the center reads Notification Log, so without
    # these the event is invisible there (the v2.36.0 bell fires only when the
    # password actually CHANGES). Account holder gets a transparency notice;
    # the issuing IT Head sees their own action in the bell. _coverage_bell is
    # fail-soft and skips Administrator/Guest recipients. The WHOLE block is
    # additionally fail-soft (same pattern as _announce_password_change): by
    # here the key has already rotated, so a throw from the import/lookup/
    # format lines would 500 the IT Head and lose the freshly minted link.
    try:
        from trustbit_ethanol.ts_gate_entry.ts_notification_coverage import _coverage_bell

        issuer_name = frappe.db.get_value("User", original_user, "full_name") or original_user
        _coverage_bell(
            [email],
            _("A password reset link for your account was generated by {0}").format(issuer_name),
            "User",
            email,
            body=_(
                "A one-time password reset link for your account ({0}) was generated by {1}. "
                "If you did not request this, contact IT immediately."
            ).format(email, issuer_name),
        )
        if email_sent:
            issuer_note = _("The link was also emailed to the user.")
        elif email_attempted:
            issuer_note = _("Emailing the link failed — share the link with the user directly.")
        else:
            issuer_note = _("Share the link with the user directly.")
        _coverage_bell(
            [original_user],
            _("Password reset link generated for {0}").format(email),
            "User",
            email,
            body=_("You generated a one-time password reset link for {0}. {1}").format(
                email, issuer_note
            ),
        )
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title="User Management: reset link generation bell failed",
            message=frappe.get_traceback(),
        )
        frappe.clear_messages()

    return {
        "user": email,
        "method": "link",
        "reset_link": link,
        "expires_in": _reset_link_expiry_label(),
        "email_attempted": email_attempted,
        "email_sent": email_sent,
    }


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
        "whatsapp_number": user.get("ts_whatsapp_number"),
        "whatsapp_opt_in": 1 if user.get("ts_whatsapp_opt_in") else 0,
        "enabled": user.enabled,
        "user_image": user.user_image,
        "creation": user.creation,
        "last_active": user.last_active,
        "roles": operational_roles,
        "has_system_roles": any(r in BLOCKED_ROLES for r in all_roles),
        # Mirrors _check_not_protected — see the note in get_users().
        "is_protected": (
            user.email.lower() in [p.lower() for p in PROTECTED_USERS]
            or any(r in BLOCKED_ROLES for r in all_roles)
        ),
        "is_self": user.email.lower() == frappe.session.user.lower(),
    }


@frappe.whitelist(methods=["POST"])
def set_user_whatsapp(email, whatsapp_number=None, whatsapp_opt_in=0):
    """Add / update an existing user's WhatsApp number + opt-in (IT Head)."""
    _require_post()
    _check_it_head()
    email = cstr(email).strip().lower()
    if not frappe.db.exists("User", email):
        frappe.throw(_("User '{0}' does not exist.").format(frappe.utils.escape_html(email)))
    # Was a bare PROTECTED_USERS literal check, which skipped the role guard —
    # leaving a REST path to repoint a privileged account's approval
    # notifications to any handset. Use the same guard as every other mutator.
    _check_not_protected(email)

    values = _normalize_whatsapp(whatsapp_number, whatsapp_opt_in)
    frappe.db.set_value("User", email, values, update_modified=True)
    frappe.db.commit()

    _audit_log(email, "WhatsApp updated", "Number: {0}, Opt-In: {1}".format(
        values.get("ts_whatsapp_number") or "(cleared)", values.get("ts_whatsapp_opt_in")
    ))
    return {
        "email": email,
        "whatsapp_number": values.get("ts_whatsapp_number"),
        "whatsapp_opt_in": values.get("ts_whatsapp_opt_in"),
    }


@frappe.whitelist(methods=["POST"])
def update_user_profile(email, first_name, last_name=None, mobile_no=None):
    """Correct a user's name / mobile after creation.

    get_user_detail already returned these three fields but nothing could write
    them, and IT Head holds no DocPerm on User anywhere else — so a typo in a name
    made at creation time was permanent.
    """
    _require_post()
    _check_it_head()
    _check_not_self(email)
    _check_not_protected(email)

    if not frappe.db.exists("User", email):
        frappe.throw(_("User '{0}' does not exist.").format(frappe.utils.escape_html(email)))

    first_name = cstr(first_name).strip()
    if not first_name:
        frappe.throw(_("First Name is required."))

    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        user = frappe.get_doc("User", email)
        user.first_name = first_name
        user.last_name = cstr(last_name).strip() or None
        user.mobile_no = cstr(mobile_no).strip() or None
        user.flags.ignore_permissions = True
        user.save()
        frappe.db.commit()
    finally:
        frappe.set_user(original_user)

    _audit_log(email, "Profile updated", "Name: {0}".format(
        " ".join(filter(None, [first_name, cstr(last_name).strip()]))
    ))
    frappe.db.commit()
    return {"user": email, "full_name": user.full_name}


@frappe.whitelist()
def get_audit_log(email, limit=20):
    """Return this page's own audit entries for a user.

    _audit_log writes Comments onto the User document, which IT Head has no
    permission to open — so the trail was write-only for the exact person it
    exists to serve. This exposes it back to the page.
    """
    _check_it_head()

    if not frappe.db.exists("User", email):
        frappe.throw(_("User '{0}' does not exist.").format(frappe.utils.escape_html(email)))

    # Clamp hard. `int(limit or 20)` was wrong twice over for a REST caller:
    # the STRING "0" is truthy, so it survived the `or` and frappe reads
    # limit_page_length=0 as UNLIMITED (the whole trail); and a negative reached
    # MariaDB as `limit -5`, raising a 1064 that echoes the generated query back.
    # OverflowError is not academic: frappe parses request bodies with stdlib
    # json.loads, which accepts the `Infinity` literal, and int(float('inf'))
    # raises OverflowError rather than ValueError.
    try:
        limit = int(limit)
    except (TypeError, ValueError, OverflowError):
        limit = 20
    limit = max(1, min(limit, 100))

    rows = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": "User",
            "reference_name": email,
            "comment_type": "Info",
            "content": ["like", "[User Management]%"],
        },
        fields=["content", "creation", "owner"],
        order_by="creation desc",
        limit_page_length=limit,
    )
    return rows


# ── USER LIST VISIBILITY (permission_query_conditions) ──────────────

# Roles that may see the whole User list.
#
# Deliberately System Manager ONLY. IT Head is NOT exempt: account administration
# is done through the User Management page, which reads via frappe.get_all
# (ignore_permissions=True, frappe/__init__.py:2048) and so is completely
# unaffected by this condition. Giving IT Head the raw /app/user roster as well
# would widen the exposure for no capability gain.
_USER_LIST_FULL_VIEW_ROLES = ("System Manager",)

# The link-field / "Assign To" pickers must keep working, and they run through
# the SAME DatabaseQuery this condition applies to. Allow those two entry points
# through by their top-level cmd.
#
# @mentions need no exemption: get_names_for_mentions (frappe/desk/search.py:292)
# serves from a cache and never touches DatabaseQuery.
#
# ⚠ Honest limitation — state it accurately, because an overstated boundary is
# worse than a documented one. `frappe.desk.search.search_widget` is itself
# whitelisted, its page_length is NOT capped (frappe/__init__.py:2520 is a bare
# cint), and an empty txt builds `LIKE '%%'` (user_query, core/.../user.py:1054),
# so ONE request returns the whole enabled roster. What it returns is bounded to
# name + full_name — user_query selects nothing else — so this closes the far
# larger hole (User is a CORE_DOCTYPE, so get_permitted_fields exposed EVERY
# column, incl. mobile_no / last_ip / api_key, to any desk user via
# frappe.client.get_list) while leaving a names-and-emails directory readable.
# It is a privacy control, NOT a hard boundary. Closing it too would collapse
# every user picker in the desk — the trade-off was chosen deliberately.
_USER_PICKER_CMDS = (
    "frappe.desk.search.search_link",
    "frappe.desk.search.search_widget",
)


def user_list_query_conditions(user=None, doctype=None):
    """Restrict the User list to the caller's own record.

    Frappe's own condition (core/doctype/user/user.py:1135) only hides
    Administrator and Guest, so every desk user could list the entire staff
    roster — names, emails, enabled state, last login. Opening another user's
    record was already denied; this closes the LIST.

    Conditions from all apps are ANDed by DatabaseQuery
    (model/db_query.py:1137), so this composes with frappe's rather than
    replacing it.
    """
    try:
        user = user or frappe.session.user
        if not user or user == "Administrator":
            return ""

        if any(r in frappe.get_roles(user) for r in _USER_LIST_FULL_VIEW_ROLES):
            return ""

        cmd = None
        form_dict = getattr(frappe.local, "form_dict", None)
        if form_dict:
            cmd = form_dict.get("cmd")
        if cmd in _USER_PICKER_CMDS:
            return ""

        return "(`tabUser`.`name` = {0})".format(frappe.db.escape(user))
    except Exception:
        # Fail CLOSED: a privacy control that breaks open is not a control. The
        # worst case is a System Manager briefly seeing only themselves in the
        # raw list, which the User Management page does not depend on.
        try:
            return "(`tabUser`.`name` = {0})".format(
                frappe.db.escape(frappe.session.user or "Guest")
            )
        except Exception:
            return "(1 = 0)"


# ── ON SESSION CREATION — Force Password Change ─────────────────────

def check_force_password_change():
    """on_session_creation hook — surface a valid password-reset link to a user
    whose password was reset by IT Head.

    Previously this minted `frappe.generate_hash(user, 20)` and wrote that RAW
    value into `reset_password_key`. Frappe v15 stores sha256(key) and looks the
    column up by that hash (core/doctype/user/user.py:375 and :927), so a raw
    20-char value could never match and every generated link was rejected. Worse,
    it ran on EVERY login while the flag was set, overwriting any legitimate key
    the user had just requested via "Forgot Password".

    We now use Frappe's own `User.reset_password()`, which stores the hash and the
    generation timestamp the expiry check needs, so the link actually works.

    NOTE: the desk login page only follows `redirect_to` when the response message
    is "Password Reset" (templates/includes/login/login.js:232). `set_user_info()`
    overwrites the message with "Logged In" AFTER this hook runs, so this cannot
    hard-force the redirect from here — it makes the link valid and stops the key
    corruption. Genuinely blocking the session needs either a hooks.py change or
    System Settings > force_user_to_reset_password; both are deliberately left to
    the team as a policy decision.
    """
    try:
        user = frappe.session.user
        if not user or user in ("Administrator", "Guest"):
            return

        if not frappe.db.get_value("User", user, "ts_force_password_change"):
            return

        doc = frappe.get_doc("User", user)
        link = doc.reset_password(send_email=False)
        frappe.db.commit()
        frappe.local.response["redirect_to"] = link
    except Exception:
        # frappe/auth.py run_trigger() has NO try/except of its own, so this
        # block is the only thing between a bug here and a site-wide login
        # outage. frappe.log_error() is itself a DB insert, so if the original
        # exception poisoned the transaction the logging call throws too and
        # escapes — nest it.
        try:
            frappe.log_error(
                title="Force Password Change Error",
                message=frappe.get_traceback(),
            )
        except Exception:
            pass


# REMOVED: clear_force_password_flag()
#
# It was a whitelisted endpoint with no authorization check of any kind, reachable
# by GET, that let ANY authenticated user clear their own force-password-change
# flag and blank their reset key — i.e. a one-request bypass of the very control
# it belonged to. It had zero callers anywhere in frappe, erpnext or this app.
# on_user_update() below now clears the flag on the real signal (the user actually
# setting a password), so nothing needs a manual escape hatch.


LINK_AUDIT_PREFIX = "[User Management] Password reset link generated"

# How close the audit Comment must sit to the key's mint time to be treated as
# the record of THAT link. reset_password() writes the Comment immediately after
# minting, so the real gap is sub-second; 120s is slack for a slow request, and
# is far tighter than the link's own lifetime.
_LINK_ATTRIBUTION_TOLERANCE_SECONDS = 120


def _link_issuer(doc):
    """The IT Head who generated the reset link this user just consumed.

    Returns None for a self-service "Forgot Password" reset, which must NOT be
    attributed to anyone.

    Attribution cannot come from the session: /update-password is guest-allowed,
    so the completing request runs as Guest. It is derived instead from the audit
    Comment this module already writes when a link is issued, matched against
    `last_reset_password_key_generated_on` — the mint time of the key that was
    just consumed, which reset_user_data() leaves intact (it clears only
    reset_password_key).
    """
    minted_on = doc.get("last_reset_password_key_generated_on")
    if not minted_on:
        return None

    row = frappe.db.sql(
        """
        SELECT owner, creation FROM `tabComment`
        WHERE reference_doctype = 'User' AND reference_name = %s
          AND comment_type = 'Info' AND content LIKE %s
        ORDER BY creation DESC LIMIT 1
        """,
        (doc.name, LINK_AUDIT_PREFIX + "%"),
        as_dict=True,
    )
    if not row:
        return None

    # SIGNED, deliberately. The audit Comment is written just AFTER the key is
    # minted, in the same request, so a genuine pairing gives a small POSITIVE
    # gap. A negative gap means the newest issued link predates this key — i.e.
    # the key came from somewhere else (a self-service "Forgot Password"), and
    # naming an IT Head for it would be a false statement in an audit record.
    # Using abs() here silently mis-attributes exactly that case.
    # STRICTLY non-negative, and that strictness is the whole test. Both
    # timestamps are microsecond-precision from the same clock, and the Comment
    # is always written AFTER the key it describes (mint → commit → _audit_log,
    # measured at +0.038s). A self-service key minted later than the newest
    # issued link therefore yields a NEGATIVE gap (measured at −0.050s), which is
    # the only thing distinguishing the two when both happen in the same second.
    # An abs(), or a loose ±seconds epsilon, silently mis-attributes a
    # self-service reset to an IT Head — a false statement in an audit record.
    gap = frappe.utils.time_diff_in_seconds(row[0].creation, minted_on)
    if gap < 0 or gap > _LINK_ATTRIBUTION_TOLERANCE_SECONDS:
        return None
    return row[0].owner


def _announce_password_change(doc, via="link"):
    """Record + notify that this user's password just changed.

    `via="link"`   — a one-time reset link was consumed (/update-password).
    `via="direct"` — the password was set straight onto the document: the
                     "Set New Password" field on the User form, the form's
                     Password menu, or our own reset_own_password. Frappe records
                     nothing useful for this — the timeline just says "<user>
                     last edited this" — which is exactly the blind spot this
                     covers.

    Fail-soft in every branch: this runs inside the request that changed the
    password, and a transparency notice must never be the reason a password
    change fails (Lessons 238/276).
    """
    try:
        if via == "direct":
            # Here the session IS meaningful (unlike the guest link flow), except
            # when one of our endpoints saved under an Administrator elevation —
            # those pass the real actor on a per-instance doc flag.
            actor = doc.flags.get("ts_pwd_actor") or frappe.session.user
            actor_name = frappe.db.get_value("User", actor, "full_name") or actor

            if actor == doc.name:
                detail = _("Changed by the account holder directly")
                subject = _("Your password was changed")
            else:
                detail = _("Set directly by {0} (not via a reset link)").format(actor_name)
                subject = _("Your password was changed by {0}").format(actor_name)
            attributed_to = actor
        else:
            issuer = _link_issuer(doc)
            if issuer:
                issuer_name = frappe.db.get_value("User", issuer, "full_name") or issuer
                detail = _("Password reset link generated by {0}").format(issuer_name)
                subject = _("Your password was changed using a reset link generated by {0}").format(
                    issuer_name
                )
            else:
                detail = _("Self-service password reset link")
                subject = _("Your password was changed using a password reset link")
            # The account holder is the one who actually set it; the issuer is
            # named in the detail.
            attributed_to = doc.name

        # 1) Activity entry on the user — shows in Recent Activity on the page.
        _audit_log(doc.name, "Password changed", detail, by=attributed_to)

        # 2) Notification Center bell for the account holder. Reuse the shared
        #    fail-soft inserter rather than hand-rolling another Notification Log
        #    write; it skips Guest/Administrator and never raises.
        from trustbit_ethanol.ts_gate_entry.ts_notification_coverage import _coverage_bell

        _coverage_bell(
            [doc.name],
            subject,
            "User",
            doc.name,
            body=_(
                "The password for your account ({0}) was just changed. {1}. "
                "If this was not you, contact IT immediately."
            ).format(doc.name, detail),
        )
    except Exception:
        try:
            frappe.log_error(
                title="User Management: password-change announce failed",
                message=frappe.get_traceback(),
            )
        except Exception:
            pass
        frappe.clear_messages()


def on_user_update(doc, method):
    """doc_events User on_update — clear the force-change flag once the user has
    actually set a new password.

    Two independent bugs made the previous version permanently dead, which is why
    the flag latched on and no account ever cleared it:
      1. it read `doc.get("__new_password")`, but Frappe assigns
         `self.__new_password` inside the User class, which Python name-mangles to
         `_User__new_password` — the plain key never exists;
      2. it mutated `doc` in `on_update`, which runs AFTER `db_update()`
         (model/document.py:428 then :431), so the assignment was never persisted.
    `db_set` writes through and is explicitly safe to call from post-save hooks.
    """
    # has_value_changed() returns True when _doc_before_save is None
    # (model/document.py:506) — i.e. on every INSERT — which would make
    # `key_consumed` below true for every brand-new doc.
    if doc.get_doc_before_save() is None:
        return

    # Two distinct ways a password actually gets set, and the original code
    # caught neither:
    #  (a) the /update-password page calls _update_password() directly and then
    #      reset_user_data() saves the doc with reset_password_key blanked
    #      (core/doctype/user/user.py:952-957) — no new-password value is ever
    #      present on the doc, so key-consumption is the only signal;
    #  (b) an ORM save carrying a new password, where Frappe stashes it as
    #      self.__new_password inside class User → mangled to _User__new_password.
    key_consumed = (
        doc.has_value_changed("reset_password_key") and not doc.get("reset_password_key")
    )
    password_set = bool(doc.get("_User__new_password") or doc.get("new_password"))

    if not (key_consumed or password_set):
        return

    # Legacy drain: temporary passwords were retired in favour of one-time reset
    # links, so nothing sets this flag any more. The accounts still carrying it
    # from before clear themselves the first time their holder sets a password.
    # db_set writes through; assigning to doc here would be discarded because
    # on_update runs AFTER db_update() (model/document.py:428 then :431).
    if doc.get("ts_force_password_change"):
        doc.db_set("ts_force_password_change", 0, update_modified=False)
        doc.db_set("reset_password_key", "", update_modified=False)

    # Transparency for BOTH routes. A directly-set password (the "Set New
    # Password" field, the form's Password menu, an admin edit) otherwise leaves
    # nothing but frappe's generic "<user> last edited this", which says neither
    # what changed nor who did it.
    if key_consumed:
        _announce_password_change(doc, via="link")
    elif password_set:
        _announce_password_change(doc, via="direct")
