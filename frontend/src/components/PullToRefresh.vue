<template>
  <div
    class="relative"
    @touchstart.passive="onStart"
    @touchmove.passive="onMove"
    @touchend="onEnd"
  >
    <!-- Pull indicator: only visible while pulling or refreshing -->
    <div
      class="pointer-events-none absolute inset-x-0 top-0 z-10 flex justify-center"
      :style="{ height: `${indicatorH}px`, opacity: indicatorH > 4 ? 1 : 0 }"
    >
      <div class="flex items-end pb-2">
        <svg
          viewBox="0 0 24 24"
          class="h-6 w-6 text-brand-deep"
          :class="{ 'exec-spin': refreshing }"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          :style="refreshing ? undefined : { transform: `rotate(${pull * 2.2}deg)` }"
        >
          <path d="M21 12a9 9 0 1 1-3-6.7" />
          <path d="M21 4v5h-5" />
        </svg>
      </div>
    </div>

    <div
      :style="{
        transform: `translateY(${indicatorH}px)`,
        transition: dragging ? 'none' : 'transform 0.2s ease',
      }"
    >
      <slot></slot>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

const props = defineProps({
  refreshing: { type: Boolean, default: false },
  threshold: { type: Number, default: 70 },
})
const emit = defineEmits(["refresh"])

const pull = ref(0) // raw px pulled (after rubber-band resistance)
const dragging = ref(false)
let startY = 0
let active = false

// While refreshing, hold the indicator open; otherwise track the finger.
const indicatorH = computed(() => (props.refreshing ? 48 : pull.value))

// Only engage when the page is already scrolled to the very top — otherwise
// this is a normal scroll and must not be hijacked.
function atTop() {
  return (window.scrollY || document.documentElement.scrollTop || 0) <= 0
}

function onStart(e) {
  if (props.refreshing || !atTop()) return
  active = true
  startY = e.touches[0].clientY
  pull.value = 0
}

function onMove(e) {
  if (!active) return
  const dy = e.touches[0].clientY - startY
  if (dy <= 0) {
    active = false
    dragging.value = false
    pull.value = 0
    return
  }
  dragging.value = true
  pull.value = Math.min(dy * 0.5, props.threshold * 1.6) // rubber-band + cap
}

function onEnd() {
  if (!active) return
  active = false
  dragging.value = false
  if (pull.value >= props.threshold) emit("refresh")
  pull.value = 0
}
</script>

<style scoped>
@keyframes exec-spin {
  to {
    transform: rotate(360deg);
  }
}
.exec-spin {
  animation: exec-spin 0.8s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .exec-spin {
    animation-duration: 1.6s;
  }
}
</style>
