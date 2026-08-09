import { reactive } from "vue"

import { apiCall } from "./session.js"

const NS = "trustbit_ethanol.ts_gate_entry.ts_exec_api."

export const overview = reactive({
  loading: false,
  error: null, // FrappeError | null — NEVER render this as an empty state
  data: null,
  loadedAt: null,
})

export async function loadOverview(date) {
  overview.loading = true
  overview.error = null
  try {
    const { message } = await apiCall(NS + "get_overview", date ? { date } : {})
    overview.data = message || null
    overview.loadedAt = Date.now()
  } catch (e) {
    overview.error = e
    // Keep the last-good payload on screen: blanking every card on a failed
    // refresh is briefly a worse lie than showing stale numbers.
  } finally {
    overview.loading = false
  }
}

// "Fresh" for 5 minutes; after that the as-of line turns amber.
export function isStale() {
  if (!overview.loadedAt) return false
  return Date.now() - overview.loadedAt > 5 * 60 * 1000
}
