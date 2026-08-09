<template>
  <div class="relative mb-2">
    <!-- type="search" gives iOS the right keyboard and its own clear affordance;
         we add our own X too because Android does not render one. -->
    <input
      ref="el"
      :value="modelValue"
      type="search"
      inputmode="search"
      enterkeyhint="search"
      autocomplete="off"
      autocorrect="off"
      autocapitalize="none"
      spellcheck="false"
      :placeholder="placeholder"
      :aria-label="placeholder"
      class="min-h-secondary w-full rounded-xl border border-surface-line bg-surface pl-10 pr-11 text-[15px] text-ink placeholder:text-ink-faint focus:border-brand focus:outline-none"
      @input="$emit('update:modelValue', $event.target.value)"
      @keydown.esc="clear"
    />

    <svg
      class="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-ink-faint"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </svg>

    <!-- Full 44px target: a 16px glyph is not tappable with a thumb. -->
    <button
      v-if="modelValue"
      type="button"
      class="absolute right-0 top-1/2 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full text-[20px] text-ink-faint"
      aria-label="Clear search"
      @click="clear"
    >
      ×
    </button>

    <!-- Spinner only when the SERVER is searching (History). Client-side
         filtering is instant and must not flash a spinner at the user. -->
    <span
      v-else-if="busy"
      class="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin rounded-full border-2 border-surface-line border-t-brand-deep"
      aria-hidden="true"
    ></span>
  </div>
</template>

<script setup>
import { ref } from "vue"

defineProps({
  modelValue: { type: String, default: "" },
  placeholder: { type: String, default: "Search" },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(["update:modelValue"])

const el = ref(null)

function clear() {
  emit("update:modelValue", "")
  if (el.value) el.value.focus()
}
</script>
