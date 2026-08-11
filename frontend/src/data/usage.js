import { computed, reactive } from "vue"

import { apiCall } from "./session.js"

const NS = "trustbit_ethanol.ts_gate_entry.ts_exec_api."

export const usage = reactive({
  loading: false,
  error: null, // FrappeError | null — NEVER render this as an empty state
  data: null,
  days: 7, // default window: over 30 days no department ever flags (measured
  //          five consecutive prod weeks), so the alerts would look broken.
  loadedAt: null,
  failedAt: null,
  seq: 0, // out-of-order guard — a fast 7→90→30 tap must never paint stale data
})

export async function loadUsage(days) {
  if (days) usage.days = days
  const mySeq = ++usage.seq
  usage.loading = true
  usage.error = null
  try {
    const { message } = await apiCall(NS + "get_usage_departments", { days: usage.days })
    if (mySeq !== usage.seq) return // a newer request superseded this one
    usage.data = message || null
    usage.loadedAt = Date.now()
  } catch (e) {
    if (mySeq !== usage.seq) return
    usage.error = e
    usage.failedAt = Date.now()
    // Keep the last-good payload: blanking every card on a failed refresh is
    // briefly a worse lie than showing stale numbers with an amber banner.
  } finally {
    if (mySeq === usage.seq) usage.loading = false
  }
}

// Tab-badge fetch: called once from TabBar when the tab is visible, so the
// badge can answer "needs a look?" BEFORE the tab is opened. Never refetches —
// opening the tab does that.
export function ensureUsageLoaded() {
  if (usage.data || usage.loading) return
  // 60s cooldown after a failure: TabBar mounts on every page, and without
  // this a persistently failing endpoint refires once per navigation.
  if (usage.failedAt && Date.now() - usage.failedAt < 60 * 1000) return
  loadUsage()
}

// Departments needing attention at the current window: idle + absent + blind.
export const alertCount = computed(() => {
  const d = usage.data
  if (!d) return 0
  let n = (d.departments || []).filter((x) => x.state && x.state !== "ok").length
  if ((d.people || {}).inactive_over_30d || (d.people || {}).never_signed_in) n += 1
  return n
})

// "Fresh" for 5 minutes; after that the as-of line turns amber. Matches overview.js.
export function isStale() {
  if (!usage.loadedAt) return false
  return Date.now() - usage.loadedAt > 5 * 60 * 1000
}
