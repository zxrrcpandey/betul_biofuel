import { reactive } from "vue"

import { apiCall } from "./session.js"

// Server-rendered per user (www/exec.py → ts_exec_api.dashboard_view):
// 0 = no Dashboard tab, 1 = the CEO dashboard, 2 = the MD dashboard (MD wins
// for an account holding both). Read once — it cannot change without a reload.
export const DASHBOARD_VIEW = Number((window.execEnv || {}).dashboard) || 0

// The desk pages' own methods, so the phone and the desk cannot disagree.
// Each one re-checks the caller's role on the server.
const METHODS = {
  1: "trustbit_ethanol.ts_gate_entry.ts_dashboard_ceo.get_ceo_overview",
  2: "trustbit_ethanol.ts_gate_entry.ts_dashboard_md.get_md_overview",
}

// The client sends a period key, never dates (same rule as Usage).
export const PERIODS = [
  { key: "Today", label: "Today", scope: "today" },
  { key: "This Week", label: "Week", scope: "this week to date" },
  { key: "This Month", label: "Month", scope: "this month to date" },
  { key: "This Quarter", label: "Quarter", scope: "this quarter to date" },
  { key: "This Year", label: "Year", scope: "this financial year to date" },
]

export const dashboard = reactive({
  period: "This Month",
  loading: false,
  error: null, // FrappeError | null — NEVER render this as an empty state
  failedPeriod: null, // set when a period switch failed and older figures stay up
  data: null,
  loadedAt: null,
  seq: 0, // out-of-order guard — a fast Month→Year→Week tap must never paint stale data
})

export async function loadDashboard(period) {
  if (period) dashboard.period = period
  const mySeq = ++dashboard.seq
  dashboard.loading = true
  dashboard.error = null
  dashboard.failedPeriod = null
  try {
    const { message } = await apiCall(METHODS[DASHBOARD_VIEW], { date_range: dashboard.period })
    if (mySeq !== dashboard.seq) return // a newer request superseded this one
    dashboard.data = message || null
    dashboard.loadedAt = Date.now()
  } catch (e) {
    if (mySeq !== dashboard.seq) return
    dashboard.error = e
    // Keep the last-good figures and put the period control back on the period
    // they belong to: a new label over old numbers would be a lie.
    if (dashboard.data) {
      if (dashboard.period !== dashboard.data.filters.date_range) dashboard.failedPeriod = dashboard.period
      dashboard.period = dashboard.data.filters.date_range
    }
  } finally {
    if (mySeq === dashboard.seq) dashboard.loading = false
  }
}

// "Fresh" for 5 minutes; after that the as-of line turns amber. Matches overview.js.
export function isStale() {
  if (!dashboard.loadedAt) return false
  return Date.now() - dashboard.loadedAt > 5 * 60 * 1000
}
