import { reactive } from "vue"

import { apiCall } from "./session.js"

export const history = reactive({
  loading: false,
  error: null,
  items: [],
  loadedAt: null,
  search: "",
  capped: false,
  limit: 50,
  totalActions: 0,
})

// Guards against out-of-order responses: typing "PO" then "PO-5" can have the
// slower first request land last and overwrite the newer results.
let seq = 0

export async function loadHistory(search) {
  const mine = ++seq
  history.loading = true
  history.error = null
  try {
    const { message } = await apiCall(
      "trustbit_ethanol.ts_gate_entry.ts_exec_api.get_my_actions",
      search ? { search } : {}
    )
    if (mine !== seq) return // a newer search already answered
    history.items = (message && message.items) || []
    history.search = (message && message.search) || ""
    history.capped = Boolean(message && message.capped)
    history.limit = (message && message.limit) || 50
    history.totalActions = (message && message.total_actions) || 0
    history.loadedAt = Date.now()
  } catch (e) {
    if (mine !== seq) return
    history.error = e
  } finally {
    if (mine === seq) history.loading = false
  }
}
