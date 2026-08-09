<template>
  <div class="flex flex-wrap gap-1.5 pb-2">
    <button
      v-for="f in FILTERS"
      :key="f.key"
      type="button"
      class="min-h-[48px] rounded-full border px-3 text-[13px] font-semibold"
      :class="
        inbox.filter === f.key
          ? 'border-brand-deep bg-brand-deep text-white'
          : 'border-surface-line bg-surface text-ink-muted'
      "
      @click="inbox.filter = f.key"
    >
      {{ f.key }} · {{ countFor(f.key) }}
    </button>
  </div>
</template>

<script setup>
import { FILTERS, countFor, inbox } from "@/data/inbox.js"
</script>
