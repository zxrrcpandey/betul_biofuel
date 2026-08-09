<template>
  <div class="pb-28">
    <AppHeader
      title="Approvals"
      logo
      bell
      refreshable
      :refreshing="inbox.loading"
      @refresh="loadInbox"
    />
    <AlertsSheet />
    <PullToRefresh :refreshing="inbox.loading" @refresh="loadInbox">

    <!-- Success-only flash -->
    <div
      v-if="flash.text"
      class="mx-4 mb-2 rounded-xl px-4 py-3 text-[14px] font-semibold"
      :class="flash.tone === 'ok' ? 'bg-ok-bg text-ok-text' : 'bg-warn-bg text-warn-text'"
    >
      {{ flash.text }}
    </div>

    <!-- Branch B notice: IT flagged a password change — non-blocking -->
    <div
      v-if="pwResetLink"
      class="mx-4 mb-2 flex items-start justify-between gap-2 rounded-xl bg-warn-bg px-4 py-3"
    >
      <p class="text-[13px] font-semibold text-warn-text">
        IT has asked you to set a new password.
        <a :href="pwResetLink" class="underline">Tap here to set one.</a>
      </p>
      <button
        type="button"
        class="-mr-1 h-12 w-12 flex-none text-warn-text"
        aria-label="Dismiss"
        @click="dismissPwNotice"
      >
        ×
      </button>
    </div>

    <InstallPrompt />

    <div class="px-4">
      <FilterChips />

      <SearchBar
        v-if="inbox.items.length || query"
        v-model="query"
        placeholder="Search approvals"
      />

      <Skeletons v-if="inbox.loading && !inbox.loadedAt" :count="4" />

      <!-- A failed load must NEVER look like an empty inbox -->
      <div v-else-if="inbox.error" class="flex flex-col items-center gap-3 px-6 py-14 text-center">
        <div class="text-[15px] font-bold">Couldn't load your approvals</div>
        <p class="text-[13px] text-ink-muted">{{ inbox.error.display() }}</p>
        <button
          type="button"
          class="min-h-secondary w-40 rounded-xl bg-brand-deep px-4 text-[14px] font-bold text-white"
          @click="loadInbox"
        >
          Retry
        </button>
      </div>

      <!-- "No match for X" is NOT the same as "your inbox is empty" — the
           generic EmptyState would tell an executive with 54 pending items
           that they have nothing to approve. -->
      <div
        v-else-if="query && !items.length"
        class="flex flex-col items-center gap-2 px-8 py-14 text-center"
      >
        <div class="text-[15px] font-bold">No match for “{{ query }}”</div>
        <p class="text-[14px] text-ink-muted">
          Searching {{ inbox.items.length }}
          {{ inbox.items.length === 1 ? "item" : "items" }} in your inbox.
        </p>
        <button
          type="button"
          class="min-h-secondary mt-1 w-40 rounded-xl bg-brand-deep px-4 text-[14px] font-bold text-white"
          @click="query = ''"
        >
          Clear search
        </button>
      </div>

      <EmptyState v-else-if="!items.length" />

      <div v-else class="flex flex-col gap-2">
        <p v-if="query" class="mb-1 text-[13px] text-ink-faint">
          {{ items.length }} of {{ inbox.items.length }}
        </p>
        <DocCard v-for="it in items" :key="it.doctype + it.name" :item="it" />
      </div>
    </div>
    </PullToRefresh>

    <TabBar />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import AlertsSheet from "@/components/AlertsSheet.vue"
import AppHeader from "@/components/AppHeader.vue"
import DocCard from "@/components/DocCard.vue"
import EmptyState from "@/components/EmptyState.vue"
import FilterChips from "@/components/FilterChips.vue"
import InstallPrompt from "@/components/InstallPrompt.vue"
import PullToRefresh from "@/components/PullToRefresh.vue"
import SearchBar from "@/components/SearchBar.vue"
import Skeletons from "@/components/Skeletons.vue"
import TabBar from "@/components/TabBar.vue"
import { refreshAlertCount } from "@/data/alerts.js"
import { flash } from "@/data/flash.js"
import { filteredItems, inbox, loadInbox } from "@/data/inbox.js"

// Client-side ON PURPOSE: get_inbox already returns the whole queue (200/doctype
// cap vs a real max of ~54), so filtering here is complete AND instant — no
// network, no spinner, no debounce. History is the opposite case and searches
// server-side, because it only ever holds the newest 50 of many hundreds.
const query = ref("")

// Match what the CARD actually shows, or the result looks arbitrary: document
// number, the party/description line, and the status pill.
function haystack(i) {
  return [
    i.name,
    i.title,
    i.supplier_name,
    i.supplier,
    i.material_request_type,
    i.ts_purchase_category,
    i.reference_name,
    i.cost_center,
    i.status,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
}

const items = computed(() => {
  const base = filteredItems()
  const q = query.value.trim().toLowerCase()
  if (!q) return base
  // Every whitespace-separated token must match somewhere ("mec 05063").
  const tokens = q.split(/\s+/)
  return base.filter((i) => {
    const hay = haystack(i)
    return tokens.every((t) => hay.includes(t))
  })
})

const pwResetLink = ref("")
try {
  pwResetLink.value = sessionStorage.getItem("exec-pw-reset-link") || ""
} catch {
  /* private mode */
}
function dismissPwNotice() {
  pwResetLink.value = ""
  try {
    sessionStorage.removeItem("exec-pw-reset-link")
  } catch {
    /* private mode */
  }
}

// Refetch whenever the app returns to the foreground — a stale approvals
// inbox is a lying inbox.
function onVisible() {
  if (document.visibilityState === "visible") {
    loadInbox()
    refreshAlertCount()
  }
}

onMounted(() => {
  loadInbox()
  refreshAlertCount()
  document.addEventListener("visibilitychange", onVisible)
})
onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", onVisible)
})
</script>
