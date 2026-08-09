import { reactive } from "vue"

import { apiCall } from "./session.js"
import { loadInbox } from "./inbox.js"

const NS = "trustbit_ethanol.ts_gate_entry."

export const docStore = reactive({
  loading: false,
  error: null,
  payload: null, // {doctype, name, doc, items, approval_log, context}
})

// ONE in-flight registry keyed `${doctype}:${name}` — busy is set BEFORE the
// fetch and NEVER re-enabled against the same document version: on failure we
// refetch first. This is the double-submit guard.
const busy = reactive({ key: null })

export function isBusy(doctype, name) {
  return busy.key === `${doctype}:${name}`
}

export async function loadDocument(doctype, name) {
  docStore.loading = true
  docStore.error = null
  try {
    const { message } = await apiCall(NS + "ts_exec_api.get_document", {
      doctype,
      docname: name,
    })
    docStore.payload = message
  } catch (e) {
    docStore.error = e
    docStore.payload = null
  } finally {
    docStore.loading = false
  }
}

// Action key → endpoint + argument shape. The server re-validates everything;
// these are just transport descriptions.
const ACTIONS = {
  review: { m: "ts_po_approval.approve_document", args: (d, n, x) => ({ doctype: d, docname: n, comment: x.comment || "" }) },
  approve: { m: "ts_po_approval.approve_document", args: (d, n, x) => ({ doctype: d, docname: n, comment: x.comment || "" }) },
  final_approve: { m: "ts_po_approval.approve_document", args: (d, n, x) => ({ doctype: d, docname: n, comment: x.comment || "" }) },
  send_to_md: { m: "ts_po_approval.send_to_md", args: (d, n, x) => ({ docname: n, comment: x.comment || "" }) },
  revise: { m: "ts_po_approval.revise_document", args: (d, n, x) => ({ doctype: d, docname: n, reason: x.reason || "", comment: x.comment || "" }) },
  reject: { m: "ts_po_approval.reject_document", args: (d, n, x) => ({ doctype: d, docname: n, reason: x.reason || "", comment: x.comment || "" }) },
  resubmit: { m: "ts_po_approval.resubmit_document", args: (d, n) => ({ doctype: d, docname: n }) },
  hold_po: { m: "ts_po_approval.hold_po", args: (d, n, x) => ({ docname: n, reason: x.reason || "" }) },
  resume_po: { m: "ts_po_approval.resume_po", args: (d, n, x) => ({ docname: n, comment: x.comment || "" }) },
  hold_mr: { m: "ts_po_approval.hold_mr", args: (d, n, x) => ({ docname: n, reason: x.reason || "" }) },
  resume_mr: { m: "ts_po_approval.resume_mr", args: (d, n, x) => ({ docname: n, comment: x.comment || "" }) },
  avp_deputy: { m: "ts_avp_deputy.approve_as_avp_deputy", args: (d, n, x) => ({ mr_name: n, reason: x.reason || "" }) },
  boa_approve: { m: "ts_budget_override.approve_budget_override", args: (d, n, x) => ({ name: n, comment: x.comment || "" }) },
  boa_reject: { m: "ts_budget_override.reject_budget_override", args: (d, n, x) => ({ name: n, comment: x.reason || "" }) },
  pd_approve: { m: "ts_post_dated.approve_request", args: (d, n) => ({ request_name: n }) },
  pd_reject: { m: "ts_post_dated.reject_request", args: (d, n, x) => ({ request_name: n, reason: x.reason || "" }) },
}

function currentStatus(payload) {
  const d = payload && payload.doc
  if (!d) return ""
  return d.ts_approval_status || d.ts_mr_status || d.status || ""
}

// Runs an action. Resolves {ok:true, already?:true, warnings?} or throws the
// FrappeError for the sheet to display. On error we refetch and, if the
// status already moved past the expected pending state, treat it as done —
// "Already approved" is the difference between trust and a call to IT.
export async function runAction(key, extra = {}) {
  const p = docStore.payload
  if (!p) throw new Error("No document loaded")
  const spec = ACTIONS[key]
  if (!spec) throw new Error(`Unknown action ${key}`)
  const busyKey = `${p.doctype}:${p.name}`
  if (busy.key) return { ok: false, busy: true }
  busy.key = busyKey

  const statusBefore = currentStatus(p)
  try {
    const { warnings } = await apiCall(NS + spec.m, spec.args(p.doctype, p.name, extra), {
      timeoutMs: 30000,
    })
    // Success: refetch server truth for both screens — never a client guess.
    await Promise.all([loadDocument(p.doctype, p.name), loadInbox()])
    return { ok: true, warnings }
  } catch (e) {
    // Duplicate-submit recovery: reload, and if the doc has already moved on
    // from the state we acted against, report success-already-done.
    try {
      await loadDocument(p.doctype, p.name)
      const after = currentStatus(docStore.payload)
      if (after && statusBefore && after !== statusBefore) {
        await loadInbox()
        return { ok: true, already: true }
      }
    } catch {
      /* keep original error */
    }
    throw e
  } finally {
    busy.key = null
  }
}

export async function avpDeputyEnabled() {
  try {
    const { message } = await apiCall(NS + "ts_avp_deputy.is_avp_deputy_enabled")
    return Boolean(Number(message))
  } catch {
    return false
  }
}
