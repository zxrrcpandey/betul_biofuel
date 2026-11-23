"""
Version info for the tag in the top bar (public/js/ts_version_badge.js): the app version of the running code,
when the code on this server last changed, the server's label from site_config (`ts_server_label`, e.g. "DEMO")
and the git commit. Version and date are read from the running code, so a server that gets its files by copy
(no git pull) is shown correctly too. The git part is cached for 5 minutes.
"""

import os
import subprocess
from datetime import datetime, timezone

import frappe
from frappe.utils import convert_utc_to_system_timezone, cstr

_changed = None  # newest code-file time, read once per process (every deploy restarts the processes)


def _last_change():
	"""When the app's code on this server last changed, in the site's time zone."""
	global _changed
	if _changed is None:
		newest = 0
		for folder, dirs, files in os.walk(frappe.get_app_path("trustbit_ethanol")):
			dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules")]
			for name in files:
				if name.endswith((".py", ".js", ".json", ".html", ".css")):
					try:
						newest = max(newest, os.path.getmtime(os.path.join(folder, name)))
					except OSError:
						pass
		_changed = newest
	return convert_utc_to_system_timezone(datetime.fromtimestamp(_changed, tz=timezone.utc))


@frappe.whitelist()
def get_version_info():
	"""{version, label, color, note, updated, updated_full, apps, commit, message, date, branch}, for a logged-in
	user only. `apps` = the installed Trustbit apps as [{title, version}], listed in the tag's popup. `color` and
	`note`: each server's site_config notes the other server's version (`ts_prod_version` on the test server,
	`ts_demo_version` on production). The tag is green while the two numbers are the same; otherwise a test
	server is orange and production gray."""
	from trustbit_ethanol import __version__

	changed = _last_change()
	label = cstr(frappe.conf.get("ts_server_label"))[:12]
	key, other_name = ("ts_prod_version", "production") if label else ("ts_demo_version", "demo")
	other = cstr(frappe.conf.get(key)).strip().lstrip("vV").split("+")[0][:20]  # "v2.85.7", "2.85.7+demo" both work
	same = bool(other) and __version__.split("+")[0] == other
	return dict(
		_git_info(),
		apps=[{"title": (frappe.get_hooks("app_title", app_name=app) or [app])[0],
			"version": cstr(getattr(frappe.get_module(app), "__version__", ""))}
			for app in frappe.get_installed_apps(_ensure_on_bench=True) if app.startswith("trustbit")],
		version=__version__,
		label=label,
		color="green" if same else ("orange" if label else "gray"),
		note=(f"Same version number as {other_name}" if same else f"{other_name.capitalize()} is on v{other}")
			if other else "",
		updated=f"{changed.day} {changed:%b}",
		updated_full=f"{changed:%d %b %Y %H:%M}",
	)


def _git_info():
	"""Current git commit as {commit, message, date, branch}. Cached 5 min."""
	cache_key = "ts_version_info"
	cached = frappe.cache().get_value(cache_key)
	if cached:
		return cached

	info = {"commit": "unknown", "message": "", "date": "", "branch": ""}
	try:
		# frappe.get_app_path returns the MODULE dir (apps/<app>/<app>/),
		# the git root is ONE level up (apps/<app>/).
		app_path = frappe.get_app_path("trustbit_ethanol")
		git_root = os.path.dirname(app_path)
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
