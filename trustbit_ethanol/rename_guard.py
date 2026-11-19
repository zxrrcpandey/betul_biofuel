"""Stops a user who can only READ a record from renaming it.

Frappe's `Document.rename` is whitelisted and passes the caller's `validate_rename` on. A caller who
sends `validate_rename=false` (run_doc_method or /api/v2/document/<doctype>/<name>/method/rename, GET
too) skips the write-permission and "Allow Rename" checks. Frappe fixed this upstream on 7 Oct 2026
(version-15 commit 79ae1a50a1) by ignoring that argument; this applies the same fix until our Frappe
has it. Server code that needs to skip the checks calls `frappe.rename_doc(...)` and is unaffected.
Registered as a before_request hook. Delete it (and the hook line) after a Frappe upgrade past that commit.
"""

import frappe
from frappe.model.document import Document



@frappe.whitelist()
def rename(self, name: str, merge=False, force=False, validate_rename=True):
	"""Rename the document to `name`. This transforms the current object."""
	# run_doc_method can build `self` from the caller's JSON (owner, confidential flag): reload the
	# saved record, then check write permission on it, not on that copy
	self.load_from_db()
	frappe.has_permission(self.doctype, "write", doc=self, throw=True)
	return self._rename(name=name, merge=merge, force=force)  # the caller's validate_rename is ignored


# The same hook (before_request and before_job) also carries these guards. Each is applied on its own: a
# guard that cannot be loaded after an upgrade must not stop every request and job; it is logged once per
# process and left off, and its test on the server fails.
GUARDS = (
	"trustbit_ethanol.chart_guard.apply",  # dashboard-chart, account-balance and get_balance_on calls
	"trustbit_ethanol.ts_gate_entry.ts_confidential_children.apply",  # one child row of a confidential document
	"trustbit_ethanol.print_guard.apply",  # a print format only on a document of its own DocType
)
_failed = set()


def apply():
	if Document.rename is not rename:
		Document.rename = rename
	for path in GUARDS:
		if path in _failed:
			continue
		try:
			frappe.get_attr(path)()
		except Exception:
			_failed.add(path)
			try:
				frappe.log_error(title="Guard not applied: " + path, message=frappe.get_traceback())
			except Exception:
				pass
