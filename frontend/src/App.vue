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
    <!-- Not on the access list. A separate state from maintenance on purpose:
         "switched off for everyone" and "not for you" are different facts and
         must not share a message. Presentation only — every endpoint re-checks
         server-side, so editing execEnv in a console grants nothing. -->
    <div
      v-else-if="notAllowed"
      class="flex min-h-dvh flex-col items-center justify-center gap-3 px-8 text-center"
    >
      <img
        :src="trustbitLogo"
        alt="Trustbit"
        class="mb-1 h-7 w-auto opacity-70"
      />
      <div class="text-lg font-semibold">This app is for approvers</div>
      <p class="text-ink-muted">
        BBPL Approvals is limited to selected people. Your account is not on
        the list.
      </p>
      <p class="text-[14px] text-ink-faint">
        Everything you normally do is still in the main ERP.
      </p>
      <a
        href="/app"
        class="min-h-secondary mt-2 flex w-48 items-center justify-center rounded-xl bg-brand-deep px-4 text-[14px] font-bold text-white"
      >
        Open the ERP
      </a>
      <button
        type="button"
        class="min-h-secondary text-[14px] font-semibold text-ink-faint"
        @click="signOut"
      >
        Sign out
      </button>
    </div>

    <template v-else>
      <OfflineBanner />
      <router-view />
    </template>
  </div>
</template>

<script setup>
import OfflineBanner from "./components/OfflineBanner.vue"
import { logout } from "./data/session.js"

// Injected server-side by www/exec.py; a Guest shell always renders (login
// must work) — the maintenance screen only replaces the app when IT set the
// kill switch to an explicit 0.
const env = window.execEnv || {}
const disabled = Number(env.enabled ?? 1) === 0

// Access list (v2.34.0). Defaults to ALLOWED when the key is absent so an old
// cached shell served by a phone's service worker cannot lock a genuine
// executive out — the server denies the API regardless, which is the boundary
// that actually matters.
const notAllowed = Number(env.allowed ?? 1) === 0

const trustbitLogo = "/assets/trustbit_ethanol/exec/icons/brand-trustbit-v2.png"

// A denied user is very likely signed in on a shared phone — give them a way
// out rather than stranding them on a dead end.
function signOut() {
  logout()
}
</script>
