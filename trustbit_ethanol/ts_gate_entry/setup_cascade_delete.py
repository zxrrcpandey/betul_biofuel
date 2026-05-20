"""Post-migrate setup for the Token Cascade Delete System (v2.11.0).

Idempotent post-install hook called via `after_migrate`. Handles:
- Email template `Cascade Delete Warning` install (if missing)
- Cascade backup directory creation on disk
- Cache flushes for Workspace / Page / TS Cascade Delete Log / TS Settings (Lesson 234)

Does NOT seed the Super Admin role — that's `setup_super_admin_role.py`.

Lesson references: 234 (sessions-preserving meta flush), 252 (hooks-cache flush after
hooks.py changes), 173 (Workspace JSON force-reload after migrate).
"""

import os
import frappe

EMAIL_TEMPLATE_NAME = "Cascade Delete Warning"
_EMAIL_TEMPLATE_PATH = os.path.join(
	os.path.dirname(__file__), "email_templates", "cascade_delete_warning.html"
)


def seed_cascade_delete():
	"""Run the post-migrate steps for the cascade delete feature. Idempotent."""
	_ensure_backup_directory()
	_ensure_email_template()
	_flush_cascade_caches()


def _ensure_backup_directory():
	"""Create the per-site `private/files/cascade_backups/` directory if missing."""
	site = frappe.local.site
	# `frappe.utils.get_site_path` is the official way to resolve `sites/<site>/`.
	from frappe.utils import get_site_path
	target_dir = get_site_path("private", "files", "cascade_backups")
	try:
		os.makedirs(target_dir, exist_ok=True)
	except Exception:
		frappe.log_error(
			title="Cascade backup directory create failed",
			message=f"Could not create {target_dir!r} (will retry on next migrate)",
		)


def _ensure_email_template():
	"""Install Email Template doc if missing; refresh body if file changed."""
	if not os.path.exists(_EMAIL_TEMPLATE_PATH):
		# Template file ships with the app; if missing here the feature is mis-installed
		# but don't block migrate.
		return
	with open(_EMAIL_TEMPLATE_PATH, "r", encoding="utf-8") as f:
		body = f.read()
	subject = "[BBPL CRITICAL] Token {{ doc.target_token }} CASCADE-DELETED by {{ doc.initiated_by }}"

	if frappe.db.exists("Email Template", EMAIL_TEMPLATE_NAME):
		tpl = frappe.get_doc("Email Template", EMAIL_TEMPLATE_NAME)
		if tpl.response != body or tpl.subject != subject:
			tpl.response = body
			tpl.subject = subject
			tpl.use_html = 1
			tpl.flags.ignore_permissions = True
			tpl.save(ignore_permissions=True)
	else:
		frappe.get_doc({
			"doctype": "Email Template",
			"name": EMAIL_TEMPLATE_NAME,
			"subject": subject,
			"response": body,
			"use_html": 1,
		}).insert(ignore_permissions=True)
	frappe.db.commit()


def _flush_cascade_caches():
	"""Lesson 234 sessions-preserving meta flush after install / re-install."""
	try:
		for dt in ("TS Cascade Delete Log", "TS Settings", "Workspace", "Page", "Email Template"):
			frappe.clear_cache(doctype=dt)
	except Exception:
		pass
	try:
		from frappe.cache_manager import clear_global_cache
		clear_global_cache()
	except Exception:
		pass
