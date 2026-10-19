<template>
  <header class="flex items-center justify-between px-4 pb-2 pt-4">
    <div class="flex items-center gap-2">
      <button
        v-if="back"
        type="button"
        class="-ml-2 flex h-12 w-12 items-center justify-center rounded-full text-2xl text-ink-muted"
        aria-label="Back"
        @click="goBack"
      >
        ‹
      </button>
      <!-- Logo variant keeps the <h1> so the page still has exactly one level-1
           heading; the wordmark is the visible label and the sr-only text keeps
           the page name for screen readers, which an image alone would lose. -->
      <h1 v-if="logo" class="flex min-w-0 items-center">
        <img :src="trustbitLogo" alt="Trustbit" class="h-7 w-auto shrink-0" />
        <span class="sr-only">{{ title }}</span>
      </h1>
      <h1 v-else class="truncate text-xl font-bold">{{ title }}</h1>
    </div>
    <div class="flex items-center gap-1">
      <button
        v-if="bell"
        type="button"
        class="relative flex h-12 w-12 items-center justify-center rounded-full text-ink-muted"
        :aria-label="bellLabel"
        :aria-expanded="alerts.open"
        aria-controls="alerts-title"
        @click="openAlerts"
      >
        <svg
          viewBox="0 0 24 24"
          class="h-6 w-6"
          fill="none"
          stroke="currentColor"
          stroke-width="1.9"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        <span
          v-if="alerts.unread > 0"
          class="absolute right-1.5 top-1.5 min-w-[20px] rounded-full bg-brand-deep px-1 text-center text-[13px] font-bold leading-5 text-white"
          aria-hidden="true"
          >{{ alerts.unread > 99 ? "99+" : alerts.unread }}</span
        >
      </button>
      <button
        v-if="refreshable"
        type="button"
        class="flex h-12 w-12 items-center justify-center rounded-full text-ink-muted disabled:opacity-40"
        :aria-label="refreshing ? 'Refreshing' : 'Refresh'"
        :disabled="refreshing"
        @click="$emit('refresh')"
      >
        <svg
          viewBox="0 0 24 24"
          class="h-6 w-6"
          :class="{ 'exec-spin': refreshing }"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M21 12a9 9 0 1 1-3-6.7" />
          <path d="M21 4v5h-5" />
        </svg>
      </button>
      <div class="relative">
      <button
        ref="avatar"
        type="button"
        class="relative flex h-12 w-12 items-center justify-center rounded-full bg-brand-deep text-[13px] font-bold text-white"
        :aria-label="alertCount > 0 ? `Account menu, usage needs a look` : 'Account menu'"
        :aria-expanded="menuOpen"
        aria-controls="account-menu"
        @click="menuOpen = !menuOpen"
      >
        {{ initials }}
        <!-- Usage moved off the tab bar; this dot keeps its "needs a look"
             signal visible without opening the menu. -->
        <span
          v-if="alertCount > 0"
          class="absolute right-0.5 top-0.5 h-3 w-3 rounded-full border-2 border-surface bg-warn-text"
          aria-hidden="true"
        ></span>
      </button>
      <!-- Backdrop: a tap anywhere else closes the menu. -->
      <div v-if="menuOpen" class="fixed inset-0 z-40" aria-hidden="true" @click="menuOpen = false"></div>
      <div
        v-if="menuOpen"
        id="account-menu"
        class="absolute right-0 top-12 z-40 w-56 rounded-xl border border-surface-line bg-surface p-2 shadow-lg"
      >
        <div class="truncate px-3 py-2 text-[13px] text-ink-muted">{{ session.user }}</div>
        <router-link :to="{ name: 'history' }" :class="MENU_ITEM" @click="menuOpen = false">
          <svg viewBox="0 0 24 24" class="h-5 w-5 shrink-0 text-ink-muted" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 7v5l3 2" /><path d="M12 21a9 9 0 1 0-9-9" /><path d="M3 12v-4m0 4h4" />
          </svg>
          History
        </router-link>
        <router-link v-if="showUsage" :to="{ name: 'usage' }" :class="MENU_ITEM" @click="menuOpen = false">
          <svg viewBox="0 0 24 24" class="h-5 w-5 shrink-0 text-ink-muted" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M4 20V10" /><path d="M10 20V4" /><path d="M16 20v-7" /><path d="M22 20H2" />
          </svg>
          <span class="flex-1">Usage</span>
          <span
            v-if="alertCount > 0"
            class="min-w-[20px] rounded-full bg-warn-text px-1 text-center text-[13px] font-bold leading-5 text-white"
            aria-hidden="true"
            >{{ alertCount }}</span
          >
          <span v-if="alertCount > 0" class="sr-only">, {{ alertCount }} need a look</span>
        </router-link>
        <div class="my-1 border-t border-surface-line"></div>
        <button
          type="button"
          class="w-full rounded-lg px-3 py-3.5 text-left text-[15px] font-semibold text-danger-text"
          @click="doLogout"
        >
          Sign out
        </button>
      </div>
      </div>
    </div>
  </header>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { useRouter } from "vue-router"

import { alerts, openAlerts } from "@/data/alerts.js"
import { logout, session } from "@/data/session.js"
import { alertCount, ensureUsageLoaded } from "@/data/usage.js"

const props = defineProps({
  title: { type: String, required: true },
  back: { type: Boolean, default: false },
  refreshable: { type: Boolean, default: false },
  refreshing: { type: Boolean, default: false },
  bell: { type: Boolean, default: false },
  // Show the Trustbit wordmark instead of the title text (home tab only).
  logo: { type: Boolean, default: false },
})
defineEmits(["refresh"])

// Same asset Login.vue renders. Bare /assets is cached ~1yr by the browser, so
// a CHANGED logo must ship under a NEW filename or phones keep the old bytes.
const trustbitLogo = "/assets/trustbit_ethanol/exec/icons/brand-trustbit-v2.png"

// The badge is aria-hidden, so the BUTTON must carry the count — otherwise a
// screen reader announces only "3, button".
const bellLabel = computed(() =>
  alerts.unread > 0 ? `Alerts, ${alerts.unread} unread` : "Alerts"
)

const router = useRouter()
const menuOpen = ref(false)
const avatar = ref(null)
const MENU_ITEM = "flex min-h-secondary items-center gap-3 rounded-lg px-3 text-[15px] font-semibold text-ink active:bg-surface-page"

// Server-rendered per user (www/exec.py): kill switch AND audience.
const showUsage = Number((window.execEnv || {}).usage) === 1

// History and Usage are reached from the menu, so a back-page can be the
// first page of the session (a bookmark, a reopened PWA) — go home then.
function goBack() {
  if (window.history.state && window.history.state.back) router.back()
  else router.replace({ name: "inbox" })
}

function onKey(e) {
  if (e.key === "Escape" && menuOpen.value) {
    menuOpen.value = false
    if (avatar.value) avatar.value.focus()
  }
}
watch(menuOpen, (open) => {
  if (open) document.addEventListener("keydown", onKey)
  else document.removeEventListener("keydown", onKey)
})

// Once per app open (ensureUsageLoaded dedupes), so the avatar dot can flag
// attention before Usage is opened. Only for the accounts that see Usage.
onMounted(() => {
  if (showUsage) ensureUsageLoaded()
})
onUnmounted(() => document.removeEventListener("keydown", onKey))

const initials = computed(() => {
  const u = session.user || ""
  const namePart = u.split("@")[0] || ""
  const bits = namePart.split(/[._-]/).filter(Boolean)
  const two = (bits[0] ? bits[0][0] : "") + (bits[1] ? bits[1][0] : "")
  return (two || namePart.slice(0, 2) || "U").toUpperCase()
})

async function doLogout() {
  menuOpen.value = false
  await logout()
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
