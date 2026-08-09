<template>
  <div class="pb-28">
    <AppHeader
      title="History"
      bell
      refreshable
      :refreshing="history.loading"
      @refresh="reload"
    />
    <AlertsSheet />
    <PullToRefresh :refreshing="history.loading" @refresh="reload">
    <div class="px-4">
      <SearchBar
        v-model="query"
        :busy="history.loading"
        placeholder="Search all your actions"
      />

      <!-- The default view is the newest 50, but an executive may have many
           hundreds. Saying so is the difference between a window and a lie —
           and it tells them search is the way to reach the rest. -->
      <p class="mb-2 text-[13px] text-ink-faint">
        <template v-if="history.search">
          {{ history.items.length }}
          {{ history.items.length === 1 ? "match" : "matches" }} for
          “{{ history.search }}” across all
          {{ history.totalActions }} of your actions.
        </template>
        <template v-else-if="history.totalActions > history.items.length">
          Your {{ history.items.length }} most recent actions of
          {{ history.totalActions }}. Search to reach the rest.
        </template>
        <template v-else>
          Your own actions on Purchase Orders and Material Requests, newest
          first.
        </template>
      </p>

      <p v-if="history.capped" class="mb-2 text-[13px] text-warn-text">
        Showing the first {{ history.limit }} matches — narrow the search to
        see more.
      </p>

      <Skeletons v-if="history.loading && !history.loadedAt" :count="4" />

      <!-- A failed load must NEVER look like an empty history -->
      <div
        v-else-if="history.error"
        class="flex flex-col items-center gap-3 px-6 py-14 text-center"
      >
        <div class="text-[15px] font-bold">Couldn't load your history</div>
        <p class="text-[13px] text-ink-muted">{{ history.error.display() }}</p>
        <button
          type="button"
          class="min-h-secondary w-40 rounded-xl bg-brand-deep px-4 text-[14px] font-bold text-white"
          @click="reload"
        >
          Retry
        </button>
      </div>

      <!-- A search with no hits is NOT an empty history — the generic message
           would tell an executive with 799 recorded actions that they have
           never approved anything. -->
      <div
        v-else-if="query && !history.items.length"
        class="flex flex-col items-center gap-2 px-8 py-14 text-center"
      >
        <div class="text-[15px] font-bold">No match for “{{ query }}”</div>
        <p class="text-[14px] text-ink-muted">
          Searched all {{ history.totalActions }} of your recorded actions.
        </p>
        <button
          type="button"
          class="min-h-secondary mt-1 w-40 rounded-xl bg-brand-deep px-4 text-[14px] font-bold text-white"
          @click="query = ''"
        >
          Clear search
        </button>
      </div>

      <div
        v-else-if="!history.items.length"
        class="flex flex-col items-center gap-2 px-8 py-16 text-center"
      >
        <div class="text-lg font-bold">No actions yet</div>
        <p class="text-[14px] text-ink-muted">
          Documents you approve, reject, hold or revise will appear here.
        </p>
      </div>

      <div v-else class="flex flex-col gap-2">
        <router-link
          v-for="(it, i) in history.items"
          :key="i"
          :to="{ name: 'document', params: { doctype: it.doctype, name: it.name } }"
          class="block rounded-xl border border-surface-line bg-surface p-3 shadow-sm active:bg-surface-page"
        >
          <div class="flex items-baseline justify-between gap-2">
            <span class="min-w-0 truncate text-[15px] font-bold">{{ it.name }}</span>
            <span v-if="it.amount" class="tnum whitespace-nowrap text-[14px] font-bold">
              {{ moneyCompact(it.amount) }}
            </span>
          </div>
          <div class="mt-0.5 flex items-center justify-between gap-2">
            <span class="text-[13px] font-semibold" :class="actionClass(it.action)">
              {{ it.action }}
            </span>
            <span class="text-[13px] text-ink-faint">{{ when(it.action_date) }}</span>
          </div>
          <div v-if="it.comment" class="mt-1 truncate text-[13px] text-ink-muted">
            “{{ it.comment }}”
          </div>
        </router-link>
      </div>
    </div>
    </PullToRefresh>

    <TabBar />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from "vue"

import AlertsSheet from "@/components/AlertsSheet.vue"
import AppHeader from "@/components/AppHeader.vue"
import PullToRefresh from "@/components/PullToRefresh.vue"
import SearchBar from "@/components/SearchBar.vue"
import Skeletons from "@/components/Skeletons.vue"
import TabBar from "@/components/TabBar.vue"
import { moneyCompact, shortDate } from "@/data/format.js"
import { history, loadHistory } from "@/data/history.js"

// SERVER-side search: History holds only the newest 50 rows, while a real
// executive has hundreds (799 for the prod CEO), so filtering the loaded page
// would answer "no results" for most of their own history.
const query = ref("")
let timer = null

watch(query, (q) => {
  if (timer) clearTimeout(timer)
  // 300ms: long enough not to fire per keystroke, short enough to feel live.
  timer = setTimeout(() => loadHistory(q.trim()), 300)
})

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})

// Header/pull-to-refresh must keep whatever the user is looking at, not
// silently snap back to the unfiltered list.
function reload() {
  loadHistory(query.value.trim())
}

function when(dt) {
  return shortDate(dt)
}

function actionClass(action) {
  const a = String(action || "")
  if (/reject/i.test(a)) return "text-danger-text"
  if (/hold/i.test(a)) return "text-warn-text"
  if (/revis/i.test(a)) return "text-info-text"
  if (/approv|forward|review/i.test(a)) return "text-ok-text"
  return "text-ink-muted"
}

onMounted(reload)
</script>
