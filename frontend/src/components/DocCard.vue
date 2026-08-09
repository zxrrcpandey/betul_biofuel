<template>
  <router-link
    :to="{ name: 'document', params: { doctype: item.doctype, name: item.name } }"
    class="block min-h-row rounded-xl border border-surface-line bg-surface p-3 shadow-sm active:bg-surface-page"
  >
    <div class="mb-0.5 flex items-center justify-between gap-2">
      <span
        class="rounded px-1.5 py-px text-[13px] font-bold tracking-wide"
        :class="tagClass"
        >{{ meta.tag }}</span
      >
      <span class="tnum text-[15px] font-bold">{{ amountText }}</span>
    </div>
    <div class="truncate text-[15px] font-bold">{{ title }}</div>
    <div class="truncate text-[13px] text-ink-muted">{{ subtitle }}</div>
    <div class="mt-1 flex items-center justify-between text-[13px] text-ink-faint">
      <StatusPill :status="item.status" />
      <span class="font-semibold text-warn-text">waiting {{ waiting }}</span>
    </div>
  </router-link>
</template>

<script setup>
import { computed } from "vue"

import { DOCTYPE_META, age, moneyCompact, shortDate } from "@/data/format.js"

import StatusPill from "./StatusPill.vue"

const props = defineProps({ item: { type: Object, required: true } })

const meta = computed(
  () => DOCTYPE_META[props.item.doctype] || { tag: props.item.doctype, tone: "neutral" }
)

const tagClass = computed(() => {
  switch (meta.value.tone) {
    case "ok":
      return "bg-ok-bg text-ok-text"
    case "info":
      return "bg-info-bg text-info-text"
    case "blocked":
      return "bg-blocked-bg text-blocked-text"
    case "warn":
      return "bg-warn-bg text-warn-text"
    default:
      return "bg-surface-line text-ink-muted"
  }
})

const title = computed(() => {
  const i = props.item
  if (i.doctype === "TS Budget Override Approval") return i.title || i.name
  return i.name
})

const subtitle = computed(() => {
  const i = props.item
  if (i.doctype === "Purchase Order") {
    const cat = i.ts_purchase_category ? ` · ${i.ts_purchase_category}` : ""
    return `${i.supplier_name || i.supplier || ""}${cat}`
  }
  if (i.doctype === "Material Request") {
    return `${i.material_request_type || "Purchase"} · needed by ${shortDate(i.schedule_date)}`
  }
  if (i.doctype === "TS Budget Override Approval") {
    return `${i.reference_doctype || ""} ${i.reference_name || ""} · ${i.cost_center || ""}`
  }
  if (i.doctype === "TS Post Dated Entry Request") {
    return `${i.request_type || ""} · ${shortDate(i.from_date)} → ${shortDate(i.to_date)}`
  }
  return ""
})

const amountText = computed(() => {
  const i = props.item
  if (i.doctype === "Purchase Order") return moneyCompact(i.grand_total)
  if (i.doctype === "Material Request")
    return moneyCompact(i.estimated_value, { dashOnEmpty: true })
  if (i.doctype === "TS Budget Override Approval") return moneyCompact(i.source_amount)
  return ""
})

const waiting = computed(() => age(props.item.modified) || "just now")
</script>
