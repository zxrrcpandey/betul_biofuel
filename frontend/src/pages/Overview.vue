<template>
  <div class="pb-28">
    <AppHeader
      title="Overview"
      bell
      refreshable
      :refreshing="overview.loading"
      @refresh="reload"
    />
    <AlertsSheet />

    <PullToRefresh :refreshing="overview.loading" @refresh="reload">
      <div class="flex flex-col gap-3 px-4">
        <p class="text-[13px]" :class="stale && !overview.error ? 'text-warn-text' : 'text-ink-faint'">
          {{ asOfLine }}
        </p>

        <!-- A refresh that FAILED while we still hold older data must say so.
             The store deliberately keeps the last-good payload rather than
             blanking every card, but stale numbers presented as current are
             the same lie this whole screen exists to avoid. -->
        <div
          v-if="overview.error && overview.data"
          class="rounded-xl bg-warn-bg px-3 py-2 text-[13px] text-warn-text"
          role="status"
        >
          Couldn't refresh just now — showing the figures from
          {{ d.as_of_display }}. {{ overview.error.display() }}
        </div>

        <!-- Confidentiality is stated, never silently applied. The existing desk
             dashboards' failure is showing a filtered number as if it were the
             whole company. -->
        <div
          v-if="d && d.confidential_scope === 'restricted'"
          class="rounded-xl bg-blocked-bg px-3 py-2 text-[13px] text-blocked-text"
        >
          Partial view — confidential purchase orders are excluded from these
          figures for your account.
        </div>

        <!-- ── Card 1 · approvals pipeline ─────────────────────────────── -->
        <StatCard
          title="Approvals waiting"
          scope="Company-wide, every approver — not just yours"
          :state="cardState"
          :error-detail="errDetail"
          @retry="reload"
        >
          <template v-if="a">
            <div
              v-if="a.partial"
              class="mb-3 rounded-lg bg-warn-bg px-3 py-2 text-[13px] text-warn-text"
            >
              One queue couldn't be read, so this total is lower than reality.
            </div>

            <!-- `&& !a.partial` is load-bearing: with both queues unreadable the
                 total is 0, and the old markup printed the definitive "Nothing
                 waiting" directly beneath the "one queue couldn't be read"
                 banner — two contradictory claims on one card. -->
            <div v-if="!a.total_pending && !a.partial" class="py-2">
              <div class="text-[17px] font-bold">Nothing waiting</div>
              <p class="mt-1 text-[14px] text-ink-muted">
                No purchase order or material request is pending a decision.
              </p>
            </div>

            <template v-else-if="a.total_pending">
              <div class="flex items-end gap-2">
                <span class="tnum text-[38px] font-bold leading-none">
                  {{ a.total_pending }}
                </span>
                <span class="pb-1 text-[14px] text-ink-muted">
                  documents pending a decision
                </span>
              </div>

              <!-- "untouched", not "waiting": ages come from `modified`, so any
                   edit for any reason resets the clock. This figure is a LOWER
                   bound and the wording must not imply more precision than the
                   data has (live-data audit — 8 POs had their age reset by a
                   bulk submit). -->
              <p v-if="a.over_30_days" class="mt-2 text-[14px] text-warn-text">
                <span class="font-bold">{{ a.over_30_days }}</span> have been
                untouched for more than 30 days.
              </p>

              <router-link
                v-if="a.oldest && oldestTo"
                :to="oldestTo"
                class="mt-2 flex min-h-secondary items-center justify-between gap-3 rounded-xl bg-surface-page px-3 py-2 active:bg-brand-soft"
              >
                <span class="min-w-0">
                  <span class="block text-[13px] text-ink-faint">Longest waiting</span>
                  <span class="block truncate text-[15px] font-semibold">
                    {{ a.oldest.name }}
                  </span>
                </span>
                <span class="tnum shrink-0 text-[15px] font-bold text-warn-text">
                  {{ Math.round(a.oldest.days) }} d
                </span>
              </router-link>

              <!-- Aging bar: proportion, with the numbers spelled out below it
                   so the bar is never the only carrier of the value. -->
              <div v-if="agingTotal" class="mt-4">
                <!-- aria-hidden: the segments are empty divs and the legend
                     below is the accessible carrier of every value. -->
                <div
                  class="flex h-2.5 overflow-hidden rounded-full bg-surface-page"
                  aria-hidden="true"
                >
                  <!-- minWidth floors a sliver so a count of 1 (0.25% ≈ 0.7px,
                       i.e. invisible) cannot silently vanish. Flex shrink lets
                       the largest segment absorb the few px, so the bar still
                       totals exactly the track width. -->
                  <div
                    v-for="s in agingSegments"
                    :key="s.key"
                    class="h-full"
                    :style="{ width: s.pct + '%', minWidth: '3px', background: s.color }"
                  ></div>
                </div>
                <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                  <span
                    v-for="s in agingSegments"
                    :key="s.key"
                    class="flex items-center gap-1.5 text-[13px] text-ink-muted"
                  >
                    <!-- The swatch carries the bar↔legend link so the counts
                         themselves stay in AA-contrast ink. -->
                    <span
                      class="h-2.5 w-2.5 shrink-0 rounded-sm"
                      :style="{ background: s.color }"
                      aria-hidden="true"
                    ></span>
                    <span class="tnum font-bold text-ink">{{ s.count }}</span>
                    {{ s.label }}
                  </span>
                </div>
              </div>

              <!-- Per-queue -->
              <div class="mt-4 flex flex-col gap-2">
                <button
                  v-for="q in a.queues"
                  :key="q.doctype"
                  type="button"
                  class="flex min-h-secondary w-full items-center justify-between gap-3 rounded-xl border border-surface-line px-3 py-2.5 text-left active:bg-surface-page"
                  :disabled="!q.ok || !q.stages.length"
                  @click="openQueue(q)"
                >
                  <span class="min-w-0">
                    <span class="block truncate text-[15px] font-semibold">
                      {{ shortName(q.doctype) }}
                    </span>
                    <span class="block text-[13px]" :class="q.ok ? 'text-ink-faint' : 'text-warn-text'">
                      <template v-if="!q.ok">{{ q.error }}</template>
                      <template v-else-if="q.past_sla">
                        {{ q.past_sla }} past the {{ a.sla_hours }} h target
                      </template>
                      <template v-else>All within the {{ a.sla_hours }} h target</template>
                    </span>
                  </span>
                  <span class="tnum shrink-0 text-[17px] font-bold">{{ q.total }}</span>
                </button>
              </div>

              <p
                v-if="a.queues.some((q) => q.capped)"
                class="mt-2 text-[13px] text-warn-text"
              >
                A queue hit the read limit — treat these counts as a minimum.
              </p>
            </template>

            <!-- ── Not moving ──────────────────────────────────────────── -->
            <div v-if="stuckRows.length" class="mt-4 border-t border-surface-line pt-3">
              <h3 class="text-[14px] font-bold">Not moving</h3>
              <p class="mt-0.5 text-[13px] text-ink-faint">
                These sit outside every approver's inbox — nobody is being asked
                to act on them.
              </p>
              <div class="mt-2 flex flex-col gap-2">
                <!-- Neutral rows with an amber NUMBER, not three solid amber
                     blocks. Previously these out-shouted the single most
                     important fact on the screen (154 of 205 untouched for a
                     month) and left amber meaning nothing. The chevron — not
                     colour — marks which rows actually open something. -->
                <component
                  :is="r.rows ? 'button' : 'div'"
                  v-for="r in stuckRows"
                  :key="r.key"
                  :type="r.rows ? 'button' : undefined"
                  class="flex min-h-secondary w-full items-center justify-between gap-3 rounded-xl bg-surface-page px-3 py-2.5 text-left"
                  :class="r.rows ? 'active:bg-brand-soft' : ''"
                  @click="r.rows && openStuck(r)"
                >
                  <span class="min-w-0">
                    <span class="block text-[14px] font-semibold text-ink">
                      {{ r.label }}
                    </span>
                    <span class="block text-[13px] text-ink-faint">{{ r.sub }}</span>
                  </span>
                  <span class="flex shrink-0 items-center gap-1">
                    <span class="tnum text-[17px] font-bold text-warn-text">
                      {{ r.count }}
                    </span>
                    <span v-if="r.rows" class="text-[17px] text-ink-faint" aria-hidden="true">›</span>
                  </span>
                </component>
              </div>
            </div>
          </template>
        </StatCard>

        <!-- ── Card 2 · gate ───────────────────────────────────────────── -->
        <StatCard
          title="Gate activity"
          scope="Material vehicles that have entered the plant"
          :state="gateState"
          :error-detail="gateErrDetail"
          @retry="reload"
        >
          <template v-if="g">
            <!-- Card 1 has a `partial` banner; card 2's three sections fail
                 INDEPENDENTLY server-side, so it needs one too. Without this a
                 failed in-plant read just made the hero vanish and the card
                 still looked complete. -->
            <div
              v-if="gatePartial"
              class="mb-3 rounded-lg bg-warn-bg px-3 py-2 text-[13px] text-warn-text"
            >
              Part of this card couldn't be read, so something below is missing.
            </div>

            <template v-if="g.in_plant">
              <!-- A bare 38px "0" above a grey 395 reads as broken. Card 1
                   already solved this; use its pattern rather than a second
                   visual language for the same situation. -->
              <div v-if="!g.in_plant.arrived_today" class="py-2">
                <div class="text-[17px] font-bold">No arrivals today</div>
                <p class="mt-1 text-[14px] text-ink-muted">
                  No material vehicle has come through the gate in the last
                  24 hours.
                </p>
              </div>
              <div v-else class="flex items-end gap-2">
                <span class="tnum text-[38px] font-bold leading-none">
                  {{ g.in_plant.arrived_today }}
                </span>
                <span class="pb-1 text-[14px] text-ink-muted">arrived in the last 24 hours</span>
              </div>

              <!-- Wording is deliberate: these are UNCLOSED RECORDS, not trucks
                   standing in the yard. -->
              <div
                v-if="g.in_plant.no_exit_recorded"
                class="mt-3 rounded-xl bg-surface-page px-3 py-2.5"
              >
                <div class="flex items-baseline justify-between gap-3">
                  <span class="text-[14px] font-semibold">No exit recorded</span>
                  <span class="tnum text-[17px] font-bold">{{ g.in_plant.no_exit_recorded }}</span>
                </div>
                <!-- The evidence sits immediately before the conclusion: a
                     record dating back months is what makes "not vehicles on
                     site" credible rather than merely reassuring. -->
                <p class="mt-1 text-[13px] text-ink-muted">
                  Gate records opened more than 24 hours ago and never
                  closed<template v-if="oldestStale">, some going back to
                  {{ oldestStale }}</template>. Unfinished paperwork, not
                  vehicles on site.
                </p>
              </div>

              <p v-if="!g.in_plant.vocab_ok" class="mt-2 text-[13px] text-warn-text">
                Gate status names have changed since this was set up — this count
                may be wrong. Worth a check.
              </p>
            </template>

            <div v-if="g.grain_held" class="mt-4 border-t border-surface-line pt-3">
              <button
                type="button"
                class="flex min-h-secondary w-full items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-left"
                :class="g.grain_held.count ? 'bg-warn-bg' : 'bg-surface-page'"
                :disabled="!g.grain_held.items.length"
                @click="openGrain"
              >
                <span class="min-w-0">
                  <span
                    class="block text-[14px] font-semibold"
                    :class="g.grain_held.count ? 'text-warn-text' : 'text-ink'"
                  >
                    Grain waiting at the gate
                  </span>
                  <span
                    class="block text-[13px]"
                    :class="g.grain_held.count ? 'text-warn-text' : 'text-ink-faint'"
                  >
                    <template v-if="!g.grain_held.count">None waiting to unload</template>
                    <template v-else>
                      {{ g.grain_held.over_6h }} waiting over 6 hours · longest
                      {{ Math.round(g.grain_held.oldest_hours) }} h
                    </template>
                    <!-- Released trucks have LEFT the site; counting them as
                         'held at the gate' was false. Reported alongside,
                         never folded into the waiting count. -->
                    <template v-if="g.grain_held.departed_owing_grn">
                      · {{ g.grain_held.departed_owing_grn }} already left, receipt owed
                    </template>
                  </span>
                </span>
                <span
                  class="tnum shrink-0 text-[17px] font-bold"
                  :class="g.grain_held.count ? 'text-warn-text' : 'text-ink'"
                >
                  {{ g.grain_held.count }}
                </span>
              </button>
              <p v-if="!g.grain_held.enforced" class="mt-2 text-[13px] text-ink-faint">
                Deferred grain linking is currently switched off, so these holds
                are shown but not enforced.
              </p>
            </div>

            <div v-if="g.grn_overdue" class="mt-3">
              <div
                class="flex min-h-secondary items-center justify-between gap-3 rounded-xl px-3 py-2.5"
                :class="g.grn_overdue.count ? 'bg-danger-bg' : 'bg-surface-page'"
              >
                <span class="min-w-0">
                  <span
                    class="block text-[14px] font-semibold"
                    :class="g.grn_overdue.count ? 'text-danger-text' : 'text-ink'"
                  >
                    Left without a receipt
                  </span>
                  <span
                    class="block text-[13px]"
                    :class="g.grn_overdue.count ? 'text-danger-text' : 'text-ink-faint'"
                  >
                    <template v-if="!g.grn_overdue.count">
                      Every released vehicle has its receipt booked
                    </template>
                    <template v-else>
                      Past the {{ g.grn_overdue.threshold_hours }} h limit · longest
                      {{ Math.round(g.grn_overdue.oldest_hours) }} h
                    </template>
                  </span>
                </span>
                <span
                  class="tnum shrink-0 text-[17px] font-bold"
                  :class="g.grn_overdue.count ? 'text-danger-text' : 'text-ink'"
                >
                  {{ g.grn_overdue.count }}
                </span>
              </div>
            </div>
          </template>
        </StatCard>
      </div>
    </PullToRefresh>

    <StatSheet
      :open="sheet.open"
      :title="sheet.title"
      :subtitle="sheet.subtitle"
      :footnote="sheet.footnote"
      :rows="sheet.rows"
      @close="sheet.open = false"
    />

    <TabBar />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue"

import AlertsSheet from "@/components/AlertsSheet.vue"
import AppHeader from "@/components/AppHeader.vue"
import PullToRefresh from "@/components/PullToRefresh.vue"
import StatCard from "@/components/StatCard.vue"
import StatSheet from "@/components/StatSheet.vue"
import TabBar from "@/components/TabBar.vue"
import { shortDate } from "@/data/format.js"
import { isStale, loadOverview, overview } from "@/data/overview.js"

const sheet = reactive({ open: false, title: "", subtitle: "", footnote: "", rows: [] })
// `stale` is time-based, so something must re-evaluate it as time passes.
// Ticking only on reload was useless: a reload resets loadedAt, so isStale()
// was false at every moment it was ever read and the indicator could never fire.
const tick = ref(0)

const d = computed(() => overview.data)
const a = computed(() => d.value && d.value.approvals)
const g = computed(() => d.value && d.value.gate)

const stale = computed(() => (tick.value, isStale()))

const asOfLine = computed(() => {
  if (!d.value) return " "
  const base = `As of ${d.value.as_of_display}`
  if (d.value.is_backdated) return `${base} — showing ${shortDate(d.value.date)}`
  // Suppressed while the refresh-failed banner is up: one signal, one place.
  if (overview.error) return base
  return stale.value ? `${base} · pull down to refresh` : base
})

// A failed load and an empty company are different things and must never share
// a screen state.
// The kill switch is not a failure — offering a Retry button that can never
// succeed is worse than saying plainly that the feature is off.
const switchedOff = computed(() =>
  Boolean(overview.error && /switched off/i.test(overview.error.display() || ""))
)

const cardState = computed(() => {
  if (overview.loading && !overview.data) return "loading"
  if (switchedOff.value && !overview.data) return "unconfigured"
  if (overview.error && !overview.data) return "error"
  return a.value ? "ok" : "error"
})
const errDetail = computed(() => (overview.error ? overview.error.display() : ""))

const gateState = computed(() => {
  if (overview.loading && !overview.data) return "loading"
  if (switchedOff.value && !overview.data) return "unconfigured"
  if (overview.error && !overview.data) return "error"
  if (!g.value) return "error"
  // `ok` alone — NOT "any section present". grain_held is a truthy object even
  // at count 0, so the old test rendered a complete-looking card while the
  // in-plant section had silently failed and its error was never displayed.
  return g.value.ok ? "ok" : "error"
})

// Server-side each gate section fails independently, so "some of it is missing"
// is a distinct state from "the card failed".
const gatePartial = computed(() =>
  Boolean(g.value && (!g.value.in_plant || !g.value.grain_held || !g.value.grn_overdue))
)
const gateErrDetail = computed(
  () => (g.value && g.value.error) || errDetail.value || "Couldn't read gate activity."
)

const oldestStale = computed(() => {
  const s = g.value && g.value.in_plant && g.value.in_plant.oldest_stale
  return s ? shortDate(s) : ""
})

// `oldest` carries no doctype — resolve it by identity against the queue that
// produced it rather than guessing from the name prefix.
const oldestTo = computed(() => {
  const o = a.value && a.value.oldest
  if (!o) return null
  const q = (a.value.queues || []).find((x) => x.ok && x.oldest && x.oldest.name === o.name)
  return q ? { name: "document", params: { doctype: q.doctype, name: o.name } } : null
})

// Colours come through inline style, not Tailwind classes: an opacity
// modifier over a var() colour compiles to nothing at all (see tokens.css),
// and a ramp built from the 5 semantic tokens alone would repeat a hue across
// two adjacent buckets.
const AGING = [
  { key: "d0_1", label: "under a day", color: "var(--exec-age-1)" },
  { key: "d1_3", label: "1–3 days", color: "var(--exec-age-2)" },
  { key: "d3_7", label: "3–7 days", color: "var(--exec-age-3)" },
  { key: "d7_30", label: "1–4 weeks", color: "var(--exec-age-4)" },
  { key: "d30_plus", label: "over 30 days", color: "var(--exec-age-5)" },
]

const agingCounts = computed(() => {
  const out = {}
  for (const s of AGING) out[s.key] = 0
  for (const q of (a.value && a.value.queues) || []) {
    if (!q.ok) continue
    for (const s of AGING) out[s.key] += (q.aging || {})[s.key] || 0
  }
  return out
})
const agingTotal = computed(() =>
  Object.values(agingCounts.value).reduce((t, n) => t + n, 0)
)
const agingSegments = computed(() =>
  AGING.map((s) => ({
    ...s,
    count: agingCounts.value[s.key],
    pct: agingTotal.value ? (agingCounts.value[s.key] / agingTotal.value) * 100 : 0,
  })).filter((s) => s.count > 0)
)

const stuckRows = computed(() => {
  const out = []
  if (!a.value) return out
  const un = a.value.unrouted || { count: 0, items: [] }
  if (un.count) {
    out.push({
      key: "unrouted",
      label: "In nobody's queue",
      sub: "Status no approval rule routes to anyone",
      count: un.count,
      rows: un.items.map((i) => ({
        label: i.stage || "(blank status)",
        value: i.count,
        tone: "warn",
        sub:
          `${shortName(i.doctype)} · longest ${Math.round(i.days)} d` +
          (i.companion ? " · handled in the budget queue" : ""),
      })),
      title: "In nobody's queue",
      subtitle: "Grouped by the status they are stuck at",
    })
  }
  const orp = a.value.orphaned || { count: 0, items: [] }
  if (orp.count) {
    out.push({
      key: "orphaned",
      // NOT "never created": on demo all three DID have an override raised and
      // then cancelled (BO-2026-09421 / -10471 / -10473). A CEO who taps
      // through and finds the request sitting in Cancelled stops trusting the
      // screen. "No longer exists" is true of both cases.
      label: "Blocked, no override in place",
      sub: "The budget override was cancelled or never raised",
      count: orp.count,
      rows: orp.items.map((i) => ({
        label: i.name,
        value: `${Math.round(i.days)} d`,
        tone: "warn",
        sub: shortName(i.doctype),
        to: { name: "document", params: { doctype: i.doctype, name: i.name } },
      })),
      title: "Blocked with no request raised",
      subtitle: "These cannot move until someone raises the override",
    })
  }
  const hid = a.value.hidden || { count: 0 }
  if (hid.count) {
    out.push({
      key: "hidden",
      label: "Submitted but still marked pending",
      sub: "Already submitted, so no approval screen lists them",
      count: hid.count,
      // Deliberately no drill-down: the server returns a count only, and an
      // empty sheet would read as "nothing here".
      rows: null,
    })
  }
  return out
})

function shortName(doctype) {
  if (doctype === "Purchase Order") return "Purchase Orders"
  if (doctype === "Material Request") return "Material Requests"
  return doctype
}

function openQueue(q) {
  if (!q.ok || !q.stages.length) return
  Object.assign(sheet, {
    open: true,
    title: shortName(q.doctype),
    subtitle: `${q.total} pending, by the step they are waiting at`,
    footnote: "",
    rows: q.stages.map((s) => ({
      label: s.stage || "(blank status)",
      value: s.count,
      tone: s.routed ? "" : "warn",
      sub:
        `longest ${Math.round(s.days)} d` +
        (s.routed ? "" : " · nobody is assigned to this step"),
    })),
  })
}

function openStuck(r) {
  Object.assign(sheet, {
    open: true,
    title: r.title,
    subtitle: r.subtitle,
    footnote: "",
    rows: r.rows,
  })
}

function openGrain() {
  const held = g.value && g.value.grain_held
  if (!held || !held.items.length) return
  Object.assign(sheet, {
    open: true,
    title: "Grain at the gate",
    subtitle: "Waiting to unload, or already left with a receipt owed",
    footnote: held.truncated ? "Showing the first 50 only." : "",
    // Three states, matching the Stores dashboard's own vocabulary — a truck
    // that has left is never described as waiting.
    rows: held.items.map((i) => ({
      label: i.vehicle || i.token,
      value: `${Math.round(i.hours)} h`,
      // Only a truck still standing at the gate is time-critical; one that has
      // already left is a paperwork chase, not a queue.
      tone: !i.departed && i.hours > 6 ? "warn" : "",
      sub: [
        i.material,
        i.departed ? "left, receipt owed" : i.released ? "released, still here" : "waiting",
      ]
        .filter(Boolean)
        .join(" · "),
    })),
  })
}

async function reload() {
  await loadOverview()
  tick.value++
}

let staleTimer = null

// An installed PWA is usually RESUMED, not launched, so onMounted alone would
// leave hours-old figures on screen with no hint they are old. Matches Inbox.
function onVisible() {
  if (document.visibilityState === "visible") reload()
}

onMounted(() => {
  reload()
  document.addEventListener("visibilitychange", onVisible)
  // Cheap wall-clock tick so the "as of" line can actually go amber while the
  // exec sits on the page. Does NOT auto-refetch: a number should not change
  // under an executive's eyes without them asking.
  staleTimer = setInterval(() => {
    tick.value += 1
  }, 30000)
})

onUnmounted(() => {
  document.removeEventListener("visibilitychange", onVisible)
  if (staleTimer) clearInterval(staleTimer)
  staleTimer = null
})
</script>
