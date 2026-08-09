import { reactive } from "vue"

import { apiCall } from "./session.js"

export const inbox = reactive({
  loading: false,
  error: null, // FrappeError | null — NEVER render the empty state on error
  items: [],
  queues: [],
  total: 0,
  filter: "All",
  loadedAt: null,
})

export const FILTERS = [
  { key: "All", doctype: null },
  { key: "PO", doctype: "Purchase Order" },
  { key: "MR", doctype: "Material Request" },
  { key: "Budget", doctype: "TS Budget Override Approval" },
  { key: "Post-Dated", doctype: "TS Post Dated Entry Request" },
]

export async function loadInbox() {
  inbox.loading = true
  inbox.error = null
  try {
    const { message } = await apiCall(
      "trustbit_ethanol.ts_gate_entry.ts_exec_api.get_inbox"
    )
    inbox.items = (message && message.items) || []
    inbox.queues = (message && message.queues) || []
    inbox.total = (message && message.total) || 0
    inbox.loadedAt = Date.now()
  } catch (e) {
    inbox.error = e
  } finally {
    inbox.loading = false
  }
}

export function countFor(filterKey) {
  const f = FILTERS.find((x) => x.key === filterKey)
  if (!f) return 0
  if (!f.doctype) return inbox.total
  return inbox.items.filter((i) => i.doctype === f.doctype).length
}

export function filteredItems() {
  const f = FILTERS.find((x) => x.key === inbox.filter)
  if (!f || !f.doctype) return inbox.items
  return inbox.items.filter((i) => i.doctype === f.doctype)
}
