<template>
  <div v-if="steps.length" class="flex flex-col gap-1">
    <div class="mb-1 text-[13px] font-bold">Approval trail</div>
    <div v-for="(s, i) in steps" :key="i" class="flex items-start gap-2 py-0.5">
      <span
        class="mt-0.5 flex h-6 w-6 flex-none items-center justify-center rounded-full text-[13px] font-bold"
        :class="badgeClass(s.status)"
      >
        {{ badgeGlyph(s.status) }}
      </span>
      <div class="min-w-0">
        <span class="text-[14px] font-semibold">{{ s.who }}</span>
        <span v-if="s.label" class="text-[14px]" :class="s.status === 'current' ? 'font-semibold text-warn-text' : 'text-ink-muted'">
          · {{ s.label }}</span
        >
        <!-- date is a PRE-FORMATTED string from the server — printed verbatim -->
        <div v-if="s.by || s.when || s.comment" class="text-[13px] text-ink-faint">
          <span v-if="s.by">{{ s.by }}</span>
          <span v-if="s.by && s.when"> · </span>
          <span v-if="s.when">{{ s.when }}</span>
          <span v-if="s.comment"> · “{{ s.comment }}”</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({
  // context.approval_chain rows: {step, role, action_type, is_manual,
  //  status: "pending"|"done"|"current"|"skipped", by, date}
  chain: { type: Array, default: () => [] },
  log: { type: Array, default: () => [] }, // ts_approval_log child rows
})

const LABELS = {
  done: "Approved",
  current: "Waiting here",
  skipped: "Skipped",
  pending: "",
}

function badgeClass(status) {
  // "done" stays semantic GREEN, not brand — approvals read as approval,
  // independent of whatever the brand hue is.
  if (status === "done") return "bg-ok-text text-white"
  if (status === "current") return "bg-warn-bg text-warn-text"
  // pending / skipped: hollow grey — visually NOT completed
  return "border border-surface-line bg-surface text-ink-faint"
}

function badgeGlyph(status) {
  if (status === "done") return "✓"
  if (status === "current") return "●"
  return ""
}

// The chain gives the display view (server pre-formats dates and resolves
// who acted); the log fills in for doctypes without a chain.
const steps = computed(() => {
  const out = (props.chain || []).map((c) => ({
    who: c.role || c.by || "",
    by: c.by || "",
    label: LABELS[c.status] ?? String(c.status || ""),
    status: c.status || "pending",
    when: c.date || "",
    comment: c.comment || "",
  }))
  if (out.length) return out
  return (props.log || []).map((l) => ({
    who: l.action_by_name || l.action_by || l.action_by_role || "",
    by: "",
    label: l.action || "",
    status: "done",
    when: l.action_date || "",
    comment: l.comment || "",
  }))
})
</script>
