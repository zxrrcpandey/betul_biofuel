# Copyright (c) 2026, Trustbit Software and contributors

import hashlib

import frappe
from frappe import _
from frappe.model.document import Document

from trustbit_ethanol.ts_gate_entry.ts_webpush import validate_endpoint


class TSPushSubscription(Document):
    def validate(self):
        """A REAL validate(), not `pass`.

        This is the third independent place the endpoint is checked (the other
        two are subscribe() and send()). A row can be written by a desk admin
        or over REST, and a stored row can outlive a policy change, so the
        SSRF gate has to hold at the persistence layer too — otherwise a
        hostile row becomes a stored SSRF primitive that the enqueued sender
        dutifully POSTs to.
        """
        if not self.user:
            frappe.throw(_("Push subscription must belong to a user."))

        # Raises on anything not an https URL at an allow-listed push service.
        validate_endpoint(self.endpoint)

        # The hash is the uniqueness carrier and must always agree with the
        # endpoint it claims to represent — never trust a supplied value.
        self.endpoint_hash = hashlib.sha256(
            (self.endpoint or "").encode("utf-8")).hexdigest()

        if not self.p256dh or not self.auth:
            frappe.throw(_("Push subscription is missing its encryption keys."))

        if self.status not in ("Active", "Expired", "Revoked"):
            self.status = "Active"

        # Never let an error code smuggle the endpoint (a capability token) or
        # an Authorization header into a field admins can read.
        if self.last_error_code:
            self.last_error_code = str(self.last_error_code)[:40]
