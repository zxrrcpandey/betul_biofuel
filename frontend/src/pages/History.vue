<template>
  <div class="pb-28">
    <AppHeader
      title="History"
      bell
      refreshable
      :refreshing="history.loading"
      @refresh="loadHistory"
    />
    <AlertsSheet />
    <PullToRefresh :refreshing="history.loading" @refresh="loadHistory">
    <div class="px-4">
      <p class="mb-2 text-[13px] text-ink-faint">
        Your own actions on Purchase Orders and Material Requests, newest
        first.
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
          @click="loadHistory"
        >
          Retry
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
import { onMounted } from "vue"

import AlertsSheet from "@/components/AlertsSheet.vue"
import AppHeader from "@/components/AppHeader.vue"
import PullToRefresh from "@/components/PullToRefresh.vue"
import Skeletons from "@/components/Skeletons.vue"
import TabBar from "@/components/TabBar.vue"
import { moneyCompact, shortDate } from "@/data/format.js"
import { history, loadHistory } from "@/data/history.js"

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

onMounted(loadHistory)
</script>
