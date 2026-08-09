<template>
  <section class="rounded-xl border border-surface-line bg-surface p-4 shadow-sm">
    <h2 class="text-[15px] font-bold">{{ title }}</h2>
    <!-- The scope line is load-bearing: the card border is only 1.33:1 against
         the page, so grouping is carried by the heading, never by the box. -->
    <p class="mt-0.5 text-[13px] text-ink-faint">{{ scope }}</p>

    <!-- ERROR: keeps the title, shows NO number. Never painted red — red means
         business danger in this system; a network hiccup is not that. -->
    <div v-if="state === 'error'" class="mt-3" role="status">
      <div class="text-[15px] font-bold">{{ errorTitle }}</div>
      <p class="mt-1 text-[13px] text-ink-muted">{{ errorDetail }}</p>
      <button
        type="button"
        class="min-h-secondary mt-3 w-40 rounded-xl bg-brand-deep text-[14px] font-bold text-white"
        @click="$emit('retry')"
      >
        Retry
      </button>
    </div>

    <!-- NOT CONFIGURED: a distinct third state. No number, and deliberately NO
         retry button — retrying will never help, and offering one is a lie. -->
    <div v-else-if="state === 'unconfigured'" class="mt-3">
      <span
        class="inline-block rounded-full bg-blocked-bg px-2.5 py-0.5 text-[13px] font-bold text-blocked-text"
        >Not set up yet</span
      >
      <p class="mt-2 text-[13px] text-ink-muted">{{ errorDetail }}</p>
    </div>

    <div v-else-if="state === 'loading'" class="mt-3" aria-hidden="true">
      <div class="skeleton h-[38px] w-32"></div>
      <div class="skeleton mt-2 h-4 w-48"></div>
      <div class="skeleton mt-2 h-4 w-40"></div>
    </div>

    <div v-else class="mt-3">
      <slot></slot>
    </div>
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  scope: { type: String, default: "" },
  state: { type: String, default: "ok" }, // ok | loading | error | unconfigured
  errorTitle: { type: String, default: "Couldn't load this" },
  errorDetail: { type: String, default: "" },
})
defineEmits(["retry"])
</script>
