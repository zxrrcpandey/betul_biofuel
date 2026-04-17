"""
Version info API — returns current git commit of trustbit_ethanol app.

Displayed in navbar via public/js/ts_version_badge.js so users can see at a
glance what's deployed on the server they're using (demo vs prod vs stale).

Cached for 5 minutes to avoid repeated subprocess git calls.
"""

import os
import subprocess
import frappe


@frappe.whitelist()
def get_version_info():
	"""Return current git commit info as {commit, message, date}. Cached 5 min."""
	cache_key = "ts_version_info"
	cached = frappe.cache().get_value(cache_key)
	if cached:
		return cached

	info = {"commit": "unknown", "message": "", "date": "", "branch": ""}
	try:
		app_path = frappe.get_app_path("trustbit_ethanol")
		git_root = os.path.dirname(os.path.dirname(app_path))
		# Single git command: short hash, subject, commit date, branch
		result = subprocess.run(
			[
				"git", "-C", git_root,
				"log", "-1",
				"--format=%h|%s|%ci",
			],
			capture_output=True, text=True, timeout=5,
		)
		if result.returncode == 0 and result.stdout.strip():
			parts = result.stdout.strip().split("|", 2)
			if len(parts) == 3:
				commit, msg, date = parts
				info["commit"] = commit
				info["message"] = msg[:80]
				info["date"] = date[:10]

		# Branch name
		branch = subprocess.run(
			["git", "-C", git_root, "rev-parse", "--abbrev-ref", "HEAD"],
			capture_output=True, text=True, timeout=3,
		)
		if branch.returncode == 0:
			info["branch"] = branch.stdout.strip()
	except Exception as e:
		frappe.log_error(message=f"get_version_info: {e}", title="ts_version_api")

	frappe.cache().set_value(cache_key, info, expires_in_sec=300)
	return info
