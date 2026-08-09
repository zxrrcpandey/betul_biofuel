import { reactive } from "vue"

import { apiCall } from "./session.js"

const NS = "trustbit_ethanol.ts_gate_entry.ts_push_api."

// Strict FIRST-MATCH state machine. Feature-detected, never UA-version-sniffed.
//   s0_master_off  IT switched alerts off
//   s1_no_keys     server has no VAPID keys yet
//   s2_ios_old     iOS installed but no PushManager (pre-16.4)
//   s2_unsupported browser has no SW/Push/Notification at all
//   s3_ios_install iOS in a Safari TAB — push needs Home Screen install
//   s4_ready       can ask for permission (the CTA)
//   s5_on          granted + live subscription
//   s6_broken      granted but the subscription vanished (iOS does this)
//   s7_denied      blocked — no button, only recovery instructions
export const push = reactive({
  state: "loading",
  loading: false,
  busy: false,
  error: "",
  testSent: false,
  serverEnabled: false,
  configured: false,
  vapidKey: "",
  dryRun: false,
  deviceCount: 0,
})

export const isIOS = () =>
  /iPhone|iPad|iPod/.test(navigator.userAgent) ||
  // iPadOS 13+ reports as Macintosh but has touch
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)

export const isStandalone = () =>
  window.matchMedia("(display-mode: standalone)").matches ||
  window.navigator.standalone === true

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/")
  const raw = window.atob(base64)
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)))
}

function keysMatch(sub, vapidKey) {
  try {
    const applied = sub.options && sub.options.applicationServerKey
    if (!applied || !vapidKey) return true
    const a = new Uint8Array(applied)
    const b = urlBase64ToUint8Array(vapidKey)
    if (a.length !== b.length) return false
    return a.every((v, i) => v === b[i])
  } catch {
    return true
  }
}

async function registration() {
  if (!("serviceWorker" in navigator)) return null
  try {
    return await navigator.serviceWorker.ready
  } catch {
    return null
  }
}

export async function refreshPushState() {
  push.loading = true
  push.error = ""
  try {
    const { message } = await apiCall(NS + "get_push_state")
    const m = message || {}
    push.serverEnabled = Boolean(m.enabled)
    push.configured = Boolean(m.configured)
    push.vapidKey = m.vapid_public_key || ""
    push.dryRun = Boolean(m.dry_run)
    push.deviceCount = (m.subscriptions || []).length

    if (!push.serverEnabled) {
      push.state = "s0_master_off"
      return
    }
    if (!push.configured || !push.vapidKey) {
      push.state = "s1_no_keys"
      return
    }
    const hasSW = "serviceWorker" in navigator
    const hasPush = "PushManager" in window
    const hasNotif = "Notification" in window

    if (isIOS() && !isStandalone()) {
      push.state = "s3_ios_install"
      return
    }
    if (isIOS() && !hasPush) {
      push.state = "s2_ios_old"
      return
    }
    if (!hasSW || !hasPush || !hasNotif) {
      push.state = "s2_unsupported"
      return
    }
    if (Notification.permission === "denied") {
      push.state = "s7_denied"
      return
    }
    if (Notification.permission === "granted") {
      const reg = await registration()
      const sub = reg ? await reg.pushManager.getSubscription() : null
      if (sub && keysMatch(sub, push.vapidKey)) {
        // Re-assert on every launch: iOS quietly drops subscriptions, and the
        // server row may have been pruned after a 410.
        await sendSubscription(sub)
        push.state = "s5_on"
      } else {
        push.state = "s6_broken"
      }
      return
    }
    push.state = "s4_ready"
  } catch (e) {
    push.error = e.display ? e.display() : String(e)
    push.state = "s1_no_keys"
  } finally {
    push.loading = false
  }
}

async function sendSubscription(sub) {
  const raw = sub.toJSON()
  await apiCall(NS + "subscribe", {
    endpoint: raw.endpoint,
    p256dh: raw.keys.p256dh,
    auth: raw.keys.auth,
    user_agent: navigator.userAgent.slice(0, 500),
  })
}

// MUST be called synchronously from a click handler — iOS only allows the
// permission prompt in response to a direct user gesture, never on load.
export async function enablePush() {
  push.busy = true
  push.error = ""
  try {
    const permission = await Notification.requestPermission()
    if (permission !== "granted") {
      push.state = permission === "denied" ? "s7_denied" : "s4_ready"
      return
    }
    const reg = await registration()
    if (!reg) throw new Error("Service worker not ready")

    // A rotated VAPID key makes subscribe() throw InvalidStateError while the
    // old subscription exists — drop it first, or a rotation bricks the phone.
    const existing = await reg.pushManager.getSubscription()
    if (existing && !keysMatch(existing, push.vapidKey)) {
      await existing.unsubscribe()
    }
    const sub =
      (await reg.pushManager.getSubscription()) ||
      (await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(push.vapidKey),
      }))

    await sendSubscription(sub)
    push.state = "s5_on"
    push.deviceCount = Math.max(1, push.deviceCount)
  } catch (e) {
    push.error = e.display ? e.display() : "Could not turn on alerts on this phone."
    await refreshPushState()
  } finally {
    push.busy = false
  }
}

export async function disablePush() {
  push.busy = true
  push.error = ""
  try {
    const reg = await registration()
    const sub = reg ? await reg.pushManager.getSubscription() : null
    if (sub) {
      await apiCall(NS + "unsubscribe", { endpoint: sub.endpoint })
      await sub.unsubscribe()
    } else {
      await apiCall(NS + "unsubscribe", {})
    }
    push.deviceCount = 0
    push.state = "s4_ready"
  } catch (e) {
    push.error = e.display ? e.display() : String(e)
  } finally {
    push.busy = false
  }
}

export async function sendTestPush() {
  push.busy = true
  push.error = ""
  push.testSent = false
  try {
    await apiCall(NS + "test_push")
    push.testSent = true
  } catch (e) {
    push.error = e.display ? e.display() : String(e)
  } finally {
    push.busy = false
  }
}

// The SW asks us to re-subscribe when the browser rotates the subscription.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (event.data && event.data.type === "exec-resubscribe") refreshPushState()
  })
}
