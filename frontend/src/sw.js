// BBPL Approvals service worker — scope /exec/ ONLY (served from
// www/exec/sw.min.js so the browser default scope is /exec/; it never
// controls /app). Shell-only caching:
//   navigations   → network-first, 3s timeout, cached-shell fallback
//   /assets/…/exec → cache-first (content-hashed, immutable)
//   /api/*        → EARLY RETURN — never intercepted, never cached
// The version placeholder below is stamped by scripts/postbuild.mjs from the
// bundle hash, so every shell change ships a new cache name in the same commit.

const SW_VERSION = "__SW_VERSION__"
const CACHE = `exec-shell-${SW_VERSION}`
const ASSET_PREFIX = "/assets/trustbit_ethanol/exec/"
const SHELL_KEY = "/exec/"

self.addEventListener("install", () => {
  self.skipWaiting()
})

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys()
      await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      await self.clients.claim()
    })()
  )
})

self.addEventListener("fetch", (event) => {
  const req = event.request
  if (req.method !== "GET") return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith("/api/")) return // business data: hands off
  if (url.pathname.startsWith(ASSET_PREFIX)) {
    event.respondWith(cacheFirst(req))
    return
  }
  if (req.mode === "navigate" && url.pathname.startsWith("/exec")) {
    event.respondWith(shellNetworkFirst(req))
  }
})

async function cacheFirst(req) {
  const cache = await caches.open(CACHE)
  const hit = await cache.match(req)
  if (hit) return hit
  const res = await fetch(req)
  if (res && res.ok) cache.put(req, res.clone())
  return res
}

function fetchWithTimeout(req, ms) {
  return new Promise((resolve, reject) => {
    const ctl = new AbortController()
    const timer = setTimeout(() => {
      ctl.abort()
      reject(new Error("timeout"))
    }, ms)
    fetch(req, { signal: ctl.signal }).then(
      (res) => {
        clearTimeout(timer)
        resolve(res)
      },
      (err) => {
        clearTimeout(timer)
        reject(err)
      }
    )
  })
}

// ── Web Push ─────────────────────────────────────────────────────────────────
// ALWAYS call showNotification: browsers penalise (and eventually revoke) a
// service worker that receives a push and shows nothing.
self.addEventListener("push", (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = {}
  }
  const title = data.t || "BBPL Approvals"
  const body = data.b || "You have a new approval."
  // Same document replaces itself instead of stacking; renotify:false keeps a
  // repeat silent rather than buzzing twice for one PO.
  const tag = data.dt && data.dn ? `exec-${data.dt}-${data.dn}` : "exec-generic"

  event.waitUntil(
    (async () => {
      await self.registration.showNotification(title, {
        body,
        tag,
        renotify: false,
        icon: "/assets/trustbit_ethanol/exec/icons/icon-192.png",
        badge: "/assets/trustbit_ethanol/exec/icons/icon-192.png",
        data: { dt: data.dt || "", dn: data.dn || "", nl: data.nl || "" },
      })
      // The badge count rides IN the payload — the SW is contractually barred
      // from calling /api, so it cannot look the number up.
      if (self.registration.setAppBadge && typeof data.u === "number") {
        try {
          await self.registration.setAppBadge(data.u)
        } catch {
          /* unsupported — cosmetic only */
        }
      }
    })()
  )
})

self.addEventListener("notificationclick", (event) => {
  event.notification.close()
  const d = event.notification.data || {}
  // Percent-encode: doctypes contain spaces ("Purchase Order") and
  // openWindow would otherwise mangle the URL.
  const target =
    d.dt && d.dn
      ? `/exec/d/${encodeURIComponent(d.dt)}/${encodeURIComponent(d.dn)}`
      : "/exec/"

  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      })
      for (const client of all) {
        if (client.url.includes("/exec")) {
          await client.focus()
          if ("navigate" in client) {
            try {
              await client.navigate(target)
            } catch {
              /* focus alone is enough */
            }
          }
          return
        }
      }
      await self.clients.openWindow(target)
    })()
  )
})

// iOS silently invalidates subscriptions; the client also re-subscribes on
// every launch, this just covers the case where the browser tells us.
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true })
      all.forEach((c) => c.postMessage({ type: "exec-resubscribe" }))
    })()
  )
})

async function shellNetworkFirst(req) {
  const cache = await caches.open(CACHE)
  try {
    const res = await fetchWithTimeout(req, 3000)
    if (res && res.ok) cache.put(SHELL_KEY, res.clone())
    return res
  } catch {
    const hit = await cache.match(SHELL_KEY)
    if (hit) return hit
    return new Response(
      "<h1>Offline</h1><p>Approvals needs an internet connection.</p>",
      { status: 503, headers: { "Content-Type": "text/html" } }
    )
  }
}
