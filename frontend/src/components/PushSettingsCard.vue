<template>
  <div class="rounded-xl border border-surface-line bg-surface p-4">
    <div class="text-[13px] font-bold uppercase tracking-wide text-ink-faint">
      Lock-screen alerts
    </div>

    <div v-if="push.loading" class="mt-2 text-[14px] text-ink-muted">Checking…</div>

    <template v-else>
      <!-- S5 — working -->
      <template v-if="push.state === 's5_on'">
        <p class="mt-1 text-[15px] font-semibold text-ok-text">
          ● Alerts are on for this phone.
        </p>
        <p class="mt-1 text-[14px] text-ink-muted">
          You'll get an alert when a document needs your approval.
          <span v-if="push.dryRun"> (IT has alerts in test mode right now.)</span>
        </p>
        <button
          type="button"
          class="min-h-secondary mt-3 w-full rounded-xl border border-surface-line text-[14px] font-semibold text-ink-muted disabled:opacity-40"
          :disabled="push.busy"
          @click="sendTestPush"
        >
          {{ push.busy ? "Sending…" : "Send a test alert" }}
        </button>
        <p v-if="push.testSent" class="mt-2 text-[14px] text-ok-text">
          Sent — it should appear on this phone in a moment.
        </p>
        <button
          type="button"
          class="min-h-secondary mt-2 w-full rounded-xl text-[14px] font-semibold text-danger-text disabled:opacity-40"
          :disabled="push.busy"
          @click="disablePush"
        >
          Turn off alerts on this phone
        </button>
      </template>

      <!-- S4 — the call to action -->
      <template v-else-if="push.state === 's4_ready'">
        <p class="mt-1 text-[14px] text-ink-muted">
          Get an alert on this phone when a document needs your approval.
        </p>
        <button
          type="button"
          class="min-h-action mt-3 w-full rounded-xl bg-brand-deep text-[16px] font-bold text-white disabled:opacity-40"
          :disabled="push.busy"
          @click="enablePush"
        >
          {{ push.busy ? "Turning on…" : "Turn on alerts" }}
        </button>
      </template>

      <!-- S6 — subscription died (iOS does this on its own) -->
      <template v-else-if="push.state === 's6_broken'">
        <p class="mt-1 text-[15px] font-semibold text-warn-text">
          Alerts have stopped working on this phone.
        </p>
        <p class="mt-1 text-[14px] text-ink-muted">
          This happens on its own sometimes. Turn them on again — you will not
          be asked for permission a second time.
        </p>
        <button
          type="button"
          class="min-h-action mt-3 w-full rounded-xl bg-brand-deep text-[16px] font-bold text-white disabled:opacity-40"
          :disabled="push.busy"
          @click="enablePush"
        >
          {{ push.busy ? "Turning on…" : "Turn alerts back on" }}
        </button>
      </template>

      <!-- S3 — iPhone, not installed to the Home Screen -->
      <template v-else-if="push.state === 's3_ios_install'">
        <p class="mt-1 text-[15px] font-semibold">Alerts need the app on your Home Screen.</p>
        <ol class="mt-2 list-decimal pl-5 text-[14px] text-ink-muted">
          <li>Tap the Share button in Safari</li>
          <li>Choose <span class="font-semibold">Add to Home Screen</span></li>
          <li>Open BBPL Approvals from the new icon</li>
          <li>Come back to Settings and turn alerts on</li>
        </ol>
        <p class="mt-2 text-[14px] text-ink-muted">
          If it is already on your Home Screen, press and hold the icon, choose
          Remove, then add it again — the app icon changed in the last update.
        </p>
      </template>

      <!-- S7 — blocked. No button: a dead button is worse than none. -->
      <template v-else-if="push.state === 's7_denied'">
        <p class="mt-1 text-[15px] font-semibold text-danger-text">
          Alerts are blocked for this app.
        </p>
        <p v-if="iosDevice" class="mt-1 text-[14px] text-ink-muted">
          Open the iPhone Settings app → Notifications → BBPL Approvals, and
          allow notifications. Then come back here.
        </p>
        <p v-else class="mt-1 text-[14px] text-ink-muted">
          Tap the lock icon beside the address bar → Site settings →
          Notifications → Allow. Then come back here.
        </p>
      </template>

      <!-- S0/S1/S2 — nothing the executive can do -->
      <template v-else-if="push.state === 's0_master_off'">
        <p class="mt-1 text-[14px] text-ink-muted">Alerts are switched off by IT.</p>
      </template>
      <template v-else-if="push.state === 's1_no_keys'">
        <p class="mt-1 text-[14px] text-ink-muted">
          Alerts are not set up on the server yet.
        </p>
      </template>
      <template v-else-if="push.state === 's2_ios_old'">
        <p class="mt-1 text-[14px] text-ink-muted">Alerts need iOS 16.4 or later.</p>
      </template>
      <template v-else>
        <p class="mt-1 text-[14px] text-ink-muted">This browser cannot show alerts.</p>
      </template>

      <p v-if="push.error" class="mt-2 text-[14px] font-semibold text-danger-text">
        {{ push.error }}
      </p>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue"

import {
  disablePush,
  enablePush,
  isIOS,
  push,
  refreshPushState,
  sendTestPush,
} from "@/data/push.js"

const iosDevice = computed(() => isIOS())

onMounted(refreshPushState)
</script>
