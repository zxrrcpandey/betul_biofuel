import { reactive } from "vue"

import { apiCall } from "./session.js"

export const history = reactive({
  loading: false,
  error: null,
  items: [],
  loadedAt: null,
})

export async function loadHistory() {
  history.loading = true
  history.error = null
  try {
    const { message } = await apiCall(
      "trustbit_ethanol.ts_gate_entry.ts_exec_api.get_my_actions"
    )
    history.items = (message && message.items) || []
    history.loadedAt = Date.now()
  } catch (e) {
    history.error = e
  } finally {
    history.loading = false
  }
}
