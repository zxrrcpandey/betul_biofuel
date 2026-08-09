<template>
  <nav
    class="safe-bottom fixed inset-x-0 bottom-0 z-30 mx-auto max-w-lg border-t border-surface-line bg-surface"
    aria-label="Main navigation"
  >
    <!-- overflow-hidden + min-w-0 + truncate: with the 4th tab the labels must
         be allowed to clip on a 320px screen rather than widen the flex row
         and push a tab off the edge. -->
    <div class="flex overflow-hidden">
      <router-link
        v-for="t in tabs"
        :key="t.name"
        :to="{ name: t.name }"
        class="flex min-h-[56px] min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-1 pt-1.5"
        :class="active === t.name ? 'text-brand-deep' : 'text-ink-faint'"
        :aria-current="active === t.name ? 'page' : undefined"
      >
        <svg
          viewBox="0 0 24 24"
          class="h-6 w-6 shrink-0"
          fill="none"
          stroke="currentColor"
          stroke-width="1.9"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path v-for="(p, i) in t.paths" :key="i" :d="p" />
        </svg>
        <span class="w-full truncate text-center text-[13px] font-semibold">{{ t.label }}</span>
      </router-link>
    </div>
  </nav>
</template>

<script setup>
import { computed } from "vue"
import { useRoute } from "vue-router"

const route = useRoute()
const active = computed(() => route.name)

// Server-rendered per user (www/exec.py → overview_visible): kill switch AND
// audience. Read ONCE at module load — it cannot change without a page load,
// and re-reading it per render would only add a way to disagree with itself.
const showOverview = Number((window.execEnv || {}).overview) === 1

const TABS = [
  {
    name: "inbox",
    label: "Inbox",
    // tray
    paths: ["M3 13h4l2 3h6l2-3h4", "M5 5h14l2 8v6H3v-6l2-8z"],
  },
  {
    name: "overview",
    label: "Overview",
    // gauge — a reading, not an action; deliberately unlike the action tabs
    paths: ["M12 13l3.5-3.5", "M4.2 17a9 9 0 1 1 15.6 0", "M4.2 17h15.6"],
    when: () => showOverview,
  },
  {
    name: "history",
    label: "History",
    // clock
    paths: ["M12 7v5l3 2", "M12 21a9 9 0 1 0-9-9", "M3 12v-4m0 4h4"],
  },
  {
    name: "settings",
    label: "Settings",
    // sliders
    paths: ["M4 8h10", "M18 8h2", "M14 8a2 2 0 1 0 4 0a2 2 0 1 0-4 0", "M4 16h2", "M10 16h10", "M6 16a2 2 0 1 0 4 0a2 2 0 1 0-4 0"],
  },
]

const tabs = TABS.filter((t) => !t.when || t.when())
</script>
