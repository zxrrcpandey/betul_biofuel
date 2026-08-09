<template>
  <div
    v-if="visible"
    class="mx-4 mb-2 rounded-xl border border-surface-line bg-brand-soft p-3"
  >
    <div class="flex items-start justify-between gap-2">
      <div class="min-w-0">
        <div class="text-[14px] font-bold">Add this app to your phone</div>
        <p v-if="mode === 'ios'" class="mt-1 text-[13px] text-ink-muted">
          Tap <span class="font-semibold">Share</span> in Safari, then
          <span class="font-semibold">Add to Home Screen</span>. The first time
          you open it from the icon it will ask you to sign in again — this is
          normal, it only happens once.
        </p>
        <p v-else class="mt-1 text-[13px] text-ink-muted">
          Install it for one-tap access from your home screen. The first time
          you open it from the icon it will ask you to sign in again — this is
          normal, it only happens once.
        </p>
      </div>
      <button
        type="button"
        class="flex h-12 w-12 flex-none items-center justify-center rounded-full text-xl text-ink-faint"
        aria-label="Dismiss"
        @click="dismiss"
      >
        ×
      </button>
    </div>
    <button
      v-if="mode === 'android'"
      type="button"
      class="min-h-secondary mt-2 w-full rounded-lg bg-brand-deep text-[14px] font-bold text-white"
      @click="install"
    >
      Install app
    </button>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue"

const DISMISS_KEY = "exec-install-dismissed"

const visible = ref(false)
const mode = ref("")
let deferredPrompt = null

const isStandalone = () =>
  window.matchMedia("(display-mode: standalone)").matches ||
  window.navigator.standalone === true

function onBeforeInstall(e) {
  // Chrome's mini-infobar is easy to miss and never reappears — capture the
  // event and render our own explicit Install button instead.
  e.preventDefault()
  deferredPrompt = e
  if (!localStorage.getItem(DISMISS_KEY) && !isStandalone()) {
    mode.value = "android"
    visible.value = true
  }
}

onMounted(() => {
  if (isStandalone() || localStorage.getItem(DISMISS_KEY)) return
  window.addEventListener("beforeinstallprompt", onBeforeInstall)
  // iOS Safari has NO beforeinstallprompt — show manual instructions.
  const ua = navigator.userAgent
  const isIphone = /iPhone|iPad|iPod/.test(ua)
  const isSafari = /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua)
  if (isIphone && isSafari) {
    mode.value = "ios"
    visible.value = true
  }
})

onBeforeUnmount(() => {
  window.removeEventListener("beforeinstallprompt", onBeforeInstall)
})

async function install() {
  if (!deferredPrompt) return
  deferredPrompt.prompt()
  try {
    await deferredPrompt.userChoice
  } finally {
    deferredPrompt = null
    visible.value = false
  }
}

function dismiss() {
  localStorage.setItem(DISMISS_KEY, "1")
  visible.value = false
}
</script>
