<template>
  <div class="flex min-h-dvh flex-col pb-28">
    <AppHeader title="Settings" />

    <div class="flex flex-col gap-3 px-4">
      <div class="rounded-xl border border-surface-line bg-surface p-4">
        <div class="text-[13px] font-bold uppercase tracking-wide text-ink-faint">
          Signed in as
        </div>
        <div class="mt-1 truncate text-[15px] font-semibold">{{ session.user }}</div>
      </div>

      <div class="rounded-xl border border-surface-line bg-surface p-4">
        <div class="text-[13px] font-bold uppercase tracking-wide text-ink-faint">
          Notifications
        </div>
        <p class="mt-1 text-[14px] text-ink-muted">
          Lock-screen alerts arrive in the next update (Phase 2). Until then,
          everything pending for you is always on the Inbox tab.
        </p>
      </div>

      <div class="rounded-xl border border-surface-line bg-surface p-4">
        <div class="text-[13px] font-bold uppercase tracking-wide text-ink-faint">
          Add to your phone
        </div>
        <p class="mt-1 text-[14px] text-ink-muted">
          iPhone: open this page in Safari → Share → Add to Home Screen.
          Android: Chrome menu → Install app.
        </p>
      </div>

      <button
        type="button"
        class="min-h-action w-full rounded-xl border-2 border-danger-text text-[16px] font-bold text-danger-text"
        @click="doLogout"
      >
        Sign out
      </button>
    </div>

    <div class="mt-auto flex flex-col items-center gap-1 pt-10">
      <span class="text-[13px] text-ink-faint">BBPL Approvals · v2.31.0 · Powered by</span>
      <img :src="trustbitLogo" alt="Trustbit" class="h-7 w-auto" />
    </div>

    <TabBar />
  </div>
</template>

<script setup>
import AppHeader from "@/components/AppHeader.vue"
import TabBar from "@/components/TabBar.vue"
import { logout, session } from "@/data/session.js"

const trustbitLogo = "/assets/trustbit_ethanol/exec/icons/brand-trustbit-v2.png"

async function doLogout() {
  await logout()
}
</script>
