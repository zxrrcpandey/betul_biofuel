<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-end justify-center bg-black/45"
    @click.self="$emit('close')"
  >
    <div
      class="flex w-full max-w-lg flex-col overflow-hidden rounded-t-2xl bg-surface"
      style="max-height: 85dvh"
      role="dialog"
      aria-modal="true"
      aria-labelledby="stat-sheet-title"
    >
      <div class="flex items-start justify-between gap-2 border-b border-surface-line px-4 py-3">
        <div class="min-w-0">
          <h2 id="stat-sheet-title" class="text-[16px] font-bold">{{ title }}</h2>
          <p v-if="subtitle" class="mt-0.5 text-[13px] text-ink-faint">{{ subtitle }}</p>
        </div>
        <button
          type="button"
          class="-mr-2 flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-2xl text-ink-faint"
          aria-label="Close"
          @click="$emit('close')"
        >
          ×
        </button>
      </div>

      <div class="flex-1 overflow-y-auto overscroll-contain px-4 py-3">
        <div v-if="!rows.length" class="px-6 py-10 text-center text-[14px] text-ink-muted">
          Nothing to show here.
        </div>
        <div v-else class="flex flex-col gap-2">
          <component
            :is="r.to ? 'router-link' : 'div'"
            v-for="(r, i) in rows"
            :key="i"
            :to="r.to"
            class="flex min-h-secondary items-center justify-between gap-3 rounded-xl border border-surface-line bg-surface px-3 py-2.5"
            :class="r.to ? 'active:bg-surface-page' : ''"
            @click="r.to && $emit('close')"
          >
            <div class="min-w-0">
              <div class="truncate text-[15px] font-semibold">{{ r.label }}</div>
              <div v-if="r.sub" class="mt-0.5 text-[13px] text-ink-faint">{{ r.sub }}</div>
            </div>
            <span
              class="tnum shrink-0 whitespace-nowrap text-[15px] font-bold"
              :class="r.tone === 'warn' ? 'text-warn-text' : 'text-ink'"
            >
              {{ r.value }}
            </span>
          </component>
        </div>
        <p v-if="footnote" class="mt-3 text-[13px] text-ink-faint">{{ footnote }}</p>
      </div>
      <div class="safe-bottom"></div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: "" },
  subtitle: { type: String, default: "" },
  footnote: { type: String, default: "" },
  // [{ label, value, sub?, tone?, to? }] — `to` makes the row a router-link.
  rows: { type: Array, default: () => [] },
})
defineEmits(["close"])
</script>
