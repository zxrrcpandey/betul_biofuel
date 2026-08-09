<template>
  <div
    v-if="action"
    class="fixed inset-0 z-50 flex items-end justify-center bg-black/45"
    @click.self="tryDismiss"
  >
    <div
      class="w-full max-w-lg overflow-y-auto overscroll-contain rounded-t-2xl bg-surface p-4 pb-2"
      style="max-height: 85dvh"
      role="dialog"
      aria-modal="true"
    >
      <h2 class="text-[16px] font-bold">{{ action.confirmTitle }}</h2>
      <p class="mb-3 mt-0.5 text-[13px] text-ink-muted">{{ action.confirmSub }}</p>

      <div v-if="action.needsReason" class="mb-2">
        <label class="mb-1 block text-[13px] font-semibold text-ink-muted" for="sheet-reason">
          {{ action.reasonLabel }}
        </label>
        <textarea
          id="sheet-reason"
          v-model="reason"
          rows="3"
          class="w-full rounded-lg border border-surface-line bg-surface-page p-2.5 text-[17px]"
          :maxlength="2000"
        ></textarea>
        <div class="mt-0.5 flex justify-between text-[13px]">
          <span v-if="action.reasonMin > 1" :class="reasonOk ? 'text-ok-text' : 'text-warn-text'">
            {{ reason.trim().length }} / {{ action.reasonMin }} minimum
          </span>
          <span v-else></span>
          <span v-if="reason.length > 1900" class="text-warn-text">{{ reason.length }} / 2000</span>
        </div>
      </div>

      <div v-if="action.allowComment" class="mb-2">
        <label class="mb-1 block text-[13px] font-semibold text-ink-muted" for="sheet-comment">
          Comment (optional)
        </label>
        <textarea
          id="sheet-comment"
          v-model="comment"
          rows="2"
          class="w-full rounded-lg border border-surface-line bg-surface-page p-2.5 text-[17px]"
          :maxlength="2000"
        ></textarea>
        <div v-if="comment.length > 1900" class="mt-0.5 text-right text-[13px] text-warn-text">
          {{ comment.length }} / 2000
        </div>
      </div>

      <!-- Failures render HERE, in the sheet — it stays open, confirm
           re-enables only after the store refetched. Toasts are success-only. -->
      <p v-if="error" class="mb-2 text-[14px] font-semibold text-danger-text">{{ error }}</p>

      <button
        type="button"
        class="min-h-action w-full rounded-xl text-[16px] font-bold"
        :class="
          action.destructive
            ? 'border-2 border-danger-text bg-surface text-danger-text disabled:opacity-40'
            : 'bg-brand-deep text-white disabled:opacity-40'
        "
        :disabled="!valid || working"
        @click="confirm"
      >
        {{ working ? "Working…" : action.confirmCta }}
      </button>
      <!-- Cancel BELOW the commit, 16px apart — the resting thumb lands here -->
      <button
        type="button"
        class="min-h-secondary mt-4 w-full rounded-xl border border-surface-line text-[15px] font-semibold text-ink-muted disabled:opacity-40"
        :disabled="working"
        @click="dismiss"
      >
        Cancel
      </button>
      <div class="safe-bottom"></div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

const props = defineProps({
  // {key, confirmTitle, confirmSub, confirmCta, needsReason, reasonLabel,
  //  reasonMin, allowComment, destructive}
  action: { type: Object, default: null },
  working: { type: Boolean, default: false },
  error: { type: String, default: "" },
})

const emit = defineEmits(["confirm", "dismiss", "edited"])

const reason = ref("")
const comment = ref("")

watch(
  () => props.action,
  () => {
    reason.value = ""
    comment.value = ""
  }
)

// Typing clears a stale failure line — the parent owns the error state.
watch([reason, comment], () => {
  if (props.error) emit("edited")
})

const reasonOk = computed(() => {
  if (!props.action || !props.action.needsReason) return true
  return reason.value.trim().length >= (props.action.reasonMin || 1)
})
const valid = computed(() => reasonOk.value)

function confirm() {
  if (!valid.value || props.working) return
  emit("confirm", { reason: reason.value.trim(), comment: comment.value.trim() })
}

function dismiss() {
  if (props.working) return
  emit("dismiss")
}

// Backdrop-tap dismissal is disabled while the reason textarea is non-empty —
// losing a typed rejection reason to a stray tap is unforgivable.
function tryDismiss() {
  if (props.working) return
  if (reason.value.trim() || comment.value.trim()) return
  emit("dismiss")
}
</script>
