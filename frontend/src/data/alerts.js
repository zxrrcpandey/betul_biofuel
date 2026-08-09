import { reactive } from "vue"

import { apiCall } from "./session.js"

const NS = "trustbit_ethanol.ts_gate_entry.ts_exec_notifications."

// "Alerts" deliberately, never "Notifications": Approvals (Inbox) is what
// needs a decision, Alerts is what happened.
export const alerts = reactive({
  open: false,
  loading: false,
  error: null,
  rows: [],
  unread: 0,
  loadedAt: null,
  marking: false,
})

// Doctypes the app can actually open. Anything else renders non-tappable
// rather than routing to a screen that would 404 or show an empty shell.
const ROUTABLE = new Set([
  "Purchase Order",
  "Material Request",
  "TS Budget Override Approval",
  "TS Post Dated Entry Request",
])

export function isRoutable(row) {
  return Boolean(row && row.doc_type && row.doc_name && ROUTABLE.has(row.doc_type))
}

export async function loadAlerts() {
  alerts.loading = true
  alerts.error = null
  try {
    const { message } = await apiCall(NS + "get_alerts", { start: 0, page_length: 20 })
    alerts.rows = (message && message.rows) || []
    alerts.unread = (message && message.unread_total) || 0
    alerts.loadedAt = Date.now()
  } catch (e) {
    alerts.error = e
  } finally {
    alerts.loading = false
  }
}

// Badge-only refresh: cheap, no trail stamping (nothing was shown to anyone).
export async function refreshAlertCount() {
  try {
    const { message } = await apiCall(NS + "alert_count")
    alerts.unread = Number(message) || 0
  } catch {
    /* badge is decorative — never surface an error for it */
  }
}

export async function markAlertRead(row) {
  if (!row || row.read) return
  try {
    const { message } = await apiCall(NS + "mark_alert_read", { log_name: row.name })
    row.read = 1
    if (message && typeof message.unread_total === "number") {
      alerts.unread = message.unread_total
    }
  } catch {
    /* a failed read-flip must not block navigation to the document */
  }
}

export async function markAllAlertsRead() {
  if (alerts.marking) return
  alerts.marking = true
  try {
    await apiCall(NS + "mark_all_alerts_read")
    alerts.rows.forEach((r) => {
      r.read = 1
    })
    await refreshAlertCount()
  } catch (e) {
    alerts.error = e
  } finally {
    alerts.marking = false
  }
}

export function openAlerts() {
  alerts.open = true
  loadAlerts()
}

export function closeAlerts() {
  alerts.open = false
}
