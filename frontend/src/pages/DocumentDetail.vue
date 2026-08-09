<template>
  <div class="pb-44">
    <AppHeader :title="name" back />

    <div class="px-4">
      <Skeletons v-if="docStore.loading && !payload" :count="3" />

      <div
        v-else-if="docStore.error"
        class="flex flex-col items-center gap-3 px-6 py-14 text-center"
      >
        <div class="text-[15px] font-bold">Couldn't load this document</div>
        <p class="text-[13px] text-ink-muted">{{ docStore.error.display() }}</p>
        <button
          type="button"
          class="min-h-secondary w-40 rounded-xl bg-brand-deep px-4 text-[14px] font-bold text-white"
          @click="reload"
        >
          Retry
        </button>
      </div>

      <template v-else-if="payload">
        <div class="mb-2 flex items-center justify-between">
          <StatusPill :status="status" />
          <span class="text-[13px] text-ink-faint">{{ typeMeta.tag }}</span>
        </div>

        <!-- Hold banner: a held PO stays "Pending" but every action is off -->
        <div
          v-if="ctx.is_on_hold"
          class="mb-2 rounded-xl bg-warn-bg px-4 py-3 text-[13px] font-semibold text-warn-text"
        >
          On hold{{ holdBy ? ` by ${holdBy}` : "" }}{{ holdReason ? ` — “${holdReason}”` : "" }}
        </div>

        <!-- Amount-drift warning: the server WILL refuse; say it beforehand -->
        <div
          v-if="amountDrifted"
          class="mb-2 rounded-xl bg-danger-bg px-4 py-3 text-[13px] font-semibold text-danger-text"
        >
          Amount changed after submission ({{ money(d.ts_amount_at_submission) }} →
          {{ money(d.grand_total) }}). Approval will be refused — ask Purchase to
          resubmit.
        </div>

        <!-- Header card -->
        <div class="mb-2 rounded-xl border border-surface-line bg-surface p-4">
          <template v-if="payload.doctype === 'Purchase Order'">
            <div class="text-[13px] text-ink-muted">{{ d.supplier_name || d.supplier }}</div>
            <div class="tnum text-[26px] font-bold">{{ money(d.grand_total, { precise: true }) }}</div>
            <div class="mt-2 flex flex-col gap-1">
              <KV k="Category" :v="d.ts_purchase_category || '—'" />
              <KV k="PO date" :v="shortDate(d.transaction_date)" />
              <KV k="Submitted by" :v="d.ts_submitted_by || '—'" />
            </div>
          </template>
          <template v-else-if="payload.doctype === 'Material Request'">
            <div class="text-[13px] text-ink-muted">{{ d.material_request_type || "Purchase" }}</div>
            <div class="tnum text-[26px] font-bold">
              {{ money(d.estimated_value, { dashOnEmpty: true }) }}
            </div>
            <div class="mt-0.5 text-[13px] text-ink-faint">Est. value</div>
            <div class="mt-2 flex flex-col gap-1">
              <KV k="Required by" :v="shortDate(d.schedule_date)" />
              <KV k="Submitted by" :v="d.ts_mr_submitted_by || '—'" />
            </div>
          </template>
          <template v-else-if="payload.doctype === 'TS Budget Override Approval'">
            <div class="text-[13px] text-ink-muted">
              {{ d.reference_doctype }} {{ d.reference_name }}
            </div>
            <div class="tnum text-[26px] font-bold">{{ money(d.source_amount, { precise: true }) }}</div>
            <div class="mt-2 flex flex-col gap-1">
              <KV k="Cost centre" :v="d.cost_center || '—'" />
              <KV k="Breach" :v="breachText" />
              <KV k="Requested by" :v="d.submitted_by || '—'" />
            </div>
            <p v-if="d.submission_reason" class="mt-2 rounded-lg bg-surface-page p-2.5 text-[13px] text-ink-muted">
              “{{ d.submission_reason }}”
            </p>
          </template>
          <template v-else>
            <div class="text-[13px] text-ink-muted">{{ d.request_type }}</div>
            <div class="text-[20px] font-bold">
              {{ shortDate(d.from_date) }} → {{ shortDate(d.to_date) }}
            </div>
            <div class="mt-2 flex flex-col gap-1">
              <KV k="Requested by" :v="d.requested_by || '—'" />
              <KV v-if="d.token_number" k="Token" :v="d.token_number" />
            </div>
            <p v-if="d.reason" class="mt-2 rounded-lg bg-surface-page p-2.5 text-[13px] text-ink-muted">
              “{{ d.reason }}”
            </p>
          </template>
        </div>

        <div v-if="payload.items && payload.items.length" class="mb-2 rounded-xl border border-surface-line bg-surface p-4">
          <ItemLines :items="payload.items" />
        </div>

        <div
          v-if="(ctx.approval_chain && ctx.approval_chain.length) || (payload.approval_log && payload.approval_log.length)"
          class="mb-2 rounded-xl border border-surface-line bg-surface p-4"
        >
          <ApprovalTrail :chain="ctx.approval_chain || []" :log="payload.approval_log || []" />
        </div>
      </template>
    </div>

    <!-- Fixed action bar. NEVER empty-and-unexplained: when no action is
         available, one plain line says why. Approve and Reject are never in
         the same horizontal row. -->
    <div
      v-if="payload && !docStore.loading"
      class="safe-bottom fixed inset-x-0 bottom-0 z-30 mx-auto max-w-lg border-t border-surface-line bg-surface px-4 pt-2.5"
    >
      <template v-if="primary || secondary.length">
        <button
          v-if="primary"
          type="button"
          class="min-h-action mb-1.5 w-full rounded-xl bg-brand-deep text-[16px] font-bold text-white disabled:opacity-40"
          :disabled="busyHere || offline"
          @click="open(primary)"
        >
          {{ primary.label }}
        </button>
        <div v-if="secondary.length" class="flex flex-wrap gap-1.5">
          <button
            v-for="a in secondary"
            :key="a.key"
            type="button"
            class="min-h-secondary flex-1 basis-[calc(50%-6px)] rounded-xl border text-[14px] font-semibold disabled:opacity-40"
            :class="
              a.destructive
                ? 'border-danger-text text-danger-text'
                : 'border-surface-line text-ink-muted'
            "
            :disabled="busyHere || offline"
            @click="open(a)"
          >
            {{ a.label }}
          </button>
        </div>
      </template>
      <p v-else class="pb-2 pt-1 text-center text-[13px] text-ink-muted">
        {{ noActionReason }}
      </p>
    </div>

    <ActionSheet
      :action="sheetAction"
      :working="working"
      :error="sheetError"
      @confirm="confirmAction"
      @dismiss="sheetAction = null"
      @edited="sheetError = ''"
    />
  </div>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, ref, watch } from "vue"
import { useRouter } from "vue-router"

import ActionSheet from "@/components/ActionSheet.vue"
import AppHeader from "@/components/AppHeader.vue"
import ApprovalTrail from "@/components/ApprovalTrail.vue"
import ItemLines from "@/components/ItemLines.vue"
import Skeletons from "@/components/Skeletons.vue"
import StatusPill from "@/components/StatusPill.vue"
import {
  avpDeputyEnabled,
  docStore,
  isBusy,
  loadDocument,
  runAction,
} from "@/data/document.js"
import { showFlash } from "@/data/flash.js"
import { money, moneyCompact, shortDate, DOCTYPE_META } from "@/data/format.js"
import { session } from "@/data/session.js"

const props = defineProps({
  doctype: { type: String, required: true },
  name: { type: String, required: true },
})

const router = useRouter()

const KV = defineComponent({
  props: { k: String, v: String },
  setup(p) {
    return () =>
      h("div", { class: "flex justify-between gap-3 text-[13px]" }, [
        h("span", { class: "text-ink-faint" }, p.k),
        h("span", { class: "text-right font-semibold" }, p.v),
      ])
  },
})

const payload = computed(() => docStore.payload)
const d = computed(() => (payload.value && payload.value.doc) || {})
const ctx = computed(() => (payload.value && payload.value.context) || {})
const typeMeta = computed(() => DOCTYPE_META[props.doctype] || { tag: props.doctype })

const status = computed(
  () => d.value.ts_approval_status || d.value.ts_mr_status || d.value.status || ""
)

const holdBy = computed(() => ctx.value.held_by || ctx.value.hold_held_by || "")
const holdReason = computed(() => ctx.value.hold_reason || "")

const amountDrifted = computed(() => {
  if (props.doctype !== "Purchase Order") return false
  const at = Number(d.value.ts_amount_at_submission || 0)
  const now = Number(d.value.grand_total || 0)
  return at > 0 && Math.abs(now - at) > 0.005 && String(status.value).startsWith("Pending")
})

const breachText = computed(() => {
  const b = d.value.breach_type || ""
  const delta = Number(d.value.annual_breach_delta || d.value.monthly_breach_delta || 0)
  return delta ? `${b} · over by ${moneyCompact(delta)}` : b || "—"
})

const offline = ref(!navigator.onLine)
window.addEventListener("online", () => (offline.value = false))
window.addEventListener("offline", () => (offline.value = true))

const busyHere = computed(() => isBusy(props.doctype, props.name))

const avpAvailable = ref(false)

// ----- action derivation (buttons are visibility only; server re-validates)
const amountLine = computed(() => {
  if (props.doctype === "Purchase Order")
    return `${money(d.value.grand_total, { precise: true })} · ${d.value.supplier_name || ""}`
  if (props.doctype === "TS Budget Override Approval")
    return `${money(d.value.source_amount, { precise: true })} · ${d.value.reference_name || ""}`
  return ""
})

const actions = computed(() => {
  const c = ctx.value
  const out = []
  const dt = props.doctype
  if (dt === "Purchase Order" || dt === "Material Request") {
    const short = dt === "Purchase Order" ? "PO" : "MR"
    if (c.can_final_approve)
      out.push({
        key: "final_approve", label: "Final Approve", primary: true, allowComment: true,
        confirmTitle: `Final approve this ${short}?`,
        confirmSub: `${amountLine.value} — this submits the ${short}.`,
        confirmCta: dt === "Purchase Order" && d.value.grand_total
          ? `Yes, approve — ${moneyCompact(d.value.grand_total)}`
          : "Yes, final approve",
      })
    else if (c.can_approve)
      out.push({
        key: "approve", label: "Approve & Forward", primary: true, allowComment: true,
        confirmTitle: `Approve & forward this ${short}?`,
        confirmSub: `${amountLine.value} — it moves to the next approver.`,
        confirmCta: "Approve & Forward",
      })
    else if (c.can_review)
      out.push({
        key: "review", label: "Reviewed & Forward", primary: true, allowComment: true,
        confirmTitle: `Mark reviewed & forward?`,
        confirmSub: "It moves to the next approver.",
        confirmCta: "Reviewed & Forward",
      })
    if (c.can_send_to_md)
      out.push({
        key: "send_to_md", label: "Send to MD", primary: !out.length, allowComment: true,
        confirmTitle: "Send this PO to the MD?",
        confirmSub: `${amountLine.value} — the MD gets it for final approval.`,
        confirmCta: "Send to MD",
      })
    if (c.can_resume)
      out.push({
        key: dt === "Purchase Order" ? "resume_po" : "resume_mr",
        label: "Resume", primary: !out.length, allowComment: true,
        confirmTitle: `Resume this ${short}?`,
        confirmSub: "It returns to the same approval step.",
        confirmCta: "Resume",
      })
    if (c.can_resubmit)
      out.push({
        key: "resubmit", label: "Resubmit for Approval", primary: !out.length,
        confirmTitle: `Resubmit this ${short}?`,
        confirmSub: "The approval chain restarts from step 1.",
        confirmCta: "Resubmit for Approval",
      })
    if (avpAvailable.value)
      out.push({
        key: "avp_deputy", label: "Approve as AVP Deputy", primary: !out.length,
        needsReason: true, reasonLabel: "Reason (acting for AVP)", reasonMin: 1,
        confirmTitle: "Approve as AVP deputy?",
        confirmSub: "You are acting in place of the AVP on this MR.",
        confirmCta: "Approve as Deputy",
      })
    if (c.can_revise)
      out.push({
        key: "revise", label: "Revise", needsReason: true, reasonLabel: "Revision reason",
        reasonMin: 1, allowComment: true,
        confirmTitle: `Send this ${short} for revision?`,
        confirmSub: "It goes back to the submitter with your reason.",
        confirmCta: "Send for Revision",
      })
    if (c.can_hold)
      out.push({
        key: dt === "Purchase Order" ? "hold_po" : "hold_mr",
        label: "Hold", needsReason: true, reasonLabel: "Hold reason", reasonMin: 1,
        confirmTitle: `Put this ${short} on hold?`,
        confirmSub: "Everything freezes until it is resumed.",
        confirmCta: dt === "Purchase Order" ? "Hold PO" : "Put On Hold",
      })
    if (c.can_reject)
      out.push({
        key: "reject", label: "Reject", destructive: true, needsReason: true,
        reasonLabel: "Rejection reason", reasonMin: 1, allowComment: true,
        confirmTitle: `Reject this ${short}?`,
        confirmSub: `${amountLine.value} — this is final.`,
        confirmCta: `Reject this ${short}`,
      })
  } else if (dt === "TS Budget Override Approval") {
    if (c.can_approve)
      out.push({
        key: "boa_approve", label: "Approve Override", primary: true, allowComment: true,
        confirmTitle: "Approve this budget override?",
        confirmSub: `${amountLine.value} — the document resumes its normal approval route.`,
        confirmCta: "Approve Override",
      })
    if (c.can_reject)
      out.push({
        key: "boa_reject", label: "Reject", destructive: true, needsReason: true,
        reasonLabel: "Rejection reason", reasonMin: 10,
        confirmTitle: "Reject this budget override?",
        confirmSub: "The source document is rejected too. Minimum 10 characters.",
        confirmCta: "Reject Override",
      })
  } else if (dt === "TS Post Dated Entry Request") {
    if (c.can_approve)
      out.push({
        key: "pd_approve", label: "Approve Request", primary: true,
        confirmTitle: "Approve this post-dated window?",
        confirmSub: `${shortDate(d.value.from_date)} → ${shortDate(d.value.to_date)} (${d.value.request_type || ""})`,
        confirmCta: "Approve Request",
      })
    if (c.can_reject)
      out.push({
        key: "pd_reject", label: "Reject", destructive: true, needsReason: true,
        reasonLabel: "Rejection reason", reasonMin: 1,
        confirmTitle: "Reject this post-dated request?",
        confirmSub: "The requester will see your reason.",
        confirmCta: "Reject Request",
      })
  }
  return out
})

const primary = computed(() => actions.value.find((a) => a.primary))
const secondary = computed(() => actions.value.filter((a) => !a.primary))

const noActionReason = computed(() => {
  const c = ctx.value
  if (c.is_on_hold) return "On hold — it must be resumed before anything else."
  if (String(status.value) === "Pending Budget Override")
    return "Waiting on the budget override decision — nobody in the chain can act until then."
  const submitter = d.value.ts_submitted_by || d.value.ts_mr_submitted_by
  if (submitter && submitter === session.user)
    return "You submitted this one — it needs the next approver, not you."
  if (String(status.value) === "Approved") return "Already approved."
  if (String(status.value) === "Rejected") return "Already rejected."
  return "This document is not at your step."
})

// ----- sheet orchestration
const sheetAction = ref(null)
const working = ref(false)
const sheetError = ref("")

function open(action) {
  sheetError.value = ""
  sheetAction.value = action
}

async function confirmAction({ reason, comment }) {
  if (!sheetAction.value) return
  working.value = true
  sheetError.value = ""
  try {
    const r = await runAction(sheetAction.value.key, { reason, comment })
    if (r.busy) return
    sheetAction.value = null
    if (r.already) showFlash("Already actioned — this document had moved on.", "warn")
    else showFlash(`Done — ${props.name}`)
    if (r.warnings && r.warnings.length) showFlash(r.warnings[0].message, "warn")
    // Always land back on the refetched inbox — never a client-side guess.
    router.replace({ name: "inbox" })
  } catch (e) {
    if (e.sessionExpired) {
      showFlash("Your session expired — the action did NOT run. Sign in again.", "warn")
      router.replace({ name: "login" })
      return
    }
    if (e.needsReload) {
      window.location.reload()
      return
    }
    sheetError.value = e.display()
  } finally {
    working.value = false
  }
}

async function reload() {
  await loadDocument(props.doctype, props.name)
  if (
    props.doctype === "Material Request" &&
    String(status.value) === "Pending AVP"
  ) {
    avpAvailable.value = await avpDeputyEnabled()
  } else {
    avpAvailable.value = false
  }
}

onMounted(reload)
watch(() => [props.doctype, props.name], reload)
</script>
