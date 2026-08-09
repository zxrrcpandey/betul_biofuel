<template>
  <span
    class="inline-block whitespace-nowrap rounded-full px-2.5 py-0.5 text-[13px] font-bold uppercase tracking-wide"
    :class="toneClass"
  >
    {{ meta.label }}
  </span>
</template>

<script setup>
import { computed } from "vue"

import { statusMeta } from "@/data/format.js"

const props = defineProps({ status: { type: String, default: "" } })

const meta = computed(() => statusMeta(props.status))
const toneClass = computed(() => {
  switch (meta.value.tone) {
    case "ok":
      return "bg-ok-bg text-ok-text"
    case "danger":
      return "bg-danger-bg text-danger-text"
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
</script>
