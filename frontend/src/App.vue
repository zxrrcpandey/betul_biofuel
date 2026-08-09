<template>
  <div class="mx-auto min-h-dvh w-full max-w-lg">
    <div
      v-if="disabled"
      class="flex min-h-dvh flex-col items-center justify-center gap-3 px-8 text-center"
    >
      <div class="text-lg font-semibold">BBPL Approvals is under maintenance</div>
      <p class="text-ink-muted">
        The app has been switched off by IT. Approvals are still available in
        the ERP. Please try again later.
      </p>
    </div>
    <template v-else>
      <OfflineBanner />
      <router-view />
    </template>
  </div>
</template>

<script setup>
import OfflineBanner from "./components/OfflineBanner.vue"

// Injected server-side by www/exec.py; a Guest shell always renders (login
// must work) — the maintenance screen only replaces the app when IT set the
// kill switch to an explicit 0.
const disabled = Number((window.execEnv || {}).enabled ?? 1) === 0
</script>
