# Copyright (c) 2026, Trustbit Software and contributors
# /exec — standalone shell for the BBPL Approvals executive PWA (v2.31.0).
#
# Serves the Vite-built SPA shell (www/exec.html) to Guest and authenticated
# users alike — the login screen must render for Guest. This context module
# returns NO business data for any user; every data read happens later over
# /api with the session's own permissions (ts_exec_api + workflow endpoints).

import frappe
import frappe.sessions

from trustbit_ethanol.ts_gate_entry.ts_exec_api import (
    KILL_SWITCH_FIELD,
    SW_FLAG_FIELD,
    flag_on,
)

# Never serve a Redis-cached shell pointing at an old bundle — the SPA
# analogue of the L318/319 stale-asset trap. exec.html additionally carries
# the <!-- no-cache --> comment marker.
no_cache = 1


def get_context(context):
    # CSRF token generation is LAZY (frappe.sessions.get_csrf_token) and a
    # standalone www page triggers nothing else that would generate it —
    # without this call the <!-- csrf_token --> comment in exec.html injects
    # an empty token and every POST from the shell fails CSRF. Precedent:
    # frappe/www/app.py, which also commits because the token is written into
    # the session store and must survive this request.
    frappe.sessions.get_csrf_token()
    frappe.db.commit()

    # Feature + service-worker kill switches, rendered into the shell as
    # window.execEnv = {enabled, sw}. (Deliberately no dunder names — frappe's
    # safe_render rejects any template containing ".__" as "Illegal template".)
    # The sw flag lets us disarm a live phone's service worker with no deploy
    # (main.js unregisters on 0).
    context.exec_enabled = 1 if flag_on(KILL_SWITCH_FIELD) else 0
    context.exec_sw = 1 if flag_on(SW_FLAG_FIELD) else 0
    return context
