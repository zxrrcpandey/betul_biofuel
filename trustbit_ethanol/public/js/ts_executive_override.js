// v2.8.12 — Executive (CEO + MD) Edit Override JS helper
// Loaded FIRST in app_include_js so window.ts_is_executive_override_user
// is defined before po_approval.js + mr_approval.js use it in field-lock helpers.
//
// Best-effort client-side check; Python before_save re-verifies server-side.
// Tampering with frappe.user_roles in DevTools will NOT bypass server guards.

(function () {
	"use strict";

	const EXEC_ROLES = ["CEO", "MD"];

	window.ts_is_executive_override_user = function () {
		const roles = (
			(typeof frappe !== "undefined" && frappe.user_roles)
			|| (typeof frappe !== "undefined" && frappe.boot && frappe.boot.user && frappe.boot.user.roles)
			|| []
		);
		return EXEC_ROLES.some(function (r) { return roles.indexOf(r) !== -1; });
	};
})();
