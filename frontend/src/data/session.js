// Session + the ONE network chokepoint. Hand-rolled fetch, deliberately not
// a resource library: (1) the double-encoded _server_messages envelope must
// surface real text; (2) respond_as_json needs an Accept header that does
// not start with "text"; (3) login has bespoke redirect_to / verification /
// tmp_id branching; (4) one chokepoint enforces "never /api/resource PUT".

import { reactive } from "vue"

import { FrappeError, errorFromResponse, parseServerMessages } from "./errors.js"

export const session = reactive({
  user: null, // null = unknown, "Guest" = logged out, else email
  booted: false,
})

function csrfToken() {
  return (window.frappe && window.frappe.csrf_token) || ""
}

// Every call POSTs to /api/method/<dotted.path>. POST is valid for reads too
// (whitelist without methods accepts it) and keeps one uniform CSRF story.
export async function apiCall(method, args = {}, { timeoutMs = 20000 } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let res
  try {
    res = await fetch(`/api/method/${method}`, {
      method: "POST",
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Frappe-CSRF-Token": csrfToken(),
      },
      body: JSON.stringify(args),
    })
  } catch (e) {
    clearTimeout(timer)
    if (e && e.name === "AbortError") {
      throw new FrappeError({ status: 0, excType: "Timeout", messages: [], text: "Taking longer than usual." })
    }
    throw new FrappeError({ status: 0, excType: "NetworkError", messages: [], text: "No connection." })
  }
  clearTimeout(timer)

  const rawText = await res.text()
  let body = null
  try {
    body = rawText ? JSON.parse(rawText) : null
  } catch {
    body = null
  }

  if (!res.ok) {
    const err = errorFromResponse(res.status, body || {}, rawText)
    if (err.isPermissionError) {
      // A 403 with a Guest session means the session expired mid-action.
      try {
        const who = await whoami()
        if (who === "Guest") {
          session.user = "Guest"
          err.sessionExpired = true
        }
      } catch {
        /* keep original error */
      }
    }
    if (err.isCSRFError) {
      // Stale token (e.g. shell cached across login) — a hard reload
      // re-injects the correct per-session token.
      err.needsReload = true
    }
    throw err
  }

  // Warnings (budget/variance) ride along on a 200 — surface them too.
  const warnings = parseServerMessages(body && body._server_messages)
  const message = body ? body.message : null
  return { message, warnings, body }
}

export async function whoami() {
  const res = await fetch("/api/method/frappe.auth.get_logged_user", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Frappe-CSRF-Token": csrfToken(),
    },
    body: "{}",
  })
  if (!res.ok) return "Guest"
  const body = await res.json().catch(() => null)
  return (body && body.message) || "Guest"
}

export async function bootSession() {
  try {
    session.user = await whoami()
  } catch {
    session.user = "Guest"
  }
  session.booted = true
  return session.user
}

// Login branches (verified against frappe.auth.LoginManager v15):
//  A success            → {message:"Logged In"}                → full reload
//  B force-pw-change    → "Logged In" + redirect_to            → non-blocking notice
//  C core expiry reset  → redirect_to + message==="Password Reset" → BLOCKING
//  D 2FA                → {verification, tmp_id}               → OTP screen
//  E/F/G bad creds etc. → 401                                  → inline error
export async function login(usr, pwd) {
  const { body } = await apiCall("login", { usr, pwd }, { timeoutMs: 25000 })
  const msg = body || {}
  if (msg.verification && msg.tmp_id) {
    return { kind: "2fa", tmpId: msg.tmp_id, verification: msg.verification }
  }
  if (msg.message === "Password Reset" && msg.redirect_to) {
    return { kind: "reset_required", redirectTo: msg.redirect_to }
  }
  if (msg.message === "Logged In" || msg.message === "No App") {
    return { kind: "ok", redirectTo: msg.redirect_to || null }
  }
  return { kind: "ok", redirectTo: msg.redirect_to || null }
}

export async function loginOtp(tmpId, otp) {
  const { body } = await apiCall("login", { tmp_id: tmpId, otp }, { timeoutMs: 25000 })
  const msg = body || {}
  if (msg.message === "Logged In") return { kind: "ok", redirectTo: msg.redirect_to || null }
  return { kind: "ok", redirectTo: msg.redirect_to || null }
}

export async function logout() {
  try {
    await apiCall("logout", {})
  } finally {
    session.user = "Guest"
    // Full reload — drops all client state and picks up a fresh Guest token.
    window.location.replace("/exec/login")
  }
}
