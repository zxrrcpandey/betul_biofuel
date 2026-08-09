<template>
  <div
    v-if="alerts.open"
    class="fixed inset-0 z-50 flex items-end justify-center bg-black/45"
    @click.self="closeAlerts"
  >
    <div
      class="flex w-full max-w-lg flex-col overflow-hidden rounded-t-2xl bg-surface"
      style="max-height: 85dvh"
      role="dialog"
      aria-modal="true"
      aria-labelledby="alerts-title"
    >
      <div class="flex items-center justify-between border-b border-surface-line px-4 py-3">
        <h2 id="alerts-title" class="text-[16px] font-bold">Alerts</h2>
        <div class="flex items-center gap-1">
          <button
            v-if="alerts.unread > 0"
            type="button"
            class="min-h-secondary rounded-lg px-3 text-[14px] font-semibold text-brand-deep disabled:opacity-40"
            :disabled="alerts.marking"
            @click="markAllAlertsRead"
          >
            {{ alerts.marking ? "Marking…" : "Mark all read" }}
          </button>
          <button
            type="button"
            class="flex h-12 w-12 items-center justify-center rounded-full text-2xl text-ink-faint"
            aria-label="Close alerts"
            @click="closeAlerts"
          >
            ×
          </button>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto overscroll-contain px-4 py-3">
        <Skeletons v-if="alerts.loading && !alerts.loadedAt" :count="4" />

        <!-- A failed load must never look like an empty alerts list -->
        <div
          v-else-if="alerts.error"
          class="flex flex-col items-center gap-3 px-6 py-12 text-center"
        >
          <div class="text-[15px] font-bold">Couldn't load your alerts</div>
          <p class="text-[13px] text-ink-muted">{{ alerts.error.display() }}</p>
          <button
            type="button"
            class="min-h-secondary w-40 rounded-xl bg-brand-deep px-4 text-[14px] font-bold text-white"
            @click="loadAlerts"
          >
            Retry
          </button>
        </div>

        <div
          v-else-if="!alerts.rows.length"
          class="flex flex-col items-center gap-2 px-8 py-12 text-center"
        >
          <div class="text-[15px] font-bold">No alerts</div>
          <p class="text-[14px] text-ink-muted">
            Anything that needs your approval is on the Inbox tab.
          </p>
        </div>

        <div v-else class="flex flex-col gap-2">
          <component
            :is="isRoutable(row) ? 'button' : 'div'"
            v-for="row in alerts.rows"
            :key="row.name"
            :type="isRoutable(row) ? 'button' : undefined"
            class="w-full rounded-xl border p-3 text-left"
            :class="
              row.read
                ? 'border-surface-line bg-surface'
                : 'border-brand-soft bg-brand-soft'
            "
            @click="isRoutable(row) && openRow(row)"
          >
            <div class="flex items-start justify-between gap-2">
              <span
                class="min-w-0 text-[14px]"
                :class="row.read ? 'font-medium text-ink-muted' : 'font-bold text-ink'"
              >
                {{ row.subject }}
              </span>
              <span class="whitespace-nowrap text-[13px] text-ink-faint">{{ row.age_display }}</span>
            </div>
            <div v-if="row.doc_name" class="mt-1 flex flex-wrap items-center gap-2">
              <span class="text-[13px] text-ink-faint">{{ row.doc_name }}</span>
              <!-- Positive match ONLY: a missing tag is harmless, a wrong
                   "not in your inbox" would be a lie. -->
              <span
                v-if="inInbox(row)"
                class="rounded-full bg-ok-bg px-2 py-0.5 text-[13px] font-semibold text-ok-text"
              >
                In your Inbox
              </span>
            </div>
          </component>
        </div>
      </div>
      <div class="safe-bottom"></div>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from "vue-router"

import Skeletons from "@/components/Skeletons.vue"
import {
  alerts,
  closeAlerts,
  isRoutable,
  loadAlerts,
  markAlertRead,
  markAllAlertsRead,
} from "@/data/alerts.js"
import { inbox } from "@/data/inbox.js"

const router = useRouter()

function inInbox(row) {
  return inbox.items.some(
    (i) => i.doctype === row.doc_type && i.name === row.doc_name
  )
}

async function openRow(row) {
  closeAlerts()
  router.push({
    name: "document",
    params: { doctype: row.doc_type, name: row.doc_name },
  })
  markAlertRead(row)
}
</script>
