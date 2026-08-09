<template>
  <div v-if="items.length" class="flex flex-col gap-1.5">
    <div class="text-[13px] font-bold">Items ({{ items.length }})</div>
    <div
      v-for="(it, i) in shown"
      :key="i"
      class="rounded-lg border border-surface-line bg-surface-page px-3 py-2"
    >
      <div class="flex items-baseline justify-between gap-2">
        <span class="min-w-0 truncate text-[14px] font-semibold">
          {{ it.item_name || it.item_code }}
        </span>
        <span class="tnum whitespace-nowrap text-[14px] font-bold">
          {{ money(it.amount, { dashOnEmpty: true }) }}
        </span>
      </div>
      <div class="text-[13px] text-ink-muted">
        {{ qtyText(it) }}<span v-if="it.cost_center"> · {{ it.cost_center }}</span>
      </div>
    </div>
    <button
      v-if="items.length > 3 && !expanded"
      type="button"
      class="min-h-secondary rounded-lg border border-surface-line bg-surface text-[14px] font-semibold text-ink-muted"
      @click="expanded = true"
    >
      Show all {{ items.length }} items
    </button>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { money } from "@/data/format.js"

const props = defineProps({ items: { type: Array, default: () => [] } })

const expanded = ref(false)
const shown = computed(() => (expanded.value ? props.items : props.items.slice(0, 3)))

function qtyText(it) {
  const qty = Number(it.qty || 0)
  const rate = Number(it.rate || 0)
  const base = `${qty} ${it.uom || ""}`.trim()
  return rate ? `${base} @ ${money(rate, { precise: true })}` : base
}
</script>
