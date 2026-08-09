<template>
  <div class="flex min-h-dvh flex-col px-6">
    <div class="pb-6 pt-16 text-center">
      <div
        class="mx-auto mb-3 flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-deep text-xl font-extrabold text-white"
      >
        BBPL
      </div>
      <div class="text-[17px] font-bold">Betul Bio Fuel Pvt Ltd</div>
      <div class="text-[13px] text-ink-muted">Executive Approvals</div>
    </div>

    <!-- Branch C: core reset — BLOCKING, no inbox until the password is set -->
    <div v-if="resetLink" class="rounded-xl border border-surface-line bg-surface p-4">
      <div class="text-[15px] font-bold">Your password must be reset first</div>
      <p class="mb-3 mt-1 text-[14px] text-ink-muted">
        Your password has expired. Set a new one, then sign in again.
      </p>
      <a
        :href="resetLink"
        class="block min-h-action w-full rounded-xl bg-brand-deep py-4 text-center text-[16px] font-bold text-white"
      >
        Set a new password
      </a>
    </div>

    <!-- Branch D: OTP -->
    <form v-else-if="tmpId" class="flex flex-col gap-3" @submit.prevent="submitOtp">
      <p class="text-[14px] text-ink-muted">
        Enter the 6-digit code from your authenticator app.
      </p>
      <input
        v-model="otp"
        inputmode="numeric"
        autocomplete="one-time-code"
        class="w-full rounded-lg border border-surface-line bg-surface p-3 text-center text-[22px] tracking-[0.4em]"
        maxlength="6"
        aria-label="One-time code"
      />
      <p v-if="error" class="text-[14px] font-semibold text-danger-text">{{ error }}</p>
      <button
        type="submit"
        class="min-h-action w-full rounded-xl bg-brand-deep text-[16px] font-bold text-white disabled:opacity-40"
        :disabled="working || otp.trim().length < 4"
      >
        {{ working ? "Checking…" : "Verify code" }}
      </button>
    </form>

    <!-- Branches A/B/E/F/G -->
    <form v-else class="flex flex-col gap-3" @submit.prevent="submit">
      <input
        v-model="usr"
        type="email"
        autocomplete="username"
        placeholder="name@erpbbpl.com"
        class="w-full rounded-lg border border-surface-line bg-surface p-3 text-[17px]"
        aria-label="Email"
      />
      <input
        v-model="pwd"
        type="password"
        autocomplete="current-password"
        placeholder="Password"
        class="w-full rounded-lg border border-surface-line bg-surface p-3 text-[17px]"
        aria-label="Password"
      />
      <p v-if="error" class="text-[14px] font-semibold text-danger-text">{{ error }}</p>
      <button
        type="submit"
        class="min-h-action w-full rounded-xl bg-brand-deep text-[16px] font-bold text-white disabled:opacity-40"
        :disabled="working || !usr.trim() || !pwd"
      >
        {{ working ? "Signing you in…" : "Sign in" }}
      </button>
      <p class="text-center text-[13px] text-ink-faint">
        Uses your existing ERP login. Nothing new to remember.
      </p>
    </form>

    <div class="mt-auto flex flex-col items-center gap-1 pb-8 pt-10">
      <span class="text-[13px] text-ink-faint">Powered by</span>
      <!-- :src (dynamic) on purpose: this is a runtime URL served by the
           bench, not a bundled asset — a static src makes Vite try to
           resolve it as a build-time import and fail -->
      <img :src="trustbitLogo" alt="Trustbit" class="h-8 w-auto" />
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue"

import { login, loginOtp } from "@/data/session.js"

// -v2 filename on purpose: /assets URLs are browser-cached ~1 year, so a
// changed logo must ship under a NEW name or phones keep the old bytes.
const trustbitLogo = "/assets/trustbit_ethanol/exec/icons/brand-trustbit-v2.png"

const usr = ref("")
const pwd = ref("")
const otp = ref("")
const tmpId = ref("")
const resetLink = ref("")
const error = ref("")
const working = ref(false)

// Login success is a FULL PAGE RELOAD by design: a Guest-loaded shell holds
// the Guest CSRF token; the reload re-injects the session token. No slide or
// fade here — a transition would be wiped mid-animation anyway.
function finish(redirectTo) {
  if (redirectTo) {
    // Branch B (force-password-change, non-blocking): surface after reload.
    try {
      sessionStorage.setItem("exec-pw-reset-link", redirectTo)
    } catch {
      /* private mode — the notice is best-effort */
    }
  }
  window.location.replace("/exec/")
}

async function submit() {
  error.value = ""
  working.value = true
  try {
    const r = await login(usr.value.trim(), pwd.value)
    if (r.kind === "2fa") {
      tmpId.value = r.tmpId
      working.value = false
    } else if (r.kind === "reset_required") {
      resetLink.value = r.redirectTo
      working.value = false
    } else {
      finish(r.redirectTo) // stays on "Signing you in…" through the reload
    }
  } catch (e) {
    error.value =
      e.status === 401 ? e.display() || "Wrong email or password." : e.display()
    working.value = false
  }
}

async function submitOtp() {
  error.value = ""
  working.value = true
  try {
    const r = await loginOtp(tmpId.value, otp.value.trim())
    finish(r.redirectTo) // stays on "Checking…" through the reload
  } catch (e) {
    error.value = e.display() || "That code didn't work. Try the next one."
    working.value = false
  }
}
</script>
